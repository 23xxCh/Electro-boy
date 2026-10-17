# EW-01 唯一场景 props 契约

状态：2026-10-17，root已正式快审通过，两owner按此冻结契约实施。来源为既有`assistant-decision-v1`安全投影，由frontend只派生一次，HUD／详情／场景消费同一帧。scene不接完整decision，不请求数据／Gateway／HA／provider、不求解或推算账单；允许固定同源public/energy-world静态GLB等资产GET。离线生成工具不打包client，browser不持Luxkey或直连Aholo；实际加载成功标“Lux3D房屋＋原生设备”，失败保留原生房屋并明确状态。

## 导出与所有权

scene_world独占`frontend/src/energy-world/**`、`frontend/public/energy-world/**`。`EnergyWorld.tsx` default export；`types.ts`导出以下同名类型。frontend owner独占ConsumerHome／DecisionStage／css／package／dist与外层适配，拥有唯一selectedTime／selectedDevice；root另批准其独占server.py仅限dist/energy-world内.glb静态GET，MIME model/gltf-binary、resolve／目录穿越校验不变。没有新数据／控制API；新增依赖另评审，本次不需要。

```ts
export type EnergyDevice = 'pv' | 'battery' | 'grid';
export type EnergyWorldFrame = {
  viewVersion: string | null;
  kind: 'observation' | 'preview' | 'actual';
  timeLabel: string;
  interval: {
    slot: number | null;
    start: string | null;
    end: string | null;
    measurementSlot: number | null;
    socBoundarySlot: number | null;
  };
  paused: boolean | null;
  pvKw: number | null;
  loadKw: number | null;
  batteryKw: number | null;
  batteryTargetKw: number | null;
  gridImportKw: number | null;
  gridExportKw: number | null;
  socPercent: number | null;
  prices: {
    buyEurKwh: number | null;
    sellEurKwh: number | null;
    sourceSlot: number | null;
    kind: string | null;
  };
  motion: 'none' | 'preview' | 'receipt_replay';
};
export type EnergyWorldProps = {
  frame: EnergyWorldFrame | null;
  selected: EnergyDevice | null;
  onSelect: (device: EnergyDevice) => void;
  reducedMotion: boolean;
};
```

以下为**合成契约示例**，不可展示为真实读数、固定路演数字或 Lux3D 实产。普通当前快照例，功率来自上一已结束区间、SOC 来自本槽边界：

```json
{
  "viewVersion": null,
  "kind": "observation",
  "timeLabel": "模拟时点示例 · 暂停 · 最近已读",
  "interval": {
    "slot": 200,
    "start": null,
    "end": null,
    "measurementSlot": 199,
    "socBoundarySlot": 200
  },
  "paused": true,
  "pvKw": 0,
  "loadKw": 0.8,
  "batteryKw": 0,
  "batteryTargetKw": 0,
  "gridImportKw": 0.8,
  "gridExportKw": 0,
  "socPercent": 70,
  "prices": {
    "buyEurKwh": null,
    "sellEurKwh": null,
    "sourceSlot": 200,
    "kind": "simulator_source_slot"
  },
  "motion": "none"
}
```

## 各模式的唯一来源

| kind | 数值来源与时间语义 | motion |
| --- | --- | --- |
| observation | 优先当前选中 projection.observation；有 decision 身份却缺字段，不借别时点 home 填数。仅无 decision 时可用既有严格 home fallback。SOC 与 measurementSlot 分别标记；网侧不靠能量平衡推算。 | 始终 none，last-known／暂停不假流 |
| preview | 相同 targetSlot 的 plan point 与 forecast point；batteryKw=dischargeKw−chargeKw，socPercent=该点 socEndPercent；PV／load／grid 同槽。不可行、stale、unknown、expired 不作可用预演。slot 是区间，SOC 是其结束边界 slot+1。 | preview 或 none；文字始终“预计／未执行” |
| actual | 完整 receipt 的 executedSlot、start/end、PV／load／batteryActual／grid 与 socEnd；socBoundarySlot=observedBoundarySlot，measurementSlot=executedSlot。不取后续预测或其他 home。 | 仅新的 evidenceRef 到达可 receipt_replay ≤ 1.8 秒一次后 none；旧 receipt 初载静态，选择／轮询不得重播 |

`batteryKw` 约定负为充电、正为放电；`gridImportKw`／`gridExportKw` 非负、分别呈现。`batteryTargetKw` 在 observation 取同 observation 目标、actual 取本次已确认 firstAction、preview 取选中槽 plan 功率；标签明确“设备目标／本次目标／选中时段预计目标”。准确首 15 分钟 targetPowerW 仍由原执行卡展示，不用所选未来槽替换可执行指令。prices 在 observation 用原 observation.prices，preview 用同 targetSlot forecast，actual 用 executedSlot forecast 并标“执行段计划价”；缺项 null，不从下一 SOC 边界时段借价。

父层显式拥有唯一 selectedMode 与 selectedSlot；actual receipt 存在仍可切到未来 preview，不能以 actual 存在无条件覆盖滑块。SOC 曲线的选中读数与 scene/HUD/详情共用该状态，保留已结束实际点与未来虚线的区别。场景收到 preview 不代表实际执行状态改变。

空／缺项保留 null，真实 0 保留 0，不使用 `||` 替换。frame=null 时可保留静态家园与可选按钮，但无能源流、填充电量或“工作中”假状态。permission 在父页面明确显示、阻断不允许的内容；旧帧若保留必须明确原时点与不可执行，不伪称当前。viewVersion 只作 opaque 同帧身份，不能由 scene 重算，纯 home fallback 可为 null。

所有选择与 orbit 只改本地展示；点击设备不生成聊天／POST。按钮叫“解释本次安排”，由父预填已有命令“解释这次安排”＋focus，不自动发送；它解释当前 episode，不假称解释任意滑块时段。场景可本地平滑聚焦，不凭选择修改能量数据。拖拽与点击需区分，避免转动误选。普通 HTML 设备按钮与数字是 canvas 的等价可访问入口；canvas 本身不重复朗读装饰。AI 伙伴装饰／选择反馈不声称用户在家或具体电器正在工作。

DOM 与 canvas 保持同一 frame；不计算新 PV、日照、云、设备开关或音频。总线只表达已知共同节点，不把几何位置当电源分流证明。0／null 无流，preview 用可见文字＋虚线区别，不能只靠颜色。

模型／WebGL loading、创建失败、上下文丢失由 scene 内部切换静态二维等价视图；readonly partial／permission／stale 由父处理。reducedMotion=true 覆盖任何 motion 并禁自动镜头缓动；hidden 页停止帧循环，回页不补假采样。卸载释放 renderer／geometry／material／listeners，不影响既有 polling 或 composer。
