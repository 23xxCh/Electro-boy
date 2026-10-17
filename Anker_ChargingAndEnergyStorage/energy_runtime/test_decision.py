"""RD-01 true HiGHS, immutable inputs, readonly views and execution separation."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from .decision import base_view, digest, episode_view, source_digest
from .live import LocalHA
from .runtime import Rejected, Runtime
from .test_runtime import Adapter, OWNER, WINDOW, effective


class ReadAdapter(Adapter):
    def __init__(self):
        super().__init__()
        self.slot = 192
        self.soc = 40.5
        self.forecasts = self.solves = self.snapshots = 0

    def snapshot(self):
        self.snapshots += 1
        return {"sourceSlot": self.slot, "sourceTime": WINDOW["start"], "targetWindow": WINDOW, "paused": True,
                "capturedAt": time.time(), "identity": {"containerId": "synthetic-fixture"},
                "ha": {"telemetry": {"site_1": {"soc_pct": self.soc, "solar_power_w": 0, "home_load_w": 500,
                           "home_load_reported_at": time.time(), "capacity_kwh": 5, "max_charge_w": 2000, "max_discharge_w": 2000}},
                       "controls": {"site_1": {"power_w": 0, "direction": "discharge", "battery_power_w": 0}}}}

    def forecast(self, snapshot):
        self.forecasts += 1
        return super().forecast(snapshot)

    def solve(self, values, floor):
        self.solves += 1
        return super().solve(values, floor)


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.adapter = ReadAdapter()
        self.runtime = Runtime(self.adapter, Path(self.folder.name) / "core.db")

    def tearDown(self):
        self.folder.cleanup()

    def preview(self, percent=40, base=None, replacement=None, request="preview1", parent=None):
        return self.runtime.preview(OWNER, base or effective(), {"minimumPercent": percent, "replaceMemoryId": replacement}, request, parent)

    def test_real_lp_changes_without_creating_executable_episode(self):
        result = self.preview()
        self.assertEqual(result["preview"]["status"], "changed")
        self.assertGreater(result["preview"]["baseline"]["firstAction"]["targetPowerW"], result["plan"]["firstAction"]["targetPowerW"])
        self.assertFalse(result["plan"]["executable"])
        self.assertTrue(result["plan"]["hypothetical"])
        self.assertEqual(result["constraints"]["consumedMemoryIds"], [])
        self.assertIsNone(result["actual"])
        with self.runtime.read_connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0], 0)
        self.assertEqual((self.adapter.forecasts, self.adapter.solves, self.adapter.writes), (1, 2, 0))

    def test_get_projection_is_readonly_and_never_calls_adapter(self):
        preview = self.preview()
        before = (self.adapter.snapshots, self.adapter.forecasts, self.adapter.solves)
        with self.runtime.read_connect() as db:
            version = db.execute("PRAGMA data_version").fetchone()[0]
            first = self.runtime.decision(OWNER["ownerKey"], previewId=preview["previewId"])
            second = self.runtime.decision(OWNER["ownerKey"], previewId=preview["previewId"])
            self.assertEqual(version, db.execute("PRAGMA data_version").fetchone()[0])
        self.assertEqual(first["viewVersion"], second["viewVersion"])
        self.assertEqual(first["viewVersion"], digest({k: v for k, v in first.items() if k != "viewVersion"}))
        self.assertEqual(before, (self.adapter.snapshots, self.adapter.forecasts, self.adapter.solves))

    def test_candidate_infeasible_is_null_not_nan_and_unchanged_is_honest(self):
        result = self.preview(80)
        self.assertEqual(result["presentationState"], "infeasible")
        self.assertIsNone(result["plan"]["firstAction"])
        self.assertIsNone(result["plan"]["expectedHorizonCostEur"])
        json.dumps(result, allow_nan=False)
        unchanged = self.preview(0, request="same")
        self.assertEqual(unchanged["preview"]["status"], "unchanged")

    def test_preserves_other_reserves_and_only_replaces_selected_item(self):
        base = effective(40)
        other = copy.deepcopy(base["constraints"][0])
        other.update(memoryId="other", value={"minimumPercent": 30})
        base["constraints"].append(other)
        base["usedMemoryIds"].append("other")
        result = self.preview(10, base, "reserve1")
        self.assertAlmostEqual(result["plan"]["reserveEnergyKwh"], 1.5)
        actual = copy.deepcopy(base)
        actual.update(memoryVersion=2, constraintsVersion="c" * 64)
        actual["constraints"][0].update(revision=2, value={"minimumPercent": 10})
        before = (self.adapter.forecasts, self.adapter.solves)
        proposal = self.runtime.propose(OWNER, actual, "actual1", result["previewId"])
        self.assertEqual(before, (self.adapter.forecasts, self.adapter.solves))
        self.assertEqual(proposal["consumedMemoryIds"], ["reserve1", "other"])
        self.assertEqual(proposal["forecastDigest"], result["origin"]["forecastDigest"])
        self.assertEqual(proposal["plan"], self.runtime.preview_record(result["previewId"], OWNER["ownerKey"])["candidateResult"]["plan"])

    def test_preview_cannot_confirm_or_reuse_before_memory_confirmation(self):
        preview = self.preview()
        with self.assertRaises(Rejected):
            self.runtime.confirm(OWNER, preview["previewId"], "not-a-proposal", "confirmation")
        with self.assertRaises(Rejected):
            self.runtime.propose(OWNER, effective(), "not-confirmed", preview["previewId"])
        self.assertEqual(self.adapter.writes, 0)

    def test_saved_proposal_and_settlement_preserve_hypothetical_preview(self):
        preview = self.preview()
        saved = effective(40)
        saved.update(memoryVersion=2, constraintsVersion="c" * 64)
        proposal = self.runtime.propose(OWNER, saved, "saved", preview["previewId"])
        counts = (self.adapter.forecasts, self.adapter.solves)
        for stage in ("proposed", "outcome"):
            with self.subTest(stage=stage):
                if stage == "outcome":
                    self.runtime.confirm(OWNER, proposal["episodeId"], proposal["proposalId"], "confirmed")
                view = self.runtime.decision(OWNER["ownerKey"], episodeId=proposal["episodeId"])
                candidate = view["preview"]["candidate"]
                self.assertEqual(view["execution"]["phase"], stage)
                self.assertTrue(candidate["hypothetical"])
                self.assertFalse(view["plan"]["hypothetical"])
                self.assertIsNot(candidate, view["plan"])
                self.assertEqual(candidate, preview["preview"]["candidate"])
                self.assertEqual(candidate["points"], view["plan"]["points"])
                self.assertEqual(candidate["expectedHorizonCostEur"], view["plan"]["expectedHorizonCostEur"])
                self.assertEqual(view["origin"]["forecastDigest"], preview["origin"]["forecastDigest"])
                self.assertEqual(counts, (self.adapter.forecasts, self.adapter.solves))

    def test_owner_payload_reuse_and_changed_source_reject(self):
        preview = self.preview()
        with self.assertRaises(Rejected):
            self.runtime.decision("c" * 64, previewId=preview["previewId"])
        with self.assertRaises(Rejected):
            self.preview(41)
        actual = effective(40)
        actual.update(memoryVersion=2, constraintsVersion="c" * 64)
        self.adapter.soc += 1
        with self.assertRaisesRegex(Rejected, "preview_source_stale"):
            self.runtime.propose(OWNER, actual, "stale", preview["previewId"])
        self.adapter.soc -= 1
        self.runtime.propose(OWNER, effective(), "same-proposal-request")
        with self.assertRaises(Rejected):
            self.runtime.propose(OWNER, effective(40), "same-proposal-request")

    def test_poll_times_do_not_invalidate_physical_origin(self):
        first = self.adapter.snapshot()
        second = self.adapter.snapshot()
        self.assertNotEqual(first["capturedAt"], second["capturedAt"])
        self.assertEqual(source_digest(first), source_digest(second))
        second["ha"]["telemetry"]["site_1"]["soc_pct"] += 1
        self.assertNotEqual(source_digest(first), source_digest(second))

    def test_planning_origin_projects_frozen_values_and_legacy_nulls(self):
        snapshot = self.adapter.snapshot()
        frozen = self.adapter.forecast(snapshot)
        frozen["socRoundingMarginPercent"] = .25
        view = base_view(snapshot, frozen, {})
        self.assertEqual(view["origin"]["initialEnergyKwh"], frozen["initialEnergyKwh"])
        self.assertEqual(view["origin"]["socRoundingMarginPercent"], .25)
        self.assertEqual(view["observation"]["socPercent"], 40.5)
        del frozen["initialEnergyKwh"]
        del frozen["socRoundingMarginPercent"]
        legacy = base_view(snapshot, frozen, {})
        self.assertIsNone(legacy["origin"]["initialEnergyKwh"])
        self.assertIsNone(legacy["origin"]["socRoundingMarginPercent"])

    def test_initial_observation_uses_matching_history_and_device_hour_price(self):
        info = [{"site_id": f"site_{n}", "capacity_kwh": 5, "p_charge_max_kw": 2, "p_discharge_max_kw": 2} for n in range(1, 4)]
        telemetry = {row["site_id"]: {"soc_pct": 40.5, "capacity_kwh": 5, "solar_power_w": 0, "home_load_w": 500,
                     "max_charge_w": 2000, "max_discharge_w": 2000} for row in info}
        controls = {"site_1": {"power_w": 1000, "direction": "charge", "battery_power_w": 500}}
        adapter = LocalHA()
        adapter.guard = lambda: ({"slot": 198, "now": "2025-06-03T01:30:00", "paused": True}, {})
        adapter.read_ha = lambda *_args, **_kwargs: {"telemetry": telemetry, "controls": controls}
        row = {"slot": 197, "grid_import_w": 1234, "grid_export_w": 56.7}
        def read(path):
            if path == "/api/siteinfo":
                return {"sites": info}
            if path.startswith("/api/history"):
                return {"series": [row]}
            return {"today": {"day": 2, "buy": [.7, .123] + [0] * 22, "sell": [.6, -.01] + [0] * 22}}
        with patch("Anker_ChargingAndEnergyStorage.energy_runtime.live.get_json", side_effect=read):
            seen = adapter.snapshot()["observation"]
            self.assertEqual(seen["gridImportKw"], 1.234)
            self.assertAlmostEqual(seen["gridExportKw"], .0567)
            self.assertEqual(seen["batteryActualKw"], .5)
            self.assertEqual(seen["batteryTargetKw"], -1)
            self.assertEqual(seen["prices"]["buyEurKwh"], .123)
            self.assertEqual(seen["prices"]["sourceSlot"], 198)
            row["slot"] = 196
            missing = adapter.snapshot()["observation"]
            self.assertIsNone(missing["gridImportKw"])
            self.assertIsNone(missing["gridExportKw"])

    def test_actual_uses_one_verified_ended_receipt_not_future_inputs(self):
        proposal = self.runtime.propose(OWNER, effective(), "rich")
        def execute(episode):
            proof = Adapter.execute(self.adapter, episode)
            proof.update(historyRow={"slot": 192, "solar_power_w": 0, "home_load_w": 500},
                veBefore={"soc_pct": 40.5}, veAfter={"soc_pct": 30.5, "battery_power_w": 2000,
                  "grid_import_w": 0, "grid_export_w": 1500},
                haAfter={"controls": {"captured_at": time.time(), "site_1": {"power_w": 2000,
                    "direction": "discharge", "battery_power_w": 2000}},
                    "telemetry": {"site_1": {"soc_pct": 30, "solar_power_w": 0, "home_load_w": 500}}})
            return proof
        self.adapter.execute = execute
        receipt = self.runtime.confirm(OWNER, proposal["episodeId"], proposal["proposalId"], "confirmed")
        view = self.runtime.decision(OWNER["ownerKey"], episodeId=receipt["episodeId"])
        self.assertEqual(view["actual"]["executedSlot"], 192)
        self.assertEqual(view["actual"]["observedBoundarySlot"], 193)
        self.assertEqual(view["actual"]["intervalCostEur"], receipt["evidence"]["intervalCostEur"])
        self.assertEqual(view["actual"]["socEndPercent"], 30.5)
        self.assertEqual(view["observation"]["kind"], "execution_readback")
        corrupted = copy.deepcopy(receipt)
        corrupted["evidence"]["historyRow"]["slot"] = 193
        self.assertIsNone(episode_view(corrupted)["actual"])
        corrupted["status"] = "dispatched"
        self.assertIsNone(episode_view(corrupted)["actual"])
        for keys in (("haAfter", "controls"), ("haAfter", "controls", "captured_at"),
                     ("veAfter", "battery_power_w"), ("historyRow", "solar_power_w")):
            with self.subTest(missing=keys):
                partial = copy.deepcopy(receipt)
                target = partial["evidence"]
                for key in keys[:-1]:
                    target = target[key]
                del target[keys[-1]]
                before = copy.deepcopy(partial)
                result = episode_view(partial)
                self.assertIsNone(result["actual"])
                self.assertEqual(result["reasonCode"], "receipt_projection_incomplete")
                self.assertEqual(result["execution"], view["execution"])
                self.assertEqual(result["observation"]["kind"], "decision_origin")
                self.assertEqual(partial, before)
        for invalid in (None, True, float("nan")):
            with self.subTest(batteryPower=invalid):
                partial = copy.deepcopy(receipt)
                partial["evidence"]["veAfter"]["battery_power_w"] = invalid
                result = episode_view(partial)
                self.assertIsNone(result["actual"])
                self.assertEqual(result["reasonCode"], "receipt_projection_incomplete")

    def test_private_or_unsupported_fields_never_enter_constraint_projection(self):
        bad = effective(40)
        bad["constraints"][0]["value"]["privateText"] = "must not be stored"
        with self.assertRaises(Rejected):
            self.preview(base=bad)
        bad = effective(40)
        bad["constraints"][0]["type"] = "device_energy"
        with self.assertRaises(Rejected):
            self.preview(base=bad)

    def test_parent_reuses_frozen_predictor_and_known_new_slot_marks_stale(self):
        parent = self.runtime.propose(OWNER, effective(), "parent")
        result = self.preview(parent=parent["episodeId"])
        self.assertEqual((self.adapter.forecasts, self.adapter.solves), (1, 2))
        self.adapter.slot += 1
        self.runtime.propose(OWNER, effective(), "advanced")
        self.assertEqual(self.runtime.decision(OWNER["ownerKey"], previewId=result["previewId"])["availability"], "stale")

    def test_dispatched_read_is_available_while_confirm_holds_execution_lock(self):
        proposal = self.runtime.propose(OWNER, effective(), "single")
        entered, release = threading.Event(), threading.Event()
        original = self.adapter.execute
        def blocked(episode):
            entered.set()
            release.wait(3)
            return original(episode)
        self.adapter.execute = blocked
        worker = threading.Thread(target=lambda: self.runtime.confirm(OWNER, proposal["episodeId"], proposal["proposalId"], "confirm"))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            started = time.monotonic()
            result = self.runtime.decision(OWNER["ownerKey"], episodeId=proposal["episodeId"])
            self.assertLess(time.monotonic() - started, .5)
            self.assertEqual(result["execution"]["phase"], "dispatched")
            self.assertIsNone(result["actual"])
        finally:
            release.set()
            worker.join(3)


if __name__ == "__main__":
    unittest.main()
