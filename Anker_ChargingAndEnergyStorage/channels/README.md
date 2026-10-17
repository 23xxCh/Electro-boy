# 本机飞书储能助手

从主工程目录 `Anker_ChargingAndEnergyStorage` 运行，使用已安装官方 `lark_oapi` 的 `C:\Users\22003\miniconda3\python.exe`。无需安装依赖。CH-02仅经受限energy_runtime工具操作用户已授权的本机Debug1仿真，绝不读取Release默认远端HA。

CH-03 复用已配对机器人和原凭据，已绑定用户无需重新配置或绑定。直接在原机器人私聊：

1. `家里现在怎么样`：查询最新本机回读，含暂停状态；网页同步这次对话。
2. `记住我通常18点到家`：模型生成习惯候选，助手复述后，回复它给出的 `记下 <资料确认号>` 才持久保存。`我的偏好` 可查看；习惯目前是讨论依据，不会凭空变成设备负荷。
3. `我想设置最低留电量` → 回复你自己的百分比 → 先看预演，再 `记下 <资料确认号>`。保存只改软件约束；之后生成的设备方案仍需另一个 `执行 <方案确认号>`，只执行首15分钟。不能复用资料号当执行号，不能用“执行吧”代替精确确认。
4. `帮我安排这一时段`：使用已确认资料与本机预测／电价求解，不会直接动作。确认执行后回复同 episode 的回读、SOC 和单槽净费；净费不是节省。
5. 结果未知时按原回复 `仿真进度 <episodeId>` 只读核对，不新建请求重发控制。

同一电脑打开 `http://127.0.0.1:19388/?view=family` 可查看同 owner 对话、资料候选、预计方案与已核验实际。最近10分钟的短期对话同步不是永久保存聊天；确认的资料保存于 SQLite，服务重启后仍在。真人飞书入站与确认需本人操作，离线验收不能代替真人完成控制。当前只接本机 site_1 仿真，不是三站 Agent 或真实家庭控制。实现与边界见 [CH-03 spec](../../docs/specs/飞书储能助手/CH03-spec.md)。

```powershell
python -m unittest discover -s channels/checks -v
python -m channels.feishu --configure
python -m channels.feishu --check-auth
python -m channels.feishu --pair
python -m channels.feishu --energy-api http://127.0.0.1:19388 --model-api http://127.0.0.1:4178
python -m channels.feishu --status
```

`--configure` 交互读取 App ID，密钥由隐藏输入读取后用 Windows 用户 DPAPI 加密保存，不能把密钥放参数。运行目录是 `%LOCALAPPDATA%\AnkerEnergyAgent`，含加密凭据、SQLite 和仅状态元数据；不要上传此目录。当前已有独立后台 runner，重复启动前用 `--status` 与系统进程核实。退出仅停止这个 runner 的 PID，不停 CLI bus、能源服务或旧模型服务。新能源候选由 frontend agent 管理，模型依赖保留的旧4178解释服务；两服务关闭均需明确降级，不自动重启/复制模型密钥。

owner 通过 `--pair` 生成十分钟单次绑定码，私下交给自己；在飞书与本应用机器人私聊 `绑定 <码>`。任何未绑定人只能得到帮助与绑定说明。第一版只绑定家庭1，不读取通讯录、不群发。

同一runner还在loopback19390提供消费者Web Gateway，复用飞书同Service、资料、确认与episode。仅本机19388/5173前端可桥接，浏览器不能传飞书身份。精确接口见 [GW-1](../../docs/specs/飞书储能助手/GW1-interface.md)。

绑定后例子：

- `家里现在怎么样` / `状态`：绑定户最新本机设备回读，明确演练暂停、原始设备时间；PV/负荷持有上一已结束区间。`收益` / `为什么`：本人家庭历史回放，始终带时间和证据边界。
- `时区 Europe/Berlin`：明确活动时区；不更改历史设备时间。
- `记住，我通常晚上六点到家`：复述habit候选，回复 `记下 <确认号>` 才保存。
- `记住，明天18点到20点我在家`：复述带日期activity候选，先确认时区与结束时间。
- `记住，我偏好备电优先`：preference，不修改硬件保底SOC。
- `我想设置最低留电量` → `30%`：十分钟短对话接续并复述，确认才保存软件reserve_soc约束；不写设备SOC寄存器。
- `资料` / `删除 <ID>` / `修改 <ID> 为……` / `忘记全部`：查看、修改候选、删除、清理个人资料。
- `按我的习惯你建议怎么安排？`：相关资料用于非执行取舍讨论；明确哪些资料被召回。真实家庭未来输入尚未接入；历史演练不代替真实未来安排。
- `帮我安排这一时段`：用本机演练源时点、既有PV LSTM/DM预测和HiGHS生成待确认方案，展示首15分钟动作/功率。确认号绑定原资料版本与源槽；只有明确`执行 <确认号>`才经核心下发一槽，读回并结算；其余horizon不自动执行。`仿真进度`只读核对已有执行，不重发。
- `反馈 <episodeId> 合适/不合适及原因`：复述确认后保存反馈。明确新保底意愿会产生另一张待确认修订，第二次确认才改变软件约束。`修订反馈 <反馈ID> 保底<百分比>%`补充候选；`删除反馈 <反馈ID>`撤回反馈与未生效候选。
- `样例 冰箱` / `样例 车` / `样例 外出` / `样例 低价`：固定八小时样例；按回复确认号选择/确认，只记下样例，不下发设备，不把用户明天活动映射成样例时间。

每个资料/方案确认号限定发送人、家庭、十分钟期限，新候选会替换旧号。收益数字直接来自paired replay本人家庭字段；本机执行的净电费来自simulator settlement，不称节省。自然语言输入超过180字会请用户缩短。资料用标准SQLite按身份/时段/相关性有界召回，LLM摘要分多个400字允许字段，省略的context IDs可审；已确认硬约束全量结构化进入真实LP，不受LLM窗口影响，不要求用户删除记忆。

已有CH-01资料首次启动时先在用户私有runtime做一致性备份再additive迁移。资料修改生成revision，删除擦除历代个人值并保留技术tombstone；episode不缓存个人原文。Web短期回复缓存十分钟，原请求tombstone保留以免超时/到期重执行。退出或reload前同时检查飞书work和web_requests的processing为空，只停止核实属于本模块的PID。

可靠性：真人p2p text白名单、持久message_id去重，received/processing/computed可恢复，出站发送前标sending；超时未知不盲重发。未完成work只短期暂存输入，进入sending删除；不保存全量聊天记录。来源message_id和结构化资料可查可删；unknown只保留状态，不保留正文。后台SDK日志禁用，以免连接异常含签名URL。

飞书后台应启用机器人、允许本人使用；权限 `im:message.p2p_msg:readonly`、`im:message:send_as_bot`；长连接接收事件 `im.message.receive_v1` 并发布可用版本。仅凭鉴权/长连接不能证明双向收发。CH-01本人已完成私聊绑定及一条习惯确认；CH-02实际HA全链使用明确合成QA owner/临时DB，不能称本人已确认执行。私聊text及安全纯文字post均归一化，富文本里的链接/图片/执行标签不成为工具指令。

官方参考：[Python SDK事件处理](https://open.feishu.cn/document/server-side-sdk/python--sdk/handling-events)、[接收消息事件](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)、[回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)。
