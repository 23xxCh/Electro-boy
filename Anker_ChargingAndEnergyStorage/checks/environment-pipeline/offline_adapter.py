"""Independent EP-01 acceptance: real modules, synthetic files and fake HTTP only."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import offline_baseline as guard
from fixtures import CONFIG, eid, states

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from Anker_ChargingAndEnergyStorage.energy_runtime import release
from Anker_ChargingAndEnergyStorage.energy_runtime.runtime import Rejected
from Anker_ChargingAndEnergyStorage.assistant.runtime_api import RuntimeAPI, RuntimeUnavailable


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.text = data, status, "synthetic response"

    def json(self):
        return copy.deepcopy(self.data)


class OfflineAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "synthetic-release.json"
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def configure(self, changes=None):
        self.path.write_text(json.dumps({**CONFIG, **(changes or {})}), encoding="utf-8")
        return release.ReleasePipeline(self.path)

    def read(self, data):
        pipe = self.configure()

        def request(session, method, url, **kwargs):
            self.calls.append((method, url, kwargs, dict(session.headers), session.trust_env))
            self.assertEqual((method, urlsplit(url).path), ("GET", "/api/states"))
            return Response(data)

        with patch("requests.Session.request", request):
            return pipe.snapshot()

    def test_construction_and_disabled_never_create_client(self):
        with patch.object(release, "load_config", side_effect=AssertionError("constructor_read_file")):
            pipe = release.ReleasePipeline(self.path)
        with patch.object(release, "simulator", side_effect=AssertionError("disabled_imported_client")):
            self.assertEqual(pipe.snapshot()["status"], "disabled")
            self.path.write_text('{"enabled":false}', encoding="utf-8")
            self.assertEqual(pipe.snapshot()["status"], "disabled")
        self.assertEqual(self.calls, [])

    def test_configuration_rejects_guessing_or_cross_environment(self):
        cases = ({"enabled": "false"}, {"enabled": 1}, {"environment": "local_debug1_sim"},
                 {"siteSuffixes": {"1": "a", "2": "b"}}, {"siteSuffixes": {"1": "a", "2": "a", "3": "c"}},
                 {"siteSuffixes": {"1": "a/b", "2": "b", "3": "c"}}, {"haToken": ""},
                 {"haUrl": "http://user:secret@environment.invalid"}, {"haUrl": "http://environment.invalid/path"},
                 {"haUrl": "http://environment.invalid/?token=private"}, {"simUrl": "http://environment.invalid/#fragment"},
                 {"timeoutSeconds": True}, {"timeoutSeconds": 11})
        for changes in cases:
            with self.subTest(changes=list(changes)):
                pipe = self.configure(changes)
                with patch.object(pipe, "make_bank", side_effect=AssertionError("invalid_created_client")):
                    result = pipe.snapshot()
                self.assertEqual(result["status"], "unavailable")
                self.assertTrue(all(value is None for value in result["observation"].values()))
        self.assertEqual(self.calls, [])

    def test_real_client_wire_reads_one_ha_batch_without_proxy_or_redirect(self):
        result = self.read(states())
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(self.calls), 1)
        method, url, kwargs, headers, trust_env = self.calls[0]
        self.assertFalse(kwargs["allow_redirects"])
        self.assertFalse(trust_env)
        self.assertEqual(headers["Authorization"], "Bearer " + CONFIG["haToken"])
        self.assertNotIn(CONFIG["simPassword"], json.dumps([url, kwargs, headers]))
        self.assertEqual(result["observation"], {"socPercent": 70, "pvKw": 1.25, "loadKw": .55,
            "chargingKw": .4, "dischargingKw": 0, "batteryActualKw": -.4, "gridImportKw": 0, "gridExportKw": .3})
        self.assertEqual(result["control"]["powerControlW"], 400)

    def test_zero_is_zero_and_foreign_site_cannot_backfill(self):
        raw = states()
        for row in raw:
            if row["entity_id"] == eid("solar_power"):
                row["state"] = "0"
            if row["entity_id"] == eid("soc", site="2"):
                row["state"] = "99"
        result = self.read(raw)
        self.assertEqual((result["observation"]["pvKw"], result["observation"]["socPercent"]), (0, 70))
        raw = [row for row in raw if row["entity_id"] != eid("soc")]
        result = self.read(raw)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["observation"]["socPercent"])

    def test_invalid_measurement_is_null_without_polluting_valid_fields(self):
        for value in (None, True, "unknown", "unavailable", "NaN", "Infinity", "-1", "100.01"):
            with self.subTest(value=value):
                raw = states()
                next(row for row in raw if row["entity_id"] == eid("soc"))["state"] = value
                result = self.read(raw)
                self.assertEqual(result["status"], "partial")
                self.assertIsNone(result["observation"]["socPercent"])
                self.assertEqual(result["observation"]["loadKw"], .55)
        raw = states()
        next(row for row in raw if row["entity_id"] == eid("solar_power"))["attributes"]["unit_of_measurement"] = "kW"
        result = self.read(raw)
        self.assertIsNone(result["observation"]["pvKw"])
        self.assertEqual(result["status"], "partial")

    def test_clock_requires_full_naive_device_timestamp(self):
        for value in ("2026-10-17", "2026-10-17T12:15", "2026-W42-6", "2026-10-17T12:15:00Z", "unknown"):
            with self.subTest(value=value):
                raw = states()
                next(row for row in raw if row["entity_id"] == eid("device_time"))["state"] = value
                result = self.read(raw)
                self.assertEqual(result["status"], "partial")
                self.assertIsNone(result["deviceTime"])

    def test_public_snapshot_has_no_secrets_endpoints_or_false_evidence(self):
        result = self.read(states())
        encoded = json.dumps(result)
        for private in (*CONFIG["siteSuffixes"].values(), CONFIG["haToken"], CONFIG["simUsername"], CONFIG["simPassword"], "http://", "https://"):
            self.assertNotIn(private, encoded)
        self.assertEqual(result["environment"], "release_official_ha_unverified")
        self.assertEqual(result["validation"], {"connectedTested": False, "identityVerified": False, "timeBasisVerified": False})
        self.assertFalse(result["control"]["enabled"])
        self.assertIsNone(result["prices"])
        self.assertNotIn("actual", result)
        self.assertNotIn("settlementAvailable", result)
        cfg = release.load_config(self.path)
        self.assertNotIn(CONFIG["haToken"], repr(cfg))

    def test_asymmetric_command_reuses_export_not_battery_watts(self):
        charge = release.command_from_plan({"charge_kw": 1.1, "discharge_kw": 0, "export_kw": 0}, -.1)
        export = release.command_from_plan({"charge_kw": 0, "discharge_kw": .8, "export_kw": 1.2}, .1)
        hold = release.command_from_plan({"charge_kw": 0, "discharge_kw": 0, "export_kw": 0}, .1)
        self.assertEqual((charge["gridFlow"], charge["powerControlW"]), ("charge", 1100))
        self.assertEqual((export["gridFlow"], export["powerControlW"]), ("discharge", 1200))
        self.assertEqual((hold["gridFlow"], hold["powerControlW"]), (None, 0))
        for command in (charge, export, hold):
            self.assertEqual(command["status"], "prepared_only")
            self.assertFalse(command["controlEnabled"])

    def test_unrepresentable_conflicting_or_truncated_plan_is_rejected(self):
        cases = ({"charge_kw": 0, "discharge_kw": .8, "export_kw": 0},
                 {"charge_kw": 1, "discharge_kw": 1, "export_kw": 1},
                 {"charge_kw": 5.001, "discharge_kw": 0, "export_kw": 0},
                 {"charge_kw": .00001, "discharge_kw": 0, "export_kw": 0},
                 {"charge_kw": 0, "discharge_kw": 0, "export_kw": .00001},
                 {"charge_kw": True, "discharge_kw": 0, "export_kw": 0},
                 {"charge_kw": float("nan"), "discharge_kw": 0, "export_kw": 0})
        for row in cases:
            with self.subTest(row=row), self.assertRaises(Rejected):
                release.command_from_plan(row, .1)
        for limit in (0, True, float("inf"), 5001):
            with self.subTest(limit=limit), self.assertRaises(Rejected):
                release.command_from_plan({"charge_kw": 0, "discharge_kw": 0, "export_kw": 0}, .1, limit)
        with self.assertRaises(Rejected):
            release.command_from_plan({"charge_kw": 0, "discharge_kw": .8, "export_kw": 1}, -.1)

    def test_future_write_client_is_single_attempt_but_dispatch_stays_blocked(self):
        pipe = self.configure()
        bank = pipe.make_bank(release.load_config(self.path))
        calls = []

        def request(session, method, url, **kwargs):
            calls.append((method, urlsplit(url).path, kwargs))
            return Response({}, 500)

        try:
            with patch("requests.Session.request", request):
                with self.assertRaises(Exception):
                    bank.ha.set_number(bank.eid(1, "power_control", "number"), 400)
                with self.assertRaises(Rejected):
                    pipe.dispatch({"requestId": "synthetic-original", "haStatus": 200})
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0:2], ("POST", "/api/services/number/set_value"))
            self.assertFalse(calls[0][2]["allow_redirects"])
            self.assertFalse(bank.ha.session.trust_env)
            self.assertEqual(bank.ha.retries, 1)
        finally:
            bank.ha.session.close()
            bank.sim.session.close()

    def test_runtime_api_remote_environment_cannot_enter_local_snapshot(self):
        api = RuntimeAPI()
        output = release.envelope()
        request_calls = []

        def open_fake(request, timeout):
            request_calls.append(request)
            return io.BytesIO(json.dumps(output).encode())

        private = json.dumps({"url": api.base, "token": "synthetic-local-tool-key"})
        with patch.object(Path, "read_text", return_value=private), patch.object(api.opener, "open", open_fake):
            self.assertEqual(api.environment_snapshot()["status"], "disabled")
            with self.assertRaises(RuntimeUnavailable):
                api.snapshot()
        self.assertEqual([urlsplit(request.full_url).path for request in request_calls], ["/environment/snapshot", "/snapshot"])
        self.assertTrue(all(request.get_method() == "GET" for request in request_calls))

    def test_app_and_agent_use_same_read_pipeline_without_seen_or_memory_writes(self):
        from Anker_ChargingAndEnergyStorage.assistant.runtime_tools import RuntimeTools
        from Anker_ChargingAndEnergyStorage.assistant.service import Service
        from Anker_ChargingAndEnergyStorage.assistant.store import Store
        from Anker_ChargingAndEnergyStorage.assistant.web_gateway import COOKIE, WebGateway
        pipe = self.configure()
        api = RuntimeAPI()
        store = Store(Path(self.temp.name) / "synthetic-owner.sqlite3")
        who = {"app": "ep_independent_qa", "sender": "synthetic_owner", "household": "site_1"}
        core_calls = []
        ha_calls = []

        def open_core(request, timeout):
            core_calls.append((request.get_method(), urlsplit(request.full_url).path))
            self.assertEqual(core_calls[-1], ("GET", "/environment/snapshot"))
            return io.BytesIO(json.dumps(pipe.snapshot()).encode())

        def request_ha(session, method, url, **kwargs):
            ha_calls.append((method, urlsplit(url).path))
            self.assertEqual(ha_calls[-1], ("GET", "/api/states"))
            return Response(states())

        try:
            store.bind(who["app"], who["sender"], store.pair_code(who["app"]))
            runtime = RuntimeTools(store, api)
            service = Service(store, object(), model=object(), runtime=runtime)
            gateway = WebGateway(who["app"], store, service)
            owner, session, token = gateway.session(None, create=True)
            cookie = COOKIE + "=" + token
            store.put_pending(who, {"action": "memory", "kind": "habit", "key": "qa_pending", "value": "synthetic"})
            before = store.db.total_changes
            seen = tuple(store.db.execute("SELECT seen_token,seen_proposal FROM web_sessions").fetchone())
            private = json.dumps({"url": api.base, "token": "synthetic-local-tool-key"})
            with patch.object(Path, "read_text", return_value=private), patch.object(api.opener, "open", open_core), patch("requests.Session.request", request_ha):
                code, result, new_token = gateway.dispatch("GET", "/api/assistant/environment", cookie)
                self.assertEqual((code, result["status"], new_token), (200, "ready", None))
                reply = runtime.handle(who, "synthetic-read", "环境机状态")
                self.assertIn("官方 HA 读数", reply)
                self.assertEqual(runtime.last_result["kind"], "environment_status")
                self.assertEqual(gateway.dispatch("GET", "/api/assistant/environment", None)[0], 401)
                self.assertEqual(gateway.dispatch("GET", "/api/assistant/environment?site=2", cookie)[0], 400)
                self.assertEqual(gateway.dispatch("POST", "/api/assistant/environment", cookie, {})[0], 405)
            self.assertEqual(len(core_calls), 2)
            self.assertEqual(len(ha_calls), 2)
            self.assertEqual(store.db.total_changes, before)
            self.assertEqual(tuple(store.db.execute("SELECT seen_token,seen_proposal FROM web_sessions").fetchone()), seen)
        finally:
            store.db.close()


if __name__ == "__main__":
    guard.ATTEMPTS.clear()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(OfflineAdapterTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    files = [ROOT / "Anker_ChargingAndEnergyStorage" / path for path in (
        "energy_runtime/release.py", "energy_runtime/server.py", "assistant/runtime_api.py",
        "assistant/web_gateway.py", "assistant/runtime_tools.py", "frontend/server.py")]
    report = {"kind": "independent_offline_adapter_acceptance", "testsRun": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "externalAttemptsDuringTests": guard.ATTEMPTS,
        "blockedImportSocketProbes": guard.IMPORT_BLOCKED, "connected": False, "deviceWrites": 0,
        "sourceHashes": {str(Path(path).relative_to(ROOT)): hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in files},
        "limitations": "Fake transports and synthetic private files only; no service, remote HA, Feishu or device acceptance."}
    (Path(__file__).parent / "adapter-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    sys.exit(0 if result.wasSuccessful() and not guard.ATTEMPTS else 1)
