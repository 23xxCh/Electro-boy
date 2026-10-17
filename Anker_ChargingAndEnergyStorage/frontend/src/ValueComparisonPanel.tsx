import { useRef } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { ArrowRightIcon, BatteryHighIcon, ChartLineUpIcon, ClockIcon, CaretDownIcon, HouseLineIcon, LightningIcon, XIcon } from '@phosphor-icons/react';
import { dateLabel, number, timeLabel } from './types';
import { actionText, changeDirection, changeText, evidenceLabel, evidenceNumber, householdName, type ComparisonSlot, type ComparisonSummary, type Evidence, type ValueComparison } from './valueComparison';
import './value-comparison.css';

type Props = {
  comparison: ValueComparison | null; loading: boolean; error: string; onRetry: () => void;
  siteId: string; onSite: (id: string) => void; day: string; onDay: (day: string) => void;
  slotIndex: number; onSlot: (index: number) => void; onSample: () => void; onProfessional: () => void;
};
const assumptionText: Record<string, string> = {
  local_physics: '电池14 kWh、充放电上限5 kW、进出口上限20／5 kW，均为本地实验约定。',
  initial_soc: '三户起点电量为8%／55%／7%，双方相同；预热不推进电池。',
  local_pcc_projection: '双方放电都受本地并网约束裁剪，保留原请求；尚未通过设备或固件验证。纯光伏外送超过5 kW、进口超过20 kW仍判失败。',
  causal_cycle: '未来天气与电价重复使用最近已结束的24小时同钟点历史；实况价只用于结算。',
  training_window: '固定模型训练窗口未独立确认，可能见过本数据；不能据此声称无污染泛化。',
  tariff_snapshot: '每户保留原始电价，10月15日00:00存在跨户价差；先逐户结算再相加。',
};

