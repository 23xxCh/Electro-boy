"""Offline checks only: temporary config/owner and fake HA responses, no sockets."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from .release import ENVIRONMENT, ReleasePipeline, command_from_plan, envelope, load_config
from .runtime import Rejected


def config():
    return {"enabled": True, "environment": ENVIRONMENT, "haUrl": "http://fixture.invalid:8123", "simUrl": "http://fixture.invalid:45678",
            "haToken": "fixture_ha_secret", "simUsername": "fixture_user", "simPassword": "fixture_sim_secret",
            "siteSuffixes": {"1": "first", "2": "second", "3": "third"}, "timeoutSeconds": 2}


def fake_bank():
    def eid(site, func, domain="sensor"):
        return f"{domain}.private_fixture_site{site}_{func}"
    return SimpleNamespace(eid=eid)


def states():
    bank = fake_bank()
    data = [("soc", "50", "%"), ("solar_power", "0", "W"), ("home_load", "400", "W"),
            ("battery_charging_power", "1000", "W"), ("battery_discharging_power", "0", "W"),
            ("grid_import_power", "1400", "W"), ("grid_export_power", "0", "W")]
    result = [{"entity_id": bank.eid(1, key), "state": value, "attributes": {"unit_of_measurement": unit}} for key, value, unit in data]
    result.extend([{"entity_id": bank.eid(1, "device_time"), "state": "2025-06-03 12:15:00"},
                   {"entity_id": bank.eid(1, "operating_mode_device_runs_by_command_in_third_party_controlled", "select"), "state": "third_party_control"},
                   {"entity_id": bank.eid(1, "grid_flow", "select"), "state": "charge"},
                   {"entity_id": bank.eid(1, "power_control", "number"), "state": "1000", "attributes": {"unit_of_measurement": "W"}}])
    return result


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "private.json"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_disabled_and_missing_config_create_no_client(self):
        pipe = ReleasePipeline(self.path)
        with patch.object(pipe, "make_bank", side_effect=AssertionError("must_not_create_client")):
            self.assertEqual(pipe.snapshot()["status"], "disabled")
            self.write({"enabled": False})
            self.assertEqual(pipe.snapshot()["status"], "disabled")
        self.assertRaises(Rejected, pipe.dispatch, {})

    def test_private_configuration_explicit_mapping_and_secret_repr(self):
        self.write(config())
        loaded = load_config(self.path)
        self.assertEqual(loaded.site_suffixes, {1: "first", 2: "second", 3: "third"})
        self.assertNotIn("fixture_ha_secret", repr(loaded))
        for patch_value in ({"siteSuffixes": {}}, {"siteSuffixes": {"1": "same", "2": "same", "3": "third"}},
                            {"haUrl": "http://name:secret@fixture.invalid"}, {"haUrl": "http://fixture.invalid/?token=x"},
                            {"enabled": 1}, {"environment": "local_debug1_sim"}, {"timeoutSeconds": True}):
            self.write({**config(), **patch_value})
            with self.assertRaises(Rejected):
                load_config(self.path)

    def test_snapshot_normalizes_only_current_site_and_preserves_zero(self):
        raw = states() + [{"entity_id": "sensor.other_household", "state": "foreign_private_sentinel"}]
        result = ReleasePipeline.project(fake_bank(), raw)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["observation"]["pvKw"], 0)
        self.assertEqual(result["observation"]["batteryActualKw"], -1)
        self.assertEqual(result["observation"]["gridImportKw"], 1.4)
        self.assertEqual(result["deviceTime"], "2025-06-03T12:15:00")
        encoded = json.dumps(result)
        for private in ("private_fixture", "foreign_private_sentinel", "fixture_ha_secret", "http://"):
            self.assertNotIn(private, encoded)
        self.assertFalse(result["validation"]["identityVerified"])
        self.assertFalse(result["control"]["enabled"])
        self.assertIsNone(result["prices"])

    def test_missing_unknown_bad_units_nonfinite_are_partial_never_zero(self):
        for replacement in (None, "unknown", "unavailable", "NaN", "Infinity", "-1", True):
            raw = states()
            raw[0]["state"] = replacement
            result = ReleasePipeline.project(fake_bank(), raw)
            self.assertEqual(result["status"], "partial")
            self.assertIsNone(result["observation"]["socPercent"])
        raw = states()
        raw[1]["attributes"]["unit_of_measurement"] = "kW"
        result = ReleasePipeline.project(fake_bank(), raw)
        self.assertIsNone(result["observation"]["pvKw"])
        self.assertEqual(result["status"], "partial")
        result = ReleasePipeline.project(fake_bank(), states()[1:])
        self.assertIsNone(result["observation"]["socPercent"])

    def test_bad_time_and_duplicate_entity_not_mislabelled(self):
        for stamp in ("2025-06-03T12:15:00Z", "2025-06-03", "2025-W23-2", "2025-06-03 12:15"):
            raw = states()
            raw[7]["state"] = stamp
            result = ReleasePipeline.project(fake_bank(), raw)
            self.assertEqual(result["status"], "partial")
            self.assertIsNone(result["deviceTime"])
        self.assertRaises(Rejected, ReleasePipeline.project, fake_bank(), states() + [states()[0]])

    def test_protocol_reuses_export_target_and_rejects_unrepresentable(self):
        cmd = command_from_plan({"charge_kw": 0, "discharge_kw": 1, "export_kw": 2}, .1)
        self.assertEqual((cmd["gridFlow"], cmd["powerControlW"]), ("discharge", 2000))
        self.assertFalse(cmd["controlEnabled"])
        cmd = command_from_plan({"charge_kw": 1, "discharge_kw": 0, "export_kw": 0}, -.1)
        self.assertEqual((cmd["gridFlow"], cmd["powerControlW"]), ("charge", 1000))
        for row in ({"charge_kw": 0, "discharge_kw": 1, "export_kw": 0},
                    {"charge_kw": 6, "discharge_kw": 0, "export_kw": 0},
                    {"charge_kw": .00001, "discharge_kw": 0, "export_kw": 0},
                    {"charge_kw": True, "discharge_kw": 0, "export_kw": 0}):
            with self.assertRaises(Rejected):
                command_from_plan(row, .1)
        with self.assertRaises(Rejected):
            command_from_plan({"charge_kw": 0, "discharge_kw": 1, "export_kw": 1}, -.1)

    def test_client_construction_disables_proxy_redirects_and_write_retries(self):
        self.write(config())
        bank = ReleasePipeline(self.path).make_bank(load_config(self.path))
        self.assertEqual(bank.ha.retries, 1)
        self.assertFalse(bank.ha.session.trust_env)
        self.assertFalse(bank.sim.session.trust_env)
        with patch("requests.Session.request", return_value=SimpleNamespace(status_code=500, text="fixture", json=lambda: {})) as request:
            with self.assertRaises(Exception):
                bank.ha.set_number(bank.eid(1, "power_control", "number"), 1000)
            self.assertEqual(request.call_count, 1)
            self.assertFalse(request.call_args.kwargs["allow_redirects"])
        bank.ha.session.close()
        bank.sim.session.close()

    def test_gateway_read_does_not_grant_seen_cleanup_or_change_owner_memory(self):
        from ..assistant.runtime_api import RuntimeAPI
        from ..assistant.runtime_tools import RuntimeTools
        from ..assistant.service import Service
        from ..assistant.store import Store
        from ..assistant.web_gateway import COOKIE, WebGateway
        store = Store(Path(self.temp.name) / "owner.sqlite3")
        try:
            who = {"app": "qa_release", "sender": "qa_owner", "household": "site_1"}
            store.bind(who["app"], who["sender"], store.pair_code(who["app"]))
            api = SimpleNamespace(owner=RuntimeAPI.owner, environment_snapshot=lambda: envelope())
            service = Service(store, object(), model=object(), runtime=RuntimeTools(store, api))
            gateway = WebGateway(who["app"], store, service)
            _, _, token = gateway.dispatch("GET", "/api/assistant/session", None)
            cookie = COOKIE + "=" + token
            store.put_pending(who, {"action": "memory", "kind": "habit", "key": "fixture", "value": "fixture"})
            before = store.db.total_changes
            seen = tuple(store.db.execute("SELECT seen_token,seen_proposal FROM web_sessions").fetchone())
            code, result, new_token = gateway.dispatch("GET", "/api/assistant/environment", cookie)
            self.assertEqual((code, result["status"], new_token), (200, "disabled", None))
            self.assertEqual(store.db.total_changes, before)
            self.assertEqual(tuple(store.db.execute("SELECT seen_token,seen_proposal FROM web_sessions").fetchone()), seen)
            self.assertEqual(gateway.dispatch("GET", "/api/assistant/environment", None)[0], 401)
            self.assertEqual(gateway.dispatch("GET", "/api/assistant/environment?site=2", cookie)[0], 400)
            self.assertEqual(gateway.dispatch("POST", "/api/assistant/environment", cookie, {})[0], 405)
            reply=service.runtime.handle(who,"fixture_environment_read","环境机状态")
            self.assertIn("尚未启用",reply)
            self.assertEqual(service.runtime.last_result['kind'],'environment_status')
            self.assertEqual(store.db.total_changes,before)
        finally:
            store.db.close()


if __name__ == "__main__":
    unittest.main()
