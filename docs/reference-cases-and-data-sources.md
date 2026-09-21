# Anker 黑客松 · 充电储能赛道 — 参考案例与数据源清单

> 整理自:《9月9日赛题解析直播资料包-充电储能赛道》(飞书文档)及前期调研。
> 更新日期:2026-09-21。所有 API 已实测验证可用。

---

## 一、官方参考案例(文档第 10 节《参考案例 / 灵感方向》)

### 1. 行业标杆产品

| 对象 | 地址 | 看什么 |
|---|---|---|
| Home Assistant 能源面板(Energy Dashboard) | https://www.home-assistant.io/docs/energy/ | 电网友/光伏/电池三层流图的可视化组织方式(→ 看板选型) |
| aWATTar(动态电价,德国/奥地利) | https://www.awattar.com | "便宜充电、贵时放电"的调度体验(→ 解释层话术) |
| Tibber(动态电价 App) | https://tibber.com | 同上,其"智能充电"解释文案值得参考 |

### 2. 社区灵感来源

- HA 社区论坛搜 **"anker solix automation"**:
  https://community.home-assistant.io/search?q=anker%20solix%20automation
  (也可在 Home Assistant 官方 Discord 的 #energy 频道交流)
- 关注点:真实用户的调度规则长什么样、踩过什么坑(→ 启发式规则起点)

### 3. 技术文章 / 开源项目

| 项目 | 地址 | 备注 |
|---|---|---|
| 官方插件仓库 | https://github.com/anker-charging/ha-anker-solix-official | **赛题指定核心资料**。README 的 FAQ 部分讲了 App 与 HA 控制优先级、写延迟等,出题方点名阅读 |
| 社区版插件 | https://github.com/thomluther/ha-anker-solix | 1000+ star,只做监控+简单调度,可对照找差异化空间 |

---

## 二、原始材料地址

| 材料 | 地址 |
|---|---|
| 大赛官网(充电储能赛道详情) | https://career.anker.com.cn/hackathon/ |
| 9.9 赛题解析直播资料包(飞书 Wiki) | https://anker-in.feishu.cn/wiki/EfeZwcu1biJ2wgkS8Wlc5kS5nme |
| 参赛报名表 | https://anker-in.feishu.cn/share/base/form/shrcnyFAWjMEOrM9o7h4d1oXrAe |

---

## 三、预赛公开数据源(已实测,全部免 key)

> 入围前拿不到 training-data.zip 和模拟器环境,预赛材料用以下公开数据先跑通管道并验证方案;
> 入围后切换到模拟器自带接口(见第四节)。

### 1. 天气 / 辐照度预测 — Open-Meteo ✅ 已实测

免 key、免注册,免费额度 10,000 次/天(非商用)。

**逐小时辐照预报(7 天,含今天)** — 对应模拟器天气预测接口的替代:

```
https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&hourly=shortwave_radiation,direct_radiation,diffuse_radiation,cloud_cover,temperature,wind_speed_10m&forecast_days=7&timezone=Europe/Berlin
```

**历史辐照(回测/校准预测模型,任意日期段)**:

```
https://archive-api.open-meteo.com/v1/archive?latitude=52.52&longitude=13.41&start_date=2026-06-01&end_date=2026-09-20&hourly=shortwave_radiation,direct_radiation,diffuse_radiation&timezone=Europe/Berlin
```

| 变量 | 单位 | 用途 |
|---|---|---|
| `shortwave_radiation` | W/m² | 总辐照 → 光伏出力预测主输入 |
| `direct_radiation` | W/m² | 直射分量(面板朝向建模) |
| `diffuse_radiation` | W/m² | 散射分量(阴天时更重要) |
| `cloud_cover` | % | 辅助特征 |
| `temperature` / `wind_speed_10m` | °C / km/h | 电池板温度修正(可选) |

- 官方文档:https://open-meteo.com/en/docs(预报)/ https://open-meteo.com/en/docs/historical-weather-api(历史)
- **坐标即"站点"**:3 个站按各自经纬度分别请求,等价于模拟器的 `?site=1|2|3`;预赛演示可任选德国城市(如柏林 52.52, 13.41)
- 光伏出力换算基线:`pv_power ≈ 装机容量 × (shortwave_radiation / 1000) × PR(PR 先取 0.8 试)`

### 2. 德国动态电价 — aWATTar ✅ 已实测

免 key、免注册。端点:

```
https://api.awattar.de/v1/marketdata?start=<毫秒时间戳>&end=<毫秒时间戳>
```

实测结果(2026-09-21):

- 当前 + 次日 24h 价格可用(示例日内价差 8 倍+,€28.58 ~ €242.98/MWh)
- **90 天历史一次拉取成功**:2159 个小时级数据点,其中 **169 小时负电价(~7.8%)** —— 直接支撑官方场景"负电价绝不卖电"的权重
- 电价 API 文档:https://www.awattar.com/api

### 3. 备选数据源(暂不需要)

| 来源 | 地址 | 说明 |
|---|---|---|
| Tibber API | https://developer.tibber.com/ | 需注册账号拿 token,预赛阶段非必需 |
| SolCast | https://solcast.com | 光伏发电预测 API,备选 |
| Energidata | https://www.energidataservice.dk | 丹麦动态电价,备选 |

---

## 四、正式赛数据来源(入围后,无独立公开地址)

| 数据 | 来源 | 说明 |
|---|---|---|
| 电价(3 站共享,含真实负电价时段) | 模拟器 HTTP API `:45678` | EC2 环境内置 |
| **按站点天气/辐照度预测** | 模拟器 HTTP API `:45678`,参数 `?site=1\|2\|3` | 官方自带,无需第三方天气 API |
| training-data.zip(100 练习站点 × 90 天) | 入围后 48h 内随工具包从 EC2 下载 | 无公开下载地址 |
| 云端 LLM 接口 | 官方提供额度 | 解释层用 |

---

## 五、注意事项

1. **时间粒度对齐**:Open-Meteo 与 aWATTar 均为小时级;`saving_settled_percent` 的结算粒度(分钟级?小时级?)需在回测框架里确认(B 任务:模拟 API 行为)
2. **时区统一**:Open-Meteo 用 `timezone=Europe/Berlin` 返回本地时间;aWATTar 返回 UTC 毫秒时间戳 —— 管道里统一成一种(建议 UTC 存储、展示层转本地)
3. **切换计划**:预赛(Open-Meteo + aWATTar)→ 入围(模拟器自带接口 + training-data.zip 校准)。预赛材料可行性部分可主动写明:"方案已在公开动态电价数据上验证,入围后将用官方 training-data.zip 校准" —— 反而体现对赛题理解到位
4. **API 调用成本**:两个源都免 key,脚本做好本地缓存即可远低于免费额度
