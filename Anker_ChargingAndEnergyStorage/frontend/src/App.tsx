import { useEffect, useMemo, useState } from 'react';
import {
  ArrowDownIcon, ArrowDownLeftIcon, ArrowRightIcon, ArrowSquareOutIcon, ArrowUpRightIcon,
  BatteryHighIcon, CalendarBlankIcon, CaretDownIcon, ChartLineUpIcon, CheckIcon,
  ClockCounterClockwiseIcon, CopyIcon, DownloadSimpleIcon, FileTextIcon, GaugeIcon,
  HouseLineIcon, InfoIcon, LeafIcon, LightningIcon, PauseIcon, PlayIcon, PlugsConnectedIcon,
  ShieldCheckIcon, SlidersHorizontalIcon, SparkleIcon, SunIcon, WarningCircleIcon, XIcon,
} from '@phosphor-icons/react';
import HouseScene from './HouseScene';
import EnergyChart from './EnergyChart';
import { AnimatedTabs } from './components/ui/animated-tabs';
import { StatefulButton } from './components/ui/stateful-button';
import FamilyExperience from './FamilyExperience';
import ConsumerHome from './ConsumerHome';
import type { ComparisonSelection } from './valueComparison';
import ViewSwitcher, { initialView, type EnergyView } from './ViewSwitcher';
import { dateLabel, integrate, number, siteEnergy, timeLabel, type Dashboard, type Dataset, type Point, type Site } from './types';

type Page = 'overview' | 'analysis' | 'history' | 'report' | 'integrations';
const navigation = [
  { id: 'overview' as const, label: '能源总览', icon: GaugeIcon },
  { id: 'analysis' as const, label: '能源分析', icon: ChartLineUpIcon },
  { id: 'history' as const, label: '调度记录', icon: ClockCounterClockwiseIcon },
  { id: 'report' as const, label: '用电报告', icon: FileTextIcon },
  { id: 'integrations' as const, label: '连接与集成', icon: PlugsConnectedIcon },
];
const siteNames = ['家庭 01', '家庭 02', '家庭 03'];
const siteDescriptions = ['三站调度 · 站点 1', '三站调度 · 站点 2', '三站调度 · 站点 3'];

async function request<T,>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal });
  if (!response.ok) throw new Error(response.status === 404 ? '没有找到所选回放数据，请尝试其他记录。' : `数据服务暂时不可用（${response.status}）`);
  return response.json();
}

function sum(points: (Point | undefined)[], key: keyof Point): number | null {
  const values = points.map(p => p?.[key]).filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  return values.length === points.length && values.length ? values.reduce((a, b) => a + b, 0) : null;
}
function aggregate(sites: Site[]): Point[] {
  const times = [...new Set(sites.flatMap(s => s.series.map(p => p.time)))].sort();
  const maps = sites.map(s => new Map(s.series.map(p => [p.time, p])));
  return times.map(time => {
    const ps = maps.map(m => m.get(time));
    const socs = ps.map(p => p?.soc);
    return { time, pvKw: sum(ps, 'pvKw'), loadKw: sum(ps, 'loadKw'), batteryKw: sum(ps, 'batteryKw'), gridKw: sum(ps, 'gridKw'), soc: socs.every(s => s != null) ? socs.reduce<number>((a, b) => a + b!, 0) / socs.length : null };
  });
}
function normalizedReason(point?: Point | null) {
  if (!point) return '暂无可用记录';
  if ((point.batteryKw ?? 0) < -.03) return '正在为下一段用电储备能量';
  if ((point.batteryKw ?? 0) > .03) return '储能正在参与家庭供能';
  if ((point.pvKw ?? 0) > (point.loadKw ?? Infinity)) return '阳光正在覆盖家庭用电';
  return '按当前记录维持能源平衡';
}

