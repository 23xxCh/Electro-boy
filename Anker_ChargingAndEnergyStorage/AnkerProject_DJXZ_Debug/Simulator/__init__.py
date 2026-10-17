# -*- coding: utf-8 -*-
"""Simulator —— 与 Solarbank Max AC（真实环境机 HA + 模拟器 API）通信的软件包。

对标 Debug1 的 VE，但这是**客户端**（真实比赛经 HA 读状态/下指令，经 :45678 读电价/天气），
而非本地模拟器。核心契约见 protocol.command_for_lp。
"""
from .config import SimConfig
from .ha_client import HaClient, HaError
from .protocol import Command, command_for_lp
from .sim_api import SimApi, SimApiError
from .solarbank import SLOTS_PER_HOUR, SiteState, SolarBank, upsample_hourly

__all__ = [
    "SimConfig", "SolarBank", "SiteState", "Command", "command_for_lp",
    "HaClient", "HaError", "SimApi", "SimApiError", "SLOTS_PER_HOUR",
    "upsample_hourly",
]
