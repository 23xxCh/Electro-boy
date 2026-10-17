"""Read-only Debug1 VE snapshot for Home Assistant's REST integration.

Run with ``python ha_host/ve_bridge.py``. HA polls GET /snapshot; this process
never writes to HA, the VE control API, or a device. Defaults bind to loopback.
"""

from __future__ import annotations

import argparse
import json
import math
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


SITES = ("site_1", "site_2", "site_3")
PUBLIC_VE_GET = frozenset(("/api/meta", "/api/score", "/api/price", "/api/weather",
                           "/api/siteinfo", "/api/state", "/api/history", "/api/scheduler"))
READ_ONLY_STYLE = ("<style>header .row,.panel:has(#ctrl){display:none!important}"
                   "header h1::after{content:' · 只读预览（控制请使用 HA）';"
                   "font-size:12px;color:#8b98b5}</style>").encode("utf-8")
BERLIN = ZoneInfo("Europe/Berlin")
WEATHER = {
    "sunny": "晴",
    "partly_cloudy": "局部多云",
    "cloudy": "多云",
    "overcast": "阴",
    "rainy": "雨",
}


def number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"invalid numeric VE field: {value!r}")
    return float(value)


def sim_time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    return result.replace(tzinfo=BERLIN) if result.tzinfo is None else result.astimezone(BERLIN)


def hourly(values) -> list[float]:
    if not isinstance(values, list) or len(values) != 24:
        raise ValueError("VE hourly series must contain 24 points")
    return [number(value) for value in values]


def entity(value, unit=None, **attributes) -> dict:
    data = {"state": value, **attributes}
    if unit is not None:
        data["unit_of_measurement"] = unit
    return data


def build_snapshot(meta: dict, price: dict, weather: dict, score: dict,
                   vp: dict, observed_at: datetime) -> dict:
    """Normalize one coherent VE slot; local score and native baseline stay distinct."""
    slot = int(meta["slot"])
    slots_per_day = int(meta["slots_per_day"])
    granularity = int(meta["granularity_min"])
    if int(meta["n_sites"]) != 3 or slots_per_day <= 0 or granularity <= 0:
        raise ValueError("expected three VE sites and a valid simulation clock")
    hour = (slot % slots_per_day) * granularity // 60
    if not 0 <= hour < 24 or int(price["today"]["day"]) != slot // slots_per_day:
        raise ValueError("VE tariff day does not match simulation slot")
    sim_now = sim_time(meta["now"])
    start_day = sim_time(meta["start"]).date()
    buy = hourly(price["today"]["buy"])[hour]
    sell = hourly(price["today"]["sell"])[hour]
    periods = []
    for day_data in (price["today"], price["tomorrow"]):
        day = start_day + timedelta(days=int(day_data["day"]))
        day_buy, day_sell = hourly(day_data["buy"]), hourly(day_data["sell"])
        for h in range(24):
            starts = datetime.combine(day, time(h), BERLIN)
            if starts + timedelta(hours=1) <= sim_now or starts > sim_now + timedelta(hours=24):
                continue
            periods.append({"start": max(starts, sim_now).isoformat(),
                            "buy_price": day_buy[h], "sell_price": day_sell[h]})

    common = {"sim_slot": slot, "observed_at": observed_at.isoformat(),
              "sim_now": sim_now.isoformat(), "source": "Debug1 VE HTTP API"}
    result = {
        "tariff_current_buy": entity(buy, "€/kWh", sell_price=sell,
                                     periods=periods, **common),
        "score_total": entity(number(score["saving_settled_percent"]), "%",
                              source_timestamp=sim_time(score["timestamp"]).isoformat(),
                              score_basis="all_load_purchased",
                              baseline_k_eur=number(score["baseline_k_eur"]), **common),
    }
    if int(score["slot"]) != slot:
        raise ValueError("VE score slot changed during snapshot")

    native_cost = 0.0
    actual_cost = 0.0
    native_delta = 0.0
    for n, site in enumerate(SITES, 1):
        w = weather[site]
        if w["today"]["site_id"] != site or int(w["today"]["day"]) != slot // slots_per_day:
            raise ValueError(f"VE forecast mismatch: {site}")
        forecast = w["today"]
        irr = hourly(forecast["irradiance_w_m2"])[hour]
        code = forecast["weather_type"][hour]
        valid_at = datetime.combine(start_day + timedelta(days=int(forecast["day"])),
                                    time(hour), BERLIN).isoformat()
        result[f"forecast_site{n}"] = entity(irr, "W/m²", weather=WEATHER.get(code, code),
                                              weather_code=code, valid_at=valid_at, **common)
        site_vp = vp[site]
        if site_vp["site_id"] != site or int(site_vp["slot"]) != slot:
            raise ValueError(f"VE settlement mismatch: {site}")
        base = number(site_vp["baseline_cost_eur"])
        actual = number(site_vp["actual_cost_eur"])
        delta = base - actual
        native_cost += base
        actual_cost += actual
        native_delta += delta
        result[f"savings_amount_site{n}"] = entity(round(delta, 4), "€",
                                                     comparison_basis="native_self_consumption",
                                                     **common)

    if abs(actual_cost - number(score["actual_cost_eur"])) > 0.002:
        raise ValueError("VE settlement endpoints crossed simulation slots")
    result["savings_amount_total"] = entity(round(native_delta, 4), "€",
                                             comparison_basis="native_self_consumption", **common)
    result["savings_percent_vs_native"] = entity(
        round(100 * native_delta / native_cost, 4) if native_cost > 0 else "unavailable", "%",
        comparison_basis="native_self_consumption", **common)
    return result


