# -*- coding: utf-8 -*-
"""lstm_predictor.py —— 运行时预测器（决策器输入侧）。

两个预测器（对应调度算法需求）：
  - PvLstmPredictor   : PV = 站点级 LSTM + XGBoost 级联（自然日 24 点对齐）。
  - HistoricalMeanLoad: Load = 同小时 + 同星期几 的历史均值（无持久性衰减）。

设计要点：
  1) LSTM 训练时是「自然日 0-23 点」多对多（输入 24 点特征 → 输出 24 点功率），
     运行时 LP 需要「从当前小时起的滚动 24h」。因此本模块维护「昨日 24 点 PV」缓存，
     每次预测「今日 24 点」与「明日 24 点」，再按当前小时拼接出未来 24h。
  2) 字段映射：运行时天气预报字段是 solar_irradiance_wm2，训练特征是
     shortwave_radiation_wm2（同一物理量），此处统一映射。
  3) 依赖 torch + xgboost（Debug 开发机专用；Release 走 ONNX，见 Tools/pt2onnx.py）。
"""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PV_DIR = os.path.join(HERE, "Predictor", "PV_Predictor")

# 与 Predictor/PV_Predictor/Train.py 一致：运行时天气预报能提供的 3 个字段
PV_WEATHER_COLS = ["shortwave_radiation_wm2", "cloud_cover_percent", "temperature_c"]
# 运行时预报字段 → 训练特征字段 映射
FORECAST_FIELD_MAP = {"solar_irradiance_wm2": "shortwave_radiation_wm2"}


