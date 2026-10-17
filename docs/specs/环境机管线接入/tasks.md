# Tasks

- [x] 读 handoff 与新旧源码，确认 no-network 边界。
- [x] S0 文档候选及事实缺口。
- [x] root S0 批准。
- [x] 新 ReleasePipeline、私有配置与严格状态解析。
- [x] 独立只读 RuntimeAPI/App/Agent 精确别名接入，默认 disabled。
- [x] 纯 mock/静态离线检查：作者8项、6AST、原Web/decision/CH03 32项；独立 QA 12项分别通过。
- [x] 前端只读入口：作者12项/tsc/隔离candidate build、独立VM/parser6项通过，served dist不切换；最终6 backend+3 frontend源pins及原served118文件SHA核对通过。
- [x] handoff 与最终边界说明。
- [ ] 用户允许后的服务reload、连通和浏览器验收（本轮明确不做）。
- [ ] 远端单槽控制／回读／退出／结算契约及实现（仍阻塞，不据离线通过放行）。
