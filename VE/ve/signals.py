# -*- coding: utf-8 -*-
"""signals.py —— 4 种模板功率信号 + 高斯白噪声 + 线性叠加。

依据《虚拟家庭环境本地模拟相关设想》：
  P_App(t) = TC[ P_work(t) · R_E(t) ]
  P_work ∈ {阶跃, 正弦(可加阻尼)},  R_E ∈ {阶跃(不停), 方波(间歇)}
  TC(·) 在上升/下降沿各留出占一个周期 5% 的斜坡（用短窗滑动平均近似）。

四种模板（对应手稿第 4 页四张草图）：
  1. damped_sine  阻尼振荡型 : P = TC[B + A·e^{-λt}·sin(ωt)]      （不停工作，收敛到 B）
  2. step_rise    上升稳定型 : P = TC[ step(0→A) ]                 （不停工作）
  3. sine_burst   波形脉冲串 : P = TC[ (A·sin(ωt)+B) · square(T,D) ]（间歇）
  4. rect_burst   矩形脉冲串 : P = TC[ step(0→A) · square(T,D) ]    （间歇）

每台设备的模板参数在构造时随机确定、运行时不改变；
每天叠加影响不大的高斯白噪声使信号“每天稍微不同”；
设备故障模拟（大幅高斯噪声）本阶段不实现，仅保留 hook。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

# 模板名 -> 说明
TEMPLATES = ("damped_sine", "step_rise", "sine_burst", "rect_burst")

# 每种模板的参数随机采样器（返回 dict）。幅度单位为 kW（家庭电器量级）。
# 四个设备叠加后总负载约 0.3~2.5 kW，与 LPTest_Demo 的家庭负载量级一致。


def _rand_params_damped_sine(rng: np.random.Generator) -> dict:
    return {
        "B": float(rng.uniform(0.08, 0.25)),        # 基本分量 kW
        "A": float(rng.uniform(0.15, 0.45)),        # 振荡幅度 kW
        "omega": float(rng.uniform(0.6, 1.8)),      # rad/h
        "decay": float(rng.uniform(0.15, 0.5)),     # 1/h
    }


def _rand_params_step_rise(rng: np.random.Generator) -> dict:
    return {
        "A": float(rng.uniform(0.2, 0.6)),          # 稳定功率 kW
    }


def _rand_params_sine_burst(rng: np.random.Generator) -> dict:
    return {
        "B": float(rng.uniform(0.05, 0.2)),         # 基本分量 kW
        "A": float(rng.uniform(0.15, 0.45)),        # 振荡幅度 kW
        "omega": float(rng.uniform(0.8, 2.0)),      # rad/h
        "T": float(rng.uniform(1.5, 5.0)),          # 使能周期 h
        "D": float(rng.uniform(0.25, 0.7)),         # 占空比
    }


def _rand_params_rect_burst(rng: np.random.Generator) -> dict:
    return {
        "A": float(rng.uniform(0.3, 0.9)),          # 矩形脉冲幅度 kW
        "T": float(rng.uniform(2.0, 6.0)),          # 使能周期 h
        "D": float(rng.uniform(0.15, 0.5)),         # 占空比
    }


RAND_PARAMS: dict[str, Callable[[np.random.Generator], dict]] = {
    "damped_sine": _rand_params_damped_sine,
    "step_rise": _rand_params_step_rise,
    "sine_burst": _rand_params_sine_burst,
    "rect_burst": _rand_params_rect_burst,
}


def _tc_smooth(sig: np.ndarray, ramp_slots: int) -> np.ndarray:
    """TC(·)：在上升/下降沿用短窗滑动平均形成 ~5% 周期的斜坡。"""
    if ramp_slots < 1 or len(sig) == 0:
        return sig
    ramp_slots = min(ramp_slots, len(sig))
    kernel = np.ones(ramp_slots, dtype=float) / ramp_slots
    return np.convolve(sig, kernel, mode="same")


def _square(t: np.ndarray, period_h: float, duty: float, phase_h: float) -> np.ndarray:
    """方波使能信号 R_E(t)：周期 period_h、占空比 duty、相位 phase_h。"""
    if period_h <= 0:
        return np.ones_like(t)
    ph = ((t + phase_h) % period_h) / period_h
    return (ph < duty).astype(float)


def work_signal(template: str, params: dict, t_h: np.ndarray) -> np.ndarray:
    """工作功率信号 P_work(t)（不含使能门控），kW。"""
    t = np.asarray(t_h, dtype=float)
    if template == "damped_sine":
        return params["B"] + params["A"] * np.exp(-params["decay"] * t) * np.sin(params["omega"] * t)
    if template == "step_rise":
        return np.where(t > 0, params["A"], 0.0)
    if template == "sine_burst":
        return params["A"] * np.sin(params["omega"] * t) + params["B"]
    if template == "rect_burst":
        return np.where(t > 0, params["A"], 0.0)
    raise ValueError(f"未知模板 {template}")


def enable_signal(template: str, params: dict, t_h: np.ndarray) -> np.ndarray:
    """工作使能信号 R_E(t)（门控，取值 0/1）。"""
    t = np.asarray(t_h, dtype=float)
    if template in ("damped_sine", "step_rise"):
        return np.ones_like(t)                    # 不停工作（阶跃使能）
    if template in ("sine_burst", "rect_burst"):
        return _square(t, params["T"], params["D"], params.get("phase_h", 0.0))
    raise ValueError(f"未知模板 {template}")


def make_template(template: str, params: dict, t_h: np.ndarray) -> np.ndarray:
    """P_App(t) = P_work(t) · R_E(t)（未做 TC；TC 由 base_signal 施加）。"""
    p = work_signal(template, params, t_h) * enable_signal(template, params, t_h)
    return np.clip(p, 0.0, None)


@dataclass
class PowerTemplate:
    """一台家电设备的功率信号定义：模板 + 固定参数 + 小高斯噪声。

    fault_noise_sigma 预留给“设备故障模拟”（本阶段 None = 不模拟故障）。
    """
    template: str
    params: dict
    noise_sigma_kw: float = 0.02          # 影响不大的高斯白噪声（kW，逐时隙）
    fault_noise_sigma_kw: float | None = None   # 故障时的大噪声（本阶段不启用）
    phase_h: float = 0.0

    @classmethod
    def random(cls, template: str, rng: np.random.Generator,
               noise_sigma_kw: float = 0.02) -> "PowerTemplate":
        params = dict(RAND_PARAMS[template](rng))
        params["phase_h"] = float(rng.uniform(0.0, 6.0))  # 随机相位，避免设备同相叠加
        return cls(template=template, params=params, noise_sigma_kw=noise_sigma_kw,
                   phase_h=params["phase_h"])

    def base_signal(self, slots_per_day: int, dt_h: float) -> np.ndarray:
        """一个完整日（slots_per_day 个时隙）的无噪声基础信号，kW。"""
        t = np.arange(slots_per_day, dtype=float) * dt_h
        sig = make_template(self.template, self.params, t)
        # TC 斜坡：取一个周期 5% 的时隙数（无周期信号取 2 个时隙兜底）
        period = self.params.get("T", 4.0)
        ramp_slots = max(1, int(round(period * 0.05 / dt_h)))
        return _tc_smooth(sig, ramp_slots)

    def sample(self, slot_in_day: int, base_daily: np.ndarray,
               rng: np.random.Generator) -> float:
        """返回某时隙功率：基础信号 + 高斯白噪声（模拟每日微差/测量噪声）。"""
        v = float(base_daily[slot_in_day % len(base_daily)])
        sigma = self.noise_sigma_kw
        if self.fault_noise_sigma_kw is not None:      # 故障模拟 hook（本阶段关闭）
            sigma = self.fault_noise_sigma_kw
        if sigma > 0:
            v += float(rng.normal(0.0, sigma))
        return max(v, 0.0)
