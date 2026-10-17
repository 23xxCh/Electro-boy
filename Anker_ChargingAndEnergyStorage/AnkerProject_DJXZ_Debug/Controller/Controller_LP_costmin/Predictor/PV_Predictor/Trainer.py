# -*- coding: utf-8 -*-
"""Weather → PV 预测器：多对多 LSTM（直接预测光伏功率）。

结构：
    输入   x   一天 24 个逐时点 × F 个标准化特征（PCC 筛选气象 + 昨日光伏 + 时刻 sin/cos）
    LSTM   batch_first 编码 → 每个时间步隐状态
    解码   每个隐状态 → 64 → ReLU → Dropout → 1（该小时功率 kW）
    输出   这一天 24 点光伏功率 kW

回归任务：损失峰值加权 MSE（alpha 可调，alpha=0 退化为普通 MSE）。
目标 R² 区间 0.75~0.9。
"""
from __future__ import annotations

import random

import numpy as np
import torch
import torch.nn as nn

SEED = 42


def set_seed(seed: int = SEED) -> None:
    """固定随机种子，保证结果可复现。"""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


class LSTMPredictor(nn.Module):
    """多对多 LSTM：每个时间步隐状态解码为该小时功率。"""

    def __init__(self, input_dim: int, hidden_dim: int = 128, num_layers: int = 2,
                 output_len: int = 24, dropout: float = 0.2):
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.output_len = output_len
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True,
                            dropout=dropout if num_layers > 1 else 0.0)
        self.fc = nn.Sequential(
            nn.Linear(hidden_dim, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 1),
        )

    def forward(self, x: torch.Tensor, return_hidden: bool = False):
        # x: (batch, 24, F)
        out, (hn, _) = self.lstm(x)          # out: (batch,24,hidden), hn: (layers,batch,hidden)
        pred = self.fc(out).squeeze(-1)      # (batch, 24)
        if return_hidden:
            return pred, hn[-1]              # 预测 + 最后层隐状态（供 XGBoost 级联）
        return pred


def peak_weighted_mse(pred: torch.Tensor, target: torch.Tensor,
                      alpha: float = 0.5) -> torch.Tensor:
    """对高峰加权的 MSE：权重 = 1 + alpha*target（夜间≈1，午间峰值≈1+5α）。

    alpha=0 时退化为普通 MSE。
    """
    w = 1.0 + alpha * target
    loss = (pred - target) ** 2 * w
    return loss.sum() / w.sum()


def train_model(train_x: np.ndarray, train_y: np.ndarray,
                val_x: np.ndarray, val_y: np.ndarray,
                input_dim: int, hidden_dim: int = 128, num_layers: int = 2,
                epochs: int = 400, lr: float = 1e-3, batch_size: int = 32,
                weight_decay: float = 1e-4, loss_alpha: float = 0.5,
                patience: int = 20, device=None):
    """训练 LSTM，返回 (model, history)。

    history = {"train_loss": [...], "val_loss": [...]}，每 epoch 一个点。
    含 L2 权重衰减 + Dropout + 早停（patience 轮验证损失不降即停），
    结束后回载验证集上最优权重。
    """
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    model = LSTMPredictor(input_dim, hidden_dim, num_layers).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        opt, mode="min", factor=0.5, patience=10)

    Xt = torch.tensor(train_x, dtype=torch.float32, device=device)
    yt = torch.tensor(train_y, dtype=torch.float32, device=device)
    Xv = torch.tensor(val_x, dtype=torch.float32, device=device)
    yv = torch.tensor(val_y, dtype=torch.float32, device=device)
    n = Xt.shape[0]

    history = {"train_loss": [], "val_loss": []}
    best_val = float("inf")
    best_state = None
    patience_counter = 0

    def loss_fn(pred, target):
        return peak_weighted_mse(pred, target, alpha=loss_alpha)

    for epoch in range(1, epochs + 1):
        model.train()
        perm = torch.randperm(n, device=device)
        total = 0.0
        for i in range(0, n, batch_size):
            idx = perm[i:i + batch_size]
            opt.zero_grad()
            loss = loss_fn(model(Xt[idx]), yt[idx])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            opt.step()
            total += loss.item() * len(idx)
        train_loss = total / n

        model.eval()
        with torch.no_grad():
            val_loss = loss_fn(model(Xv), yv).item()
        scheduler.step(val_loss)

        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)

        if val_loss < best_val:
            best_val = val_loss
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            patience_counter = 0
        else:
            patience_counter += 1

        if epoch % 10 == 0 or epoch == 1:
            print(f"  epoch {epoch:>4d}/{epochs}  train_loss={train_loss:.4f}  "
                  f"val_loss={val_loss:.4f}  lr={opt.param_groups[0]['lr']:.1e}")

        if patience_counter >= patience:
            print(f"  早停于 epoch {epoch}（最佳验证 val_loss={best_val:.4f}）")
            break

    model.load_state_dict(best_state)
    model.eval()
    print(f"训练完成：最佳验证 val_loss={best_val:.4f}")
    return model, history


def predict(model: LSTMPredictor, X: np.ndarray) -> np.ndarray:
    """对 (N,24,F) 批量预测，返回 (N,24) kW。"""
    device = next(model.parameters()).device
    model.eval()
    with torch.no_grad():
        out = model(torch.tensor(X, dtype=torch.float32, device=device))
    return out.cpu().numpy()


def extract_features(model: LSTMPredictor, X: np.ndarray):
    """提取 LSTM 的预测与最后层隐状态，供 XGBoost 级联输入。

    返回 (pred, hidden)：pred (N,24)，hidden (N, hidden_dim)。
    """
    device = next(model.parameters()).device
    model.eval()
    with torch.no_grad():
        pred, hidden = model(torch.tensor(X, dtype=torch.float32, device=device),
                             return_hidden=True)
    return pred.cpu().numpy(), hidden.cpu().numpy()


def fine_tune_model(model: LSTMPredictor, train_x: np.ndarray, train_y: np.ndarray,
                    lr: float = 1e-4, epochs: int = 15,
                    weight_decay: float = 1e-4, loss_alpha: float = 0.5,
                    device=None) -> LSTMPredictor:
    """用少量新站点数据（如 7 天历史）微调已训练 LSTM。

    低学习率 + 全批梯度 + 少量轮次，快速适应该站点而不破坏全局知识。
    """
    device = device or next(model.parameters()).device
    model.train()
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    Xt = torch.tensor(train_x, dtype=torch.float32, device=device)
    yt = torch.tensor(train_y, dtype=torch.float32, device=device)

    for epoch in range(epochs):
        opt.zero_grad()
        loss = peak_weighted_mse(model(Xt), yt, alpha=loss_alpha)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        opt.step()
        if epoch % 3 == 0 or epoch == epochs - 1:
            print(f"    warmup epoch {epoch + 1}/{epochs}  loss={loss.item():.4f}")

    model.eval()
    return model


def save_checkpoint(model: LSTMPredictor, path: str, meta: dict) -> None:
    torch.save({"state_dict": model.state_dict(), **meta}, path)


def load_checkpoint(path: str):
    """加载模型，返回 (model, meta)。meta 含 scaled_feature_names / scaler 等。"""
    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    model = LSTMPredictor(ckpt["input_dim"], ckpt["hidden_dim"],
                          ckpt["num_layers"], ckpt["output_len"])
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, ckpt
