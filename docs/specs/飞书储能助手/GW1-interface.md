# GW-1 单户本机消费者入口

固定upstream `http://127.0.0.1:19390/api/assistant`，UI仅从19388或5173同源桥接调用。共用飞书的Service、SQLite和单线程业务执行器；浏览器不能提交app/sender/household。服务只选择已配对应用的唯一site_1 owner；无owner/多个owner为409，禁止猜选。

HTTP Host严格固定loopback地址与监听端口；GET可无Origin，有Origin必须为固定19388/5173；POST必须同样Origin且application/json。Cookie为HttpOnly、SameSite=Strict、Path=/api/assistant、12小时。请求日志关闭；API响应Cache-Control=no-store。

## 通用响应

```text
schemaVersion: assistant-web-v1
ownerLabel: 家庭1 | null
memoryVersion: integer | null
pending: Pending | null
status: ready | completed | processing | unknown | unbound | ambiguous | error
```

未配对/不唯一的session返回409与通用可解析结构，ownerLabel/memoryVersion/pending=null、error.code=owner_unbound/owner_ambiguous。

## 接口

| 方法/path | 请求 | 结果 |
| --- | --- | --- |
| GET /session | Cookie可缺失 | 创建/复用会话，Set-Cookie；当前pending |
| GET /status | Cookie | home/runtime/channelConnected及通用字段 |
| POST /messages | `{requestId:<lowercase UUID>,text:<string>}` 或 `{requestId,action:<Action>}` | requestId、status、reply、可选episode、当前pending |
| GET /messages?requestId=… | 同一UUID | 原请求结果，不重发写入 |
| GET /memories | Cookie | items当前确认资料，含id/kind/key/value/startsAt/expiresAt/updatedAt/revision/status/machineConstraint |
| GET /episodes | Cookie | items最近30条本人技术episode |
| GET /episodes/{episodeId} | 本人ID | episode技术详情 |
| GET /feedback | Cookie | items反馈及revisionCandidates候选；删除后的summary/proposed已擦除 |

消息completed.reply=`{kind,text}`；processing或unknown的reply可能null，UI必须显示真实未知并查原requestId。不要自动重新POST。回复缓存十分钟，completed缓存到期仍保留请求tombstone：status=completed、cacheExpired=true、retryable=false，reply为不含个人原文的completed_receipt并附可恢复技术episode及freshpending。允许继续新话题，旧UUID永不重新执行。真正unknown保持未知，技术只读核对；不是缓存到期。

## Pending与Action

```text
Pending: {
  kind: memory | forget | feedback | sample_plan | simulation_plan | revision_candidate | clarification,
  token: opaque string, expiresAt: Unix seconds, summary: user-visible string,
  canConfirm: boolean, confirmAction: confirm_pending | null,
  choices: [{id:string,label:string,description:string}],
  episodeId?,proposalId?,execution?,constraint?,feedbackId?,revisionCandidateId?,consumedMemoryIds?,memoryVersion?
}
execution: {targetPowerW:number,sourceSlot:integer,intervalMinutes:15,intervalCount:1,
            signConvention:negative_charge_positive_discharge}
```

只展示实际summary及execution；用户见过后才允许确认。canConfirm=false/confirmAction=null必须禁确认；clarification由下一条短回答接续（例如“我想设置最低留电量”→“30%”→候选确认）。飞书与Web新pending相互替换，旧token不能执行。

当前Action：

```text
{type:confirm_pending,token}
{type:choose_pending,token,choiceId}
{type:delete_memory,memoryId}
{type:feedback,episodeId,rating:suitable|unsuitable,reasonText}
```

反馈先复述并确认保存；明确的新保底意愿产生另一张revision_candidate，需单独确认。一次反馈不改硬边界/权重/代码。查看及撤回当前可走GET/feedback与普通消息“删除反馈 <id>”；明确补充修订可说“修订反馈 <id> 保底<百分比>%”。资料修改走同Service自然语言复述候选，删除走已存在的delete_memory；前端不自建记忆。技术ID可以放详情，不把中文字符串解析成决策状态。

## 首页与阶段

```text
home: null | {environment:local_debug1_sim,deviceTime:string,timeBasis:device_local_naive,
  observedAt:number|null,paused:boolean|null,socPercent:number|null,batteryPowerKw:number|null,
  pvKw:number|null,loadKw:number|null,physicalInputsKind:string|null,
  measurementSourceSlot:integer|null,socBoundarySlot:integer|null,controlSourceSlot:integer}
runtime: {status:snapshot_available|unavailable,forecastStatus:not_checked,
  executionStatus:not_requested,reasonCode:string|null}
channelConnected: boolean
```

home只包含绑定site_1；PV/load对应physicalInputsKind及measurementSourceSlot，不把observedAt称实时采样。paused=true须标本机演练暂停。health200只证明连接条件，不能替代预测历史充分、实际执行或回读证据。

```text
episode: {episodeId,stage,proposalId,createdAt,updatedAt,memoryVersion,constraintsVersion,
  usedMemoryIds,consumedMemoryIds,events:[{sequence,phase,createdAt,evidence}]}
stage/phase: created|proposed|confirmed|dispatched|acknowledged|settled|outcome|rejected|failed|expired|unknown
```

evidence只投影proposal/request/confirmation技术ID、消耗资料ID、来源槽、功率、成本、证据类别与布尔核验结果。原生Feishu消息/actor字段不进public JSON。dispatched不等于acknowledged，acknowledged不等于settled，净电费costEur不是节省金额。真正来源与证明由核心返回，UI不升级阶段。

主要错误码：session_required(401)、origin_or_host_rejected(403)、owner_unbound/owner_ambiguous(409)、pending_not_shown_or_stale/proposal_not_shown/pending_not_confirmable/choice_mismatch(409)、invalid_message_fields/invalid_request_id/unsupported_action(400)、request_not_found/episode_not_found(404)。拒绝不自动换UUID重发。
