# -*- coding: utf-8 -*-
"""Controller —— 调度算法族（同一时间只运行一个）。

当前两个 LP 调度器，除「优化目标」外完全一致（LMS / LSTM / XGBoost / 预测器 / 预热
/ 超参数 / 报告均相同），各自是一个完整的最小实现，互不依赖：
    - Controller_LP_costmin  : costmin 目标（最小化已实现电费，6 变量/槽）
    - Controller_LP_scoremax : scoremax 目标（最大化结算节省率，8 变量/槽，含在库成本 C_T）

入口（main.py / main_local.py 等）直接 import 对应子包，不存在外层的 Controller_LP。
"""