class PvLstmPredictor:
    """站点级光伏预测器（LSTM + XGBoost 级联）。"""

    def __init__(self, site_no: int, use_site_model: bool = True, use_xgb: bool = True):
        self.site_no = int(site_no)
        self._use_xgb = bool(use_xgb)   # False = 纯 LSTM（跳过 XGB 级联），做 A/B

        if PV_DIR not in sys.path:
            sys.path.insert(0, PV_DIR)
        # 延迟 import（torch / xgboost 依赖只在 Debug 运行时加载）
        import xgboost as xgb
        import Trainer as _T
        from Train import WEATHER_COLS as _wc, build_xgb_features as _bx

        self._T = _T
        self._bx = _bx
        self._xgb_mod = xgb
        self._weather_cols = list(_wc)

        # 下游站点级模型优先（LSTM-only，无 XGB）；缺失则回退上游全局（LSTM+XGB）。
        pt = os.path.join(PV_DIR, "Downstream_model", f"pv_predictor_site{site_no}.pt")
        xgb_path = None
        if not use_site_model or not os.path.exists(pt):
            pt = os.path.join(PV_DIR, "Upstream_model", "pv_predictor.pt")
            xgb_path = os.path.join(PV_DIR, "Upstream_model", "pv_predictor.xgb.json")
        if not os.path.exists(pt):
            raise FileNotFoundError(
                f"找不到 PV LSTM checkpoint：{pt}\n请先运行 Predictor/PV_Predictor 训练。")

        self.model, self.meta = _T.load_checkpoint(pt)
        self.feature_names = list(self.meta["feature_names"])
        self.mean = np.asarray(self.meta["scaler_mean"], dtype=np.float32)
        self.std = np.asarray(self.meta["scaler_scale"], dtype=np.float32)
        if xgb_path is not None and os.path.exists(xgb_path):
            self.xgb_model = xgb.Booster()
            self.xgb_model.load_model(xgb_path)
        else:
            self.xgb_model = None   # 下游模型无 XGB 级联

        # 昨日 24 点 PV（比赛前 = 准备历史最后一天；比赛中 = 滚动更新）
        self._yesterday_pv = np.zeros(24, dtype=np.float32)
        self._today_obs: dict[int, float] = {}   # 今日已观测 PV（hour -> kW）
        self._last_day = None
        # 自然日预测缓存（今日/明日 24 点只在跨日时重算，避免每槽重复 LSTM 推理）
        self._cache_day = None
        self._cache_today = None
        self._cache_tomorrow = None

        # LMS 在线残差修正（纯 numpy，无 torch，参赛机可用）
        self._lms_w = np.zeros(6, dtype=np.float32)   # [weather3, hour_sin, hour_cos, bias]
        self._lms_mu = 0.008                          # 学习率（3 站 1010/1012/1014 上 +0.6~+1.2pp）
        self._lms_enabled = True
        self._today_weather = {}   # hour -> (3,) 已观测小时的天气（供 LMS 特征）
        self._lms_done = set()     # 当前自然日已完成 LMS 更新的小时（每时仅更新一次）

    # ---------------- 观测维护（昨日 PV 缓存） ----------------
    def reset(self) -> None:
        """重置全部运行时状态（复用模型权重时，跑分前调用，避免跨场状态泄漏）。"""
        self._yesterday_pv = np.zeros(24, dtype=np.float32)
        self._today_obs = {}
        self._last_day = None
        self._cache_day = None
        self._cache_today = None
        self._cache_tomorrow = None
        self._lms_w = np.zeros(6, dtype=np.float32)
        self._today_weather = {}
        self._lms_done = set()

    def set_yesterday_pv(self, pv_24) -> None:
        """用准备阶段最后一天（或任意完整一天）的 24 点 PV 初始化昨日缓存。"""
        arr = np.asarray(pv_24, dtype=np.float32).reshape(-1)
        if arr.shape[0] >= 24:
            self._yesterday_pv = arr[:24].copy()

    def observe_pv(self, hour: int, pv_kw: float, day: int = None,
                   weather_cur=None) -> None:
        """记录当前实测 PV（按小时），跨日时自动滚入昨日缓存并失效预测缓存。

        weather_cur: 当前小时的 3 维天气（PV_WEATHER_COLS 顺序，可选）。给出时用于
        在线 LMS 残差更新（e = 实测 − (LSTM 预测 + wᵀx)，w ← w + μ·e·x，纯 numpy）。
        """
        if day is not None and self._last_day is not None and day != self._last_day:
            self.roll_day()
            self._cache_day = None                 # 昨日已变，预测缓存失效
            self._today_weather = {}
            self._lms_done = set()
        self._last_day = day
        hi = int(hour) % 24
        self._today_obs[hi] = float(pv_kw)
        if weather_cur is not None:
            self._today_weather[hi] = np.asarray(weather_cur, dtype=np.float32).reshape(-1)[:3]
        # 若当前自然日的 LSTM 预测已算好，且该小时尚未更新，则做一次 LMS 更新
        if (self._lms_enabled and day is not None and day == self._cache_day
                and hi not in self._lms_done):
            self._lms_update_hour(hi)
            self._lms_done.add(hi)

    def roll_day(self) -> None:
        """跨日：把今日观测（缺失小时用昨日缓存补齐）作为新的昨日。"""
        t = np.array([self._today_obs.get(h, float(self._yesterday_pv[h]))
                      for h in range(24)], dtype=np.float32)
        self._yesterday_pv = t
        self._today_obs = {}

    # ---------------- 特征构造 ----------------
    # 训练字段 → 运行时可能出现的字段名（辐照度有 solar/shortwave 两个名字）
    _WEATHER_CANDIDATES = {
        "shortwave_radiation_wm2": ["solar_irradiance_wm2", "shortwave_radiation_wm2"],
        "cloud_cover_percent": ["cloud_cover_percent"],
        "temperature_c": ["temperature_c"],
    }

    @staticmethod
    def weather_array(points) -> np.ndarray:
        """从 points（24 个 dict）提取 (24,3) 按 PV_WEATHER_COLS 顺序。

        兼容两种字段名：运行时预报用 solar_irradiance_wm2，历史/训练数据用
        shortwave_radiation_wm2（同一物理量）。
        """
        cols = []
        for c in PV_WEATHER_COLS:
            actual = c
            if points:
                for cand in PvLstmPredictor._WEATHER_CANDIDATES.get(c, [c]):
                    if cand in points[0]:
                        actual = cand
                        break
            cols.append([float(p[actual]) for p in points])
        return np.asarray(cols, dtype=np.float32).T   # (24, 3)

    def _build_input(self, weather_24: np.ndarray, pv_lag_24) -> np.ndarray:
        """构造 (1, 24, F) 标准化输入。weather_24: (24,3)；pv_lag_24: (24,)。"""
        hours = np.arange(24, dtype=np.float32)
        feats = np.concatenate([
            np.asarray(weather_24, dtype=np.float32).reshape(24, -1)[:, :len(PV_WEATHER_COLS)],
            np.asarray(pv_lag_24, dtype=np.float32).reshape(-1, 1)[:24],
            np.sin(2 * np.pi * hours / 24.0).reshape(-1, 1),
            np.cos(2 * np.pi * hours / 24.0).reshape(-1, 1),
        ], axis=1)   # (24, 3+1+2)
        all_names = PV_WEATHER_COLS + ["pv_lag_24", "hour_sin", "hour_cos"]
        idx = [all_names.index(nm) for nm in self.feature_names]
        X = feats[:, idx].astype(np.float32)          # (24, F)
        X = (X - self.mean) / self.std
        return X[None, ...]                            # (1, 24, F)

    # ---------------- LMS 在线残差修正 ----------------
    # 天气 3 维归一化尺度（辐照度/云量/温度 → O(1)），避免 LMS 因辐照度(~1000)尺度发散
    _LMS_SCALE = np.array([1000.0, 100.0, 40.0], dtype=np.float32)

    @classmethod
    def _lms_feature(cls, weather_3, hour: int) -> np.ndarray:
        """构造 LMS 特征 x：[weather3 归一化, hour_sin, hour_cos, bias]（6 维含偏置）。"""
        w = np.asarray(weather_3, dtype=np.float32).reshape(-1)[:3] / cls._LMS_SCALE
        h = int(hour) % 24
        return np.concatenate([w, np.array([
            np.sin(2 * np.pi * h / 24.0), np.cos(2 * np.pi * h / 24.0), 1.0,
        ], dtype=np.float32)]).astype(np.float32)

    @classmethod
    def _lms_residual(cls, weather_24: np.ndarray, w_lms: np.ndarray,
                      enabled: bool) -> np.ndarray:
        """一整天的 LMS 残差 wᵀx，(24,)。无启用时返回零向量。"""
        if not enabled:
            return np.zeros(24, dtype=np.float32)
        w24 = np.asarray(weather_24, dtype=np.float32).reshape(24, -1)[:, :3] / cls._LMS_SCALE
        hours = np.arange(24, dtype=np.float32)
        feats = np.column_stack([
            w24,
            np.sin(2 * np.pi * hours / 24.0),
            np.cos(2 * np.pi * hours / 24.0),
            np.ones(24, dtype=np.float32),
        ])                                            # (24, 6)
        return (feats @ w_lms).astype(np.float32)

    def _lms_update_hour(self, hi: int) -> None:
        """对已观测小时 hi 做一次 LMS 权重更新（需要当前自然日 LSTM 预测已缓存）。"""
        if not self._lms_enabled or self._cache_today is None:
            return
        w3 = self._today_weather.get(hi)
        if w3 is None:
            return
        x = self._lms_feature(w3, hi)
        lstm_pred = float(self._cache_today[hi])      # 纯 LSTM（未含残差）预测
        resid = float(x @ self._lms_w)
        e = float(self._today_obs.get(hi, lstm_pred)) - (lstm_pred + resid)
        self._lms_w = self._lms_w + self._lms_mu * e * x


    # ---------------- 推理 ----------------
    def predict_day(self, weather_24: np.ndarray, pv_lag_24) -> np.ndarray:
        """预测某一自然日的 24 点 PV（kW）。weather_24: (24,3)；pv_lag_24: (24,)。"""
        X = self._build_input(weather_24, pv_lag_24)
        pred, hidden = self._T.extract_features(self.model, X)   # pred (1,24), hidden (1,H)
        if not self._use_xgb or self.xgb_model is None:
            return np.clip(np.asarray(pred).reshape(-1), 0.0, None).astype(np.float32)
        Xgb = self._bx(pred, hidden, X)                          # (24, H+1+F)
        out = self.xgb_model.predict(self._xgb_mod.DMatrix(Xgb))
        return np.clip(out, 0.0, None).astype(np.float32)        # (24,)

    def forecast(self, hour_now: int, weather_today_24, weather_tomorrow_24,
                 day: int = None, horizon_hours: int = 24) -> np.ndarray:
        """返回「从 hour_now 起的未来 horizon_hours 点 PV」。

        今日/明日预测在自然日内不变（仅跨日时 lag 滚动），故按 day 缓存，每自然日
        只推理 2 次（今日 + 明日）。最多可返回「今天剩余 + 明天全部」= 48-hi 点
        （天气 API 只给今明两天，无后天），故 horizon_hours>48 时按 48 封顶。
        """
        if day is None or day != self._cache_day:
            self._cache_today = self.predict_day(weather_today_24, self._yesterday_pv)
            self._cache_tomorrow = self.predict_day(weather_tomorrow_24, self._cache_today)
            self._cache_day = day
            # 今日缓存刚算好 → 补齐此前已观测但尚未 LMS 更新的小时（如当日第 0 槽）
            for h in sorted(self._today_obs.keys()):
                if h not in self._lms_done:
                    self._lms_update_hour(h)
                    self._lms_done.add(h)
        hi = int(hour_now) % 24
        # LMS 残差叠加在缓存 LSTM 预测之上（权重实时，故不写入缓存）
        today_res = self._lms_residual(weather_today_24, self._lms_w, self._lms_enabled)
        tom_res = self._lms_residual(weather_tomorrow_24, self._lms_w, self._lms_enabled)
        # 今天剩余 + 明天全部 = 48-hi 点（horizon_hours 封顶）
        full = np.concatenate([
            self._cache_today[hi:] + today_res[hi:],
            self._cache_tomorrow + tom_res,
        ]).astype(np.float32)
        return full[:int(horizon_hours)]