def read_decisions(report_root: Path | None, slot: int, sim_now: str,
                   observed_at: datetime, slot_real_seconds: float,
                   running: bool = False, granularity_min: int = 15) -> dict:
    """Read only Controller_LP XML for this exact slot, if configured and fresh."""
    result = {f"decision_site{n}": entity("unavailable", source="Controller XML not available")
              for n in range(1, 4)}
    if report_root is None or not report_root.is_dir():
        return result
    runs = [p for p in report_root.iterdir() if p.is_dir() and p.name.isdecimal()]
    if not runs:
        return result
    run = max(runs, key=lambda p: int(p.name))
    ttl = max(3.0, slot_real_seconds * 1.5)
    for n, site in enumerate(SITES, 1):
        path = run / site / f"slot_{slot:06d}.xml"
        if not path.is_file():
            if not running or slot < 1:
                continue
            previous = run / site / f"slot_{slot - 1:06d}.xml"
            if (not previous.is_file()
                    or not 0 <= observed_at.timestamp() - previous.stat().st_mtime <= ttl):
                continue
            try:
                root = ET.parse(previous).getroot()
                previous_now = (datetime.fromisoformat(sim_now)
                                - timedelta(minutes=granularity_min)).isoformat()
                if (root.findtext("site_id") != site
                        or int(root.findtext("slot", "-1")) != slot - 1
                        or root.findtext("timestamp") != previous_now):
                    continue
                previous_reason = root.findtext("explanation/reason")
                if not previous_reason:
                    continue
                result[f"decision_site{n}"] = entity(
                    "计算中", reason=f"本槽计划计算中；上一槽（{slot - 1}）说明：{previous_reason}",
                    sim_slot=slot,
                    previous_reason=previous_reason, previous_slot=slot - 1,
                    valid_until=(observed_at + timedelta(seconds=5)).isoformat(),
                    source="Previous Controller_LP XML; current plan pending")
            except (OSError, ET.ParseError, ValueError):
                continue
            continue
        if observed_at.timestamp() - path.stat().st_mtime > ttl:
            continue
        try:
            root = ET.parse(path).getroot()
            if (root.findtext("site_id") != site or int(root.findtext("slot", "-1")) != slot
                    or root.findtext("timestamp") != sim_now):
                continue
            power = float(root.findtext("action/net_power_w", "nan"))
            if not math.isfinite(power):
                continue
            reason = root.findtext("explanation/reason")
            if not reason:
                continue
            action = "充电" if power < -0.5 else "放电" if power > 0.5 else "待机"
            result[f"decision_site{n}"] = entity(
                action, reason=reason, target_power_w=power, sim_slot=slot,
                source_timestamp=sim_time(sim_now).isoformat(),
                valid_until=(observed_at + timedelta(seconds=ttl)).isoformat(),
                source="Controller_LP XML; control path unverified")
        except (OSError, ET.ParseError, ValueError):
            continue
    return result


