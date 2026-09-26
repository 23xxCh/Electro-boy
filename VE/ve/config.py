# -*- coding: utf-8 -*-
"""config.py —— VirtualEnv (VE) 全局配置与站点物理参数。

VE 是一个**与 LP 算法解耦**的独立模拟器：
  - 三站点独立运行，每个站点 4 台家电设备（4 种模板功率信号线性叠加 -> 站点总负载）；
  - 共享电网电价 API、按站天气 API、评分 Score；
  - 通过 **Modbus TCP**（模拟 Anker SOLIX Solarbank Max AC 寄存器）与官方 HA 插件通信；
  - 通过 **HTTP API**（:45678 风格）与外部调度器 / 看板通信；
  - 通过 **模拟 HA REST**（:8123 风格）给自制看板 / LP 控制器提供 HA 兼容读写面。

内部单位约定（与 LPTest_Demo 保持一致）：
  功率 kW、能量 kWh、电价 €/kWh、辐照 W/m2、云量 %、气温 ℃、SOC ∈ [0,1]。
对外（Modbus / HA / HTTP）功率一律为 **W**、SOC 为 **%**（与官方插件一致）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# ---------------------------------------------------------------------------
# 端口（与官方环境对齐）
# ---------------------------------------------------------------------------
# 官方：3 台设备模拟器 Modbus 端口 1502/1503/1504（不得绕开 HA 直连）；HTTP API :45678。
MODBUS_PORTS = {1: 1502, 2: 1503, 3: 1504}
HTTP_API_PORT = 45678          # 电价/天气/评分/状态/控制 数据接口
HA_REST_PORT = 8123            # 模拟 HA REST（/api/states, /api/services）
UI_PORT = 45679                # 预留（内置 HTML 看板由 HTTP_API 直接提供）

# 设备身份（供官方插件识别为 Solarbank Max AC）
#   PN(0x8000) 被插件 SHA256(salt+PN) 后定位 config/<hash>.yaml
#   SN(10100)  的 17 位规则第 4~7 字符 = "DMWH" -> "Anker SOLIX Solarbank Max AC"
DEVICE_PN = "DMWHVESIM"
DEVICE_SN = "123DMWH4567890123"
DEVICE_SW_VERSION = "v0.0.8.4"
SALT = "anker_solix_ha_2024"   # 官方插件对 PN 的加盐哈希盐值（不可改动）

# 官方口径
EFF_CHARGE = 0.95              # 往返效率计充侧
EFF_DISCHARGE = 1.0
PRICE_SPREAD = 0.008           # 卖价 = 买价 - 0.008 €/kWh

# 官方天气/电价的时间粒度：60min/时隙（逐时，24 点/天）。
# 控制/仿真粒度由 SimConfig.granularity_min 决定（默认 15min/时隙，96 点/天）。
WEATHER_PRICE_GRANULARITY_MIN = 60
HOUR_SLOTS_PER_DAY = 24


@dataclass
class SiteParams:
    """一个站点的设备/物理参数（与 LPTest_Demo.SiteParams 一致）。"""
    site_id: str = "site_1"
    capacity_kwh: float = 5.0
    p_charge_max_kw: float = 2.0
    p_discharge_max_kw: float = 2.0
    p_grid_max_kw: float = 10.0
    pv_peak_kw: float = 1.6
    pv_pr: float = 0.85
    load_base_kw: float = 0.3
    lat: float = 50.0
    eff_charge: float = EFF_CHARGE
    eff_discharge: float = EFF_DISCHARGE


@dataclass
class SimConfig:
    """仿真运行配置。"""
    n_sites: int = 3
    granularity_min: int = 15          # 时隙粒度（min），范围 1-60
    speed: float = 180.0               # 模拟倍速（1 时隙 wall-clock = slot_min*60/speed 秒）
    duration_days: int | None = None   # None = 无限模拟；3-7 = 有限
    start: datetime = field(default_factory=lambda: datetime(2025, 6, 1))
    seed: int = 123
    modbus_ports: dict = field(default_factory=lambda: dict(MODBUS_PORTS))
    http_api_port: int = HTTP_API_PORT
    ha_rest_port: int = HA_REST_PORT
    ui_port: int = UI_PORT
    host: str = "127.0.0.1"
    # 是否在本进程内启动 Modbus / HTTP / HA-REST / UI 服务（run.py / app.py 都开）
    enable_modbus: bool = True
    enable_http_api: bool = True
    enable_ha_rest: bool = True
    enable_ui: bool = True

    @property
    def slots_per_day(self) -> int:
        return 1440 // self.granularity_min

    @property
    def dt_h(self) -> float:
        return self.granularity_min / 60.0

    @property
    def slot_real_seconds(self) -> float:
        return self.granularity_min * 60.0 / self.speed

    def validate(self) -> None:
        if not (1 <= self.granularity_min <= 60):
            raise ValueError("granularity_min 必须在 1-60 min")
        if self.duration_days is not None and not (3 <= self.duration_days <= 7):
            raise ValueError("duration_days 可调范围 3-7 天（或 None = 无限）")
        if self.speed <= 0:
            raise ValueError("speed 必须 > 0")
