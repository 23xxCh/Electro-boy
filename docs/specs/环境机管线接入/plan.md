# 实施计划

1. S0：审阅新旧源代码并批准本范围，事实缺口留在 spec。
2. S1：新增 ReleasePipeline 与配置/严格解析；引用新 Simulator 包，不复制其协议。新原包保持原样。
3. S2：现有 loopback server 增加独立环境机 snapshot 入口；RuntimeAPI 增加独立方法与环境校验。默认 disabled；本地运行服务本轮不切换。
4. S3：纯 mock 离线断言；旧 Runtime/decision/CH03 focused regression（如导入有联网副作用则不执行）；独立 QA 复核范围。
5. S4：handoff 替换 Now/In progress，明确代码接入、默认禁用、控制 gate 与尚未连通，不改无关成片/CH03记录。

root 批准的生产路径：`energy_runtime/release.py`、`energy_runtime/server.py`、`assistant/runtime_api.py`、`assistant/web_gateway.py`、`assistant/runtime_tools.py` 精确环境机只读分支、`frontend/server.py` allowlist、`energy_runtime/README.md`。作者测试 `energy_runtime/test_release.py`；独立 QA 只写 `checks/environment-pipeline/**`。frontend owner 独占 ConsumerHome/assistantWeb/必要 CSS 与隔离 candidate-dist，不覆盖当前 served dist。解锁控制须先解决单槽执行/结算契约，不能扩大本轮。
