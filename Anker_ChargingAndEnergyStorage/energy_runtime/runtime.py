"""Typed constraints and immutable, owner-scoped simulation episodes."""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

SCHEMA = "energy-runtime-v1"
ENVIRONMENT = "local_debug1_sim"
POLICY = "household_plan_highs_live_pv_lstm_dm_v1"


def target_watts(row):
    return -math.ceil(row["charge_kw"] * 1000 - 1e-6) if row["charge_kw"] > 1e-6 else math.floor(row["discharge_kw"] * 1000 + 1e-6)


class Rejected(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def owner_key(owner):
    if not isinstance(owner, dict) or set(owner) != {"ownerKey", "householdId"}:
        raise Rejected("invalid_owner")
    if owner["householdId"] != "site_1" or not re.fullmatch(r"[0-9a-f]{64}", str(owner["ownerKey"])):
        raise Rejected("owner_or_household_not_allowed")
    return owner["ownerKey"]


def reserve_constraints(effective, target_window, capacity, horizon):
    if not isinstance(effective, dict) or effective.get("schemaVersion") != "ch02-effective-constraints-v1":
        raise Rejected("invalid_constraints_schema")
    if effective.get("targetWindow") != target_window:
        raise Rejected("constraints_window_mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", str(effective.get("constraintsVersion", ""))):
        raise Rejected("invalid_constraints_version")
    floor = 0.0
    consumed, unsupported, rejected = [], [], []
    rows = effective.get("constraints")
    if not isinstance(rows, list) or len(rows) > 1000:
        raise Rejected("invalid_constraints_list")
    seen = set()
    for item in rows:
        mid = item.get("memoryId") if isinstance(item, dict) else None
        if not isinstance(mid, (str, int)) or isinstance(mid, bool) or mid in seen:
            raise Rejected("invalid_or_duplicate_memory_id")
        seen.add(mid)
        if item.get("startsAt") is not None or item.get("expiresAt") is not None:
            rejected.append({"memoryId": mid, "reason": "time_basis_unknown"})
            continue
        if item.get("type") != "reserve_soc":
            unsupported.append({"memoryId": mid, "reason": "unsupported_constraint_type"})
            continue
        value = item.get("value")
        pct = value.get("minimumPercent") if isinstance(value, dict) else None
        if (not isinstance(value, dict) or set(value) != {"minimumPercent"}
                or not isinstance(pct, (int, float)) or isinstance(pct, bool)
                or not math.isfinite(pct) or not 0 <= pct <= 100):
            rejected.append({"memoryId": mid, "reason": "invalid_reserve_soc"})
            continue
        floor = max(floor, capacity * pct / 100)
        consumed.append(mid)
    declared = effective.get("usedMemoryIds", [])
    if not isinstance(declared, list) or set(declared) != seen:
        raise Rejected("typed_memory_ids_mismatch")
    return floor, consumed, unsupported, rejected


def check_plan(plan, inputs, floor):
    """Independent hard checks on every LP slot before any target is exposed."""
    energy = inputs["initialEnergyKwh"]
    cfg = inputs["physics"]
    if len(plan) != len(inputs["buy"]):
        raise Rejected("plan_horizon_mismatch")
    for index, row in enumerate(plan):
        values = [row[k] for k in ("charge_kw", "discharge_kw", "import_kw", "export_kw", "energy_kwh", "curtail_kw")]
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < -1e-6 for v in values):
            raise Rejected("invalid_plan_values")
        ch, dis, imp, exp, end, curtail = values
        if ch > 1e-6 and dis > 1e-6:
            raise Rejected("simultaneous_charge_discharge")
        if (ch > cfg["maxChargeKw"] + 1e-6 or dis > cfg["maxDischargeKw"] + 1e-6
                or imp > cfg["maxGridImportKw"] + 1e-6 or exp > cfg["maxGridExportKw"] + 1e-6
                or end < floor - 1e-6 or end > cfg["capacityKwh"] + 1e-6):
            raise Rejected("plan_boundary_violation")
        if inputs["sell"][index] < 0 and (dis > 1e-6 or exp > 1e-6):
            raise Rejected("negative_export_price_violation")
        expected = energy + (cfg["effCharge"] * ch - dis / cfg["effDischarge"]) * inputs["dtHours"]
        balance = inputs["pvKw"][index] + dis + imp - inputs["loadKw"][index] - ch - exp - curtail
        if abs(expected - end) > 1e-5 or abs(balance) > 1e-5:
            raise Rejected("plan_physics_violation")
        energy = end


