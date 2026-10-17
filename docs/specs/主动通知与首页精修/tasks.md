# AN-01 Tasks

- [x] 用户实施范围确认：当前首页精修、本机闭环、远端独立只读不连通、重要事件+每日摘要。
- [x] 只读审查现Service/Store/Worker/RuntimeTools/feed与本地SDK字段。
- [x] root S0批准两表、两API、Web字段、quiet/daily/unknown/feed/owner边界。
- [x] 四份SDD准备，类型发root以便frontend排期。
- [ ] Git baseline pushed（root证据），生产解锁。
- [ ] Backend两表/事件与reply覆盖/两API/单次owner发送门。
- [ ] Frontend当前首页精修、通知设置/最近20条/agent终态气泡。
- [ ] 作者离线边界检查及旧闭环定向回归。
- [ ] 独立QA、source/build pins与截图评审。
- [ ] root评审与本机候选部署边界确认；不连接环境机。
- [ ] 最终handoff与真实已验/未验范围更新。

## 必需测试场景

1. 未绑定/歧义/异户/session过期；任意收件人/query/extra POST字段拒绝。GET不写库/授seen/清缓存。
2. 默认off构造无发送；无IANA true返回409 needsTimezone且不启用；重复true不重置enabledAt。
3. 首次只处理enabledAt后事实与新pending；同episode/sequence重复扫描、重启和并发不重复；关闭取消/再开启不补积压。
4. 发送前UUID/sending持久化；成功、明确拒绝、超时/5xx、发送中崩溃分别映射；unknown不自动重发。
5. quiet跨午夜/DST按家庭现实时间；08:00只一个合并，unknown后已解决只报新状态；无时区或无事实不发。
6. pending过期/替换/资料删除后发送前取消，不覆盖或延长原pending、不后台propose/confirm。
7. Feishu即时reply尝试sent/unknown/sending都覆盖，不再create；后续由Feishu查询产生的新reconcile结果也不重复。
8. daily事件扫描与消费水位独立、每户现实日唯一；固定事实入队/cutoff原子；无新事实、错过时刻和重启不补多条旧摘要。
9. sourceagent bot-only终态insert一次稳定id；queued仅GET20条、排序稳定，feed不漏游标/不授seen/不显示原生ID/token。
10. 费用只同episode已核验15分钟并标模拟时段；epoch/现实通知日不混、无actual不造结果、发送unknown与执行unknown不同。
11. 本机资料保存和设备执行双确认、幂等UUID/原ID核对、Web/飞书资料/decision同步、专业/历史/Environment只读兼容。
12. 首页1440/390/320与键盘/确认/输入可达，减少动效、3D失败保底；截图/SSR/真实手机验收分别注明。
13. 通知schema首次/重复初始化独立检查；MemoryV2 user_version=2保持；真实迁移备份使用SQLite backup API，GET缺表仍零DDL/零writer变动。
