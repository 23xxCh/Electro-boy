# HA 主机辅助代码

这里保留当前桥接、快照导出、身份检查及旧调度辅助脚本源码。`config.example.json` 仅含占位符与空认证字段。

官方 HA 插件、个人 HA 配置、实体映射、webhook、数据库及 `ha_lovelace_preview/` 运行目录未发布；这些是本机实控的外部前置条件。

`stack.ps1 Start` 是旧三站持续调度路径，会启动控制与推进环境，不能用作当前 Agent 单家庭单槽流程的启动器。公开源码检查不运行它，也不启动 HA、环境机或任何设备操作。

当前 Agent 数值链位于 `Anker_ChargingAndEnergyStorage/energy_runtime/`；新环境机候选仅提供独立只读状态，默认禁用，远端写入恒阻塞。没有现场身份、确认范围和下一 tick／退出／结算证据时，不可把 HTTP 成功称为设备执行完成。
