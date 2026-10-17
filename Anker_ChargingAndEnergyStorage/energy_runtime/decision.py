"""RD-01 immutable decision projection. No network, solver or database writes."""
from datetime import datetime, timedelta
import hashlib
import math

from .runtime import ENVIRONMENT, POLICY, Rejected, canonical, check_receipt, target_watts


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def source_digest(snapshot):
    raw = snapshot.get("ha", {}).get("telemetry", {}).get("site_1", {})
    return digest({"sourceSlot": snapshot["sourceSlot"], "sourceTime": snapshot.get("sourceTime"),
                   "identity": snapshot.get("identity"), "targetWindow": snapshot["targetWindow"],
                   "physical": {key: value for key, value in raw.items() if key != "home_load_reported_at"}})


def version(view):
    view["viewVersion"] = digest({key: value for key, value in view.items() if key != "viewVersion"})
    return view


def observation(snapshot, buy=None, sell=None, kind="decision_origin"):
    if "observation" in snapshot:
        return {**snapshot["observation"], "kind": kind}
    raw = snapshot.get("ha", {}).get("telemetry", {}).get("site_1", {})
    control = snapshot.get("ha", {}).get("controls", {}).get("site_1", {})
    def kw(value):
        return value / 1000 if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
    target = control.get("power_w")
    signed = kw(target) if control.get("direction") == "discharge" else -kw(target) if control.get("direction") == "charge" and kw(target) is not None else None
    return {"kind": kind, "lastKnown": True, "sourceSlot": snapshot["sourceSlot"],
            "measurementSourceSlot": snapshot.get("telemetryPhysicalAsOfSlot", snapshot["sourceSlot"] - 1),
            "socBoundarySlot": snapshot.get("socBoundarySlot", snapshot["sourceSlot"]),
            "physicalInputsKind": snapshot.get("physicalInputsKind", "previous_ended_interval_hold"),
            "observedAt": snapshot.get("capturedAt"), "paused": snapshot.get("paused"),
            "socPercent": raw.get("soc_pct"), "pvKw": kw(raw.get("solar_power_w")), "loadKw": kw(raw.get("home_load_w")),
            "batteryActualKw": kw(control.get("battery_power_w")), "batteryTargetKw": signed,
            "gridImportKw": None, "gridExportKw": None,
            "prices": {"buyEurKwh": buy, "sellEurKwh": sell, "sourceSlot": snapshot["sourceSlot"], "kind": "plan_origin_forecast"},
            "fieldSources": {"grid": None, "power": "official_ha"}}


def interval(snapshot, inputs, index):
    origin = snapshot.get("sourceTime", snapshot["targetWindow"]["start"])
    start = datetime.fromisoformat(origin) + timedelta(hours=inputs["dtHours"] * index)
    return {"targetSlot": snapshot["sourceSlot"] + index, "start": start.isoformat(timespec="seconds"),
            "end": (start + timedelta(hours=inputs["dtHours"])).isoformat(timespec="seconds")}


def plan_view(snapshot, inputs, result, floor, hypothetical):
    ok = result.get("status") == "ok"
    points = []
    for index, row in enumerate(result.get("plan", []) if ok else []):
        points.append({**interval(snapshot, inputs, index), "chargeKw": row["charge_kw"], "dischargeKw": row["discharge_kw"],
                       "importKw": row["import_kw"], "exportKw": row["export_kw"], "curtailKw": row["curtail_kw"],
                       "energyEndKwh": row["energy_kwh"], "socEndPercent": row["energy_kwh"] / inputs["physics"]["capacityKwh"] * 100})
    return {"status": "ok" if ok else "infeasible", "reserveEnergyKwh": floor,
            "minimumPercent": floor / inputs["physics"]["capacityKwh"] * 100, "hypothetical": hypothetical,
            "executable": False, "expectedHorizonCostEur": result["expected_cost"] if ok else None,
            "firstAction": {"targetPowerW": target_watts(result["plan"][0]), "sourceSlot": snapshot["sourceSlot"],
                            "intervalMinutes": 15, "intervalCount": 1, "signConvention": "negative_charge_positive_discharge"} if ok else None,
            "points": points}


