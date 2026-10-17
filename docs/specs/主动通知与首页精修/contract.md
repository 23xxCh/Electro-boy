# AN-01 冻结接口合同

本合同由 root 批准；frontend 与 backend 分别按此实现，字段不再缩短或另起同义名称。所有时刻数值均 Unix 秒；notifications 的日期/静默按已确认家庭 IANA 现实时间计算。

## GET /api/assistant/notifications

固定同源、无 query。复用现 readonly owner/session 鉴权；不创建 cookie、不授 pending seen、不清 cache/request、不求解、不读取或控制 HA。只返回绑定户最近20条，按 updatedAt DESC、id ASC 稳定排序。

GET 必须通过 DecisionReader 的 mode=ro 连接核身份，不能调用可能授seen的 Service.session/result，不能通过factory触发迁移；通知表不存在时只读返回默认off与空items。

```json
{
  "schemaVersion": "assistant-notifications-v1",
  "settings": {
    "enabled": false,
    "needsTimezone": true,
    "timezone": null,
    "dailyAt": "20:00",
    "quietHours": {"start": "22:00", "end": "08:00"},
    "triggers": ["important_events", "daily_summary"],
    "enabledAt": null
  },
  "items": []
}
```

`settings.enabled` 是持久 master；时区缺失时默认 false。开启后若原家庭时区后来失效，发送 gate 仍关闭并 needsTimezone=true，不采用系统时区。相同 enabled=true 重复请求不重置首次水位。

每个 item 的字段全部存在，不适用时为 null：

```json
{
  "id": "stable_notification_id",
  "kind": "execution_outcome",
  "state": "sent",
  "title": "本机演练结果已核验",
  "text": "这是对应设备时段的15分钟结果，不是今日账单或节省金额。",
  "createdAt": 0,
  "updatedAt": 0,
  "dueAt": null,
  "episodeId": "owned_episode_id",
  "environment": "local_debug1_sim",
  "deliveryVia": "proactive"
}
```

以上时间0与文字仅字段示例，不是运行证据。kind 固定为 pending_attention/execution_outcome/execution_unknown/execution_resolved/quiet_digest/daily_summary。state 固定为 queued/deferred/sending/sent/unknown/failed/cancelled/covered_by_reply。deliveryVia 为 proactive/reply/null。id 为稳定内部通知标识；不得返回 open_id、chat_id、原生消息 ID、发送 UUID、凭据、原始 provider 响应或 pending 确认 token。

title 最长80字符，text 最长1200字符，来自受控事实模板；通知不是记忆或执行指令。pending 提醒只请用户查看当前待确认内容，原确认仍沿已有显式流程。环境字段仅 local_debug1_sim，不混 Release 数据。

## POST /api/assistant/notification-settings

请求 body **只能**是 `{"enabled":true}` 或 `{"enabled":false}`；不接受 requestId、时区、时间、触发列表、收件人、任意 URL/功率。要求有效 owner/session、现 Host/Origin/JSON/大小校验；走原业务串行 executor，不调用 pending/result seen 更新。

- 成功 HTTP200，返回同 GET envelope。
- 缺已确认有效 IANA 时区而请求 true：HTTP409，返回同 envelope 另加 `error:{"code":"needs_timezone"}`；settings.needsTimezone=true，enabled 保持 false，无发送/排队。
- 时区缺失提示沿既有聊天“时区 <IANA>”设置；不新增时区修改端点或猜测系统时区。
- 无 session 401、未绑定/歧义409、非法body/query400、方法错误405；错误采用既有 `{error:{code}}`，不得把网络错误当设置已保存。
- false→true：同事务保存 enabledAt、事件壁钟下界、当前 pending fingerprint、摘要水位。已有历史不回放。
- true→true：幂等保留水位。false：取消 queued/deferred；已 sending/sent/unknown 事实保留，不能声称撤回已尝试发送。
- 不提供 HTTP/API 主动发消息、指定收件人或重发 unknown 入口。

## SQLite 与发送生命周期

两张表均以 app/sender/household 隔离。settings 保存 master、enabledAt、pending baseline、事件扫描下界、独立 dailyCutoff/已处理现实日期等技术水位；timezone 不复制，读 bindings。outbox 保存 kind/sourceRefs/sourceDigest、owner、稳定UUID、dueAt、发送状态、受控事实/正文、originMessageId/coveredByReply、provider ID（私有）、feedPublished。

通知schema用独立表/列检查和 CREATE TABLE IF NOT EXISTS，不改变 MemoryV2 的 PRAGMA user_version=2 语义。真实首次DDL前，在受控idle迁移步骤以 SQLite backup API 生成私有备份，不能裸copy正在运行/WAL数据库。GET不执行DDL；离线fixture迁移不等于本人数据库已迁移，生产reload仍待QA与idle。

事件去重使用 environment+episodeId+sequence 或 pending fingerprint。无需也不得假定 episode_events.sequence 全局递增。扫描下界使用 enabledAt 壁钟，边界含等时去重；推进扫描水位与入队在同事务。日摘要另用每户现实日期唯一键与独立 cutoff，固定事实快照/入队/摘要消费原子提交，不被即时提醒消费。

queued/deferred→sending 必须在 SDK 调用前 commit UUID；一个通知只尝试一次。成功 sent；明确拒绝 failed；超时/5xx/部分生效不明/重启遗留 sending→unknown。unknown 不自动重试、不改 UUID、不增加重复摘要。CreateMessageRequest 使用绑定 open_id；SDK字段存在不等于已验平台权限/幂等期限。

原 Worker 即时 reply 覆盖的事实，必须以当前处理消息关联 outbox，不能仅按最初 confirmed 来源推断。无论原 reply sending/sent/unknown，均不再 create 同一通知；covered_by_reply 只表示尝试覆盖，不表示用户已读。后续新 reconcile 阶段可成为独立新事实。

## 静默与日摘要

22:00≤当地时刻或<08:00为静默，首版没有自动绕过。静默事实 deferred，08:00按户合并、同 episode 只保留最新核验状态；已过期/被替换pending取消。合并引用保存在 outbox，不需要第三张队列表。

每日20:00检查持久新事实，不能依赖10分钟feed拼全天。文案用“自上次摘要以来”并给现实记录区间，避免漏掉20:00后进展或称全天账单。无新事实不发；第一天只含启用后事实。错过发送时刻不补一串历史摘要，下一次摘要使用未消费的新事实和真实区间。摘要 queued 之后，其事实已有唯一持久归属；unknown 不创建替代摘要。

## feed 扩展

assistant-feed-v1 保留现字段；sourceChannel 扩展 agent，新增可选 notificationId、deliveryState。agent 只能 userText为空、agentText为受控通知文字；deliveryState只sent/unknown/failed，稳定notificationId在终态 insert一次，不更新已被cursor越过的旧气泡。queued/deferred/sending仅notifications GET。covered_by_reply沿已有feishu气泡，不再另发agent气泡。通知GET/feed不授seen。

## 模块接口候选

- NotificationSettings.get/set(identity, enabled)
- NotificationOutbox.enqueue(identity, kind, sourceRefs, dueAt)
- NotificationPump.tick(now)：复用既有monitor+executor，同一future未完成不再排队。
- FeishuTransport.send_owner(identity, text, outbound_uuid)：绑定重查，单次发送。
- Store.publish_notification(notificationId)：终态feed一次发布。

这些接口是实施合同，不代表当前已有实现或已部署。
