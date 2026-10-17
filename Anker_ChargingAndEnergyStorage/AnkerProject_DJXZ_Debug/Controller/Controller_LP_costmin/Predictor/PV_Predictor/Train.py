# -*- coding: utf-8 -*-
"""训练 Weather → PV 的多对多 LSTM + XGBoost 级联（逐小时 24 点/天）。

数据：Anker 官方训练数据 solarbank-training-data/*.csv（100 站，逐小时）。
特征：全部气象列（26 列）+ 昨日同时刻光伏（1 列，持久性）经 PCC 筛选
      + 时刻 sin/cos（恒保留）。全部标准化。
标签：光伏功率 kW（pv_power_avg_w / 1000）。

模型：多对多 LSTM（直接预测功率）→ XGBoost 级联（用 LSTM 隐状态+预测+特征修正）。
训练完成后输出：损失-轮次曲线 / 散点 / 单日 / 多日 / 指标表(R2/MAE/RMSE/MSE)，
并保存 LSTM + XGBoost、损失历史 CSV、指标 CSV、图片。

用法（env_ankerproject）：
    python Train.py                 # 100 站，默认参数（400 轮）
    python Train.py 10              # 前 10 站（快速试跑）
    python Train.py 100 400 0.2     # 100 站、400 轮、PCC 阈值 0.2
"""
from __future__ import annotations

import csv
import glob
import os
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import Trainer

try:
    import xgboost as xgb
except ImportError:
    xgb = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "..", "..", "..", "..", "DataSet",
                        "DataSet_100site92days_60mpt", "solarbank-training-data")
CKPT = os.path.join(HERE, "models", "pv_predictor.pt")
XGB_CKPT = os.path.join(HERE, "models", "pv_predictor.xgb.json")

matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "PingFang SC"]
matplotlib.rcParams["axes.unicode_minus"] = False

POINTS_PER_DAY = 24          # 逐小时，一天 24 点
TARGET_COL = "pv_power_avg_w"

# 参与 PCC 筛选的气象列——只保留「运行时天气预报能提供」的 3 个字段
# （weather/today 只返回 solar_irradiance_wm2 / cloud_cover_percent / temperature_c）
# 训练数据里 shortwave_radiation_wm2 ≈ 预报的 solar_irradiance_wm2（同一量，命名不同，推理时做映射）
WEATHER_COLS = ["shortwave_radiation_wm2", "cloud_cover_percent", "temperature_c"]

# 运行时天气预报噪声（模拟 weather/today 的「预报 = 实际 + 高斯噪声」，与官方 VE 一致）。
# 训练时对实际气象加此噪声，让 LSTM 学到「预报气象 → 实际 PV」，与运行时输入分布一致；
# 否则训练用实际、推理用预报会分布偏移（预测系统性偏差）。
FC_SIGMA = {
    "shortwave_radiation_wm2": 60.0,   # W/m²
    "cloud_cover_percent": 15.0,       # %
    "temperature_c": 1.5,              # ℃
}


def add_forecast_noise(feat_days: "np.ndarray", seed: int = 42) -> "np.ndarray":
    """对气象列加高斯噪声，模拟运行时天气预报（预报 = 实际 + 噪声）。

    feat_days: (days, 24, N_WEATHER)。返回同形状（仅气象列加噪，辐照度/云量做物理截断）。
    固定 seed 保证训练可复现。
    """
    out = feat_days.copy()
    rng = np.random.default_rng(seed)
    for i, c in enumerate(WEATHER_COLS):
        sigma = FC_SIGMA.get(c, 0.0)
        if sigma <= 0.0:
            continue
        out[:, :, i] += rng.normal(0.0, sigma, size=out.shape[:2]).astype(np.float32)
        if c == "shortwave_radiation_wm2":
            out[:, :, i] = np.clip(out[:, :, i], 0.0, None)
        elif c == "cloud_cover_percent":
            out[:, :, i] = np.clip(out[:, :, i], 0.0, 100.0)
    return out

# 特征索引约定（add_engineered 拼接顺序）：
#   [0..N_WEATHER-1] = WEATHER_COLS（气象，3 列）
#   [N_WEATHER]      = pv_lag_24（昨日同时刻光伏，持久性）
#   [+1]             = hour_sin
#   [+2]             = hour_cos
N_WEATHER = len(WEATHER_COLS)   # 3
IDX_PV_LAG = N_WEATHER          # 3
IDX_HOUR_SIN = N_WEATHER + 1    # 4
IDX_HOUR_COS = N_WEATHER + 2    # 5
HOUR_IDX = [IDX_HOUR_SIN, IDX_HOUR_COS]


