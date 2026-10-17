"""Audit a redacted one-tick receipt without contacting HA or importing its runtime."""
import json
import math
from pathlib import Path
import sys


def number(value):
    assert not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value), "finite_number_required"
    return value


def audit_tick(evidence):
    source, following = evidence["sourceSlot"], evidence["nextSlot"]
    assert type(source) is int and type(following) is int and following == source + 1, "exact_next_tick_required"
    row = evidence["historyRow"]
    assert row["slot"] == source, "history_must_describe_dispatched_interval"
    dt = number(evidence["dtHours"])
    capacity = number(evidence["capacityKwh"])
    eta_c, eta_d = number(evidence["effCharge"]), number(evidence["effDischarge"])
    assert dt > 0 and capacity > 0 and 0 < eta_c <= 1 and 0 < eta_d <= 1, "invalid_physical_parameters"
    before, after = evidence["veBefore"], evidence["veAfter"]
    assert before["site_id"] == after["site_id"] == "site_1", "bound_site_required"
    target = number(evidence["targetPowerW"])
    assert abs(number(after["power_setpoint_w"]) - target) <= 0.01, "ve_target_mismatch"
    ha = evidence["haAfter"]
    assert ha["controls"]["sim_slot"] == following == ha["telemetry"]["sim_slot"], "official_ha_tick_mismatch"
    control = ha["controls"]["site_1"]
    assert control["mode"] == "third_party_control" and control["direction"] in ("charge", "discharge"), "official_ha_control_mode_mismatch"
    official_target = number(control["power_w"]) * (-1 if control["direction"] == "charge" else 1)
    assert abs(official_target - target) <= 0.01, "official_ha_target_mismatch"
    soc_before, soc_after = number(before["soc_pct"]), number(after["soc_pct"])
    assert 0 <= soc_before <= 100 and 0 <= soc_after <= 100, "soc_out_of_range"
    accepted = number(after["battery_power_w"])
    assert abs(number(control["battery_power_w"]) - accepted) <= 1.1, "official_ha_battery_mismatch"
    assert abs(number(control["soc_pct"]) - soc_after) <= .51, "official_ha_rounded_soc_mismatch"
    assert abs(number(row["battery_power_w"]) - accepted) <= 0.051, "history_accepted_power_mismatch"
    assert target * accepted >= -0.01 and abs(accepted) <= abs(target) + 0.051, "accepted_power_outside_request"
    # Energy change follows accepted AC power, not the requested command or rounded HA SOC.
    delta_expected = (-accepted * eta_c if accepted < 0 else -accepted / eta_d) * dt / 1000
    delta_observed = (soc_after - soc_before) * capacity / 100
    assert abs(delta_observed - delta_expected) <= 1e-6, "battery_energy_integral_mismatch"
    load = number(row["home_load_w"])
    pv = number(row["solar_power_w"])
    imported, exported = number(row["grid_import_w"]), number(row["grid_export_w"])
    assert min(load, pv, imported, exported) >= 0, "negative_unsigned_power"
    assert min(imported, exported) <= 0.051, "simultaneous_import_export"
    buy, sell = number(evidence["prices"]["buy"]), number(evidence["prices"]["sell"])
    residual = pv + imported + number(row["battery_power_w"]) - load - exported
    if sell < 0:
        assert exported <= 0.051 and accepted <= 0.051 and residual >= -0.21, "negative_sell_price_rule_mismatch"
    else:
        assert abs(residual) <= 0.21, "grid_power_balance_mismatch"
    cost = (imported * buy - exported * sell) * dt / 1000
    reported = number(evidence["intervalCostEur"])
    cumulative = number(after["cum_cost_eur"]) - number(before["cum_cost_eur"])
    assert abs(cost - reported) <= 0.0001 and abs(reported - cumulative) <= 1e-6, "interval_settlement_mismatch"
    assert abs(number(row["actual_cost_eur"]) - number(after["cum_cost_eur"])) <= 0.000051, "history_cumulative_cost_mismatch"
    for site in ("site_2", "site_3"):
        assert evidence["otherSitesBefore"][site]["power_setpoint_w"] == evidence["otherSitesAfter"][site]["power_setpoint_w"], "unbound_site_target_changed"
    return {"sourceSlot": source, "nextSlot": following, "targetPowerW": target,
            "acceptedPowerW": accepted, "deltaEnergyKwh": delta_observed,
            "intervalCostEur": reported, "powerBalanceResidualW": residual,
            "curtailmentInferred": sell < 0 and residual > 0.21,
            "proofLevel": "numeric_receipt_only_until_source_identity_verified"}


if __name__ == "__main__":
    document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    print(json.dumps(audit_tick(document.get("evidence", document)), ensure_ascii=False, allow_nan=False))
