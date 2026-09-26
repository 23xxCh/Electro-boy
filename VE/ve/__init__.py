# -*- coding: utf-8 -*-
"""VE —— Anker 充电储能赛道 虚拟家庭环境（VirtualEnv）模拟器核心包。

与 LPTest_Demo 解耦的独立模拟器：3 站点 × 4 设备、共享电价、天气、评分，
通过 Modbus TCP（官方 HA 插件通信管道）+ HTTP API + 模拟 HA REST 对外通信。
"""
__version__ = "0.1.0"
