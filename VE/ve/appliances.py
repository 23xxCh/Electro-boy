# -*- coding: utf-8 -*-
"""appliances.py —— 家电设备（Appliance）：模板功率信号 + 固定参数 + 高斯白噪声。

一个站点拥有 4 台设备，分别对应 4 种模板功率信号；参数随机、运行时不改；
4 台设备的功率线性叠加即该站点的家庭负载（替代 LPTest_Demo 的 load_from_time）。
"""
from __future__ import annotations

import numpy as np

from .signals import PowerTemplate, TEMPLATES

# 模板 -> 展示名（可个性化，这里给默认名）
TEMPLATE_LABELS = {
    "damped_sine": "变频空调 (Inverter AC)",
    "step_rise": "电热水器 (Water Heater)",
    "sine_burst": "洗衣机 (Washing Machine)",
    "rect_burst": "冰箱 (Refrigerator)",
}


class Appliance:
    """单台家电设备：Name / Power / Importance / Enable。"""

    def __init__(self, name: str, template: PowerTemplate, importance: int,
                 rng_seed: int, slots_per_day: int, dt_h: float):
        self.name = name
        self.template = template
        self.importance = importance          # 1-5 优先级（public）
        self.enable = True                    # 使/失能（public，可控）
        self._base_daily = template.base_signal(slots_per_day, dt_h)  # kW
        self._noise_rng = np.random.default_rng(rng_seed)

    def power_kw(self, slot_in_day: int) -> float:
        """当前时隙功率（kW）：基础信号 + 高斯白噪声（使能=0 时输出 0）。"""
        if not self.enable:
            return 0.0
        return self.template.sample(slot_in_day, self._base_daily, self._noise_rng)

    def info(self) -> dict:
        return {
            "name": self.name,
            "template": self.template.template,
            "importance": self.importance,
            "enable": self.enable,
            "params": {k: round(float(v), 4) for k, v in self.template.params.items()},
            "noise_sigma_kw": self.template.noise_sigma_kw,
        }


def make_site_appliances(site_id: str, rng: np.random.Generator,
                         slots_per_day: int, dt_h: float) -> list[Appliance]:
    """为一个站点生成 4 台设备（四种模板各一台，参数随机，固定）。"""
    # 打乱模板顺序，让不同站点的设备命名/组合略有差异
    order = list(TEMPLATES)
    rng.shuffle(order)
    apps = []
    for i, tpl in enumerate(order):
        pt = PowerTemplate.random(tpl, rng)
        name = f"{site_id}-{TEMPLATE_LABELS[tpl]}"
        apps.append(Appliance(name=name, template=pt, importance=int(rng.integers(1, 6)),
                              rng_seed=int(rng.integers(0, 2**31)),
                              slots_per_day=slots_per_day, dt_h=dt_h))
    return apps
