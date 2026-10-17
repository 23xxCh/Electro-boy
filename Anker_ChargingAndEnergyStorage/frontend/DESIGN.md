# 电击小子 · 前端设计约定

当前消费者首页为EW-01家庭能源世界，继承RD-01真实数据与确认链。正本为docs/specs/家庭能源世界/spec.md及scene-contract.md；既有专业／FV保留独立历史来源。

## 布局与交互

- 桌面中央约2/3为可转动、缩放、点选的家庭浮岛与设备详情，右约1/3为Agent，唯一composer首屏可见。短桌面让Agent从主区顶部占右列；顶部四组HUD默认显示SOC／功率与目标、PV／总负荷、电网进出、模拟购售价。
- 手机视觉顺序为“摘要／时点 → Agent → 完整常显HUD → 3D／曲线”，唯一底部输入保留足够滚动空间。thread至少180px且按视口有界，初载可看至少3个完整助手回复行；HUD全字段保留不折叠。此为CSS视觉顺序，DOM与读屏阅读顺序仍需专项验收。普通HTML设备按钮与读数是canvas等价入口。
- 点PV／电池／电网只选中；拖唯一时间条只预演。解释入口名为“解释本次安排”，仅预填“解释这次安排”并focus，用户另行send，不假称能解释任意未来槽。
- 专业／history／dataset／dashboard／调度记录／report／导出及FV入口和接口完整保留；不把旧历史强称当前同源。

## 同帧与证据

ConsumerHome唯一selectedMode／selectedSlot，assistantWeb.energyWorldFrame只派生一次，HUD／场景／设备详情／SOC选中点共用。observation取自身保持值与SOC边界；选中decision缺字段或整份observation缺失不借另一时点home。无选择的初始快照才允许严格fallback，真实0不换成缺项。

preview按选中targetSlot取plan＋forecast，SOC为该槽末预计值；即使有receipt仍可看未来，不用旧actual覆盖。actual只取完整receipt单区间，电价取executedSlot预测采用价；目标与传感器分别标记。SOC当前选中点按frame.socBoundarySlot定位，actual历史两点保留，未来始终虚线。全窗预计净费用与单15分钟实际分开，无paired基线不叫节省。

技术slot／digest／完整来源折叠，模拟时点、最近已读／预计未执行／实际回读常显。电池动作使用充电／放电／待机＋W，不要求消费者理解负号。

## 视觉与资源

深靛环境、翡翠总线、暖色家庭剖面，场景／房屋灯光是美术示意，不代表天气、在家状态或具体电器工作。沿用本地Noto Sans SC／Source Sans 3，普通文字≥14px，触摸热区≥44px，焦点可见；正式无障碍合格仍需有界检查与人工验收。

复用已安装Three.js。旧ThreeUI PredictiveArc装饰与专业样式保留；package／lock原样保全，未加依赖。场景只GET固定同源静态GLB；数据与控制仍由既有RD通路。Lux3D房屋只有实际load后标来源，原生PV／电池／机器人另区分；失败保留原生房屋，WebGL失败切静态2D。离线生成工具／密钥不打包client，浏览器不直连Aholo。

当前保持值静止，0／null无流；预计虚线与文字区分。只在新evidenceRef到达时最多1.8s实际回放，旧receipt初载／选择／轮询不重播。reduce／hidden／离屏停帧，卸载释放资源。预算80k triangles／100 calls、桌面45fps／手机30fps；有效可见采样不达标先降DPR再2D，暂停窗口不计入帧率，不把桌面浏览器窗口采样当实体手机性能。

## 验证边界

作者契约与build、root实看、独立QA分别留收据。memory保存与首15分钟执行仍双确认；一个pending，seen／owner／version／expiry／幂等与unknown原UUID核对不变。新通知候选仅在户主显式开启、已确认家庭IANA且产生新事实后尝试owner-only消息；发送sent不是已读，发送unknown不是设备执行unknown。pending到达不自动抢焦点，用户显式查看时才定位确认按钮。本次只发布候选源码，不开启本人提醒或切换运行服务；实体手机／真人双端／真实消息送达和真实家庭收益另验。
