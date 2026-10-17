"""占位求解器。决赛换成 LPTest_Demo 的 LP 输出。"""
from __future__ import annotations


def plan(site: dict, weather, price) -> dict:
    """返回该站本拍功率。正充负放是约定，SOP 确认前只打日志。"""
    _ = weather, price
    return {
        "site_id": site["id"],
        "setpoint_w": 0.0,
        "mode": "third_party_control",
        "reason": "skeleton-hold",
    }