class PvOnnxPredictor:
    """站点级光伏预测器（ONNX 推理，无 torch）—— Release 参赛机用。

    与 PvLstmPredictor（Debug torch 版）接口一致、纯 numpy 部分逻辑一致：
    LSTM 前向改用 onnxruntime（加载 pt2onnx 导出的 pv_predictor*.onnx），
    XGBoost 级联内联（build_xgb_features 复制自 Train.py，避免 import Trainer→torch）。
    Load 预测器仍是 HistoricalMeanLoad（纯历史均值，无模型）。
    """

    def __init__(self, site_no: int, use_site_model: bool = True, use_xgb: bool = True):
        import json
        import onnxruntime as ort
        import xgboost as xgb

        self.site_no = int(site_no)
        self._use_xgb = bool(use_xgb)
        self._xgb = xgb

        # 下游站点级模型优先（LSTM-only，无 XGB）；缺失则回退上游全局（LSTM+XGB）。
        onnx = os.path.join(PV_DIR, "Downstream_model", f"pv_predictor_site{site_no}.onnx")
        xgb_path = None
        if not use_site_model or not os.path.exists(onnx):
            onnx = os.path.join(PV_DIR, "Upstream_model", "pv_predictor.onnx")
            xgb_path = os.path.join(PV_DIR, "Upstream_model", "pv_predictor.xgb.json")
        if not os.path.exists(onnx):
            raise FileNotFoundError(
                f"找不到 PV ONNX：{onnx}\n请先运行 Tools/pt2onnx.py 导出 .pt → .onnx")

        # meta（scaler / feature_names / 维度）由 pt2onnx 与 .onnx 同目录保存
        with open(onnx + ".meta.json", encoding="utf-8") as f:
            meta = json.load(f)
        self._sess = ort.InferenceSession(onnx, providers=["CPUExecutionProvider"])
        self.feature_names = list(meta.get("feature_names", []))
        self.mean = np.asarray(meta.get("scaler_mean", []), dtype=np.float32)
        self.std = np.asarray(meta.get("scaler_scale", []), dtype=np.float32)

        if xgb_path is not None and os.path.exists(xgb_path) and self._use_xgb:
            self.xgb_model = xgb.Booster()
            self.xgb_model.load_model(xgb_path)
        else:
            self.xgb_model = None   # 下游模型无 XGB 级联

        # 昨日 24 点 PV + 观测状态（同 PvLstmPredictor）
        self._yesterday_pv = np.zeros(24, dtype=np.float32)
        self._today_obs: dict[int, float] = {}
        self._last_day = None
        self._cache_day = None
        self._cache_today = None
        self._cache_tomorrow = None
        self._lms_w = np.zeros(6, dtype=np.float32)
        self._lms_mu = 0.008
        self._lms_enabled = True
        self._today_weather = {}
        self._lms_done = set()

    # ---------------- 观测维护（昨日 PV 缓存） ----------------
    def reset(self) -> None:
        self._yesterday_pv = np.zeros(24, dtype=np.float32)
        self._today_obs = {}
        self._last_day = None
        self._cache_day = None
        self._cache_today = None
        self._cache_tomorrow = None
        self._lms_w = np.zeros(6, dtype=np.float32)
        self._today_weather = {}
        self._lms_done = set()

    def set_yesterday_pv(self, pv_24) -> None:
        arr = np.asarray(pv_24, dtype=np.float32).reshape(-1)
        if arr.shape[0] >= 24:
            self._yesterday_pv = arr[:24].copy()

    def observe_pv(self, hour: int, pv_kw: float, day: int = None,
                   weather_cur=None) -> None:
        if day is not None and self._last_day is not None and day != self._last_day:
            self.roll_day()
            self._cache_day = None
            self._today_weather = {}
            self._lms_done = set()
        self._last_day = day
        hi = int(hour) % 24
        self._today_obs[hi] = float(pv_kw)
        if weather_cur is not None:
            self._today_weather[hi] = np.asarray(weather_cur, dtype=np.float32).reshape(-1)[:3]
        if (self._lms_enabled and day is not None and day == self._cache_day
                and hi not in self._lms_done):
            self._lms_update_hour(hi)
            self._lms_done.add(hi)

    def roll_day(self) -> None:
        t = np.array([self._today_obs.get(h, float(self._yesterday_pv[h]))
                      for h in range(24)], dtype=np.float32)
        self._yesterday_pv = t
        self._today_obs = {}

    # ---------------- 特征构造 ----------------
    _WEATHER_CANDIDATES = {
        "shortwave_radiation_wm2": ["solar_irradiance_wm2", "shortwave_radiation_wm2"],
        "cloud_cover_percent": ["cloud_cover_percent"],
        "temperature_c": ["temperature_c"],
    }

    @staticmethod
    def weather_array(points) -> np.ndarray:
        cols = []
        for c in PV_WEATHER_COLS:
            actual = c
            if points:
                for cand in PvOnnxPredictor._WEATHER_CANDIDATES.get(c, [c]):
                    if cand in points[0]:
                        actual = cand
                        break
            cols.append([float(p[actual]) for p in points])
        return np.asarray(cols, dtype=np.float32).T   # (24, 3)

    def _build_input(self, weather_24: np.ndarray, pv_lag_24) -> np.ndarray:
        hours = np.arange(24, dtype=np.float32)
        feats = np.concatenate([
            np.asarray(weather_24, dtype=np.float32).reshape(24, -1)[:, :len(PV_WEATHER_COLS)],
            np.asarray(pv_lag_24, dtype=np.float32).reshape(-1, 1)[:24],
            np.sin(2 * np.pi * hours / 24.0).reshape(-1, 1),
            np.cos(2 * np.pi * hours / 24.0).reshape(-1, 1),
        ], axis=1)
        all_names = PV_WEATHER_COLS + ["pv_lag_24", "hour_sin", "hour_cos"]
        idx = [all_names.index(nm) for nm in self.feature_names]
        X = feats[:, idx].astype(np.float32)
        X = (X - self.mean) / self.std
        return X[None, ...]

    # ---------------- LMS 在线残差修正 ----------------
    _LMS_SCALE = np.array([1000.0, 100.0, 40.0], dtype=np.float32)

    @classmethod
    def _lms_feature(cls, weather_3, hour: int) -> np.ndarray:
        w = np.asarray(weather_3, dtype=np.float32).reshape(-1)[:3] / cls._LMS_SCALE
        h = int(hour) % 24
        return np.concatenate([w, np.array([
            np.sin(2 * np.pi * h / 24.0), np.cos(2 * np.pi * h / 24.0), 1.0,
        ], dtype=np.float32)]).astype(np.float32)

    @classmethod
    def _lms_residual(cls, weather_24: np.ndarray, w_lms: np.ndarray,
                      enabled: bool) -> np.ndarray:
        if not enabled:
            return np.zeros(24, dtype=np.float32)
        w24 = np.asarray(weather_24, dtype=np.float32).reshape(24, -1)[:, :3] / cls._LMS_SCALE
        hours = np.arange(24, dtype=np.float32)
        feats = np.column_stack([
            w24,
            np.sin(2 * np.pi * hours / 24.0),
            np.cos(2 * np.pi * hours / 24.0),
            np.ones(24, dtype=np.float32),
        ])
        return (feats @ w_lms).astype(np.float32)

    def _lms_update_hour(self, hi: int) -> None:
        if not self._lms_enabled or self._cache_today is None:
            return
        w3 = self._today_weather.get(hi)
        if w3 is None:
            return
        x = self._lms_feature(w3, hi)
        lstm_pred = float(self._cache_today[hi])
        resid = float(x @ self._lms_w)
        e = float(self._today_obs.get(hi, lstm_pred)) - (lstm_pred + resid)
        self._lms_w = self._lms_w + self._lms_mu * e * x

    # ---------------- XGBoost 级联（内联 build_xgb_features，纯 numpy） ----------------
    @staticmethod
    def _build_xgb(pred, hidden, X_s):
        N, P, F = X_s.shape
        hid_rep = np.repeat(hidden, P, axis=0)
        pred_flat = pred.reshape(N * P, 1)
        x_flat = X_s.reshape(N * P, F)
        return np.concatenate([hid_rep, pred_flat, x_flat], axis=1).astype(np.float32)

    # ---------------- 推理（onnxruntime 替代 torch） ----------------
    def predict_day(self, weather_24: np.ndarray, pv_lag_24) -> np.ndarray:
        X = self._build_input(weather_24, pv_lag_24)   # (1, 24, F)
        pred, hidden = self._sess.run(None, {"x": X})  # pred (1,24), hidden (1,H)
        pred = np.asarray(pred).reshape(1, -1)
        hidden = np.asarray(hidden).reshape(1, -1)
        if not self._use_xgb or self.xgb_model is None:
            return np.clip(pred.reshape(-1), 0.0, None).astype(np.float32)
        Xgb = self._build_xgb(pred, hidden, X)
        out = self.xgb_model.predict(self._xgb.DMatrix(Xgb))
        return np.clip(out, 0.0, None).astype(np.float32)

    def forecast(self, hour_now: int, weather_today_24, weather_tomorrow_24,
                 day: int = None, horizon_hours: int = 24) -> np.ndarray:
        if day is None or day != self._cache_day:
            self._cache_today = self.predict_day(weather_today_24, self._yesterday_pv)
            self._cache_tomorrow = self.predict_day(weather_tomorrow_24, self._cache_today)
            self._cache_day = day
            for h in sorted(self._today_obs.keys()):
                if h not in self._lms_done:
                    self._lms_update_hour(h)
                    self._lms_done.add(h)
        hi = int(hour_now) % 24
        today_res = self._lms_residual(weather_today_24, self._lms_w, self._lms_enabled)
        tom_res = self._lms_residual(weather_tomorrow_24, self._lms_w, self._lms_enabled)
        full = np.concatenate([
            self._cache_today[hi:] + today_res[hi:],
            self._cache_tomorrow + tom_res,
        ]).astype(np.float32)
        return full[:int(horizon_hours)]


