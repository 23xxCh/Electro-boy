# -*- coding: utf-8 -*-
"""score.py —— 调度成绩 Score（官方口径）。

官方：saving_settled_percent = (a+b+c)/(d+e+f)×100%
  a/b/c = 三站各自结算收益差 ΔR_settled = K_i − actual_cost_i
  d/e/f = 三站各自基准电费 K_i（全站负载按买价买电，与策略无关）
即：saving = Σ(K_i − cost_i) / ΣK_i × 100%。
"""
from __future__ import annotations

from typing import Dict


def compute_score(sites: Dict[str, "Site"]) -> dict:
    K = sum(s.baseline_k_eur for s in sites.values())
    cost = sum(s.actual_cost_eur for s in sites.values())
    saving = (K - cost) / K * 100.0 if K > 0 else 0.0
    per_site = {}
    for sid, s in sites.items():
        k = s.baseline_k_eur
        c = s.actual_cost_eur
        per_site[sid] = {
            "baseline_k_eur": k,
            "actual_cost_eur": c,
            "reward_eur": k - c,
            "saving_pct": (k - c) / k * 100.0 if k > 0 else 0.0,
        }
    return {
        "saving_settled_percent": saving,
        "baseline_k_eur": K,
        "actual_cost_eur": cost,
        "reward_eur": K - cost,
        "per_site": per_site,
    }
