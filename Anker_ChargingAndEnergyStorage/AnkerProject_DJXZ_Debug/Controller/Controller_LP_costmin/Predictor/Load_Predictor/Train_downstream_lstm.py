# -*- coding: utf-8 -*-
"""Train_downstream_lstm.py —— 下游 15min 负荷预测器（DM，从 UM 微调，BERT 式解耦）。

接口解耦（核心，对应 BERT 的 [CLS] 向量）：
    UM（冻结）= um_embedding 的两个窗口自编码器
        → 48h 负荷窗（15min 数据降采样到 60min）→ 12 维窗口嵌入 z（分辨率无关）
    DM（可训练）= 自己的 15min LSTM，消费 z + 15min 特征，直接预测 15min 负荷

    UM 是 60min（48 tick/2天），DM 是 15min（192 tick/2天）。二者不做 tick 对齐：
    接口是「固定 12 维向量 z」，不是序列。DM 拿到 z 后广播到自己的每个 15min 槽，
    配以自己的 15min lag / 时间 / 天气 / 电价特征，再走自己的序列层。
    → 上游只管「这 48h 是什么负荷形态」，下游只管「这个形态下每 15min 是多少」。

数据：DataSet/DataSet_mocksampling_15mpt/dataset_mock_*/contest_simulation_site{n}.csv
      （15min，home_load_w 为负荷真值；多个 run 目录）。
产出：Downstream_model/load_predictor_site{n}.pt（覆盖旧的 VMD-LSTM 残留权重）。

用法：python Train_downstream_lstm.py [epochs]
"""
from __future__ import annotations
import csv
import glob
import os
import sys
from datetime import datetime

import numpy as np
import torch
import torch.nn as nn

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
MERGE_CSV = os.path.join(ROOT, "DataSet", "DataSet_merge", "dataset_merge_1",
                         "dataset_merge_15mpt", "dataset_merge_15mpt.csv")
OUT_DIR = os.path.join(HERE, "Downstream_model")
PLOT_DIR = os.path.join(OUT_DIR, "plots")
TABLE_DIR = os.path.join(HERE, "Upstream_model", "results", "tables")

sys.path.insert(0, HERE)
from um_embedding import load_window_encoders, encode_window_emb, downsample_15min_to_60min  # noqa: E402
from signal_features.plotting import save_fig  # noqa: E402

HIST_15 = 192          # 48h 历史（15min 槽数），供 z 嵌入 + lag
HORIZON = 192          # 48h 预测（15min 槽数）
HOP = 24               # 6h 滑窗步长
LAGS_15 = [4, 96, 192]  # 1h / 1day / 2day（15min 槽）
SEED = 42


