# Anker 充电储能赛道 · VE + Controller 双节点架构

三部分：**Controller（调度器）· VirtualEnv（VE 模拟器）· VisionPanel（看板）**。
VE 是环境，Controller 是可替换的调度算法，VisionPanel 是展示层。调度器可扩展为多个
（LP / DRL / PSO …），但**同一时间只允许一个外部调度器运行**（基线调度器始终隐含存在）。

```
VE/              VE 节点：3 站点模拟器 + Modbus/HTTP/HA 通信 + 能流/收益数据接口（不含任何调度器代码）
Controller_LP/   LP 调度器节点：读状态→LP(ONNX)求解→下发控制 + 决策解释报告（不含 VE 内部代码）
README.md        本文件（打包/启动/接口/开发要点）
```

---

## 1. 启动流程（两节点独立启动）

> 依赖 conda 环境 `env_ankerproject`（VE 只需 numpy；Controller 需 numpy+scipy+onnxruntime）。

**第 1 步：启动 VE 节点**

```powershell
conda activate env_ankerproject
cd VE
python run.py --days 7 --speed 180   # --days 省略=无限；--speed=倍速；--granularity=时隙粒度min
```

启动后：内置看板 http://127.0.0.1:45678/ ，模拟 HA REST http://127.0.0.1:8123/api/states ，
Modbus 设备 127.0.0.1:1502/1503/1504。

**第 2 步：启动 Controller 节点（另开一个终端）**

```powershell
conda activate env_ankerproject
cd Controller_LP
python node.py --ve http://127.0.0.1:45678 --ha http://127.0.0.1:8123 --name Controller_LP
```

> 若还没导出 ONNX，先跑一次 `python convert_onnx.py`（把 .pt 权重转 .onnx）。

---

## 2. 数据流与三个接口

```
Controller/node.py --读状态/读参数(经HA)·读预报(经VE HTTP)·写动作(经HA)-->  VE/run.py
VisionPanel      --读 /api/vp（能流+收益）·读 /api/states（状态）-->  VE
```

| 接口 | 提供方 | 说明 |
|---|---|---|
| HA 实体（soc/solar/load/rated_energy/…） | VE 模拟 HA REST(:8123) / 真实 HA | 设备状态 + 参数 + 控制写入 |
| `/api/lp_forecast`、`/api/state`、`/api/vp` | VE HTTP(:45678) | 预报 / 状态 / 能流收益 |
| Modbus TCP(:1502/1503/1504) | VE | 官方 HA 插件接入（复用官方寄存器表） |

---

## 3. VisionPanel 数据接口（`GET /api/vp?site=N`）

VP 需要三类数据：**经济收益、能量流向、调度解释**。`/api/vp` 提供前两类（解释类在 Controller 的
XML 报告里）。

**与经济收益相关（5 个指标）**：

| 字段 | 含义 | 公式 |
|---|---|---|
| `total_revenue_r_eur` | 累计总收益 R | 卖电收入 + 自发自用节省 |
| `settled_revenue_eur` | 结算后收益 R_settled | 基准 K − 实际电费（官方 ΔR_settled） |
| `buy_cost_eur` | 累计买电支出 | Σ p_imp × buy × Δt |
| `sell_revenue_eur` | 累计卖电收入 | Σ p_exp × sell × Δt |
| `saving_rate_pct` | 本站结算节省率 | R_settled / K × 100% |

**与能量流向相关（电功率转移矩阵 + SOC）**：

四节点 `solar / load / grid / battery`，7 条有向边（单位 W）：

```
solar → load / battery / grid        （光伏：3 出边）
grid  → load / battery               （电网：2 出边；solar→grid、battery→grid 为 2 入边）
battery → load / grid                （电池：2 出边；solar→battery、grid→battery 为 2 入边）
```

`flow` 字段：`solar_to_load / solar_to_battery / solar_to_grid / grid_to_load /
grid_to_battery / battery_to_load / battery_to_grid`，外加 `curtail`（弃光）与
`charge_loss`（充电损耗）。系统外边的能量（光伏涌现、负载泯灭、损耗、弃光）在看板默认不画。

