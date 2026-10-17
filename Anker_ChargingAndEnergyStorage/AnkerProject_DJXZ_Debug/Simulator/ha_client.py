# -*- coding: utf-8 -*-
"""ha_client.py —— 真实 Home Assistant REST API 客户端（读状态 / 下发服务）。

依据 SOP §7.2：所有设备数据读写必须经 HA，用长期访问令牌（Authorization: Bearer）。
    GET  /api/states                      列出全部实体
    GET  /api/states/<entity_id>          读单个实体
    POST /api/services/<domain>/<service>  调用服务（select.select_option / number.set_value）
"""
from __future__ import annotations

import time

import requests


class HaError(Exception):
    pass


class HaClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0,
                 retries: int = 3, backoff_s: float = 1.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.backoff_s = backoff_s
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })

    def _get(self, path: str) -> object:
        last = None
        for i in range(self.retries):
            r = self.session.get(f"{self.base}{path}", timeout=self.timeout)
            if r.status_code == 401:
                raise HaError(f"HA 鉴权失败（401）——检查 HA_TOKEN：{path}")
            if r.status_code == 200:
                return r.json()
            last = r
            if r.status_code >= 500 and i < self.retries - 1:
                time.sleep(self.backoff_s * (i + 1))   # 瞬时 5xx 退避重试
        raise HaError(f"HA GET {path} -> {last.status_code}: {last.text[:200]}")

    def _post(self, path: str, body: dict) -> object:
        last = None
        for i in range(self.retries):
            r = self.session.post(f"{self.base}{path}", json=body, timeout=self.timeout)
            if r.status_code == 401:
                raise HaError(f"HA 鉴权失败（401）——检查 HA_TOKEN：{path}")
            if r.status_code in (200, 201):
                return r.json()
            last = r
            if r.status_code >= 500 and i < self.retries - 1:
                time.sleep(self.backoff_s * (i + 1))   # 瞬时 5xx 退避重试
        raise HaError(f"HA POST {path} -> {last.status_code}: {last.text[:200]}")

    # ---------- 读 ----------
    def states(self) -> list:
        return self._get("/api/states")

    def get_state(self, entity_id: str) -> dict:
        return self._get(f"/api/states/{entity_id}")

    def get_float(self, entity_id: str) -> float:
        st = self.get_state(entity_id)
        try:
            return float(st["state"])
        except (ValueError, TypeError):
            raise HaError(f"实体 {entity_id} 的 state 不是数值：{st.get('state')!r}")

    # ---------- 写 ----------
    def call_service(self, domain: str, service: str, entity_id: str, **data) -> object:
        body = {"entity_id": entity_id, **data}
        return self._post(f"/api/services/{domain}/{service}", body)

    def set_select(self, entity_id: str, option: str) -> object:
        return self.call_service("select", "select_option", entity_id, option=option)

    def set_number(self, entity_id: str, value: float) -> object:
        return self.call_service("number", "set_value", entity_id, value=float(value))
