# -*- coding: utf-8 -*-
"""site_params.py —— LP 控制器所需的站点物理参数。

只保留 LP 求解（model_lp.solve）**真正用到**的字段：
  capacity_kwh（电池容量）、eff_charge（充侧效率）、
  p_charge_max_kw / p_discharge_max_kw（充/放电功率上限）、p_grid_max_kw（电网上限）。

这些字段在真实比赛中**通过 HA 拿**：
  capacity       ← sensor.<设备>_rated_energy（"Battery Capacity"，kWh）
  max_charge     ← number.<设备>_battery_power_setpoint 的 attributes.max_charge_power（W）
  max_discharge  ← 同上 attributes.max_discharge_power（W）
  eff_charge     ← 官方常数 0.95
  p_grid_max_kw  ← 官方无此数据，取一个不约束的大值（10 kW）

本文件不包含任何 VE 代码，只是数据结构 + 从 HA 值构造的入口。
"""
from __future__ import annotations

from dataclasses import dataclass

# 官方口径常数
OFFICIAL_EFF_CHARGE = 0.95
OFFICIAL_P_GRID_MAX_KW = 10.0   # 官方无此数据，建模假设（取大值不约束）


@dataclass
class SiteParams:
    """一个站点的物理参数（供 LP 求解器使用）。"""
    site_id: str
    capacity_kwh: float
    p_charge_max_kw: float
    p_discharge_max_kw: float
    p_grid_max_kw: float = OFFICIAL_P_GRID_MAX_KW
    eff_charge: float = OFFICIAL_EFF_CHARGE

    @classmethod
    def from_ha(cls, slug: str, rated_energy_kwh: float,
                max_charge_w: float, max_discharge_w: float) -> "SiteParams":
        """从 HA 读到的值构造（真实比赛路径：全部经 HA 拿）。"""
        return cls(
            site_id=slug,
            capacity_kwh=float(rated_energy_kwh),
            p_charge_max_kw=float(max_charge_w) / 1000.0,
            p_discharge_max_kw=float(max_discharge_w) / 1000.0,
            p_grid_max_kw=OFFICIAL_P_GRID_MAX_KW,
            eff_charge=OFFICIAL_EFF_CHARGE,
        )
