# 电击小子当前系统架构（2026-10-17）

这次只按现有源码和已保存的验收证据校正架构图，不修改生产代码，不为匹配旧图改系统。保留原稿的米白、深绿与四列布局，将已实现主链、离线代码候选、规划能力分开。

- [新版可编辑 SVG](current-system-20261017.svg)
- [新版 PNG](current-system-20261017.png)
- [原 SVG](everywatt-original.svg) 保留不改。

## 源码事实与改图依据

所有源码路径以下均相对 `Anker_ChargingAndEnergyStorage/`。绿色表示已实现且有既有本机验收记录；不表示当前服务在线或真实家庭连续自治。本轮没有运行服务、HTTP、HA、环境机、模型或飞书连通测试。

| 图中节点或连线 | 源码 / 既有验收依据 | 边界 |
|---|---|---|
| Web 与飞书共用一个管家 | `channels/feishu.py:149-163` 构建同一个 `Service`、`WebGateway` 和单线程业务执行器；`assistant/web_gateway.py:1`、`:29-40` | 已有飞书入站与 SDK 回复 API 成功记录；不能代签户主已读或真人确认控制。微信未接入。 |
| Service → RuntimeTools → RuntimeAPI → Runtime | `assistant/service.py:29-37`、`:61-92`；`assistant/runtime_tools.py:11-16`；`assistant/runtime_api.py:24-39`、`:97-118`；`energy_runtime/server.py:24-33`；`AnkerProject_DJXZ_Debug/main_local.py:1-22` | `main_local.py` 是独立离线工具，不是 App 入口。 |
| 持久记忆、版本、方案与短期 feed | `assistant/store.py:10-41`、`:101-108`、`:136-164`；`assistant/memory_v2.py:110-155`、`:168-194`；`assistant/web_gateway.py:210-239` | SQLite 持久资料与最近10分钟交互缓存分开；按绑定户主隔离，关键词与时窗检索上下文。 |
| 资料保存与设备执行分两次确认 | `assistant/service.py:17-18`、`:73-75`、`:104-122`；`assistant/runtime_tools.py:213` 起 `propose`；`energy_runtime/runtime.py:356-404` | `记下 <资料号>` 保存后生成新方案，`执行 <方案号>` 才执行首15分钟；裸“确认”不自动选 pending。 |
| 只有无日期 reserve_soc 进入当前 LP 硬约束 | `assistant/service.py:196-241`；`energy_runtime/runtime.py:39-76`；`assistant/memory_v2.py` 的 effective constraints | 活动、习惯等可持久保存为上下文；不是任意自然语言偏好都已成为可执行约束。 |
| PV LSTM + DM → HiGHS LP | `energy_runtime/live.py:166-258`：加载 `PvLstmPredictor`、`DmLoadPredictor` 和 `.pt` 权重；复用 `AnkerProject_DJXZ_Debug/Controller/Controller_LP_costminTree/household_plan.py:15`、`:424` 的 HiGHS 求解 | 未使用旧图写的 PV ONNX。无历史实测天气，PV LMS 明确禁用，不训练新模型。 |
| 当前到次日末，最多48小时 | `energy_runtime/live.py:133-141`；`:210-224`；`energy_runtime/README.md:23` | 规划时域随当前时刻缩短，不承诺固定24h或每次完整48h；仅首15分钟另次确认执行。 |
| LocalHA → 官方 HA 插件 → 本机 Debug 仿真 | `energy_runtime/live.py:62-117`、`:267-353`；`energy_runtime/runtime.py:356-413`；`checks/ew01/browser-live-audit.json` | 本机 `site_1` 单时段闭环，非远端 Release 或真实家庭设备；LP 不绕过确认直连 HA。 |
| next-tick、实际回读与单槽结算收据 | `energy_runtime/runtime.py:104-125`、`:406-449`；`energy_runtime/live.py:322-362`；`checks/ew01/browser-live-core-receipt.json`、`browser-live-ui.json` | 同一 episode 核对功率、SOC 与15分钟净电费；全窗预计费用不是已发生收益。unknown 沿原 ID 只读核对，不自动重发。 |
| 独立环境机只读代码候选 | `energy_runtime/release.py:76-95`、`:128-177`；`assistant/runtime_api.py:76-81`；`assistant/runtime_tools.py:20-21`、`:82-101`；`assistant/web_gateway.py:262-274` | 复用 `DJXZ/AnkerProject_DJXZ_Release/Simulator`；默认关闭、未部署、未连通；身份与时间未验，远端控制恒 blocked。 |
| App 候选读取入口 | `frontend/src/assistantWeb.ts` 的 `environment` 调用；`frontend/src/ConsumerHome.tsx` 的环境机设置抽屉；`checks/environment-view/README.md` | 只在用户主动读取时请求，不混本机 HUD、decision 或执行卡；候选 dist 未替换 served dist。 |
| 主动通知、能源偏差监控、自动重规划、挑战 Agent | 本轮核对的 Service、RuntimeTools、RuntimeAPI、Runtime 与飞书渠道代码没有接入这些能源业务能力 | 灰色虚线规划区。渠道在线状态 monitor 存在，但不是能源偏差检测 / 自动重规划。没有证据支持旧图“挑战 Agent 已有离线原型”。 |

## 与旧图的主要差别

1. 聊天入口从“待接入”改为“飞书已接入”，微信仍为扩展项。
2. 将 `main_local.py` 从统一入口移到离线支路，主链改为共享 Service 与受限 Runtime 工具。
3. PV ONNX／固定24h 改为实际 PV LSTM＋DM、HiGHS LP 和变长至次日末的时域。
4. 显式画出资料确认和设备另次确认，避免把保存偏好当作设备动作。
5. 本机 HA／Debug 单槽闭环从待接改为已有本机验收；同时把远端环境机候选独立隔离。
6. 记忆、版本、方案、执行证据及短期 feed 加入图中；预计与实际不混算。
7. 主动通知、能源偏差监控、自动重规划和挑战 Agent 统一放入规划区，不用已完成箭头连接。

## 验收与限制

独立导出审查已生成 2 倍 PNG（3360×2660），111 个文字节点、最小字号12px；外部请求、文字互撞、画布裁剪、卡片文字越界、绿色回流线穿过规划卡均为0。源 SVG 为 UTF-8、无外部图片／字体／脚本，文字可编辑。首版回流横线穿过规划卡的 P1 已修复，并重新导出；当前 SVG 和 PNG 对应同一冻结版本。离线渲染检查记录保留于本地，未随公共副本上传。

| 文件 | SHA256 |
|---|---|
| 新版 SVG | `c323ffa2dd700dd6b0ee7477aa203c74c85f7dcf63d4afd8610815e9d31a6690` |
| 新版 PNG | `069386a1928c93fc1c7084062d6c972f2ce16828a53729486d3a3b0f36b92eb9` |
| 原 SVG（保留不改） | `4b070f321b086aa3447a84892bf11fe37e53ead470a85cc13d8c51afaa302f3a` |

现有本机闭环和渠道验收依据保留于本地，没有在本轮重跑；公共仓库不上传本人资料或原始执行收据。离线图形渲染不等于运行服务、设备或收益验收。

独立审查的12项源码事实、7项图形结构检查、20个生产文件 SHA 保留和原稿保留均通过，最终 PNG 已由导出 agent、独立 QA 和 root 目视核对。独立验收、事实核对及最终文件校验记录保留于本地，未随公共副本上传。
