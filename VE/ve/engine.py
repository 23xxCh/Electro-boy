# -*- coding: utf-8 -*-
"""engine.py —— VirtualEnv 引擎：3 站点 + 共享电价 + 时钟 + 评分。

与 LP 算法**完全解耦**：引擎只负责
  (1) 生成真值（4 设备叠加负载 / 天气 / PV / 电价 / SOC 物理）；
  (2) 提供 LP 所需状态读取接口 + 接收 LP 控制动作的接口（signed power，负=充/正=放）；
  (3) 按 180 倍速推进时隙，支持可调时长(3-7 天)与无限模拟；
  (4) 每时隙结算一次 Score，供外部调度器/看板各通讯一次。

线程模型：引擎循环在后台线程推进；Modbus / HTTP / HA-REST 服务各自线程读写引擎。
所有对外状态访问用 RLock 保护。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import timedelta
from typing import Dict, Optional

import numpy as np

from .appliances import make_site_appliances
from .config import SiteParams, SimConfig
from .price import PriceGenerator
from .score import compute_score
from .site import Site
from .weather import WeatherGenerator


class VirtualEnvEngine:
    def __init__(self, cfg: SimConfig):
        cfg.validate()
        self.cfg = cfg
        self.slots_per_day = cfg.slots_per_day
        self.dt_h = cfg.dt_h

        self.lock = threading.RLock()
        self.t: int = 0                      # 全局时隙序号
        self.running = False
        self.paused = False
        self.finished = False
        self._thread: Optional[threading.Thread] = None
        self._stop_requested = False
        self._on_step_callbacks = []

        self.price_gen: PriceGenerator = None
        self.sites: Dict[str, Site] = {}
        self.commands: Dict[str, float] = {}       # site_id -> signed W（last-write-wins）
        self._fc_seed: Dict[str, int] = {}
        self.history: deque = deque(maxlen=5000)   # 每日成绩快照
        self._slot_series: Dict[str, deque] = {}   # 每站点逐时隙功率/电费历史（画曲线用）
        self.best_score: Optional[dict] = None
        self.live_score: dict = {}
        self.scheduler_name: Optional[str] = None   # 当前注册的外部调度器名
        self.scheduler_since: Optional[str] = None

        self._build()

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        base_rng = np.random.default_rng(self.cfg.seed)
        # 电价/天气：官方 60min/时隙（逐时，24 点/天）
        self.price_gen = PriceGenerator(
            np.random.default_rng(int(base_rng.integers(0, 2**31))))
        for i in range(1, self.cfg.n_sites + 1):
            sid = f"site_{i}"
            rng = np.random.default_rng(self.cfg.seed + 1000 * i)
            sp = SiteParams(
                site_id=sid,
                capacity_kwh=5.0,
                p_charge_max_kw=2.0,
                p_discharge_max_kw=2.0,
                p_grid_max_kw=10.0,
                pv_peak_kw=1.6,
                pv_pr=0.85,
                load_base_kw=0.3,
                lat=46.0 + 3.0 * i,           # 三站点纬度略微错开 -> 辐照/日长略不同
            )
            wg = WeatherGenerator(sp, np.random.default_rng(int(rng.integers(0, 2**31))),
                                  self.cfg.start)
            apps = make_site_appliances(sid, np.random.default_rng(int(rng.integers(0, 2**31))),
                                        self.slots_per_day, self.dt_h)
            soc0 = float(np.clip(rng.normal(0.5, 0.2), 0.0, 1.0))
            site = Site(sp, apps, wg, np.random.default_rng(int(rng.integers(0, 2**31))),
                        self.dt_h, self.slots_per_day, soc0=soc0)
            self.sites[sid] = site
            self.commands[sid] = 0.0
            self._fc_seed[sid] = int(self.cfg.seed * 1000003 + i)
        self._slot_series = {sid: deque(maxlen=10000) for sid in self.sites}

    # ------------------------------------------------------------------ 时间
    def day_slot(self, t: int) -> tuple[int, int]:
        return t // self.slots_per_day, t % self.slots_per_day

    def now(self, t: int = None) -> "datetime":
        t = self.t if t is None else t
        return self.cfg.start + timedelta(hours=self.dt_h * t)

    # ------------------------------------------------------------------ 单步
    def add_on_step(self, cb) -> None:
        """注册每时隙结束回调（用于刷新 Modbus 寄存器面等）。"""
        self._on_step_callbacks.append(cb)

    def step_once(self) -> tuple[Dict, dict]:
        """推进一个时隙：读取命令 -> 应用动作 -> 结算 -> 评分。"""
        with self.lock:
            d, si = self.day_slot(self.t)
            price = self.price_gen.day(d)
            hi = int(si * self.dt_h)          # 逐时序号（0..23）
            buy = float(price["buy"][hi])
            sell = float(price["sell"][hi])

            reports: Dict[str, dict] = {}
            for sid, site in self.sites.items():
                cmd = float(self.commands.get(sid, 0.0))
                site.power_setpoint_w = int(cmd)
                reports[sid] = site.apply_action(cmd, buy, sell, d, si)

            score = compute_score(self.sites)
            score["slot"] = self.t
            score["timestamp"] = self.now().isoformat(timespec="seconds")
            self.live_score = score

            # 逐时隙历史（供看板画功率/收益曲线）
            ts = score["timestamp"]
            for sid, rep in reports.items():
                self._slot_series[sid].append({
                    "slot": self.t, "timestamp": ts,
                    "home_load_w": round(rep["home_load_w"], 1),
                    "solar_power_w": round(rep["solar_power_w"], 1),
                    "battery_power_w": round(rep["battery_power_w"], 1),
                    "grid_import_w": round(rep["grid_import_w"], 1),
                    "grid_export_w": round(rep["grid_export_w"], 1),
                    "soc_pct": round(rep["soc_pct"], 1),
                    "baseline_k_eur": round(rep["baseline_k_eur"], 4),
                    "baseline_cost_eur": round(rep.get("baseline_cost_eur", 0.0), 4),
                    "actual_cost_eur": round(rep["cum_cost_eur"], 4),
                    "reward_eur": round(rep["baseline_k_eur"] - rep["cum_cost_eur"], 4),
                })

            # 每日边界记录一次历史成绩（供“历史成绩/最好一次”）
            if si == self.slots_per_day - 1:
                snap = dict(score)
                self.history.append(snap)
                if self.best_score is None or snap["saving_settled_percent"] > \
                        self.best_score["saving_settled_percent"]:
                    self.best_score = snap

            self.t += 1
            if self.cfg.duration_days is not None and \
                    self.t >= self.cfg.duration_days * self.slots_per_day:
                self.finished = True
                self.running = False
            # 每时隙结束：通知外部（刷新寄存器面等）
            for cb in self._on_step_callbacks:
                try:
                    cb()
                except Exception:
                    pass
            return reports, score

    # ------------------------------------------------------------------ 控制
    def set_command(self, site_id: str, power_w: float) -> None:
        with self.lock:
            if site_id in self.commands:
                self.commands[site_id] = float(power_w)

    def set_operating_mode(self, site_id: str, mode: int) -> None:
        with self.lock:
            if site_id in self.sites:
                self.sites[site_id].operating_mode = int(mode)

    def register_scheduler(self, name: str) -> tuple:
        """注册当前调度器。返回 (ok, message)。

        限制：同一时间只允许一个外部调度器（基线调度器始终隐含存在）。
        """
        with self.lock:
            name = (name or "").strip()
            if not name:
                return False, "调度器名不能为空"
            if self.scheduler_name and self.scheduler_name != name:
                return False, f"调度器 {self.scheduler_name} 正在运行，请先停止它再启动 {name}"
            self.scheduler_name = name
            self.scheduler_since = self.now().isoformat(timespec="seconds")
            return True, "ok"

    def unregister_scheduler(self, name: str = None) -> tuple:
        """注销调度器。返回 (ok, message)。"""
        with self.lock:
            if name and self.scheduler_name and (name or "").strip() != self.scheduler_name:
                return False, f"当前调度器是 {self.scheduler_name}，不是 {name}"
            self.scheduler_name = None
            self.scheduler_since = None
            return True, "ok"

    # ------------------------------------------------------------------ 后台循环
    def _loop(self) -> None:
        while not self._stop_requested:
            if self.paused:
                time.sleep(0.05)
                continue
            self.step_once()
            if self.finished:
                break
            time.sleep(self.cfg.slot_real_seconds)
        self.running = False

    def start(self) -> None:
        with self.lock:
            if self.running:
                return
            self.running = True
            self._stop_requested = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        with self.lock:
            self.paused = True

    def resume(self) -> None:
        with self.lock:
            self.paused = False
            self.running = True

    def stop(self) -> None:
        self._stop_requested = True
        self.running = False
        self.paused = False

    def step_manual(self) -> tuple[Dict, dict]:
        """暂停时手动单步（调试用）。"""
        with self.lock:
            self.paused = True
        return self.step_once()

    # ------------------------------------------------------------------ 只读查询
    def site_state(self, site_id: str) -> dict:
        with self.lock:
            site = self.sites[site_id]
            d, si = self.day_slot(self.t)
            st = site.state(d, si)
            last = site.last or {}
            return {
                "site_id": site_id,
                "soc_pct": st["soc"] * 100.0,
                "solar_power_w": st["solar_power_kw"] * 1000.0,
                "home_load_w": st["home_load_kw"] * 1000.0,
                "battery_power_w": last.get("battery_power_w", 0.0),
                "battery_charging_w": last.get("battery_charging_w", 0.0),
                "battery_discharging_w": last.get("battery_discharging_w", 0.0),
                "grid_import_w": last.get("grid_import_w", 0.0),
                "grid_export_w": last.get("grid_export_w", 0.0),
                "operating_mode": site.operating_mode,
                "power_setpoint_w": site.power_setpoint_w,
                "cum_cost_eur": site.actual_cost_eur,
                "baseline_k_eur": site.baseline_k_eur,
            }

    def slot_series(self, site_id: str, limit: int = None) -> list:
        """返回某站点逐时隙历史曲线数据（功率 / 累计电费 / 收益）。"""
        with self.lock:
            items = list(self._slot_series.get(site_id, []))
        if limit and limit > 0:
            items = items[-limit:]
        return items

    def vp_data(self, site_id: str) -> dict:
        """给 VisionPanel 的数据接口：收益指标 + 能流矩阵 + SOC。"""
        with self.lock:
            site = self.sites[site_id]
            last = site.last or {}
            k = site.baseline_k_eur
            settled = site.settled_revenue_eur
            baseline_settled = k - site.baseline_cost_eur   # 基线调度器（原生自用）结算收益
            saving_rate = settled / k * 100.0 if k > 0 else 0.0
            return {
                "site_id": site_id,
                "slot": self.t,
                "timestamp": self.now().isoformat(timespec="seconds"),
                "scheduler": self.scheduler_name,
                # 与经济收益相关（5 个 + 基线对照）
                "total_revenue_r_eur": round(site.total_revenue_r_eur, 4),
                "settled_revenue_eur": round(settled, 4),
                "buy_cost_eur": round(site.buy_cost_eur, 4),
                "sell_revenue_eur": round(site.sell_revenue_eur, 4),
                "saving_rate_pct": round(saving_rate, 3),
                "baseline_k_eur": round(k, 4),
                "actual_cost_eur": round(site.actual_cost_eur, 4),
                "baseline_cost_eur": round(site.baseline_cost_eur, 4),
                "baseline_settled_revenue_eur": round(baseline_settled, 4),
                # 与能量流向相关
                "soc_pct": round(last.get("soc_pct", 0.0), 1),
                "flow": last.get("flow", {}),   # 7 条边 + 弃光 + 充电损耗（W）
            }

    def price_day(self, d: int) -> dict:
        pd = self.price_gen.day(d)
        return {"day": d, "buy": [float(x) for x in pd["buy"]],
                "sell": [float(x) for x in pd["sell"]],
                "spread": [float(x) for x in pd["spread"]]}

    def price_today_tomorrow(self) -> dict:
        d, _ = self.day_slot(self.t)
        return {"today": self.price_day(d), "tomorrow": self.price_day(d + 1)}

    def weather_day(self, site_id: str, d: int) -> dict:
        """第 d 天（相对 start）的逐时（60min，24 点）天气预报（类型/云量/辐照/温度）。"""
        site = self.sites[site_id]
        fc = site.weather_forecast_day(d, self._fc_seed[site_id])
        dd = site._day_data(d)
        return {
            "site_id": site_id,
            "day": d,
            "date": self.cfg.start.date() if d == 0 else
                    (self.cfg.start + timedelta(days=d)).date(),
            "weather_type": fc["weather_type"],
            "cloud_pct": [float(x) for x in fc["cloud"]],
            "irradiance_w_m2": [float(x) for x in fc["irradiance"]],
            "temperature_c": [float(x) for x in fc["temperature"]],
            # 实际值（供对照，官方 API 不暴露，但便于自测）
            "_actual_irr": [float(x) for x in dd["irr"]],
        }

    def weather_today_tomorrow(self, site_id: str) -> dict:
        d, _ = self.day_slot(self.t)
        return {"today": self.weather_day(site_id, d),
                "tomorrow": self.weather_day(site_id, d + 1)}

    def lp_forecast(self, site_id: str, horizon: int) -> dict:
        """LP 就绪预报包：未来 horizon 个**仿真时隙**的 买/卖价 + 天气预报 + 时刻/星期特征。

        电价/天气为官方 60min 粒度（逐时），此处上采样到仿真时隙：
        同一小时内的若干 15min 时隙共享同一逐时电价/天气。
        这是「获取 LP 所需状态」的接口：与 LPTest_Demo Controller.decide 的
        (buy, sell, irr_f, cloud_f, temp_f, hours, dows) 一一对应。
        """
        d, si = self.day_slot(self.t)
        n_days = (si + horizon + self.slots_per_day - 1) // self.slots_per_day + 1
        price_days = {d + k: self.price_gen.day(d + k) for k in range(n_days)}
        site = self.sites[site_id]
        fc_days = {d + k: site.weather_forecast_day(d + k, self._fc_seed[site_id])
                   for k in range(n_days)}
        buy, sell, irr, cloud, temp, hours, dows = [], [], [], [], [], [], []
        for k in range(horizon):
            g = si + k
            dd = d + g // self.slots_per_day
            s = g % self.slots_per_day
            hi = int(s * self.dt_h)           # 逐时序号（0..23）
            pd = price_days[dd]
            buy.append(float(pd["buy"][hi]))
            sell.append(float(pd["sell"][hi]))
            wf = fc_days[dd]
            irr.append(float(wf["irradiance"][hi]))
            cloud.append(float(wf["cloud"][hi]))
            temp.append(float(wf["temperature"][hi]))
            hours.append(s * self.dt_h)
            dows.append((self.cfg.start + timedelta(days=dd)).weekday())
        return {"site_id": site_id, "horizon": horizon,
                "buy": buy, "sell": sell, "irradiance": irr, "cloud": cloud,
                "temperature": temp, "hours": hours, "dows": dows}

    def score(self) -> dict:
        with self.lock:
            return {
                "saving_settled_percent": self.live_score.get("saving_settled_percent", 0.0),
                "reward_eur": self.live_score.get("reward_eur", 0.0),
                "baseline_k_eur": self.live_score.get("baseline_k_eur", 0.0),
                "actual_cost_eur": self.live_score.get("actual_cost_eur", 0.0),
                "per_site": self.live_score.get("per_site", {}),
                "slot": self.t,
                "timestamp": self.now().isoformat(timespec="seconds"),
                "best": self.best_score,
                "history": list(self.history),
            }

    def site_infos(self) -> dict:
        with self.lock:
            return {"sites": [s.site_info() for s in self.sites.values()]}

    def meta(self) -> dict:
        return {
            "n_sites": self.cfg.n_sites,
            "granularity_min": self.cfg.granularity_min,
            "slots_per_day": self.slots_per_day,
            "dt_h": self.dt_h,
            "speed": self.cfg.speed,
            "slot_real_seconds": self.cfg.slot_real_seconds,
            "duration_days": self.cfg.duration_days,
            "start": self.cfg.start.isoformat(),
            "now": self.now().isoformat(timespec="seconds"),
            "slot": self.t,
            "running": self.running,
            "paused": self.paused,
            "finished": self.finished,
            "seed": self.cfg.seed,
            "scheduler": self.scheduler_name,
            "scheduler_since": self.scheduler_since,
        }
