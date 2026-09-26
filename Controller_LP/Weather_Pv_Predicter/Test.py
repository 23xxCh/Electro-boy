# -*- coding: utf-8 -*-
"""测试已训练的 Weather → PV 预测器（在整份数据上评估）。"""
from __future__ import annotations

import os

import numpy as np

import Trainer
from Train import build_dataset, CSV

CKPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Weather_Pv_Predicter.pt")


def main() -> None:
    X, y = build_dataset(CSV)
    model = Trainer.load_checkpoint(CKPT)
    pred = Trainer.predict(model, X)
    mae = float(np.mean(np.abs(pred - y)))
    rmse = float(np.sqrt(np.mean((pred - y) ** 2)))
    r2 = float(1 - np.sum((y - pred) ** 2) / np.sum((y - np.mean(y)) ** 2))
    print(f"Weather_Pv_Predicter  MAE={mae:.4f} kW  RMSE={rmse:.4f} kW  R2={r2:.4f}")


if __name__ == "__main__":
    main()