def load_station(csv_path: str):
    """读取单个站点 CSV，按自然日对齐成 (days, 24, 26) 气象特征 + (days, 24) 目标 kW。"""
    days = defaultdict(dict)
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dt = datetime.strptime(row["local_hour_time"], "%Y-%m-%d %H:%M:%S")
            days[dt.strftime("%Y-%m-%d")][dt.hour] = row

    feat_days, tgt_days = [], []
    for date in sorted(days):
        hours = days[date]
        if len(hours) < 24:
            continue
        rows = [hours[h] for h in range(24)]
        feat_days.append([[float(r[c]) for c in WEATHER_COLS] for r in rows])
        tgt_days.append([float(r[TARGET_COL]) / 1000.0 for r in rows])  # W -> kW

    if len(feat_days) < 2:
        return None
    feat = np.asarray(feat_days, dtype=np.float32)
    feat = add_forecast_noise(feat)   # 气象加预报噪声，与运行时 weather/today 分布对齐
    return feat, np.asarray(tgt_days, dtype=np.float32)


def add_engineered(feat_days, tgt_days):
    """拼接 昨日同时刻光伏 + 时刻 sin/cos，并丢弃第一天（无昨日）。

    feat_days: (days, 24, 26)；tgt_days: (days, 24)。返回 (days-1, 24, 29), (days-1, 24)。
    """
    D = feat_days.shape[0]
    hours = np.tile(np.arange(24, dtype=np.float32), (D, 1))
    hour_sin = np.sin(2 * np.pi * hours / 24.0)[..., None]
    hour_cos = np.cos(2 * np.pi * hours / 24.0)[..., None]

    pv_lag = np.zeros_like(tgt_days)
    pv_lag[1:] = tgt_days[:-1]
    pv_lag = pv_lag[..., None]

    feats = np.concatenate([feat_days, pv_lag, hour_sin, hour_cos], axis=-1)
    return feats[1:], tgt_days[1:]


def build_raw_stations(max_sites=None):
    """读取所有站点，返回 [(feat_days, tgt_days), ...]（已含工程特征、已丢首日）。"""
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.csv")))
    if max_sites:
        files = files[:int(max_sites)]
    stations = []
    for csv_path in files:
        loaded = load_station(csv_path)
        if loaded is None:
            continue
        f, t = add_engineered(*loaded)
        if f.shape[0] >= 3:
            stations.append((f, t))
    return stations


def pcc_select(X, y, threshold=0.2):
    """对 (N,24,F) 特征与目标做皮尔逊相关，返回 (保留索引, 各特征 PCC)。"""
    flat_y = y.reshape(-1).astype(np.float64)
    flat_y = flat_y - flat_y.mean()
    denom_y = float(np.sqrt(np.sum(flat_y ** 2)))
    D = X.shape[-1]
    scores = []
    for i in range(D):
        flat = X[:, :, i].reshape(-1).astype(np.float64)
        flat = flat - flat.mean()
        denom = denom_y * float(np.sqrt(np.sum(flat ** 2)))
        c = float(np.sum(flat * flat_y) / denom) if denom > 0 else 0.0
        scores.append(0.0 if np.isnan(c) else c)
    scores = np.asarray(scores)
    keep = np.where(np.abs(scores) >= threshold)[0]
    if len(keep) < 3:
        keep = np.argsort(np.abs(scores))[-5:]
    return np.sort(keep), scores


def r2_score(y_true, y_pred) -> float:
    """决定系数 R²（纯 numpy 实现）。"""
    yt = np.asarray(y_true, dtype=np.float64).reshape(-1)
    yp = np.asarray(y_pred, dtype=np.float64).reshape(-1)
    ss_res = float(np.sum((yt - yp) ** 2))
    ss_tot = float(np.sum((yt - yt.mean()) ** 2))
    return 0.0 if ss_tot == 0 else float(1.0 - ss_res / ss_tot)


