# -*- coding: utf-8 -*-
"""plot_report.py —— 绘制最近一次运行的决策/收益曲线（读取 XML 报告）。

默认绘制 report/ 下**最新一次运行**（最大 run 序号）的 report；输出两幅 SVG 到
<run>/plot/ 目录：

  decision_curve.svg —— 决策曲线总图（4 子图）：
    1. 买入/卖出电功率对比：光伏卖电网、电池卖电网、电网买给负载、电网买给电池
    2. 电价曲线（买价/卖价）
    3. 电池 SOC
    4. 电池充放电功率对比
  gain_curve.svg      —— 收益曲线（3 子图）：
    1. 累计总收益 R 与 结算后收益 R_settled 对比
    2. 累计买电支出 与 累计卖电收入 对比
    3. 本站结算节省率

用法：
    python plot_report.py                 # 最新一次运行，site_1
    python plot_report.py --run 1         # 指定第 1 次运行
    python plot_report.py --site site_2   # 指定站点
"""
from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = HERE   # plot_report.py 就在 report/ 下，run 目录（1/2/...）与其同级

matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "PingFang SC"]
matplotlib.rcParams["axes.unicode_minus"] = False


def latest_run() -> int:
    idx = 0
    for name in os.listdir(REPORT_DIR):
        if name.isdigit():
            idx = max(idx, int(name))
    return idx


def load_site(report_dir: str, site: str) -> list:
    """读取一个站点的全部 slot_*.xml，按 slot 排序，返回记录列表。"""
    d = os.path.join(report_dir, site)
    records = []
    if not os.path.isdir(d):
        return records
    for fname in sorted(os.listdir(d)):
        if not fname.endswith(".xml"):
            continue
        root = ET.parse(os.path.join(d, fname)).getroot()
        def g(tag, default="0"):
            node = root.find(tag)
            return float(node.text) if node is not None else float(default)
        records.append({
            "slot": int(g("slot")),
            "soc_pct": g("state/soc_pct"),
            "solar_to_grid": g("energy_flow/solar_to_grid"),
            "battery_to_grid": g("energy_flow/battery_to_grid"),
            "grid_to_load": g("energy_flow/grid_to_load"),
            "grid_to_battery": g("energy_flow/grid_to_battery"),
            "charge_kw": g("action/charge_kw"),
            "discharge_kw": g("action/discharge_kw"),
            "buy": g("prices/buy_now_eur_kwh"),
            "sell": g("prices/sell_now_eur_kwh"),
            "r": g("revenue/total_revenue_r_eur"),
            "r_settled": g("revenue/settled_revenue_eur"),
            "buy_cost": g("revenue/buy_cost_eur"),
            "sell_rev": g("revenue/sell_revenue_eur"),
            "saving_rate": g("revenue/saving_rate_pct"),
            "baseline_cost": g("revenue/baseline_cost_eur"),
            "baseline_settled": g("revenue/baseline_settled_revenue_eur"),
        })
    return sorted(records, key=lambda r: r["slot"])


