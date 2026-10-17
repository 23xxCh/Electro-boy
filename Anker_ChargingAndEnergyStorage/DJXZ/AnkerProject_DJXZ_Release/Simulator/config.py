# -*- coding: utf-8 -*-
"""config.py —— Simulator 包配置（连接真实环境机 HA + 模拟器 API）。

依据 SOP §2/§3/§5/§7：
    HA     :8123  读设备状态、下发控制（唯一合法链路，Bearer 长期令牌）
    模拟器  :45678 电价/天气/成绩（公开信息源，看板 token，先 POST /api/auth/login）
地区 us-west-2。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SimConfig:
    # 环境机地址（<你的地址>，组织方分发；策略程序跑在参赛机或本地都一样）
    ha_url: str = "http://127.0.0.1:8123"
    sim_url: str = "http://127.0.0.1:45678"

    # HA 长期令牌（HA 网页：头像 → 安全 → 长期访问令牌）
    ha_token: str = ""

    # 模拟器看板账号（独立于 HA 账号）
    sim_user: str = ""
    sim_password: str = ""

    # 官方插件实体前缀（SOP §4.2 / §5.1）
    device_prefix: str = "anker_solix_solarbank_max_ac"

    # 设备后缀 → 站点号显式映射（在 HA 开发者工具搜 anker_solix 查到后填写）
    # 例：{"1": "a1b2c3d4", "2": "e5f6a7b8", "3": "c9d0e1f2"}
    site_suffixes: dict = field(default_factory=dict)

    # 超时
    timeout_s: float = 10.0

    # 待机下发语义（歧义 #6，需自由运行期自检确认）：
    #   "hold"  -> 保持 third_party_control + (charge, 0)（电池待机）
    #   "self_consumption" -> 切回自发自用（电池自动充放=基线行为）
    standby_mode: str = "hold"

    # 控制节奏（SOP §7.2：循环周期秒级；tick=5s；×180 加速）
    tick_seconds: float = 5.0