def regression_metrics(y_true, y_pred):
    """回归指标：R² / MAE / RMSE / MSE + 峰谷时序（DirAcc/TurnF1）。"""
    yt = np.asarray(y_true).reshape(-1)
    yp = np.asarray(y_pred).reshape(-1)
    err = yp - yt
    m = {
        "R2": r2_score(yt, yp),
        "MAE": float(np.mean(np.abs(err))),
        "RMSE": float(np.sqrt(np.mean(err ** 2))),
        "MSE": float(np.mean(err ** 2)),
    }
    m.update(timing_metrics(y_true, y_pred))
    return m


def timing_metrics(y_true, y_pred, tol: int = 1, smooth: int = 3):
    """峰谷时序指标：方向准确率 + 拐点 F1（±tol 小时容差）。

    y_true/y_pred 形状 (D, 24)。诊断「预测幅度差但时序对不对」——调度器真正要的是
    峰谷时序（何时谷充/峰放），而非幅度。返回 {"DirAcc": ..., "TurnF1": ...}。
    """
    yt = np.asarray(y_true, dtype=np.float64)
    yp = np.asarray(y_pred, dtype=np.float64)
    if yt.ndim == 1:
        yt = yt.reshape(-1, 24); yp = yp.reshape(-1, 24)
    D, H = yt.shape

    # 1) 方向准确率：逐日相邻小时增减方向一致占比（忽略持平段）
    d_true = np.sign(np.diff(yt, axis=1))
    d_pred = np.sign(np.diff(yp, axis=1))
    mask = d_true != 0
    dir_acc = float(np.mean(d_true[mask] == d_pred[mask])) if mask.sum() else float("nan")

    # 3 点中心滑动平均去噪后再找拐点
    def _smooth(x):
        out = x.copy()
        out[:, 1:-1] = (x[:, :-2] + x[:, 1:-1] + x[:, 2:]) / 3.0
        return out

    yt_s = _smooth(yt) if smooth > 1 and H >= smooth else yt
    yp_s = _smooth(yp) if smooth > 1 and H >= smooth else yp

    def turning(x):
        pts = []
        for d in range(D):
            row = x[d]
            for h in range(1, H - 1):
                if row[h] > row[h - 1] and row[h] > row[h + 1]:      # 峰
                    pts.append((d, h))
                elif row[h] < row[h - 1] and row[h] < row[h + 1]:    # 谷
                    pts.append((d, h))
        return pts

    tp_true = turning(yt_s)
    tp_pred = turning(yp_s)

    def f1(true_pts, pred_pts):
        if not pred_pts:
            return 1.0 if not true_pts else 0.0
        if not true_pts:
            return 0.0
        matched = 0
        used = set()
        for (d, h) in pred_pts:
            for (d2, h2) in true_pts:
                if d == d2 and abs(h - h2) <= tol and (d2, h2) not in used:
                    matched += 1
                    used.add((d2, h2))
                    break
        prec = matched / len(pred_pts)
        rec = matched / len(true_pts)
        return 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0

    return {"DirAcc": dir_acc, "TurnF1": f1(tp_true, tp_pred)}


