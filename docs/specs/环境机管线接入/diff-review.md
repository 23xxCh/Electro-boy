# 环境机 Simulator 离线差异审查

审查日期：2026-10-17。仅读取源码、文件长度与 SHA256；未 import 业务模块，未启动 main、服务、Docker、HA、模拟器或任何 HTTP／socket 连通测试。本文只覆盖 Simulator 客户端及与现 App／Agent 调用边界有关的源码，不能代表远端环境已可用。

## 结论

用户提供的 `Anker_ChargingAndEnergyStorage/DJXZ/AnkerProject_DJXZ_Release/Simulator` 是环境机客户端；不是一个新的 HA 插件，也不是环境机服务端。其 6 个 Python 源文件中，5 个与旧 Release、旧 Debug 的同名 Simulator 源文件逐字节相同。唯一变化是 `solarbank.py` 的预测时域截断：只用今天剩余＋明天已提供的电价／天气，不再越界或外推到后天。

因此，“拿到了新管线”不等于“App 已接上环境机”。现 `energy_runtime/server.py:29` 固定实例化 `Runtime(LocalHA(), ...)`，`LocalHA` 仍依赖本机 Docker、私有 HA 快照、paused clock 和本机 VE 结算 API。应复用已有 Simulator 客户端，新增受控环境机适配边界，并保留现 Web／飞书 owner、持久记忆、双确认和 episode 状态机。不能把新目录整体覆盖旧模型、controller、main，也不能用环境机 HTTP 200 代替实际回读。

## 精确文件差异

本表比较 `.py` 源文件；Debug 已有的 `__pycache__` 属于生成物，不作为功能变化，不迁移。

| 文件 | 新文件 bytes | 新文件 SHA256 | 与旧 Release／Debug 比较 |
| --- | ---: | --- | --- |
| `__init__.py` | 736 | `8A70AA098791504E5BB70E101FCC098D05DF40490FFB39FD077119B3FD73AAB9` | 两者相同 |
| `config.py` | 1656 | `BCD205C6744925889BCA97331C00D3E0F280A9283E30C830B734DB70171033AB` | 两者相同 |
| `ha_client.py` | 3327 | `9060AD2A86BF7E1EFBAAD431320680C465A7C35271C84A8CE6ABF6E1891DBFD8` | 两者相同 |
| `protocol.py` | 2847 | `12511E28DB68774040CACD4BC199FDC3AB11C5916528837B7F118345566D41CE` | 两者相同 |
| `sim_api.py` | 4544 | `068C84B7401F52E977D163DBB7A25EBB6DBA0F632A7FC2EC57C62641DE6C767E` | 两者相同 |
| `solarbank.py` | 12681 | `F0500AB88DA5B7A5A04395C264BFE75B26CECA8EA0597840834EC2D58CB970DD` | 唯一源码差异 |

旧 Release `solarbank.py`：12308 bytes，SHA256 `2438A0EE46AA6685478E7651C6F36F7729A06CA4FD0883E3A3A6653DEC6B5BC5`。

旧 Debug `solarbank.py`：SHA256 `3FFDC73C3B2C99AC8B5A1274BAD7E445FAC08740C3441C00E3BD2223FDF0542A`。

唯一 diff 位于新 `solarbank.py:184`：

```python
eff_h = min(horizon_hours, len(price_pts) - hi)
for k in range(eff_h):
    idx = hi + k
```

- 旧 Release：`range(horizon_hours)`、`idx = hi + k`，超出今明可用点后可能索引越界。
- 旧 Debug：`range(horizon_hours)`、`idx = min(hi + k, len(price_pts) - 1)`，超窗时重复最后一点，形成后天外推。
- 新包：返回长度允许少于 `horizon_hours`。完整 48 点且设备时间 23 点时，最长为 25 小时；请求 24 小时保持 24 点，请求 48 小时变成 25 点。
- 本变更只按电价长度截断；未新增天气各字段长度一致性、非有限数、空时域校验。`build_lp_forecast` docstring 仍称“每项 horizon_hours 点”，已与可变长度行为不完全一致。
- 该方法输出逐时点，`upsample_hourly` 每点重复 4 次。分钟偏移没有在该函数处理，15／30／45 分的首小时对齐应在环境 adapter 中明确，不能借用本机 runtime 的另一套预测时间规则。

新 DJXZ 包的 `main.py` 有 DPV／DM 及多数组时域对齐逻辑，旧 Release main 的相关实现不同；这是 Simulator 之外的范围，不是本次“只差这几行”的结论对象，不做全包替换建议。

## 可复用接口与单位

