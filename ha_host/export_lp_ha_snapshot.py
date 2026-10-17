"""Validate one HA-native telemetry JSON argument and atomically export it.

Only official-plugin state values and the VE bridge slot are accepted. The
output is for a local optimizer to read; this helper never sends controls.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path


OUTPUT = Path("/config/sim_src/lp_ha_snapshot.json")
SITE_KEYS = frozenset(("soc_pct", "solar_power_w", "home_load_w", "home_load_reported_at",
                       "capacity_kwh", "max_charge_w", "max_discharge_w"))
RANGES = {
    "soc_pct": (0, 100),
    "solar_power_w": (0, 20_000),
    "home_load_w": (0, 20_000),
    "home_load_reported_at": (1, 4_102_444_800),
    "capacity_kwh": (0.1, 100),
    "max_charge_w": (1, 20_000),
    "max_discharge_w": (1, 20_000),
}


def validate(raw: dict, captured_at: float) -> dict:
    if not math.isfinite(captured_at) or captured_at <= 0:
        raise ValueError("invalid capture time")
    if not isinstance(raw, dict) or set(raw) != {"sim_slot", "site_1", "site_2", "site_3"}:
        raise ValueError("expected one slot and exactly three sites")
    slot = raw["sim_slot"]
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot <= 10_000_000:
        raise ValueError("sim_slot must be a nonnegative integer")
    result = {"sim_slot": slot, "captured_at": captured_at}
    for site in ("site_1", "site_2", "site_3"):
        fields = raw[site]
        if not isinstance(fields, dict) or set(fields) != SITE_KEYS:
            raise ValueError(f"{site}: missing or unexpected HA field")
        checked = {}
        for key, (lower, upper) in RANGES.items():
            value = fields[key]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not lower <= value <= upper):
                raise ValueError(f"{site}.{key}: unavailable, nonfinite, or out of range")
            checked[key] = float(value)
        if checked["home_load_reported_at"] > captured_at + 5:
            raise ValueError(f"{site}.home_load_reported_at: future HA report time")
        result[site] = checked
    return result


def write_atomic(data: dict, output: Path = OUTPUT) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent,
                                         prefix=".lp_ha_snapshot-", suffix=".tmp",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: export_lp_ha_snapshot.py '<numeric JSON>'")
    try:
        def reject_constant(value):
            raise ValueError(f"nonfinite JSON constant: {value}")

        raw = json.loads(sys.argv[1], parse_constant=reject_constant)
        data = validate(raw, time.time())
        write_atomic(data)
    except (ValueError, OSError, TypeError) as error:
        raise SystemExit(f"HA telemetry snapshot rejected: {error}") from error
    print(data["sim_slot"])


if __name__ == "__main__":
    main()
