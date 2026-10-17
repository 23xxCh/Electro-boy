"""Independent core boundaries with a synthetic source and the real installed LP."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from energy_runtime.live import LocalHA
from energy_runtime.runtime import Rejected, Runtime, canonical
from test_physical_oracle import fixture


HERE = Path(__file__).resolve().parent
OWNER = {"ownerKey": "a" * 64, "householdId": "site_1"}
WINDOW = {"start": "2025-07-18T00:00:00", "end": "2025-07-18T00:30:00",
          "timeBasis": "device_local_naive", "timezone": None}


def constraints(percent=None):
    rows = [] if percent is None else [{"memoryId": "qa_reserve", "revision": 1,
        "type": "reserve_soc", "value": {"minimumPercent": percent}, "startsAt": None, "expiresAt": None}]
    return {"schemaVersion": "ch02-effective-constraints-v1", "memoryVersion": 1,
            "constraintsVersion": "b" * 64, "targetWindow": copy.deepcopy(WINDOW),
            "constraints": rows, "usedMemoryIds": [row["memoryId"] for row in rows]}


class SyntheticSource:
    def __init__(self):
        self.snapshot_calls = self.solve_calls = self.execute_calls = self.stop_calls = 0
        self.execution = "complete"
        self.validation_error = False
        self.reconciliation = "unknown"
        self.reconcile_calls = 0

    def snapshot(self):
        self.snapshot_calls += 1
        return {"environment": "local_debug1_sim", "sourceSlot": 9,
                "targetWindow": copy.deepcopy(WINDOW), "identity": {"fixture": True}}

    def forecast(self, snapshot):
        return {"initialEnergyKwh": 2.5, "physics": {"capacityKwh": 5, "maxChargeKw": 2,
                "maxDischargeKw": 2, "maxGridImportKw": 10, "maxGridExportKw": 10,
                "effCharge": .95, "effDischarge": 1}, "dtHours": .25,
                "pvKw": [0, 0], "loadKw": [1, 1], "buy": [.5, .5], "sell": [0, 0]}

    def solve(self, inputs, floor):
        self.solve_calls += 1
        return LocalHA().solve(inputs, floor)

    def validate_execution(self, episode):
        if self.validation_error:
            raise Rejected("qa_source_changed")

    def execute(self, episode):
        self.execute_calls += 1
        if self.execution == "unknown":
            raise TimeoutError("qa_submission_unknown")
        if self.execution == "submitted":
            return {"haStatus": 200, "status": "submitted"}
        result = fixture(episode["execution"]["targetPowerW"])
        result.update(requestId=episode["episodeId"], readbackMatches=True, settlementAvailable=True,
                      evidenceKind="simulator_settlement", environment="local_debug1_sim")
        return result

    def prepare_execution(self, episode):
        return {"requestId": episode["episodeId"], "sourceSlot": episode["sourceSlot"],
                "targetPowerW": episode["execution"]["targetPowerW"], "fixtureOnly": True}

    def reconcile(self, episode):
        self.reconcile_calls += 1
        if self.reconciliation != "complete":
            return {"settlementAvailable": False, "observedSlot": 11, "readOnly": True}
        result = fixture(episode["execution"]["targetPowerW"])
        result.update(requestId=episode["episodeId"], readbackMatches=True, settlementAvailable=True,
                      evidenceKind="simulator_settlement", environment="local_debug1_sim")
        return result

    def stop(self):
        self.stop_calls += 1
        return {"targetPowerW": 0, "readbackMatches": True, "zeroPowerVerified": True, "fixtureOnly": True}


class CoreContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="core-", dir=HERE)
        self.database = Path(self.temp.name) / "synthetic.sqlite"
        self.adapter = SyntheticSource()
        self.runtime = Runtime(self.adapter, self.database)

    def tearDown(self):
        self.temp.cleanup()

    def proposal(self, percent=None, request="qa_proposal"):
        return self.runtime.propose(OWNER, constraints(percent), request)

    def confirm(self, proposal, confirmation="qa_confirmation", owner=OWNER):
        return self.runtime.confirm(owner, proposal["episodeId"], proposal["proposalId"], confirmation)

    def test_typed_reserve_changes_real_lp_bound_and_action(self):
        base = self.proposal(request="qa_base")
        reserve = self.proposal(50, request="qa_reserve")
        self.assertEqual(base["reserveEnergyKwh"], 0)
        self.assertEqual(reserve["reserveEnergyKwh"], 2.5)
        self.assertEqual(reserve["consumedMemoryIds"], ["qa_reserve"])
        self.assertGreater(base["plan"][0]["discharge_kw"], .9)
        self.assertAlmostEqual(reserve["plan"][0]["discharge_kw"], 0)
        self.assertTrue(all(row["energy_kwh"] >= 2.5 - 1e-8 for row in reserve["plan"]))
        self.assertGreater(reserve["expectedCostEur"], base["expectedCostEur"])
        self.assertEqual(self.adapter.execute_calls, 0)

    def test_unsupported_invalid_dated_or_unbound_constraints_rejected_before_lp(self):
        for field, value in (("type", "qa_unknown"), ("value", {"minimumPercent": True}),
                             ("value", {"minimumPercent": 101}), ("value", {"minimumPercent": float("nan")}),
                             ("startsAt", "2026-10-18T00:00:00+00:00")):
            document = constraints(50)
            document["constraints"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(Rejected):
                self.runtime.propose(OWNER, document, "qa_invalid")
        document = constraints(50)
        document["usedMemoryIds"] = []
        with self.assertRaises(Rejected):
            self.runtime.propose(OWNER, document, "qa_invalid_ids")
        self.assertEqual(self.adapter.solve_calls, 0)
        self.assertEqual(self.adapter.execute_calls, 0)

    def test_infeasible_reserve_and_wrong_window_never_expose_action(self):
        with self.assertRaises(Rejected):
            self.proposal(100)
        document = constraints(50)
        document["targetWindow"]["start"] = "2025-07-18T00:15:00"
        with self.assertRaises(Rejected):
            self.runtime.propose(OWNER, document, "qa_wrong_window")
        self.assertEqual(self.adapter.execute_calls, 0)

    def test_duplicate_proposal_and_confirm_survive_restart_without_second_execution(self):
        proposal = self.proposal(50)
        self.assertEqual(self.proposal(50), proposal)
        self.assertEqual(self.adapter.snapshot_calls, 1)
        final = self.confirm(proposal)
        self.assertEqual(final["status"], "outcome")
        self.runtime = Runtime(self.adapter, self.database)
        self.assertEqual(self.confirm(proposal), final)
        self.assertEqual(self.adapter.execute_calls, 1)
        self.assertEqual(self.adapter.stop_calls, 0)

    def test_owner_site_proposal_expiry_and_changed_source_rejected_before_write(self):
        proposal = self.proposal(50)
        for owner in ({**OWNER, "ownerKey": "c" * 64}, {**OWNER, "householdId": "site_2"},
                      {**OWNER, "ownerKey": "not_opaque"}):
            with self.subTest(owner=owner), self.assertRaises(Rejected):
                self.confirm(proposal, owner=owner)
        with self.assertRaises(Rejected):
            self.runtime.confirm(OWNER, proposal["episodeId"], "qa_wrong_proposal", "qa_confirmation")
        self.adapter.validation_error = True
        with self.assertRaises(Rejected):
            self.confirm(proposal)
        self.adapter.validation_error = False
        proposal["expiresAt"] = 0
        self.runtime.save(proposal)
        with self.assertRaises(Rejected):
            self.confirm(proposal)
        self.assertEqual(self.adapter.execute_calls, 0)

    def test_unknown_submission_is_retained_and_not_resent_after_restart(self):
        proposal = self.proposal(50)
        self.adapter.execution = "unknown"
        final = self.confirm(proposal)
        self.assertEqual(final["status"], "unknown")
        self.assertNotIn("acknowledged", [event["stage"] for event in final["events"]])
        self.runtime = Runtime(self.adapter, self.database)
        self.assertEqual(self.confirm(proposal), final)
        with self.assertRaises(Rejected):
            self.confirm(proposal, confirmation="qa_new_confirmation")
        self.assertEqual(self.adapter.execute_calls, 1)
        self.assertEqual(self.adapter.stop_calls, 1)

    def test_submitted_without_device_proof_cannot_record_ack_or_settlement(self):
        proposal = self.proposal(50)
        self.adapter.execution = "submitted"
        final = self.confirm(proposal)
        self.assertEqual(final["status"], "unknown")
        phases = [event["stage"] for event in final["events"]]
        self.assertNotIn("acknowledged", phases)
        self.assertNotIn("settled", phases)

    def test_reconcile_unknown_is_read_only_and_cannot_bypass_proof_gate(self):
        proposal = self.proposal(50)
        self.adapter.execution = "unknown"
        self.confirm(proposal)
        unresolved = self.runtime.reconcile(OWNER, proposal["episodeId"])
        self.assertEqual(unresolved["status"], "unknown")
        self.assertTrue(unresolved["reconciliation"]["readOnly"])
        self.assertEqual(self.adapter.execute_calls, 1)
        self.assertEqual(self.adapter.stop_calls, 1)
        self.adapter.reconciliation = "complete"
        completed = self.runtime.reconcile(OWNER, proposal["episodeId"])
        self.assertEqual(completed["status"], "outcome")
        self.assertEqual(self.adapter.execute_calls, 1)
        self.assertEqual(self.adapter.stop_calls, 1)
        self.assertEqual(self.adapter.reconcile_calls, 2)

    def test_crash_after_durable_dispatch_never_reexecutes_on_confirmation_replay(self):
        proposal = self.proposal(50)
        stage = self.runtime.stage

        def crash_after_dispatch(episode, phase):
            stage(episode, phase)
            if phase == "dispatched":
                raise SystemExit("QA process crash after durable dispatch")

        with patch.object(self.runtime, "stage", crash_after_dispatch), self.assertRaises(SystemExit):
            self.confirm(proposal)
        self.runtime = Runtime(self.adapter, self.database)
        replay = self.confirm(proposal)
        self.assertEqual(replay["status"], "dispatched")
        self.assertEqual(self.adapter.execute_calls, 0)
        self.adapter.reconciliation = "complete"
        self.assertEqual(self.runtime.reconcile(OWNER, proposal["episodeId"])["status"], "outcome")
        self.assertEqual(self.adapter.execute_calls, 0)

    def test_crash_between_confirmation_insert_and_phase_update_rolls_back_atomically(self):
        proposal = self.proposal(50)

        def crash_at_confirmed_serialization(value):
            if isinstance(value, dict) and value.get("status") == "confirmed":
                raise SystemExit("QA crash after confirmation INSERT before phase UPDATE")
            return canonical(value)

        with patch("energy_runtime.runtime.canonical", crash_at_confirmed_serialization), self.assertRaises(SystemExit):
            self.confirm(proposal)
        self.runtime = Runtime(self.adapter, self.database)
        self.assertEqual(self.runtime.episode(proposal["episodeId"], OWNER["ownerKey"])["status"], "proposed")
        with self.runtime.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM confirmations").fetchone()[0], 0)
        self.assertEqual(self.confirm(proposal)["status"], "outcome")
        self.assertEqual(self.adapter.execute_calls, 1)

    def test_stop_is_durable_idempotent_and_unverified_zero_stays_unknown(self):
        stopped = self.runtime.stop(OWNER, "qa_stop")
        self.assertEqual(stopped["status"], "stopped")
        self.runtime = Runtime(self.adapter, self.database)
        self.assertEqual(self.runtime.stop(OWNER, "qa_stop"), stopped)
        self.assertEqual(self.adapter.stop_calls, 1)
        with patch.object(self.adapter, "stop", return_value={"haStatus": 200}):
            unverified = self.runtime.stop(OWNER, "qa_unverified_stop")
        self.assertEqual(unverified["status"], "unknown")

    def test_proposal_request_with_changed_constraints_version_is_rejected(self):
        self.proposal(50)
        changed = constraints(60)
        changed["constraintsVersion"] = "c" * 64
        with self.assertRaises(Rejected):
            self.runtime.propose(OWNER, changed, "qa_proposal")
        self.assertEqual(self.adapter.solve_calls, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