class HistoricalMeanLoad:
    """负载预测 = 同小时 + 同星期几 的历史均值 + z·标准差（分位数对冲）。

    z=0 为纯历史均值（默认）；z>0 上抬到「均值+zσ」分位，用于 newsvendor 对冲——
    补偿「按均值调度会低估高峰、高峰时段买贵电」的系统性偏差。
    """

    def __init__(self, z: float = 0.0):
        self.z = float(z)
        self._acc = np.zeros((24, 7), dtype=np.float64)   # 一阶矩（和）
        self._acc2 = np.zeros((24, 7), dtype=np.float64)  # 二阶矩（平方和）
        self._cnt = np.zeros((24, 7), dtype=np.float64)

    def observe(self, hour: int, dow: int, load_kw: float) -> None:
        hi, di = int(hour) % 24, int(dow) % 7
        v = float(load_kw)
        self._acc[hi][di] += v
        self._acc2[hi][di] += v * v
        self._cnt[hi][di] += 1.0

    def forecast(self, hours, dows) -> np.ndarray:
        """未来逐时负载预测 kW = 均值 + z·σ。未观测到的 (hour,dow) 回退全局均值+zσ。"""
        total = float(self._cnt.sum())
        if total > 0:
            g_mean = float(self._acc.sum()) / total
            g_var = max(0.0, float(self._acc2.sum()) / total - g_mean * g_mean)
        else:
            g_mean, g_var = 0.0, 0.0
        out = np.empty(len(hours), dtype=np.float32)
        for k, (h, d) in enumerate(zip(hours, dows)):
            hi, di = int(h) % 24, int(d) % 7
            c = self._cnt[hi][di]
            if c > 0:
                m = float(self._acc[hi][di]) / c
                var = max(0.0, float(self._acc2[hi][di]) / c - m * m)
            else:
                m, var = g_mean, g_var
            out[k] = m + self.z * (var ** 0.5)
        return np.clip(out, 0.0, None)

    def n_obs(self) -> int:
        return int(self._cnt.sum())


