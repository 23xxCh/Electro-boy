# Anker 三站 Home Assistant 看板

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
