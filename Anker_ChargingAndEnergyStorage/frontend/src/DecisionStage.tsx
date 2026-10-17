import { useId, type CSSProperties } from 'react';
import { ArrowRightIcon, BatteryHighIcon, ChartLineIcon, HouseLineIcon, PlugIcon, SunIcon } from '@phosphor-icons/react';
import type { DecisionProjection, PlanPoint } from './assistantWeb';
import type { EnergyWorldFrame } from './energy-world/types';

const kw = (n: number | null | undefined) => n == null ? '未接入' : `${n.toFixed(2)} kW`;
const pct = (n: number | null | undefined) => n == null ? '未接入' : `${n.toFixed(1)}%`;
const euro = (n: number) => `${n < 0 ? '−' : ''}€${Math.abs(n).toFixed(4)}`;
const clock = (s: string | undefined) => s?.replace('T', ' ').slice(5, 16) ?? '时点未提供';
const action = (n: number | null | undefined) => n == null ? '目标未接入' : n === 0 ? '待机 0 W' : `${n < 0 ? '充电' : '放电'} ${Math.abs(n)} W`;
const stateText: Record<string, string> = { preview: '方案预览 · 尚未保存或执行', proposed: '预计安排 · 等你确认', executing: '已提交 · 等待实际回读', settled: '这15分钟已结算', unknown: '结果未知 · 需要核对', infeasible: '这项要求暂时不可行', unchanged: '现有安排已满足要求', expired: '方案已过期', empty: '设备读数 · 尚无安排' };

function Flow({ id, path, power, reverse, moving, preview }: { id: string; path: string; power: number | null; reverse?: boolean; moving: boolean; preview: boolean }) {
  const flowing = power !== null && Math.abs(power) > 0;
  return <path d={path} className={`rd-flow ${preview ? 'is-preview' : ''} ${flowing && moving ? 'is-moving' : ''} ${reverse ? 'is-reverse' : ''}`}
    strokeWidth={flowing ? Math.min(4.5, 1.5 + Math.abs(power!)) : 1.2} markerEnd={flowing ? `url(#${id})` : undefined}
    style={{ '--flow-duration': `${Math.max(.7, 2.5 - Math.abs(power ?? 0) * .5)}s` } as CSSProperties}/>;
}

