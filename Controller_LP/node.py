# -*- coding: utf-8 -*-
"""node.py —— Controller_LP 节点（独立运行，连接 VE 节点）。

职责（只做与 VE 的通讯 + LP 调度 + 决策解释报告，不含任何 VE 内部实现）：
  1. 启动时向 VE 注册调度器名（同一时间只允许一个外部调度器，注册被拒则退出）；
  2. 每时隙：读状态/参数 → LP 决策 → 写回控制动作；
  3. 每时隙把「为什么这样决策」写成 XML 报告，输出到固定目录 report/<第N次运行>/<站点>/。

数据来源（对齐真实比赛）：
  - 设备状态 + 物理参数：经 **HA**（真实比赛经真实 HA，本地经 VE 的模拟 HA REST :8123）
  - 电价 / 天气预报：经 **VE HTTP API**（:45678）
  - 能流 + 收益（供报告/看板）：经 **VE HTTP API** `/api/vp`
  - 控制下发：经 **HA**（number.set_value）

用法：
    python node.py --ve http://127.0.0.1:45678 --ha http://127.0.0.1:8123 --name Controller_LP
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Controller import Controller          # noqa: E402  (LP 控制器，纯模型)
from site_params import SiteParams          # noqa: E402  (参数结构，非 VE 代码)

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(HERE, "report")   # 报告根目录（按第几次运行分 run 子目录）


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def next_run_index() -> int:
    """返回本次运行的序号（= 现有最大 run 目录号 + 1）。"""
    os.makedirs(REPORT_DIR, exist_ok=True)
    idx = 0
    for name in os.listdir(REPORT_DIR):
        if name.isdigit():
            idx = max(idx, int(name))
    return idx + 1


def discover_slugs(ha: str) -> list:
    """从 HA /api/states 发现站点（以 sensor.<slug>_rated_energy 为标记）。"""
    states = get_json(f"{ha}/api/states")
    slugs = set()
    for st in states:
        eid = st.get("entity_id", "")
        if eid.startswith("sensor.") and eid.endswith("_rated_energy"):
            slugs.add(eid[len("sensor."):-len("_rated_energy")])
    return sorted(slugs)


def build_sites(ha: str) -> list:
    """经 HA 读每个站点的物理参数（容量 + 充/放电上限）。"""
    sites = []
    for slug in discover_slugs(ha):
        rated = get_json(f"{ha}/api/states/sensor.{slug}_rated_energy")
        num = get_json(f"{ha}/api/states/number.{slug}_battery_power_setpoint")
        cap_kwh = float(rated["state"])
        max_ch = float(num["attributes"]["max_charge_power"])
        max_dis = float(num["attributes"]["max_discharge_power"])
        sites.append(SiteParams.from_ha(slug, cap_kwh, max_ch, max_dis))
    return sites


def read_state(ha: str, sid: str) -> dict:
    """经 HA 读当前状态（soc / solar_power / home_load）。"""
    def _f(key):
        return float(get_json(f"{ha}/api/states/sensor.{sid}_{key}")["state"])
    return {"soc": _f("soc") / 100.0,
            "solar_power_kw": _f("solar_power") / 1000.0,
            "home_load_kw": _f("home_load") / 1000.0}


def build_reason(sol: dict, buy: float, sell: float) -> str:
    """从 LP 结果 + 当前电价生成一句人话解释。"""
    if sol.get("status") != "ok":
        return f"LP 不可行/求解失败：{sol.get('note', '')}"
    reasons = []
    if sell < 0:
        reasons.append("当前卖价<0，硬约束禁止放电/上网")
    if sol.get("discharge_kw", 0.0) > 1e-6:
        reasons.append(f"放电 {sol['discharge_kw']:.2f}kW：当前买价 {buy*1000:.1f} €/MWh 偏高，"
                       f"用电池覆盖负载或高价卖电")
    if sol.get("charge_kw", 0.0) > 1e-6:
        reasons.append(f"充电 {sol['charge_kw']:.2f}kW：当前买价 {buy*1000:.1f} €/MWh 偏低，"
                       f"备后续高价放出")
    if sol.get("charge_kw", 0.0) <= 1e-6 and sol.get("discharge_kw", 0.0) <= 1e-6:
        reasons.append("无需充放（光伏/负载平衡或 SOC/功率受限）")
    return "；".join(reasons)


def write_report(run_dir: str, scheduler_name: str, sid: str, slot: int, timestamp: str,
                 state: dict, fc: dict, sol: dict, action_w: float, site: SiteParams,
                 vp: dict) -> str:
    """把一个时隙的决策解释写成 XML，返回文件路径。

    命名规则：report/<run>/<站点>/slot_<slot>.xml（slot 六位补零，字典序即时间序）。
    """
    d = os.path.join(run_dir, sid)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"slot_{slot:06d}.xml")

    root = ET.Element("decision")
    ET.SubElement(root, "scheduler").text = scheduler_name
    ET.SubElement(root, "site_id").text = sid
    ET.SubElement(root, "slot").text = str(slot)
    ET.SubElement(root, "timestamp").text = timestamp

    st = ET.SubElement(root, "state")
    ET.SubElement(st, "soc_pct").text = f"{state['soc']*100:.1f}"
    ET.SubElement(st, "solar_power_kw").text = f"{state['solar_power_kw']:.3f}"
    ET.SubElement(st, "home_load_kw").text = f"{state['home_load_kw']:.3f}"
    ET.SubElement(st, "capacity_kwh").text = f"{site.capacity_kwh:.2f}"

    pr = ET.SubElement(root, "prices")
    ET.SubElement(pr, "buy_now_eur_kwh").text = f"{fc['buy'][0]:.5f}"
    ET.SubElement(pr, "sell_now_eur_kwh").text = f"{fc['sell'][0]:.5f}"
    ET.SubElement(pr, "buy_min_eur_kwh").text = f"{min(fc['buy']):.5f}"
    ET.SubElement(pr, "buy_max_eur_kwh").text = f"{max(fc['buy']):.5f}"
    ET.SubElement(pr, "horizon_slots").text = str(len(fc["buy"]))

    ac = ET.SubElement(root, "action")
    ET.SubElement(ac, "charge_kw").text = f"{sol.get('charge_kw', 0.0):.3f}"
    ET.SubElement(ac, "discharge_kw").text = f"{sol.get('discharge_kw', 0.0):.3f}"
    ET.SubElement(ac, "net_power_w").text = f"{action_w:.1f}"
    ET.SubElement(ac, "expected_cost_eur").text = f"{sol.get('expected_cost_eur', float('nan')):.4f}"

    # 能流（电功率转移矩阵的 7 条边 + 弃光 + 充电损耗，单位 W）
    en = ET.SubElement(root, "energy_flow")
    for k, v in vp.get("flow", {}).items():
        ET.SubElement(en, k).text = f"{v:.1f}"

    # 收益（5 个指标，€/百分比）
    rv = ET.SubElement(root, "revenue")
    ET.SubElement(rv, "total_revenue_r_eur").text = f"{vp.get('total_revenue_r_eur', 0.0):.4f}"
    ET.SubElement(rv, "settled_revenue_eur").text = f"{vp.get('settled_revenue_eur', 0.0):.4f}"
    ET.SubElement(rv, "buy_cost_eur").text = f"{vp.get('buy_cost_eur', 0.0):.4f}"
    ET.SubElement(rv, "sell_revenue_eur").text = f"{vp.get('sell_revenue_eur', 0.0):.4f}"
    ET.SubElement(rv, "saving_rate_pct").text = f"{vp.get('saving_rate_pct', 0.0):.3f}"
    ET.SubElement(rv, "baseline_cost_eur").text = f"{vp.get('baseline_cost_eur', 0.0):.4f}"
    ET.SubElement(rv, "baseline_settled_revenue_eur").text = \
        f"{vp.get('baseline_settled_revenue_eur', 0.0):.4f}"

    ex = ET.SubElement(root, "explanation")
    ET.SubElement(ex, "shadow_price_storage_eur_kwh").text = \
        f"{sol.get('shadow_price_storage_eur_kwh', float('nan')):.5f}"
    ET.SubElement(ex, "shadow_price_energy_eur_kwh").text = \
        f"{sol.get('shadow_price_energy_eur_kwh', float('nan')):.5f}"
    ET.SubElement(ex, "reason").text = build_reason(sol, fc["buy"][0], fc["sell"][0])

    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Controller_LP 节点")
    ap.add_argument("--ve", default="http://127.0.0.1:45678", help="VE HTTP API 地址")
    ap.add_argument("--ha", default="http://127.0.0.1:8123", help="HA 地址（真实比赛填真实 HA）")
    ap.add_argument("--name", default="Controller_LP", help="调度器名")
    args = ap.parse_args()

    try:
        reg = post_json(f"{args.ve}/api/scheduler/register", {"name": args.name})
        if not reg.get("ok"):
            print(f"[错误] 注册失败：{reg.get('error', '未知')}")
            return
    except Exception as e:
        print(f"[警告] 注册调度器名失败：{e}")

    sites = build_sites(args.ha)
    if not sites:
        print(f"[错误] 未在 HA({args.ha}) 发现任何站点（sensor.<设备>_rated_energy）")
        return
    site_ids = [s.site_id for s in sites]

    run_idx = next_run_index()
    run_dir = os.path.join(REPORT_DIR, str(run_idx))
    for sid in site_ids:
        os.makedirs(os.path.join(run_dir, sid), exist_ok=True)
    os.makedirs(os.path.join(run_dir, "plot"), exist_ok=True)   # plot_report.py 输出 SVG 用

    meta = get_json(f"{args.ve}/api/meta")
    dt_h = meta["granularity_min"] / 60.0
    horizon = max(1, round(24.0 / dt_h))

    ctrl = Controller(sites, dt_h=dt_h, horizon=horizon)
    print(f"{args.name} 节点已启动：{len(sites)} 站点，run={run_idx}，"
          f"dt={dt_h}h，horizon={horizon}，report={run_dir}")

    last_slot = None
    try:
        while True:
            meta = get_json(f"{args.ve}/api/meta")
            slot = meta["slot"]
            if slot == last_slot:
                time.sleep(0.3)
                continue
            last_slot = slot
            ts = meta.get("now", "")

            for sid in site_ids:
                st = read_state(args.ha, sid)
                fc = get_json(f"{args.ve}/api/lp_forecast?site={sid}&horizon={horizon}")
                vp = get_json(f"{args.ve}/api/vp?site={sid}")
                try:
                    sol = ctrl.decide(sid, st, fc["buy"], fc["sell"],
                                      fc["irradiance"], fc["cloud"], fc["temperature"],
                                      fc["hours"], fc["dows"])
                    action_w = 0.0
                    if sol.get("status") == "ok":
                        action_w = (sol["discharge_kw"] - sol["charge_kw"]) * 1000.0
                except Exception as e:
                    sol = {"status": "error", "note": str(e)}
                    action_w = 0.0
                    print(f"  [{sid}] LP 决策异常：{e}")

                post_json(f"{args.ha}/api/services/number/set_value",
                          {"entity_id": f"number.{sid}_battery_power_setpoint",
                           "value": action_w})
                write_report(run_dir, args.name, sid, slot, ts, st, fc, sol, action_w,
                             ctrl.sites[sid], vp)
                if sid == site_ids[0]:
                    print(f"slot={slot} {sid}: SOC={st['soc']*100:.0f}% "
                          f"PV={st['solar_power_kw']*1000:.0f}W "
                          f"Load={st['home_load_kw']*1000:.0f}W -> {action_w:+.0f}W")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            post_json(f"{args.ve}/api/scheduler/unregister", {"name": args.name})
        except Exception:
            pass
        print(f"\n{args.name} 节点已停止。run={run_idx} 报告在 {run_dir}")


if __name__ == "__main__":
    main()
