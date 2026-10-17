# -*- coding: utf-8 -*-
"""signal_features —— 时域 + 频域特征工具函数包。

专目录存放「计算时域/频域特征的工具函数」（MATLAB 参考代码的 Python 仿写）。
"""
from .time_domain import (framing, stamd, short_time_energy, short_time_energy_diff,
                          conlocpksrate, crossrate, peak_to_peak, peak_valley,
                          local_rise_rate, peak_count, otsu_threshold,
                          adaptive_mindiff, peak_count_adaptive, energy,
                          energy_per_day, short_time_zero_crossing, peak_segments)
from .frequency_domain import (fft_spectrum, dominant_freq, spectral_centroid,
                               fundamental_count, band_energy_share, freq_features,
                               harmonic_spectrum, peak_position_profile)

__all__ = [
    "framing", "stamd", "short_time_energy", "short_time_energy_diff",
    "conlocpksrate", "crossrate", "peak_to_peak", "peak_valley", "local_rise_rate",
    "peak_count", "otsu_threshold", "adaptive_mindiff", "peak_count_adaptive",
    "energy", "energy_per_day", "short_time_zero_crossing", "peak_segments",
    "fft_spectrum", "dominant_freq", "spectral_centroid", "fundamental_count",
    "band_energy_share", "freq_features", "harmonic_spectrum", "peak_position_profile",
]
