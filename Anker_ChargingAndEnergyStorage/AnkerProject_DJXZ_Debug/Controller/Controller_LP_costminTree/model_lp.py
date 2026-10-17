# -*- coding: utf-8 -*-
"""model_lp.py —— LP 调度模型 + 两个预测器的调用（ONNX 推理，无 torch 依赖）。

LP 建模（每时隙 6 变量 [p_ch, p_dis, p_imp, p_exp, e, p_cur]，kW / kWh）：
    min  Σ (buy·p_imp − sell·p_exp)·Δt  −  V_term·e_H   （e_H = 末端储能，€/kWh）
    s.t. 功率平衡 / SOC 动力学(η_c 充侧) / 边界 / 负价硬约束 / 终端 SOC / 充电余量

相对 Debug1 的三处修正（详见 docs/01 变量统计_歧义消解_LP复核.md §3）：
    L2  末端储能价值项 −V_term·e_H：真实评分是 settled 口径（期末库存市值计入，
        SOP §9.1），滚动 horizon 若不赋予末端 SOC 价值，LP 会在每个 24h 末把电池放空。
        V_term 默认 = 0（纯套利：便宜充/贵放）；main.py 用整场滑动平均买价 settle_est
        作为结算价代理覆盖，避免滚动 horizon 在 24h 末把电池放空。

「谷充峰放」意识（本 LP 的核心）：
    - 买价低谷（b 低）→ 目标自动鼓励充电（b·p_imp 贡献小、甚至负成本）；
    - 卖价峰值（s 高）→ 目标自动鼓励放电/上网（−s·p_exp 贡献大）；
    - 负卖价 → 硬约束不放电、不上网（SOP §5.2）。
    L3  充电余量硬约束（防御性）：η_c·Δt·p_ch[t] + e[t-1] ≤ E，显式禁止「充电功率超过
        当槽物理余量」。实测（check_lp_model）e≤E 已隐含此约束，但显式写出更稳健。
    L4  站点上限改用 site_params 正式口径（14kWh/5kW/5kW/10kW 卖电目标上限）。

预测器用 ONNX（由 Tools/pt2onnx.py 从 .pt 导出），推理只用 onnxruntime。
LP 求解用 scipy.optimize.linprog(HiGHS)。
"""
from __future__ import annotations

import json
import os
import warnings
from functools import lru_cache
from typing import List, Tuple

import numpy as np
import onnxruntime as ort
from scipy.optimize import OptimizeResult
from scipy.sparse import coo_matrix, vstack

from . import predictor_config as pc
from .site_params import SiteParams

HERE = os.path.dirname(os.path.abspath(__file__))
PV_DIR = os.path.join(HERE, "Predictor", "PV_Predictor")
LOAD_DIR = os.path.join(HERE, "Predictor", "Load_Predictor")
PV_ONNX = os.path.join(PV_DIR, "models", "pv_predictor.onnx")
LOAD_ONNX = os.path.join(LOAD_DIR, "models", "load_predictor.onnx")


def featurize_pv(irradiance_w_m2: float, cloud_pct: float, temp_c: float, hour: float,
                 current_pv_kw: float = 0.0) -> np.ndarray:
    """与 Predictor/PV_Predictor/Trainer.featurize 保持一致（含当前光伏锚点）。"""
    return np.array([
        irradiance_w_m2 / pc.IRR_NORM_W_M2,
        cloud_pct / pc.CLOUD_NORM_PCT,
        temp_c / pc.TEMP_NORM_C,
        np.sin(2 * np.pi * hour / 24.0),
        np.cos(2 * np.pi * hour / 24.0),
        current_pv_kw / pc.PV_PEAK_KW,   # 当前实测光伏功率 / 光伏装机
    ], dtype=np.float32)


def featurize_load(hour: float, dow: int, temp_c: float, current_load_kw: float = 0.0) -> np.ndarray:
    """与 Predictor/Load_Predictor/Trainer.featurize 保持一致（含当前负载锚点）。"""
    return np.array([
        np.sin(2 * np.pi * hour / 24.0),
        np.cos(2 * np.pi * hour / 24.0),
        np.sin(2 * np.pi * dow / 7.0),
        np.cos(2 * np.pi * dow / 7.0),
        temp_c / pc.TEMP_NORM_C,
        current_load_kw / pc.LOAD_REF_KW,   # 当前实测负载 / 参考值
    ], dtype=np.float32)


