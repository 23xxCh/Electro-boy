# -*- coding: utf-8 -*-
"""station_embedding.py —— 站点类型嵌入（两个 autoencoder：时域 + 频域分离）。

目的：从各站点负荷信号中提取「幅值峰 / 突变 / 周期」特征，用两个独立 autoencoder
      分别编码时域与频域，得到组合站点嵌入，解决「上游池化训练没学到站间幅值差异」。

时域特征（25 维，含短时能量差）：
  A. 全局水平/离散度：mean, median, p5, p95, max, std, cv (7)
  B. 短时 2h 帧（重叠 hop=1h）：峰峰值/峰值谷值比/上升速率 mean/std/p95 (6)
  C. 短时能量差（新，24h 帧 hop=12h）：相邻帧能量差 mean/std/p95 (3)
  D. 日级：全天峰峰值均值、日峰值均值、日谷值均值 (3)
  E. STCLPR/穿越率：clpr_global, stclpr_mean/std/p95, cr_mean, cr_p5 (6)

频域特征（6 维）：基频数量、主频、谱质心、日基频(1/24h)能量占比、2/3 次谐波占比。

两个 autoencoder：
  时域 25 → 16 → 8（时域嵌入 8 维）
  频域  6 →  4 → 4（频域嵌入 4 维）
  组合站点嵌入 = 12 维。

特征工具函数见 signal_features/ 目录（MATLAB 参考代码的 Python 仿写）。

产出：Upstream_model/station_embedding_time.pt + station_embedding_freq.pt
     + station_embeddings.csv（组合嵌入 + PCA）。
"""
from __future__ import annotations
import csv
import glob
import os
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np
import torch
import torch.nn as nn

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
TRAIN_DIR = os.path.join(ROOT, "DataSet", "DataSet_100site92days_60mpt", "solarbank-training-data")
SIM_SITE_DIR = os.path.join(ROOT, "DataSet", "DataSet_100site92days_60mpt", "solarbank-sim-sites")
CKPT_TIME = os.path.join(HERE, "Upstream_model", "station_embedding_time.pt")
CKPT_FREQ = os.path.join(HERE, "Upstream_model", "station_embedding_freq.pt")
EMB_CSV = os.path.join(HERE, "Upstream_model", "results", "tables", "station_embeddings.csv")
FFT_LEN = 336   # 14 天，固定频域分析长度（保证训练站/比赛站可比）

sys.path.insert(0, HERE)
from signal_features import (  # noqa: E402
    short_time_energy_diff, conlocpksrate, crossrate, freq_features, peak_count,
    peak_count_adaptive, peak_segments,
)

TIME_LATENT = 8
FREQ_LATENT = 4


