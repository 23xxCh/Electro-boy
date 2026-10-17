"""Read-only check of the copied simulator's three HA device identities."""

from __future__ import annotations

import sys
from pathlib import Path

SIM_VE = Path(__file__).resolve().parents[1] / "ha_lovelace_preview" / "sim_src" / "VE"
sys.path.insert(0, str(SIM_VE))

from ve.config import DEVICE_PN, SimConfig  # noqa: E402
from ve.register_map import decode_value  # noqa: E402
from ve.runtime import Runtime  # noqa: E402

from modbus_502_proxy import TARGETS  # noqa: E402


def main() -> None:
    runtime = Runtime(SimConfig())  # Construct only; do not open sockets or start time.
    serials = []
    for index, (site, regmap) in enumerate(runtime.regmaps.items(), start=1):
        sn = decode_value(regmap.read("input", 10100, 12), "STRING")
        pn = decode_value(regmap.read("input", 32768, 5), "STRING")
        assert site == f"site_{index}"
        assert len(sn) == 17 and sn[3:7] == "DMWH", (site, sn)
        assert pn == DEVICE_PN, (site, pn)
        assert TARGETS[f"127.0.0.{index + 1}"] == ("127.0.0.1", 1501 + index)
        serials.append(sn)
    assert len(set(serials)) == 3, serials
    print("3 distinct Solarbank Max AC identities and loopback mappings: OK")


if __name__ == "__main__":
    main()
