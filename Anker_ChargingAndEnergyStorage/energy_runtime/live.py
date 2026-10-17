"""Local HA adapter. Battery writes are confined to the existing HA webhook."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .runtime import ENVIRONMENT, Rejected, target_watts

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "ha_lovelace_preview"
DEBUG = ROOT / "Anker_ChargingAndEnergyStorage/AnkerProject_DJXZ_Debug"
CONTAINER = "anker-ha-lovelace-preview"
IMAGE_ID = "sha256:d8922685169707fd91e8b9729902d975f06157d005e422874d201e0261dda196"
VE = "http://127.0.0.1:45678"
HA = "http://127.0.0.1:8123"
SITES = ("site_1", "site_2", "site_3")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args):
        raise Rejected("local_endpoint_redirect_refused")


HTTP = build_opener(ProxyHandler({}), NoRedirect())


def get_json(path):
    with HTTP.open(VE + path, timeout=8) as response:
        return json.load(response)


def docker(*args, stdin=None):
    completed = subprocess.run(["docker", "--context", "desktop-linux", *args], input=stdin,
                               capture_output=True, text=True, encoding="utf-8", timeout=30)
    if completed.returncode:
        raise Rejected("local_docker_operation_failed")
    return completed.stdout.strip()


def clock(action):
    if action not in ("pause", "step"):
        raise Rejected("invalid_clock_action")
    code = ("import json,urllib.request\n"
            "r=urllib.request.Request('http://127.0.0.1:45678/api/sim',"
            "data=json.dumps({'action':" + repr(action) + "}).encode(),headers={'Content-Type':'application/json'},method='POST')\n"
            "print(json.dumps(json.load(urllib.request.urlopen(r,timeout=8))))\n")
    return json.loads(docker("exec", "-i", CONTAINER, "python3", "-", stdin=code))["meta"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LocalHA:
    def __init__(self):
        self.pv = self.load = None

    def guard(self):
        context = docker("context", "inspect", "desktop-linux")
        endpoint = json.loads(context)[0]["Endpoints"]["docker"]["Host"]
        if endpoint != "npipe:////./pipe/dockerDesktopLinuxEngine":
            raise Rejected("docker_endpoint_not_local")
        raw = json.loads(docker("inspect", CONTAINER))[0]
        if not raw["State"]["Running"]:
            raise Rejected("local_ha_not_running")
        if raw["Image"] != IMAGE_ID:
            raise Rejected("local_ha_image_identity_mismatch")
        mount = next((m for m in raw["Mounts"] if m["Destination"] == "/config"), None)
        if not mount or Path(mount["Source"]).resolve() != PRIVATE.resolve():
            raise Rejected("ha_config_identity_mismatch")
        ports = raw["HostConfig"]["PortBindings"]
        if any(binding["HostIp"] != "127.0.0.1" for values in ports.values() for binding in values):
            raise Rejected("ha_ports_not_loopback")
        if set(ports) != {"8123/tcp", "45680/tcp"} or ports["8123/tcp"] != [{"HostIp": "127.0.0.1", "HostPort": "8123"}] or ports["45680/tcp"] != [{"HostIp": "127.0.0.1", "HostPort": "45678"}]:
            raise Rejected("ha_ports_missing")
        running = docker("ps", "--format", "{{.Names}} {{.Command}}", "--no-trunc")
        if "anker-lp-controller" in running or "node_live.py" in running:
            raise Rejected("another_controller_running")
        manifest = json.loads((PRIVATE / "custom_components/anker_solix_official/manifest.json").read_text(encoding="utf-8"))
        if manifest.get("domain") != "anker_solix_official" or manifest.get("version") != "1.5.0":
            raise Rejected("official_plugin_identity_mismatch")
        entries = json.loads((PRIVATE / ".storage/core.config_entries").read_text(encoding="utf-8"))["data"]["entries"]
        official = [entry for entry in entries if entry["domain"] == "anker_solix_official"]
        if sorted((entry["data"].get("ip_address"), entry["data"].get("port")) for entry in official) != [("127.0.0.2", 502), ("127.0.0.3", 502), ("127.0.0.4", 502)]:
            raise Rejected("official_devices_not_local_three_site")
        meta = get_json("/api/meta")
        if (not meta["paused"] or meta["n_sites"] != 3 or meta["granularity_min"] != 15
                or float(meta["dt_h"]) != .25 or meta["finished"]):
            raise Rejected("sim_must_be_paused_15min_three_site")
        return meta, {"containerId": raw["Id"], "imageId": raw["Image"],
                      "officialPluginVersion": manifest["version"],
                      "officialPluginManifestSha256": sha(PRIVATE / "custom_components/anker_solix_official/manifest.json")}

    def read_ha(self, slot, after=0, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                values = json.loads((PRIVATE / "sim_src/lp_ha_snapshot.json").read_text(encoding="utf-8"))
                controls = json.loads((PRIVATE / "sim_src/runtime_ha_readback.json").read_text(encoding="utf-8"))
                if (values["sim_slot"] == slot == controls["sim_slot"] and
                        0 <= time.time() - values["captured_at"] <= 5 and
                        0 <= time.time() - controls["captured_at"] <= 5 and
                        values["captured_at"] >= after and controls["captured_at"] >= after and
                        all(values[s]["home_load_reported_at"] >= after and controls[s]["reported_at"] >= after for s in SITES)):
                    return {"telemetry": values, "controls": controls}
            except (OSError, ValueError, KeyError, TypeError):
                pass
            time.sleep(.2)
        raise Rejected("official_ha_telemetry_stale_or_missing")

    def snapshot(self):
        meta, identity = self.guard()
        ha = self.read_ha(meta["slot"], after=time.time())
        info = [{key: value for key, value in row.items() if key != "appliances"} for row in get_json("/api/siteinfo")["sites"]]
        if [row["site_id"] for row in info] != list(SITES):
            raise Rejected("sim_site_identity_mismatch")
        for item in info:
            data = ha["telemetry"][item["site_id"]]
            if (abs(data["capacity_kwh"] - item["capacity_kwh"]) > 1e-6
                    or abs(data["max_charge_w"] / 1000 - item["p_charge_max_kw"]) > 1e-6
                    or abs(data["max_discharge_w"] / 1000 - item["p_discharge_max_kw"]) > 1e-6):
                raise Rejected("ha_sim_physical_identity_mismatch")
        now = datetime.fromisoformat(meta["now"])
        if now.tzinfo is not None:
            raise Rejected("unexpected_sim_time_basis")
        horizon = (48 - now.hour) * 4 - now.minute // 15
        snapshot = {"environment": ENVIRONMENT, "sourceSlot": meta["slot"], "sourceTime": meta["now"],
                "capturedAt": time.time(), "paused": meta["paused"], "identity": identity, "ha": ha,
                "physicalInputsKind": "previous_ended_interval_hold" if meta["slot"] > 0 else "initial_sim_state",
                "telemetryPhysicalAsOfSlot": meta["slot"] - 1 if meta["slot"] > 0 else None,
                "socBoundarySlot": meta["slot"],
                "sites": info, "horizonSlots": horizon,
                "targetWindow": {"start": now.isoformat(timespec="seconds"),
                                 "end": (now + timedelta(minutes=15 * horizon)).isoformat(timespec="seconds"),
                                 "timeBasis": "device_local_naive", "timezone": None}}
        from .decision import observation
        observed = observation(snapshot, kind="current_snapshot")
        try:
            series = get_json("/api/history?site=site_1&limit=1")["series"]
            if series and series[-1]["slot"] == meta["slot"] - 1:
                row = series[-1]
                values = [row["grid_import_w"], row["grid_export_w"]]
                if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 for v in values):
                    observed.update(gridImportKw=values[0] / 1000, gridExportKw=values[1] / 1000)
                    observed["fieldSources"]["grid"] = "simulator_ended_history"
        except (OSError, ValueError, KeyError, TypeError):
            pass
        observed["prices"]["kind"] = "simulator_source_slot"
        try:
            price = get_json("/api/price")["today"]
            values = [price["buy"][now.hour], price["sell"][now.hour]]
            if price["day"] == meta["slot"] // 96 and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
                observed["prices"].update(buyEurKwh=values[0], sellEurKwh=values[1])
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            pass
        snapshot["observation"] = observed
        return snapshot

    def forecast(self, snapshot):
        import numpy as np
        import torch
        torch.set_num_threads(1)
        sys.dont_write_bytecode = True
        if str(DEBUG) not in sys.path:
            sys.path.insert(0, str(DEBUG))
        from Controller.Controller_LP_costmin.lstm_predictor import PvLstmPredictor, DmLoadPredictor
        now = datetime.fromisoformat(snapshot["sourceTime"])
        history = get_json("/api/history?site=site_1&limit=300")["series"]
        if len(history) < 192:
            raise Rejected("forecast_requires_192_ended_sim_intervals")
        tail = history[-192:]
        if [r["slot"] for r in tail] != list(range(snapshot["sourceSlot"] - 192, snapshot["sourceSlot"])):
            raise Rejected("forecast_history_not_contiguous_or_causal")
        for row in tail:
            timestamp = datetime.fromisoformat(row["timestamp"])
            if timestamp + timedelta(minutes=15) > now:
                raise Rejected("unfinished_history_interval")
            if any(not math.isfinite(row[k]) or row[k] < 0 for k in ("solar_power_w", "home_load_w")):
                raise Rejected("invalid_ended_history")
        yesterday = now.replace(hour=0, minute=0) - timedelta(days=1)
        dayrows = [r for r in history if yesterday <= datetime.fromisoformat(r["timestamp"]) < yesterday + timedelta(days=1)]
        if len(dayrows) != 96:
            raise Rejected("forecast_requires_complete_yesterday")
        pv_path = DEBUG / "Controller/Controller_LP_costmin/Predictor/PV_Predictor/Downstream_model/pv_predictor_site1.pt"
        load_path = DEBUG / "Controller/Controller_LP_costmin/Predictor/Load_Predictor/Downstream_model/load_predictor_site1.pt"
        if not pv_path.is_file() or not load_path.is_file():
            raise Rejected("required_site_checkpoint_missing")
        if self.pv is None:
            self.pv, self.load = PvLstmPredictor(1), DmLoadPredictor(1)
        self.pv.reset()
        self.pv._lms_enabled = False  # No observed historical weather in VE history; never invent LMS inputs.
        self.pv.set_yesterday_pv([sum(r["solar_power_w"] for r in dayrows[4*h:4*h+4]) / 4000 for h in range(24)])
        self.load._hist = []
        for row in tail:
            self.load.observe(row["slot"], row["home_load_w"] / 1000)
        embedded = self.load._TD.encode_window_emb(self.load.encoders, self.load._TD.downsample_15min_to_60min(np.asarray(self.load._hist)))
        if embedded is None or not np.all(np.isfinite(embedded)):
            raise Rejected("load_encoder_unavailable")
        weather = get_json("/api/weather?site=site_1")
        arrays = [np.asarray([[max(d["irradiance_w_m2"][h], 0), d["cloud_pct"][h], d["temperature_c"][h]] for h in range(24)], dtype=np.float32)
                  for d in (weather["today"], weather["tomorrow"])]
        self.pv._last_day = now.toordinal()
        pv = self.pv.forecast(now.hour, *arrays, day=now.toordinal(), horizon_hours=48)
        pv = np.repeat(pv, 4)[now.minute // 15:]
        price = get_json("/api/price")
        fc = {"buy": [], "sell": [], "temperature": []}
        for index in range(snapshot["horizonSlots"]):
            target = now + timedelta(minutes=15 * index)
            day = "today" if target.date() == now.date() else "tomorrow"
            fc["buy"].append(price[day]["buy"][target.hour])
            fc["sell"].append(price[day]["sell"][target.hour])
            fc["temperature"].append(weather[day]["temperature_c"][target.hour])
        # shortcut: DM repeats each exogenous point four slots from now; upgrade with a quarter-aligned model interface.
        load = self.load.forecast(now, temp_h=fc["temperature"][::4], buy_h=fc["buy"][::4], sell_h=fc["sell"][::4])
        horizon = min(snapshot["horizonSlots"], len(pv), len(load))
        if horizon != snapshot["horizonSlots"] or self.pv.xgb_model is not None:
            raise Rejected("predictor_identity_or_horizon_mismatch")
        if any(not np.all(np.isfinite(v)) for v in (pv, load, fc["buy"], fc["sell"])):
            raise Rejected("nonfinite_forecast")
        raw = snapshot["ha"]["telemetry"]["site_1"]
        pv, load = np.maximum(pv[:horizon], 0), np.maximum(load[:horizon], 0)
        pv[0], load[0] = raw["solar_power_w"] / 1000, raw["home_load_w"] / 1000
        physics = {"capacityKwh": raw["capacity_kwh"], "maxChargeKw": raw["max_charge_w"] / 1000,
                   "maxDischargeKw": raw["max_discharge_w"] / 1000, "maxGridImportKw": 10.0,
                   "maxGridExportKw": 10.0, "effCharge": .95, "effDischarge": 1.0}
        return {"initialEnergyKwh": max(raw["soc_pct"] - .5, 0) / 100 * raw["capacity_kwh"],
                "socRoundingMarginPercent": .5, "physics": physics, "dtHours": .25,
                "pvKw": pv.tolist(), "loadKw": load.tolist(), "buy": fc["buy"][:horizon], "sell": fc["sell"][:horizon],
                "provenance": {"pvModel": "site1_PvLstmPredictor_LMS_disabled_no_historic_weather",
                               "loadModel": "site1_DmLoadPredictor", "pvCheckpointSha256": sha(pv_path),
                               "loadCheckpointSha256": sha(load_path), "endedHistoryFirstSlot": tail[0]["slot"],
                               "endedHistoryLastSlot": tail[-1]["slot"], "historyPoints": 192,
                               "physicalInputsKind": "previous_ended_interval_hold",
                               "telemetryPhysicalAsOfSlot": snapshot["sourceSlot"] - 1,
                               "socBoundarySlot": snapshot["sourceSlot"],
                               "weatherSource": "local_Debug1_weather_forecast_without_actual_irr",
                               "priceSource": "local_Debug1_price_today_tomorrow", "inputKind": "live_HA_telemetry_and_ended_sim_history",
                               "loadExogenousAlignment": "existing_DM_four_slot_repeat_from_origin"}}

    def solve(self, inputs, floor):
        if str(DEBUG) not in sys.path:
            sys.path.insert(0, str(DEBUG))
        from Controller.Controller_LP_costminTree.household_plan import Ask, _dispatch, validate
        from Controller.Controller_LP_costminTree.site_params import SiteParams
        cfg = inputs["physics"]
        site = SiteParams("site_1", capacity_kwh=cfg["capacityKwh"], p_charge_max_kw=cfg["maxChargeKw"],
                          p_discharge_max_kw=cfg["maxDischargeKw"], p_grid_import_max_kw=cfg["maxGridImportKw"],
                          p_grid_export_max_kw=cfg["maxGridExportKw"], eff_charge=cfg["effCharge"], eff_discharge=cfg["effDischarge"])
        ask = Ask(reserve_kwh_at=tuple((n, floor) for n in range(len(inputs["buy"]))))
        validate(ask, site, len(inputs["buy"]))
        return _dispatch(inputs["initialEnergyKwh"], inputs["pvKw"], inputs["loadKw"], inputs["buy"], inputs["sell"], site, ask, .25)

    def validate_execution(self, episode):
        snapshot = self.snapshot()
        if snapshot["sourceSlot"] != episode["sourceSlot"] or snapshot["identity"] != episode["sourceSnapshot"]["identity"]:
            raise Rejected("proposal_source_changed")
        if snapshot["ha"]["telemetry"]["site_1"]["soc_pct"] != episode["sourceSnapshot"]["ha"]["telemetry"]["site_1"]["soc_pct"]:
            raise Rejected("proposal_ha_soc_changed")

    def send(self, slot, watts):
        import re
        value = json.loads((PRIVATE / "sim_src/lp_webhooks.json").read_text(encoding="utf-8"))["webhook_id"]
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{48}", value):
            raise Rejected("invalid_private_webhook")
        data = json.dumps({"site": "site_1", "slot": slot, "power_w": watts, "sent_at": time.time()}, allow_nan=False).encode()
        request = Request(HA + "/api/webhook/" + value, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with HTTP.open(request, timeout=10) as response:
                if response.status != 200:
                    raise Rejected("ha_webhook_not_submitted")
        except Exception:
            raise Rejected("ha_submission_unknown") from None

    def wait_target(self, slot, watts, after):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                result = self.read_ha(slot, after=after, timeout=3)
            except Rejected:
                continue
            target = result["controls"]["site_1"]
            signed = target["power_w"] * (-1 if target["direction"] == "charge" else 1)
            if signed == watts and target["mode"] == "third_party_control":
                return result
            time.sleep(.2)
        raise Rejected("ha_target_readback_timeout")

    def execute(self, episode):
        self.validate_execution(episode)
        slot = episode["sourceSlot"]
        dispatch = episode["dispatch"]
        watts = dispatch["targetPowerW"]
        before = dispatch["veBeforeAll"]
        current = get_json("/api/state?site=site_1")
        if current["cum_cost_eur"] != before["site_1"]["cum_cost_eur"]:
            raise Rejected("sim_changed_before_dispatch")
        sent = time.time()
        self.send(slot, watts)
        pre_tick = self.wait_target(slot, watts, sent)
        next_meta = clock("step")
        if not next_meta["paused"] or next_meta["slot"] != slot + 1:
            raise Rejected("unexpected_next_sim_tick")
        stepped = time.time()
        ha = self.wait_target(slot + 1, watts, stepped)
        return self.collect_receipt(episode, ha, pre_tick, sent, stepped)

    def prepare_execution(self, episode):
        row = episode["plan"][0]
        watts = target_watts(row)
        before = {site: get_json("/api/state?site=" + site) for site in SITES}
        return {"requestId": episode["episodeId"], "environment": ENVIRONMENT,
                "sourceSlot": episode["sourceSlot"], "targetPowerW": watts, "veBeforeAll": before,
                "preparedAt": time.time()}

    def collect_receipt(self, episode, ha, pre_tick=None, sent=None, stepped=None):
        slot, watts = episode["sourceSlot"], episode["dispatch"]["targetPowerW"]
        before = episode["dispatch"]["veBeforeAll"]
        after = {site: get_json("/api/state?site=" + site) for site in SITES}
        history = get_json("/api/history?site=site_1&limit=1")["series"][-1]
        if history["slot"] != slot or after["site_1"]["power_setpoint_w"] != watts:
            raise Rejected("next_tick_setpoint_not_observed")
        if any(after[site]["power_setpoint_w"] != before[site]["power_setpoint_w"] for site in SITES[1:]):
            raise Rejected("other_site_setpoint_changed")
        cfg = episode["forecast"]["physics"]
        start_e = before["site_1"]["soc_pct"] / 100 * cfg["capacityKwh"]
        end_e = after["site_1"]["soc_pct"] / 100 * cfg["capacityKwh"]
        actual = after["site_1"]["battery_power_w"] / 1000
        expected_e = start_e + (max(-actual, 0) * .95 - max(actual, 0)) * .25
        balance_w = history["solar_power_w"] + history["battery_power_w"] + history["grid_import_w"] - history["home_load_w"] - history["grid_export_w"]
        ha_control = ha["controls"]["site_1"]
        if (abs(end_e - expected_e) > 1e-6 or balance_w < -.3
                or abs(ha_control["battery_power_w"] - after["site_1"]["battery_power_w"]) > 1.1
                or abs(ha_control["soc_pct"] - after["site_1"]["soc_pct"]) > .51):
            raise Rejected("actual_tick_physics_mismatch")
        buy, sell = episode["forecast"]["buy"][0], episode["forecast"]["sell"][0]
        cost = after["site_1"]["cum_cost_eur"] - before["site_1"]["cum_cost_eur"]
        measured_cost = (after["site_1"]["grid_import_w"] * buy - after["site_1"]["grid_export_w"] * sell) / 1000 * .25
        if not math.isfinite(cost) or abs(cost - measured_cost) > 1e-6:
            raise Rejected("settlement_cost_mismatch")
        return {"requestId": episode["episodeId"], "environment": ENVIRONMENT, "sourceSlot": slot, "nextSlot": slot + 1,
                "dtHours": .25, "capacityKwh": cfg["capacityKwh"], "effCharge": .95, "effDischarge": 1,
                "targetPowerW": watts, "haStatus": 200, "submittedAt": sent, "steppedAt": stepped,
                "haBeforeTick": pre_tick, "haAfter": ha, "veBefore": before["site_1"], "veAfter": after["site_1"],
                "otherSitesBefore": {site: before[site] for site in SITES[1:]},
                "otherSitesAfter": {site: after[site] for site in SITES[1:]}, "historyRow": history,
                "prices": {"buy": buy, "sell": sell}, "intervalCostEur": cost,
                "readbackMatches": True, "evidenceKind": "simulator_settlement", "settlementAvailable": True}

    def reconcile(self, episode):
        meta, _identity = self.guard()
        if meta["slot"] == episode["sourceSlot"] + 1 and "dispatch" in episode:
            ha = self.wait_target(meta["slot"], episode["dispatch"]["targetPowerW"], 0)
            return self.collect_receipt(episode, ha)
        return {"settlementAvailable": False, "observedSlot": meta["slot"],
                "sourceSlot": episode["sourceSlot"], "reason": "no_unique_next_tick_receipt", "readOnly": True}

    def stop(self):
        meta = clock("pause")
        sent = time.time()
        self.send(meta["slot"], 0)
        official = self.wait_target(meta["slot"], 0, sent)
        meta = clock("step")
        official_next = self.wait_target(meta["slot"], 0, time.time())
        state = get_json("/api/state?site=site_1")
        if state["power_setpoint_w"] != 0 or abs(state["battery_power_w"]) > .1:
            raise Rejected("zero_power_not_verified")
        return {"paused": True, "zeroPowerVerified": True, "nextSlot": meta["slot"], "ha": official_next, "ve": state}
