"""Run with: python -m unittest ha_host.test_ve_bridge"""

import tempfile
import unittest
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from ha_host.ve_bridge import SnapshotSource, build_snapshot, make_handler, read_decisions


class BridgeSnapshotTest(unittest.TestCase):
    def test_ve_dashboard_proxy_is_read_only(self):
        writes = []

        class VE(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                body = (b"<html><head></head><body>VE</body></html>" if self.path == "/"
                        else b'{"slot": 7}')
                self.send_response(200)
                self.send_header("Content-Type", "text/html" if self.path == "/" else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                writes.append(self.path)
                self.send_response(200)
                self.end_headers()

        ve = ThreadingHTTPServer(("127.0.0.1", 0), VE)
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
            SnapshotSource(f"http://127.0.0.1:{ve.server_port}")))
        threads = [Thread(target=server.serve_forever, daemon=True) for server in (ve, proxy)]
        for thread in threads:
            thread.start()
        try:
            base = f"http://127.0.0.1:{proxy.server_port}"
            with urllib.request.urlopen(base + "/", timeout=2) as response:
                self.assertIn("只读预览".encode(), response.read())
                self.assertEqual(response.headers["Content-Type"], "text/html")
            with urllib.request.urlopen(base + "/api/meta", timeout=2) as response:
                self.assertEqual(response.read(), b'{"slot": 7}')
                self.assertEqual(response.headers["Content-Type"], "application/json")
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(
                    base + "/api/control", data=b"{}", method="POST"), timeout=2)
            self.assertEqual(error.exception.code, 405)
            self.assertEqual(writes, [])
        finally:
            for server in (proxy, ve):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=2)

    def test_tariff_forecast_score_and_native_baseline_stay_distinct(self):
        meta = {"slot": 49, "slots_per_day": 96, "granularity_min": 15,
                "n_sites": 3, "now": "2025-06-01T12:15:00",
                "start": "2025-06-01T00:00:00"}
        buy = [0.10] * 24
        sell = [0.09] * 24
        buy[12], sell[12] = -0.02, -0.03
        price = {"today": {"day": 0, "buy": buy, "sell": sell},
                 "tomorrow": {"day": 1, "buy": [0.11] * 24, "sell": [0.10] * 24}}
        weather = {
            f"site_{n}": {"today": {"site_id": f"site_{n}", "day": 0,
                                     "irradiance_w_m2": [n * 100] * 24,
                                     "weather_type": ["sunny"] * 24}}
            for n in range(1, 4)
        }
        vp = {
            f"site_{n}": {"site_id": f"site_{n}", "slot": 49,
                           "baseline_cost_eur": baseline,
                           "actual_cost_eur": actual}
            for n, (baseline, actual) in enumerate(((2, 1.5), (1, 1.25), (2, 1)), 1)
        }
        score = {"slot": 49, "timestamp": "2025-06-01T12:00:00",
                 "saving_settled_percent": 11.5, "baseline_k_eur": 4.237,
                 "actual_cost_eur": 3.75}
        result = build_snapshot(meta, price, weather, score, vp,
                                datetime(2026, 9, 27, tzinfo=timezone.utc))
        self.assertEqual(result["tariff_current_buy"]["state"], -0.02)
        self.assertEqual(result["tariff_current_buy"]["sell_price"], -0.03)
        self.assertEqual(result["tariff_current_buy"]["periods"][0]["start"],
                         "2025-06-01T12:15:00+02:00")
        self.assertEqual(result["forecast_site2"]["state"], 200)
        self.assertEqual(result["score_total"]["state"], 11.5)
        self.assertEqual(result["score_total"]["score_basis"], "all_load_purchased")
        self.assertEqual(result["savings_amount_total"]["state"], 1.25)
        self.assertEqual(result["savings_percent_vs_native"]["state"], 25.0)

    def test_decision_requires_real_current_slot_xml(self):
        observed = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "1" / "site_1"
            folder.mkdir(parents=True)
            (folder / "slot_000049.xml").write_text(
                "<decision><site_id>site_1</site_id><slot>49</slot>"
                "<timestamp>2025-06-01T12:15:00</timestamp>"
                "<action><net_power_w>-370</net_power_w></action>"
                "<explanation><reason>低价充电</reason></explanation></decision>",
                encoding="utf-8")
            values = read_decisions(Path(temp), 49, "2025-06-01T12:15:00", observed, 15)
            self.assertEqual(values["decision_site1"]["state"], "充电")
            self.assertEqual(values["decision_site1"]["sim_slot"], 49)
            self.assertEqual(values["decision_site1"]["target_power_w"], -370)
            self.assertEqual(values["decision_site2"]["state"], "unavailable")
            self.assertEqual(read_decisions(Path(temp), 50, "2025-06-01T12:30:00",
                                            observed, 15)["decision_site1"]["state"],
                             "unavailable")
            os.utime(folder / "slot_000049.xml",
                     (observed.timestamp() - 30, observed.timestamp() - 30))
            self.assertEqual(read_decisions(Path(temp), 49, "2025-06-01T12:15:00",
                                            observed, 15)["decision_site1"]["state"],
                             "unavailable")

    def test_pending_decision_uses_only_fresh_previous_slot_while_running(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "1" / "site_1"
            folder.mkdir(parents=True)
            previous = folder / "slot_000049.xml"
            previous.write_text(
                "<decision><site_id>site_1</site_id><slot>49</slot>"
                "<timestamp>2025-06-01T12:15:00</timestamp>"
                "<explanation><reason>低价充电</reason></explanation></decision>",
                encoding="utf-8")
            observed = datetime.now(timezone.utc)
            pending = read_decisions(Path(temp), 50, "2025-06-01T12:30:00",
                                     observed, 30, running=True)["decision_site1"]
            self.assertEqual(pending["state"], "计算中")
            self.assertEqual(pending["sim_slot"], 50)
            self.assertEqual(pending["previous_slot"], 49)
            self.assertEqual(pending["previous_reason"], "低价充电")
            self.assertIn("上一槽", pending["reason"])
            self.assertNotIn("target_power_w", pending)
            self.assertEqual(read_decisions(Path(temp), 50, "2025-06-01T12:30:00",
                                            observed, 30)["decision_site1"]["state"],
                             "unavailable")
            current = folder / "slot_000050.xml"
            current.write_text(
                "<decision><site_id>site_1</site_id><slot>50</slot>"
                "<timestamp>2025-06-01T12:30:00</timestamp>"
                "<action><net_power_w>250</net_power_w></action>"
                "<explanation><reason>本槽放电</reason></explanation></decision>",
                encoding="utf-8")
            self.assertEqual(read_decisions(Path(temp), 50, "2025-06-01T12:30:00",
                                            observed, 30, running=True)["decision_site1"]["state"],
                             "放电")
            current.unlink()
            os.utime(previous, (observed.timestamp() - 46, observed.timestamp() - 46))
            self.assertEqual(read_decisions(Path(temp), 50, "2025-06-01T12:30:00",
                                            observed, 30, running=True)["decision_site1"]["state"],
                             "unavailable")


if __name__ == "__main__":
    unittest.main()
