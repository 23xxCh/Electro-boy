# AN-01 实施与所有权

## 顺序

1. S0 已完成：读取现状，冻结spec/contract/类型与测试。
2. S1 已完成：工程基线、聊天hotfix和架构文档已合并；AN-01只在隔离发布副本集成，不覆盖仍有活动写入者的主工作区或录屏版本。
3. S2 backend：两表/状态机/owner-only发送门、只读GET与开关POST、feed agent终态事件、原Worker即时回复覆盖、monitor受限tick。通知schema独立检查，不改MemoryV2 user_version=2；真实首次DDL前由受控idle迁移做SQLite backup API备份，不裸copy/WAL。GET mode=ro不触发factory迁移。构造/默认disabled不发送，无后台propose/HA轮询。
4. S2 frontend并行：精修现首页状态/实际/预计/建议层级、通知开关与最近20条、needsTimezone提示、agent bot-only气泡与消息/执行unknown区分；不改backend。
5. S3离线已完成修复候选评审：临时owner/SQLite、fake时间/SDK/HTTP；同时间戳首次启用/重启/迁移修复与四视口首页独立验证。各套检查分开记录于verification，不混合计数或代签真人送达。
6. S4 root评审源码/截图/证据并收敛。只有明确授权与idle边界才考虑本机候选部署；不连接环境机，不代本人开启提醒/确认控制或发送演示通知。

## 所有权与冲突边界

| 范围 | owner | 说明 |
|---|---|---|
| docs/specs/主动通知与首页精修/{spec,contract,plan,tasks}.md | /root/app_environment_adapter | root批准合同后冻结，变更先同步所有owner |
| 能源handoff相关Now/In progress | /root/app_environment_adapter | 写前重读，保留另一会话影片等无关条目 |
| Git baseline/推送与生产解锁 | root及其发布审查agent | 本owner不提交/推送或读秘密 |
| assistant/notifications.py（新）、store.py、service.py、runtime_tools.py必要事件关联、web_gateway.py、channels/feishu.py、frontend/server.py allowlist | backend owner，待root解锁指派 | API/通道统一owner；不改runtime/live/optimizer/release transport |
| frontend/src/ConsumerHome.tsx、assistantWeb.ts、consumer.css与候选build | frontend owner，待root指派 | backend不碰这些文件；保留Environment独立只读合同 |
| checks/notifications/**、checks/home-polish/** 独立QA及证据 | QA owner，待root指派 | 不改生产，不代发飞书/本人确认 |

原环境机独立接口合同保持；需要改它必须另评范围。不得在监控线程直接共享writer写通知；只向现串行业务executor投递有界tick。

本次公开候选来源是逐文件冻结快照：通知和公开测试使用已修复副本，六个UI文件使用独立修复副本，其余七个调用依赖保持作者已验hash。不能直接用主工作区的旧通知/首页覆盖候选。实际部署、真实SQLite迁移、用户开启与消息送达仍是后续验收；本次Git发布不执行这些动作。

## 验收与风险

构造无发送、master明确开启、无时区不能开启、无新事实无发送；收件人重查、一次尝试/unknown不重发、首次不回放、关闭取消、quiet合并、daily独立水位、reply覆盖、feed终态一次均需离线证据。实际飞书权限/送达及真人体验另记，不能用fake成功或SDK返回代签。当前已通过的本机闭环证据不改成新的执行成果；另一个获授权任务的HA只读采样成功，看板认证、身份/时间与App链仍未完成，远端控制blocked。
