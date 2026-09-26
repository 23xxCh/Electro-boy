# -*- coding: utf-8 -*-
"""训练负载预测器，保存 Load_Predicter.pt。"""
from __future__ import annotations

import csv
import os
from datetime import datetime

import numpy as np

import Trainer

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "..", "training_data.csv")
CKPT = os.path.join(HERE, "Load_Predicter.pt")


def build_dataset(csv_path: str, horizon: int = 96, step: int = 8):
    """构造"配对样本"：以 t 时刻的实测负载为锚点，预测 t+1..t+horizon 的负载。"""
    temp, hour, dow, load = [], [], [], []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            temp.append(float(row["temperature_c"]))
            dt = datetime.strptime(row["timestamp"], "%Y-%m-%dT%H:%M:%S")
            hour.append(dt.hour + dt.minute / 60.0)
            dow.append(dt.weekday())
            load.append(float(row["home_load_kw"]))
    temp = np.array(temp)
    hour = np.array(hour)
    dow = np.array(dow)
    load = np.array(load)
    N = len(load)
    X, y = [], []
    for t in range(0, N - horizon, step):
        cur_load = load[t]
        for k in range(1, horizon + 1):
            X.append(Trainer.featurize(hour[t + k], dow[t + k], temp[t + k], cur_load))
            y.append(load[t + k])
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