def load_site_runs(site_no):
    """读 merge 后 15mpt CSV，取该站、按 fragment_id 分组，每组为一个 run。

    merge CSV 列：local_hour_time / site / fragment_id / load_power_avg_w(W) /
    temperature_c / import_price_eur_kwh / export_price_eur_kwh ...
    负荷列 load_power_avg_w 是 W，读时 /1000 转 kW（与训练口径一致）。
    每个 fragment（比赛窗口）独立成一个 run，避免跨片段 lag 泄漏。
    """
    runs = []
    frags = {}
    with open(MERGE_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["site"] != str(site_no):
                continue
            frag = row["fragment_id"]
            frags.setdefault(frag, []).append((
                datetime.strptime(row["local_hour_time"], "%Y-%m-%d %H:%M:%S"),
                float(row["load_power_avg_w"]) / 1000.0,
                float(row.get("temperature_c", 0.0) or 0.0),
                float(row.get("import_price_eur_kwh", 0.0) or 0.0),
                float(row.get("export_price_eur_kwh", 0.0) or 0.0),
            ))
    for frag, rows in frags.items():
        rows.sort(key=lambda r: r[0])                       # 按时间排序
        # 去重相邻重复时间戳（保留首次出现的值）
        dedup = []
        for r in rows:
            if not dedup or r[0] != dedup[-1][0]:
                dedup.append(r)
        runs.append({"times": np.asarray([r[0] for r in dedup]),
                     "load": np.asarray([r[1] for r in dedup], dtype=np.float64),
                     "temp": np.asarray([r[2] for r in dedup], dtype=np.float64),
                     "imp": np.asarray([r[3] for r in dedup], dtype=np.float64),
                     "exp": np.asarray([r[4] for r in dedup], dtype=np.float64)})
    runs.sort(key=lambda r: r["times"][0])                  # 按 run 起始时间排序
    return runs


def build_lag_features_15(flat):
    N = flat.size
    out = np.zeros((N, len(LAGS_15)), dtype=np.float64)
    for j, lg in enumerate(LAGS_15):
        out[lg:, j] = flat[:-lg]
    return out


def build_tick_features(run, z, lagf, t_start, t_end):
    """目标窗 [t_start, t_end) 内每 15min 槽的特征 (HORIZON, F)。"""
    F = []
    for t in range(t_start, t_end):
        dt = run["times"][t]
        h = dt.hour + dt.minute / 60.0
        minute = dt.minute
        feat = np.concatenate([
            z,                                                     # 12 维 UM 嵌入
            lagf[t],                                               # 3 维 lag
            [np.sin(2 * np.pi * h / 24.0), np.cos(2 * np.pi * h / 24.0),
             np.sin(2 * np.pi * minute / 60.0), np.cos(2 * np.pi * minute / 60.0),
             np.sin(2 * np.pi * dt.weekday() / 7.0), np.cos(2 * np.pi * dt.weekday() / 7.0)],
            [run["temp"][t], run["imp"][t], run["exp"][t]],        # 3 维天气/电价
        ])
        F.append(feat)
    return np.stack(F).astype(np.float32)   # (HORIZON, F)


class DownstreamLSTM(nn.Module):
    """DM：自己的 15min LSTM，消费 z（广播）+ 15min 特征，预测「高于 base(p5) 的增量」。

    输出是增量（>=0，经 ReLU），最终负荷 = base(p5) + 增量。base 提供下限，避免
    ReLU 输出头直接回归负荷时塌缩到 0（梯度在 0 处为 0 导致卡死）。
    """

    def __init__(self, input_dim, hidden=64, num_layers=2):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden, num_layers, batch_first=True)
        self.head = nn.Sequential(nn.Linear(hidden, 32), nn.ReLU(),
                                  nn.Linear(32, 1), nn.ReLU())
        with torch.no_grad():
            self.head[2].bias.fill_(0.5)   # 正偏置初始化，避免增量头初始就塌缩到 0

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.head(out).squeeze(-1)   # (B, HORIZON) 增量


def mae(y_true, y_pred):
    return float(np.mean(np.abs(np.asarray(y_true) - np.asarray(y_pred))))


def build_windows(runs, encoders):
    """把多个 run 拆成 (X, y增量, baseline, base) 窗口；z 只用过去 48h（因果）。

    y增量 = 未来 192 槽负荷 − base(p5(过去48h))；最终负荷 = base + 增量。
    """
    Xs, ys, pss, bases = [], [], [], []
    for run in runs:
        load = run["load"]
        n = load.size
        if n < HIST_15 + HORIZON:
            continue
        lagf = build_lag_features_15(load)
        for s in range(0, n - HIST_15 - HORIZON + 1, HOP):
            hist_15 = load[s:s + HIST_15]
            hist_60 = downsample_15min_to_60min(hist_15)   # 192→48（60min 口径）
            if hist_60.size < 48:
                continue
            z = encode_window_emb(encoders, hist_60)        # 12 维，过去 48h（无泄漏）
            if z is None:
                continue
            t_start = s + HIST_15
            t_end = t_start + HORIZON
            X = build_tick_features(run, z, lagf, t_start, t_end)   # (HORIZON, F)
            base = float(np.percentile(hist_15, 5)) + 1e-6          # p5 基线
            y = load[t_start:t_end].astype(np.float32) - base       # 增量目标
            ps = lagf[t_start:t_end, 1].astype(np.float32)          # 基线 lag96（昨日同时）
            Xs.append(X); ys.append(y); pss.append(ps); bases.append(base)
    return (np.stack(Xs), np.stack(ys), np.stack(pss),
            np.asarray(bases, dtype=np.float32))