---

## 4. 调度器限制（同一时间只允许一个）

- `POST /api/scheduler/register {name}` 会**拒绝**第二个不同名调度器（HTTP 409）；
- `POST /api/scheduler/unregister {name}` 释放名额；
- 同时只能有 **1 个外部调度器**（基线除外）；切换先停旧的再启新的。

---

## 5. 决策解释报告（XML）与绘图

Controller 每时隙、每站点写一份 XML 报告，按**运行次数**分目录：

```
Controller_LP/report/
├── plot_report.py      # 绘图脚本（默认最新一次运行）
├── 1/                  # 第 1 次运行
│   ├── site_1/slot_000001.xml ...
│   ├── site_2/  site_3/
│   └── plot/decision_curve.svg  gain_curve.svg
└── 2/ ...
```

**命名规则**：`report/<第N次运行>/<站点>/slot_<六位补零>.xml`（字典序=时间序）。

XML 里解释决策的数据：`state`（soc/光伏/负载/容量）、`prices`（当前+未来买/卖价）、
`action`（充/放/净功率/预期成本）、`energy_flow`（7 条能流边）、`revenue`（5 个收益指标）、
`explanation`（影子价格 `shadow_price_storage/energy` + 一句人话 `reason`）。

**绘图**：

```powershell
python report/plot_report.py                    # 最新 run，site_1
python report/plot_report.py --run 1 --site site_2
# 输出 <run>/plot/decision_curve.svg（4 子图：买卖电功率/电价/SOC/充放电）
#            gain_curve.svg（3 子图：R vs R_settled / 买电支出 vs 卖电收入 / 节省率）
```

---

## 6. 预测器 pt→ONNX（去掉 torch 依赖）

调度器只用**预训练好的模型**做推理，用 ONNX + onnxruntime 更轻量，release 不需要 torch。

```powershell
python convert_onnx.py    # 一次性：把 Weather/Load_Predicter/*.pt 导出为 *.onnx
```

`model_lp.py` 已改为 `onnxruntime` 推理（`LPModel` 加载 .onnx，无 torch import）。

**release 可删除（不再需要）**：

- `Weather_Pv_Predicter/Train.py`、`Trainer.py`、`Test.py`、`*.pt`
- `Load_Predicter/Train.py`、`Trainer.py`、`Test.py`、`*.pt`
- `training_data.csv`
- `convert_onnx.py`（导出完即可删；保留也行）

**必须保留**：`node.py`、`Controller.py`、`site_params.py`、`model_lp.py`、
`Weather_Pv_Predicter/*.onnx`、`Load_Predicter/*.onnx`。

---

## 7. 打包成 exe（PyInstaller）

打包前激活环境（`%CONDA_PREFIX%` = env_ankerproject）。MKL DLL 收集方式见下面命令。

### 7.1 VE（numpy，较小）

```powershell
cd VE
pyinstaller -D -n VE_Anker --clean --noconfirm --paths . `
  --add-data "web;web" `
  --add-binary "%CONDA_PREFIX%\Library\bin\mkl*.dll;." `
  --add-binary "%CONDA_PREFIX%\Library\bin\libiomp5md.dll;." `
  run.py
```

### 7.2 Controller_LP（numpy + scipy + onnxruntime，无需 torch，体量可控）

```powershell
cd Controller_LP
pyinstaller -D -n Controller_LP --clean --noconfirm --paths . `
  --add-data "Weather_Pv_Predicter;Weather_Pv_Predicter" `
  --add-data "Load_Predicter;Load_Predicter" `
  --add-binary "%CONDA_PREFIX%\Library\bin\mkl*.dll;." `
  --add-binary "%CONDA_PREFIX%\Library\bin\libiomp5md.dll;." `
  node.py
