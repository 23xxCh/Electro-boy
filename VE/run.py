# -*- coding: utf-8 -*-
"""run.py —— VE 一键启动（无 UI，后台服务：引擎 + Modbus + HTTP API + HA REST 桥）。

开箱即用（仅需 numpy，无第三方依赖）：
    conda activate env_ankerproject
    cd VE
    python run.py [--days 3..7] [--speed 180] [--granularity 15] [--seed 123]

启动后：
  - 引擎按 180 倍速推进时隙（15min/时隙，可调 1-60min）；
  - 3 台虚拟 Solarbank Max AC 设备在 Modbus TCP 1502/1503/1504 上（供官方 HA 插件接入）；
  - 数据/控制 HTTP API 在 :45678（含内置 HTML 看板，浏览器打开 http://127.0.0.1:45678/）；
  - 模拟 HA REST 桥在 :8123（/api/states、/api/services，供自制看板 / LP 控制器）。

用 Ctrl+C 停止。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ve.config import SimConfig
from ve.runtime import Runtime


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="VE — Anker 虚拟家庭环境模拟器")
    ap.add_argument("--days", type=int, default=None, help="模拟天数 3-7（默认 None = 无限）")
    ap.add_argument("--speed", type=float, default=720.0, help="模拟倍速（默认 180）")
    ap.add_argument("--granularity", type=int, default=15, help="时隙粒度 min，1-60（默认 15）")
    ap.add_argument("--seed", type=int, default=123, help="随机种子")
    ap.add_argument("--host", type=str, default="127.0.0.1", help="绑定地址")
    ap.add_argument("--no-modbus", action="store_true", help="不启动 Modbus 设备服务器")
    ap.add_argument("--no-http", action="store_true", help="不启动 HTTP API/看板")
    ap.add_argument("--no-ha-rest", action="store_true", help="不启动模拟 HA REST 桥")
    args = ap.parse_args()

    cfg = SimConfig(duration_days=args.days, speed=args.speed,
                    granularity_min=args.granularity, seed=args.seed, host=args.host)
    if args.no_modbus:
        cfg.enable_modbus = False
    if args.no_http:
        cfg.enable_http_api = False
    if args.no_ha_rest:
        cfg.enable_ha_rest = False

    rt = Runtime(cfg)
    rt.start()
    print(rt.summary())
    print("Ctrl+C 停止。")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        rt.stop()
        print("\nVE 已停止。")


if __name__ == "__main__":
    main()
