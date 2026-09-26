# -*- coding: utf-8 -*-
"""Weather → PV 预测器：带傅里叶特征 + 残差块的 MLP。

输入特征（6 维）：辐照度、云量、气温、时刻(sin/cos)、当前光伏(锚点) —— 见 featurize()
输出：光伏功率 kW。

注意：featurize() 与 model_lp.py 里的 featurize_pv() 必须保持一致。
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

INPUT_DIM = 6
FOURIER_DIM = 64       # 傅里叶特征映射维度
HIDDEN_DIM = 64        # 残差块隐藏维度
NUM_BLOCKS = 4         # 残差块数量
OUTPUT_DIM = 1
SCALE = 1.0            # 傅里叶频率缩放系数（输入已归一化到[0,1]，高频会搅乱近线性拟合）
PV_PEAK = 1.6          # 站点光伏峰值 kW，归一化"当前光伏功率"特征（与 SiteParams.pv_peak_kw 一致）


def featurize(irradiance_w_m2: float, cloud_pct: float, temp_c: float, hour: float,
              current_pv_kw: float = 0.0) -> np.ndarray:
    return np.array([
        irradiance_w_m2 / 1000.0,
        cloud_pct / 100.0,
        temp_c / 35.0,
        np.sin(2 * np.pi * hour / 24.0),
        np.cos(2 * np.pi * hour / 24.0),
        current_pv_kw / PV_PEAK,   # 当前实测光伏功率（持久性锚点）
    ], dtype=np.float32)


class ResidualBlock(nn.Module):
    """残差块：Mish 激活 + 线性变换 + 残差连接。"""

    def __init__(self, dim: int):
        super().__init__()
        self.fc = nn.Linear(dim, dim)

    def forward(self, x):
        return x + self.fc(F.mish(x))


class MLP(nn.Module):
    """带傅里叶特征 + 残差块的 MLP（参考 1-Pytorch快速测试 的 MLP_withFourierAndResidual）。"""

    def __init__(self, input_dim: int = INPUT_DIM, fourier_dim: int = FOURIER_DIM,
                 hidden_dim: int = HIDDEN_DIM, num_blocks: int = NUM_BLOCKS,
                 output_dim: int = OUTPUT_DIM, scale: float = SCALE):
        super().__init__()
        self.register_buffer("B", torch.randn(input_dim, fourier_dim) * scale)
        self.input_layer = nn.Linear(2 * fourier_dim, hidden_dim)
        self.blocks = nn.ModuleList([ResidualBlock(hidden_dim) for _ in range(num_blocks)])
        self.output_layer = nn.Linear(hidden_dim, output_dim)

    def forward(self, x):
        x = torch.cat([torch.sin((2 * torch.pi * x) @ self.B),
                       torch.cos((2 * torch.pi * x) @ self.B)], dim=1)
        x = F.mish(self.input_layer(x))
        for block in self.blocks:
            x = block(x)
        return self.output_layer(x)


def save_checkpoint(model: MLP, path: str) -> None:
    torch.save({
        "state_dict": model.state_dict(),
        "input_dim": INPUT_DIM,
        "fourier_dim": FOURIER_DIM,
        "hidden_dim": HIDDEN_DIM,
        "num_blocks": NUM_BLOCKS,
        "output_dim": OUTPUT_DIM,
    }, path)


def load_checkpoint(path: str) -> MLP:
    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    model = MLP(ckpt["input_dim"], ckpt["fourier_dim"], ckpt["hidden_dim"],
                ckpt["num_blocks"], ckpt["output_dim"])
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model


def train_model(X: np.ndarray, y: np.ndarray, epochs: int = 300, lr: float = 1e-3,
                batch_size: int = 512, device=None) -> MLP:
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    model = MLP().to(device)
    Xt = torch.tensor(X, dtype=torch.float32, device=device)
    yt = torch.tensor(y, dtype=torch.float32, device=device).reshape(-1, 1)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()
    n = Xt.shape[0]
    for epoch in range(epochs):
        model.train()
        perm = torch.randperm(n, device=device)
        total = 0.0
        for i in range(0, n, batch_size):
            idx = perm[i:i + batch_size]
            opt.zero_grad()
            loss = loss_fn(model(Xt[idx]), yt[idx])
            loss.backward()
            opt.step()
            total += loss.item() * len(idx)
        if (epoch + 1) % 50 == 0:
            print(f"    epoch {epoch + 1}/{epochs}  loss={total / n:.6f}")
    model.eval()
    return model


def predict(model: MLP, X: np.ndarray) -> np.ndarray:
    model.eval()
    device = next(model.parameters()).device
    with torch.no_grad():
        out = model(torch.tensor(X, dtype=torch.float32, device=device)).squeeze(-1)
    return out.cpu().numpy()