def build_xgb_features(pred, hidden, X_s):
    """把 (天,24) 级数据压平成 XGBoost 表格样本。

    每个 (天, 小时) 一个样本，特征 = [LSTM 隐状态(天级) + LSTM 预测(1) + 标准化特征(F)]。
    """
    N, P, F = X_s.shape
    hid_rep = np.repeat(hidden, P, axis=0)          # (N*P, hidden)
    pred_flat = pred.reshape(N * P, 1)              # (N*P, 1)
    x_flat = X_s.reshape(N * P, F)                  # (N*P, F)
    return np.concatenate([hid_rep, pred_flat, x_flat], axis=1).astype(np.float32)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    Trainer.set_seed()

    max_sites = int(sys.argv[1]) if len(sys.argv) > 1 else None
    epochs = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    pcc_threshold = float(sys.argv[3]) if len(sys.argv) > 3 else 0.2
    lr = float(sys.argv[4]) if len(sys.argv) > 4 else 1e-3
    hidden = int(sys.argv[5]) if len(sys.argv) > 5 else 128
    alpha = float(sys.argv[6]) if len(sys.argv) > 6 else 0.5

    print(f"加载站点（max_sites={max_sites or '全部'}）...")
    stations = build_raw_stations(max_sites)
    if not stations:
        print("未加载到任何站点数据，请检查 solarbank-training-data 目录。")
        return
    print(f"有效站点数={len(stations)}")

    # ---- 1. 按站内时间顺序划分 train/val/test（避免时序泄漏）----
    VAL_RATIO, TEST_RATIO = 0.15, 0.15
    train_list, val_list, test_list = [], [], []
    for f, t in stations:
        n = f.shape[0]
        n_test = int(n * TEST_RATIO)
        n_val = int(n * VAL_RATIO)
        n_train = n - n_val - n_test
        train_list.append((f[:n_train], t[:n_train]))
        val_list.append((f[n_train:n_train + n_val], t[n_train:n_train + n_val]))
        test_list.append((f[n_train + n_val:], t[n_train + n_val:]))

    Xtr = np.concatenate([f for f, _ in train_list], axis=0)
    ytr = np.concatenate([t for _, t in train_list], axis=0)
    Xva = np.concatenate([f for f, _ in val_list], axis=0)
    yva = np.concatenate([t for _, t in val_list], axis=0)
    Xte = np.concatenate([f for f, _ in test_list], axis=0)
    yte = np.concatenate([t for _, t in test_list], axis=0)
    print(f"样本（天）：训练 {Xtr.shape[0]} / 验证 {Xva.shape[0]} / 测试 {Xte.shape[0]}")

    # ---- 2. PCC 特征筛选（气象 + 昨日光伏；时刻 sin/cos 恒保留）----
    pcc_names = WEATHER_COLS + ["pv_lag_24"]
    keep_idx, pcc_scores = pcc_select(Xtr[..., :N_WEATHER + 1], ytr, threshold=pcc_threshold)
    final_idx = list(keep_idx) + HOUR_IDX
    final_names = [pcc_names[i] if i < N_WEATHER + 1 else
                   ("hour_sin" if i == IDX_HOUR_SIN else "hour_cos")
                   for i in final_idx]
    print(f"\n各候选特征 PCC（|PCC|≥{pcc_threshold} 保留，时刻 sin/cos 恒保留）：")
    for i, name in enumerate(pcc_names):
        mark = "✓" if i in keep_idx else " "
        print(f"  [{mark}] {name:28s} PCC={pcc_scores[i]:+.4f}")
    print(f"输入特征({len(final_names)} 维)：{final_names}")

    Xtr = Xtr[..., final_idx]
    Xva = Xva[..., final_idx]
    Xte = Xte[..., final_idx]
    F = Xtr.shape[-1]

    # ---- 3. 标准化（均值和标准差只在训练集上算，避免泄漏）----
    flat_tr = Xtr.reshape(-1, F)
    mean = flat_tr.mean(axis=0)
    std = flat_tr.std(axis=0)
    std = np.where(std == 0, 1.0, std)

    def _scale(X):
        return ((X.reshape(-1, F) - mean) / std).reshape(-1, POINTS_PER_DAY, F).astype(np.float32)

    Xtr = _scale(Xtr)
    Xva = _scale(Xva)
    Xte = _scale(Xte)

    # ---- 4. 训练 LSTM ----
    print(f"\n训练 LSTM（input_dim={F}, epochs={epochs}, lr={lr}, "
          f"hidden={hidden}, weight_decay=1e-4, loss_alpha={alpha}, 早停 patience=20）...")
    model, history = Trainer.train_model(Xtr, ytr, Xva, yva, input_dim=F,
                                         hidden_dim=hidden,
                                         epochs=epochs, lr=lr,
                                         weight_decay=1e-4, loss_alpha=alpha)

    # ---- 5. LSTM 预测与评估 ----
    pred_tr = Trainer.predict(model, Xtr)
    pred_va = Trainer.predict(model, Xva)
    pred_te = Trainer.predict(model, Xte)

    m_tr = regression_metrics(ytr, pred_tr)
    m_va = regression_metrics(yva, pred_va)
    m_te = regression_metrics(yte, pred_te)

    # ---- 5.5 XGBoost 级联（修正 LSTM 预测）----
    if xgb is None:
        raise SystemExit("缺少 xgboost 库：请先 pip install xgboost")
    print(f"\n训练 XGBoost 级联（修正 LSTM 预测）...")
    p_tr, h_tr = Trainer.extract_features(model, Xtr)
    p_va, h_va = Trainer.extract_features(model, Xva)
    p_te, h_te = Trainer.extract_features(model, Xte)

    Xgb_tr = build_xgb_features(p_tr, h_tr, Xtr)
    Xgb_va = build_xgb_features(p_va, h_va, Xva)
    Xgb_te = build_xgb_features(p_te, h_te, Xte)

    y_flat_tr = ytr.reshape(-1)
    y_flat_va = yva.reshape(-1)
    y_flat_te = yte.reshape(-1)

    dtr = xgb.DMatrix(Xgb_tr, label=y_flat_tr)
    dva = xgb.DMatrix(Xgb_va, label=y_flat_va)
    params = {
        "objective": "reg:squarederror", "max_depth": 5, "eta": 0.05,
        "subsample": 0.8, "colsample_bytree": 0.8,
        "alpha": 0.1, "lambda": 1.0, "seed": 42,
    }
    xgb_model = xgb.train(params, dtr, num_boost_round=500,
                          evals=[(dva, "val")], early_stopping_rounds=30,
                          verbose_eval=False)
    print(f"  XGBoost 最佳轮数={xgb_model.best_iteration}")

    pred_xgb_tr = xgb_model.predict(xgb.DMatrix(Xgb_tr)).reshape(-1, POINTS_PER_DAY)
    pred_xgb_va = xgb_model.predict(xgb.DMatrix(Xgb_va)).reshape(-1, POINTS_PER_DAY)
    pred_xgb_te = xgb_model.predict(xgb.DMatrix(Xgb_te)).reshape(-1, POINTS_PER_DAY)

    m_xgb_tr = regression_metrics(ytr, pred_xgb_tr)
    m_xgb_va = regression_metrics(yva, pred_xgb_va)
    m_xgb_te = regression_metrics(yte, pred_xgb_te)

    # ---- 6. 保存模型（LSTM + XGBoost）----
    Trainer.save_checkpoint(model, CKPT, {
        "input_dim": F,
        "hidden_dim": model.hidden_dim,
        "num_layers": model.num_layers,
        "output_len": model.output_len,
        "feature_names": final_names,
        "pcc_threshold": pcc_threshold,
        "scaler_mean": mean.tolist(),
        "scaler_scale": std.tolist(),
        "points_per_day": POINTS_PER_DAY,
    })
    xgb_model.save_model(XGB_CKPT)
    print(f"\n已保存模型：{CKPT}")
    print(f"已保存 XGBoost：{XGB_CKPT}")

    # ---- 7. 指标表（打印 + 存 CSV）----
    _print_metrics_table(m_tr, m_va, m_te, m_xgb_tr, m_xgb_va, m_xgb_te)
    _save_metrics_csv(m_tr, m_va, m_te, m_xgb_te)

    # ---- 8. 损失历史落盘 ----
    _save_loss_history(history)

    # ---- 9. 绘图 ----
    _plot_results(history, yte, pred_xgb_te, pcc_scores, pcc_names, keep_idx)
    print("训练完成。")


