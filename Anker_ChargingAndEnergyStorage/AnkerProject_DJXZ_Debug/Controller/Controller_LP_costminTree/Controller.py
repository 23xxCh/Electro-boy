# -*- coding: utf-8 -*-
"""Controller.py —— 沟通 model_lp 与外部环境（Simulator/真实 HA）的可复用控制器（纯函数、无状态）。

只接收 (state, price, weather_forecast, time_features)，产出调度决策；
不直接依赖 Simulator / HA，便于单测与回测。
"""
from __future__ import annotations

from .model_lp import LPModel
from .site_params import SiteParams


class Controller:
    def __init__(self, sites, pv_onnx: str = None, load_onnx: str = None,
                 dt_h: float = 0.25, horizon: int = 96,
                 terminal_value_eur_kwh: float = None):
        self.lp = LPModel(pv_onnx, load_onnx)
        self.sites = {s.site_id: s for s in sites}
        self.dt = dt_h
        self.horizon = horizon
        self.terminal_value = terminal_value_eur_kwh   # None = 用 horizon 平均买价代理

    def decide(self, site_id: str, state: dict, buy, sell,
               irr_f, cloud_f, temp_f, hours, dows) -> dict:
        """给定当前状态 + 未来预报，返回 LP 决策（含首个时隙充/放 + 全计划 + 影子价格）。"""
        s: SiteParams = self.sites[site_id]
        e0 = state["soc"] * s.capacity_kwh

        pv = self.lp.forecast_pv(irr_f, cloud_f, temp_f, hours, state.get("solar_power_kw", 0.0))
        load = self.lp.forecast_load(temp_f, hours, dows, state.get("home_load_kw", 0.0))

        # 当前时隙用实测真值（soc/solar_power/home_load 可实时读，SOP §5.1）
        if state.get("solar_power_kw") is not None:
            pv[0] = state["solar_power_kw"]
        if state.get("home_load_kw") is not None:
            load[0] = state["home_load_kw"]

        return self.lp.solve(e0, pv, load, buy, sell, s, self.dt,
                             terminal_value_eur_kwh=self.terminal_value)
