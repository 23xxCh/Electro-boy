"""模拟器数据口（电价/天气/评分）。路径以开箱夜截图为准，缺的留空。"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class SimError(RuntimeError):
    pass


class SimClient:
    def __init__(self, base: str, timeout: float = 8.0):
        self.base = (base or "").rstrip("/")
        self.timeout = timeout

    def get_json(self, path: str, query: dict | None = None) -> dict | list | None:
        if not self.base or not path:
            return None
        qs = f"?{urllib.parse.urlencode(query)}" if query else ""
        url = f"{self.base}{path}{qs}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise SimError(f"sim {url} -> {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise SimError(f"sim 连不上 {url}: {exc.reason}") from exc
        return json.loads(raw) if raw else {}

    def weather(self, path: str, site: int) -> dict | list | None:
        return self.get_json(path, {"site": site}) if path else None
