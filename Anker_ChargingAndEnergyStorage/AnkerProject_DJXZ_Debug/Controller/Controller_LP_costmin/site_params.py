# -*- coding: utf-8 -*-
"""site_params.py —— LP 控制器所需的站点物理参数（决赛正式口径）。

依据：SOP §8「必读的物理约束」（额定充放电功率 5000W、电池容量 14 kWh、光伏装机 5000W）
+ Anker_Plans 官方补充回答 Q1（往返效率 0.95 计充电侧、放电不再额外损耗）。

与 Debug1 的差异（必须改）：
    capacity        5 kWh -> 14 kWh
    p_charge_max    2 kW  -> 5 kW
    p_discharge_max 2 kW  -> 5 kW
    pv_peak         1.6 kW-> 5 kW
    p_grid_export   10 kW（卖电目标上限 = 额定 + 光伏装机，SOP §8）

内部单位：kW / kWh / SOC∈[0,1]；对外（HA/HTTP）为 W / %。
"""
from __future__ import annotations

from dataclasses import dataclass

# 官方口径常数（SOP §8 / Anker_Plans Q1）
OFFICIAL_CAPACITY_KWH = 14.0
OFFICIAL_P_CHARGE_MAX_KW = 5.0
OFFICIAL_P_DISCHARGE_MAX_KW = 5.0
OFFICIAL_PV_PEAK_KW = 5.0
OFFICIAL_EFF_CHARGE = 0.95          # 往返效率计充侧（进电池电量 = 购入/光伏电量 × 0.95）
OFFICIAL_EFF_DISCHARGE = 1.0        # 放电不再额外损耗

# 电网交换上限：
#   - 卖电目标（power_control 实体）max = 5000 W（真实 HA 实体属性实测），不是 SOP §8 说的 10000W。
#     power_control = 并网点输出目标；实际卖电 = 光伏盈余 + 电池放电，但目标值被实体封顶在 5000。
#   - 买电上限官方未给，取一个不约束的大值（家庭负载+充电最大不会超过此值）。
OFFICIAL_P_GRID_EXPORT_MAX_KW = 5.0
OFFICIAL_P_GRID_IMPORT_MAX_KW = 20.0


@dataclass
class SiteParams:
    """一个站点的物理参数（供 LP 求解器 model_lp.solve 使用）。"""
    site_id: str
    capacity_kwh: float = OFFICIAL_CAPACITY_KWH
    p_charge_max_kw: float = OFFICIAL_P_CHARGE_MAX_KW
    p_discharge_max_kw: float = OFFICIAL_P_DISCHARGE_MAX_KW
    pv_peak_kw: float = OFFICIAL_PV_PEAK_KW
    p_grid_import_max_kw: float = OFFICIAL_P_GRID_IMPORT_MAX_KW
    p_grid_export_max_kw: float = OFFICIAL_P_GRID_EXPORT_MAX_KW
    eff_charge: float = OFFICIAL_EFF_CHARGE
    eff_discharge: float = OFFICIAL_EFF_DISCHARGE

    @classmethod
    def official(cls, site_id: str) -> "SiteParams":
        """决赛三个站点的物理规格完全一致（SOP §8：3 站统一）。"""
        return cls(site_id=site_id)
