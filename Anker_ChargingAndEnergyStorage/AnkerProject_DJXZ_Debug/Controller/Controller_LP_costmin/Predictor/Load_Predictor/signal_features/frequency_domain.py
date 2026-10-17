# -*- coding: utf-8 -*-
"""frequency_domain.py —— 频域特征工具函数（纯 numpy）。

对负荷/功率信号的 FFT 幅度谱提取频域统计量，用于描述站点类型的「周期纯净度」。
所有函数纯 numpy。
"""
from __future__ import annotations

import numpy as np


def fft_spectrum(x, d: float = 1.0):
    """FFT 幅度谱 + 频率轴。x 1-D；d 为采样间隔（小时，负荷信号 d=1h）。

    返回 (freqs, magnitude)：freqs 单位 1/d（负荷 = 1/h）。
    """
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    x = x - x.mean()
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(x.size, d=d)
    return freqs, spec


def dominant_freq(freqs, spec):
    """主频：能量最高的非零频率（单位同 freqs）。"""
    if spec.size <= 1:
        return 0.0
    i = int(np.argmax(spec[1:])) + 1
    return float(freqs[i])


def spectral_centroid(freqs, spec):
    """谱质心：功率加权平均频率。"""
    power = spec ** 2
    total = float(power.sum())
    if total < 1e-12:
        return 0.0
    return float((freqs * power).sum() / total)


def fundamental_count(freqs, spec, rel_thresh: float = 0.1):
    """基频数量：谱峰中能量 > rel_thresh×主峰 的个数。"""
    peaks = []
    for i in range(1, spec.size - 1):
        if spec[i] > spec[i - 1] and spec[i] > spec[i + 1]:
            peaks.append(i)
    if not peaks:
        return 0.0
    top = max(spec[i] for i in peaks)
    return float(sum(1 for i in peaks if spec[i] > rel_thresh * top))


def band_energy_share(freqs, spec, f0: float, tol: float = 1e-9):
    """某个频点 f0 的能量占比（谱线能量 / 总能量）。"""
    power = spec ** 2
    total = float(power.sum())
    if total < 1e-12:
        return 0.0
    idx = [i for i, f in enumerate(freqs) if abs(f - f0) < tol]
    return float(power[idx[0]] / total) if idx else 0.0


def freq_features(x, d: float = 1.0, f0: float = 1.0 / 24.0):
    """负荷信号的完整频域特征集（6 维）。

    返回 dict：
        n_fund      基频数量（显著谱峰数）
        dom_freq    主频
        centroid    谱质心
        fund_share  日基频(1/24h)能量占比
        h2_share    2 次谐波(2/24h)能量占比
        h3_share    3 次谐波(3/24h)能量占比
    """
    freqs, spec = fft_spectrum(x, d=d)
    return {
        "n_fund": fundamental_count(freqs, spec),
        "dom_freq": dominant_freq(freqs, spec),
        "centroid": spectral_centroid(freqs, spec),
        "fund_share": band_energy_share(freqs, spec, f0),
        "h2_share": band_energy_share(freqs, spec, 2 * f0),
        "h3_share": band_energy_share(freqs, spec, 3 * f0),
    }


def harmonic_spectrum(x, n_harmonics: int = 16, d: float = 1.0, f0: float = 1.0 / 24.0):
    """连续频谱：日基频 f0 的 1..n_harmonics 次谐波的**归一化幅度**（连续频谱，不是标量）。

    返回 (n_harmonics,) 数组：每个谐波的幅度 / 总能量平方根。
    这比 freq_features 的 6 个标量更「连续」，能刻画负荷频谱的完整形状。
    """
    freqs, spec = fft_spectrum(x, d=d)
    total = float((spec ** 2).sum())
    amps = []
    for k in range(1, n_harmonics + 1):
        idx = [i for i, f in enumerate(freqs) if abs(f - k * f0) < 1e-9]
        amps.append(float(spec[idx[0]] / (np.sqrt(total) + 1e-12)) if idx else 0.0)
    return np.asarray(amps, dtype=np.float64)


def peak_position_profile(x, sigma: float = 1.0):
    """峰位置分布（频域指标）：24 维，每个 hour 的「峰概率」。

    峰位置 = 负荷 z-score > sigma 的 hour；按天统计每个 hour 出现峰的天数占比。
    这反映「峰在一天中的位置」（日周期形状），是频域/形状信息。
    返回 (24,) 数组。
    """
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    D = x.size // 24
    if D == 0:
        return np.zeros(24, dtype=np.float64)
    day = x[: D * 24].reshape(D, 24)
    z_day = (day - day.mean(axis=1, keepdims=True)) / (day.std(axis=1, keepdims=True) + 1e-9)
    above = z_day > sigma
    return above.mean(axis=0)   # (24,) 每 hour 峰概率