| 接口 | 源码位置 | 契约 |
| --- | --- | --- |
| `SolarBank(cfg: SimConfig)` | `solarbank.py:46` | 构造 HA 与看板客户端；构造本身没有调用 discover／login／apply |
| `discover_sites() -> dict[int, str]` | `solarbank.py:55` | 从 HA SOC entity 发现设备后缀，显式 `site_suffixes` 优先；缺省按后缀字典序分配，不能作为真实站点身份依据 |
| `read_state(site_no) -> SiteState` | `solarbank.py:86` | 7 个数值字段＋device_time；W→kW，SOC 百分数→0..1；此单站方法的 mode 没有实读，沿用 dataclass 默认值 |
| `read_all_states() -> dict` | `solarbank.py:101` | 一次 `/api/states` 组装站点状态，额外读取 operating mode；缺失／不能解析的数值被填 0，适配器必须防止“无数据”被当作真实 0 |
| `build_lp_forecast(site_no, horizon_hours=24, now=None)` | `solarbank.py:164` | `(buy,sell,irr,cloud,temp,hours,dows)` 七个逐时数组；价格单位 €/kWh；天气字段分别 W/m²、%、°C；无 now 时读取设备时间 |
| `build_day_weather(site_no)` | `solarbank.py:199` | 今／明两天按自然日返回 points，供已有预测器使用 |
| `command_for_lp(charge_kw, discharge_kw, export_kw, max_power_w=5000.0, sell_now=None) -> Command` | `protocol.py:27` | 非负 kW 解→方向＋非负 W 的设备命令；充电优先，负卖价不放电／不上网，设备能力仍需按目标环境核实 |
| `apply_command(site_no, cmd) -> None` | `solarbank.py:146` | 切第三方控制→方向→功率三实体服务；内部未返回可验证物理 receipt |

`SiteState.charging_kw`、`discharging_kw`、`grid_import_kw`、`grid_export_kw` 是分开的非负测量量；`Command.power_control_w` 是目标量，两者不可互相冒充。`device_time` 用无时区的 `%Y-%m-%d %H:%M:%S` 解析；源码不能证明真实环境时区或真实 tick 已推进。

### 放电语义是接入阻断点

这是队友协议的源码契约，尚未据远端回读验证：

- `charge`：`power_control_w = charge_kw * 1000`，表示电池总充电目标，光伏优先，电网补足。
- `discharge`：`power_control_w = export_kw * 1000`，表示并网点出口目标，**不是电池放电功率**。
- `discharge_kw > 0` 且 `export_kw == 0`：该协议返回待机；不能表示“电池只补家庭负荷而不上网”。
- `hold` 待机：保持第三方模式，`charge`＋`0 W`；`self_consumption` 待机选项会切回自发自用，设备可能继续自主充放电，不能叫作零功率停止。

现 `energy_runtime/runtime.py:19` 的 `target_watts` 使用“负数=电池充电、正数=电池放电”并将正值取自 `discharge_kw`。把该正值直接送进环境机的 discharge 控制会改变动作含义。环境 adapter 必须根据同一 LP 行的 `charge_kw / discharge_kw / export_kw` 调用 `command_for_lp`，同时校验动作是否可表达；不可表达时拒绝或重新求解有设备约束的方案，不能悄悄把计划放电改成待机并声称按原计划执行。

## HA 与看板数据路径

以下为源码声明的相对路径与固定协议，不包含任何现场地址或认证值。

| 通道 | 源码路径 | 用途 |
| --- | --- | --- |
| HA REST | `GET /api/states`，`GET /api/states/<entity_id>` | 设备状态和控件状态读取；HA Bearer 令牌 |
| HA REST | `POST /api/services/select/select_option` | `operating_mode_device_runs_by_command_in_third_party_controlled` 设为 `third_party_control`，以及 `grid_flow` 设为 charge／discharge |
| HA REST | `POST /api/services/number/set_value` | `power_control` 目标 W |
| 看板 API | `POST /api/auth/login` | 看板独立账号换 Bearer；与 HA 令牌不通用 |
| 看板 API | `GET /api/price/today`、`/api/price/tomorrow`、`/api/price/current` | 公布电价 |
| 看板 API | `GET /api/weather/today?site=N`、`/api/weather/tomorrow?site=N` | 各站天气预报 |
| 看板 API | `GET /api/contest/history?site=N&format=json`、`/api/contest` | 比赛历史与阶段／时钟 |
| 看板 API | `GET /api/telemetry?site=N` | 单站遥测和 revenue；源码提到 settle_price，但没有在此客户端形成 episode 单槽结算证明 |

设备 entity 按 `domain.<device_prefix>_<suffix>_<func>` 构造。正确后缀必须在配置中显式绑定站点，不从 UI 文本、LLM 输出或字典排序猜测。当前 `config.py` 含认证默认字段；接入配置只接受私有服务端提供的值，不复制仓库默认账号／密码到新说明或浏览器，不把 token、现场地址、返回原文放入日志。

## 重试、测量与实际回读的缺口

新包与旧包完全相同，没有修复以下行为：

