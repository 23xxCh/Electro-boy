"""Opt-in Release read transport; no IO on import/construction or remote controls."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from importlib import import_module
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from .runtime import Rejected

ENVIRONMENT = "release_official_ha_unverified"
SCHEMA = "environment-pipeline-v1"


@dataclass(repr=False)
class ReleaseConfig:
    ha_url: str
    sim_url: str
    ha_token: str = field(repr=False)
    sim_username: str = field(repr=False)
    sim_password: str = field(repr=False)
    site_suffixes: dict
    timeout: float


def endpoint(value):
    if not isinstance(value, str):
        raise Rejected("release_endpoint_invalid")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise Rejected("release_endpoint_invalid") from None
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or port is not None and not 1 <= port <= 65535 or any(c.isspace() for c in value)):
        raise Rejected("release_endpoint_invalid")
    return value.rstrip("/")


def load_config(path):
    """Only an explicitly enabled private file can create a remote client."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise Rejected("release_configuration_unreadable") from None
    try:
        if len(raw) > 32_768:
            raise ValueError()
        value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
            raise ValueError()
        if value["enabled"] is False:
            return None
        expected = {"enabled", "environment", "haUrl", "simUrl", "haToken", "simUsername", "simPassword", "siteSuffixes", "timeoutSeconds"}
        if set(value) != expected or value["environment"] != ENVIRONMENT:
            raise ValueError()
        for key in ("haToken", "simUsername", "simPassword"):
            if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 8192 or any(c in value[key] for c in "\r\n"):
                raise ValueError()
        suffixes = value["siteSuffixes"]
        if (not isinstance(suffixes, dict) or set(suffixes) != {"1", "2", "3"}
                or any(not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,100}", s) for s in suffixes.values())
                or len(set(suffixes.values())) != 3):
            raise ValueError()
        timeout = value["timeoutSeconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 10:
            raise ValueError()
        return ReleaseConfig(endpoint(value["haUrl"]), endpoint(value["simUrl"]), value["haToken"],
                             value["simUsername"], value["simPassword"], {int(k): v for k, v in suffixes.items()}, timeout)
    except (ValueError, KeyError, TypeError):
        raise Rejected("release_configuration_invalid") from None


def simulator():
    parent = __package__.rsplit(".", 1)[0] + "." if "." in __package__ else ""
    return import_module(parent + "DJXZ.AnkerProject_DJXZ_Release.Simulator")


def envelope(status="disabled", reason="release_pipeline_not_configured"):
    return {"schemaVersion": SCHEMA, "environment": ENVIRONMENT, "status": status, "reasonCode": reason,
            "householdId": "site_1", "validation": {"connectedTested": False, "identityVerified": False, "timeBasisVerified": False},
            "deviceTime": None, "timeBasis": "device_local_naive", "observedAt": None,
            "units": {"power": "kW", "soc": "%", "controlPower": "W"},
            "observation": dict.fromkeys(("socPercent", "pvKw", "loadKw", "chargingKw", "dischargingKw", "batteryActualKw", "gridImportKw", "gridExportKw")),
            "control": {"enabled": False, "reasonCode": "release_execution_contract_unverified", "operatingMode": None,
                        "gridFlow": None, "powerControlW": None, "targetSemantics": "battery_total_charge_or_grid_export"},
            "fieldSources": {"device": "official_ha", "prices": None}, "prices": None}


def number(value, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or maximum is not None and value > maximum:
        raise Rejected("release_plan_value_invalid")
    return float(value)


def command_from_plan(row, sell_now, max_power_w=5000):
    """Map the reviewed LP quantities, never the old signed battery target."""
    limit = number(max_power_w)
    if limit == 0 or limit > 5000 or not isinstance(row, dict):
        raise Rejected("release_power_limit_invalid")
    try:
        ch, dis, exp = (number(row[key], limit / 1000) for key in ("charge_kw", "discharge_kw", "export_kw"))
    except KeyError:
        raise Rejected("release_plan_fields_missing") from None
    if isinstance(sell_now, bool) or not isinstance(sell_now, (int, float)) or not math.isfinite(sell_now):
        raise Rejected("release_sell_price_invalid")
    if ch > 1e-6 and dis > 1e-6 or sell_now < 0 and (dis > 1e-6 or exp > 1e-6):
        raise Rejected("release_plan_protocol_conflict")
    if dis > 1e-6 and exp <= 1e-6:
        raise Rejected("release_self_use_discharge_unrepresentable")
    cmd = simulator().command_for_lp(ch, dis, exp, max_power_w=limit, sell_now=sell_now)
    if not cmd.is_standby and cmd.power_control_w <= 0:
        raise Rejected("release_command_below_protocol_precision")
    return {"environment": ENVIRONMENT, "status": "prepared_only", "gridFlow": cmd.grid_flow,
            "powerControlW": cmd.power_control_w, "standbyMode": "hold", "targetSemantics": "battery_total_charge_or_grid_export",
            "controlEnabled": False, "reasonCode": "release_execution_contract_unverified"}


class ReleasePipeline:
    def __init__(self, config_path=None):
        # No configuration reads or client construction until the explicit read tool.
        self.config_path = Path(config_path) if config_path is not None else (Path(os.environ["LOCALAPPDATA"]) / "AnkerEnergyRuntime/release-pipeline.json" if os.environ.get("LOCALAPPDATA") else None)

    def make_bank(self, cfg):
        module = simulator()
        import requests

        class FixedSession(requests.Session):
            def __init__(self):
                super().__init__()
                self.trust_env = False

            def request(self, method, url, **kwargs):
                kwargs["allow_redirects"] = False
                return super().request(method, url, **kwargs)

        bank = module.SolarBank(module.SimConfig(ha_url=cfg.ha_url, sim_url=cfg.sim_url, ha_token=cfg.ha_token,
            sim_user=cfg.sim_username, sim_password=cfg.sim_password, site_suffixes=cfg.site_suffixes, timeout_s=cfg.timeout, standby_mode="hold"))
        for client in (bank.ha, bank.sim):
            headers = dict(client.session.headers)
            client.session.close()
            client.session = FixedSession()
            client.session.headers.update(headers)
        bank.ha.retries = 1  # A service result can be unknown; never resend a partial three-entity action.
        bank.suffixes = dict(cfg.site_suffixes)
        return bank

    def snapshot(self):
        if self.config_path is None:
            return envelope()
        try:
            cfg = load_config(self.config_path)
            if cfg is None:
                return envelope()
            bank = self.make_bank(cfg)
            try:
                states = bank.ha.states()
            finally:
                bank.ha.session.close()
                bank.sim.session.close()
            return self.project(bank, states)
        except Rejected as error:
            return envelope("unavailable", str(error))
        except Exception:
            return envelope("unavailable", "release_ha_read_failed")

    @staticmethod
    def project(bank, states):
        if not isinstance(states, list) or len(states) > 10_000 or any(not isinstance(row, dict) for row in states):
            raise Rejected("release_ha_states_invalid")
        by_id = {}
        for row in states:
            eid = row.get("entity_id")
            if not isinstance(eid, str) or eid in by_id:
                raise Rejected("release_ha_entities_invalid")
            by_id[eid] = row
        result = envelope("ready", None)

        def state(func, domain="sensor"):
            return by_id.get(bank.eid(1, func, domain), {})

        def measurement(func, unit, maximum=None):
            raw = state(func)
            if raw.get("attributes", {}).get("unit_of_measurement") != unit or isinstance(raw.get("state"), bool):
                return None
            try:
                return number(float(raw["state"]), maximum)
            except (ValueError, TypeError, KeyError):
                return None

        obs = result["observation"]
        obs["socPercent"] = measurement("soc", "%", 100)
        for source, target in (("solar_power", "pvKw"), ("home_load", "loadKw"), ("battery_charging_power", "chargingKw"),
                               ("battery_discharging_power", "dischargingKw"), ("grid_import_power", "gridImportKw"), ("grid_export_power", "gridExportKw")):
            value = measurement(source, "W")
            obs[target] = None if value is None else value / 1000
        if obs["chargingKw"] is not None and obs["dischargingKw"] is not None:
            obs["batteryActualKw"] = obs["dischargingKw"] - obs["chargingKw"]
        raw_time = state("device_time").get("state")
        try:
            if not isinstance(raw_time, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", raw_time):
                raise ValueError()
            parsed = datetime.fromisoformat(raw_time)
            if parsed.tzinfo is not None:
                raise ValueError()
            result["deviceTime"] = parsed.isoformat(timespec="seconds")
        except (TypeError, ValueError):
            pass
        control = result["control"]
        mode = state("operating_mode_device_runs_by_command_in_third_party_controlled", "select").get("state")
        flow = state("grid_flow", "select").get("state")
        control["operatingMode"] = mode if mode in {"third_party_control", "self_consumption"} else None
        control["gridFlow"] = flow if flow in {"charge", "discharge"} else None
        raw_power = state("power_control", "number")
        try:
            if raw_power.get("attributes", {}).get("unit_of_measurement") == "W" and not isinstance(raw_power.get("state"), bool):
                control["powerControlW"] = number(float(raw_power["state"]), 5000)
        except (ValueError, TypeError, KeyError):
            pass
        result["observedAt"] = time.time()
        if (any(value is None for value in obs.values()) or result["deviceTime"] is None
                or any(control[key] is None for key in ("operatingMode", "gridFlow", "powerControlW"))):
            result.update(status="partial", reasonCode="release_ha_fields_missing_or_invalid")
        return result

    @staticmethod
    def prepare_command(row, sell_now, max_power_w=5000):
        return command_from_plan(row, sell_now, max_power_w)

    @staticmethod
    def dispatch(*_args, **_kwargs):
        raise Rejected("release_execution_contract_unverified")
