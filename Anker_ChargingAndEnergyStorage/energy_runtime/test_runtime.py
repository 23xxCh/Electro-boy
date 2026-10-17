"""Focused trust-boundary and real HiGHS tests; not a HA execution claim."""
import copy
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from .live import LocalHA
from .runtime import Rejected, Runtime, check_plan, reserve_constraints

OWNER = {"ownerKey": "a" * 64, "householdId": "site_1"}
WINDOW = {"start": "2025-06-03T00:00:00", "end": "2025-06-05T00:00:00", "timeBasis": "device_local_naive", "timezone": None}


def effective(percent=None):
    constraints = [] if percent is None else [{"memoryId": "reserve1", "revision": 1, "type": "reserve_soc", "value": {"minimumPercent": percent}, "startsAt": None, "expiresAt": None}]
    return {"schemaVersion": "ch02-effective-constraints-v1", "memoryVersion": 1, "constraintsVersion": "b" * 64,
            "targetWindow": WINDOW, "constraints": constraints, "usedMemoryIds": [row["memoryId"] for row in constraints], "missingFields": []}


def inputs():
    return {"initialEnergyKwh": 2., "dtHours": .25, "physics": {"capacityKwh": 5., "maxChargeKw": 2., "maxDischargeKw": 2., "maxGridImportKw": 10., "maxGridExportKw": 10., "effCharge": .95, "effDischarge": 1.},
            "pvKw": [0.] * 8, "loadKw": [.5] * 8, "buy": [.4, .4, .4, .4, .1, .1, .1, .1], "sell": [.392, .392, .392, .392, .092, .092, .092, .092]}


class Adapter:
    def __init__(self):
        self.writes = 0
        self.fail = False

    def snapshot(self):
        return {"sourceSlot": 192, "targetWindow": WINDOW}

    def forecast(self, _snapshot):
        return inputs()

    def solve(self, values, floor):
        return LocalHA().solve(values, floor)

    def validate_execution(self, episode):
        if episode["sourceSlot"] != 192:
            raise Rejected("source_changed")

    def execute(self, episode):
        self.writes += 1
        if self.fail:
            raise OSError("submitted_unknown")
        return {"requestId": episode["episodeId"], "environment": "local_debug1_sim", "sourceSlot": 192, "nextSlot": 193,
                "readbackMatches": True, "settlementAvailable": True, "evidenceKind": "simulator_settlement",
                "intervalCostEur": .1, "capacityKwh": 5, "veAfter": {"soc_pct": 40}}

    def prepare_execution(self, episode):
        return {"requestId": episode["episodeId"]}

    def reconcile(self, episode):
        return {"settlementAvailable": False, "readOnly": True}

    def stop(self):
        return {"zeroPowerVerified": True}


