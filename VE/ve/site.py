# -*- coding: utf-8 -*-
"""site.py —— 单个站点：4 台设备叠加负载 + 天气/PV + 电池物理 + LP 状态。

电池物理规则**复用 LPTest_Demo/VirtualEnv.py 的 step()**（单母线、η_c=0.95 充侧、
负价禁放、SOC 动力学、成本结算），保持状态输入/动作输出/优化目标口径不变。

对外（Modbus/HA/HTTP）功率单位 W、SOC %；内部 kW、SOC∈[0,1]。
动作：signed power（负=充电、正=放电），与 LP 动作 p_dis - p_ch 及插件
battery_power_setpoint 寄存器（负=充）一致。
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np

from .appliances import Appliance
from .config import SiteParams
from .weather import WeatherGenerator, pv_from_weather, weather_type_from_cloud

FC_SIGMA_IRR = 60.0
FC_SIGMA_CLOUD = 15.0
FC_SIGMA_TEMP = 1.5


class Site:
    def __init__(self, params: SiteParams, appliances: List[Appliance],
                 weather_gen: WeatherGenerator, pv_rng: np.random.Generator,
                 dt_h: float, slots_per_day: int, soc0: float = 0.5):
        self.params = params
        self.appliances = appliances
        self.weather_gen = weather_gen
        self.pv_rng = pv_rng
        self.dt_h = dt_h
        self.slots_per_day = slots_per_day

        self._day_cache: Dict[int, dict] = {}
        self._soc0 = float(np.clip(soc0, 0.0, 1.0))

        # 电池 / 结算状态（受控 = 外部调度器；基线 = 原生自用，作为"基线调度器"参照）
        self.energy_kwh = self._soc0 * params.capacity_kwh
        self.actual_cost_eur = 0.0
        self.baseline_k_eur = 0.0
        self.cum_charge_kwh = 0.0
        self.cum_discharge_kwh = 0.0
        self.pv_total_kwh = 0.0

        # 基线调度器（原生自用）的影子电池：独立 SOC + 累计电费
        self.baseline_energy_kwh = self._soc0 * params.capacity_kwh
        self.baseline_cost_eur = 0.0

        # 收益指标（给 VisionPanel）：
        #   total_revenue_r_eur   = 累计总收益 R（卖电收入 + 自发自用节省）
        #   settled_revenue_eur   = 结算后收益 R_settled = K − 实际电费
        #   buy_cost_eur          = 累计买电支出
        #   sell_revenue_eur      = 累计卖电收入
        self.total_revenue_r_eur = 0.0
        self.settled_revenue_eur = 0.0
        self.buy_cost_eur = 0.0
        self.sell_revenue_eur = 0.0

        # 控制状态（默认第三方接管模式 + 0 功率）
        self.operating_mode: int = 3          # 3 = third_party_control
        self.power_setpoint_w: int = 0        # signed，负=充、正=放

        # 上一时隙执行结果（供 Modbus/HA/HTTP 读取）
        self.last: dict = {}

    # ---------------- 逐日数据（惰性生成） ----------------
    def _day_data(self, d: int) -> dict:
        if d not in self._day_cache:
            w = self.weather_gen.day(d)
            pv = pv_from_weather(self.params, w["irr"], self.pv_rng)
            self._day_cache[d] = {
                "irr": w["irr"], "cloud": w["cloud"], "temp": w["temp"], "pv": pv,
            }
        return self._day_cache[d]

    def _slot(self, d: int, slot_in_day: int) -> dict:
        dd = self._day_data(d)
        hi = int(slot_in_day * self.dt_h)      # sim 时隙 -> 小时序号（0..23）
        return {
            "irr": float(dd["irr"][hi]),
            "cloud": float(dd["cloud"][hi]),
            "temp": float(dd["temp"][hi]),
            "pv": float(dd["pv"][hi]),         # 逐时 PV（同一小时内恒定）
            "load": sum(app.power_kw(slot_in_day) for app in self.appliances),  # 15min 负载
        }

    # ---------------- LP 所需状态（当前时隙真值） ----------------
    def state(self, d: int, slot_in_day: int) -> dict:
        s = self._slot(d, slot_in_day)
        return {
            "soc": self.energy_kwh / self.params.capacity_kwh,
            "solar_power_kw": s["pv"],
            "home_load_kw": s["load"],
        }

    # ---------------- 天气预报（今明逐时：类型/云量/辐照/温度） ----------------
    @staticmethod
    def _fc_noise(seed: int, idx: int, sigma: float) -> float:
        return float(np.random.default_rng(seed * 1000003 + idx).normal(0.0, sigma))

    def weather_forecast_day(self, d: int, fc_seed: int) -> dict:
        """第 d 天逐时（60min，24 点）天气预报（实际 + 误差）。"""
        dd = self._day_data(d)
        irr, cloud, temp, wtype = [], [], [], []
        for h in range(24):                    # 逐时
            i = d * 24 + h                     # 绝对小时序号（误差确定性、跨查询稳定）
            irr.append(float(dd["irr"][h]) + self._fc_noise(fc_seed, i, FC_SIGMA_IRR))
            c = float(dd["cloud"][h]) + self._fc_noise(fc_seed, i, FC_SIGMA_CLOUD)
            c = float(np.clip(c, 0.0, 100.0))
            cloud.append(c)
            temp.append(float(dd["temp"][h]) + self._fc_noise(fc_seed, i, FC_SIGMA_TEMP))
            wtype.append(weather_type_from_cloud(c))
        return {"irradiance": irr, "cloud": cloud, "temperature": temp,
                "weather_type": wtype}

    # ---------------- 动作执行 + 结算（复用 LPTest_Demo 物理） ----------------
    def _physics(self, energy_kwh: float, a_kw: float, pv: float, load: float,
                 buy: float, sell: float) -> tuple:
        """单步电池物理（单母线 + η_c=0.95 充侧 + 负价禁放），返回结算元组。"""
        s = self.params
        p_ch = min(max(-a_kw, 0.0), s.p_charge_max_kw)
        p_dis = min(max(a_kw, 0.0), s.p_discharge_max_kw)
        if sell < 0.0:
            p_dis = 0.0                      # 负价禁放
        p_ch = min(p_ch, max(0.0, (s.capacity_kwh - energy_kwh) / s.eff_charge / self.dt_h))
        p_dis = min(p_dis, max(0.0, energy_kwh / self.dt_h))
        energy = float(np.clip(
            energy_kwh + s.eff_charge * p_ch * self.dt_h - p_dis * self.dt_h,
            0.0, s.capacity_kwh))
        net = load - pv - p_dis + p_ch
        p_imp = max(net, 0.0)
        p_exp = max(-net, 0.0)
        if sell < 0.0 and p_exp > 0.0:
            p_exp = 0.0                      # 负价弃光
        cost = (p_imp * buy - p_exp * sell) * self.dt_h
        return energy, p_ch, p_dis, p_imp, p_exp, cost

    @staticmethod
    def _native_action_kw(pv: float, load: float, p_ch_max: float, p_dis_max: float) -> float:
        """基线调度器（原生自用）：光伏盈余充电、缺口放电，不做套利。"""
        surplus = pv - load
        if surplus > 0:
            return -min(surplus, p_ch_max)   # 负 = 充电
        return min(-surplus, p_dis_max)      # 正 = 放电

    @staticmethod
    def _decompose_flows(pv: float, load: float, p_ch: float, p_dis: float,
                         p_imp: float, p_exp: float, eff_charge: float) -> dict:
        """把单母线净流分解为 4 节点间的有向能流（kW）。

        约定：光伏优先供负载 → 再充电池 → 再卖电 → 最后弃光；
              负载余量由电池放 → 再电网买；电池放电超负载部分卖电。
        返回 7 条边 + 弃光 + 充电损耗（单位 kW）。
        """
        solar_to_load = min(pv, load)
        pv_rem = pv - solar_to_load
        solar_to_battery = min(pv_rem, p_ch)
        pv_rem2 = pv_rem - solar_to_battery
        solar_to_grid = min(pv_rem2, p_exp)
        curtail = pv_rem2 - solar_to_grid

        load_rem = load - solar_to_load
        battery_to_load = min(load_rem, p_dis)
        grid_to_load = load_rem - battery_to_load

        grid_to_battery = max(0.0, p_ch - solar_to_battery)
        battery_to_grid = max(0.0, p_dis - battery_to_load)
        charge_loss = (1.0 - eff_charge) * p_ch

        return {
            "solar_to_load": solar_to_load, "solar_to_battery": solar_to_battery,
            "solar_to_grid": solar_to_grid, "grid_to_load": grid_to_load,
            "grid_to_battery": grid_to_battery, "battery_to_load": battery_to_load,
            "battery_to_grid": battery_to_grid, "curtail": curtail,
            "charge_loss": charge_loss,
        }

    def apply_action(self, power_w: float, buy: float, sell: float,
                     d: int, slot_in_day: int) -> dict:
        s = self.params
        pv = self._slot(d, slot_in_day)["pv"]
        load = self._slot(d, slot_in_day)["load"]

        # 受控（外部调度器）
        a_kw = float(power_w) / 1000.0
        self.energy_kwh, p_ch, p_dis, p_imp, p_exp, cost = \
            self._physics(self.energy_kwh, a_kw, pv, load, buy, sell)
        self.actual_cost_eur += cost
        self.baseline_k_eur += load * buy * self.dt_h          # 基准 K（负载全买电）
        self.cum_charge_kwh += s.eff_charge * p_ch * self.dt_h
        self.cum_discharge_kwh += p_dis * self.dt_h
        self.pv_total_kwh += pv * self.dt_h

        # 能流分解 + 收益指标
        flow = self._decompose_flows(pv, load, p_ch, p_dis, p_imp, p_exp, s.eff_charge)
        buy_cost = p_imp * buy * self.dt_h                      # 本时隙买电支出
        sell_rev = p_exp * sell * self.dt_h                     # 本时隙卖电收入
        self.buy_cost_eur += buy_cost
        self.sell_revenue_eur += sell_rev
        # 自发自用节省 = (光伏供负载 + 电池供负载) 按买价折算
        self_save = (flow["solar_to_load"] + flow["battery_to_load"]) * buy * self.dt_h
        self.total_revenue_r_eur += sell_rev + self_save
        # 结算后收益 = 基准 K − 实际电费（官方口径 ΔR_settled）
        self.settled_revenue_eur = self.baseline_k_eur - self.actual_cost_eur

        # 基线调度器（原生自用，影子电池，始终运行作参照）
        native_kw = self._native_action_kw(pv, load, s.p_charge_max_kw, s.p_discharge_max_kw)
        self.baseline_energy_kwh, *_bc, b_cost = \
            self._physics(self.baseline_energy_kwh, native_kw, pv, load, buy, sell)
        self.baseline_cost_eur += b_cost

        self.last = {
            "soc": self.energy_kwh / s.capacity_kwh,
            "soc_pct": self.energy_kwh / s.capacity_kwh * 100.0,
            "solar_power_w": pv * 1000.0,
            "home_load_w": load * 1000.0,
            "battery_power_w": (p_dis - p_ch) * 1000.0,        # 负=充、正=放
            "battery_charging_w": p_ch * 1000.0,
            "battery_discharging_w": p_dis * 1000.0,
            "grid_import_w": p_imp * 1000.0,
            "grid_export_w": p_exp * 1000.0,
            "buy": buy, "sell": sell,
            "cost_eur": cost,
            "cum_cost_eur": self.actual_cost_eur,
            "baseline_k_eur": self.baseline_k_eur,
            "baseline_cost_eur": self.baseline_cost_eur,        # 基线调度器累计电费
            "baseline_soc_pct": self.baseline_energy_kwh / s.capacity_kwh * 100.0,
            "cum_charge_kwh": self.cum_charge_kwh,
            "cum_discharge_kwh": self.cum_discharge_kwh,
            "pv_total_kwh": self.pv_total_kwh,
            "operating_mode": self.operating_mode,
            "power_setpoint_w": self.power_setpoint_w,
            # 能流（kW -> W）+ 收益（给 VisionPanel）
            "flow": {k: round(v * 1000.0, 1) for k, v in flow.items()},   # W
            "total_revenue_r_eur": self.total_revenue_r_eur,
            "settled_revenue_eur": self.settled_revenue_eur,
            "buy_cost_eur": self.buy_cost_eur,
            "sell_revenue_eur": self.sell_revenue_eur,
        }
        return self.last

    def site_info(self) -> dict:
        return {
            "site_id": self.params.site_id,
            "capacity_kwh": self.params.capacity_kwh,
            "p_charge_max_kw": self.params.p_charge_max_kw,
            "p_discharge_max_kw": self.params.p_discharge_max_kw,
            "pv_peak_kw": self.params.pv_peak_kw,
            "appliances": [app.info() for app in self.appliances],
        }