def plot_decision(records: list, svg_path: str, site: str) -> None:
    n = len(records)
    x = np.arange(n)
    fig, axes = plt.subplots(4, 1, figsize=(12, 12), sharex=True)
    fig.suptitle(f"{site} 决策曲线（{n} 个时隙）", fontsize=13)

    # 1) 买入/卖出功率（细化到能流边）
    axes[0].plot(x, [r["solar_to_grid"] for r in records], lw=0.9, label="光伏→电网")
    axes[0].plot(x, [r["battery_to_grid"] for r in records], lw=0.9, label="电池→电网")
    axes[0].plot(x, [r["grid_to_load"] for r in records], lw=0.9, label="电网→负载")
    axes[0].plot(x, [r["grid_to_battery"] for r in records], lw=0.9, label="电网→电池")
    axes[0].set_ylabel("功率 W")
    axes[0].legend(fontsize=8, ncol=2)
    axes[0].grid(alpha=0.3)

    # 2) 电价
    axes[1].plot(x, [r["buy"]*1000 for r in records], lw=0.9, label="买价")
    axes[1].plot(x, [r["sell"]*1000 for r in records], lw=0.9, label="卖价")
    axes[1].axhline(0, color="gray", lw=0.5)
    axes[1].set_ylabel("电价 €/MWh")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.3)

    # 3) SOC
    axes[2].plot(x, [r["soc_pct"] for r in records], lw=0.9, color="tab:purple")
    axes[2].set_ylabel("SOC %")
    axes[2].set_ylim(0, 105)
    axes[2].grid(alpha=0.3)

    # 4) 充放电功率
    axes[3].fill_between(x, [r["charge_kw"]*1000 for r in records], 0, color="tab:blue",
                         alpha=0.5, label="充电")
    axes[3].fill_between(x, [r["discharge_kw"]*1000 for r in records], 0, color="tab:orange",
                         alpha=0.5, label="放电")
    axes[3].axhline(0, color="gray", lw=0.5)
    axes[3].set_ylabel("电池功率 W")
    axes[3].set_xlabel("时隙")
    axes[3].legend(fontsize=8)
    axes[3].grid(alpha=0.3)

    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(svg_path, format="svg")
    plt.close(fig)


def plot_gain(records: list, svg_path: str, site: str) -> None:
    n = len(records)
    x = np.arange(n)
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    fig.suptitle(f"{site} 收益曲线（{n} 个时隙）", fontsize=13)

    # 1) 累计总收益 R / 结算后收益 R_settled / 基线调度器 R_settled（对比）
    axes[0].plot(x, [r["r"] for r in records], lw=1.2, label="累计总收益 R")
    axes[0].plot(x, [r["r_settled"] for r in records], lw=1.2, label="结算后收益 R_settled")
    axes[0].plot(x, [r["baseline_settled"] for r in records], lw=1.2, ls="--",
                 label="基线调度器 R_settled")
    axes[0].set_ylabel("€")
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.3)

    # 2) 累计买电支出 与 累计卖电收入
    axes[1].plot(x, [r["buy_cost"] for r in records], lw=1.2, label="累计买电支出")
    axes[1].plot(x, [r["sell_rev"] for r in records], lw=1.2, label="累计卖电收入")
    axes[1].set_ylabel("€")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.3)

    # 3) 本站结算节省率
    axes[2].plot(x, [r["saving_rate"] for r in records], lw=1.2, color="tab:green")
    axes[2].set_ylabel("节省率 %")
    axes[2].set_xlabel("时隙")
    axes[2].grid(alpha=0.3)

    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(svg_path, format="svg")
    plt.close(fig)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=None, help="运行序号（默认最新一次）")
    ap.add_argument("--site", type=str, default="site_1", help="站点（默认 site_1）")
    args = ap.parse_args()

    run = args.run if args.run is not None else latest_run()
    if run <= 0:
        print(f"[错误] 没有找到 report（{REPORT_DIR} 下无 run 目录）")
        return
    run_dir = os.path.join(REPORT_DIR, str(run))
    records = load_site(run_dir, args.site)
    if not records:
        print(f"[错误] {run_dir}/{args.site} 下没有 XML 报告")
        return

    plot_dir = os.path.join(run_dir, "plot")
    os.makedirs(plot_dir, exist_ok=True)
    d_svg = os.path.join(plot_dir, "decision_curve.svg")
    g_svg = os.path.join(plot_dir, "gain_curve.svg")
    plot_decision(records, d_svg, args.site)
    plot_gain(records, g_svg, args.site)
    print(f"已绘制 run={run}，{args.site}（{len(records)} 时隙）：")
    print(f"  决策曲线: {d_svg}")
    print(f"  收益曲线: {g_svg}")


if __name__ == "__main__":
    main()
