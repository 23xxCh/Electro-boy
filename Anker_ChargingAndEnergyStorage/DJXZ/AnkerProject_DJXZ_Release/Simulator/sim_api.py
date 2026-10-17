# -*- coding: utf-8 -*-
"""sim_api.py —— 模拟器数据 API 客户端（:45678，电价 / 天气 / 比赛窗口历史）。

依据 SOP §5.2：先 POST /api/auth/login 换看板 token（24h 有效），之后每次请求带
Authorization: Bearer <看板token>。与 HA 的 :8123 令牌是两套独立系统，不通用。
"""
from __future__ import annotations

import time

import requests


class SimApiError(Exception):
    pass


class SimApi:
    def __init__(self, base_url: str, username: str, password: str, timeout: float = 10.0):
        self.base = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.token: str | None = None
        self._expires_at: str | None = None
        self.session = requests.Session()
        # TTL 缓存：电价/天气按设备日变化（×180 下设备跨日约 8 分钟），15s 刷新即可
        self._cache: dict = {}          # path -> (ts, data)
        self.cache_ttl_s: float = 15.0

    # ---------- 鉴权 ----------
    def login(self, force: bool = False) -> str:
        if self.token and not force:
            return self.token
        r = self.session.post(f"{self.base}/api/auth/login", json={
            "username": self.username, "password": self.password,
        }, timeout=self.timeout)
        if r.status_code != 200:
            raise SimApiError(f"模拟器登录失败 {r.status_code}: {r.text[:200]}")
        data = r.json()
        self.token = data.get("token")
        self._expires_at = data.get("expires_at")
        if not self.token:
            raise SimApiError(f"登录响应缺 token：{data}")
        return self.token

    def _headers(self) -> dict:
        self.login()
        return {"Authorization": f"Bearer {self.token}"}

    def _get(self, path: str) -> object:
        now = time.time()
        hit = self._cache.get(path)
        if hit and now - hit[0] < self.cache_ttl_s:
            return hit[1]
        last = None
        for i in range(3):
            r = self.session.get(f"{self.base}{path}", headers=self._headers(), timeout=self.timeout)
            if r.status_code == 401:
                # token 过期（24h），换一次重试
                self.login(force=True)
                r = self.session.get(f"{self.base}{path}", headers=self._headers(), timeout=self.timeout)
            if r.status_code == 200:
                data = r.json()
                self._cache[path] = (time.time(), data)
                return data
            last = r
            if r.status_code >= 500 and i < 2:
                time.sleep(1.0 * (i + 1))   # 瞬时 5xx 退避重试（与 ha_client 一致）
        raise SimApiError(f"模拟器 GET {path} -> {last.status_code}: {last.text[:200]}")

    # ---------- 电价（3 站共享） ----------
    def price_today(self) -> dict:
        return self._get("/api/price/today")

    def price_tomorrow(self) -> dict:
        return self._get("/api/price/tomorrow")

    def price_current(self) -> dict:
        return self._get("/api/price/current")

    # ---------- 天气（按站点） ----------
    def weather_today(self, site: int) -> dict:
        return self._get(f"/api/weather/today?site={site}")

    def weather_tomorrow(self, site: int) -> dict:
        return self._get(f"/api/weather/tomorrow?site={site}")

    # ---------- 比赛窗口历史（预热/预测基线，SOP §9.3） ----------
    def contest_history(self, site: int, fmt: str = "json") -> object:
        return self._get(f"/api/contest/history?site={site}&format={fmt}")

    # ---------- 比赛状态（准备倒计时 / 进行中 / 结束） ----------
    def contest_status(self) -> dict:
        """比赛状态：{mode, now, seconds_until_start, seconds_remaining, speed}。

        mode: idle(未启动) / 准备期(seconds_until_start>0) / 进行中(seconds_remaining>0) / ended。
        seconds_until_start = 准备阶段剩余倒计时秒数（5 分钟准备期递减到 0）。
        """
        return self._get("/api/contest")

    # ---------- 单站遥测（状态 + 收益账本，含 settle_price 结算价） ----------
    def telemetry(self, site: int) -> dict:
        """单站遥测：sim_time / pv_power / load_power / soc / revenue。

        revenue.settle_price = 当前累计结算价（整场窗口平均买价，随周进程收敛到定值），
        用于动态期末信用锚（替代固定 terminal_value）。
        """
        return self._get(f"/api/telemetry?site={site}")
