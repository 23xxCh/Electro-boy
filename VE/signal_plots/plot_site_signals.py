# -*- coding: utf-8 -*-
"""plot_site_signals.py —— 画出每个站点 4 台设备的工作功率/使能/总功率信号。

输出 3 张 SVG（每站点一张），每张 3 个子图：
  子图1：4 台设备的工作功率信号 P_work(t)（4 条曲线）
  子图2：4 台设备的工作使能信号 R_E(t)（4 条曲线）
  子图3：4 台设备的实际功率 P_App(t) + 站点总功率 ΣP_App（5 条曲线）

曲线均为**无噪声**模板信号（运行时每时隙另叠加小高斯白噪声）。

用法：
    python plot_site_signals.py [--seed 123] [--out-dir signal_plots]
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ve.config import SimConfig
from ve.engine import VirtualEnvEngine
from ve.signals import work_signal, enable_signal

matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "PingFang SC"]
matplotlib.rcParams["axes.unicode_minus"] = False

COLORS = ["tab:blue", "tab:orange", "tab:green", "tab:red"]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=123)
    ap.add_argument("--out-dir", type=str,
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "signal_plots"))
    args = ap.parse_args()

    cfg = SimConfig(duration_days=None, granularity_min=15, seed=args.seed)
    eng = VirtualEnvEngine(cfg)
    slots_per_day = cfg.slots_per_day
    dt_h = cfg.dt_h
    t = np.arange(slots_per_day, dtype=float) * dt_h     # 0 .. 23.75 h

    os.makedirs(args.out_dir, exist_ok=True)
    print(f"每站点设备数 = {len(next(iter(eng.sites.values())).appliances)}（应为 4）")

    for sid, site in eng.sites.items():
        fig, axes = plt.subplots(3, 1, figsize=(12, 11), sharex=True)
        fig.suptitle(f"{sid} · 4 台设备功率信号（工作功率 / 工作使能 / 叠加总功率）", fontsize=13)

        p_apps = []
        for i, app in enumerate(site.appliances):
            tpl = app.template.template
            params = app.template.params
            name = app.name
            pw = work_signal(tpl, params, t)
            re_ = enable_signal(tpl, params, t)
            papp = app.template.base_signal(slots_per_day, dt_h)   # 含 TC 斜坡，无噪声
            p_apps.append(papp)

            axes[0].plot(t, pw, lw=1.2, color=COLORS[i], label=f"设备{i + 1} · {name}")
            axes[1].step(t, re_, lw=1.2, color=COLORS[i], where="post",
                         label=f"设备{i + 1} · {name}")

        total = np.sum(p_apps, axis=0)
        for i, papp in enumerate(p_apps):
            axes[2].plot(t, papp, lw=1.0, color=COLORS[i], alpha=0.75,
                         label=f"设备{i + 1} P_App")
        axes[2].plot(t, total, lw=2.2, color="black", label="站点总功率 ΣP_App")

        axes[0].set_ylabel("工作功率 P_work (kW)")
        axes[1].set_ylabel("工作使能 R_E (0/1)")
        axes[1].set_ylim(-0.05, 1.15)
        axes[2].set_ylabel("功率 (kW)")
        axes[2].set_xlabel("时间 (小时)")
        axes[0].axhline(0, color="gray", lw=0.6)
        axes[2].axhline(0, color="gray", lw=0.6)
        for ax in axes:
            ax.grid(alpha=0.3)
            ax.legend(loc="upper right", fontsize=8, ncol=2)

        out = os.path.join(args.out_dir, f"{sid}_signals.svg")
        fig.tight_layout(rect=[0, 0, 1, 0.97])
        fig.savefig(out, format="svg")
        plt.close(fig)
        print(f"written: {out}")


if __name__ == "__main__":
    main()