export default function ValueComparisonPanel({ comparison, loading, error, onRetry, siteId, onSite, day, onDay, slotIndex, onSlot, onSample, onProfessional }: Props) {
  const sourceTrigger = useRef<HTMLButtonElement>(null);
  const sites = comparison?.sites.filter(site => siteId === 'all' || site.id === siteId) ?? [];
  const selected = sites.length === 1 ? sites[0] : null;
  const summary = selected?.summary ?? comparison?.summary;
  const validSites = comparison?.sites.filter(site => site.status === 'valid').length ?? 0;
  const anchor = sites.find(site => site.status === 'valid') ?? sites[0];
  const days = [...new Set(anchor?.slots.map(slot => slot.start.slice(0, 10)) ?? [])];
  const daySlots = anchor?.slots.filter(slot => !day || slot.start.startsWith(day)) ?? [];
  const slot = daySlots.find(item => item.slotIndex === slotIndex) ?? daySlots[0];
  const rows = sites.map(site => ({ site, slot: site.slots.find(item => item.slotIndex === slot?.slotIndex) }));
  const heading = selected ? actionText(slot?.policy.requestedBatteryKw) : '三户分别安排，逐户算清楚';
  const total = evidenceNumber(summary?.improvementEur);
  const fullWindow = comparison ? `${dateLabel(comparison.scope.start)} ${timeLabel(comparison.scope.start)} → ${dateLabel(comparison.scope.endExclusive)} ${timeLabel(comparison.scope.endExclusive)}` : '2026.10.13 09:15 → 2026.10.17 08:00';

  return <Dialog.Root><main id="family-main" className="family-main fv-main">
    <section className="family-context fv-context" aria-label="对照范围与回放身份">
      <label><HouseLineIcon size={20}/><span className="sr-only">查看哪个家庭</span><select aria-label="查看哪个家庭" value={siteId} onChange={event => onSite(event.target.value)}>
        {(comparison?.sites ?? [{ id: 'site_1' }, { id: 'site_2' }, { id: 'site_3' }]).map(site => <option key={site.id} value={site.id}>{householdName(site.id)}</option>)}<option value="all">三户总览</option>
      </select><CaretDownIcon size={14}/></label>
      <div className="fv-identity"><strong className="family-replay">离线历史回放模型对照</strong><span className="fv-boundary">非真实账单／官方成绩 · 不控制设备</span></div>
    </section>
    <p className="fv-scope">全回放期间对照 · {fullWindow} · 94小时45分</p>
    {loading ? <section className="family-state" role="status"><ClockIcon size={32}/><h1>正在读取这次配对对照</h1><p>动作、曲线与金额会使用同一份证据。</p></section>
      : error || !comparison ? <section className="family-state" role="alert"><h1>暂时不能比较</h1><p>{error || '离线配对产物还没有准备好。'}</p><p>不会用旧记录或零金额代替。</p><button className="family-primary" onClick={onRetry}>重新读取<ArrowRightIcon size={18}/></button></section>
        : <>
          <section className="family-hero fv-hero" aria-label="全回放期间综合对照">
            <div className={`fv-result is-${changeDirection(summary?.improvementEur)}`}>
              <span>全回放期间 · 94小时45分</span>
              <h1>{changeText(summary?.improvementEur, 'total')}</h1>
              <strong>{total === null ? '—' : `€${number(Math.abs(total))}`}</strong>
              <small>{evidenceLabel(summary?.improvementEur)}{total === null ? ` · ${validSites}/3 户可比，暂无完整三户总数` : ' · 含期末剩余电量价值修正'}</small>
              <div className="fv-hero-split">
                <Difference title="购售电现金差" item={summary?.cashDeltaEur} kind="cash" detail="原生自用现金成本 − 本策略现金成本。"/>
                <Difference title="期末剩余电量价值修正" item={summary?.terminalCorrectionEur} kind="inventory" detail="期末电量差 × 各户本次平均买价；不是到账的钱。"/>
              </div>
              <div className="fv-hero-links"><a className="family-primary" href="#comparison-timeline">查看差异发生在哪<ArrowRightIcon size={18}/></a><Dialog.Trigger asChild><button className="fv-source-trigger" onClick={event => { sourceTrigger.current = event.currentTarget; }}>查看实验依据</button></Dialog.Trigger></div>
            </div>
            <figure className="fv-hero-art"><img src="/assets/house-solar-storage-fv01-v1.png" alt="" width="1536" height="1024" decoding="async" loading="lazy"/><figcaption>家庭光储概念插画 · 非设备回读</figcaption></figure>
          </section>
          <section className="fv-action" aria-label="所选历史时隙的系统安排">
            <div><span className="family-eyebrow"><LightningIcon size={18}/>回放时刻的安排</span><h2>{slot ? heading : '安排还没有有效证据'}</h2></div>
            <div><p className="fv-action-time">{selected ? householdName(selected.id) : '三户总览'} · {slot ? `${dateLabel(slot.start)} ${timeLabel(slot.start)}–${timeLabel(slot.end)}` : '缺少有效配对'}</p>
              {selected && slot?.status === 'valid' ? <p>请求 {number(slot.policy.requestedBatteryKw)} kW · 模拟接受 {number(slot.policy.batteryKw)} kW。{slot.policy.reason || '未附原始原因。'}</p>
                : <p>{selected ? '当前家庭未完整通过，不能判断全窗收益。' : '每户独立安排，使用该户相同输入与物理约束。'}</p>}
            </div>
          </section>

          {rows.length > 1 && <section className="fv-households" aria-label="逐户运行与安排">
            {rows.map(({ site, slot: current }) => <button key={site.id} className={`is-${changeDirection(site.summary.improvementEur)}`} onClick={() => onSite(site.id)}><span>{householdName(site.id)} · {site.status === 'valid' ? '配对有效' : '不可比'}</span><b>{changeText(site.summary.improvementEur, 'total')} {evidenceNumber(site.summary.improvementEur) === null ? '—' : `€${number(Math.abs(evidenceNumber(site.summary.improvementEur)!))}`}</b><small>{current?.status === 'valid' ? actionText(current.policy.requestedBatteryKw) : '当前槽未知'} · 全回放期间</small></button>)}
          </section>}
          {(selected ? selected.status === 'invalid' : comparison.status === 'invalid') && <p className="fv-warning" role="status">{selected ? `${householdName(selected.id)}不可比：` : `${validSites}/3 户可比，暂无完整三户总数。`}{selected?.issues[0]?.message || comparison.issues[0]?.message || '运行或结算未通过完整验证。'}</p>}
          {selected?.status === 'valid' && comparison.status === 'invalid' && <p className="fv-warning" role="status">当前家庭配对有效。{validSites}/3 户可比，暂无完整三户总数。</p>}

          <section className="fv-comparison" aria-labelledby="comparison-title">
            <div className="family-section-title"><div><h2 id="comparison-title">把这笔对照看清楚</h2><p>现金差与期末电量价值分别计算，再得到综合差。下方日期只切换动作，不改变全窗金额。</p></div></div>
            <details className="fv-details"><summary>展开双方费用、期末电量与结算依据</summary>
              <p>购售电现金差 = 原生自用现金成本 − 本策略现金成本。期末剩余电量价值修正 = 期末电量差 × 各户本次平均买价；它不是到账的钱。两项相加为综合改善。本策略与原生自用模型使用该户相同输入与物理约束。</p>
              <div className="fv-table-scroll"><table><thead><tr><th>全窗结果</th><th>本策略</th><th>原生自用</th><th>证据</th></tr></thead><tbody>
                <SummaryRow label="购售电净支出" policy={summary?.policyCashCostEur} native={summary?.nativeCashCostEur} unit="EUR"/>
                <SummaryRow label="期末剩余电量" policy={summary?.policyFinalEnergyKwh} native={summary?.nativeFinalEnergyKwh} unit="kWh"/>
              </tbody></table></div>
              <p>期末电量差 {evidenceValue(summary?.terminalEnergyDeltaKwh, 'kWh')}；价值修正 {evidenceValue(summary?.terminalCorrectionEur, 'EUR')}。现金、库存与综合差分别保留证据状态。</p>
              <ul>{sites.map(site => <li key={site.id}>{householdName(site.id)}：期末估值买价 {evidenceValue(site.summary.valuationPriceEurPerKwh, 'EUR/kWh', 5)}；初始电量 {site.initialSocPercent.value === null ? '未知' : `${number(site.initialSocPercent.value, 0)}%`}（{evidenceLabel(site.initialSocPercent)}）。</li>)}</ul>
              <p>综合差采用本次冻结的库存估值修正，不是官方得分。每户先结算再相加，不抵消不同家庭的买卖电。</p>
            </details>
          </section>

          <section id="comparison-timeline" className="fv-timeline" aria-labelledby="timeline-title">
            <div className="family-section-title"><div><h2 id="timeline-title">差异发生在哪一段？</h2><p>只看同一运行、同一配对时隙。这里切时刻，上方仍是全回放期间对照。</p></div></div>
            <div className="fv-timeline-controls"><label>查看动作日期<select aria-label="查看动作日期" value={day} onChange={event => { onDay(event.target.value); const first = anchor?.slots.find(item => item.start.startsWith(event.target.value)); if (first) onSlot(first.slotIndex); }}>{days.map(value => <option key={value} value={value}>{dateLabel(value)}</option>)}</select></label>
              <label>查看动作时刻<select aria-label="查看动作时刻" value={slot?.slotIndex ?? ''} onChange={event => onSlot(Number(event.target.value))}>{daySlots.map(item => <option key={item.pairId} value={item.slotIndex}>{timeLabel(item.start)}–{timeLabel(item.end)}</option>)}</select></label></div>
            {daySlots.length ? <>
              <PairedChart slots={daySlots} allSites={sites.map(site => site.slots)} selectedIndex={slot?.slotIndex ?? -1}/>
              <label className="fv-slider">浏览配对时隙<input aria-label="浏览配对时隙" type="range" min="0" max={Math.max(0, daySlots.length - 1)} value={Math.max(0, daySlots.findIndex(item => item.slotIndex === slot?.slotIndex))} onChange={event => onSlot(daySlots[Number(event.target.value)].slotIndex)}/></label>
              <p className="fv-selected-time"><ClockIcon size={17}/>{dateLabel(slot?.start)} {timeLabel(slot?.start)}–{timeLabel(slot?.end)} · 15分钟模拟结果</p>
              {rows.map(({ site, slot: current }) => <article className="fv-slot" key={site.id}><h3>{householdName(site.id)} · {current?.status === 'valid' ? actionText(current.policy.requestedBatteryKw) : '此配对槽不可比'}</h3>
                {current?.status === 'valid' ? <><p>共同输入：阳光 {number(current.common.pvKw)} kW，家庭用电 {number(current.common.loadKw)} kW；买价 {number(current.common.buyPriceEurPerKwh, 5)}、卖价 {number(current.common.sellPriceEurPerKwh, 5)} EUR/kWh。</p>
                  <div className="fv-table-scroll"><table><thead><tr><th>本时隙模拟结果</th><th>本策略</th><th>原生自用</th></tr></thead><tbody>
                    <FlowRow label="请求电池功率 · 正放电／负充电" a={current.policy.requestedBatteryKw} b={current.native.requestedBatteryKw} unit="kW"/>
                    <FlowRow label="电池功率 · 正放电／负充电" a={current.policy.batteryKw} b={current.native.batteryKw} unit="kW"/>
                    <FlowRow label="从电网买入" a={current.policy.gridImportKw} b={current.native.gridImportKw} unit="kW"/>
                    <FlowRow label="向电网送出" a={current.policy.gridExportKw} b={current.native.gridExportKw} unit="kW"/>
                    <FlowRow label="时隙结束剩余电量" a={current.policy.energyEndKwh} b={current.native.energyEndKwh} unit="kWh"/>
                    <FlowRow label="本时隙购售电净支出" a={current.policy.costEur} b={current.native.costEur} unit="EUR"/>
                  </tbody></table></div><p>{current.policy.reason || '此槽没有原始决策动机，不补写原因。'}</p><small className="fv-source-id">配对 {current.pairId} · 实际预测 {current.actualHorizonSlots} 个时隙</small></>
                  : <p>{current?.issues[0]?.message || site.issues[0]?.message || '缺少同配对有效结果，不能用另一户或旧看板补齐。'}</p>}
              </article>)}
            </> : <p className="fv-warning">没有有效配对时隙。上方金额不会由不完整曲线补算。</p>}
          </section>
          <section id="comparison-source" className="fv-source-entry"><Dialog.Trigger asChild><button onClick={event => { sourceTrigger.current = event.currentTarget; }}>数据来源与本地实验假设<ArrowRightIcon size={17}/></button></Dialog.Trigger><p>查看固定数据集、因果预测边界、原始声明与文件指纹。</p></section>
        </>}
    <section className="fv-sample"><div><span className="family-eyebrow"><BatteryHighIcon size={19}/>独立的固定样例</span><h2>想试一件用电需求？</h2><p>用固定样例看看要求怎样改变安排。不是上方所选家庭的真实参数，也不修改回放结果或设备。</p></div><button className="family-primary" onClick={onSample}>试算固定样例<ArrowRightIcon size={18}/></button></section>
    <footer className="family-footer"><span>回放配对证据与固定样例分开查看。</span><button onClick={onProfessional}>查看独立专业记录<ArrowRightIcon size={17}/></button></footer>
  </main>
    {comparison && <Dialog.Portal><Dialog.Overlay className="fv-dialog-overlay"/><Dialog.Content className="fv-details fv-source-dialog" onCloseAutoFocus={event => { event.preventDefault(); sourceTrigger.current?.focus(); }}>
      <header className="fv-dialog-heading"><div><Dialog.Title>数据来源与本地实验假设</Dialog.Title><Dialog.Description>离线历史回放模型对照 · 非真实账单／官方成绩</Dialog.Description></div><Dialog.Close className="fv-dialog-close" aria-label="关闭实验依据"><XIcon size={22}/></Dialog.Close></header>
      <div className="fv-source-copy"><p>数据集 {comparison.datasetId} · 离线历史回放模型对照。计算值不是实际账单，实验约定不是设备回读。</p>
            <ul>{comparison.assumptions.map(item => <li key={item.id}>{assumptionText[item.id] || item.statement}（{evidenceLabel({ value: null, status: item.status, reason: null })}）</li>)}</ul>
            <details><summary>原始声明、{comparison.issues.length}条运行提示与文件指纹</summary>
              <ul>{comparison.assumptions.map(item => <li key={item.id}>{item.id}：{item.statement}<br/>{item.impact}</li>)}</ul>
              {comparison.issues.length > 0 && <ul>{comparison.issues.map((item, index) => <li key={`${item.code}-${index}`}>{item.siteId ? `${householdName(item.siteId)}／槽${item.slotIndex}：` : ''}{item.code}：{item.message}</li>)}</ul>}
              <p>{comparison.provenance.fixedModelTrainingWindow.statement}</p>
              <p className="fv-source-id">运行 {comparison.runId}<br/>配置 SHA-256 {comparison.provenance.configSha256}</p>
              <ul>{[...comparison.provenance.inputFiles, ...comparison.provenance.modelFiles, ...comparison.provenance.codeFiles].map(item => <li className="fv-source-id" key={item.path}>{item.path}<br/>SHA-256 {item.sha256}</li>)}</ul>
              <ul>{comparison.provenance.tariffHashes.map(item => <li className="fv-source-id" key={item.siteId}>{householdName(item.siteId)}价格指纹<br/>本策略 {item.policySha256}<br/>原生自用 {item.nativeSha256}</li>)}</ul>
            </details></div>
    </Dialog.Content></Dialog.Portal>}
  </Dialog.Root>;
}

