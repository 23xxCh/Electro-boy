# -*- coding: utf-8 -*-
"""um_embedding.py —— UM 冻结窗口自编码器接口（上游→下游共享，BERT 式解耦的「接口契约」）。

这个模块是上下游之间唯一的耦合点，对应 BERT 里「[CLS] 向量」的角色：

    UM（冻结）= 两个窗口自编码器（时域 26→8 + 频域 8→4），把任意 48h 负荷窗编码成
    12 维向量 z。z 是**分辨率无关**的——无论输入是 60min 的 48 点，还是 15min 降采样
    后的 48 点，得到的都是同一个「负荷形态」摘要（峰数/能量/STCLPR/频谱）。

    DM（可训练）只读 z（固定 12 维向量），不重跑 UM 的序列层，也不做 tick 对齐。
    UM 48 tick（60min）vs DM 192 tick（15min）的分辨率不匹配，因为接口是「固定维向量」
    而非「tick 对齐序列」而自动解耦。

对外接口：
    load_window_encoders() -> encoders
    encode_window_emb(encoders, load_window) -> z (12,)   # load_window 为 1-D kW，长度>=48
    downsample_15min_to_60min(x) -> (n//4,)               # 15min 均值降采样到 60min
"""
from __future__ import annotations
import os
import sys

import numpy as np
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
WEMB_TIME = os.path.join(HERE, "Upstream_model", "window_embedding_time.pt")
WEMB_FREQ = os.path.join(HERE, "Upstream_model", "window_embedding_freq.pt")

sys.path.insert(0, HERE)
from station_embedding import Autoencoder, station_time_features  # noqa: E402
from signal_features import harmonic_spectrum  # noqa: E402


def load_window_encoders():
    ct = torch.load(WEMB_TIME, map_location="cpu", weights_only=False)
    cf = torch.load(WEMB_FREQ, map_location="cpu", weights_only=False)
    enc_t = Autoencoder(len(ct["feature_names"]), 16, ct["latent"])
    enc_t.load_state_dict(ct["state_dict"]); enc_t.eval()
    enc_f = Autoencoder(len(cf["feature_names"]), 4, cf["latent"])
    enc_f.load_state_dict(cf["state_dict"]); enc_f.eval()
    return (enc_t, enc_f,
            np.asarray(ct["scaler_mean"], dtype=np.float64),
            np.asarray(ct["scaler_scale"], dtype=np.float64),
            np.asarray(cf["scaler_mean"], dtype=np.float64),
            np.asarray(cf["scaler_scale"], dtype=np.float64))


def encode_window_emb(encoders, load_window):
    """load_window: 1-D kW 负荷窗（长度>=48，60min 口径）→ z (12,) float32。"""
    load_window = np.asarray(load_window, dtype=np.float64).reshape(-1)
    enc_t, enc_f, tmu, tsd, fmu, fsd = encoders
    rt = station_time_features(load_window)
    if rt is None:
        return None
    vec_t = rt[1]
    hf = harmonic_spectrum(load_window, n_harmonics=8)
    xn_t = (vec_t - tmu) / tsd
    xn_f = (hf - fmu) / fsd
    with torch.no_grad():
        e_t = enc_t.encode(torch.tensor(xn_t, dtype=torch.float32).unsqueeze(0)).numpy().reshape(-1)
        e_f = enc_f.encode(torch.tensor(xn_f, dtype=torch.float32).unsqueeze(0)).numpy().reshape(-1)
    return np.concatenate([e_t, e_f]).astype(np.float32)


def downsample_15min_to_60min(x):
    """15min → 60min：每 4 个 15min 样本取均值 = 60min 平均功率。"""
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    n = (x.size // 4) * 4
    return x[:n].reshape(-1, 4).mean(axis=1)
