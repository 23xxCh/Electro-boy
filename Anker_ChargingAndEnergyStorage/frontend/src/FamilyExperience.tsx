import './family.css';
import { useEffect, useState } from 'react';
import { LightningIcon, TextAaIcon } from '@phosphor-icons/react';
import AgentChat from './AgentChat';
import ValueComparisonPanel from './ValueComparisonPanel';
import type { EnergyView } from './ViewSwitcher';
import { readValueComparison, type ComparisonSelection, type ValueComparison } from './valueComparison';

type Props = { view: EnergyView; onView: (view: EnergyView) => void; onProfessional: () => void;
  selection: ComparisonSelection; onSelection: (update: (current: ComparisonSelection) => ComparisonSelection) => void };
const sampleSites = [1, 2, 3].map(index => ({ id: `site_${index}`, label: `样例家庭 ${String(index).padStart(2, '0')}` }));

export default function FamilyExperience({ view, onView, onProfessional, selection, onSelection }: Props) {
  const [large, setLarge] = useState(false);
  const [comparison, setComparison] = useState<ValueComparison | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const { siteId, day, slotIndex } = selection;
  const [sampleSite, setSampleSite] = useState('site_1');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(''); setComparison(null);
    void fetch('/api/value-comparison?dataset=dataset_mock_1011_10160249', { signal: controller.signal })
      .then(async response => {
        const body = await response.json().catch(() => null);
        if (!response.ok) throw new Error(typeof body?.error?.message === 'string' ? body.error.message : `配对数据暂时不可用（${response.status}）。`);
        return readValueComparison(body);
      })
      .then(result => {
        if (controller.signal.aborted) return;
        setComparison(result);
        onSelection(current => current.runId === result.runId ? current : { runId: result.runId, siteId: 'site_1', day: result.scope.start.slice(0, 10), slotIndex: 0 });
      })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '没有读到有效配对证据。'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [retry]);

  if (view === 'assistant') return <AgentChat
    household={sampleSites.find(site => site.id === sampleSite)!.label} householdId={sampleSite}
    siteId={sampleSite} sites={sampleSites} onSite={setSampleSite} timeText="固定 8 小时样例"
    context={{ source: '独立固定8小时样例，不是上方回放家庭的真实参数', household: sampleSite }}
    reference={{ pv: '固定样例输入', load: '固定样例输入', soc: '固定样例起点', grid: '试算后查看' }}
    onFamily={() => onView('family')} onProfessional={onProfessional} seed={null} onSeedDone={() => {}}
  />;

  return <div className={`family-app ${large ? 'is-large' : ''}`}>
    <a className="skip-link" href="#family-main">跳到主要内容</a>
    <header className="family-header">
      <a href="?view=family" className="family-brand" onClick={event => { event.preventDefault(); onView('family'); }}><span><LightningIcon size={24} weight="fill"/></span><b>电击小子<small>让用电，简单一点</small></b></a>
      <button className="family-size" onClick={() => onView('family')}>返回家庭助手</button>
      <button className="family-size" aria-pressed={large} onClick={() => setLarge(value => !value)}><TextAaIcon size={23}/>{large ? '标准字' : '大字'}</button>
    </header>
    <ValueComparisonPanel comparison={comparison} loading={loading} error={error} onRetry={() => setRetry(value => value + 1)}
      siteId={siteId} onSite={id => onSelection(current => ({ ...current, siteId: id }))}
      day={day} onDay={value => onSelection(current => ({ ...current, day: value }))}
      slotIndex={slotIndex} onSlot={value => onSelection(current => ({ ...current, slotIndex: value }))}
      onSample={() => onView('assistant')} onProfessional={onProfessional}/>
  </div>;
}
