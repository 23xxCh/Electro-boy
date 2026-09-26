# -*- coding: utf-8 -*-
"""http_api.py —— VE 数据/控制 HTTP API（:45678 风格）+ 内置 HTML 看板。

零第三方依赖（http.server 线程化）。对外（外部调度器 / 自制看板 / 前端）：
  GET  /api/health           健康 + 元信息
  GET  /api/meta             仿真元信息
  GET  /api/price[?day=today|tomorrow]   电价（买/卖/价差）
  GET  /api/weather[?site=N][&day=...]   天气（类型/云量/辐照/温度）
  GET  /api/score            调度成绩（saving_settled_percent 等）
  GET  /api/state?site=N     单站实时状态
  GET  /api/siteinfo         站点信息（Site Info）
  POST /api/control {site, power_w}      下发控制功率（W，负=充/正=放）
  POST /api/mode    {site, mode}         切换 operating_mode
  POST /api/sim     {action}             start/pause/resume/stop/step
  GET  /                      内置 HTML 看板（开箱即用的“网页”）
"""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))


def _asset_path(rel: str) -> str:
    """资源路径：PyInstaller 打包(exe)时用 _MEIPASS，否则用源码目录。"""
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", ""), rel)
    return os.path.join(HERE, "..", rel)


DASHBOARD_HTML = _asset_path(os.path.join("web", "dashboard.html"))


def make_handler(engine):
    """返回绑定引擎的请求处理类。"""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, obj, code: int = 200):
            body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _serve_dashboard(self):
            try:
                with open(DASHBOARD_HTML, "r", encoding="utf-8") as f:
                    html = f.read().encode("utf-8")
            except OSError:
                html = b"<h1>dashboard.html missing</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def _safe_error(self, e):
            try:
                self._send({"error": f"internal error: {e}"}, 500)
            except Exception:
                pass

        def do_GET(self):
            try:
                self._do_GET()
            except Exception as e:
                self._safe_error(e)

        def _do_GET(self):
            u = urlparse(self.path)
            path, q = u.path, parse_qs(u.query)
            if path in ("/", "/index.html", "/dashboard.html"):
                return self._serve_dashboard()
            if path == "/api/health":
                return self._send({"ok": True, "meta": engine.meta()})
            if path == "/api/meta":
                return self._send(engine.meta())
            if path == "/api/scheduler":
                return self._send({"name": engine.scheduler_name,
                                   "since": engine.scheduler_since})
            if path == "/api/price":
                day = q.get("day", [None])[0]
                if day in ("today", "tomorrow"):
                    d, _ = engine.day_slot(engine.t)
                    return self._send(engine.price_day(d if day == "today" else d + 1))
                return self._send(engine.price_today_tomorrow())
            if path == "/api/weather":
                site = q.get("site", ["site_1"])[0]
                if site not in engine.sites:
                    return self._send({"error": f"unknown site {site}"}, 404)
                day = q.get("day", [None])[0]
                if day in ("today", "tomorrow"):
                    d, _ = engine.day_slot(engine.t)
                    return self._send(engine.weather_day(site, d if day == "today" else d + 1))
                return self._send(engine.weather_today_tomorrow(site))
            if path == "/api/score":
                return self._send(engine.score())
            if path == "/api/state":
                site = q.get("site", ["site_1"])[0]
                if site not in engine.sites:
                    return self._send({"error": f"unknown site {site}"}, 404)
                return self._send(engine.site_state(site))
            if path == "/api/history":
                site = q.get("site", ["site_1"])[0]
                if site not in engine.sites:
                    return self._send({"error": f"unknown site {site}"}, 404)
                limit = int(q.get("limit", ["500"])[0])
                return self._send({"site_id": site, "series": engine.slot_series(site, limit)})
            if path == "/api/vp":
                site = q.get("site", ["site_1"])[0]
                if site not in engine.sites:
                    return self._send({"error": f"unknown site {site}"}, 404)
                return self._send(engine.vp_data(site))
            if path == "/api/siteinfo":
                return self._send(engine.site_infos())
            if path == "/api/lp_forecast":
                site = q.get("site", ["site_1"])[0]
                if site not in engine.sites:
                    return self._send({"error": f"unknown site {site}"}, 404)
                horizon = int(q.get("horizon", ["96"])[0])
                return self._send(engine.lp_forecast(site, horizon))
            return self._send({"error": "not found"}, 404)

        def do_POST(self):
            try:
                self._do_POST()
            except Exception as e:
                self._safe_error(e)

        def _do_POST(self):
            u = urlparse(self.path)
            path = u.path
            try:
                ln = int(self.headers.get("Content-Length", 0) or 0)
                payload = json.loads(self.rfile.read(ln).decode("utf-8")) if ln else {}
            except Exception:
                payload = {}
            if path == "/api/control":
                site = payload.get("site", "site_1")
                if site not in engine.sites:
                    return self._send({"error": f"unknown site {site}"}, 404)
                engine.set_command(site, float(payload.get("power_w", 0)))
                return self._send({"ok": True, "site": site,
                                   "power_w": engine.commands.get(site)})
            if path == "/api/mode":
                site = payload.get("site", "site_1")
                if site not in engine.sites:
                    return self._send({"error": f"unknown site {site}"}, 404)
                engine.set_operating_mode(site, int(payload.get("mode", 3)))
                return self._send({"ok": True})
            if path == "/api/scheduler/register":
                ok, msg = engine.register_scheduler(str(payload.get("name", "")))
                if not ok:
                    return self._send({"ok": False, "error": msg}, 409)
                return self._send({"ok": True, "name": engine.scheduler_name})
            if path == "/api/scheduler/unregister":
                ok, msg = engine.unregister_scheduler(str(payload.get("name", "")))
                if not ok:
                    return self._send({"ok": False, "error": msg}, 409)
                return self._send({"ok": True})
            if path == "/api/sim":
                action = payload.get("action")
                if action == "start":
                    engine.start()
                elif action == "pause":
                    engine.pause()
                elif action == "resume":
                    engine.resume()
                elif action == "stop":
                    engine.stop()
                elif action == "step":
                    engine.step_manual()
                else:
                    return self._send({"error": f"unknown action {action}"}, 400)
                return self._send({"ok": True, "meta": engine.meta()})
            return self._send({"error": "not found"}, 404)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

    return Handler


class HttpApiServer(threading.Thread):
    def __init__(self, engine, host: str, port: int):
        super().__init__(daemon=True, name="http-api")
        self.engine = engine
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None

    def run(self) -> None:
        handler = make_handler(self.engine)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.serve_forever(poll_interval=0.2)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
