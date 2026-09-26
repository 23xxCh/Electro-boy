# -*- coding: utf-8 -*-
"""runtime.py —— 组装引擎 + Modbus + HTTP API + HA REST 的运行时（run.py / app.py 共用）。"""
from __future__ import annotations

from .config import DEVICE_PN, DEVICE_SN, DEVICE_SW_VERSION, SimConfig
from .engine import VirtualEnvEngine
from .ha_rest import HaRestServer
from .http_api import HttpApiServer
from .modbus_device import ModbusDeviceServer
from .register_map import DeviceRegisterMap


class Runtime:
    def __init__(self, cfg: SimConfig):
        cfg.validate()
        self.cfg = cfg
        self.engine = VirtualEnvEngine(cfg)
        self.regmaps = {}
        self.servers = []
        self._build()

    def _build(self) -> None:
        for sid in self.engine.sites:
            regmap = DeviceRegisterMap(sid, DEVICE_PN, DEVICE_SN, DEVICE_SW_VERSION)

            def on_write(key, value, _sid=sid):
                if key == "operating_mode":
                    self.engine.set_operating_mode(_sid, int(value))
                elif key == "battery_power_setpoint":
                    self.engine.set_command(_sid, float(value))
                elif key == "backup_soc_enable":
                    pass

            regmap.on_write = on_write
            self.regmaps[sid] = regmap
        self.engine.add_on_step(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        """把引擎当前状态刷到各站点 Modbus 寄存器面。"""
        for sid, site in self.engine.sites.items():
            rep = site.last or {}
            if not rep:
                d, si = self.engine.day_slot(self.engine.t)
                st = site.state(d, si)
                rep = {
                    "soc": st["soc"], "soc_pct": st["soc"] * 100.0,
                    "solar_power_w": st["solar_power_kw"] * 1000.0,
                    "home_load_w": st["home_load_kw"] * 1000.0,
                    "battery_power_w": 0.0, "battery_charging_w": 0.0,
                    "battery_discharging_w": 0.0, "grid_import_w": 0.0, "grid_export_w": 0.0,
                    "cum_cost_eur": site.actual_cost_eur, "baseline_k_eur": site.baseline_k_eur,
                    "cum_charge_kwh": site.cum_charge_kwh, "cum_discharge_kwh": site.cum_discharge_kwh,
                    "pv_total_kwh": site.pv_total_kwh, "operating_mode": site.operating_mode,
                    "power_setpoint_w": site.power_setpoint_w,
                }
            self.regmaps[sid].refresh(rep, site.params)

    def start_services(self) -> None:
        cfg = self.cfg
        if cfg.enable_modbus:
            for i, sid in enumerate(self.engine.sites, start=1):
                srv = ModbusDeviceServer(cfg.host, cfg.modbus_ports.get(i, 1500 + i),
                                         self.regmaps[sid], sid)
                srv.start()
                self.servers.append(srv)
        if cfg.enable_http_api:
            api = HttpApiServer(self.engine, cfg.host, cfg.http_api_port)
            api.start()
            self.servers.append(api)
        if cfg.enable_ha_rest:
            ha = HaRestServer(self.engine, cfg.host, cfg.ha_rest_port)
            ha.start()
            self.servers.append(ha)

    def start(self) -> None:
        self.start_services()
        self.engine.start()

    def stop(self) -> None:
        self.engine.stop()
        for s in self.servers:
            s.stop()
        self.servers.clear()

    def summary(self) -> str:
        cfg = self.cfg
        lines = [
            "=" * 70,
            "VE (VirtualEnv) 运行时",
            f"  站点 : {cfg.n_sites} 个（每站 4 台设备）",
            f"  时隙 : {cfg.granularity_min} min（{cfg.slots_per_day} 槽/天）",
            f"  倍速 : {cfg.speed}x（1 时隙 ≈ {cfg.slot_real_seconds:.2f}s）",
            f"  时长 : {'无限' if cfg.duration_days is None else str(cfg.duration_days) + ' 天'}",
            f"  种子 : {cfg.seed}",
        ]
        if cfg.enable_modbus:
            lines.append("  Modbus 设备（官方 HA 插件接入）:")
            for i, sid in enumerate(self.engine.sites, start=1):
                lines.append(f"    {sid} -> {cfg.host}:{cfg.modbus_ports.get(i, 1500 + i)}")
        if cfg.enable_http_api:
            lines.append(f"  HTTP API / 看板 : http://{cfg.host}:{cfg.http_api_port}/")
        if cfg.enable_ha_rest:
            lines.append(f"  模拟 HA REST    : http://{cfg.host}:{cfg.ha_rest_port}/api/states")
        lines.append("=" * 70)
        return "\n".join(lines)
