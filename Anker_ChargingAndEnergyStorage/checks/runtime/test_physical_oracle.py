"""Oracle self-checks use declared numbers and never establish a real device ACK."""
import copy
import unittest

from physical_oracle import audit_tick


def fixture(power=-1000):
    # Declared synthetic 15-minute interval: 5kWh battery, 500W load, no PV.
    imported, exported = max(500 - power, 0), max(power - 500, 0)
    delta = (-power * .95 if power < 0 else -power) * .25 / 1000
    cost = (imported * .3 - exported * .1) * .25 / 1000
    return {"sourceSlot": 9, "nextSlot": 10, "dtHours": .25,
            "capacityKwh": 5, "effCharge": .95, "effDischarge": 1,
            "targetPowerW": power, "haAfter": {"controls": {"sim_slot": 10,
                "site_1": {"power_w": abs(power), "direction": "charge" if power < 0 else "discharge",
                           "mode": "third_party_control", "battery_power_w": power,
                           "soc_pct": 50 + delta / 5 * 100}}, "telemetry": {"sim_slot": 10}},
            "veBefore": {"site_id": "site_1", "soc_pct": 50, "cum_cost_eur": 1},
            "veAfter": {"site_id": "site_1", "soc_pct": 50 + delta / 5 * 100,
                        "cum_cost_eur": 1 + cost, "power_setpoint_w": power,
                        "battery_power_w": power},
            "historyRow": {"slot": 9, "home_load_w": 500, "solar_power_w": 0,
                           "battery_power_w": power, "grid_import_w": imported,
                           "grid_export_w": exported, "actual_cost_eur": round(1 + cost, 4)},
            "prices": {"buy": .3, "sell": .1}, "intervalCostEur": cost,
            "otherSitesBefore": {"site_2": {"power_setpoint_w": 0}, "site_3": {"power_setpoint_w": 0}},
            "otherSitesAfter": {"site_2": {"power_setpoint_w": 0}, "site_3": {"power_setpoint_w": 0}}}


class PhysicalOracleTests(unittest.TestCase):
    def test_charge_discharge_and_zero_have_independent_energy_and_cash_integrals(self):
        for power, delta in ((-1000, .2375), (1000, -.25), (0, 0)):
            with self.subTest(power=power):
                result = audit_tick(fixture(power))
                self.assertAlmostEqual(result["deltaEnergyKwh"], delta)
                self.assertEqual(result["nextSlot"], 10)

    def test_corrupted_ticks_targets_units_energy_settlement_and_other_site_fail(self):
        corruptions = [
            ("nextSlot", None, 9), ("historyRow", "slot", 10),
            ("veAfter", "power_setpoint_w", 0),
            ("veAfter", "soc_pct", 50), ("dtHours", None, 900),
            ("historyRow", "grid_import_w", 1000), ("intervalCostEur", None, 10),
            ("capacityKwh", None, True), ("prices", "buy", float("nan")),
        ]
        for section, key, value in corruptions:
            wrong = copy.deepcopy(fixture())
            if key is None:
                wrong[section] = value
            else:
                wrong[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(AssertionError):
                audit_tick(wrong)
        wrong = fixture()
        wrong["haAfter"]["controls"]["site_1"]["power_w"] = 0
        with self.assertRaises(AssertionError):
            audit_tick(wrong)
        wrong = fixture()
        wrong["otherSitesAfter"]["site_2"]["power_setpoint_w"] = 100
        with self.assertRaises(AssertionError):
            audit_tick(wrong)


if __name__ == "__main__":
    unittest.main(verbosity=2)
