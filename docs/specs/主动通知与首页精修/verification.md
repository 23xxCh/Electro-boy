# AN-01 源码候选验收

状态：**修复源码候选已完成离线检查与独立评审，未部署、未开启本人提醒、未真实发送。** 本次以 Draft PR 交付，不自动合并业务，不替换正在录屏的服务或前端。源码发布不等于功能上线。

## 实现与修复

复用同一个 Service、SQLite、串行业务执行器与已绑定飞书 owner；新增通知 settings/outbox 两表、只读通知 GET、仅 enabled 开关 POST、重要事件／每日摘要与 agent 终态 feed。默认关闭，缺已确认家庭 IANA 不能开启；不增加 HA 轮询、后台求解或自动控制。

首次开启与重新开启在 BEGIN IMMEDIATE 事务内固定已存在事件的 key 基线：相同 wall clock 的旧事实不回放，开启后相同时间戳的新事实仍可发送一次。旧 schema 的只读 GET 不执行 DDL；active 旧设置拒绝静默迁移，显式关闭后按 SQLite backup API 备份再升级，MemoryV2 user_version 保持2。发送前持久 UUID/sending，超时、5xx或崩溃按 unknown 保存，不自动重发。

首页沿用家庭能源世界：摘要区分当前状态、最近实际与未来预计；短桌面输入可达，手机视觉顺序为“摘要／时点 → Agent → 完整HUD → 3D／曲线”。四个已验视口初载至少显示3个完整助手回复行；pending 不自动抢焦点，点击查看才定位确认按钮。通知发送 unknown 与设备执行 unknown 分开展示。

## 分开记录的证据

以下是不同检查套件，存在覆盖重叠，**不能相加称为独立测试数量**。

| 检查 | 结果与范围 |
|---|---|
| 可公开自包含通知测试 | 25项通过；临时SQLite、fake API／SDK，包含同时间戳基线、迁移与代理调用 |
| 既有 Web／decision／CH03 回归 | 32项通过；与通知测试分别运行 |
| 二次后端候选边界检查 | 34项通过；更广的临时owner、quiet／daily／unknown／app隔离边界 |
| 最终修复独立复核 | 7项通过；同时间戳、旧schema及 BEGIN IMMEDIATE 先于基线读取 |
| 原隔离前端检查 | 48项通过，TypeScript与Vite构建通过；全请求 route-fulfill 合成数据，业务POST／外部请求均0 |
| 实际公开集成产物复验 | 另次48项通过；直接读取公开worktree构建，四视口和通知交互复核，不能与原48项相加 |

发布集成副本另外重新运行其自身25项通知测试和32项旧回归；测试模块、相对代理fixture与生产导入均来自公开候选，没有注入本地私有QA文件。进程审计禁止 socket／subprocess 和临时目录外 SQLite；测试阶段外部尝试0，无本人数据库读写、真实发送或设备操作。导入时依赖的可选 socket 探针被拒绝并单独记录；旧 ONNX 平台元数据采用合成 stub，没有预测推理。

前端视口为1440×900、1366×600、390×844、320×812；消息区域内部滚动、显式pending按钮、唯一输入、44px热区、404降级、缺时区、通知unknown、3D正常加载/展开与两手机键盘高度近似分别检查。本轮未重新注入WebGL/GLB失败，旧EW降级保留不算本轮48项通过内容。实体手机软键盘和读屏阅读顺序没有验收，CSS视觉顺序不等同DOM阅读顺序。

实际公开构建的主JS为 `index-Dm86mxbl.js`（SHA256 `93dc2594f321fc90fcfa16ac3d17c8fcaa87a861fb9b903e3e092e4d625d6c4d`），CSS为 `index-CuSOnnhj.css`（`9c6c749b0be8a9b00c6db79301030ac1ff80eec5e02191f5dcfc01975b7e9a5a`）。它们不是旧fixture产物；独立复验前后118资产hash保持。主JS归一化静态文件名hash后逐字相同，517条能源自定义CSS相同；其他CSS差异来自不同Tailwind扫描上下文，未据此宣称整份CSS字节相同或全部专业页已回归。

## 复验与冻结来源

在 `Anker_ChargingAndEnergyStorage` 下，先安装既有 requirements；Windows IANA数据依赖已明确声明为已验版本 `tzdata==2025.3`。常规 fake 测试命令如下；本次进程审计与元数据stub属于额外隔离验收，不由这些命令自动提供。

```powershell
$env:PYTHONPATH = "..;.;channels/checks;AnkerProject_DJXZ_Debug;AnkerProject_DJXZ_Debug/Controller"
python -B -m unittest discover -s channels/checks -p test_notifications.py -v
python -B -m unittest test_web_gateway test_decision test_ch03
```

生产候选逐文件取自已验冻结快照，通知与六个UI文件使用独立修复副本，其余七个后端依赖保持作者已验版本。两个关键文件SHA256：

- `assistant/notifications.py`：`13761185c98782fb1bf856c694105cf64dd5f54596e72fa1bfec4814c56d28e3`
- `channels/checks/test_notifications.py`：`d6aeca42da5ba705e89d472dac93d572c948848691c59812824a13141631640d`

15个工作副本源码pin保持，其中12个公开Git blob字节相同；其余3个仅按既有Git规则将CRLF规范为LF，已逐字节证明，Python代理AST也相同。公开blob SHA256为：

| 文件（相对项目目录） | 公开blob SHA256 |
|---|---|
| `frontend/server.py` | `14e0dc86928bf3a0b90fb4da884b7922ce8123156965b12b80a2e07785d47d61` |
| `frontend/src/ConsumerHome.tsx` | `37a58b19f052041fa945ff75d09aaed95f57e6f30f7b1ed8260d53f0e55acf5b` |
| `frontend/src/consumer.css` | `fbabf9c6304f8fe3296e554358a11aec8f1a1a784fb976355e4cb2a87b74dfcd` |

当前工程基线、PR2聊天热修与已净化的Simulator认证默认值保留。环境机 `release.py`／核心server／RuntimeAPI不变；不上传私有配置、本人handoff／数据库／原始收据、视频、node_modules或dist。

## 已知边界与未验部分

- **现实系统时钟回拨仍可能漏提醒。** 合成时钟回拨0.5秒的新事实因 created_at 低于 enabledAt／scan_at 被排除；等时间戳基线修复不解决非单调时间。本次没有增加时钟纠偏机制，不能宣称长期通知可靠性已通过。
- **HTTP200的部分业务拒绝仍保守记为 unknown。** 并非所有SDK业务拒绝都已映射为 failed；unknown保持不重发。SDK sent／accepted不证明户主已读。
- 本机本人库迁移、真实SDK主动消息权限／provider回执／真人看到、实体手机与持续运行均未验；没有代本人开启提醒或确认控制。
- 另一获授权任务曾取得官方HA一次只读采样成功，看板登录超时未重试；App环境机路由在旧运行版仍为404，生产配置仍关闭、远端控制blocked。该采样不等于App全链路接通或远端执行闭环，本次Git发布没有连接环境机。
- 架构图按其既有验收时点保留规划／候选图例，不把本次未部署代码画成已上线实线。
