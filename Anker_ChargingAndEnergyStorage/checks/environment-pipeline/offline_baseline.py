"""Delivered source characterization under a process-wide deny-network guard."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ATTEMPTS = []


def deny_external(event, args):
    if event.startswith("socket.") or event in ("subprocess.Popen", "os.system", "os.posix_spawn"):
        ATTEMPTS.append(event)
        raise RuntimeError("offline_network_or_process_denied")


sys.addaudithook(deny_external)
BASE = Path(__file__).resolve().parents[2]
NEW = BASE / "DJXZ/AnkerProject_DJXZ_Release/Simulator"
OLD = BASE / "AnkerProject_DJXZ_Release/Simulator"
PACKAGE = "ep_qa_delivered_simulator"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(NEW)]
sys.modules[PACKAGE] = package


def load(name):
    qualified = PACKAGE + "." + name
    if qualified not in sys.modules:
        spec = importlib.util.spec_from_file_location(qualified, NEW / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
    return sys.modules[qualified]


config, ha, protocol, sim_api, solarbank = [load(name) for name in ("config", "ha_client", "protocol", "sim_api", "solarbank")]
IMPORT_BLOCKED = ATTEMPTS[:]
ATTEMPTS.clear()


class Response:
    def __init__(self, code, data=None):
        self.status_code, self.data, self.text = code, data or {}, "synthetic response"

    def json(self):
        return self.data


class DeliveredPackageCharacterization(unittest.TestCase):
    def test_only_horizon_source_changed(self):
        changed = [path.name for path in sorted(NEW.glob("*.py")) if path.read_bytes() != (OLD / path.name).read_bytes()]
        self.assertEqual(changed, ["solarbank.py"])

    def test_horizon_stops_at_public_forecast_boundary(self):
        bank = solarbank.SolarBank(config.SimConfig())
        prices = {"points": [{"import_price": n / 100, "export_price": n / 200} for n in range(24)]}
        weather = {"points": [{"solar_irradiance_wm2": n, "cloud_cover_percent": 10, "temperature_c": 20} for n in range(24)]}
        bank.sim = types.SimpleNamespace(price_today=lambda: prices, price_tomorrow=lambda: prices,
                                         weather_today=lambda site: weather, weather_tomorrow=lambda site: weather)
        from datetime import datetime
        result = bank.build_lp_forecast(1, horizon_hours=48, now=datetime(2026, 10, 17, 23, 45))
        self.assertEqual([len(values) for values in result], [25] * 7)
        self.assertEqual(result[0][0], .23)
        self.assertEqual(result[0][1], 0)

    def test_protocol_asymmetry_is_existing_contract(self):
        charge = protocol.command_for_lp(.4, 0, 0)
        export = protocol.command_for_lp(0, .8, 1.1)
        battery_only = protocol.command_for_lp(0, .8, 0)
        self.assertEqual((charge.grid_flow, charge.power_control_w), ("charge", 400))
        self.assertEqual((export.grid_flow, export.power_control_w), ("discharge", 1100))
        self.assertTrue(battery_only.is_standby)

    def test_missing_measurement_zero_is_existing_risk(self):
        bank = solarbank.SolarBank(config.SimConfig(site_suffixes={"1": "qa1"}))
        bank.suffixes = {1: "qa1"}
        bank.ha = types.SimpleNamespace(states=lambda: [])
        state = bank.read_all_states()[1]
        self.assertEqual(state.soc, 0)
        self.assertEqual(state.home_load_kw, 0)
        self.assertEqual(state.device_time, "")

    def test_unconfigured_lexical_mapping_is_existing_risk(self):
        bank = solarbank.SolarBank(config.SimConfig())
        prefix = "sensor." + bank.cfg.device_prefix + "_"
        bank.ha = types.SimpleNamespace(states=lambda: [{"entity_id": prefix + suffix + "_soc"} for suffix in ("zz3", "aa1", "mm2")])
        self.assertEqual(bank.discover_sites(), {1: "aa1", 2: "mm2", 3: "zz3"})

    def test_original_post_retries_500_without_ack(self):
        client = ha.HaClient("http://offline.invalid:8123", "synthetic-ha-token")
        calls = []

        def post(*args, **kwargs):
            calls.append((args, kwargs))
            return Response(500 if len(calls) == 1 else 200)

        client.session = types.SimpleNamespace(post=post)
        with patch.object(ha.time, "sleep", lambda _: None):
            client.set_number("number.synthetic_power_control", 400)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DeliveredPackageCharacterization)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {"kind": "offline_source_characterization", "testsRun": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors),
              "blockedImportSocketProbes": IMPORT_BLOCKED, "externalAttemptsDuringTests": ATTEMPTS,
              "sourceHashes": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(NEW.glob("*.py"))},
              "connected": False, "deviceWrites": 0,
              "limitations": "Existing-risk characterization is not adapter acceptance or connectivity proof."}
    (Path(__file__).parent / "baseline-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    sys.exit(0 if result.wasSuccessful() and not ATTEMPTS else 1)
