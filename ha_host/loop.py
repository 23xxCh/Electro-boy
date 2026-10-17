"""定时循环：读 HA + 天气 → 占位求解 → 可选下发。崩溃会错过充放窗口，所以异常只记日志、不退出。"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

from ha_client import HAClient, HAError
from optimizer import plan
from sim_client import SimClient, SimError

HERE = Path(__file__).resolve().parent


def load_config() -> dict:
    path = HERE / "config.json"
    if not path.exists():
        print("先复制 config.example.json 为 config.json", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def tick(cfg: dict, ha: HAClient, sim: SimClient) -> None:
    dry = bool(cfg.get("dry_run", True))
    for site in cfg["sites"]:
        snapshot = {}
        for key in ("soc", "pv", "load"):
            entity_id = site[key]
            try:
                state = ha.get_state(entity_id)
                snapshot[key] = state.get("state")
            except HAError as exc:
                snapshot[key] = f"ERR:{exc}"
        try:
            weather = sim.weather(cfg.get("weather_today", ""), int(site["id"]))
        except SimError as exc:
            weather = f"ERR:{exc}"
        price = None
        if cfg.get("price_path"):
            try:
                price = sim.get_json(cfg["price_path"], {"site": site["id"]})
            except SimError as exc:
                price = f"ERR:{exc}"
        decision = plan(site, weather, price)
        print(
            json.dumps(
                {"site": site["id"], "snapshot": snapshot, "decision": decision, "dry_run": dry},
                ensure_ascii=False,
            )
        )
        try:
            print(ha.set_select(site["mode"], decision["mode"], dry_run=dry))
            print(ha.set_number(site["setpoint"], float(decision["setpoint_w"]), dry_run=dry))
        except HAError as exc:
            print(f"site {site['id']} 下发失败: {exc}", file=sys.stderr)


def main() -> None:
    cfg = load_config()
    ha = HAClient(cfg["ha_base"], cfg.get("ha_token", ""))
    sim = SimClient(cfg.get("sim_base", ""))
    interval = max(5, int(cfg.get("interval_sec", 60)))
    print(f"ha_host 启动 dry_run={cfg.get('dry_run', True)} interval={interval}s sites={len(cfg['sites'])}")
    while True:
        try:
            tick(cfg, ha, sim)
        except Exception:
            traceback.print_exc()
        time.sleep(interval)


if __name__ == "__main__":
    main()
