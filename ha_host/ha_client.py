"""Home Assistant REST。只读写允许的实体。"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

FORBIDDEN_KEYS = (
    "charging_limit_soc",
    "discharge_limit_soc",
    "backup_reserve_soc",
    "backup_soc_enable",
)


class HAError(RuntimeError):
    pass


class HAClient:
    def __init__(self, base: str, token: str, timeout: float = 8.0):
        self.base = base.rstrip("/")
        self.token = token or os.environ.get("HA_TOKEN", "")
        self.timeout = timeout

    def _headers(self) -> dict:
        if not self.token:
            raise HAError("缺少 HA token（config.ha_token 或环境变量 HA_TOKEN）")
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, body: dict | None = None) -> dict | list:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            headers=self._headers(),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise HAError(f"HA {method} {path} -> {exc.code}: {detail[:400]}") from exc
        except urllib.error.URLError as exc:
            raise HAError(f"HA 连不上 {self.base}: {exc.reason}") from exc
        return json.loads(raw) if raw else {}

    def get_state(self, entity_id: str) -> dict:
        return self._request("GET", f"/api/states/{entity_id}")

    def call_service(self, domain: str, service: str, payload: dict) -> list | dict:
        return self._request("POST", f"/api/services/{domain}/{service}", payload)

    def set_select(self, entity_id: str, option: str, dry_run: bool) -> dict:
        self._assert_writable(entity_id)
        body = {"entity_id": entity_id, "option": option}
        if dry_run:
            return {"dry_run": True, "service": "select.select_option", "body": body}
        return {"ok": True, "result": self.call_service("select", "select_option", body)}

    def set_number(self, entity_id: str, value: float, dry_run: bool) -> dict:
        self._assert_writable(entity_id)
        body = {"entity_id": entity_id, "value": value}
        if dry_run:
            return {"dry_run": True, "service": "number.set_value", "body": body}
        return {"ok": True, "result": self.call_service("number", "set_value", body)}

    @staticmethod
    def _assert_writable(entity_id: str) -> None:
        lowered = entity_id.lower()
        for key in FORBIDDEN_KEYS:
            if key in lowered:
                raise HAError(f"禁止写 SOC 限值实体: {entity_id}")