class SnapshotSource:
    def __init__(self, ve_url: str, report_root: Path | None = None):
        self.ve_url = ve_url.rstrip("/")
        self.report_root = report_root
        self.lock = threading.Lock()
        self.cached_slot = None
        self.cached = None

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.ve_url + path, timeout=3) as response:
            return json.load(response)

    def snapshot(self) -> dict:
        with self.lock:
            for _ in range(2):
                meta = self.get("/api/meta")
                slot = int(meta["slot"])
                if self.cached_slot == slot:
                    body = dict(self.cached)
                    break
                price = self.get("/api/price")
                weather = {site: self.get(f"/api/weather?site={site}") for site in SITES}
                score = self.get("/api/score")
                vp = {site: self.get(f"/api/vp?site={site}") for site in SITES}
                if int(self.get("/api/meta")["slot"]) != slot:
                    continue
                body = build_snapshot(meta, price, weather, score, vp,
                                      datetime.now(timezone.utc))
                self.cached_slot, self.cached = slot, body
                body = dict(body)
                break
            else:
                raise RuntimeError("VE advanced during both snapshot attempts")
            observed_at = datetime.now(timezone.utc)
            body.update(read_decisions(self.report_root, slot, meta["now"], observed_at,
                                       number(meta["slot_real_seconds"]),
                                       bool(meta["running"] and not meta["paused"]
                                            and not meta["finished"]),
                                       int(meta["granularity_min"])))
            status = "finished" if meta["finished"] else "paused" if meta["paused"] else \
                     "running" if meta["running"] else "stopped"
            body["bridge_status"] = entity(status, sim_slot=slot,
                                           sim_now=sim_time(meta["now"]).isoformat(),
                                           observed_at=observed_at.isoformat(),
                                           valid_until=(observed_at + timedelta(seconds=5)).isoformat(),
                                           source="Debug1 VE HTTP API")
            return body


def make_handler(source: SnapshotSource):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            route = urlparse(self.path)
            if route.path == "/snapshot":
                try:
                    payload, status = source.snapshot(), 200
                except Exception as error:
                    payload, status = {"error": str(error)}, 503
                data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            elif route.path == "/" or route.path in PUBLIC_VE_GET:
                try:
                    url = source.ve_url + route.path + (("?" + route.query) if route.query else "")
                    with urllib.request.urlopen(url, timeout=3) as response:
                        data = response.read(2_000_001)
                        content_type = response.headers.get("Content-Type", "application/octet-stream")
                    if len(data) > 2_000_000:
                        self.send_error(502, "VE response too large")
                        return
                    if route.path == "/":
                        data = data.replace(b"</head>", READ_ONLY_STYLE + b"</head>", 1)
                    status = 200
                except (OSError, ValueError, urllib.error.URLError):
                    self.send_error(503, "VE unavailable")
                    return
            else:
                self.send_error(404)
                return
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self.send_error(405, "Read-only VE preview")

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ve-url", default="http://127.0.0.1:45678")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=45680)
    parser.add_argument("--report-root", type=Path,
                        help="optional Controller_LP/report directory for real XML explanations")
    args = parser.parse_args()
    source = SnapshotSource(args.ve_url, args.report_root)

    server = ThreadingHTTPServer((args.host, args.port), make_handler(source))
    print(f"VE read-only snapshot: http://{args.host}:{args.port}/snapshot", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