class RuntimeTests(unittest.TestCase):
    def test_snapshot_separates_poll_clock_from_physical_input_slot(self):
        info = [{"site_id": f"site_{n}", "capacity_kwh": 5, "p_charge_max_kw": 2, "p_discharge_max_kw": 2} for n in range(1, 4)]
        telemetry = {row["site_id"]: {"capacity_kwh": 5, "max_charge_w": 2000, "max_discharge_w": 2000} for row in info}
        for slot, kind, physical_slot in ((192, "previous_ended_interval_hold", 191), (0, "initial_sim_state", None)):
            adapter = LocalHA()
            adapter.guard = lambda: ({"slot": slot, "now": "2025-06-03T00:00:00", "paused": True}, {})
            adapter.read_ha = lambda *_args, **_kwargs: {"telemetry": telemetry}
            with patch("Anker_ChargingAndEnergyStorage.energy_runtime.live.get_json", return_value={"sites": info}):
                snapshot = adapter.snapshot()
            self.assertEqual(snapshot["sourceSlot"], snapshot["socBoundarySlot"])
            self.assertEqual(snapshot["physicalInputsKind"], kind)
            self.assertEqual(snapshot["telemetryPhysicalAsOfSlot"], physical_slot)

    def test_reserve_changes_real_highs_plan(self):
        adapter = LocalHA()
        base = adapter.solve(inputs(), 0)
        constrained = adapter.solve(inputs(), 2)
        check_plan(base["plan"], inputs(), 0)
        check_plan(constrained["plan"], inputs(), 2)
        self.assertGreater(base["plan"][0]["discharge_kw"], constrained["plan"][0]["discharge_kw"])
        self.assertTrue(all(row["energy_kwh"] >= 2 - 1e-6 for row in constrained["plan"]))
        self.assertEqual(reserve_constraints(effective(40), WINDOW, 5, 8)[1], ["reserve1"])

    def test_invalid_memory_and_time_basis(self):
        item = effective(True)
        self.assertEqual(reserve_constraints(item, WINDOW, 5, 8)[3][0]["reason"], "invalid_reserve_soc")
        item = effective(40)
        item["constraints"][0]["expiresAt"] = "2026-10-18T00:00:00Z"
        self.assertEqual(reserve_constraints(item, WINDOW, 5, 8)[3][0]["reason"], "time_basis_unknown")

    def test_confirm_restarts_idempotently_and_isolates_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            adapter = Adapter()
            runtime = Runtime(adapter, Path(folder) / "episodes.db")
            proposal = runtime.propose(OWNER, effective(40), "request1")
            args = dict(owner=OWNER, episodeId=proposal["episodeId"], proposalId=proposal["proposalId"], confirmationId="confirm1")
            receipt = runtime.confirm(**args)
            self.assertEqual(receipt["status"], "outcome")
            self.assertEqual(Runtime(adapter, runtime.database).confirm(**args)["status"], "outcome")
            self.assertEqual(adapter.writes, 1)
            with self.assertRaises(Rejected):
                runtime.episode(proposal["episodeId"], "c" * 64)

    def test_unknown_submission_never_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            adapter = Adapter()
            adapter.fail = True
            runtime = Runtime(adapter, Path(folder) / "episodes.db")
            proposal = runtime.propose(OWNER, effective(), "request1")
            args = dict(owner=OWNER, episodeId=proposal["episodeId"], proposalId=proposal["proposalId"], confirmationId="confirm1")
            self.assertEqual(runtime.confirm(**args)["status"], "unknown")
            self.assertEqual(runtime.confirm(**args)["status"], "unknown")
            self.assertEqual(adapter.writes, 1)
            with self.assertRaises(Rejected):
                runtime.confirm(**{**args, "confirmationId": "confirm2"})

    def test_submitted_only_never_becomes_acknowledged(self):
        with tempfile.TemporaryDirectory() as folder:
            adapter = Adapter()
            adapter.execute = lambda _episode: {"haStatus": 200}
            runtime = Runtime(adapter, Path(folder) / "episodes.db")
            proposal = runtime.propose(OWNER, effective(), "request1")
            receipt = runtime.confirm(OWNER, proposal["episodeId"], proposal["proposalId"], "c1")
            self.assertEqual(receipt["status"], "unknown")
            self.assertNotIn("acknowledged", [event["stage"] for event in receipt["events"]])
            self.assertFalse(runtime.reconcile(OWNER, receipt["episodeId"])["reconciliation"]["settlementAvailable"])

    def test_stop_request_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Runtime(Adapter(), Path(folder) / "episodes.db")
            self.assertEqual(runtime.stop(OWNER, "stop1")["status"], "stopped")
            runtime.adapter.stop = lambda: self.fail("Repeated stop wrote again")
            self.assertEqual(runtime.stop(OWNER, "stop1")["status"], "stopped")

    def test_expired_wrong_proposal_and_tampered_plan_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Runtime(Adapter(), Path(folder) / "episodes.db")
            proposal = runtime.propose(OWNER, effective(), "request1")
            with self.assertRaises(Rejected):
                runtime.confirm(OWNER, proposal["episodeId"], "wrong", "c1")
            proposal["expiresAt"] = time.time() - 1
            runtime.save(proposal)
            with self.assertRaises(Rejected):
                runtime.confirm(OWNER, proposal["episodeId"], proposal["proposalId"], "c2")
            bad = copy.deepcopy(LocalHA().solve(inputs(), 0)["plan"])
            bad[0]["energy_kwh"] = 5
            with self.assertRaises(Rejected):
                check_plan(bad, inputs(), 0)


if __name__ == "__main__":
    unittest.main()
