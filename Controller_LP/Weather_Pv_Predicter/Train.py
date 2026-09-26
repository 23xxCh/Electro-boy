# -*- coding: utf-8 -*-
"""训练 Weather → PV 预测器，保存 Weather_Pv_Predicter.pt。"""
from __future__ import annotations

import csv
import os
from datetime import datetime

import numpy as np

import Trainer

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "..", "training_data.csv")
CKPT = os.path.join(HERE, "Weather_Pv_Predicter.pt")


def build_dataset(csv_path: str, horizon: int = 96, step: int = 4):
    """构造"配对样本"：以 t 时刻的实测光伏为锚点，预测 t+1..t+horizon 的光伏。

    特征 = [t+k 时刻的辐照/云量/气温/时刻, t 时刻的实测光伏]；标签 = t+k 的实测光伏。
    step 控制当前时刻 t 的采样步长（减小样本量）。
    """
    irr, cloud, temp, hour, pv = [], [], [], [], []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            irr.append(float(row["irradiance_w_m2"]))
            cloud.append(float(row["cloud_cover_pct"]))
            temp.append(float(row["temperature_c"]))
            dt = datetime.strptime(row["timestamp"], "%Y-%m-%dT%H:%M:%S")
            hour.append(dt.hour + dt.minute / 60.0)
            pv.append(float(row["pv_power_kw"]))
    irr = np.array(irr)
    cloud = np.array(cloud)
    temp = np.array(temp)
    hour = np.array(hour)
    pv = np.array(pv)
    N = len(pv)
    X, y = [], []
    for t in range(0, N - horizon, step):
        cur_pv = pv[t]
        for k in range(1, horizon + 1):
            X.append(Trainer.featurize(irr[t + k], cloud[t + k], temp[t + k], hour[t + k], cur_pv))
            y.append(pv[t + k])
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


def main() -> None:
    X, y = build_dataset(CSV, horizon=96, step=8)
    n = len(X)
    idx = np.random.default_rng(0).permutation(n)
    ntr = int(n * 0.85)
    tr, te = idx[:ntr], idx[ntr:]
    print(f"训练集 {ntr} / 测试集 {n - ntr} 样本")

    model = Trainer.train_model(X[tr], y[tr], epochs=300, lr=1e-3)
    Trainer.save_checkpoint(model, CKPT)

    pred_tr = Trainer.predict(model, X[tr])
    pred_te = Trainer.predict(model, X[te])
    print(f"MAE  train={np.mean(np.abs(pred_tr - y[tr])):.4f} kW  "
          f"test={np.mean(np.abs(pred_te - y[te])):.4f} kW")
    print(f"saved : {CKPT}")


if __name__ == "__main__":
    main()