def base_view(snapshot, inputs, summary):
    origin = {"environment": ENVIRONMENT, "policyId": POLICY, "sourceSlot": snapshot["sourceSlot"],
              "deviceTime": snapshot.get("sourceTime", snapshot["targetWindow"]["start"]),
              "timeBasis": snapshot["targetWindow"]["timeBasis"], "targetWindow": snapshot["targetWindow"],
              "dtHours": inputs["dtHours"], "initialEnergyKwh": inputs.get("initialEnergyKwh"),
              "socRoundingMarginPercent": inputs.get("socRoundingMarginPercent"),
              "measurementSourceSlot": snapshot.get("telemetryPhysicalAsOfSlot", snapshot["sourceSlot"] - 1),
              "socBoundarySlot": snapshot.get("socBoundarySlot", snapshot["sourceSlot"]),
              "physicalInputsKind": snapshot.get("physicalInputsKind", "previous_ended_interval_hold"),
              "sourceSnapshotDigest": source_digest(snapshot), "forecastDigest": digest(inputs), "physics": inputs["physics"],
              "observedAt": snapshot.get("capturedAt"), "sourceValidation": "last_known"}
    points = [{**interval(snapshot, inputs, k), "pvKw": pv, "loadKw": inputs["loadKw"][k],
               "buyEurKwh": inputs["buy"][k], "sellEurKwh": inputs["sell"][k],
               "inputKind": "held_anchor" if k == 0 else "model_forecast"} for k, pv in enumerate(inputs["pvKw"])]
    return {"schemaVersion": "assistant-decision-v1", "availability": "ready", "presentationState": "proposed", "reasonCode": None,
            "episodeId": None, "proposalId": None, "previewId": None, "expiresAt": None, "origin": origin,
            "observation": observation(snapshot, inputs["buy"][0], inputs["sell"][0]), "constraints": summary,
            "currentConstraints": None, "pendingRef": None, "forecast": {"points": points, "provenance": inputs.get("provenance", {})},
            "plan": None, "preview": None,
            "execution": {"phase": "proposed", "events": [], "requestId": None, "executedSlot": None,
                          "observedBoundarySlot": None, "readbackMatches": None, "settlementAvailable": False, "failureCode": None},
            "actual": None, "savings": "not_evaluated"}


def preview_view(preview):
    snapshot, inputs = preview["sourceSnapshot"], preview["forecast"]
    view = base_view(snapshot, inputs, preview["constraintSummary"])
    baseline = plan_view(snapshot, inputs, preview["baseline"], preview["constraintSummary"]["reserveEnergyKwh"], False)
    candidate = plan_view(snapshot, inputs, preview["candidateResult"], preview["candidateReserveEnergyKwh"], True)
    view.update(previewId=preview["previewId"], expiresAt=preview["expiresAt"], plan=candidate,
                presentationState=preview["previewStatus"] if preview["previewStatus"] != "changed" else "preview")
    view["preview"] = {"status": preview["previewStatus"], "parentEpisodeId": preview.get("parentEpisodeId"),
                       "candidateDigest": digest(preview["candidate"]), "requestedMinimumPercent": preview["candidate"]["minimumPercent"],
                       "replaceMemoryId": preview["candidate"]["replaceMemoryId"], "sourceSnapshotDigest": source_digest(snapshot),
                       "forecastDigest": digest(inputs), "sameOrigin": True, "baseline": baseline, "candidate": candidate}
    view["execution"]["phase"] = "preview"
    return view