class PeakAwareLoad(HistoricalMeanLoad):
    """负载预测 = 同小时+同星期几均值 + 峰时抬升（A/B：vs 纯均值）。

    动机：均值预测系统性低估「峰」，调度器会在峰时欠放电、被迫买贵电。
    抬升只作用于「高于全局均值」的小时，使预测峰值 = 在线观测到的日均峰值；
    谷值小时与均值小时保持不动（不抬谷、不过冲）。

        out[h] = m[h] + α·max(0, m[h] − mean_kw)
        α      = (day_peak_mean − max(m)) / (max(m) − mean_kw)

    day_peak_mean 由已完成的自然日峰值在线累积（ready 预热 + 滚动观测，无未来泄漏）。
    """

    def __init__(self, z: float = 0.0):
        super().__init__(z=z)
        self._cur_day_max = None      # 当前自然日的运行峰值
        self._day_peaks = []          # 已完成自然日的峰值 (kW)
        self._last_hour = None

    def observe(self, hour: int, dow: int, load_kw: float) -> None:
        super().observe(hour, dow, load_kw)
        hi = int(hour) % 24
        v = float(load_kw)
        if self._last_hour is not None and hi < self._last_hour:
            # 跨日：收尾上一个自然日
            if self._cur_day_max is not None:
                self._day_peaks.append(self._cur_day_max)
            self._cur_day_max = v
        else:
            self._cur_day_max = v if self._cur_day_max is None else max(self._cur_day_max, v)
        self._last_hour = hi

    def forecast(self, hours, dows) -> np.ndarray:
        m = super().forecast(hours, dows)          # 均值（含 z·σ，z 默认 0）
        total = float(self._cnt.sum())
        mean_kw = float(self._acc.sum()) / total if total > 0 else 0.0
        mmax = float(m.max()) if m.size else 0.0
        day_peak = float(np.mean(self._day_peaks)) if self._day_peaks else mmax
        if mmax > mean_kw + 1e-9 and day_peak > mmax:
            alpha = (day_peak - mmax) / (mmax - mean_kw)
            lift = np.clip(np.asarray(m, dtype=np.float64) - mean_kw, 0.0, None) * alpha
            return np.clip(np.asarray(m, dtype=np.float64) + lift, 0.0, None).astype(np.float32)
        return m

    def peak_lift_info(self) -> dict:
        """诊断信息：当前 α 抬升系数、日均峰值、均值、观测天数。"""
        total = float(self._cnt.sum())
        mean_kw = float(self._acc.sum()) / total if total > 0 else 0.0
        day_peak = float(np.mean(self._day_peaks)) if self._day_peaks else 0.0
        return {"mean_kw": mean_kw, "day_peak_mean": day_peak,
                "n_days": len(self._day_peaks)}