export default function DecisionStage({ decision, frame, selectedSlot, onSlot, error, loading, busy, onRefresh, onProfessional, onAsk, compact = false }: {
  decision: DecisionProjection | null; frame: EnergyWorldFrame | null; selectedSlot: number | null; onSlot: (slot: number) => void;
  error: string; loading: boolean; busy: boolean; onRefresh: () => void; onProfessional: () => void; onAsk: () => void; compact?: boolean;
}) {
  const marker = useId().replace(/:/g, '');
  const plan = decision?.plan;
  const baseline = decision?.preview?.baseline;
  const points = plan?.points ?? [];
  const actual = decision?.actual;
  const selected = Math.max(0, points.findIndex(point => point.targetSlot === selectedSlot));
  const point = points[selected];
  const previewAvailable = decision?.availability === 'ready' && !!points.length && !['unknown', 'expired', 'infeasible'].includes(decision.presentationState);
  const previewing = frame?.kind === 'preview';
  const pv = frame?.pvKw ?? null, load = frame?.loadKw ?? null, battery = frame?.batteryKw ?? null;
  const imported = frame?.gridImportKw ?? null, exported = frame?.gridExportKw ?? null, soc = frame?.socPercent ?? null;
  const target = frame?.batteryTargetKw == null ? null : Math.round(frame.batteryTargetKw * 1000);
  const prices = frame?.prices;
  const buy = prices?.buyEurKwh;
  const sell = prices?.sellEurKwh;
  const grid = imported === null || exported === null ? null : imported - exported;
  const moving = frame?.motion !== 'none' && frame !== null;
  const title = frame?.kind === 'actual' ? '刚才这15分钟' : previewing ? '预计怎样用电' : '家里的供用电';
  const scope = frame?.timeLabel ?? '尚无可信读数';
  const origin = decision?.origin?.sourceSlot ?? frame?.interval.slot;
  const last = points.at(-1)?.targetSlot ?? origin ?? 0;
  const first = origin ?? points[0]?.targetSlot ?? 0;
  const x = (slot: number) => 38 + ((slot - first) / Math.max(1, last + 1 - first)) * 420;
  const y = (percent: number) => 146 - percent * 1.24;
  const planningStart = decision?.origin?.initialEnergyKwh == null ? null : decision.origin.initialEnergyKwh / decision.origin.physics.capacityKwh * 100;
  const path = (rows: PlanPoint[]) => (planningStart === null ? '' : `M${x(first)},${y(planningStart)} `) + rows.map((p, i) => `${i || planningStart !== null ? 'L' : 'M'}${x(p.targetSlot + 1).toFixed(2)},${y(p.socEndPercent).toFixed(2)}`).join(' ');
  const beforePoint = baseline?.points.find(p => p.targetSlot === point?.targetSlot);
  const horizonCost = plan?.expectedHorizonCostEur;

  return <aside className={`rd-stage ${compact ? 'ew-stage' : ''}`} aria-label="数据与仿真台">
    {!compact && <>
    <header className="rd-stage-heading"><div><span className="consumer-eyebrow">数据与仿真台</span><h2>{title}</h2></div><button className="consumer-link" onClick={onRefresh} disabled={loading}>刷新<ArrowRightIcon size={15}/></button></header>
    <p className={`rd-state ${decision?.availability === 'stale' || decision?.presentationState === 'unknown' ? 'is-caution' : ''}`} role="status">{loading && !decision ? '正在读取数据…' : decision?.availability === 'stale' ? '来源已变化 · 旧安排待更新' : decision?.availability === 'unavailable' ? '数据暂时不可用' : stateText[decision?.presentationState ?? 'empty']}</p>
    <p className="rd-source">{scope}</p>
    {error && <p className="rd-error" role="alert">{error} 已有读数保留时点，不能作为新执行依据。</p>}
    <svg className="rd-flow-map" viewBox="0 0 400 222" role="img" aria-label={`${previewing ? '预计' : '已回读'}能量流：太阳能${kw(pv)}，家庭用电${kw(load)}，电池${kw(battery)}，电网购入${kw(imported)}、送出${kw(exported)}`}>
      <defs><marker id={marker} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor"/></marker></defs>
      <Flow id={marker} path="M100 84 H200 V111" power={pv} moving={moving} preview={previewing}/>
      <Flow id={marker} path="M200 111 V84 H301" power={load} moving={moving} preview={previewing}/>
      <Flow id={marker} path={grid !== null && grid < 0 ? 'M200 111 V188 H130' : 'M130 188 H200 V111'} power={grid} reverse={grid !== null && grid < 0} moving={moving} preview={previewing}/>
      <Flow id={marker} path={battery !== null && battery > 0 ? 'M260 188 H200 V111' : 'M200 111 V188 H260'} power={battery} reverse={battery !== null && battery > 0} moving={moving} preview={previewing}/>
      <circle className="rd-bus" cx="200" cy="111" r="5"/>
      <g className="rd-node rd-solar"><SunIcon x="40" y="12" width="25" height="25"/><text x="70" y="31">太阳能</text><text className="rd-number" x="40" y="62">{kw(pv)}</text></g>
      <g className="rd-node"><HouseLineIcon x="285" y="12" width="25" height="25"/><text x="315" y="31">用电</text><text className="rd-number" x="280" y="62">{kw(load)}</text></g>
      <g className="rd-node"><PlugIcon x="40" y="132" width="25" height="25"/><text x="70" y="151">电网</text><text className="rd-grid-value" x="28" y="181">购 {kw(imported)}</text><text className="rd-grid-value" x="28" y="204">售 {kw(exported)}</text></g>
      <g className="rd-node"><BatteryHighIcon x="285" y="132" width="25" height="25"/><text x="315" y="151">电池</text><text className="rd-number" x="280" y="181">{pct(soc)}</text><text className="rd-grid-value" x="264" y="204">{previewing ? '预计' : '实际'} {kw(battery)}</text></g>
    </svg>
    <div className="rd-power-meta"><span>{previewing ? '选中时段预计目标' : decision?.episodeId ? '本次设备目标' : '设备目标'}<strong>{action(target)}</strong></span><span>{previewing ? '预计功率' : '实际功率'}<strong>{kw(battery)}</strong></span></div>
    <div className="rd-tariff"><span>购电 <strong>{buy == null ? '未接入' : buy.toFixed(4)}</strong></span><span>售电 <strong>{sell == null ? '未接入' : sell.toFixed(4)}</strong></span><small>EUR/kWh · {frame?.kind === 'actual' ? '执行段计划价' : previewing ? '计划采用价' : '模拟时段价'}</small></div>
    {!previewing && frame?.kind !== 'actual' && <p className="rd-source">功率：已结束时段 {frame?.interval.measurementSlot ?? '未知'} · 电量边界 {frame?.interval.socBoundarySlot ?? '未知'}</p>}
    </>}
    {compact && <div className="ew-timeline-heading"><h3>电池留电轨迹</h3></div>}
    <section className="rd-trajectory" aria-label="电池计划与实际"><div className="rd-chart-heading"><h3><ChartLineIcon size={19}/>电池怎样留电</h3>{plan && <span>最低 {pct(plan.minimumPercent)}</span>}</div>
      <div className="rd-chart-key"><span className="is-before">调整前预计</span><span className="is-plan">{baseline ? '调整后预计' : '计划预计'}</span><span className="is-actual">实际回读</span></div>
      <svg className="rd-soc-chart" viewBox="0 0 480 174" role="img" aria-label="电池SOC计划与实际；虚线为预计，圆点为已回读，精确值在下方">
        {[0, 50, 100].map(n => <g key={n}><line className="rd-chart-grid" x1="38" y1={y(n)} x2="458" y2={y(n)}/><text x="4" y={y(n) + 4}>{n}%</text></g>)}
        {previewing && !!point && <rect className="rd-selected-slot" x={x(point.targetSlot)} y="20" width={Math.max(3, x(point.targetSlot + 1) - x(point.targetSlot))} height="126"/>}
        {plan && <><line className="rd-reserve" x1="38" x2="458" y1={y(plan.minimumPercent)} y2={y(plan.minimumPercent)}/><text className="rd-reserve-label" x="454" y={Math.max(14, y(plan.minimumPercent) - 4)} textAnchor="end">最低 {plan.minimumPercent}%</text></>}
        {baseline?.points.length ? <path className="rd-before-line" d={path(baseline.points)}/> : null}
        {!!points.length && <path className="rd-plan-line" d={path(points)}/>}
        {previewing && point && <circle className="rd-plan-point" cx={x(point.targetSlot + 1)} cy={y(point.socEndPercent)} r="4"/>}
        {actual && <><circle className="rd-actual-point" cx={x(actual.executedSlot)} cy={y(actual.socStartPercent)} r="4"/><circle className="rd-actual-point" cx={x(actual.observedBoundarySlot)} cy={y(actual.socEndPercent)} r="5"/></>}
        {frame?.kind === 'observation' && frame.socPercent !== null && frame.interval.socBoundarySlot !== null && <circle className="rd-observation-point" cx={x(frame.interval.socBoundarySlot)} cy={y(frame.socPercent)} r="5"/>}
        {!points.length && <text className="rd-chart-empty" x="250" y="82" textAnchor="middle">{busy ? '正在生成真实预览…' : '说说留电要求，看看安排怎么变'}</text>}
        <text x="38" y="169">{points[0]?.start.slice(11, 16) ?? '当前'}</text><text x="458" y="169" textAnchor="end">{points.at(-1)?.end.replace('T', ' ').slice(5, 16) ?? '尚无计划'}</text>
      </svg>
      {!!points.length && <label className="rd-slot-slider"><span>拖动预演时间</span><input disabled={!previewAvailable} aria-label="查看预计时段" type="range" min="0" max={points.length - 1} value={Math.min(selected, points.length - 1)} onChange={e => onSlot(points[Number(e.target.value)].targetSlot)}/></label>}
      {previewing && point && <div className="rd-selected-reading"><span>{clock(point.start)}–{point.end.slice(11, 16)} · 未执行</span><strong>{beforePoint ? `${pct(beforePoint.socEndPercent)} → ` : ''}{pct(point.socEndPercent)}<small>预计时段末电量</small></strong></div>}
      <div className="rd-outcome"><span>这15分钟净电费<strong>{actual?.settlementAvailable && actual.intervalCostEur !== null ? euro(actual.intervalCostEur) : '待结算'}</strong></span><span>备用电量结果<strong>{actual?.reserveMet == null ? '待回读' : actual.reserveMet ? '已满足' : '未满足'}</strong></span></div>
      {horizonCost != null && <p className="rd-estimate">全预测窗预计净费 {euro(horizonCost)}{baseline?.expectedHorizonCostEur != null ? ` · 调整前 ${euro(baseline.expectedHorizonCostEur)}` : ''}；与单15分钟实际费用分开。</p>}
      {decision?.preview?.status === 'unchanged' && <p className="rd-estimate">现有安排已满足这项要求，预计动作没有变化。</p>}
      {decision?.presentationState === 'infeasible' && <p className="rd-error">这项留电要求暂时无法满足，请与助手调整；未执行。</p>}
    </section>
    <div className="rd-stage-links"><button className="consumer-link" onClick={onAsk}>解释本次安排<ArrowRightIcon size={15}/></button><button className="consumer-link" onClick={onProfessional}>详细分析／专业视图<ArrowRightIcon size={15}/></button></div>
    <details className="rd-evidence"><summary>数据依据与来源</summary><p>本机Debug1仿真；专业视图为独立历史记录。此处预计费用不是实际账单或节省。</p><p>来源 {frame?.interval.slot ?? '未提供'} · 功率区间 {frame?.interval.measurementSlot ?? '未提供'} · SOC边界 {frame?.interval.socBoundarySlot ?? '未提供'}</p><p>观测 {frame?.kind ?? '未提供'}，保持值不等于瞬时采样；规划余量 {decision?.origin?.socRoundingMarginPercent == null ? '未提供' : `${decision.origin.socRoundingMarginPercent} 个百分点`}。未来均未执行。</p>{decision && <><p>事件 {decision.episodeId ?? '无'} · 预览 {decision.previewId ?? '无'} · {decision.reasonCode ?? '来源有效性仍由执行前核验'}</p><p>实际证明 {actual?.evidenceRef ?? '尚无'} · 状态 {decision.execution.phase}</p></>}</details>
  </aside>;
}