def _print_metrics_table(m_tr, m_va, m_te, m_xgb_tr, m_xgb_va, m_xgb_te):
    print("\n" + "=" * 74)
    print("      回归指标（kW 口径）—— 纯 LSTM vs LSTM+XGBoost")
    print("=" * 74)
    print(f"  {'指标':<6}{'LSTM训练':>11}{'LSTM验证':>11}{'LSTM测试':>11}"
          f"{'XGB训练':>11}{'XGB验证':>11}{'XGB测试':>11}")
    print("-" * 74)
    for k in ("R2", "MAE", "RMSE", "MSE", "DirAcc", "TurnF1"):
        print(f"  {k:<6}{m_tr[k]:>11.4f}{m_va[k]:>11.4f}{m_te[k]:>11.4f}"
              f"{m_xgb_tr[k]:>11.4f}{m_xgb_va[k]:>11.4f}{m_xgb_te[k]:>11.4f}")
    print("=" * 74)
    print(f"  测试集 R2：纯LSTM {m_te['R2']:.4f} -> LSTM+XGB {m_xgb_te['R2']:.4f} "
          f"（提升 {m_xgb_te['R2']-m_te['R2']:+.4f}）")


def _save_metrics_csv(m_tr, m_va, m_te, m_xgb_te):
    out = os.path.join(HERE, "results", "PV_Predictor_Metrics.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "lstm_train", "lstm_val", "lstm_test", "xgb_test"])
        for k in ("R2", "MAE", "RMSE", "MSE", "DirAcc", "TurnF1"):
            w.writerow([k, f"{m_tr[k]:.6f}", f"{m_va[k]:.6f}", f"{m_te[k]:.6f}",
                        f"{m_xgb_te[k]:.6f}"])
    print(f"指标表已保存：{out}")


def _save_loss_history(history):
    out = os.path.join(HERE, "results", "PV_Predictor_Loss_History.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epoch", "train_loss", "val_loss"])
        for i, (tr, va) in enumerate(zip(history["train_loss"], history["val_loss"]), start=1):
            w.writerow([i, f"{tr:.6f}", f"{va:.6f}"])
    va = history["val_loss"]
    tr = history["train_loss"]
    best = int(np.argmin(va)) + 1
    print(f"损失历史已保存：{out}")
    print(f"  验证损失：初始={va[0]:.4f} 最小=第{best}轮({min(va):.4f}) "
          f"末轮={va[-1]:.4f}（末轮-最小={va[-1]-min(va):+.4f}）")
    print(f"  训练损失：初始={tr[0]:.4f} 末轮={tr[-1]:.4f}")