class LstmEmbLoadPredictor:
    """LSTM + 站点嵌入 负荷预测器（池化直接预测，autoencoder 嵌入提供站点幅值）。

    对应 Train_embedding.py：load_pred = LSTM( [外生变量15维 ⊕ 站点嵌入8维] × 24h )。
    站点嵌入由 station_embedding.py 的 MLP autoencoder 对「时域峰统计 + 频域基频统计」
    编码，是池化模型判断「该站幅值多大」的唯一信号。模型静态（无 lag、无在线状态）。
    """

    LOAD_DIR = os.path.join(HERE, "Predictor", "Load_Predictor")
    CKPT = os.path.join(LOAD_DIR, "Upstream_model", "load_lstm_emb.pt")
    SITE_EMB = os.path.join(LOAD_DIR, "Upstream_model", "site_embeddings_ready.json")
    P = 24

    def __init__(self, site_no: int):
        import json
        import torch
        import torch.nn as nn
        self._torch = torch
        self.site_no = int(site_no)
        ckpt = torch.load(self.CKPT, map_location="cpu", weights_only=False)
        F = int(ckpt["input_dim"]); hidden = int(ckpt["hidden_dim"])
        num_layers = int(ckpt["num_layers"]); emb_dim = int(ckpt["emb_dim"])

        class _LoadLSTM(nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm = nn.LSTM(F, hidden, num_layers, batch_first=True)
                self.fc = nn.Sequential(nn.Linear(hidden, 64), nn.ReLU(),
                                        nn.Dropout(0.2), nn.Linear(64, 1))
            def forward(self, x):
                out, _ = self.lstm(x)
                return self.fc(out).squeeze(-1)

        self._model = _LoadLSTM()
        self._model.load_state_dict(ckpt["state_dict"])
        self._model.eval()
        self._feat_mean = np.asarray(ckpt["feat_mean"], dtype=np.float32)
        self._feat_std = np.asarray(ckpt["feat_std"], dtype=np.float32)
        self._load_mean = float(ckpt["load_mean"])
        self._load_std = float(ckpt["load_std"])
        self._emb_dim = emb_dim

        with open(self.SITE_EMB, encoding="utf-8") as f:
            embs = json.load(f)
        self._emb = np.asarray(embs.get(f"site{site_no}", [0.0] * emb_dim), dtype=np.float32)

    def _build_input(self, weather_24, price_24, dow: int) -> np.ndarray:
        w = np.asarray(weather_24, dtype=np.float32).reshape(self.P, 2)
        pr = np.asarray(price_24, dtype=np.float32).reshape(self.P, 2)
        hours = np.arange(self.P, dtype=np.float32)
        hour_sin = np.sin(2 * np.pi * hours / 24.0)
        hour_cos = np.cos(2 * np.pi * hours / 24.0)
        d = float(int(dow) % 7)
        temp = w[:, 0]; swr = w[:, 1]
        feats = np.empty((self.P, 15), dtype=np.float32)
        feats[:, 0] = temp
        feats[:, 1] = temp ** 2
        feats[:, 2] = temp * hour_sin
        feats[:, 3] = swr
        feats[:, 4] = pr[:, 0]
        feats[:, 5] = pr[:, 1]
        feats[:, 6] = 0.0   # is_holiday（运行时保守置 0）
        feats[:, 7] = hour_sin
        feats[:, 8] = hour_cos
        feats[:, 9] = np.sin(2 * np.pi * 1 * d / 7.0)
        feats[:, 10] = np.cos(2 * np.pi * 1 * d / 7.0)
        feats[:, 11] = np.sin(2 * np.pi * 2 * d / 7.0)
        feats[:, 12] = np.cos(2 * np.pi * 2 * d / 7.0)
        feats[:, 13] = np.sin(2 * np.pi * 3 * d / 7.0)
        feats[:, 14] = np.cos(2 * np.pi * 3 * d / 7.0)
        emb = np.tile(self._emb.reshape(1, -1), (self.P, 1))
        X = np.concatenate([feats, emb], axis=1)   # (24, 15+8)
        X = (X - self._feat_mean) / self._feat_std
        return X[None, ...]                        # (1, 24, F)

    def _predict_day(self, weather_24, price_24, dow: int) -> np.ndarray:
        X = self._build_input(weather_24, price_24, dow)
        with self._torch.no_grad():
            y = self._model(self._torch.tensor(X, dtype=self._torch.float32)).numpy()
        y = y.reshape(-1) * self._load_std + self._load_mean
        return np.clip(y, 0.0, None).astype(np.float32)

    def forecast(self, hour_now, weather_today_24, weather_tomorrow_24,
                 price_today_24, price_tomorrow_24, day=None, dow=None) -> np.ndarray:
        """返回「今天剩余 + 明天全部」= 48-hi 点（与 PV 预测器口径一致）。"""
        dow0 = int(dow) if dow is not None else 0
        today = self._predict_day(weather_today_24, price_today_24, dow0)
        tomorrow = self._predict_day(weather_tomorrow_24, price_tomorrow_24, (dow0 + 1) % 7)
        hi = int(hour_now) % self.P
        return np.concatenate([today[hi:], tomorrow]).astype(np.float32)


class LoadLstmPredictor:
    """负载预测 = VMD-LSTM（1 小时 / 24 点/天，自然日对齐）。

    与 PvLstmPredictor 同款「今日+明日两天预测再切片」的缓存策略。
    特征 12 维：load_lag_24/48/72 + is_holiday + import/export_price + temperature
              + shortwave_radiation + hour_sin/cos + dow_sin/cos。
    输入 weather 为 (24, 2) [temperature_c, shortwave_radiation_wm2]，price 为 (24, 2)
    [import_price, export_price]（逐时，运行时 15min 由 upsample_hourly 桥接）。
    """

    LOAD_DIR = os.path.join(HERE, "Predictor", "Load_Predictor")
    LOAD_MODELS = os.path.join(LOAD_DIR, "models")
    _F_NAMES = ["load_lag_24", "load_lag_48", "load_lag_72", "is_holiday",
                "import_price_eur_kwh", "export_price_eur_kwh", "temperature_c",
                "shortwave_radiation_wm2", "hour_sin", "hour_cos", "dow_sin", "dow_cos"]
    P = 24   # 1 小时 点/天

    def __init__(self, site_no: int, use_site_model: bool = True):
        self.site_no = int(site_no)
        pt = os.path.join(self.LOAD_MODELS, f"load_predictor_site{site_no}.pt")
        if not use_site_model or not os.path.exists(pt):
            pt = os.path.join(self.LOAD_MODELS, "load_predictor.pt")
        if not os.path.exists(pt):
            raise FileNotFoundError(
                f"找不到 load VMD-LSTM checkpoint：{pt}\n请先运行 Predictor/Load_Predictor/Train.py 训练。")

        import torch
        import torch.nn as nn
        self._torch = torch
        ckpt = torch.load(pt, map_location="cpu", weights_only=False)

        class _VMDLSTM(nn.Module):
            def __init__(self, input_dim, hidden_dim, num_layers, num_imfs):
                super().__init__()
                self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True)
                self.fc = nn.Sequential(nn.Linear(hidden_dim, 64), nn.ReLU(),
                                        nn.Dropout(0.2), nn.Linear(64, num_imfs))
            def forward(self, x):
                out, _ = self.lstm(x)
                return self.fc(out)

        self._model = _VMDLSTM(ckpt["input_dim"], ckpt["hidden_dim"], ckpt["num_layers"],
                               ckpt["num_imfs"])
        self._model.load_state_dict(ckpt["state_dict"])
        self._model.eval()
        self._mean = np.asarray(ckpt["scaler_mean"], dtype=np.float32)
        self._std = np.asarray(ckpt["scaler_scale"], dtype=np.float32)
        self._K = ckpt["num_imfs"]
        self._hol = self._load_holidays()

        # 过去 3 天 24 点负荷（lag 用）
        self._lag = np.zeros((3, self.P), dtype=np.float32)
        self._today_obs = {}
        self._last_day = None
        self._cache_day = None
        self._cache_today = None
        self._cache_tomorrow = None

    @staticmethod
    def _load_holidays():
        try:
            import holidays
            return holidays.country_holidays("DE", years=[2026])
        except Exception:
            return set()

    def set_yesterday_load(self, load_3days) -> None:
        """用准备阶段最后 3 天的 24 点负荷初始化 lag。load_3days: (3, 24) 或拉平。"""
        arr = np.asarray(load_3days, dtype=np.float32).reshape(-1)
        if arr.shape[0] >= 3 * self.P:
            self._lag = arr[-3 * self.P:].reshape(3, self.P).copy()

    def roll_day(self) -> None:
        t = np.array([self._today_obs.get(i, float(self._lag[-1, i]))
                      for i in range(self.P)], dtype=np.float32)
        self._lag = np.vstack([self._lag[1:], t])
        self._today_obs = {}

    def observe(self, slot: int, load_kw: float, day: int = None) -> None:
        if day is not None and self._last_day is not None and day != self._last_day:
            self.roll_day()
            self._cache_day = None
        self._last_day = day
        self._today_obs[int(slot) % self.P] = float(load_kw)

    def _build_input(self, weather_24, price_24, dow: int) -> np.ndarray:
        w = np.asarray(weather_24, dtype=np.float32).reshape(self.P, 2)
        pr = np.asarray(price_24, dtype=np.float32).reshape(self.P, 2)
        hours = np.arange(self.P, dtype=np.float32)
        feat = np.empty((self.P, 12), dtype=np.float32)
        for i in range(self.P):
            feat[i, 0] = self._lag[0, i]
            feat[i, 1] = self._lag[1, i]
            feat[i, 2] = self._lag[2, i]
            feat[i, 3] = 0.0   # holiday 运行时按天填入（见 forecast）
            feat[i, 4] = pr[i, 0]
            feat[i, 5] = pr[i, 1]
            feat[i, 6] = w[i, 0]
            feat[i, 7] = w[i, 1]
            feat[i, 8] = np.sin(2 * np.pi * hours[i] / 24.0)
            feat[i, 9] = np.cos(2 * np.pi * hours[i] / 24.0)
            feat[i, 10] = np.sin(2 * np.pi * dow / 7.0)
            feat[i, 11] = np.cos(2 * np.pi * dow / 7.0)
        X = (feat - self._mean) / self._std
        return X[None, ...]   # (1, 24, 12)

    def _predict_day(self, weather_24, price_24, dow, is_holiday):
        X = self._build_input(weather_24, price_24, dow)
        if is_holiday:
            X[:, :, 3] = 1.0
        with self._torch.no_grad():
            imfs = self._model(self._torch.tensor(X, dtype=self._torch.float32)).numpy()
        pred_norm = imfs.sum(axis=-1)[0]   # (24,) 归一化（训练时每站 mean0/std1）
        lag_mean = float(self._lag.mean())
        lag_std = float(self._lag.std()) + 1e-9
        return pred_norm * lag_std + lag_mean   # 反归一化到站点实际量级

    def forecast(self, hour_now, weather_today_24, weather_tomorrow_24,
                 price_today_24, price_tomorrow_24, day=None, dow=None) -> np.ndarray:
        """返回「从 hour_now 起的未来 24 点（1 小时）负荷」。"""
        if day is None or day != self._cache_day:
            dow0 = dow if dow is not None else 0
            hol = self._is_holiday(day, dow0)
            self._cache_today = self._predict_day(weather_today_24, price_today_24, dow0, hol)
            self._cache_tomorrow = self._predict_day(weather_tomorrow_24, price_tomorrow_24,
                                                     (dow0 + 1) % 7, False)
            self._cache_day = day
        hi = int(hour_now) % self.P
        return np.concatenate([self._cache_today[hi:], self._cache_tomorrow[:hi]]).astype(np.float32)

    def _is_holiday(self, day, dow):
        return False   # 简化：运行时 holiday 由调用方处理（此处保守返回 False）


