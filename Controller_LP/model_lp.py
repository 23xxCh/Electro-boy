# -*- coding: utf-8 -*-
"""model_lp.py —— LP 调度模型 + 两个预测器的调用（ONNX 推理版，无 torch 依赖）。

包含：
  1. featurize_pv / featurize_load（与各自 Trainer.py 的特征构造保持一致）；
  2. LPModel：onnxruntime 推理预测 PV/负载 -> 双价 LP 求解 -> 输出充/放计划。

预测器用 **ONNX**（由 convert_onnx.py 从 .pt 导出），推理只用 onnxruntime（轻量），
release 无需 torch。LP 求解用 scipy.optimize.linprog(HiGHS)。

LP 建模（严格按官方口径，与 LPTest_Demo 完全一致）：
  每时隙 6 变量 [p_ch, p_dis, p_imp, p_exp, e, p_cur]
  min  Σ (buy·p_imp − sell·p_exp)·Δt
  s.t. 功率平衡 / SOC 动力学 / 边界 / 负价硬约束 / 终端 SOC。
"""
from __future__ import annotations

import os
from typing import List, Tuple

import numpy as np
import onnxruntime as ort
from scipy.optimize import linprog

HERE = os.path.dirname(os.path.abspath(__file__))
PV_ONNX = os.path.join(HERE, "Weather_Pv_Predicter", "Weather_Pv_Predicter.onnx")
LOAD_ONNX = os.path.join(HERE, "Load_Predicter", "Load_Predicter.onnx")


def featurize_pv(irradiance_w_m2: float, cloud_pct: float, temp_c: float, hour: float,
                 current_pv_kw: float = 0.0) -> np.ndarray:
    """与 Weather_Pv_Predicter/Trainer.featurize 保持一致（含当前光伏锚点）。"""
    return np.array([
        irradiance_w_m2 / 1000.0,
        cloud_pct / 100.0,
        temp_c / 35.0,
        np.sin(2 * np.pi * hour / 24.0),
        np.cos(2 * np.pi * hour / 24.0),
        current_pv_kw / 1.6,   # 当前实测光伏功率 / 峰值
    ], dtype=np.float32)


def featurize_load(hour: float, dow: int, temp_c: float, current_load_kw: float = 0.0) -> np.ndarray:
    """与 Load_Predicter/Trainer.featurize 保持一致（含当前负载锚点）。"""
    return np.array([
        np.sin(2 * np.pi * hour / 24.0),
        np.cos(2 * np.pi * hour / 24.0),
        np.sin(2 * np.pi * dow / 7.0),
        np.cos(2 * np.pi * dow / 7.0),
        temp_c / 35.0,
        current_load_kw / 2.0,   # 当前实测负载 / 参考值
    ], dtype=np.float32)


def _load_onnx(path: str) -> "ort.InferenceSession":
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到预测器 ONNX：{path}\n请先运行 convert_onnx.py 导出 .pt → .onnx")
    return ort.InferenceSession(path, providers=["CPUExecutionProvider"])


class LPModel:
    """预测器（ONNX）+ LP 求解器。"""

    def __init__(self, pv_onnx: str = None, load_onnx: str = None):
        self.pv_sess = _load_onnx(pv_onnx or PV_ONNX)
        self.load_sess = _load_onnx(load_onnx or LOAD_ONNX)

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
    def solve(self, e0_kwh: float, pv, load, buy, sell, site, dt_h: float = 0.25,
              terminal_soc_floor: float = 0.0) -> dict:
        H = len(buy)
        eta = site.eff_charge
        E = site.capacity_kwh
        n = 6 * H

        c = np.zeros(n)
        A_eq = np.zeros((2 * H, n))
        b_eq = np.zeros(2 * H)
        lb = np.zeros(n)
        ub = np.zeros(n)

        for t in range(H):
            b = t * 6
            lb[b + 0], ub[b + 0] = 0.0, site.p_charge_max_kw
            lb[b + 1], ub[b + 1] = 0.0, site.p_discharge_max_kw
            lb[b + 2], ub[b + 2] = 0.0, site.p_grid_max_kw
            lb[b + 3], ub[b + 3] = 0.0, site.p_grid_max_kw
            lb[b + 4], ub[b + 4] = 0.0, E
            lb[b + 5], ub[b + 5] = 0.0, max(float(pv[t]), 0.0)

            if sell[t] < 0.0:
                ub[b + 1] = 0.0
                ub[b + 3] = 0.0

            c[b + 2] += buy[t] * dt_h
            c[b + 3] -= sell[t] * dt_h
            c[b + 0] += 1e-5 * dt_h
            c[b + 1] += 1e-5 * dt_h

        lb[(H - 1) * 6 + 4] = terminal_soc_floor * E

        for t in range(H):
            b = t * 6
            A_eq[t, b + 0] = -1.0
            A_eq[t, b + 1] = +1.0
            A_eq[t, b + 2] = +1.0
            A_eq[t, b + 3] = -1.0
            A_eq[t, b + 5] = -1.0
            b_eq[t] = load[t] - pv[t]
            row = H + t
            A_eq[row, b + 0] = -eta * dt_h
            A_eq[row, b + 1] = +dt_h
            A_eq[row, b + 4] = +1.0
            if t == 0:
                b_eq[row] = e0_kwh
            else:
                A_eq[row, (t - 1) * 6 + 4] = -1.0

        res = linprog(c, A_eq=A_eq, b_eq=b_eq, bounds=list(zip(lb, ub)), method="highs")

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
        shadow_storage = -float(res.eqlin.marginals[H])
        shadow_energy = float(res.eqlin.marginals[0])
        return {"status": "ok",
                "charge_kw": float(x[0, 0]),
                "discharge_kw": float(x[0, 1]),
                "plan": plan,
                "expected_cost_eur": econ,
                "shadow_price_storage_eur_kwh": shadow_storage,
                "shadow_price_energy_eur_kwh": shadow_energy,
                "note": ""}