def check_receipt(episode, evidence):
    if not isinstance(evidence, dict) or evidence.get("requestId") != episode["episodeId"]:
        raise Rejected("execution_proof_missing")
    if (evidence.get("environment") != ENVIRONMENT or evidence.get("readbackMatches") is not True
            or evidence.get("sourceSlot") != episode["sourceSlot"]
            or evidence.get("nextSlot") != episode["sourceSlot"] + 1
            or evidence.get("settlementAvailable") is not True
            or evidence.get("evidenceKind") != "simulator_settlement"):
        raise Rejected("next_tick_settlement_proof_missing")
    cost = evidence.get("intervalCostEur")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost):
        raise Rejected("settlement_cost_missing")
    state = evidence.get("veAfter", {})
    capacity = evidence.get("capacityKwh")
    soc = state.get("soc_pct")
    if (not isinstance(soc, (int, float)) or isinstance(soc, bool) or not math.isfinite(soc)
            or not 0 <= soc <= 100 or not isinstance(capacity, (int, float)) or capacity <= 0
            or capacity != episode["forecast"]["physics"]["capacityKwh"]):
        raise Rejected("actual_energy_proof_missing")
    if soc / 100 * capacity < episode["reserveEnergyKwh"] - .001:
        raise Rejected("actual_reserve_not_met")