class UmWindowLoadPredictor:
    """UM 窗口 LSTM 负荷预测器（day-ahead 无泄漏，60min 粒度）。

    对应 load_predictor_um_windowlstm.pt（SharedLSTMv6 + 窗口自编码器）。
    推理：维持过去 168h 负荷滚动缓冲 → 取过去 48h 算因果窗口嵌入（无泄漏）→
    拼 25 维输入（窗口嵌入12 + 峰位置1 + lag3 + 逐时刻9）→ LSTM 输出 48h → 取前 N。
    lag1/24/168 中 lag24/lag168 对未来 24h 完全落在历史内（精确）；lag1 用「持最后值」
    近似（首小时精确）。LSTM 逐 tick 因果，故前 24 输出只依赖前 24 输入。
    """

    LOAD_DIR = os.path.join(HERE, "Predictor", "Load_Predictor")
    CKPT = os.path.join(LOAD_DIR, "Upstream_model", "load_predictor_um_windowlstm.pt")
    WINDOW = 48

    def __init__(self):
        import torch
        self._torch = torch
        if self.LOAD_DIR not in sys.path:
            sys.path.insert(0, self.LOAD_DIR)
        import Train_window_lstm_v7_dayahead as T
        self._T = T
        ckpt = torch.load(self.CKPT, map_location="cpu", weights_only=False)
        F = int(ckpt["F"])
        self.model = T.SharedLSTMv6(F, k=2)
        self.model.load_state_dict(ckpt["model"])
        self.model.eval()
        self.feat_mean = np.asarray(ckpt["feat_mean"], dtype=np.float32)
        self.feat_std = np.asarray(ckpt["feat_std"], dtype=np.float32)
        self.encoders = T.load_window_encoders()
        self._hist = []   # 过去负荷（kW，逐时，最多保留 168h）

    def observe(self, hour: int, dow: int, load_kw: float) -> None:
        self._hist.append(float(load_kw))
        if len(self._hist) > 168:
            self._hist = self._hist[-168:]

    def n_obs(self) -> int:
        return len(self._hist)

    def forecast(self, hours, dows, temp_h=None, swr_h=None,
                 import_h=None, export_h=None) -> np.ndarray:
        T = self._T
        hours = np.asarray(hours, dtype=np.float64).reshape(-1)
        n = hours.size
        if len(self._hist) < self.WINDOW:
            return np.zeros(n, dtype=np.float32)
        hist = np.asarray(self._hist, dtype=np.float64)
        base = float(np.percentile(hist, 5)) + 1e-6

        # 峰位置分布（24 维）—— 只用历史
        D = hist.size // 24
        if D >= 2:
            peak_prob = T.peak_position_profile(hist[: D * 24].reshape(D, 24), sigma=1.0)
        else:
            peak_prob = np.zeros(24, dtype=np.float64)
        hour_idx = np.tile(np.arange(24), 2)          # 48
        peak_pos_t = peak_prob[hour_idx]

        # 窗口嵌入（过去 48h，因果无泄漏）
        wemb = T.encode_window_emb(self.encoders, hist[-48:])
        if wemb is None:
            return np.zeros(n, dtype=np.float32)
        wemb_br = np.tile(wemb.reshape(1, -1), (self.WINDOW, 1))   # (48, 12)

        # lag 特征：历史 + 48 个「持最后值」占位（lag1 近似，lag24/168 精确）
        ext = np.concatenate([hist, np.full(self.WINDOW, hist[-1])])
        lagf = T.build_lag_features(ext)
        lag_fc = lagf[len(hist): len(hist) + self.WINDOW]          # (48, 3)

        # 逐时刻特征（9 维）：hour_sin/cos, dow_sin/cos, temp, swr, import, export, holiday
        def _ext(a):
            a = np.asarray(a, dtype=np.float64).reshape(-1)
            if a.size == 0:
                return np.zeros(self.WINDOW)
            if a.size >= self.WINDOW:
                return a[: self.WINDOW]
            return np.concatenate([a, np.full(self.WINDOW - a.size, a[-1])])

        hours48 = _ext(hours); dows48 = _ext(dows)
        temp48 = _ext(temp_h) if temp_h is not None else np.zeros(self.WINDOW)
        swr48 = _ext(swr_h) if swr_h is not None else np.zeros(self.WINDOW)
        imp48 = _ext(import_h) if import_h is not None else np.zeros(self.WINDOW)
        exp48 = _ext(export_h) if export_h is not None else np.zeros(self.WINDOW)
        hol48 = np.zeros(self.WINDOW)   # holiday 运行时保守置 0
        pw = np.stack([
            np.sin(2 * np.pi * hours48 / 24.0), np.cos(2 * np.pi * hours48 / 24.0),
            np.sin(2 * np.pi * dows48 / 7.0), np.cos(2 * np.pi * dows48 / 7.0),
            temp48, swr48, imp48, exp48, hol48,
        ], axis=-1)                                              # (48, 9)

        x = np.concatenate([wemb_br, peak_pos_t[:, None], lag_fc, pw], axis=1)  # (48, 25)
        x = ((x - self.feat_mean) / self.feat_std).astype(np.float32)
        with self._torch.no_grad():
            p, logits, means, logstds = self.model(
                self._torch.tensor(x[None], dtype=self._torch.float32))
        pi = self._torch.softmax(logits, dim=-1)
        mode_enable = T.gmm_mode(means, pi).cpu().numpy()[0]
        p = p.cpu().numpy()[0]
        pred = base + p * np.clip(mode_enable, 0.0, 1.0)         # (48,)
        return pred[: n].astype(np.float32)


