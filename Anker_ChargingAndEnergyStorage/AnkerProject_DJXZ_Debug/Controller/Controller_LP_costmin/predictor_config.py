# -*- coding: utf-8 -*-
"""predictor_config.py —— 预测器归一化常量（单一事实来源）。

这两个常量必须与「训练时」所用一致（featurize 在训练与推理间共享）。
Debug1 用的是 PV_PEAK=1.6 / LOAD_REF=2.0（对应它 1.6kW 光伏的小站点），
决赛正式站点光伏装机 = 5 kW（SOP §8），所以 PV_PEAK_KW 必须改为 5.0。

负载参考值 LOAD_REF_KW 决赛未给定，按站点自适应（warmup.py 在线标定覆盖）。
"""
from __future__ import annotations

# 光伏功率归一化参考（= 光伏装机，SOP §8：5000W）
PV_PEAK_KW = 5.0

# 负载归一化参考（初始猜测；warmup.py 会按站点实测高分位在线覆盖）
LOAD_REF_KW = 2.0

# 天气/气温归一化（与 Debug1 一致，无站点依赖）
IRR_NORM_W_M2 = 1000.0     # 辐照度 / 1000
CLOUD_NORM_PCT = 100.0     # 云量 / 100
TEMP_NORM_C = 35.0         # 气温 / 35