def _warn_norm_mismatch(onnx_path: str, kind: str) -> None:
    """若模型旁有 .norm.json 记录训练时归一化，与当前配置不符则告警（提示需重训）。"""
    meta_path = onnx_path + ".norm.json"
    if not os.path.exists(meta_path):
        return
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        return
    if kind == "pv" and abs(float(meta.get("pv_peak_kw", pc.PV_PEAK_KW)) - pc.PV_PEAK_KW) > 1e-6:
        warnings.warn(
            f"[预测器] {os.path.basename(onnx_path)} 训练时 pv_peak={meta.get('pv_peak_kw')} kW "
            f"与 predictor_config.PV_PEAK_KW={pc.PV_PEAK_KW} kW 不一致——该模型对 5kW 站点失真，"
            f"请用 Tools 重训后重新 pt2onnx。", stacklevel=2)
    if kind == "load" and abs(float(meta.get("load_ref_kw", pc.LOAD_REF_KW)) - pc.LOAD_REF_KW) > 1e-6:
        warnings.warn(
            f"[预测器] {os.path.basename(onnx_path)} 训练时 load_ref={meta.get('load_ref_kw')} kW "
            f"与 predictor_config.LOAD_REF_KW={pc.LOAD_REF_KW} kW 不一致——请重训。", stacklevel=2)


def _load_onnx(path: str, kind: str) -> "ort.InferenceSession":
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"找不到预测器 ONNX：{path}\n请先运行 Tools/pt2onnx.py 导出 .pt → .onnx")
    _warn_norm_mismatch(path, kind)
    return ort.InferenceSession(path, providers=["CPUExecutionProvider"])


@lru_cache(maxsize=32)
def _lp_matrices(H: int, eta: float, dt_h: float, m_bind: float):
    """预构建 LP 约束矩阵（sparse CSR），结构与所有非零值都是常数。

    只依赖 H / eta / dt_h / m_bind（运行中固定），与输入 load/pv/buy/sell/e0 无关。
    复用它，避免 linprog 内部每次 dense→sparse 转换（实测 ~8s 瓶颈）。
    """
    n = 6 * H
    rows_eq, cols_eq, data_eq = [], [], []
    rows_ub, cols_ub, data_ub = [], [], []
    for t in range(H):
        b = t * 6
        # A_ub 前 H 行：L3 充电余量
        rows_ub.append(t); cols_ub.append(b + 0); data_ub.append(eta * dt_h)
        if t > 0:
            rows_ub.append(t); cols_ub.append((t - 1) * 6 + 4); data_ub.append(1.0)
        # A_ub 后 H 行：放电-出口绑定 p_dis ≤ M·p_exp
        rows_ub.append(H + t); cols_ub.append(b + 1); data_ub.append(1.0)
        rows_ub.append(H + t); cols_ub.append(b + 3); data_ub.append(-m_bind)
        # A_eq 功率平衡：-p_ch + p_dis + p_imp - p_exp - p_cur = load - pv
        rows_eq.append(t); cols_eq.append(b + 0); data_eq.append(-1.0)
        rows_eq.append(t); cols_eq.append(b + 1); data_eq.append(+1.0)
        rows_eq.append(t); cols_eq.append(b + 2); data_eq.append(+1.0)
        rows_eq.append(t); cols_eq.append(b + 3); data_eq.append(-1.0)
        rows_eq.append(t); cols_eq.append(b + 5); data_eq.append(-1.0)
        # A_eq SOC 动力学：e[t] = e[t-1] + η·Δt·p_ch − Δt·p_dis
        row = H + t
        rows_eq.append(row); cols_eq.append(b + 0); data_eq.append(-eta * dt_h)
        rows_eq.append(row); cols_eq.append(b + 1); data_eq.append(+dt_h)
        rows_eq.append(row); cols_eq.append(b + 4); data_eq.append(+1.0)
        if t > 0:
            rows_eq.append(row); cols_eq.append((t - 1) * 6 + 4); data_eq.append(-1.0)
    A_eq = coo_matrix((data_eq, (rows_eq, cols_eq)), shape=(2 * H, n))
    A_ub = coo_matrix((data_ub, (rows_ub, cols_ub)), shape=(2 * H, n))
    # 合并成 CSC（A_ub 在上、A_eq 在下），结构与所有非零值都是常数，供直接 HiGHS 复用
    A = vstack([A_ub, A_eq]).tocsc()
    return A


