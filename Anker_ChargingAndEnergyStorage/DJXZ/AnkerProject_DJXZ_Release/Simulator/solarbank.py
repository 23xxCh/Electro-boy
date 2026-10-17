# -*- coding: utf-8 -*-
"""solarbank.py —— Solarbank Max AC 客户端：站点发现 + 读状态 + 下指令 + 组装 LP 预报。

把「真实环境机」的 HA(:8123) 与模拟器(:45678) 封装成一个供 main.py 调用的软件包：
    - 站点发现：从 HA /api/states 找 3 个 <设备后缀>，映射到站点 1/2/3；
    - 读状态：8 个传感器 → 归一化 SiteState（kW / SOC∈[0,1] / device_time）；
    - 下指令：LP 首槽 → 3 实体协议（operating_mode → grid_flow → power_control）；
    - LP 预报：电价(3站共享,逐时) + 天气(按站,逐时) → 上采样到 15min×96 槽。

重要约束（SOP）：控制只能经 HA 下发；指令下一 tick(5s) 生效；×180 加速下 1 设备小时 = 4 tick。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import SimConfig
from .ha_client import HaClient, HaError
from .protocol import Command, command_for_lp
from .sim_api import SimApi, SimApiError

# LP 控制粒度：15min/槽（1 设备小时 = 4 tick，SOP §7.2 / QA）
SLOTS_PER_HOUR = 4
HOURS_PER_DAY = 24


@dataclass
class SiteState:
    """一个站点某一时刻的归一化状态（内部单位 kW / SOC∈[0,1]）。"""
    site_no: int
    soc: float                 # 0..1
    solar_power_kw: float
    home_load_kw: float
    charging_kw: float         # 校验/回读用
    discharging_kw: float
    grid_import_kw: float
    grid_export_kw: float
    device_time: str
    mode: str = "self_consumption"   # 当前 operating_mode（用于检测被环境退回）

    def e0_kwh(self, capacity_kwh: float) -> float:
        return self.soc * capacity_kwh


class SolarBank:
    def __init__(self, cfg: SimConfig):
        self.cfg = cfg
        self.ha = HaClient(cfg.ha_url, cfg.ha_token, cfg.timeout_s)
        self.sim = SimApi(cfg.sim_url, cfg.sim_user, cfg.sim_password, cfg.timeout_s)
        self.suffixes: dict[int, str] = {}       # site_no(1..3) -> 设备后缀
        self._mode_third_party: dict[int, bool] = {}   # 记录是否已切 third_party（避免重复写）

    # ---------------- 站点发现 ----------------
    def discover_sites(self) -> dict[int, str]:
        """从 HA 发现 3 个站点的 <设备后缀>，映射到 site 1/2/3。"""
        prefix = f"sensor.{self.cfg.device_prefix}_"
        suffixes = set()
        for st in self.ha.states():
            eid = st.get("entity_id", "")
            if eid.startswith(prefix) and eid.endswith("_soc"):
                suffixes.add(eid[len(prefix):-len("_soc")])
        suffixes = sorted(suffixes)

        if self.cfg.site_suffixes:
            # 显式映射优先（SOP §4.2：后缀→站点需按端口 1502/1503/1504 对应）
            self.suffixes = {int(k): v for k, v in self.cfg.site_suffixes.items()}
            missing = [v for v in self.suffixes.values() if v not in suffixes]
            if missing:
                raise HaError(f"配置的站点后缀未在 HA 中发现：{missing}；实际发现 {suffixes}")
        else:
            # 未配置则按字典序分配（⚠ 可能不对应真实站点，需在仪表盘核对）
            if len(suffixes) < 3:
                raise HaError(f"HA 中发现的后缀不足 3 个：{suffixes}（请先按 SOP §4 添加 3 台设备）")
            self.suffixes = {i + 1: s for i, s in enumerate(suffixes[:3])}
        return dict(self.suffixes)

    # ---------------- entity_id 构造 ----------------
    def eid(self, site_no: int, func: str, domain: str = "sensor") -> str:
        sfx = self.suffixes.get(int(site_no))
        if not sfx:
            raise HaError(f"未发现 site {site_no} 的后缀，先调 discover_sites()")
        return f"{domain}.{self.cfg.device_prefix}_{sfx}_{func}"

    # ---------------- 读状态 ----------------
    def read_state(self, site_no: int) -> SiteState:
        def _f(func):
            return self.ha.get_float(self.eid(site_no, func))
        return SiteState(
            site_no=int(site_no),
            soc=_f("soc") / 100.0,
            solar_power_kw=_f("solar_power") / 1000.0,
            home_load_kw=_f("home_load") / 1000.0,
            charging_kw=_f("battery_charging_power") / 1000.0,
            discharging_kw=_f("battery_discharging_power") / 1000.0,
            grid_import_kw=_f("grid_import_power") / 1000.0,
            grid_export_kw=_f("grid_export_power") / 1000.0,
            device_time=self.ha.get_state(self.eid(site_no, "device_time"))["state"],
        )

    def read_all_states(self) -> dict:
        """一次 GET /api/states 读全部站点状态（省 8×3 次 HTTP；HA 每次约 1s）。"""
        states = self.ha.states()
        by_eid = {s["entity_id"]: s for s in states}
        out = {}
        for n, sfx in self.suffixes.items():
            p = f"{self.cfg.device_prefix}_{sfx}"

            def f(func):
                try:
                    return float(by_eid[f"sensor.{p}_{func}"]["state"])
                except (KeyError, ValueError):
                    return 0.0

            mode_eid = f"select.{p}_operating_mode_device_runs_by_command_in_third_party_controlled"
            out[int(n)] = SiteState(
                site_no=int(n),
                soc=f("soc") / 100.0,
                solar_power_kw=f("solar_power") / 1000.0,
                home_load_kw=f("home_load") / 1000.0,
                charging_kw=f("battery_charging_power") / 1000.0,
                discharging_kw=f("battery_discharging_power") / 1000.0,
                grid_import_kw=f("grid_import_power") / 1000.0,
                grid_export_kw=f("grid_export_power") / 1000.0,
                device_time=by_eid.get(f"sensor.{p}_device_time", {}).get("state", ""),
                mode=by_eid.get(mode_eid, {}).get("state", "self_consumption"),
            )
        return out

    def read_device_time(self, site_no: int) -> datetime:
        raw = self.ha.get_state(self.eid(site_no, "device_time"))["state"]
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")

    # ---------------- 下发控制（3 实体协议） ----------------
    def ensure_third_party(self, site_no: int) -> None:
        """切到 third_party_control（只在未切过时发，避免重复写触发写保护）。"""
        if self._mode_third_party.get(int(site_no)):
            return
        self.ha.set_select(
            self.eid(site_no, "operating_mode_device_runs_by_command_in_third_party_controlled",
                     domain="select"),
            "third_party_control")
        self._mode_third_party[int(site_no)] = True
        time.sleep(1.0)   # SOP §6：切换后等 1s 再发下一条

    def apply_command(self, site_no: int, cmd: Command) -> None:
        """把一条 Command 下发给设备（operating_mode → grid_flow → power_control）。"""
        self.ensure_third_party(site_no)
        if cmd.is_standby:
            # 待机：按 cfg.standby_mode 处理
            if self.cfg.standby_mode == "self_consumption":
                self.ha.set_select(
                    self.eid(site_no,
                             "operating_mode_device_runs_by_command_in_third_party_controlled",
                             domain="select"), "self_consumption")
                self._mode_third_party[int(site_no)] = False
                return
            cmd = Command(grid_flow="charge", power_control_w=0.0)   # 保持第三方 + 充电 0 = 待机
        self.ha.set_select(self.eid(site_no, "grid_flow", domain="select"), cmd.grid_flow)
        self.ha.set_number(self.eid(site_no, "power_control", domain="number"),
                           cmd.power_control_w)

    # ---------------- LP 预报（电价/天气 → 逐时 24 点） ----------------
    def build_lp_forecast(self, site_no: int, horizon_hours: int = 24, now: datetime = None):
        """返回 (buy, sell, irr, cloud, temp, hours, dows)，每项 horizon_hours 点（逐时）。

        电价 3 站共享、天气按站，均逐时（24 点/天，SOP §5.2）。预测器在**逐时**粒度训练，
        这里只取逐时点；主循环负责上采样到 15min×96 槽（同一小时 4 槽共享同一逐时值）。
        now 由调用方从 read_all_states 传入，避免额外 device_time HTTP 调用。
        """
        if now is None:
            now = self.read_device_time(site_no)      # 兜底：设备世界当前时刻
        ptoday = self.sim.price_today()
        ptom = self.sim.price_tomorrow()
        price_pts = _price_points(ptoday) + _price_points(ptom)   # 48 逐时（今+明）

        wtoday = self.sim.weather_today(site_no)
        wtom = self.sim.weather_tomorrow(site_no)
        w_irr = _weather_points(wtoday, "solar_irradiance_wm2") + _weather_points(wtom, "solar_irradiance_wm2")
        w_cloud = _weather_points(wtoday, "cloud_cover_percent") + _weather_points(wtom, "cloud_cover_percent")
        w_temp = _weather_points(wtoday, "temperature_c") + _weather_points(wtom, "temperature_c")

        hi = now.hour                                   # 当前逐时序号 0..23

        # 零外推：电价/天气 API 只给今明两天（price_pts 48 点，索引 0..47），
        # 故有效时域 = 今天剩余(24-hi) + 明天(24) = 48-hi。超出部分不再生成
        # （不再 clamp 到最后一点外推「后天」），与 PV 预测器的 48-hi 口径一致。
        eff_h = min(horizon_hours, len(price_pts) - hi)
        buy, sell, irr, cloud, temp, hours_l, dows = [], [], [], [], [], [], []
        for k in range(eff_h):
            idx = hi + k                                  # k < 48-hi → idx < 48，不越界
            b, s = price_pts[idx]
            buy.append(b); sell.append(s)
            irr.append(w_irr[idx]); cloud.append(w_cloud[idx]); temp.append(w_temp[idx])
            hours_l.append((hi + k) % HOURS_PER_DAY)
            dows.append((now + timedelta(hours=k)).weekday())
        return buy, sell, irr, cloud, temp, hours_l, dows

    def build_day_weather(self, site_no: int):
        """返回 (today_pts, tomorrow_pts)：今/明两个自然日的 24 点预报（points 列表）。

        区别于 build_lp_forecast 的「滚动 24h 切片」，本方法按自然日对齐返回完整 24 点，
        供 LSTM 预测器（自然日多对多）使用。
        """
        wtoday = self.sim.weather_today(site_no)
        wtom = self.sim.weather_tomorrow(site_no)
        return _weather_points_list(wtoday), _weather_points_list(wtom)

    # ---------------- 登录（预热时可选） ----------------
    def login_sim(self) -> str:
        return self.sim.login()


# ---------------------------------------------------------------------------
# 容错解析：官方 API 的精确 JSON 结构需在首次自检（SOP §7.3）时核对，这里同时兼容
# 「points 数组」与「扁平数组」两种形态。
# ---------------------------------------------------------------------------
def _price_points(data: dict) -> list:
    """返回 [(import_price, export_price) × 24]（€/kWh）。"""
    if "points" in data and isinstance(data["points"], list):
        return [(float(p["import_price"]), float(p["export_price"])) for p in data["points"]]
    if "import_price" in data and isinstance(data["import_price"], list):
        return [(float(a), float(b)) for a, b in zip(data["import_price"], data["export_price"])]
    raise SimApiError(f"无法解析电价响应：{list(data.keys())}")


def _weather_points(data: dict, key: str) -> list:
    """返回某天气字段的 24 点浮点列表。"""
    if "points" in data and isinstance(data["points"], list):
        return [float(p[key]) for p in data["points"]]
    if isinstance(data.get(key), list):
        return [float(x) for x in data[key]]
    raise SimApiError(f"无法解析天气字段 {key}：{list(data.keys())}")


def _weather_points_list(data: dict) -> list:
    """返回天气响应的 points 列表（24 个 dict）。兼容 points / 扁平两种形态。"""
    if "points" in data and isinstance(data["points"], list):
        return data["points"]
    keys = [k for k in data if isinstance(data.get(k), list)]
    if keys:
        n = len(data[keys[0]])
        return [{k: data[k][i] for k in keys} for i in range(n)]
    raise SimApiError(f"无法解析天气响应：{list(data.keys())}")


def upsample_hourly(arr, slots_per_hour: int = SLOTS_PER_HOUR) -> list:
    """逐时数组（24 点）→ 15min 槽数组（96 点）：同一小时内每槽共享同一逐时值。"""
    return [arr[k // slots_per_hour] for k in range(len(arr) * slots_per_hour)]
