# -*- coding: utf-8 -*-
"""Controller.py —— 沟通 model_lp 与 VirtualEnv 的可复用控制器（纯函数、无状态）。

它不直接依赖 VirtualEnv：只接收 (state, price, weather_forecast, time_features)，
产出调度决策。这样同一份 Controller 既能接 VirtualEnv，也能接真实 HA/API。
"""
from __future__ import annotations

from model_lp import LPModel


class Controller:
    def __init__(self, sites, pv_ckpt: str = None, load_ckpt: str = None,
                 dt_h: float = 0.25, horizon: int = 96):
        self.lp = LPModel(pv_ckpt, load_ckpt)
        self.sites = {s.site_id: s for s in sites}
        self.dt = dt_h
        self.horizon = horizon

    def decide(self, site_id: str, state: dict, buy, sell,
               irr_f, cloud_f, temp_f, hours, dows) -> dict:
        """给定当前状态 + 未来预报，返回 LP 决策（含首个时隙充/放）。"""
        s = self.sites[site_id]
        e0 = state["soc"] * s.capacity_kwh

        # 未来 PV/负载：由预测器从天气预报/日历得到；光伏/负载都用当前实测值作持久性锚点
        pv = self.lp.forecast_pv(irr_f, cloud_f, temp_f, hours, state.get("solar_power_kw", 0.0))
        load = self.lp.forecast_load(temp_f, hours, dows, state.get("home_load_kw", 0.0))

        # 当前时隙用实测真值（官方：soc/solar_power/home_load 可实时读）
        if state.get("solar_power_kw") is not None:
            pv[0] = state["solar_power_kw"]
        if state.get("home_load_kw") is not None:
            load[0] = state["home_load_kw"]

        return self.lp.solve(e0, pv, load, buy, sell, s, self.dt)