```

> `--add-data` 把 `*.onnx` 打进 exe。若通配不展开就逐个 `--add-binary`；
> 漏了 MKL 会报 `Cannot load mkl_intel_thread.2.dll`。发布时拷贝整个 `dist/xxx` 文件夹。

---

## 8. Controller 应用其它算法的开发要点

架构与算法无关。新建 `Controller_DRL/`、`Controller_PSO/` 等，只需遵守同一套**节点协议**：

1. **注册**：`POST /api/scheduler/register {name}`（受单调度器限制，被拒则退出）；
2. **读状态/参数**：经 HA（`sensor.<设备>_soc/_solar_power/_home_load/_rated_energy` +
   `number.<设备>_battery_power_setpoint` 的 attributes）；
3. **读预报**：经 VE HTTP `/api/lp_forecast`（或自己组合 `/api/price` + `/api/weather` 上采样）；
4. **算动作**：把算法包装成 `state X → action Y`（负=充/正=放，W），与 LP 同接口；
5. **写动作**：经 HA `POST /api/services/number/set_value`；
6. **写报告**：每时隙输出一份 XML（复用 `write_report` 的格式/命名规则，供 RAG + plot_report）；
7. **注销**：退出时 `POST /api/scheduler/unregister {name}`。

**不需要向 VE 注册额外的"Node 类型"**——VE 只认"当前调度器名"，算法换成 DRL/PSO 对 VE 完全透明。
唯一约束：同一时间只能跑一个外部调度器。

---

## 9. 口径与单位

- 内部 kW / kWh / €/kWh / SOC∈[0,1]；对外（Modbus/HA/HTTP）功率 **W**、SOC **%**。
- 双粒度：控制/仿真 = `granularity_min`（默认 15min）；天气/电价 = 60min 逐时（24 点/天）。
- 电池 η_c=0.95（充侧）、η_d=1.0；负卖价硬约束"不放电/不上网"。
- 评分：`saving_settled_percent = Σ(K_i − cost_i)/ΣK_i × 100%`，`K_i`=负载按买价全买电的基准。

---

## 附录：三站 Home Assistant 看板（Lovelace）

[lovelace-anker-demo.yaml](lovelace-anker-demo.yaml) 是面向三台 SOLIX Solarbank Max AC 模拟器的 Lovelace 配置，包含总览、三个站点详情、电价和调度决策六个视图。它只负责展示；设备控制仍须走官方 HA 插件。

## 当前状态

- YAML 中有 34 个**待替换实体 ID**，没有假定官方插件的实体名。
- 排名分取模拟器总览的 `saving_settled_percent`；金额和相对原生自发自用的改善率需要独立结算数据，尚未实测。
- 浏览器预览使用明确标注的示例值，其 HA 认证、数据库和示例数据不在本仓库。

## 接入

1. 使用 Home Assistant Core 2026.2 或更新版本，并通过 HACS 安装 **ApexCharts Card**。分布卡要求同一卡内实体属于相同 domain 和 `device_class`；现场核对官方插件的功率实体。
2. 将 YAML 中 34 个占位符替换为实际 HA 实体 ID：每站 7 个设备读数，以及共享电价、各站天气、官方排名分、结算对照和各站决策等展示实体。外部程序把模拟器 API 与调度结果写入**专用展示实体**；属性格式见 YAML 文件顶部注释。
3. 在 Lovelace「原始配置编辑器」粘贴替换后的配置。接入真实设备和模拟器后，再核对刷新、单位、充放电方向、负售电价提示、过期指令和结算口径。

占位符未替换前，这份配置不会显示真实数据，也不能当作赛站验收结果。
接入还需要三站实际实体 ID、模拟器电价/天气 API 的 JSON 示例、总览分数获取方式，以及调度程序写入 HA 的实体与属性约定。

参考：[HA Dashboards](https://www.home-assistant.io/dashboards/) · [Distribution card](https://www.home-assistant.io/dashboards/distribution/) · [ApexCharts Card](https://github.com/RomRider/apexcharts-card)
