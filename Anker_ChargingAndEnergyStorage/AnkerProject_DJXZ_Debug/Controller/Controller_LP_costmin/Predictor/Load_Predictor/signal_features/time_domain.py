# -*- coding: utf-8 -*-
"""time_domain.py —— 时域特征工具函数（MATLAB 参考代码的 Python 仿写）。

参考（旋转机械智能运维，Zhivomirov 等）：
    framing.m          —— 重叠分帧（buffer）
    conlocpksrate.m    —— 条件局部峰值速率 STCLPR
    crossrate.m        —— 阈值穿越率 CR
    stamd.m            —— 短时平均幅度差 STAMD
新增：
    短时能量 / 短时能量差（与 STCLPR/STAMD 同族：分帧 + 短时统计，捕捉能量突变）

所有函数纯 numpy，无第三方依赖；输入 x 为 1-D 时域信号。
"""
from __future__ import annotations

import numpy as np


# ------------------------- 分帧 -------------------------
def framing(x, frlen: int, hop: int):
    """重叠分帧（仿 MATLAB buffer(x, frlen, frlen-hop, 'nodelay')）。

    返回 (frlen, n_frames) 矩阵：列 = 帧，行 = 帧内样本。
    """
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    if x.size < frlen:
        return x.reshape(-1, 1)
    n_frames = 1 + (x.size - frlen) // hop
    idx = np.arange(frlen)[None, :] + hop * np.arange(n_frames)[:, None]
    return x[idx].T   # (frlen, n_frames)


# ------------------------- 短时统计（ST 一族） -------------------------
def stamd(frames):
    """短时平均幅度差 STAMD（仿 stamd.m）：每帧相邻样本绝对差值的平均。

    输入 frames: (frlen, n_frames)。返回 (n_frames,) 每帧的 amd。
    """
    f = np.asarray(frames, dtype=np.float64)
    return np.mean(np.abs(np.diff(f, axis=0)), axis=0)   # (n_frames,)


def short_time_energy(frames):
    """短时能量：每帧能量 = mean(|frame|²)（也可用 sum，mean 消除帧长差异）。"""
    f = np.asarray(frames, dtype=np.float64)
    return np.mean(f ** 2, axis=0)   # (n_frames,)


def short_time_energy_diff(x, frlen: int, hop: int):
    """短时能量差：相邻帧能量之差（捕捉能量突变）。返回 (n_frames-1,)。"""
    e = short_time_energy(framing(x, frlen, hop))
    return np.diff(e)


def conlocpksrate(x, mindiff: float):
    """条件局部峰值速率 STCLPR（conlocpksrate.m 精确仿写）。

    条件局部峰值 = 一个样本「先涨超 mindiff、再跌超 -mindiff」；
    速率 = 峰个数 / 样本数。x 1-D 返回标量，2-D (N,F) 返回 (F,) 每列一个值。
    """
    x = np.asarray(x, dtype=np.float64)
    is_1d = (x.ndim == 1)
    if is_1d:
        x = x.reshape(-1, 1)
    if x.shape[0] < 3:
        r = np.zeros(x.shape[1])
        return float(r[0]) if is_1d else r
    d1 = np.diff(x[:-1, :], axis=0)   # 上升差分
    d2 = np.diff(x[1:, :], axis=0)    # 随后的差分
    r = np.sum((d1 > mindiff) & (d2 <= -mindiff), axis=0) / x.shape[0]
    return float(r[0]) if is_1d else r


def crossrate(x, threshold: float):
    """阈值穿越率 CR（crossrate.m 精确仿写）：穿越 threshold 的次数 / 样本数。

    x 1-D 返回标量，2-D (N,F) 返回 (F,)。
    """
    x = np.asarray(x, dtype=np.float64)
    is_1d = (x.ndim == 1)
    if is_1d:
        x = x.reshape(-1, 1)
    if x.shape[0] < 2:
        r = np.zeros(x.shape[1])
        return float(r[0]) if is_1d else r
    r = np.count_nonzero(np.diff(x > threshold, axis=0), axis=0) / x.shape[0]
    return float(r[0]) if is_1d else r


# ------------------------- 峰统计（短时） -------------------------
def peak_to_peak(frames):
    """每帧峰峰值 = max - min。"""
    f = np.asarray(frames, dtype=np.float64)
    return np.ptp(f, axis=0)   # (n_frames,)


def peak_valley(frames):
    """每帧峰值与谷值（分开返回）。"""
    f = np.asarray(frames, dtype=np.float64)
    return f.max(axis=0), f.min(axis=0)   # (n_frames,), (n_frames,)


def local_rise_rate(x):
    """局部上升速率：正差分的均值（度量「向上突变」强度）。"""
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    d = np.diff(x)
    rise = d[d > 0]
    return float(rise.mean()) if rise.size else 0.0


