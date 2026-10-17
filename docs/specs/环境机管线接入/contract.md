# 最小契约

私有配置拟路径：`%LOCALAPPDATA%/AnkerEnergyRuntime/release-pipeline.json`；无文件默认 disabled。秘密仅本机用户目录，文档只放字段名，不抄队友默认账号密码或主程序地址。

```json
{
  "enabled": false,
  "environment": "release_official_ha_unverified",
  "haUrl": "",
  "simUrl": "",
  "haToken": "",
  "simUsername": "",
  "simPassword": "",
  "siteSuffixes": {"1": "", "2": "", "3": ""},
  "timeoutSeconds": 10
}
```

- 仅允许明确 HTTP/HTTPS、无 userinfo/query/fragment、固定根地址；禁止自动重定向和环境代理。
- 站点必须显式 1/2/3，后缀不重复；不自动发现后排序分配。
- Snapshot 返回 schema/environment/validation/readback 时间与站点实际读数；设备状态只来自 HA。缺失/unknown/unavailable/非有限数字是拒绝或明确缺失，绝不填零；看板 telemetry 不顶替设备证据。
- 计划转命令保留 `chargeKw/dischargeKw/exportKw/sellEurKwh/maxPowerW`。discharge 目标来自 exportKw；自用放电但 export=0 的不可实现动作必须标明，不静默当作执行。
- HA HTTP 服务返回仅 submitted；mode/grid_flow/power_control 同一轮回读才可 acknowledged；没有唯一 nexttick/账单证据不得 settled。
- 任意写入异常为 outcome_unknown，禁止 POST 自动重试；reconcile 只读，不新增动作。
- 未完成控制放行时 `/environment/*` 不接受控制 POST 或生产 plan confirm。

## App 响应与 Agent 别名

固定同源、无 query 的 `GET /api/assistant/environment` 只接受已有绑定家庭1的有效 session；401/400/405 返回 `{error:{code}}`。核心不可用以同 schema 的 unavailable 返回，unexpected read failure 为502。该 GET 不创建 session、不授 pending seen、不清已过期 request/cache、不改本人资料或 local decision。

```json
{
  "schemaVersion": "environment-pipeline-v1",
  "environment": "release_official_ha_unverified",
  "status": "disabled",
  "reasonCode": "release_pipeline_not_configured",
  "householdId": "site_1",
  "validation": {"connectedTested": false, "identityVerified": false, "timeBasisVerified": false},
  "deviceTime": null,
  "timeBasis": "device_local_naive",
  "observedAt": null,
  "units": {"power": "kW", "soc": "%", "controlPower": "W"},
  "observation": {"socPercent": null, "pvKw": null, "loadKw": null, "chargingKw": null, "dischargingKw": null, "batteryActualKw": null, "gridImportKw": null, "gridExportKw": null},
  "control": {"enabled": false, "reasonCode": "release_execution_contract_unverified", "operatingMode": null, "gridFlow": null, "powerControlW": null, "targetSemantics": "battery_total_charge_or_grid_export"},
  "fieldSources": {"device": "official_ha", "prices": null},
  "prices": null
}
```

状态集固定 disabled/ready/partial/unavailable。ready 要 observation 八项、deviceTime、observedAt 与 control 的 operatingMode/gridFlow/powerControlW 完整；仍没有控制权限或已验环境声明。partial 保留原缺项为 null，0 是有效值；capture observedAt 为 Unix 秒，不证明传感器测量时刻。实际功率正放负充；control.powerControlW 是总充电／并网出口目标，不能当 batteryTargetKw。单位错误、NaN、unknown、不合法 SOC/目标上限不显示为真值。设备 naive 时间不转 UTC。价格始终 null；不含结算、actual 或节省。

精确 Agent 别名“环境机状态”“查看环境机状态”只读并明确未启用/未验证/读数缺项；其 last_result.kind 为 environment_status，与本机 device_status/decision 分离。新候选未 reload，不能称当前飞书已部署此别名。