def train_site(site_no, encoders, epochs):
    runs = load_site_runs(site_no)
    if len(runs) < 2:
        print(f"  site{site_no}: 数据 run 不足（{len(runs)}），跳过")
        return
    # 最后 1 个 run 作为 held-out（模拟「新比赛」泛化），其余训练
    tr_runs, te_runs = runs[:-1], runs[-1:]
    Xtr, ytr, _, _ = build_windows(tr_runs, encoders)
    Xte, yte, pte, bte = build_windows(te_runs, encoders)
    if Xtr.shape[0] == 0 or Xte.shape[0] == 0:
        print(f"  site{site_no}: 窗口不足（train={Xtr.shape[0]}, test={Xte.shape[0]}），跳过")
        return
    F = Xtr.shape[-1]
    print(f"  site{site_no}: 训练 {Xtr.shape[0]} 窗 / 测试 {Xte.shape[0]} 窗，特征 {F} 维")

    # 特征标准化（用训练集统计）
    flat_x = Xtr.reshape(-1, F)
    feat_mean = flat_x.mean(axis=0); feat_std = flat_x.std(axis=0)
    feat_std = np.where(feat_std == 0, 1.0, feat_std)
    Xtr_s = ((flat_x - feat_mean) / feat_std).reshape(Xtr.shape).astype(np.float32)
    flat_xe = Xte.reshape(-1, F)
    Xte_s = ((flat_xe - feat_mean) / feat_std).reshape(Xte.shape).astype(np.float32)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch.manual_seed(SEED); np.random.seed(SEED)
    model = DownstreamLSTM(F).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-3)
    Xt = torch.tensor(Xtr_s, device=device); yt = torch.tensor(ytr, device=device)
    n = Xt.shape[0]
    perm = torch.randperm(n, device=device); ntr = int(n * 0.9)
    tr_ids, va_ids = perm[:ntr], perm[ntr:]
    best_val, best_state, cnt = float("inf"), None, 0
    for ep in range(1, epochs + 1):
        model.train()
        for i in range(0, ntr, 32):
            ids = tr_ids[i:i + 32]
            opt.zero_grad()
            pred = model(Xt[ids])
            loss = (pred - yt[ids]).abs().mean()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
        model.eval()
        with torch.no_grad():
            vp = (model(Xt[va_ids]) - yt[va_ids]).abs().mean().item()
        if vp < best_val - 1e-5:
            best_val = vp; best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            cnt = 0
        else:
            cnt += 1
        if cnt >= 30:
            break
    model.load_state_dict(best_state)
    model.eval()

    with torch.no_grad():
        inc = model(torch.tensor(Xte_s, device=device)).cpu().numpy()
    # 负荷 = base + 增量（重构），与昨日同时基线比较
    pred = inc + bte.reshape(-1, 1)                    # (n_test, HORIZON) 负荷
    yte_load = yte + bte.reshape(-1, 1)                # 实际负荷
    dm_mae = mae(yte_load.reshape(-1), pred.reshape(-1))
    ps_mae = mae(yte_load.reshape(-1), pte.reshape(-1))
    print(f"  site{site_no}: held-out MAE  DM={dm_mae:.3f}  昨日同时基线={ps_mae:.3f}  "
          f"({'胜' if dm_mae < ps_mae else '败'})")

    # 保存 checkpoint（含 DM 结构 + 标准化 + UM 嵌入配置）；带时间戳另存，不覆盖正式权重
    os.makedirs(OUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    ckpt_path = os.path.join(OUT_DIR, f"load_predictor_site{site_no}_{ts}.pt")
    torch.save({"model": best_state, "feat_mean": feat_mean, "feat_std": feat_std,
                "F": F, "hidden": 64, "num_layers": 2, "HORIZON": HORIZON,
                "HIST_15": HIST_15, "LAGS_15": LAGS_15,
                "site": site_no}, ckpt_path)
    print(f"  site{site_no}: 已保存（另存，未覆盖正式权重）：{ckpt_path}")
    print(f"  site{site_no}: 正式权重仍为 {OUT_DIR}/load_predictor_site{site_no}.pt")

    # 画图
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "PingFang SC"]
    plt.rcParams["axes.unicode_minus"] = False
    yt_flat = yte_load.reshape(-1); pred_flat = pred.reshape(-1); ps_flat = pte.reshape(-1)
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    ax = axes[0]
    ax.scatter(yt_flat, ps_flat, s=4, alpha=0.3, c="b", label=f"昨日同时 (MAE={ps_mae:.3f})")
    ax.scatter(yt_flat, pred_flat, s=4, alpha=0.3, c="g", label=f"DM (MAE={dm_mae:.3f})")
    lim = [0, max(yt_flat.max(), ps_flat.max(), pred_flat.max()) * 1.05]
    ax.plot(lim, lim, "k--", lw=1, label="对角线 y=x")
    ax.set_xlim(lim); ax.set_ylim(lim)
    ax.set_xlabel("真实 (kW)"); ax.set_ylabel("预测 (kW)")
    ax.set_title(f"site{site_no} DM 15min 散点"); ax.legend(loc="upper left"); ax.grid(alpha=0.3)
    ax = axes[1]
    n1 = min(HORIZON, yt_flat.size)
    ax.plot(yt_flat[:n1], "k-", lw=1.5, label="真实")
    ax.plot(ps_flat[:n1], "b--", lw=1, label="昨日同时")
    ax.plot(pred_flat[:n1], "g:", lw=1.2, label="DM")
    ax.set_xlabel("15min 槽"); ax.set_ylabel("kW"); ax.set_title(f"site{site_no} 单日"); ax.legend(); ax.grid(alpha=0.3)
    ax = axes[2]
    n5 = min(HORIZON * 5, yt_flat.size)
    ax.plot(yt_flat[:n5], "k-", lw=1, alpha=0.8, label="真实")
    ax.plot(ps_flat[:n5], "b--", lw=1, alpha=0.8, label="昨日同时")
    ax.plot(pred_flat[:n5], "g:", lw=1.1, alpha=0.8, label="DM")
    ax.set_xlabel("15min 槽（前5天）"); ax.set_ylabel("kW"); ax.set_title(f"site{site_no} 多日"); ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout()
    os.makedirs(PLOT_DIR, exist_ok=True)
    save_fig(fig, os.path.join(PLOT_DIR, f"load_site{site_no}_dm15min.png")); plt.close(fig)
    return dm_mae, ps_mae


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    encoders = load_window_encoders()
    print(f"下游 DM 训练（15min，BERT 式解耦，z 只用过去 48h）  epochs={epochs}")
    results = {}
    for site_no in (1, 2, 3):
        r = train_site(site_no, encoders, epochs)
        if r:
            results[f"site{site_no}"] = r
    print("\n" + "=" * 70)
    print("  下游 DM held-out 汇总（15min，DM vs 昨日同时基线 lag96）")
    print("=" * 70)
    for sid, (dm, ps) in results.items():
        print(f"  {sid}: DM={dm:.3f}  基线={ps:.3f}  差={dm-ps:+.3f}")
    # 写指标表
    os.makedirs(TABLE_DIR, exist_ok=True)
    with open(os.path.join(TABLE_DIR, "downstream_lstm_metrics.csv"), "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["site", "DM_MAE", "baseline_lag96_MAE", "delta"])
        for sid, (dm, ps) in results.items():
            w.writerow([sid, f"{dm:.6f}", f"{ps:.6f}", f"{dm-ps:.6f}"])


if __name__ == "__main__":
    main()
