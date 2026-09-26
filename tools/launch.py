# -*- coding: utf-8 -*-
"""launch.py —— 一键启动 VE + Controller_LP 两个节点（源码版/exe 版自适应）。

用法（在 env_ankerproject 中）：
    python launch.py                     # 同时启动 VE + Controller_LP
    python launch.py --no-controller     # 只启动 VE
    python launch.py --ve-args "--days 7 --seed 123"   # 给 VE 传参

启动入口自适应：
  - VE          优先用 VE/run.py（源码），否则用 VE/VE_Anker.exe（打包 exe）；
  - Controller  优先用 Controller_LP/node.py（源码），否则用 Controller_LP/Controller_LP.exe。
两节点为独立进程；Ctrl+C 停止全部节点。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
VE_DIR = os.path.join(WS, "VE")
CTRL_DIR = os.path.join(WS, "Controller_LP")


def resolve_cmd(target_dir: str, script: str, exe: str, args: list) -> list | None:
    """返回启动命令列表：优先源码脚本，否则用打包好的 exe。"""
    script_path = os.path.join(target_dir, script)
    exe_path = os.path.join(target_dir, exe)
    if os.path.isfile(script_path):
        return [sys.executable, script_path] + args
    if os.path.isfile(exe_path):
        return [exe_path] + args
    return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="一键启动 VE + Controller 节点")
    ap.add_argument("--no-controller", action="store_true", help="只启动 VE")
    ap.add_argument("--controller-name", default="Controller_LP", help="调度器名")
    ap.add_argument("--ve-args", default="", help="传给 VE 的额外参数")
    args = ap.parse_args()

    procs = []

    ve_cmd = resolve_cmd(VE_DIR, "run.py", "VE_Anker.exe", args.ve_args.split())
    if ve_cmd is None:
        print(f"[错误] 找不到 VE 启动入口（run.py 或 VE_Anker.exe）：{VE_DIR}")
        return 1
    p_ve = subprocess.Popen(ve_cmd, cwd=VE_DIR)
    procs.append(("VE", p_ve))
    print(f"[launch] VE 节点已启动（PID {p_ve.pid}，入口={os.path.basename(ve_cmd[0])}）")

    if not args.no_controller:
        ctrl_cmd = resolve_cmd(CTRL_DIR, "node.py", "Controller_LP.exe",
                               ["--name", args.controller_name])
        if ctrl_cmd is None:
            print(f"[错误] 找不到 Controller 启动入口（node.py 或 Controller_LP.exe）：{CTRL_DIR}")
            for name, p in procs:
                p.terminate()
            return 1
        time.sleep(1.5)   # 等 VE 起好端口
        p_ctrl = subprocess.Popen(ctrl_cmd, cwd=CTRL_DIR)
        procs.append((args.controller_name, p_ctrl))
        print(f"[launch] {args.controller_name} 节点已启动（PID {p_ctrl.pid}，"
              f"入口={os.path.basename(ctrl_cmd[0])}）")

    print("[launch] 全部就绪。VE 看板 http://127.0.0.1:45678/ · Ctrl+C 停止全部。")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for name, p in procs:
            if p.poll() is None:
                p.terminate()
        time.sleep(1)
        for name, p in procs:
            if p.poll() is None:
                p.kill()
        print("[launch] 已停止全部节点。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
