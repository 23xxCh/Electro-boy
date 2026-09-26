# -*- coding: utf-8 -*-
"""ha_rest.py —— 模拟 Home Assistant REST 桥（:8123 风格）。

让自制看板 / LP 控制器**无需真实 HA** 即可用 HA 的 REST 语义读写 VE：
  GET  /api/states                列出全部实体（entity_id + state + attributes）
  GET  /api/states/<entity_id>    读单个实体
  POST /api/services/number/set_value     写 number（battery_power_setpoint 等）
  POST /api/services/select/select_option 写 select（operating_mode / direction）
  POST /api/services/switch/turn_on|off   写 switch（备用）

entity_id 命名（与官方比赛口径一致，站点后缀 = site_1/site_2/site_3）：
  sensor.<site>_soc / _solar_power / _home_load
  sensor.<site>_battery_charging_power / _battery_discharging_power
  sensor.<site>_grid_import_power / _grid_export_power
  select.<site>_operating_mode / select.<site>_battery_power_direction
  number.<site>_battery_power_setpoint（有符号 W，负=充/正=放）
  number.<site>_charging_limit_soc / _discharge_limit_soc / _backup_reserve_soc

对应官方插件 translation_key：soc=battery_soc, solar_power=pv_power, home_load=load_power。
真实 HA 下 entity_id 由 HA 按设备名 slug 生成，见 README「看板/LP 控制器如何接入」。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

MODE_VALUE_TO_KEY = {
    0: "self_consumption", 1: "tou_mode", 3: "third_party_control",
    4: "custom_mode", 5: "socket_overlay_mode", 6: "smart_mode", 7: "dynamic_pricing",
}
MODE_KEY_TO_VALUE = {v: k for k, v in MODE_VALUE_TO_KEY.items()}

# 实体 key -> engine.site_state() 返回的字段名
STATE_KEY_MAP = {
    "soc": "soc_pct",
    "solar_power": "solar_power_w",
    "home_load": "home_load_w",
    "battery_charging_power": "battery_charging_w",
    "battery_discharging_power": "battery_discharging_w",
    "grid_import_power": "grid_import_w",
    "grid_export_power": "grid_export_w",
}


def _entity_schema(slug: str) -> list[dict]:
    """一个站点（slug）的全部实体定义。"""
    s = lambda key, friendly, unit=None, devcls=None: {
        "entity_id": f"sensor.{slug}_{key}", "domain": "sensor", "key": key,
        "friendly_name": friendly, "unit": unit, "device_class": devcls,
    }
    return [
        s("soc", "SOC", "%", "battery"),
        s("solar_power", "Solar Power", "W", "power"),
        s("home_load", "Home Load", "W", "power"),
        s("battery_charging_power", "Battery Charging Power", "W", "power"),
        s("battery_discharging_power", "Battery Discharging Power", "W", "power"),
        s("grid_import_power", "Grid Import Power", "W", "power"),
        s("grid_export_power", "Grid Export Power", "W", "power"),
        s("rated_energy", "Battery Capacity", "kWh", "energy"),
        {"entity_id": f"select.{slug}_operating_mode", "domain": "select",
         "key": "operating_mode", "friendly_name": "Operating Mode"},
        {"entity_id": f"select.{slug}_battery_power_direction", "domain": "select",
         "key": "battery_power_direction", "friendly_name": "Grid Flow"},
        {"entity_id": f"number.{slug}_battery_power_setpoint", "domain": "number",
         "key": "battery_power_setpoint", "friendly_name": "Power Control", "unit": "W"},
        {"entity_id": f"number.{slug}_charging_limit_soc", "domain": "number",
         "key": "charging_limit_soc", "friendly_name": "Charging Limit", "unit": "%"},
        {"entity_id": f"number.{slug}_discharge_limit_soc", "domain": "number",
         "key": "discharge_limit_soc", "friendly_name": "Discharge Limit", "unit": "%"},
        {"entity_id": f"number.{slug}_backup_reserve_soc", "domain": "number",
         "key": "backup_reserve_soc", "friendly_name": "Backup Reserve", "unit": "%"},
    ]


def make_handler(engine):
    schemas = {sid: _entity_schema(sid) for sid in engine.sites}
    by_id = {}
    for sid, ents in schemas.items():
        for e in ents:
            by_id[e["entity_id"]] = (sid, e)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, obj, code: int = 200):
            body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _state(self, sid: str, e: dict) -> dict:
            st = engine.site_state(sid)
            mode = int(st["operating_mode"])
            if e["domain"] == "select":
                if e["key"] == "operating_mode":
                    val = MODE_VALUE_TO_KEY.get(mode, "third_party_control")
                else:  # battery_power_direction
                    val = "charge" if st["power_setpoint_w"] < 0 else "discharge"
                return {"entity_id": e["entity_id"], "state": val,
                        "attributes": {"friendly_name": e["friendly_name"]}}
            if e["domain"] == "number":
                if e["key"] == "battery_power_setpoint":
                    # never_read_device：显示用户最后一次下发的命令（未到下一时隙也可见）
                    val = engine.commands.get(sid, 0.0)
                    # 与真实插件一致：把充/放电上限暴露为 number 实体的 attributes（W）
                    sp = engine.sites[sid].params
                    return {"entity_id": e["entity_id"], "state": str(int(val)),
                            "attributes": {"friendly_name": e["friendly_name"],
                                           "unit_of_measurement": e["unit"],
                                           "max_charge_power": int(sp.p_charge_max_kw * 1000),
                                           "max_discharge_power": int(sp.p_discharge_max_kw * 1000)}}
                elif e["key"] == "charging_limit_soc":
                    val = 100
                elif e["key"] == "discharge_limit_soc":
                    val = 0
                else:
                    val = 0
                return {"entity_id": e["entity_id"], "state": str(int(val)),
                        "attributes": {"friendly_name": e["friendly_name"],
                                       "unit_of_measurement": e["unit"]}}
            # sensor
            if e["key"] == "rated_energy":
                val = engine.sites[sid].params.capacity_kwh   # 电池容量 kWh
            else:
                val = st.get(STATE_KEY_MAP.get(e["key"], e["key"]), 0.0)
            return {"entity_id": e["entity_id"], "state": f"{val:.1f}",
                    "attributes": {"friendly_name": e["friendly_name"],
                                   "unit_of_measurement": e["unit"],
                                   "device_class": e.get("device_class")}}

        def _safe_error(self, e):
            try:
                self._send({"error": f"internal error: {e}"}, 500)
            except Exception:
                pass

        def do_GET(self):
            try:
                self._do_GET()
            except Exception as e:
                self._safe_error(e)

        def _do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/states":
                states = [self._state(sid, e) for sid, ents in schemas.items() for e in ents]
                return self._send(states)
            if path.startswith("/api/states/"):
                eid = path[len("/api/states/"):]
                if eid in by_id:
                    sid, e = by_id[eid]
                    return self._send(self._state(sid, e))
                return self._send({"error": "not found"}, 404)
            if path == "/api/" or path == "/api":
                return self._send({"message": "HA-compatible REST bridge"})
            return self._send({"error": "not found"}, 404)

        def do_POST(self):
            try:
                self._do_POST()
            except Exception as e:
                self._safe_error(e)

        def _do_POST(self):
            path = urlparse(self.path).path
            try:
                ln = int(self.headers.get("Content-Length", 0) or 0)
                payload = json.loads(self.rfile.read(ln).decode("utf-8")) if ln else {}
            except Exception:
                payload = {}
            eid = payload.get("entity_id", "")
            if eid not in by_id:
                return self._send({"error": f"unknown entity {eid}"}, 404)
            sid, e = by_id[eid]

            if path == "/api/services/number/set_value":
                val = float(payload.get("value", 0))
                if e["key"] == "battery_power_setpoint":
                    engine.set_command(sid, val)          # 有符号：负=充/正=放
                    return self._send([self._state(sid, e)])
                return self._send({"error": "locked or unsupported"}, 400)
            if path == "/api/services/select/select_option":
                opt = payload.get("option", "")
                if e["key"] == "operating_mode":
                    engine.set_operating_mode(sid, MODE_KEY_TO_VALUE.get(opt, 3))
                    return self._send([self._state(sid, e)])
                if e["key"] == "battery_power_direction":
                    # 方向仅提示；实际功率由有符号 setpoint 承载
                    return self._send([self._state(sid, e)])
                return self._send({"error": "unsupported select"}, 400)
            if path in ("/api/services/switch/turn_on", "/api/services/switch/turn_off"):
                return self._send([self._state(sid, e)])
            return self._send({"error": "not found"}, 404)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

    return Handler


class HaRestServer(threading.Thread):
    def __init__(self, engine, host: str, port: int):
        super().__init__(daemon=True, name="ha-rest")
        self.engine = engine
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None

    def run(self) -> None:
        handler = make_handler(self.engine)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.serve_forever(poll_interval=0.2)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