_DEFAULT_OPTIONS = None


def _highs_options():
    """构造 HiGHS 求解器选项（一次缓存），与 scipy linprog 的默认一致。"""
    global _DEFAULT_OPTIONS
    if _DEFAULT_OPTIONS is None:
        from scipy.optimize._highspy._core import (HighsDebugLevel, ObjSense,
                                                   simplex_constants as _s_c)
        _DEFAULT_OPTIONS = {
            'presolve': True,
            'sense': ObjSense.kMinimize,
            'solver': None,
            'time_limit': None,
            'highs_debug_level': HighsDebugLevel.kHighsDebugLevelNone,
            'dual_feasibility_tolerance': None,
            'ipm_optimality_tolerance': None,
            'log_to_console': False,
            'mip_max_nodes': None,
            'output_flag': False,
            'primal_feasibility_tolerance': None,
            'simplex_dual_edge_weight_strategy': None,
            'simplex_strategy': _s_c.SimplexStrategy.kSimplexStrategyDual,
            'ipm_iteration_limit': None,
            'simplex_iteration_limit': None,
            'mip_rel_gap': None,
        }
    return _DEFAULT_OPTIONS


def _solve_highs(c, A, b_ub, b_eq, lb, ub):
    """直接调 scipy 的 HiGHS C++ 接口（绕过 linprog 的 _parse/_clean/vstack 包装层）。

    A 为预构建的合并 CSC 约束矩阵（A_ub 在上、A_eq 在下），结构固定。
    返回 OptimizeResult（含 x / success / eqlin.marginals / message）。
    """
    from scipy.optimize._highspy._core import HighsModelStatus, kHighsInf
    from scipy.optimize._highspy._highs_wrapper import _highs_wrapper

    n_ub = len(b_ub)
    lhs = np.concatenate([np.full(n_ub, -kHighsInf), b_eq])
    rhs = np.concatenate([b_ub, b_eq])
    res = _highs_wrapper(c, A.indptr, A.indices, A.data, lhs, rhs, lb, ub,
                         np.empty(0, dtype=np.uint8), _highs_options())

    x = np.asarray(res.get('x'), dtype=float) if res.get('x') is not None else None
    success = res.get('status') == HighsModelStatus.kOptimal
    message = res.get('message', '')
    marg_eqlin = None
    if res.get('lambda') is not None:
        marg_eqlin = np.asarray(res['lambda'])[n_ub:]   # A_eq 部分的影子价格
    return OptimizeResult(x=x, success=success, message=message,
                          eqlin=OptimizeResult(marginals=marg_eqlin))


