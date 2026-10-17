# 环境机管线接入 SDD

状态：root 已逐读批准 S0；候选源码与隔离前端构建已通过作者及独立离线验收。见 verification.md。本轮禁止连通测试，默认未启用、未部署。

## 目的

复用队友 Release/Simulator，在现有 App → Assistant/RuntimeAPI → energy_runtime 边界中增加独立的环境机管线入口，保留现用本机 Debug1、资料、旧收据与前端契约。代码准备与离线验证不等于环境机已连通。

## 已确认的差异

1. 新包是客户端；设备读写经官方 HA REST/Bearer，电价、天气、比赛状态经另一个模拟器看板登录。旧 live.py 依赖指定本地 Docker、文件快照、Webhook 和暂停单步。
2. 新协议 charge 是电池总充电功率；discharge 是并网点出口目标。旧 signed target_watts 是电池功率，不能直接用于新放电接口。
3. Release 源码声明 14 kWh/5 kW、三站、5 秒 tick 对应 15 设备分钟；当前本机 5 kWh/2 kW，不能互称同一环境或移用旧实际账单。
4. 新包默认 POST 5xx 重试、缺值归零、未配置时按后缀排序猜站点；App 适配必须拒绝这些不确定性，不能据 HTTP 200 声称已执行。

## 最小范围

- 增加私有配置读取与 ReleasePipeline：显式环境、精确站点映射、严格 HA 状态解析、复用 SolarBank 的实体/协议映射与 HA/看板客户端。
- 适配层在构造时无网络，默认禁用；配置缺项或没有明确启用时在任何 IO 前拒绝。浏览器始终只访问本机 Gateway/API，永不持远端凭据或指定任意 URL/实体/功率。
- 增加现有 loopback runtime 的独立 `/environment/snapshot` 只读工具，通过既有 Assistant RuntimeAPI 可调用。其结果单独标识 `release_official_ha_unverified`，不冒充 local_debug1_sim，不复用旧 decision/actual。
- App 独立 `GET /api/assistant/environment` 走只读 owner/session 鉴权，不授 seen、不清请求/缓存、不变更 decision selection；设置抽屉仅用户主动读取。App/飞书精确问“环境机状态”使用同来源，原本机状态/计划不改。
- 命令准备在纯函数内复用 `command_for_lp`，保留 LP 充电/放电/出口三量及协议语义。控制入口在缺少已核验远端执行契约时明确阻塞；不允许环境机动作混进本机 15 分钟确认卡。
- 仅离线 mock 检查禁用无 IO、私有配置和站点校验、缺值不归零、协议映射、POST 一次/unknown、环境来源隔离。

`ready` 只表示所需 HA 字段完整，不代表环境身份／时间／控制验收；缺项为 partial/null。价格仍 null，未将未知结构的看板输入冒充已对齐预测。当前只准备控制纯映射，所有实际远端控制统一阻塞。

## 控制放行条件

环境机地址/两种认证/后缀映射、官方实体选项与 max、设备时间格式、下一 tick 回读与单槽退出语义，须后续在用户允许连通后逐项核验。当前源码没有能供原 `check_receipt` 接受的逐槽费用凭证，因此不得生成 settled/actual/节省金额。未知控制结果只回查原 request，不重发。

## 排除

不启动 main.py/连续调度，不做三站接管，不改优化器、不加载训练模型、不改本人资料或远端 HA、不重启服务，不合并队友整套主程序，不做网络/健康探测或公网发布。