def peak_count(x, frlen: int, hop: int, mindiff: float, state_thresh: float = 0.07):
    """用 STCLPR 检测峰段数（仿 AccProcess.m 的状态分割）。

    流程：分帧 → 去均值取 abs → conlocpksrate 每帧 → 阈值 = state_thresh×mean →
         峰段数 = STCLPR 超过阈值的帧数。
    返回 (n_peaks, stclpr 曲线, 阈值)。
    """
    frames = framing(x, frlen, hop)
    abs_frames = np.abs(frames - frames.mean(axis=0, keepdims=True))
    clpr = conlocpksrate(abs_frames, mindiff)
    clpr = np.asarray(clpr, dtype=np.float64)
    thresh = state_thresh * clpr.mean() if clpr.size else 0.0
    n_peaks = int((clpr > thresh).sum())
    return n_peaks, clpr, thresh


# ------------------------- 自适应阈值器 -------------------------
def otsu_threshold(values):
    """Otsu 二分类阈值（最大化类间方差），用于 STCLPR 状态分割的自适应阈值。"""
    values = np.asarray(values, dtype=np.float64)
    if values.size < 4 or values.min() == values.max():
        return float(values.min()) if values.size else 0.0
    hist, bins = np.histogram(values, bins=50)
    centers = (bins[:-1] + bins[1:]) / 2
    total = float(values.size)
    w1 = np.cumsum(hist).astype(np.float64)
    w2 = total - w1
    m1 = np.cumsum(hist * centers) / np.maximum(w1, 1)
    m2 = (np.cumsum((hist * centers)[::-1])[::-1]) / np.maximum(w2, 1)
    between = w1 * w2 * (m1 - m2) ** 2
    between = np.where((w1 > 0) & (w2 > 0), between, 0.0)
    return float(centers[int(np.argmax(between))])


def adaptive_mindiff(x, quantile: float = 0.75):
    """自适应 mindiff（条件局部峰值阈值 Δ）：正差分的 quantile 分位数。

    用「一阶微分的正分量」的高分位作为 Δ，自动过滤小抖动、只保留显著峰。
    """
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    d = np.diff(x)
    pos = d[d > 0]
    if pos.size == 0:
        return 0.0
    return float(np.quantile(pos, quantile))


def peak_count_adaptive(x, frlen: int, hop: int, mindiff_quantile: float = 0.75):
    """自适应峰段数：mindiff = 正差分分位数，状态阈值 = Otsu 阈值。

    返回 (n_peaks, stclpr 曲线, 阈值, mindiff)。
    """
    z = (x - x.mean()) / (x.std() + 1e-9)   # z-score
    md = adaptive_mindiff(z, mindiff_quantile)
    frames = framing(z, frlen, hop)
    abs_frames = np.abs(frames - frames.mean(axis=0, keepdims=True))
    clpr = np.asarray(conlocpksrate(abs_frames, md), dtype=np.float64)
    thresh = otsu_threshold(clpr)
    n_peaks = int((clpr > thresh).sum())
    return n_peaks, clpr, thresh, md


# ------------------------- 能量特征（功率积分） -------------------------
def energy(x, dt: float = 1.0):
    """能量特征（功率积分）= sum(x)·dt。负荷 dt=1h，故能量 = sum(kW)·1h = kWh。"""
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    return float(np.sum(x) * dt)


def energy_per_day(x, dt: float = 1.0):
    """日均能量（kWh/day）：把 24h 一组的能量取平均。"""
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    D = x.size // 24
    if D == 0:
        return energy(x, dt)
    day = x[: D * 24].reshape(D, 24)
    return float(np.mean(day.sum(axis=1) * dt))


def short_time_zero_crossing(x, threshold=None):
    """短时过零率（负荷信号全正，用「穿越均值」替代「穿越零」）。

    返回 (cr_mean, cr_std)：分帧后每帧穿越率（穿越 threshold）的均值/标准差。
    """
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    thr = float(np.mean(x)) if threshold is None else float(threshold)
    frames = framing(x, 6, 3)   # 6h 帧 hop 3h
    cr = crossrate(frames, thr)
    cr = np.asarray(cr, dtype=np.float64)
    return float(cr.mean()) if cr.size else 0.0, float(cr.std()) if cr.size else 0.0


def peak_segments(x, sigma: float = 1.0):
    """峰段数：负荷 z-score > sigma 的连续时段个数（σ>1 才算高峰，直接阈值分割）。

    这是「高峰段数」的正确定义（不是 STCLPR 的涨跌幅度）。
    返回 (n_segments, 每个峰段的 z-score 峰值均值)。
    """
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    z = (x - x.mean()) / (x.std() + 1e-9)
    above = z > sigma
    if not above.any():
        return 0, 0.0
    # 上升沿数（False→True 的跳变）+ 开头为 True
    edges = np.diff(above.astype(int))
    n = int(np.sum(edges == 1)) + (1 if above[0] else 0)
    return n, float(z[above].mean())