export default function App() {
  const [view, setView] = useState<EnergyView>(initialView);
  const [comparisonSelection, setComparisonSelection] = useState<ComparisonSelection>({ runId: null, siteId: 'site_1', day: '2026-10-13', slotIndex: 0 });
  const [page, setPage] = useState<Page>('overview');
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetId, setDatasetId] = useState('');
  const [data, setData] = useState<Dashboard | null>(null);
  const [siteId, setSiteId] = useState('all');
  const [day, setDay] = useState('');
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [toast, setToast] = useState('');
  const [historyFilter, setHistoryFilter] = useState('all');

  useEffect(() => {
    if (view !== 'professional') return;
    const controller = new AbortController();
    request<{ datasets: Dataset[] }>('/api/datasets', controller.signal).then(result => setDatasets(result.datasets)).catch(() => {});
    return () => controller.abort();
  }, [retry, view]);
  useEffect(() => {
    if (view !== 'professional') return;
    const controller = new AbortController();
    setLoading(true); setError(''); setPlaying(false);
    request<Dashboard>(`/api/dashboard${datasetId ? `?dataset=${encodeURIComponent(datasetId)}` : ''}`, controller.signal)
      .then(result => {
        if (!result.sites?.length) throw new Error('这份回放还没有三站记录，请选择其他数据集。');
        setData(result); setSiteId(result.sites[0].id); setHistoryFilter('all');
        const firstDay = result.sites[0].series[0]?.time.slice(0, 10) ?? '';
        setDay(firstDay);
        const first = result.sites[0].series.filter(p => p.time.startsWith(firstDay));
        setCursor(Math.max(0, first.findIndex(p => timeLabel(p.time) === '12:00')));
      }).catch(e => { if (e.name !== 'AbortError') { setData(null); setError(e.message || '无法读取数据，请检查只读数据服务是否启动。'); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [datasetId, retry, view]);
  useEffect(() => { if (!toast) return; const timer = setTimeout(() => setToast(''), 3500); return () => clearTimeout(timer); }, [toast]);

  const days = useMemo(() => [...new Set(data?.sites.flatMap(s => s.series.map(p => p.time.slice(0, 10))) ?? [])].sort(), [data]);
  const selectedSites = useMemo(() => data?.sites.filter(s => siteId === 'all' || s.id === siteId) ?? [], [data, siteId]);
  const series = useMemo(() => aggregate(selectedSites).filter(p => !day || p.time.startsWith(day)), [selectedSites, day]);
  const point = series[Math.min(cursor, Math.max(0, series.length - 1))] ?? null;
  const energy = useMemo(() => siteEnergy(selectedSites, day), [selectedSites, day]);
  const matching = (site: Site) => site.series.find(p => p.time === point?.time);
  const selectedName = siteId === 'all' ? '全部家庭' : siteNames[data?.sites.findIndex(s => s.id === siteId) ?? 0] || '当前家庭';
  const history = useMemo(() => selectedSites.flatMap((site, i) => site.series.filter(p => !day || p.time.startsWith(day)).map(p => ({ ...p, siteName: siteNames[data?.sites.findIndex(s => s.id === site.id) ?? i], siteId: site.id }))).filter(p => historyFilter === 'all' || historyFilter === 'charge' && (p.batteryKw ?? 0) < -.01 || historyFilter === 'discharge' && (p.batteryKw ?? 0) > .01).sort((a, b) => b.time.localeCompare(a.time)), [selectedSites, day, historyFilter, data]);

  useEffect(() => {
    if (!playing || series.length < 2) return;
    const timer = setInterval(() => setCursor(c => { if (c >= series.length - 1) { setPlaying(false); return c; } return c + 1; }), 700);
    return () => clearInterval(timer);
  }, [playing, series.length]);

  useEffect(() => {
    if (!sourceOpen) return;
    const before = document.activeElement as HTMLElement | null;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSourceOpen(false);
      if (event.key === 'Tab') {
        const buttons = document.querySelectorAll<HTMLButtonElement>('.source-dialog button');
        if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons[buttons.length - 1]?.focus(); }
        else if (!event.shiftKey && document.activeElement === buttons[buttons.length - 1]) { event.preventDefault(); buttons[0]?.focus(); }
      }
    };
    document.addEventListener('keydown', closeOnEscape);
    const scroll = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.removeEventListener('keydown', closeOnEscape); document.body.style.overflow = scroll; before?.focus(); };
  }, [sourceOpen]);

  function downloadReport() {
    if (!data || loading || error) return;
    const rows = [
      '# 电击小子 · 家庭能源回放总结', '',
      `范围：${day || `${data.dataset.timeRange?.start} 至 ${data.dataset.timeRange?.end}`}`,
      `家庭：${selectedName}`, `数据来源：${data.dataset.source || data.dataset.label}（历史回放，非实时设备）`, '',
      `- 光伏发电：${number(energy.pv)} kWh`, `- 家庭用电：${number(energy.load)} kWh`,
      `- 电网购电（平衡推导）：${number(energy.imported)} kWh`, `- 电网送电（平衡推导）：${number(energy.exported)} kWh`, '',
      '## 计算口径', '逐站按相邻正常15分钟记录的前值积分，再相加；保留午夜右端点。断档不补值，不把LP预测目标当账单。',
      '未提供官方结算数据，本报告不计算节省率、金额或未观测月份的月报。', '',
      '## 数据提示', ...(data.warnings?.map(w => `- ${w}`) ?? ['- 无额外提示']),
    ];
    const url = URL.createObjectURL(new Blob([rows.join('\n')], { type: 'text/markdown;charset=utf-8' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `电击小子-用电总结-${day || data.dataset.id}.md`; anchor.click(); URL.revokeObjectURL(url);
    setToast('用电总结已导出');
  }

  function changeView(next: EnergyView) {
    setView(next); setPlaying(false); setSourceOpen(false);
    try { localStorage.setItem('dianji-energy-view', next); } catch { /* Optional preference only. */ }
    const url = new URL(location.href); url.searchParams.set('view', next);
    window.history.replaceState(null, '', url);
  }

  if (view === 'family' || view === 'assistant') return <ConsumerHome onView={changeView}/>;

  if (view !== 'professional') return <FamilyExperience view={view === 'sample' ? 'assistant' : 'family'} onView={next => changeView(next === 'assistant' ? 'sample' : next)}
    selection={comparisonSelection} onSelection={setComparisonSelection}
    onProfessional={() => { setPage('overview'); changeView('professional'); }}/>
  ;

  return <div className="app-shell">
    <a className="skip-link" href="#main">跳到主要内容</a>
    <aside className="sidebar">
      <a className="brand" href="#overview" onClick={() => setPage('overview')} aria-label="电击小子首页"><span className="brand-symbol"><LightningIcon weight="fill" size={23}/></span><span>电击小子<small>HOME ENERGY</small></span></a>
      <div className="workspace-label">我的能源空间 <span>03</span></div>
      <nav aria-label="主导航">{navigation.map(item => <button key={item.id} className={page === item.id ? 'nav-item active' : 'nav-item'} aria-current={page === item.id ? 'page' : undefined} onClick={() => setPage(item.id)}><item.icon size={20} weight={page === item.id ? 'duotone' : 'regular'}/><span>{item.label}</span>{page === item.id && <span className="nav-active-dot"/>}</button>)}</nav>
      <div className="sidebar-note"><span className="note-mark"><LeafIcon size={21}/></span><strong>让能源回到生活里</strong><p>不必时刻关注电量，<br/>但可以随时了解它。</p><span className="note-line"/></div>
      <div className="sidebar-bottom"><ShieldCheckIcon size={18}/><div>只读回放空间<small>不会下发设备控制</small></div></div>
      <div className="profile"><div className="profile-avatar">电</div><div>电击小子团队<small>家庭能源工作台</small></div><span className="version">V0.2</span></div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><div className="breadcrumb">我的能源空间 <span>/</span><b>{navigation.find(n => n.id === page)?.label}</b></div><ViewSwitcher value={view} onChange={changeView}/><div className="topbar-right"><span className="replay-indicator"><i/>历史回放</span><button className="icon-button" aria-label="查看数据来源与计算口径" onClick={() => setSourceOpen(true)}><InfoIcon size={20}/></button><span className="mini-avatar">电</span></div></header>
      <main id="main" className="main-content">
        <section className="page-heading flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div><div className="eyebrow">DIANJI / HOME ENERGY WORKSPACE</div><h1>{page === 'overview' ? '每一度电，都有去处。' : { analysis: '看见能源的日常。', history: '每一次调度，都有记录。', report: '把用电，读成生活。', integrations: '你的能源，不止一个入口。' }[page]}</h1><p className="professional-source">独立历史记录，非本次 FV 配对 · {data?.dataset.id || '正在读取独立数据集'} · 只读</p></div>
          <StatefulButton className="button-outline" onAction={downloadReport} disabled={!data || loading || !!error}>导出用电总结</StatefulButton>
        </section>
        <div className="filterbar flex flex-wrap items-center gap-3">
          <AnimatedTabs tabs={[{ title: "三户总览", value: "all" }, ...(data?.sites.map((s, i) => ({ title: siteNames[i], value: s.id })) ?? [])]} value={siteId} onChange={value => { setSiteId(value); setCursor(0); setPlaying(false); }}/>
          <label className="select-wrap"><CalendarBlankIcon size={17}/><span className="sr-only">选择日期</span><select aria-label="选择日期" value={day} onChange={e => {setDay(e.target.value); setCursor(0); setPlaying(false);}}>{days.map(d => <option key={d} value={d}>{dateLabel(d)}</option>)}</select><CaretDownIcon size={12}/></label>
          <label className="select-wrap dataset-select"><ClockCounterClockwiseIcon size={17}/><span className="sr-only">选择回放记录</span><select aria-label="选择回放记录" value={datasetId || data?.dataset.id || ''} onChange={e => setDatasetId(e.target.value)}>{datasets.length ? datasets.map(d => <option key={d.id} value={d.id}>{d.label}</option>) : <option value="">正在读取记录</option>}</select><CaretDownIcon size={12}/></label>
          <span className="filter-note"><i/>{data ? `${data.sites.length} 个家庭 · 只读数据` : '正在连接本地数据'}</span>
        </div>
        <div id="energy-content" role="tabpanel" aria-labelledby={`family-${siteId}`} tabIndex={-1}>
        {loading ? <div className="loading-layout" aria-busy="true" aria-label="正在读取能源数据"><div className="skeleton skeleton-metrics"/><div className="skeleton skeleton-scene"/><div className="skeleton skeleton-chart"/></div> : error ? <section className="error-state" role="alert"><WarningCircleIcon size={36}/><h2>能源记录暂时没有连上</h2><p>{error}</p><small>请在前端目录启动 python server.py，然后重试。</small><button className="button button-primary" onClick={() => setRetry(v => v + 1)}>重新连接<ArrowRightIcon size={16}/></button></section> : data && <>
          {(page === 'overview' || page === 'analysis') && <>
            <section className="metrics-grid" aria-label="所选日期能源汇总">
              <Metric label="光伏发电" value={energy.pv} icon={<SunIcon size={20}/>} note="来自家里的阳光" accent/>
              <Metric label="家庭用电" value={energy.load} icon={<HouseLineIcon size={20}/>} note="所选日期 · 已记录区间"/>
              <Metric label="电网购电" value={energy.imported} icon={<ArrowDownLeftIcon size={20}/>} note="由功率平衡推导"/>
              <Metric label="电网送电" value={energy.exported} icon={<ArrowUpRightIcon size={20}/>} note="由功率平衡推导"/>
            </section>
            {page === 'overview' && <div className="overview-grid">
              <section className="panel flow-panel"><div className="panel-heading"><div><h2>家里的能量流</h2><p>{selectedName} · {dateLabel(point?.time)} {timeLabel(point?.time)}</p></div><span className="badge"><span className="status-dot"/>记录快照</span></div><HouseScene point={point}/><div className="playback"><button className="play-button" aria-label={playing ? '暂停回放' : '播放回放'} onClick={() => {if(cursor >= series.length - 1) setCursor(0); setPlaying(v => !v);}} disabled={series.length < 2}>{playing ? <PauseIcon size={13} weight="fill"/> : <PlayIcon size={13} weight="fill"/>}</button><time>{timeLabel(point?.time)}</time><label className="sr-only" htmlFor="playback-range">回放时间</label><input id="playback-range" type="range" min="0" max={Math.max(0, series.length - 1)} value={Math.min(cursor, Math.max(0, series.length - 1))} onChange={e => {setCursor(Number(e.target.value)); setPlaying(false);}}/><span className="playback-end">{timeLabel(series.at(-1)?.time)}</span><span className="playback-tag">15 MIN / STEP</span></div></section>
              <section className="insight-panel"><div className="insight-heading"><span><LightningIcon size={18} weight="duotone"/>能源小结</span><SparkleIcon className="insight-star" size={24} weight="duotone"/></div><span className="insight-kicker">这一刻，家里正在</span><h2>{normalizedReason(point)}</h2><p>{point ? `光伏供给 ${number(point.pvKw)} kW，家庭需求 ${number(point.loadKw)} kW。${point.batteryKw == null ? '当前记录未包含储能功率。' : `储能${point.batteryKw < -.01 ? '正在充电' : point.batteryKw > .01 ? '正在放电' : '处于待机状态'}。`}` : '选择一个记录时刻，查看能源如何流动。'}</p><div className="insight-divider"/><div className="soc-heading"><span>储能电量{siteId === 'all' ? ' · 三站均值' : ''}</span><strong>{number(point?.soc, 1)}<small>%</small></strong></div><div className="soc-track"><span style={{width: `${Math.max(0, Math.min(100, point?.soc ?? 0))}%`}}/></div><div className="insight-footnote"><ShieldCheckIcon size={15}/><span>储能为已存策略动作，非设备执行回读</span></div><button className="insight-link" onClick={() => setPage('history')}>查看原始决策记录<ArrowUpRightIcon size={17}/></button></section>
            </div>}
            <div className={page === 'analysis' ? 'analysis-grid' : 'bottom-grid'}>
              <section className="panel chart-panel"><div className="panel-heading"><div><h2>用电的节奏</h2><p>{dateLabel(day)} · {selectedName} · 15 分钟观测</p></div><span className="subtle-label">功率趋势</span></div><EnergyChart points={series}/><p className="chart-caption">电网曲线为所选家庭净交换功率；上方电量按各站分别积分再相加。缺失时段不连线。</p></section>
              <section className="panel sites-panel"><div className="panel-heading"><div><h2>我的家庭</h2><p>三个家庭，各有自己的节奏</p></div><span className="count-badge">{data.sites.length}</span></div><div className="site-list">{data.sites.map((s, i) => {const p=matching(s); return <button key={s.id} className={`site-row ${siteId === s.id ? 'selected' : ''}`} onClick={() => setSiteId(siteId === s.id ? 'all' : s.id)}><span className={`site-thumbnail house-${i}`}><HouseLineIcon size={28} weight="duotone"/></span><span className="site-row-name"><b>{siteNames[i]}</b><small>{siteDescriptions[i]}</small></span><span className="site-row-value"><b>{number(p?.soc, 1)}<small>%</small></b><span>{p ? p.batteryKw == null ? '动作未提供' : p.batteryKw < -.01 ? '充电中' : p.batteryKw > .01 ? '放电中' : '待机' : '无同期记录'}</span></span><ArrowUpRightIcon size={15}/></button>;})}</div><button className="text-link" onClick={() => {setPage('analysis');setSiteId('all');}}>比较三站能源表现<ArrowRightIcon size={15}/></button></section>
            </div>
            {page === 'analysis' && <section className="panel comparison-panel"><div className="panel-heading"><div><h2>三站能量对照</h2><p>同一日期、同一口径，按可用记录积分</p></div></div><div className="comparison-grid">{data.sites.map((s, i) => {const ps=s.series.filter(p=>p.time.startsWith(day)); const pv=integrate(s.series,'pvKw','all',day);const load=integrate(s.series,'loadKw','all',day);return <div className="comparison-item" key={s.id}><h3>{siteNames[i]}</h3><dl><div><dt>光伏发电</dt><dd>{number(pv)} <small>kWh</small></dd></div><div><dt>家庭用电</dt><dd>{number(load)} <small>kWh</small></dd></div><div><dt>有效记录</dt><dd>{ps.length} <small>条</small></dd></div></dl><button className="text-link" onClick={() => setSiteId(s.id)}>查看本站<ArrowRightIcon size={14}/></button></div>;})}</div></section>}
          </>}
          {page === 'history' && <section className="panel history-panel"><div className="panel-heading"><div><h2>原始调度记录</h2><p>{data.dataset.kind === 'report' ? '读自组员控制器 XML 报告，最新时刻在前' : '输入采样仅含观测，不含策略动作或决策解释'}</p></div><label className="select-wrap"><SlidersHorizontalIcon size={17}/><span className="sr-only">筛选调度方向</span><select disabled={data.dataset.kind !== 'report'} value={historyFilter} onChange={e=>setHistoryFilter(e.target.value)}><option value="all">全部方向</option><option value="charge">充电记录</option><option value="discharge">放电记录</option></select></label></div><div className="table-scroll"><table><thead><tr><th>设备时间</th><th>家庭</th><th>储能状态</th><th>电量</th><th>储能功率</th><th>决策解释 · 原始报告</th></tr></thead><tbody>{history.slice(0,180).map(p=><tr key={`${p.siteId}-${p.time}`}><td className="tabular-nums">{timeLabel(p.time)}<small>{dateLabel(p.time)}</small></td><td>{p.siteName}</td><td><span className="badge">{p.batteryKw==null?'未提供':p.batteryKw<-.01?'充电':p.batteryKw>.01?'放电':'待机'}</span></td><td>{number(p.soc,1)}%</td><td>{number(p.batteryKw)} kW</td><td className="reason-cell">{p.reason || '该时刻未提供原始文字解释'}</td></tr>)}</tbody></table>{!history.length&&<div className="empty-chart">当前筛选没有记录，试试其他方向或日期。</div>}</div><div className="table-footnote">显示 {Math.min(history.length,180)} / {history.length} 条记录 · 储能功率正值放电、负值充电</div></section>}
          {page === 'report' && <section className="report-layout"><div className="panel report-paper"><div className="report-masthead"><span>DIANJI / ENERGY JOURNAL</span><LeafIcon size={25}/></div><span className="eyebrow">{dateLabel(day)} · {selectedName}</span><h2>有阳光的日子，<br/>让家多一份从容。</h2><p className="report-intro">这份记录里，光伏产生了 <strong>{number(energy.pv)} kWh</strong> 电，家庭消耗了 <strong>{number(energy.load)} kWh</strong>。从数据出发，重新认识日常能源。</p><div className="report-numbers"><div><span>电网购电 · 推导</span><b>{number(energy.imported)}<small> kWh</small></b></div><div><span>电网送电 · 推导</span><b>{number(energy.exported)}<small> kWh</small></b></div></div><h3>关于收益，我们只说有证据的。</h3><p>当前数据包含功率、储能电量和决策记录，尚未包含可核验的结算账单，因此不展示节省金额和官方节省率。这里也不会把几天的回放写成整月报告。</p><StatefulButton className="button-primary" onAction={downloadReport}>下载这份用电总结</StatefulButton></div><aside className="panel report-method"><ShieldCheckIcon size={28}/><h3>一份可以追溯的总结</h3><p>所选回放：{data.dataset.label}</p><ul><li>来源于三站原始输出</li><li>仅统计有效相邻时段</li><li>缺失记录不补造数据</li><li>预测费用不冒充已付账单</li></ul><button className="text-link" onClick={()=>setSourceOpen(true)}>查看数据与计算口径<ArrowUpRightIcon size={15}/></button></aside></section>}
          {page === 'integrations' && <section className="integration-layout"><div className="panel integration-main"><span className="section-icon"><PlugsConnectedIcon size={28}/></span><h2>让自己的 Agent，<br/>也能读懂家里的能源。</h2><p>前端已接上本地只读数据服务。MCP 插件与跨 Agent 调用仍是接下来的产品计划，当前没有后台授权或设备控制入口。</p><div className="integration-row"><span><b>本地能源数据 API</b><small>三站回放 · 已接通</small></span><span className="badge"><CheckIcon size={13}/>只读可用</span></div>{['Codex','Claude Code','WorkBuddy'].map(name=><div className="integration-row" key={name}><span><b>{name}</b><small>计划通过统一 MCP 服务接入</small></span><span className="badge badge-neutral">未接入</span></div>)}</div><div className="panel api-panel"><div className="panel-heading"><h2>现有只读接口</h2><span className="badge">GET ONLY</span></div><p>供本地前端查询，不包含凭据或控制权限。</p><code>GET /api/datasets</code><code>GET /api/dashboard</code><button className="button button-outline" onClick={async()=>{try{await navigator.clipboard.writeText(`${location.origin}/api/dashboard`);setToast('只读接口地址已复制');}catch{setToast('浏览器不允许复制，请手动复制接口路径');}}}><CopyIcon size={16}/>复制接口地址</button><div className="api-boundary"><ShieldCheckIcon size={20}/><p>数据查询与设备控制分开。这个工作台只读记录，不向 HA 下发指令。</p></div></div></section>}
          <footer className="page-footer"><span><ShieldCheckIcon size={14}/>原始记录驱动 · 非实时设备看板</span><button onClick={()=>setSourceOpen(true)}>数据来源与计算口径<ArrowSquareOutIcon size={13}/></button><span className="footer-brand">DIANJI · MAKE ENERGY FEEL LIKE HOME</span></footer>
        </>}
        </div>
      </main>
    </div>
    {sourceOpen && <div className="modal-backdrop" onClick={()=>setSourceOpen(false)}><section className="source-dialog" role="dialog" aria-modal="true" aria-labelledby="source-title" onClick={e=>e.stopPropagation()}><button className="icon-button dialog-close" aria-label="关闭数据说明" onClick={()=>setSourceOpen(false)} autoFocus><XIcon size={21}/></button><ShieldCheckIcon size={30}/><h2 id="source-title">每个数字，都有出处。</h2><dl><div><dt>回放记录</dt><dd>{data?.dataset.label || '未连接'}</dd></div><div><dt>记录范围</dt><dd>{data?.dataset.timeRange ? `${dateLabel(data.dataset.timeRange.start)} ${timeLabel(data.dataset.timeRange.start)} — ${dateLabel(data.dataset.timeRange.end)} ${timeLabel(data.dataset.timeRange.end)}` : '待数据加载'}</dd></div><div><dt>数据来源</dt><dd className="source-path">{data?.dataset.source || '本地控制器 XML 决策报告'}</dd></div></dl><p>电量逐站按相邻正常 15 分钟时段的前值积分再相加，不跨家庭抵消购售电。储能功率来自已保存的策略动作，并非设备执行回读。电网功率由光伏、负荷和储能功率平衡推导，不是并网电表实测值。期末无下一条记录的时段不外推。</p><p>三站电量百分比取算术均值，不作为单台设备的电量。未提供官方结算账单，不展示节省率；LP 期望费用不是实付账单。</p>{data?.warnings?.length ? <div className="source-warnings"><b>记录提示</b>{data.warnings.map((w,i)=><p key={i}>{w}</p>)}</div> : null}<button className="button button-primary" onClick={()=>setSourceOpen(false)}>知道了<CheckIcon size={16}/></button></section></div>}
    {toast && <div className="toast" role="status"><CheckIcon size={17}/>{toast}</div>}
  </div>;
}

function Metric({label,value,note,icon,accent=false}:{label:string;value:number|null;note:string;icon:React.ReactNode;accent?:boolean}) {
  return <div className={`metric ${accent?'metric-accent':''}`}><div className="metric-label">{icon}<span>{label}</span></div><div className="metric-number">{number(value)}<small>kWh</small></div><div className="metric-note">{accent&&<ArrowDownIcon size={12}/>} {note}</div></div>;
}
