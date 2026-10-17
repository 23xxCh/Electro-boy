"""Run with: python -m unittest ha_host.test_export_lp_ha_snapshot"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ha_host.export_lp_ha_snapshot import validate, write_atomic


def sample():
    site = {"soc_pct": 50, "solar_power_w": 750, "home_load_w": 600,
            "home_load_reported_at": 1_790_000_000.0,
            "capacity_kwh": 5, "max_charge_w": 2000, "max_discharge_w": 2000}
    return {"sim_slot": 9, **{f"site_{n}": dict(site) for n in range(1, 4)}}


class ExportTest(unittest.TestCase):
    def test_validates_three_official_sites_then_atomically_replaces(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lp_ha_snapshot.json"
            output.write_text("old", encoding="utf-8")
            payload = validate(sample(), 1_790_000_000.0)
            write_atomic(payload, output)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["sim_slot"], 9)
            self.assertEqual(saved["site_3"]["capacity_kwh"], 5.0)
            self.assertEqual(saved["captured_at"], 1_790_000_000.0)
            self.assertEqual(saved["site_1"]["home_load_reported_at"], 1_790_000_000.0)
            for bad in (None, float("nan"), -1, 20_001):
                invalid = sample()
                invalid["site_2"]["solar_power_w"] = bad
                with self.assertRaises(ValueError):
                    validate(invalid, 1_790_000_001.0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), saved)

    def test_rejects_future_home_load_report(self):
        invalid = sample()
        invalid["site_3"]["home_load_reported_at"] = 1_790_000_006.0
        with self.assertRaises(ValueError):
            validate(invalid, 1_790_000_000.0)

    def test_controller_waits_for_all_three_current_slot_reports(self):
        controller_path = Path(__file__).resolve().parents[1] / "ha_lovelace_preview/sim_src/Controller_LP"
        sys.path.insert(0, str(controller_path))
        try:
            import node_live
        finally:
            sys.path.remove(str(controller_path))
        now = time.time()
        fresh = sample()
        for site in ("site_1", "site_2", "site_3"):
            fresh[site]["home_load_reported_at"] = now - 1
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lp_ha_snapshot.json"
            output.write_text(json.dumps(validate(fresh, now)), encoding="utf-8")
            with patch.object(node_live, "HA_SNAPSHOT", output):
                self.assertEqual(node_live.ha_snapshot(9, timeout=0.3,
                                 min_home_load_reported_at=now - 2)["sim_slot"], 9)
                stale = sample()
                for site in ("site_1", "site_2", "site_3"):
                    stale[site]["home_load_reported_at"] = now - 1
                stale["site_3"]["home_load_reported_at"] = now - 3
                output.write_text(json.dumps(validate(stale, now)), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    node_live.ha_snapshot(9, timeout=0.3,
                                          min_home_load_reported_at=now - 2)


if __name__ == "__main__":
    unittest.main()
