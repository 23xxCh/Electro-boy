"""HA command_line helper; exports official entity readings, never controls."""
import json
import math
import os
from pathlib import Path
import sys
import time


def validate(raw):
    if set(raw) != {"sim_slot", "site_1", "site_2", "site_3"} or not isinstance(raw["sim_slot"], int):
        raise ValueError("invalid_readback_sites_or_slot")
    for site in ("site_1", "site_2", "site_3"):
        row = raw[site]
        if set(row) != {"power_w", "direction", "mode", "battery_power_w", "soc_pct", "reported_at"}:
            raise ValueError("invalid_readback_fields")
        for key in ("power_w", "battery_power_w", "soc_pct", "reported_at"):
            if isinstance(row[key], bool) or not isinstance(row[key], (int, float)) or not math.isfinite(row[key]):
                raise ValueError("invalid_readback_numeric")
        if not 0 <= row["power_w"] <= 2000 or not 0 <= row["soc_pct"] <= 100 or row["reported_at"] <= 0:
            raise ValueError("readback_out_of_range")
        if row["direction"] not in ("charge", "discharge", "unknown", "unavailable") or row["mode"] != "third_party_control":
            raise ValueError("invalid_readback_mode")
    return {"captured_at": time.time(), **raw}


if __name__ == "__main__":
    data = validate(json.loads(sys.argv[1]))
    output = Path("/config/sim_src/runtime_ha_readback.json")
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
    os.replace(temporary, output)
    print(data["sim_slot"])
