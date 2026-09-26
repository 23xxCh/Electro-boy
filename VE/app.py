# -*- coding: utf-8 -*-
"""app.py —— Streamlit 版 VE 看板（可选，需 `pip install streamlit`）。

与内置 HTML 看板（run.py 启动后 http://127.0.0.1:45678/）等价，但用 Streamlit 渲染。
开箱即用（无 UI 依赖）请用 run.py；本文件提供 Streamlit 的备选“网页”形态。

    conda activate env_ankerproject
    cd VE
    streamlit run app.py

配置在首次运行固定（默认：无限、180x、15min/槽、seed=123）；改配置请重启本脚本，
或改 `DEFAULT_*` 常量。也可直接改 run.py 的命令行参数后走内置看板。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st  # noqa: E402

from ve.config import SimConfig  # noqa: E402
from ve.runtime import Runtime  # noqa: E402

DEFAULT_DAYS = None       # None = 无限
DEFAULT_SPEED = 180.0
DEFAULT_GRANULARITY = 15
DEFAULT_SEED = 123

st.set_page_config(page_title="VE · Anker 虚拟家庭环境", layout="wide")
st.title("VE · Anker 虚拟家庭环境模拟器（Streamlit）")

if "rt" not in st.session_state:
    cfg = SimConfig(duration_days=DEFAULT_DAYS, speed=DEFAULT_SPEED,
                    granularity_min=DEFAULT_GRANULARITY, seed=DEFAULT_SEED)
    st.session_state.rt = Runtime(cfg)
    st.session_state.rt.start()

rt: Runtime = st.session_state.rt
eng = rt.engine

with st.sidebar:
    st.header("控制")
    c = st.columns(3)
    if c[0].button("▶ 启动"):
        eng.start()
    if c[1].button("⏸ 暂停"):
        eng.pause()
    if c[2].button("▶ 继续"):
        eng.resume()
    c2 = st.columns(2)
    if c2[0].button("⏭ 单步"):
        eng.step_manual()
    if c2[1].button("■ 停止"):
        eng.stop()
    st.divider()
    meta = eng.meta()
    st.write(f"slot={meta['slot']} · {meta['now']}")
    st.write(f"{meta['granularity_min']}min/槽 · {meta['speed']}x")
    st.write(f"时长：{'无限' if meta['duration_days'] is None else str(meta['duration_days'])+'天'}")
    st.caption("改配置请重启本脚本或改 app.py 顶部常量。")

# ---- 评分 ----
sc = eng.score()
c1, c2, c3, c4 = st.columns(4)
c1.metric("saving_settled_percent", f"{sc['saving_settled_percent']:.1f}%")
c2.metric("累计收益", f"€{sc['reward_eur']:.2f}")
c3.metric("基准 K", f"€{sc['baseline_k_eur']:.2f}")
c4.metric("最好成绩", f"{sc['best']['saving_settled_percent']:.1f}%" if sc.get("best") else "—")

# ---- 站点 ----
st.subheader("站点 Site Info / 实时状态")
cols = st.columns(eng.cfg.n_sites)
for i, sid in enumerate(eng.sites):
    stt = eng.site_state(sid)
    with cols[i]:
        mode = "第三方接管" if stt["operating_mode"] == 3 else f"mode {stt['operating_mode']}"
        st.markdown(f"**{sid}** · {mode}")
        st.metric("SOC", f"{stt['soc_pct']:.1f}%")
        st.write(f"光伏 {stt['solar_power_w']:.0f} W · 负载 {stt['home_load_w']:.0f} W")
        st.write(f"电池功率 {stt['battery_power_w']:.0f} W（负=充）")
        st.write(f"买电 {stt['grid_import_w']:.0f} / 卖电 {stt['grid_export_w']:.0f} W")
        st.write(f"下发 setpoint {stt['power_setpoint_w']:.0f} W")

# ---- 电价 ----
st.subheader("电网电价（三站共享）")
price = eng.price_today_tomorrow()
import pandas as pd  # noqa: E402
pday = price["today"]
pdf = pd.DataFrame({"买价": pday["buy"], "卖价": pday["sell"], "价差": pday["spread"]})
st.line_chart(pdf)

# ---- 天气 ----
st.subheader("天气预报（今明逐时）")
for sid in list(eng.sites)[:1]:
    wt = eng.weather_today_tomorrow(sid)
    t = wt["today"]
    wdf = pd.DataFrame({
        "云量%": t["cloud_pct"], "辐照W/m2": t["irradiance_w_m2"], "温度℃": t["temperature_c"],
    })
    st.caption(f"{sid} 今日")
    st.line_chart(wdf)

# ---- 站点功率 / 收益曲线 ----
st.subheader("站点功率 / 收益曲线")
series_sid = st.selectbox("站点", list(eng.sites), key="series_sid")
series = eng.slot_series(series_sid, 400)
if series:
    sdf = pd.DataFrame(series)
    sig = st.selectbox(
        "功率信号",
        ["home_load_w", "solar_power_w", "battery_power_w", "grid_import_w", "grid_export_w", "soc_pct"],
        format_func=lambda x: {"home_load_w": "家庭负载(功率信号)", "solar_power_w": "光伏",
                               "battery_power_w": "电池功率", "grid_import_w": "买电",
                               "grid_export_w": "卖电", "soc_pct": "SOC"}[x],
        key="sig")
    st.line_chart(sdf.set_index("slot")[sig])
    st.line_chart(sdf.set_index("slot")[["baseline_k_eur", "actual_cost_eur"]].rename(
        columns={"baseline_k_eur": "基准电费(不调度)", "actual_cost_eur": "LP实际电费(调度)"}))

st.caption("模拟 HA REST 桥：http://127.0.0.1:8123/api/states · HTTP API：http://127.0.0.1:45678/")

if st.checkbox("自动刷新", value=True):
    time.sleep(2)
    st.rerun()
