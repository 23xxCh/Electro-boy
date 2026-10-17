# -*- coding: utf-8 -*-
"""protocol.py —— LP 动作 → 设备指令映射（本工程核心契约，依据 Anker官方重要QA.txt）。

QA 澄清的两条不对称语义（最容易错，错则反向扣分）：
  - charge    模式：power_control = **电池总充电功率**（光伏优先、电网补足），不是买电功率。
  - discharge 模式：power_control = **并网点输出目标**（= 电池放电 + 光伏余量），不是电池放电功率。
    电池放电 = max(0, 目标 − (光伏 − 负载))；光伏余量已超目标时电池待机、多余弃光。

指令下一 tick（5s 后）执行（QA）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Command:
    """一条待下发的控制指令。grid_flow=None 表示"无电池动作"（待机语义由 standby_mode 决定）。"""
    grid_flow: str | None          # "charge" | "discharge" | None
    power_control_w: float = 0.0

    @property
    def is_standby(self) -> bool:
        return self.grid_flow is None


def command_for_lp(charge_kw: float, discharge_kw: float, export_kw: float,
                   max_power_w: float = 5000.0, sell_now: float = None) -> Command:
    """LP 首槽解 (p_ch, p_dis, p_exp) → 设备指令（对齐官方 QA 语义）。

    映射规则（QA 已澄清）：
      p_ch > 0      -> ("charge", p_ch×1000)     电池总充电目标（光伏优先、电网补足）
      p_exp > 0     -> ("discharge", p_exp×1000) 并网点出口目标（电池放电 = 目标 + 负载缺口）
      负卖价        -> 待机（不放电不上网）；若 p_ch>0 仍 charge（光伏余量充电）
      其余          -> 待机（None）
    注意：power_control=0 时电池待机、既不充也不放（QA）；「补负载不上网」无法通过
    discharge 实现（目标 0 = 待机），故「p_dis>0 且 p_exp=0」按待机处理。
    power_control 实体上限 5000W（实测），此处再夹紧一次，防御性截断。
    """
    eps = 1e-6
    ch = float(charge_kw)
    exp = float(export_kw)
    clamp = lambda w: max(0.0, min(float(w), max_power_w))

    # 充电优先（QA：charge = 电池总充电，光伏优先、电网补足；负卖价时仍可光伏余量充电）
    if ch > eps:
        return Command(grid_flow="charge", power_control_w=round(clamp(ch * 1000.0), 1))
    # 负卖价：不放电、不上网（SOP §5.2），待机
    if sell_now is not None and float(sell_now) < 0.0:
        return Command(grid_flow=None, power_control_w=0.0)
    # 放电：并网点出口目标 = p_exp
    if exp > eps:
        return Command(grid_flow="discharge", power_control_w=round(clamp(exp * 1000.0), 1))
    # 待机（含「p_dis>0 且 p_exp=0」这一真实设备不可实现动作）
    return Command(grid_flow=None, power_control_w=0.0)