# ------------------------- 数据读取 -------------------------
def load_train_station(fp):
    vals = []
    with open(fp, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            vals.append(float(row["load_power_avg_w"]) / 1000.0)
    return np.asarray(vals, dtype=np.float64)


def load_sim_site_hourly(site_no):
    """比赛站 = solarbank-sim-sites（训练口径，已 60min/kW），读 load_power_avg_w。"""
    fp = os.path.join(SIM_SITE_DIR, f"sim_site{site_no}.csv")
    vals = []
    if os.path.exists(fp):
        with open(fp, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                vals.append(float(row["load_power_avg_w"]) / 1000.0)
    return np.asarray(vals, dtype=np.float64)


# ------------------------- 时域特征 -------------------------
def station_time_features(a):
    """时域特征（25 维）。a 为 1-D 负荷信号 (kW)。"""
    a = a[np.isfinite(a)]
    if a.size < 48:
        return None
    D = a.size // 24
    a = a[: D * 24]
    day = a.reshape(D, 24)

    mean = float(a.mean()); std = float(a.std()); med = float(np.median(a))
    p5, p95 = np.percentile(a, [5, 95])
    cv = std / mean if mean > 1e-6 else 0.0
    mx = float(a.max())

    # 短时 2h 帧（重叠 hop=1h）：峰峰值 / 峰值谷值比 / 上升速率
    p2p, pk, vl, rise = [], [], [], []
    for i in range(a.size - 1):
        x0, x1 = a[i], a[i + 1]
        p2p.append(abs(x1 - x0))
        pk.append(max(x0, x1)); vl.append(min(x0, x1))
        rise.append(max(0.0, x1 - x0))
    p2p = np.array(p2p); pk = np.array(pk); vl = np.array(vl); rise = np.array(rise)

    # 短时能量差（新）：24h 帧 hop=12h，相邻帧能量差
    sed = short_time_energy_diff(a, frlen=24, hop=12)

    day_p2p = np.ptp(day, axis=1)
    day_pk = day.max(axis=1); day_vl = day.min(axis=1)

    # STCLPR + 穿越率：z 分数 + mindiff=0.3σ，24h 帧 hop=12h
    z = (a - mean) / (std + 1e-9)
    md = 0.3
    clpr_global = float(conlocpksrate(z, md))
    fr_clpr = []
    for s0 in range(0, a.size - 24 + 1, 12):
        seg = a[s0:s0 + 24]
        sm, ss = seg.mean(), seg.std()
        fr_clpr.append(float(conlocpksrate((seg - sm) / (ss + 1e-9), md)))
    fr_clpr = np.asarray(fr_clpr, dtype=np.float64)
    cr_mean = float(crossrate(a, mean))
    cr_p5 = float(crossrate(a, p5))

    # 峰段数（σ>1 高峰，直接阈值分割）：负荷 z-score > 1 的连续时段个数
    n_peaks, _ = peak_segments(z, sigma=1.0)

    names = [
        "mean", "median", "p5", "p95", "max", "std", "cv",
        "frame_p2p_mean", "frame_p2p_std", "frame_p2p_p95",
        "frame_rise_mean", "frame_rise_p95", "frame_peak_valley_ratio",
        "sed_mean", "sed_std", "sed_p95",
        "day_p2p_mean", "day_peak_mean", "day_valley_mean",
        "clpr_global", "stclpr_mean", "stclpr_std", "stclpr_p95",
        "cr_mean", "cr_p5", "n_peaks",
    ]
    vec = [
        mean, med, p5, p95, mx, std, cv,
        float(p2p.mean()), float(p2p.std()), float(np.percentile(p2p, 95)),
        float(rise.mean()), float(np.percentile(rise, 95)),
        float((pk.mean() / (vl.mean() + 1e-6))),
        float(sed.mean()) if sed.size else 0.0,
        float(sed.std()) if sed.size else 0.0,
        float(np.percentile(sed, 95)) if sed.size else 0.0,
        float(day_p2p.mean()), float(day_pk.mean()), float(day_vl.mean()),
        clpr_global,
        float(fr_clpr.mean()) if fr_clpr.size else 0.0,
        float(fr_clpr.std()) if fr_clpr.size else 0.0,
        float(np.percentile(fr_clpr, 95)) if fr_clpr.size else 0.0,
        cr_mean, cr_p5, float(n_peaks),
    ]
    return names, np.asarray(vec, dtype=np.float64)


# ------------------------- 频域特征 -------------------------
def station_freq_features(a):
    """频域特征（6 维）。固定 FFT_LEN 点保证可比。"""
    a = a[np.isfinite(a)]
    if a.size < 48:
        return None
    sig = np.zeros(FFT_LEN, dtype=np.float64) if a.size < FFT_LEN else a[:FFT_LEN]
    if a.size < FFT_LEN:
        sig[: a.size] = a[:FFT_LEN]
    f = freq_features(sig, d=1.0, f0=1.0 / 24.0)
    names = ["n_fund", "dom_freq", "centroid", "fund_share", "h2_share", "h3_share"]
    vec = [f["n_fund"], f["dom_freq"], f["centroid"], f["fund_share"],
           f["h2_share"], f["h3_share"]]
    return names, np.asarray(vec, dtype=np.float64)


def station_features(a):
    """拼接时域+频域特征（向后兼容）。返回 (names, vec)。"""
    r_t = station_time_features(a)
    r_f = station_freq_features(a)
    if r_t is None or r_f is None:
        return None
    names_t, vec_t = r_t
    names_f, vec_f = r_f
    return list(names_t) + list(names_f), np.concatenate([vec_t, vec_f])


# ------------------------- 模型 -------------------------
class Autoencoder(nn.Module):
    def __init__(self, fdim, hid, latent):
        super().__init__()
        self.enc = nn.Sequential(nn.Linear(fdim, hid), nn.ReLU(),
                                 nn.Linear(hid, latent))
        self.dec = nn.Sequential(nn.Linear(latent, hid), nn.ReLU(),
                                 nn.Linear(hid, fdim))

    def encode(self, x):
        return self.enc(x)

    def forward(self, x):
        return self.dec(self.enc(x))


def _train_ae(model, Xt, epochs=2000, lr=1e-3):
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    for ep in range(epochs):
        model.train()
        opt.zero_grad()
        loss = ((model(Xt) - Xt) ** 2).mean()
        loss.backward()
        opt.step()
    model.eval()
    return float(((model(Xt) - Xt) ** 2).mean().item())


def _zscore(X):
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd = np.where(sd == 0, 1.0, sd)
    return (X - mu) / sd, mu, sd


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    torch.manual_seed(42); np.random.seed(42)

    # 读取所有站点（100 训练站 + 3 比赛站）
    stations = {}
    for fp in sorted(glob.glob(os.path.join(TRAIN_DIR, "*.csv")),
                     key=lambda x: int(os.path.basename(x)[:-4])):
        stations[f"train{os.path.basename(fp)[:-4]}"] = load_train_station(fp)
    for n in (1, 2, 3):
        stations[f"site{n}"] = load_sim_site_hourly(n)

    # 特征
    ids, Xt_list, Xf_list = [], [], []
    for sid, a in stations.items():
        rt = station_time_features(a)
        rf = station_freq_features(a)
        if rt is None or rf is None:
            print(f"跳过 {sid}（样本过短）")
            continue
        ids.append(sid); Xt_list.append(rt[1]); Xf_list.append(rf[1])
    Xt = np.stack(Xt_list); Xf = np.stack(Xf_list)
    print(f"站点 {len(ids)}，时域 {Xt.shape[1]} 维，频域 {Xf.shape[1]} 维")

    # 标准化 + 训练两个 autoencoder
    Xtn, mu_t, sd_t = _zscore(Xt)
    Xfn, mu_f, sd_f = _zscore(Xf)

    time_ae = Autoencoder(Xt.shape[1], 16, TIME_LATENT)
    freq_ae = Autoencoder(Xf.shape[1], 4, FREQ_LATENT)
    Xtt = torch.tensor(Xtn, dtype=torch.float32)
    Xff = torch.tensor(Xfn, dtype=torch.float32)
    mse_t = _train_ae(time_ae, Xtt)
    mse_f = _train_ae(freq_ae, Xff)
    print(f"\n时域 autoencoder 重构 MSE = {mse_t:.4f}（{Xt.shape[1]}→{TIME_LATENT}）")
    print(f"频域 autoencoder 重构 MSE = {mse_f:.4f}（{Xf.shape[1]}→{FREQ_LATENT}）")

    # 组合嵌入
    with torch.no_grad():
        emb_t = time_ae.encode(Xtt).numpy()   # (N, 8)
        emb_f = freq_ae.encode(Xff).numpy()   # (N, 4)
    emb = np.concatenate([emb_t, emb_f], axis=1)   # (N, 12)

    # PCA 2D
    E = emb - emb.mean(axis=0)
    _, _, vt = np.linalg.svd(E, full_matrices=False)
    pca2 = E @ vt[:2].T

    # 保存两个 checkpoint + 组合嵌入 CSV
    os.makedirs(os.path.dirname(CKPT_TIME), exist_ok=True)
    torch.save({"state_dict": time_ae.state_dict(),
                "feature_names": station_time_features(stations["train1"])[0],
                "scaler_mean": mu_t.tolist(), "scaler_scale": sd_t.tolist(),
                "latent": TIME_LATENT}, CKPT_TIME)
    torch.save({"state_dict": freq_ae.state_dict(),
                "feature_names": station_freq_features(stations["train1"])[0],
                "scaler_mean": mu_f.tolist(), "scaler_scale": sd_f.tolist(),
                "latent": FREQ_LATENT}, CKPT_FREQ)
    with open(EMB_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["station"] + [f"emb{i}" for i in range(emb.shape[1])] +
                   ["pca0", "pca1"])
        for sid, e, p in zip(ids, emb, pca2):
            w.writerow([sid] + [f"{v:.6f}" for v in e] + [f"{p[0]:.4f}", f"{p[1]:.4f}"])
    print(f"已保存时域嵌入：{CKPT_TIME}")
    print(f"已保存频域嵌入：{CKPT_FREQ}")
    print(f"已保存组合嵌入表：{EMB_CSV}")

    # 3 比赛站在 PCA2 的位置 vs 训练站质心
    tr_idx = [i for i, s in enumerate(ids) if s.startswith("train")]
    sim_idx = {s: i for i, s in enumerate(ids) if s.startswith("site")}
    tr_c = pca2[tr_idx].mean(axis=0)
    tr_std = pca2[tr_idx].std(axis=0)
    print("\n" + "=" * 70)
    print("3 比赛站组合嵌入位置（PCA2）vs 训练站质心")
    print("=" * 70)
    print(f"  训练站质心 pca=({tr_c[0]:.3f},{tr_c[1]:.3f})  散布 std=({tr_std[0]:.3f},{tr_std[1]:.3f})")
    for s, i in sorted(sim_idx.items()):
        p = pca2[i]
        dist = float(np.linalg.norm(p - tr_c))
        z = dist / (np.linalg.norm(tr_std) + 1e-9)
        print(f"  {s}: pca=({p[0]:+.3f},{p[1]:+.3f})  距质心={dist:.3f}  (≈{z:.1f}σ)")

    # 组合嵌入与「类型」对齐
    mean_kw = np.array([Xt[i][0] for i in range(len(ids))])   # 时域第 0 维 = mean
    cv_kw = np.array([Xt[i][6] for i in range(len(ids))])     # 时域第 6 维 = cv
    for label, arr in (("mean_kw", mean_kw), ("cv", cv_kw)):
        c0 = float(np.corrcoef(pca2[:, 0], arr)[0, 1])
        c1 = float(np.corrcoef(pca2[:, 1], arr)[0, 1])
        print(f"  组合嵌入PCA与 {label} 的相关：pca0={c0:+.3f}  pca1={c1:+.3f}")


if __name__ == "__main__":
    main()