class DmLoadPredictor:
    """DM 下游 15min 负荷预测器（站点级，从 UM 微调，BERT 式解耦）。

    对应 Downstream_model/load_predictor_site{n}.pt（DownstreamLSTM）。
    推理：维持过去 192 槽(48h) 15min 负荷滚动缓冲 → 取过去 192 槽降采样到 60min 算
    因果窗口嵌入 z（12 维，来自 UM 冻结自编码器）→ 拼 24 维输入（z12 + lag3 + 时间6 +
    天气电价3）×96 槽 → DM LSTM 直接输出 96 槽(24h) 15min 负荷，无上采样。
    """

    LOAD_DIR = os.path.join(HERE, "Predictor", "Load_Predictor")
    CKPT = os.path.join(LOAD_DIR, "Downstream_model", "load_predictor_site{n}.pt")

    def __init__(self, site_no: int):
        import torch
        self._torch = torch
        self.site_no = int(site_no)
        if self.LOAD_DIR not in sys.path:
            sys.path.insert(0, self.LOAD_DIR)
        import Train_downstream_lstm as TD
        self._TD = TD
        ckpt = torch.load(self.CKPT.format(n=self.site_no), map_location="cpu",
                          weights_only=False)
        F = int(ckpt["F"]); hidden = int(ckpt["hidden"]); num_layers = int(ckpt["num_layers"])
        self.model = TD.DownstreamLSTM(F, hidden, num_layers)
        self.model.load_state_dict(ckpt["model"])
        self.model.eval()
        self.feat_mean = np.asarray(ckpt["feat_mean"], dtype=np.float32)
        self.feat_std = np.asarray(ckpt["feat_std"], dtype=np.float32)
        self.HORIZON = int(ckpt["HORIZON"])      # 96
        self.HIST_15 = int(ckpt["HIST_15"])      # 192
        self.LAGS_15 = list(ckpt["LAGS_15"])     # [4, 96, 192]
        self.encoders = TD.load_window_encoders()
        self._hist = []                          # 15min 负荷历史（kW，最多 192 槽）

    def observe(self, slot: int, load_kw: float) -> None:
        self._hist.append(float(load_kw))
        if len(self._hist) > self.HIST_15:
            self._hist = self._hist[-self.HIST_15:]

    def n_obs(self) -> int:
        return len(self._hist)

    def forecast(self, now, temp_h=None, buy_h=None, sell_h=None) -> np.ndarray:
        """预测从 now 起未来 eff_h 小时(15min) 负荷，eff_h = 48 - now.hour（今天剩余+明天，
        不预测后天——后天无天气/电价数据）。返回 (eff_h*4,) float32。

        temp_h/buy_h/sell_h：未来逐时天气/电价，内部按 eff_h 截断上采样。
        """
        from datetime import timedelta
        TD = self._TD
        eff_h = min(self.HORIZON // 4, 48 - now.hour)   # D 方案：时域封顶到今天剩余+明天
        eff = eff_h * 4                                  # 15min 槽数
        if len(self._hist) < self.HIST_15:
            return np.zeros(eff, dtype=np.float32)
        hist = np.asarray(self._hist, dtype=np.float64)

        # z：过去 48h（192 槽 → 降采样 60min → 48 点）因果嵌入
        hist_60 = TD.downsample_15min_to_60min(hist[-self.HIST_15:])
        if hist_60.size < 48:
            return np.zeros(eff, dtype=np.float32)
        z = TD.encode_window_emb(self.encoders, hist_60)
        if z is None:
            return np.zeros(eff, dtype=np.float32)

        # lag（15min：lag4/96/192）—— 历史 + 持最后值占位
        ext = np.concatenate([hist, np.full(eff, hist[-1])])
        lagf = TD.build_lag_features_15(ext)
        lag_fc = lagf[len(hist): len(hist) + eff]      # (eff, 3)

        # 时间特征（eff 槽）
        hour = []; minute = []; dow = []
        for k in range(eff):
            ts = now + timedelta(minutes=15 * k)
            hour.append(ts.hour + ts.minute / 60.0)
            minute.append(ts.minute)
            dow.append(ts.weekday())
        hour = np.asarray(hour, dtype=np.float64)
        minute = np.asarray(minute, dtype=np.float64)
        dow = np.asarray(dow, dtype=np.float64)

        # 天气/电价：逐时 → 15min（每时重复 4 次）；只取 eff_h 逐时点（全真实，不碰后天）
        def _up15(a):
            a = np.asarray(a, dtype=np.float64).reshape(-1)
            if a.size == 0:
                return np.zeros(eff)
            a = a[:eff_h] if a.size > eff_h else a
            return np.repeat(a, 4)[:eff]

        temp15 = _up15(temp_h) if temp_h is not None else np.zeros(eff)
        imp15 = _up15(buy_h) if buy_h is not None else np.zeros(eff)
        exp15 = _up15(sell_h) if sell_h is not None else np.zeros(eff)

        z_br = np.tile(z.reshape(1, -1), (eff, 1))      # (eff, 12)
        pw = np.stack([
            np.sin(2 * np.pi * hour / 24.0), np.cos(2 * np.pi * hour / 24.0),
            np.sin(2 * np.pi * minute / 60.0), np.cos(2 * np.pi * minute / 60.0),
            np.sin(2 * np.pi * dow / 7.0), np.cos(2 * np.pi * dow / 7.0),
            temp15, imp15, exp15,
        ], axis=-1)                                              # (96, 9)
        x = np.concatenate([z_br, lag_fc, pw], axis=1)           # (96, 24)
        x = ((x - self.feat_mean) / self.feat_std).astype(np.float32)
        with self._torch.no_grad():
            inc = self.model(self._torch.tensor(x[None], dtype=self._torch.float32))
        inc = inc.cpu().numpy()[0].astype(np.float32)            # (96,) 增量（>=0）
        base = float(np.percentile(hist, 5)) + 1e-6              # p5 基线
        return (base + inc).astype(np.float32)                   # (96,) 负荷


def make_predictors(site_no: int, use_site_model: bool = True):
    """工厂：返回 (pv_predictor, load_predictor)。"""
    return PvLstmPredictor(site_no, use_site_model=use_site_model), HistoricalMeanLoad()