function evidenceValue(item: Evidence | undefined, unit: string, digits = 2) {
  const value = evidenceNumber(item);
  return value === null ? `暂无数值（${evidenceLabel(item)}）` : `${number(value, digits)} ${unit}`;
}
function Difference({ title, item, kind, detail }: { title: string; item?: Evidence; kind: 'cash' | 'total' | 'inventory'; detail: string }) {
  const value = evidenceNumber(item);
  return <article className={`family-life-card fv-metric is-${changeDirection(item)}`}><div className="family-card-label"><ChartLineUpIcon size={21}/>{title}</div><h3>{changeText(item, kind)}</h3><strong>{value === null ? '—' : `€${number(Math.abs(value))}`}</strong><p>{detail}</p><small>{evidenceLabel(item)}{item?.reason ? ` · ${item.reason}` : ''}</small></article>;
}
function SummaryRow({ label, policy, native, unit }: { label: string; policy?: Evidence; native?: Evidence; unit: string }) {
  return <tr><th>{label}</th><td>{evidenceValue(policy, unit)}</td><td>{evidenceValue(native, unit)}</td><td>{evidenceLabel(policy)}／{evidenceLabel(native)}</td></tr>;
}
function FlowRow({ label, a, b, unit }: { label: string; a: number | null; b: number | null; unit: string }) {
  return <tr><th>{label}</th><td>{number(a, unit === 'EUR' ? 4 : 2)} {unit}</td><td>{number(b, unit === 'EUR' ? 4 : 2)} {unit}</td></tr>;
}
function PairedChart({ slots, allSites, selectedIndex }: { slots: ComparisonSlot[]; allSites: ComparisonSlot[][]; selectedIndex: number }) {
  const values = slots.map(slot => {
    const paired = allSites.map(site => site.find(item => item.slotIndex === slot.slotIndex && item.status === 'valid'));
    if (paired.some(item => !item)) return null;
    return { policy: paired.reduce((sum, item) => sum + item!.policy.gridImportKw!, 0), native: paired.reduce((sum, item) => sum + item!.native.gridImportKw!, 0) };
  });
  const maximum = Math.max(1, ...values.flatMap(value => value ? [value.policy, value.native] : []));
  const x = (index: number) => 48 + index / Math.max(1, slots.length - 1) * 704;
  const y = (value: number) => 170 - value / maximum * 135;
  const line = (key: 'policy' | 'native') => {
    let connected = false;
    return values.map((value, index) => { if (!value) { connected = false; return ''; } const segment = `${connected ? 'L' : 'M'}${x(index).toFixed(1)},${y(value[key]).toFixed(1)}`; connected = true; return segment; }).join(' ');
  };
  const chosen = slots.findIndex(slot => slot.slotIndex === selectedIndex);
  return <div className="fv-chart"><div className="fv-legend"><span><i/>本策略</span><span><i/>原生自用</span><b>{allSites.length > 1 ? '各户买入功率之和' : '从电网买入功率'} · kW</b></div>
    <svg viewBox="0 0 800 205" role="img" aria-label="同配对时隙的本策略与原生自用电网买入功率；缺失区间不连线">
      {[0, .5, 1].map(fraction => <g key={fraction}><line x1="48" x2="752" y1={y(maximum * fraction)} y2={y(maximum * fraction)} className="fv-grid-line"/><text x="38" y={y(maximum * fraction) + 4} textAnchor="end">{number(maximum * fraction, 1)}</text></g>)}
      <path d={line('native')} className="fv-line-native"/><path d={line('policy')} className="fv-line-policy"/>
      {chosen >= 0 && <line x1={x(chosen)} x2={x(chosen)} y1="27" y2="174" className="fv-chart-cursor"/>}
      <text x="48" y="195">{timeLabel(slots[0]?.start)}</text><text x="752" y="195" textAnchor="end">{timeLabel(slots.at(-1)?.end)}</text>
    </svg><small>曲线只展示买入功率，费用仍逐户按买卖原价结算。与上方金额来自同一运行。</small></div>;
}