def _plot_results(history, y_test, pred_final, pcc_scores, pcc_names, keep_idx):
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))

    ax = axes[0][0]
    ax.plot(history["train_loss"], label="训练损失", lw=1.5)
    ax.plot(history["val_loss"], label="验证损失", lw=1.5)
    ax.set_title("损失-轮次曲线")
    ax.set_xlabel("轮次 (epoch)")
    ax.set_ylabel("MSE 损失")
    ax.legend()
    ax.grid(alpha=0.3)

    ax = axes[0][1]
    yf, pf = y_test.reshape(-1), pred_final.reshape(-1)
    r2 = r2_score(yf, pf)
    ax.scatter(yf, pf, s=4, alpha=0.25)
    lim = [0, max(float(np.max(yf)), float(np.max(pf))) * 1.05]
    ax.plot(lim, lim, "r--", lw=1, label="y=x")
    ax.set_xlim(lim); ax.set_ylim(lim)
    ax.set_xlabel("真实光伏 kW")
    ax.set_ylabel("预测光伏 kW")
    ax.set_title(f"预测 vs 真实 散点（测试集 R2={r2:.4f}）")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)

    ax = axes[1][0]
    day_idx = int(np.argmax(y_test.sum(axis=1)))
    ax.plot(y_test[day_idx], "k-", lw=2, label="真实")
    ax.plot(pred_final[day_idx], "r-", lw=1.5, label="预测")
    ax.set_title(f"单日预测 vs 真实（测试集第 {day_idx + 1} 天）")
    ax.set_xlabel("小时 (0-23)")
    ax.set_ylabel("功率 kW")
    ax.legend()
    ax.grid(alpha=0.3)

    ax = axes[1][1]
    days = min(5, y_test.shape[0])
    ax.plot(y_test[:days].reshape(-1), "k-", lw=1.8, label="真实")
    ax.plot(pred_final[:days].reshape(-1), "r-", lw=1.2, label="预测")
    for d in range(1, days):
        ax.axvline(d * POINTS_PER_DAY, color="gray", linestyle=":", alpha=0.4)
    ax.set_title(f"多日预测 vs 真实（前 {days} 天）")
    ax.set_xlabel("时间步 (小时)")
    ax.set_ylabel("功率 kW")
    ax.legend()
    ax.grid(alpha=0.3)

    plt.tight_layout()
    out = os.path.join(HERE, "results", "PV_Predictor_Results.png")
    plt.savefig(out, dpi=120)
    plt.close(fig)
    print(f"结果图已保存：{out}")

    fig2, ax2 = plt.subplots(figsize=(13, 5))
    colors = ["tab:red" if i in keep_idx else "tab:gray" for i in range(len(pcc_names))]
    ax2.bar(range(len(pcc_names)), pcc_scores, color=colors)
    ax2.axhline(0, color="k", lw=0.7)
    ax2.set_xticks(range(len(pcc_names)))
    ax2.set_xticklabels(pcc_names, rotation=45, ha="right", fontsize=8)
    ax2.set_ylabel("PCC")
    ax2.set_title("候选特征与光伏功率的皮尔逊相关系数（红=保留，灰=剔除）")
    ax2.grid(alpha=0.3, axis="y")
    plt.tight_layout()
    out2 = os.path.join(HERE, "results", "PV_Predictor_Pcc.png")
    plt.savefig(out2, dpi=120)
    plt.close(fig2)
    print(f"PCC 图已保存：{out2}")


if __name__ == "__main__":
    main()