class LPModel:
    """预测器（ONNX）+ LP 求解器。"""

    def __init__(self, pv_onnx: str = None, load_onnx: str = None):
        self.pv_sess = _load_onnx(pv_onnx or PV_ONNX, "pv")
        self.load_sess = _load_onnx(load_onnx or LOAD_ONNX, "load")

    # ---------------- 预测（onnxruntime 推理） ----------------
    def forecast_pv(self, irr_f, cloud_f, temp_f, hours, current_pv_kw: float = 0.0) -> np.ndarray:
        X = np.stack([featurize_pv(irr_f[i], cloud_f[i], temp_f[i], hours[i], current_pv_kw)
                      for i in range(len(hours))]).astype(np.float32)
        pred = self.pv_sess.run(None, {"x": X})[0].squeeze(-1)
        return np.clip(pred, 0.0, None)

    def forecast_load(self, temp_f, hours, dows, current_load_kw: float = 0.0) -> np.ndarray:
        X = np.stack([featurize_load(hours[i], dows[i], temp_f[i], current_load_kw)
                      for i in range(len(hours))]).astype(np.float32)
        pred = self.load_sess.run(None, {"x": X})[0].squeeze(-1)
        return np.clip(pred, 0.0, None)

    # ---------------- LP 求解 ----------------
    def solve(self, e0_kwh: float, pv, load, buy, sell, site: SiteParams, dt_h: float = 0.25,
              terminal_soc_floor: float = 0.0, terminal_value_eur_kwh: float = None,
              charge_cap=None) -> dict:
        """求解单站 LP 计划，返回首槽动作 + 全计划 + 影子价格。

        terminal_value_eur_kwh：末端储能的结算价值（€/kWh）。None = 0（纯套利：
        便宜充/贵放，不做跨 horizon 囤电）；main.py 建议传整场滑动平均买价 settle_est。
        charge_cap：逐槽充电功率上限 kW 列表（None = 不限 = p_charge_max）。
        用于「谷价闸门」：仅深谷价小时允许充电，其余小时给 0 上界。
        """
        H = len(buy)
        eta = site.eff_charge
        E = site.capacity_kwh
        n = 6 * H

        if terminal_value_eur_kwh is None:
            # 默认关掉终端储能价值：滚动 horizon 下 V_term=平均买价 会诱发「从电网买电囤电
            # → 下一轮低卖价时段倒卖」的亏损套利（实测 -688%）。LP 只做纯套利（便宜充/贵放）。
            terminal_value_eur_kwh = 0.0

        # 放电-出口绑定的大 M：真实设备 discharge 只能设并网点出口目标，p_exp=0 时电池待机。
        # M 取 p_discharge_max/0.01，使放电时出口目标至少约 1W，避免下发被 round 成 0。
        M_BIND = site.p_discharge_max_kw / 0.01

        # 复用预构建的合并 CSC 约束矩阵（结构与值都是常数）
        A = _lp_matrices(H, eta, dt_h, M_BIND)

        c = np.zeros(n)
        b_eq = np.zeros(2 * H)
        b_ub = np.zeros(2 * H)
        lb = np.zeros(n)
        ub = np.zeros(n)

        for t in range(H):
            b = t * 6
            # 充电允许「买电充电」（QA：charge = 光伏优先、电网补足；谷充峰放核心套利）。
            _cap = site.p_charge_max_kw if charge_cap is None else \
                max(0.0, min(site.p_charge_max_kw, float(charge_cap[t])))
            lb[b + 0], ub[b + 0] = 0.0, _cap
            lb[b + 1], ub[b + 1] = 0.0, site.p_discharge_max_kw
            lb[b + 2], ub[b + 2] = 0.0, site.p_grid_import_max_kw
            lb[b + 3], ub[b + 3] = 0.0, site.p_grid_export_max_kw
            lb[b + 4], ub[b + 4] = 0.0, E
            lb[b + 5], ub[b + 5] = 0.0, max(float(pv[t]), 0.0)

            if sell[t] < 0.0:                 # 负卖价硬约束：不放电、不上网（SOP §5.2）
                ub[b + 1] = 0.0
                ub[b + 3] = 0.0

            c[b + 2] += buy[t] * dt_h
            c[b + 3] -= sell[t] * dt_h
            c[b + 0] += 1e-5 * dt_h          # 微小惩罚，破充/放对称退化
            c[b + 1] += 1e-5 * dt_h

            # L3 余量 RHS + 功率平衡 RHS（A_ub/A_eq 结构已预构建）
            b_ub[t] = E - (e0_kwh if t == 0 else 0.0)
            b_eq[t] = load[t] - pv[t]

        # SOC 首行 RHS = e0（其余 SOC 行 RHS 默认 0）
        b_eq[H] = e0_kwh

        lb[(H - 1) * 6 + 4] = terminal_soc_floor * E
        # L2：末端储能价值（min cost → 减掉末端价值）
        c[(H - 1) * 6 + 4] += -terminal_value_eur_kwh

        res = _solve_highs(c, A, b_ub, b_eq, lb, ub)

        if not res.success:
            return {"status": "infeasible", "charge_kw": 0.0, "discharge_kw": 0.0,
                    "plan": [], "expected_cost_eur": float("nan"), "note": str(res.message)}

        x = res.x.reshape(H, 6)
        x[:, 0] = np.clip(x[:, 0], 0.0, None)
        x[:, 1] = np.clip(x[:, 1], 0.0, None)
        plan = [{"charge_kw": float(x[t, 0]), "discharge_kw": float(x[t, 1]),
                 "import_kw": float(x[t, 2]), "export_kw": float(x[t, 3]),
                 "energy_kwh": float(x[t, 4]), "curtail_kw": float(x[t, 5])}
                for t in range(H)]
        econ = float(sum((x[t, 2] * buy[t] - x[t, 3] * sell[t]) * dt_h for t in range(H)))
        shadow_storage = -float(res.eqlin.marginals[H]) if H < len(res.eqlin.marginals) else 0.0
        shadow_energy = float(res.eqlin.marginals[0])
        return {"status": "ok",
                "charge_kw": float(x[0, 0]),
                "discharge_kw": float(x[0, 1]),
                "import_kw": float(x[0, 2]),
                "export_kw": float(x[0, 3]),
                "plan": plan,
                "expected_cost_eur": econ,
                "terminal_value_eur_kwh": float(terminal_value_eur_kwh),
                "shadow_price_storage_eur_kwh": shadow_storage,
                "shadow_price_energy_eur_kwh": shadow_energy,
                "note": ""}