def episode_view(episode, preview=None):
    snapshot, inputs = episode["sourceSnapshot"], episode["forecast"]
    floor = episode["reserveEnergyKwh"]
    summary = episode.get("constraintSummary", {"memoryVersion": episode.get("memoryVersion"), "constraintsVersion": episode["constraintsVersion"],
        "reserveEnergyKwh": floor, "minimumPercent": floor / inputs["physics"]["capacityKwh"] * 100, "items": [],
        "consumedMemoryIds": episode["consumedMemoryIds"], "contextOnlyMemoryIds": [], "missingFields": []})
    view = base_view(snapshot, inputs, summary)
    view.update(episodeId=episode["episodeId"], proposalId=episode["proposalId"], expiresAt=episode["expiresAt"],
                plan=plan_view(snapshot, inputs, {"status": "ok", "plan": episode["plan"], "expected_cost": episode["expectedCostEur"]}, floor, False))
    if preview is not None:
        compared = preview_view(preview)
        view.update(previewId=preview["previewId"], preview=compared["preview"])
    status = episode["status"]
    view["presentationState"] = "settled" if status in ("settled", "outcome") else "executing" if status in ("confirmed", "dispatched", "acknowledged") else status
    proof = episode.get("evidence", {})
    view["execution"] = {"phase": status, "events": [{"sequence": k + 1, "phase": e["stage"], "eventAt": e["at"],
        "evidenceKind": "simulator_settlement" if e["stage"] == "settled" and proof.get("settlementAvailable") is True else
                        "ha_readback" if e["stage"] == "acknowledged" and proof.get("readbackMatches") is True else None}
        for k, e in enumerate(episode["events"])], "requestId": episode.get("dispatch", {}).get("requestId"),
        "executedSlot": proof.get("sourceSlot"), "observedBoundarySlot": proof.get("nextSlot"),
        "readbackMatches": proof.get("readbackMatches"), "settlementAvailable": proof.get("settlementAvailable", False),
        "failureCode": episode.get("failure", "").split(":", 1)[0] or None}
    if status in ("settled", "outcome") and proof.get("readbackMatches") is True and proof.get("settlementAvailable") is True:
        try:
            check_receipt(episode, proof)
        except (Rejected, KeyError, TypeError, ValueError):
            view["reasonCode"] = "receipt_unverified"
            return view
        try:
            row, before, after = proof["historyRow"], proof["veBefore"], proof["veAfter"]
            captured_at = proof["haAfter"]["controls"]["captured_at"]
            numbers = (row["solar_power_w"], row["home_load_w"], before["soc_pct"], after["soc_pct"],
                       after["battery_power_w"], after["grid_import_w"], after["grid_export_w"], captured_at)
            if (row["slot"] != proof["sourceSlot"] or any(isinstance(value, bool)
                    or not isinstance(value, (int, float)) or not math.isfinite(value) for value in numbers)):
                raise ValueError("incomplete_receipt_projection")
            actual = {"executedSlot": proof["sourceSlot"], "observedBoundarySlot": proof["nextSlot"],
                **{key: value for key, value in interval(snapshot, inputs, 0).items() if key != "targetSlot"},
                "pvKw": row["solar_power_w"] / 1000, "loadKw": row["home_load_w"] / 1000,
                "batteryActualKw": after["battery_power_w"] / 1000, "gridImportKw": after["grid_import_w"] / 1000,
                "gridExportKw": after["grid_export_w"] / 1000, "socStartPercent": before["soc_pct"], "socEndPercent": after["soc_pct"],
                "intervalCostEur": proof["intervalCostEur"], "reserveMet": episode.get("outcome", {}).get("reserveMet"),
                "readbackMatches": True, "settlementAvailable": True, "evidenceRef": episode["episodeId"]}
            after_snapshot = {"sourceSlot": proof["nextSlot"], "capturedAt": captured_at,
                              "paused": True, "ha": proof["haAfter"]}
            current = observation(after_snapshot, inputs["buy"][1] if len(inputs["buy"]) > 1 else None,
                                  inputs["sell"][1] if len(inputs["sell"]) > 1 else None, "execution_readback")
            current.update(gridImportKw=actual["gridImportKw"], gridExportKw=actual["gridExportKw"])
            current["fieldSources"]["grid"] = "receipt"
        except (KeyError, TypeError, AttributeError, ValueError):
            view["reasonCode"] = "receipt_projection_incomplete"
            return view
        view.update(actual=actual, observation=current)
    return view