1. `HaClient._get/_post` 默认各循环最多 3 次。401 立即报错；5xx 退避 1、2 秒；其他非成功状态也继续下一轮，只是不退避。网络异常不在循环内捕获，会直接向上传播。尤其写请求 5xx／部分 4xx 的重发可能重复设备写入；适配器必须限制每步写最多一次，失败／超时记为 unknown 并回读原 episode，不能泛用自动重试。
2. `ensure_third_party` 收到服务返回后就缓存“已切换”，只固定 sleep 1 秒，未证实 mode 生效；`apply_command` 三步中途失败存在部分生效可能。两者都没有 requestId、持久命令 journal、owner 检查或安全收据。
3. `read_all_states` 缺数值填 0、不验证时间一致性／非有限数，`read_state` 也不验证同批次时间；不能作为控制许可或 UI“最新真实 0 W”的唯一依据。
4. 看板 `_get` 缓存 15 秒，401 后可重新登录。读遥测与历史若走此缓存，不能简单用于刚下发后的 nexttick 完成判定；要明确强制新鲜读取与时间边界。
5. `apply_command` 返回 None；调用成功仅证明相应 HTTP 服务返回，不能证明下一 tick 电池功率、SOC 或费用。`main.py` 的报告也不能替代新 App episode 的独立下发后回读。

## 与当前 App／Agent 链路的区别

现有调用方向可复用：

```text
Web 同源 /api/assistant/* 或飞书 Worker
  → 同一 assistant Service／持久记忆／owner／显式确认
  → assistant/runtime_tools.py → RuntimeAPI
  → energy_runtime Runtime／preview／proposal／confirm／reconcile
  → 当前 LocalHA（仅本机 Debug1）
```

| 边界 | 当前本机 `LocalHA` | 用户提供的环境机 Simulator |
| --- | --- | --- |
| 设备入口 | 现有本机 HA webhook，随后官方插件 | 官方 HA REST 的 select／number 服务 |
| 状态入口 | HA 私有导出文件＋本机 VE | HA `/api/states`＋看板遥测／历史 |
| 时钟 | Docker 内主动 `pause/step`，确认单15分钟 | 源码注释称环境下一 tick 执行；客户端没有主动 step API |
| 身份 | 固定本机容器镜像／挂载／官方插件／3个本地设备 | 需要配置设备后缀与真实环境身份；不能借用本机 identity |
| 计划动作 | 电池 signed W | 方向＋充电电池 W／放电出口 W |
| 完成证据 | 目标回读＋下一槽 HA＋VE 精确物理／费用＋其他站未改 | 原客户端未实现完整 episode 证据 |
| 环境标识 | `local_debug1_sim`，`check_receipt` 也固定此值 | 必须独立标识与收据验证，不能伪装 local Debug1 |

`energy_runtime/live.py:61` 的本机身份核验、`snapshot`、`execute`、`collect_receipt` 和 `stop` 都不能用环境机地址简单替换。特别是本机 `/api/state`／`/api/meta`／`/api/sim` 与队友看板 `/api/telemetry`／`/api/contest` 不是同一 schema；“stop”也不能调用远端不存在的本机 clock。

## 最小更新建议与离线验收边界

1. 只吸收 `solarbank.py` 的有效时域截断，并为返回长度、天气长度和空时域补离线契约检查；不搬 `__pycache__`，不替换模型／controller／main。
2. 在 core 服务端增加显式的环境机 adapter／配置选择，默认仍本机。环境机地址、HA token、看板账号、站点映射、功率／容量及开关放私有配置；配置不完整直接 blocked，不自动发现后排序控制。
3. 两端继续调用同一 RuntimeTools、同一已绑定 owner 与同一 SQLite 记忆。新增环境必须拥有独立 episode／环境标识；旧本机 pending／episode 不允许跨环境执行，Web／飞书控制仍走另一次明确确认。
4. 环境 adapter 复用队友客户端的状态／预测 API、entity 构造和 `command_for_lp`，在边界严格验证数字、时间／槽、身份与可表达动作。写请求一次、事先持久 journal；确认前后对齐同一源状态，unknown 不重发。
5. 完整回执需要先定义远端官方 HA 控件回读、下一设备时间／物理读数、实际成本的来源字段与精度。缺结算源时只可给 observed／unknown，不生成本机风格的 settled／outcome 或节省金额。
6. 本轮只做静态／纯内存 mock：拒绝配置缺项、签名／单位、charge／discharge／standby 映射、禁止自动 POST 重试、缺状态不填0、过期／错owner／错环境拒绝、partial write→unknown、下发HTTP200不能直接settled、旧本机行为不回归。所有 transport 注入假对象，禁止创建真实网络／socket／HA／Docker 连接。

未做远端认证、实体发现、插件／固件核对、价格天气 JSON 实测、时钟连通或下发测试。源码中关于下一 tick、5秒／180倍及第三方模式行为只能作为待验证契约，不能写成已经观察到的事实。
