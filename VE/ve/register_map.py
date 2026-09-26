# -*- coding: utf-8 -*-
"""register_map.py —— Anker SOLIX Solarbank Max AC 寄存器表（移植自官方插件）。

把官方插件 config/8fcbb87c….yaml（Solarbank Max AC）的寄存器地址/数据类型/增益
与 modbus_client.py 的**大端编解码语义**移植到这里，作为 VE 的 Modbus 设备面。

数据类型与官方插件完全一致：
  UINT16 / INT16 / INT32(有符号, 大端高字在前) / UINT32 / STRING(每寄存器 2 字节,
  高字节在前) / VERSION；gain != 1 时寄存器原始值 = 物理值 × gain（读取时除以 gain）。

写保护：charging_limit_soc / discharge_limit_soc / backup_reserve_soc 被比赛规则
锁定为 100/0/0，写入返回 Modbus 异常码 3（Illegal Data Value）。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

# entity_key -> (address, reg_type, data_type, count, gain)
# reg_type: "input"(FC04 只读) / "holding"(FC03/06/16 读写)
ENTITY_REGISTERS: Dict[str, dict] = {
    # ---- 只读（input, FC04）----
    "battery_status":          dict(addr=10001, reg="input", dtype="UINT16", count=1),
    "pv_power":                dict(addr=10002, reg="input", dtype="INT32",  count=2),
    "third_party_pv_power":    dict(addr=10004, reg="input", dtype="INT32",  count=2),
    "battery_power":           dict(addr=10008, reg="input", dtype="INT32",  count=2),
    "load_power":              dict(addr=10010, reg="input", dtype="INT32",  count=2),
    "grid_power":              dict(addr=10012, reg="input", dtype="INT32",  count=2),
    "battery_soc":             dict(addr=10014, reg="input", dtype="UINT16", count=1),
    "pv_total_generation":     dict(addr=10018, reg="input", dtype="UINT32", count=2, gain=10),
    "max_charge_power":        dict(addr=10036, reg="input", dtype="INT32",  count=2),
    "max_discharge_power":     dict(addr=10038, reg="input", dtype="INT32",  count=2),
    "device_sn":               dict(addr=10100, reg="input", dtype="STRING", count=12),
    "device_sw_version":       dict(addr=10112, reg="input", dtype="STRING", count=6),
    "ac_grid_output_power":    dict(addr=10208, reg="input", dtype="INT32",  count=2),
    "rated_energy":            dict(addr=10250, reg="input", dtype="UINT32", count=2, gain=10),
    "cumulative_charge_energy":    dict(addr=10262, reg="input", dtype="UINT32", count=2, gain=10),
    "cumulative_discharge_energy": dict(addr=10264, reg="input", dtype="UINT32", count=2, gain=10),
    "device_model":            dict(addr=32768, reg="input", dtype="STRING", count=5),
    "ems_mode_mask":           dict(addr=32774, reg="input", dtype="UINT16", count=1),
    "parallel_capability_mask": dict(addr=32775, reg="input", dtype="UINT16", count=1),
    # ---- 读写（holding, FC03/06/16）----
    "operating_mode":          dict(addr=10064, reg="holding", dtype="UINT16", count=1),
    "battery_power_setpoint":  dict(addr=10071, reg="holding", dtype="INT32",  count=2),
    "charging_limit_soc":      dict(addr=60000, reg="holding", dtype="UINT16", count=1, locked=100),
    "discharge_limit_soc":     dict(addr=60001, reg="holding", dtype="UINT16", count=1, locked=0),
    "backup_reserve_soc":      dict(addr=60002, reg="holding", dtype="UINT16", count=1, locked=0),
    "backup_soc_enable":       dict(addr=60003, reg="holding", dtype="UINT16", count=1),
}

# 官方插件 batch_read_ranges（读区间；区间内未映射地址返回 0）
BATCH_INPUT_RANGES = [(10000, 10050), (10090, 10156), (10208, 10265), (32768, 32774)]
BATCH_HOLDING_RANGES = [(10060, 10072), (10074, 10081), (60000, 60003)]

# 锁定 SOC 限值（写这些地址返回异常码 3）
LOCKED_ADDRS = {60000, 60001, 60002}


def encode_value(value: Any, dtype: str, count: int = 1) -> List[int]:
    """把物理值编码为寄存器字列表（大端，与插件 write 路径一致）。"""
    if dtype == "UINT16":
        return [int(value) & 0xFFFF]
    if dtype == "INT16":
        raw = int(value) & 0xFFFF
        return [raw if raw < 0x8000 else raw - 0x10000]
    if dtype == "INT32":
        v = int(value)
        if v < 0:
            v += 0x100000000
        return [(v >> 16) & 0xFFFF, v & 0xFFFF]
    if dtype == "UINT32":
        v = int(value) & 0xFFFFFFFF
        return [(v >> 16) & 0xFFFF, v & 0xFFFF]
    if dtype == "STRING":
        b = str(value).encode("utf-8")
        b = b.ljust(count * 2, b"\x00")[:count * 2]
        return [(b[i] << 8) | b[i + 1] for i in range(0, len(b), 2)]
    if dtype == "VERSION":
        parts = str(value).lstrip("vV").split(".")
        parts = (parts + ["0"] * 4)[:4]
        return [int(parts[0]) << 8 | int(parts[1]), int(parts[2]) << 8 | int(parts[3])]
    return [int(value) & 0xFFFF]


def decode_value(words: List[int], dtype: str) -> Any:
    """把寄存器字列表解码为物理值（大端，与插件读路径一致）。"""
    if dtype == "UINT16":
        return words[0] & 0xFFFF
    if dtype == "INT16":
        raw = words[0] & 0xFFFF
        return raw if raw < 0x8000 else raw - 0x10000
    if dtype == "INT32":
        high, low = words[0] & 0xFFFF, words[1] & 0xFFFF
        u = (high << 16) | low
        return u - 0x100000000 if u & 0x80000000 else u
    if dtype == "UINT32":
        return ((words[0] & 0xFFFF) << 16) | (words[1] & 0xFFFF)
    if dtype == "STRING":
        bs = bytearray()
        for w in words:
            bs.append((w >> 8) & 0xFF)
            bs.append(w & 0xFF)
        return bytes(bs).decode("utf-8", errors="ignore").rstrip("\x00")
    if dtype == "VERSION":
        bs = []
        for w in words[:2]:
            bs.append((w >> 8) & 0xFF)
            bs.append(w & 0xFF)
        return ".".join(str(x) for x in bs[:4])
    return words[0]


class DeviceRegisterMap:
    """一台虚拟设备的寄存器面：把站点实时状态映射到输入/保持寄存器字表。"""

    def __init__(self, site_id: str, pn: str, sn: str, sw_version: str):
        self.site_id = site_id
        self.pn = pn
        self.sn = sn
        self.sw_version = sw_version
        self.lock = threading.RLock()
        self._input_words: Dict[int, int] = {}
        self._holding_words: Dict[int, int] = {}
        # 反向表：地址 -> (entity_key, spec)
        self._addr_map: Dict[int, tuple] = {}
        for key, spec in ENTITY_REGISTERS.items():
            self._addr_map[spec["addr"]] = (key, spec)
        # 写回调：entity_key -> 解码后的物理值（由引擎注入，把 HA 写入转发给引擎）
        self.on_write = None
        self._init_static()

    def _init_static(self) -> None:
        """静态值：PN/SN/固件版本/能力掩码/SOC 锁定限值。"""
        self._put_static("device_model", self.pn)
        self._put_static("device_sn", self.sn)
        self._put_static("device_sw_version", self.sw_version)
        # ems_mode_mask(0x8006)：BIT0..BIT6 全部置位 -> 所有模式可见（含 third_party_control BIT5）
        self._put_static("ems_mode_mask", 0x7F)
        # parallel_capability_mask(0x8007)：BIT0/1/2/3 -> SOC 限值与备电可见
        self._put_static("parallel_capability_mask", 0x0F)
        # SOC 锁定限值（写入被拒）
        self._put_static("charging_limit_soc", 100)
        self._put_static("discharge_limit_soc", 0)
        self._put_static("backup_reserve_soc", 0)
        self._put_static("backup_soc_enable", 0)

    def _put_static(self, key: str, value: Any) -> None:
        spec = ENTITY_REGISTERS[key]
        words = encode_value(value, spec["dtype"], spec.get("count", 1))
        table = self._input_words if spec["reg"] == "input" else self._holding_words
        for i, w in enumerate(words):
            table[spec["addr"] + i] = w & 0xFFFF

    def _apply_gain(self, value: float, spec: dict) -> int:
        gain = spec.get("gain", 1)
        return int(round(float(value) * gain))

    def refresh(self, report: dict, site_params) -> None:
        """用站点上一时隙执行结果刷新动态寄存器。report = Site.last。"""
        with self.lock:
            self._set("battery_soc", round(report.get("soc_pct", 0.0)))
            self._set("pv_power", round(report.get("solar_power_w", 0.0)))
            self._set("third_party_pv_power", 0)
            self._set("load_power", round(report.get("home_load_w", 0.0)))
            self._set("battery_power", round(report.get("battery_power_w", 0.0)))
            # grid_power = import - export（有符号）
            self._set("grid_power", round(report.get("grid_import_w", 0.0)
                                          - report.get("grid_export_w", 0.0)))
            self._set("ac_grid_output_power", round(report.get("grid_export_w", 0.0)))
            self._set("max_charge_power", round(site_params.p_charge_max_kw * 1000.0))
            self._set("max_discharge_power", round(site_params.p_discharge_max_kw * 1000.0))
            self._set("rated_energy", site_params.capacity_kwh)
            self._set("pv_total_generation", report.get("pv_total_kwh", 0.0))
            self._set("cumulative_charge_energy", report.get("cum_charge_kwh", 0.0))
            self._set("cumulative_discharge_energy", report.get("cum_discharge_kwh", 0.0))
            # 电池状态：充/放/待机
            bp = report.get("battery_power_w", 0.0)
            status = 2 if bp > 0 else (1 if bp < 0 else 0)
            self._set("battery_status", status)
            # 控制寄存器（保持上次写入值）
            self._set("operating_mode", report.get("operating_mode", 3))
            self._set("battery_power_setpoint", report.get("power_setpoint_w", 0))

    def _set(self, key: str, value: float) -> None:
        spec = ENTITY_REGISTERS[key]
        raw = self._apply_gain(value, spec)
        words = encode_value(raw, spec["dtype"], spec.get("count", 1))
        table = self._input_words if spec["reg"] == "input" else self._holding_words
        for i, w in enumerate(words):
            table[spec["addr"] + i] = w & 0xFFFF

    # ---------------- 读 ----------------
    def read(self, reg_type: str, addr: int, count: int) -> List[int]:
        with self.lock:
            table = self._input_words if reg_type == "input" else self._holding_words
            return [table.get(addr + i, 0) for i in range(count)]

    # ---------------- 写 ----------------
    def write(self, addr: int, values: List[int]) -> int:
        """写寄存器，返回 0=成功 或 Modbus 异常码。"""
        with self.lock:
            if addr in LOCKED_ADDRS:
                return 3                       # Illegal Data Value（比赛规则：SOC 限值锁定）
            entry = self._addr_map.get(addr)
            if entry is None or entry[1]["reg"] != "holding":
                return 2                       # Illegal Data Address
            key, spec = entry
            n = spec.get("count", 1)
            if len(values) < n:
                return 3
            # 仅把该实体的地址范围写回字表
            for i in range(n):
                self._holding_words[addr + i] = values[i] & 0xFFFF
            if self.on_write is not None:
                try:
                    raw = decode_value(values[:n], spec["dtype"])
                    gain = spec.get("gain", 1)
                    phys = raw / gain if gain not in (None, 1) else raw
                    self.on_write(key, phys)
                except Exception:
                    pass
            return 0
