# -*- coding: utf-8 -*-
"""plotting.py —— 绘图公共工具（安全保存 + 中文字体配置）。

save_fig：保存前自动删除同名文件，避免 Windows 下 matplotlib/PIL 覆盖已存在文件时的
         OSError: [Errno 22] Invalid argument。所有绘图脚本应统一调用它，不再手动删文件。
"""
from __future__ import annotations

import os


def setup_chinese_font():
    """配置 matplotlib 中文字体（返回 plt 模块）。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "PingFang SC"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def save_fig(fig, path, dpi=120):
    """安全保存图片：先删除同名文件（若存在），再 savefig，避免覆盖 OSError。

    返回 path。失败时（文件被锁等）尝试改后缀重试一次。
    """
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    try:
        fig.savefig(path, dpi=dpi)
    except OSError:
        # 极端情况（文件仍被锁）：改一个带时间戳的名字重试
        import time
        root, ext = os.path.splitext(path)
        path = f"{root}_{int(time.time() * 1000)}{ext}"
        fig.savefig(path, dpi=dpi)
    return path
