# -*- coding: utf-8 -*-
"""weather.py —— 站点天气生成 + 今明逐时天气预报。

复用 LPTest_Demo/VirtualEnv.py 的物理规则（gen_weather / pv_from_weather /
_sun_elev_sin），但重构为**按日惰性生成**（支持无限模拟），并补充：
  - weather_type（天气类型）：由云量启发式映射（官方天气 API 需要该字段，
    LPTest_Demo 未提供，此处用云量做代理）；
  - 逐时（逐时隙）预报：今明两天，含 天气类型 / 云量 / 辐照 / 温度。

预报 ≠ 实际：天气预报 = 实际 + 高斯误差（与 LPTest_Demo 口径一致）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from .config import SiteParams

# 预报误差（与 LPTest_Demo make_forecast 一致）
FC_SIGMA_IRR = 60.0     # W/m2
FC_SIGMA_CLOUD = 15.0   # %
FC_SIGMA_TEMP = 1.5     # ℃


def _sun_elev_sin(doy: int, hour: float, lat: float) -> float:
    """近似太阳高度角正弦（0 = 地平线下）。"""
    decl = np.deg2rad(23.44 * np.sin(2 * np.pi * (284 + doy) / 365.0))
    lat_r = np.deg2rad(lat)
    ha = np.deg2rad(15.0 * (hour - 12.0))
    s = np.sin(lat_r) * np.sin(decl) + np.cos(lat_r) * np.cos(decl) * np.cos(ha)
    return float(np.clip(s, 0.0, None))


def weather_type_from_cloud(cloud_pct: float) -> str:
    """云量 -> 天气类型（启发式代理，非官方映射）。"""
    c = float(cloud_pct)
    if c < 10:
        return "sunny"
    if c < 40:
        return "partly_cloudy"
    if c < 70:
        return "cloudy"
    if c < 90:
        return "overcast"
    return "rainy"


class WeatherGenerator:
    """按日生成一个站点的实际天气（辐照/云量/气温），支持无限模拟。

    逐日云量 AR(1)（连晴/连阴），与 LPTest_Demo 同分布。
    **时间粒度 = 60min/时隙（逐时，24 点/天）**，与官方天气口径一致。
    season 用日序(doy)驱动（比 LPTest_Demo 的 d/ndays 更适合任意时长）。
    """

    def __init__(self, site: SiteParams, rng: np.random.Generator, start: datetime):
        self.site = site
        self.rng = rng
        self.start = start
        self.slots_per_day = 24              # 逐时
        self.dt_h = 1.0                      # 1 小时/时隙
        self._cloud_state = 0.45
        self._cache: dict[int, dict] = {}

    def day(self, d: int) -> dict:
        """返回第 d 天（自 start 起）的实际天气 dict(irr, cloud, temp)，逐时（24 点）。"""
        while max(self._cache, default=-1) < d:
            nd = (max(self._cache, default=-1) + 1)
            self._cache[nd] = self._gen_day(nd)
        return self._cache[d]

    def _gen_day(self, d: int) -> dict:
        c = self._cloud_state
        c = 0.35 + 0.6 * (c - 0.35) + 0.18 * self.rng.normal()
        self._cloud_state = float(np.clip(c, 0.05, 0.95))
        day = self.start + timedelta(days=d)
        doy = day.timetuple().tm_yday
        season = (doy - 1) / 365.0
        t_base = 8.0 + 10.0 * season
        irr = np.zeros(self.slots_per_day)
        cloud = np.zeros(self.slots_per_day)
        temp = np.zeros(self.slots_per_day)
        for h in range(self.slots_per_day):          # hour = h（0..23）
            cc = float(np.clip(self._cloud_state + 0.06 * self.rng.normal(), 0.0, 1.0))
            clear_ghi = 1000.0 * _sun_elev_sin(doy, float(h), self.site.lat)
            ghi = float(np.clip(clear_ghi * (1.0 - 0.75 * cc) + self.rng.normal(0.0, 15.0), 0.0, None))
            tt = t_base + 5.0 * np.cos(2 * np.pi * (h - 15.0) / 24.0) - 4.0 * cc + self.rng.normal(0.0, 0.8)
            irr[h] = ghi
            cloud[h] = cc * 100.0
            temp[h] = tt
        return {"irr": irr, "cloud": cloud, "temp": temp}


def pv_from_weather(site: SiteParams, irr: np.ndarray,
                    rng: np.random.Generator) -> np.ndarray:
    """辐照度 -> 光伏功率（近线性，含小噪声），kW。与 LPTest_Demo 一致。"""
    pv = site.pv_peak_kw * (irr / 1000.0) * site.pv_pr * (1.0 + rng.normal(0.0, 0.03, len(irr)))
    return np.clip(pv, 0.0, site.pv_peak_kw)


def make_forecast(actual: np.ndarray, rng: np.random.Generator, sigma: float) -> np.ndarray:
    """预报值 = 实际值 + 高斯误差（预报≠实际）。"""
    return actual + rng.normal(0.0, sigma, len(actual))
