# -*- coding: utf-8 -*-
"""convert_onnx.py —— 把两个预测器的 .pt 权重导出为 ONNX（一次性开发步骤）。

导出后 model_lp.py 用 onnxruntime 推理，release 不再需要 torch。
用法（开发机，有 torch 的环境）：
    python convert_onnx.py
"""
from __future__ import annotations

import os
import sys

import torch
import torch.nn as nn
import torch.nn.functional as F

HERE = os.path.dirname(os.path.abspath(__file__))


class ResidualBlock(nn.Module):
    def __init__(self, dim: int):
        super().__init__()
        self.fc = nn.Linear(dim, dim)

    def forward(self, x):
        return x + self.fc(F.mish(x))


class MLP(nn.Module):
    """与各 Trainer.py 的 MLP 一致（仅用于导出 ONNX）。"""

    def __init__(self, input_dim: int, fourier_dim: int, hidden_dim: int,
                 num_blocks: int, output_dim: int, scale: float = 10.0):
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


def convert(pt_path: str, onnx_path: str) -> None:
    ckpt = torch.load(pt_path, map_location="cpu", weights_only=False)
    model = MLP(ckpt["input_dim"], ckpt["fourier_dim"], ckpt["hidden_dim"],
                ckpt["num_blocks"], ckpt["output_dim"])
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    dummy = torch.randn(1, ckpt["input_dim"])
    torch.onnx.export(model, dummy, onnx_path,
                      input_names=["x"], output_names=["y"],
                      opset_version=13,
                      dynamic_axes={"x": {0: "batch"}, "y": {0: "batch"}})
    print(f"导出成功: {pt_path} -> {onnx_path}")


def main() -> None:
    convert(os.path.join(HERE, "Weather_Pv_Predicter", "Weather_Pv_Predicter.pt"),
            os.path.join(HERE, "Weather_Pv_Predicter", "Weather_Pv_Predicter.onnx"))
    convert(os.path.join(HERE, "Load_Predicter", "Load_Predicter.pt"),
            os.path.join(HERE, "Load_Predicter", "Load_Predicter.onnx"))
    print("完成。model_lp.py 现在会自动加载 .onnx（无需 torch）。")


if __name__ == "__main__":
    main()