def robust_solve(e0_kwh: float, pv, load, buy, sell, site: SiteParams, dt_h: float = 0.25,
                 terminal_value_eur_kwh: float = None, charge_cap=None,
                 n_scenarios: int = 0, pv_noise_kw: float = 0.0, load_noise_kw: float = 0.0,
                 seed: int = 42) -> dict:
    """场景法鲁棒 MPC：对 PV/load 预测加高斯噪声生成多条场景，各解一次 LP，
    首槽动作取各场景的期望平均（中性抗噪，不过度保守）。

    - n_scenarios=0：退化为单次确定性 LP（等价 LPModel.solve）。
    - pv_noise_kw / load_noise_kw：噪声标准差（kW）。用官方预报噪声换算：
      PV 噪声 ≈ pv_peak × σ_irr/1000，负载噪声可按负载均值比例设。
    - 平均策略：首槽动作取各场景均值，滤掉单场景噪声的极端抖动；「隐忍」由
      谷价闸门 + 放电闸承担，鲁棒层只负责让决策对预报噪声不敏感。
    """
    def _solve(pv_s, load_s):
        return LPModel.solve(None, e0_kwh, pv_s, load_s, buy, sell, site, dt_h=dt_h,
                             terminal_value_eur_kwh=terminal_value_eur_kwh, charge_cap=charge_cap)

    if n_scenarios <= 0:
        return _solve(pv, load)

    rng = np.random.default_rng(seed)
    sols = []
    for _ in range(n_scenarios):
        pv_s = np.asarray(pv, dtype=float).copy()
        load_s = np.asarray(load, dtype=float).copy()
        if pv_noise_kw > 0:
            pv_s += rng.normal(0.0, pv_noise_kw, len(pv_s))
        if load_noise_kw > 0:
            load_s += rng.normal(0.0, load_noise_kw, len(load_s))
        pv_s = np.clip(pv_s, 0.0, None)
        load_s = np.clip(load_s, 0.0, None)
        sols.append(_solve(pv_s, load_s))

    base = dict(sols[0])
    base["charge_kw"] = sum(s.get("charge_kw", 0.0) for s in sols) / len(sols)
    base["discharge_kw"] = sum(s.get("discharge_kw", 0.0) for s in sols) / len(sols)
    base["export_kw"] = sum(s.get("export_kw", 0.0) for s in sols) / len(sols)
    base["import_kw"] = sum(s.get("import_kw", 0.0) for s in sols) / len(sols)
    base["note"] = f"robust({n_scenarios} scenarios, mean)"
    return base