class Runtime:
    def __init__(self, adapter, database):
        self.adapter = adapter
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS episodes (id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(owner,request_id))")
            db.execute("CREATE TABLE IF NOT EXISTS confirmations (owner TEXT NOT NULL, request_id TEXT NOT NULL, episode TEXT NOT NULL, PRIMARY KEY(owner,request_id))")
            db.execute("CREATE TABLE IF NOT EXISTS stops (owner TEXT NOT NULL, request_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(owner,request_id))")
            db.execute("CREATE TABLE IF NOT EXISTS previews (id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(owner,request_id))")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, episode):
        with self.connect() as db:
            db.execute("UPDATE episodes SET payload=? WHERE id=?", (canonical(episode), episode["episodeId"]))

    def episode(self, episode_id, owner):
        with self.connect() as db:
            row = db.execute("SELECT owner,payload FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not row or row[0] != owner:
            raise Rejected("episode_not_found")
        return json.loads(row[1])

    def snapshot(self):
        return self.adapter.snapshot()

    @contextmanager
    def read_connect(self):
        db = sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            db.execute("BEGIN")
            yield db
        finally:
            db.close()

    def preview_record(self, preview_id, key):
        with self.read_connect() as db:
            row = db.execute("SELECT payload FROM previews WHERE id=? AND owner=?", (preview_id, key)).fetchone()
        if not row:
            raise Rejected("preview_not_found")
        return json.loads(row[0])

    def constraint_summary(self, effective, snapshot, inputs):
        floor, consumed, unsupported, rejected = reserve_constraints(effective, snapshot["targetWindow"], inputs["physics"]["capacityKwh"], len(inputs["buy"]))
        if rejected or unsupported:
            raise Rejected(canonical({"reason": "hard_constraints_not_consumed", "unsupported": unsupported, "rejectedMemoryIds": rejected}))
        return {"memoryVersion": effective.get("memoryVersion"), "constraintsVersion": effective["constraintsVersion"],
                "reserveEnergyKwh": floor, "minimumPercent": floor / inputs["physics"]["capacityKwh"] * 100,
                "items": [{key: item.get(key) for key in ("memoryId", "revision", "type", "value", "startsAt", "expiresAt")} for item in effective["constraints"]],
                "consumedMemoryIds": consumed, "contextOnlyMemoryIds": effective.get("contextOnlyMemoryIds", []), "missingFields": effective.get("missingFields", [])}

    def preview(self, owner, effectiveConstraints, candidate, requestId, parentEpisodeId=None):
        from .decision import digest, source_digest
        key = owner_key(owner)
        if not isinstance(requestId, str) or not 1 <= len(requestId) <= 128:
            raise Rejected("invalid_request_id")
        if not isinstance(candidate, dict) or set(candidate) != {"minimumPercent", "replaceMemoryId"}:
            raise Rejected("invalid_preview_candidate")
        percent, replace_id = candidate["minimumPercent"], candidate["replaceMemoryId"]
        if (isinstance(percent, bool) or not isinstance(percent, (int, float)) or not math.isfinite(percent)
                or not 0 <= percent <= 100 or replace_id is not None and not isinstance(replace_id, str)):
            raise Rejected("invalid_preview_candidate")
        try:
            request_digest = digest({"effectiveConstraints": effectiveConstraints, "candidate": candidate, "parentEpisodeId": parentEpisodeId})
        except (TypeError, ValueError):
            raise Rejected("invalid_constraints_payload") from None
        with self.lock:
            with self.read_connect() as db:
                prior = db.execute("SELECT payload FROM previews WHERE owner=? AND request_id=?", (key, requestId)).fetchone()
            if prior:
                previous = json.loads(prior[0])
                if previous["requestDigest"] != request_digest:
                    raise Rejected("preview_request_reused")
                return self.decision(key, previewId=previous["previewId"])
            current = self.adapter.snapshot()
            if parentEpisodeId is not None:
                parent = self.episode(parentEpisodeId, key)
                if parent["status"] != "proposed" or "constraintSummary" not in parent or time.time() > parent["expiresAt"]:
                    raise Rejected("parent_not_previewable")
                snapshot, inputs = parent["sourceSnapshot"], parent["forecast"]
                if source_digest(current) != source_digest(snapshot):
                    raise Rejected("preview_source_stale")
                summary = self.constraint_summary(effectiveConstraints, snapshot, inputs)
                if summary != parent["constraintSummary"]:
                    raise Rejected("preview_baseline_constraints_changed")
                baseline = {"status": "ok", "plan": parent["plan"], "expected_cost": parent["expectedCostEur"]}
            else:
                snapshot, inputs = current, self.adapter.forecast(current)
                summary = self.constraint_summary(effectiveConstraints, snapshot, inputs)
                original_inputs = digest(inputs)
                baseline = self.adapter.solve(inputs, summary["reserveEnergyKwh"])
                if digest(inputs) != original_inputs:
                    raise Rejected("solver_mutated_frozen_inputs")
            if baseline.get("status") != "ok":
                raise Rejected("baseline_constraints_infeasible")
            check_plan(baseline["plan"], inputs, summary["reserveEnergyKwh"])
            if replace_id is not None and replace_id not in summary["consumedMemoryIds"]:
                raise Rejected("replace_memory_not_in_baseline")
            other = [item["value"]["minimumPercent"] for item in summary["items"] if item["memoryId"] != replace_id]
            floor = inputs["physics"]["capacityKwh"] * max([percent, *other]) / 100
            original_inputs = digest(inputs)
            result = self.adapter.solve(inputs, floor)
            if digest(inputs) != original_inputs:
                raise Rejected("solver_mutated_frozen_inputs")
            if result.get("status") == "ok":
                check_plan(result["plan"], inputs, floor)
                state = "unchanged" if digest(result["plan"]) == digest(baseline["plan"]) else "changed"
            else:
                result = {"status": "infeasible", "plan": [], "expected_cost": None}
                state = "infeasible"
            preview_id = str(uuid.uuid4())
            payload = {"previewId": preview_id, "owner": owner, "requestDigest": request_digest, "parentEpisodeId": parentEpisodeId,
                       "createdAt": time.time(), "expiresAt": time.time() + 600, "sourceSnapshot": snapshot, "forecast": inputs,
                       "sourceSnapshotDigest": source_digest(snapshot), "forecastDigest": original_inputs,
                       "constraintSummary": summary, "candidate": candidate, "candidateReserveEnergyKwh": floor,
                       "baseline": baseline, "candidateResult": result, "previewStatus": state}
            with self.connect() as db:
                db.execute("INSERT INTO previews VALUES (?,?,?,?)", (preview_id, key, requestId, canonical(payload)))
            return self.decision(key, previewId=preview_id)

    def decision(self, ownerKey, episodeId=None, previewId=None):
        from .decision import episode_view, preview_view, version
        key = owner_key({"ownerKey": ownerKey, "householdId": "site_1"})
        if bool(episodeId) == bool(previewId):
            raise Rejected("one_decision_reference_required")
        with self.read_connect() as db:
            table = "previews" if previewId else "episodes"
            row = db.execute(f"SELECT payload FROM {table} WHERE id=? AND owner=?", (previewId or episodeId, key)).fetchone()
            if not row:
                raise Rejected("preview_not_found" if previewId else "episode_not_found")
            selected = json.loads(row[0])
            preview = None
            if not previewId and selected.get("previewId"):
                row = db.execute("SELECT payload FROM previews WHERE id=? AND owner=?", (selected["previewId"], key)).fetchone()
                if not row:
                    raise Rejected("preview_not_found")
                preview = json.loads(row[0])
            pending = previewId or selected["status"] == "proposed"
            episodes = [json.loads(r[0]) for r in db.execute("SELECT payload FROM episodes")] if pending else []
            stops = [json.loads(r[0]) for r in db.execute("SELECT payload FROM stops")] if pending else []
        if previewId:
            view = preview_view(selected)
            created = selected["createdAt"]
        else:
            view = episode_view(selected, preview)
            created = selected["events"][0]["at"]
        if pending:
            advanced = any(r.get("events", []) and r["events"][-1]["at"] > created and
                           r.get("evidence", {}).get("nextSlot", r["sourceSlot"]) > selected["sourceSnapshot"]["sourceSlot"] for r in episodes)
            advanced = advanced or any(r.get("createdAt", 0) > created and r.get("evidence", {}).get("nextSlot", -1) > selected["sourceSnapshot"]["sourceSlot"] for r in stops)
            if advanced or time.time() > selected["expiresAt"]:
                view.update(availability="stale", presentationState="expired" if not advanced else view["presentationState"],
                            reasonCode="source_advanced" if advanced else "decision_expired")
        return version(view)

    def propose(self, owner, effectiveConstraints, requestId, previewId=None):
        from .decision import digest, source_digest
        key = owner_key(owner)
        if not isinstance(requestId, str) or not 1 <= len(requestId) <= 128:
            raise Rejected("invalid_request_id")
        try:
            request_digest = digest({"effectiveConstraints": effectiveConstraints, "previewId": previewId})
        except (TypeError, ValueError):
            raise Rejected("invalid_constraints_payload") from None
        with self.lock:
            with self.connect() as db:
                prior = db.execute("SELECT payload FROM episodes WHERE owner=? AND request_id=?", (key, requestId)).fetchone()
            if prior:
                previous = json.loads(prior[0])
                if (previous["constraintsVersion"] != effectiveConstraints.get("constraintsVersion")
                        or previous.get("previewId") != previewId
                        or previous.get("requestDigest", request_digest) != request_digest):
                    raise Rejected("proposal_request_reused_with_changed_constraints")
                return previous
            current = self.adapter.snapshot()
            if previewId is not None:
                preview = self.preview_record(previewId, key)
                if time.time() > preview["expiresAt"] or preview["candidateResult"]["status"] != "ok":
                    raise Rejected("preview_expired_or_infeasible")
                snapshot, inputs = preview["sourceSnapshot"], preview["forecast"]
                if source_digest(current) != preview["sourceSnapshotDigest"] or digest(inputs) != preview["forecastDigest"]:
                    raise Rejected("preview_source_stale")
                summary = self.constraint_summary(effectiveConstraints, snapshot, inputs)
                unchanged = [item for item in preview["constraintSummary"]["items"] if item["memoryId"] != preview["candidate"]["replaceMemoryId"]]
                actual = summary["items"]
                if any(item not in actual for item in unchanged):
                    raise Rejected("preview_other_constraints_changed")
                changed = [item for item in actual if item not in unchanged]
                if (len(changed) != 1 or changed[0]["value"] != {"minimumPercent": preview["candidate"]["minimumPercent"]}
                        or preview["candidate"]["replaceMemoryId"] is not None and changed[0]["memoryId"] != preview["candidate"]["replaceMemoryId"]
                        or abs(summary["reserveEnergyKwh"] - preview["candidateReserveEnergyKwh"]) > 1e-9):
                    raise Rejected("preview_candidate_not_confirmed")
                result = preview["candidateResult"]
            else:
                snapshot, inputs = current, self.adapter.forecast(current)
                summary = self.constraint_summary(effectiveConstraints, snapshot, inputs)
                result = self.adapter.solve(inputs, summary["reserveEnergyKwh"])
            floor, consumed = summary["reserveEnergyKwh"], summary["consumedMemoryIds"]
            if result.get("status") != "ok":
                raise Rejected("constraints_infeasible")
            check_plan(result["plan"], inputs, floor)
            episode_id = str(uuid.uuid4())
            payload = {"schemaVersion": SCHEMA, "environment": ENVIRONMENT, "policyId": POLICY,
                       "owner": owner, "episodeId": episode_id, "proposalId": str(uuid.uuid4()),
                       "status": "proposed", "sourceSlot": snapshot["sourceSlot"], "sourceSnapshot": snapshot,
                       "expiresAt": time.time() + 600, "constraintsVersion": effectiveConstraints["constraintsVersion"],
                       "memoryVersion": effectiveConstraints.get("memoryVersion"), "usedMemoryIds": effectiveConstraints["usedMemoryIds"],
                       "consumedMemoryIds": consumed, "unsupported": [], "rejectedMemoryIds": [],
                       "constraintSummary": summary, "sourceSnapshotDigest": source_digest(snapshot), "forecastDigest": digest(inputs),
                       "previewId": previewId,
                       "requestDigest": request_digest,
                       "reserveEnergyKwh": floor, "forecast": inputs,
                       "execution": {"targetPowerW": target_watts(result["plan"][0]), "sourceSlot": snapshot["sourceSlot"],
                                     "intervalMinutes": 15, "intervalCount": 1, "signConvention": "negative_charge_positive_discharge"},
                       "plan": result["plan"], "expectedCostEur": result["expected_cost"],
                       "events": [{"stage": "proposed", "at": time.time()}]}
            with self.connect() as db:
                db.execute("INSERT INTO episodes VALUES (?,?,?,?)", (episode_id, key, requestId, canonical(payload)))
            return payload

    def confirm(self, owner, episodeId, proposalId, confirmationId):
        key = owner_key(owner)
        if not isinstance(confirmationId, str) or not 1 <= len(confirmationId) <= 128:
            raise Rejected("invalid_confirmation_id")
        with self.lock:
            episode = self.episode(episodeId, key)
            if episode["proposalId"] != proposalId:
                raise Rejected("proposal_mismatch")
            with self.connect() as db:
                prior = db.execute("SELECT episode FROM confirmations WHERE owner=? AND request_id=?", (key, confirmationId)).fetchone()
            if prior:
                if prior[0] != episodeId:
                    raise Rejected("confirmation_reused")
                return episode
            if episode["status"] != "proposed":
                raise Rejected("episode_already_confirmed_or_unknown")
            if time.time() > episode["expiresAt"]:
                raise Rejected("proposal_expired")
            with self.connect() as db:
                unfinished = [json.loads(row[0]) for row in db.execute("SELECT payload FROM episodes")]
            if any(row["status"] in ("confirmed", "dispatched", "unknown") for row in unfinished):
                raise Rejected("unreconciled_execution_exists")
            self.adapter.validate_execution(episode)
            check_plan(episode["plan"], episode["forecast"], episode["reserveEnergyKwh"])
            episode["dispatch"] = self.adapter.prepare_execution(episode)
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = json.loads(db.execute("SELECT payload FROM episodes WHERE id=?", (episodeId,)).fetchone()[0])
                if current["status"] != "proposed":
                    raise Rejected("episode_already_confirmed_or_unknown")
                active = [json.loads(row[0]) for row in db.execute("SELECT payload FROM episodes")]
                if any(row["status"] in ("confirmed", "dispatched", "unknown") for row in active):
                    raise Rejected("unreconciled_execution_exists")
                episode["status"] = "confirmed"
                episode["events"].append({"stage": "confirmed", "at": time.time()})
                db.execute("INSERT INTO confirmations VALUES (?,?,?)", (key, confirmationId, episodeId))
                db.execute("UPDATE episodes SET payload=? WHERE id=?", (canonical(episode), episodeId))
            self.stage(episode, "dispatched")
            try:
                evidence = self.adapter.execute(episode)
                self.complete(episode, evidence)
            except Exception as error:
                episode["failure"] = type(error).__name__ + ": " + str(error)
                try:
                    episode["stopEvidence"] = self.adapter.stop()
                except Exception as stop_error:
                    episode["stopFailure"] = type(stop_error).__name__ + ": " + str(stop_error)
                self.stage(episode, "unknown")
            return episode

    def complete(self, episode, evidence):
        check_receipt(episode, evidence)
        episode["evidence"] = evidence
        self.stage(episode, "acknowledged")
        self.stage(episode, "settled")
        episode["outcome"] = {"kind": "local_sim_single_interval", "intervalCostEur": evidence["intervalCostEur"],
                              "settlementAvailable": True, "reserveMet": True, "savingsClaim": "not_evaluated"}
        self.stage(episode, "outcome")

    def reconcile(self, owner, episodeId):
        key = owner_key(owner)
        with self.lock:
            episode = self.episode(episodeId, key)
            if episode["status"] not in ("dispatched", "unknown", "confirmed"):
                return episode
            evidence = self.adapter.reconcile(episode)
            if evidence.get("settlementAvailable") is True:
                self.complete(episode, evidence)
            else:
                episode["reconciliation"] = evidence
                self.save(episode)
            return episode

    def stop(self, owner, requestId):
        key = owner_key(owner)
        if not isinstance(requestId, str) or not 1 <= len(requestId) <= 128:
            raise Rejected("invalid_request_id")
        with self.lock:
            with self.connect() as db:
                prior = db.execute("SELECT payload FROM stops WHERE owner=? AND request_id=?", (key, requestId)).fetchone()
                if prior:
                    return json.loads(prior[0])
                result = {"requestId": requestId, "status": "unknown", "environment": ENVIRONMENT, "createdAt": time.time()}
                db.execute("INSERT INTO stops VALUES (?,?,?)", (key, requestId, canonical(result)))
            try:
                result["evidence"] = self.adapter.stop()
                if result["evidence"].get("zeroPowerVerified") is not True:
                    raise Rejected("zero_power_not_verified")
                result["status"] = "stopped"
            except Exception as error:
                result["failure"] = type(error).__name__ + ": " + str(error)
            with self.connect() as db:
                db.execute("UPDATE stops SET payload=? WHERE owner=? AND request_id=?", (canonical(result), key, requestId))
            return result

    def stage(self, episode, stage):
        episode["status"] = stage
        episode["events"].append({"stage": stage, "at": time.time()})
        self.save(episode)
