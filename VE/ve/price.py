# -*- coding: utf-8 -*-
"""price.py —— 电网电价生成（三站共享）+ 今明逐时电价查询。

复用 LPTest_Demo/VirtualEnv.py 的 gen_price 物理规则（早/晚峰 + 正午光伏谷，
正午谷深由“市场光伏充裕度”AR(1) 驱动 -> 晴天正午负电价；卖价 = 买价 - 0.008）。

**时间粒度 = 60min/时隙（逐时，24 点/天）**，与官方天气/电价口径一致；
控制/仿真仍按 SimConfig.granularity_min（默认 15min）运行，由引擎把逐时电价
映射到对应的 15min 时隙（同一小时内的 4 个时隙共享同一小时电价）。

对外提供当日 / 明日逐时电价：买电价 buy、卖电价 sell、价差 spread(=buy-sell)。
电价确定性（日前已知，预报=实际，无误差）。
"""
from __future__ import annotations

import numpy as np

from .config import HOUR_SLOTS_PER_DAY, PRICE_SPREAD


class PriceGenerator:
    """按日生成共享日前电价（买/卖），€/kWh，逐时（24 点/天）。"""

    def __init__(self, rng: np.random.Generator, base: float = 0.065,
                 spread: float = PRICE_SPREAD):
        self.rng = rng
        self.base = base
        self.spread = spread
        self.slots_per_day = HOUR_SLOTS_PER_DAY     # 24（逐时）
        self.dt_h = 1.0                              # 1 小时/时隙
        self._dayf_state = 0.5
        self._cache: dict[int, dict] = {}

    def day(self, d: int) -> dict:
        """返回第 d 天的 dict(buy, sell, spread) 数组（长度 24，逐时）。"""
        while max(self._cache, default=-1) < d:
            nd = (max(self._cache, default=-1) + 1)
            self._cache[nd] = self._gen_day(nd)
        return self._cache[d]

    def _gen_day(self, d: int) -> dict:
        f = self._dayf_state
        f = 0.35 + 0.6 * (f - 0.35) + 0.18 * self.rng.normal()
        self._dayf_state = float(np.clip(f, 0.05, 0.95))
        dayf = self._dayf_state
        buy = np.zeros(self.slots_per_day)
        for h in range(self.slots_per_day):          # hour = h（0..23）
            morning = 0.045 * np.exp(-((h - 8.0) ** 2) / (2 * 1.3 ** 2))
            evening = 0.060 * np.exp(-((h - 19.5) ** 2) / (2 * 1.6 ** 2))
            midday = -0.09 * np.exp(-((h - 13.0) ** 2) / (2 * 2.2 ** 2)) * (0.3 + 0.9 * dayf)
            buy[h] = self.base + morning + evening + midday + self.rng.normal(0.0, 0.006)
        sell = buy - self.spread
        return {"buy": buy, "sell": sell, "spread": buy - sell}
