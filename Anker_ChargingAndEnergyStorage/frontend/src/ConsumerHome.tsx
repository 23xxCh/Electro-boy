import { lazy, Suspense, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { useReducedMotion } from 'motion/react';
import * as Dialog from '@radix-ui/react-dialog';
import { ArrowRightIcon, ArrowUpIcon, BatteryHighIcon, CheckIcon, ClockIcon, GearSixIcon, HouseLineIcon, LeafIcon, LightningIcon, SparkleIcon, WarningCircleIcon, XIcon } from '@phosphor-icons/react';
import { energyWorldFrame, findMessage, getDecision, getEnvironment, getFeed, getMemories, getNotifications, getSession, getStatus, homeSummary, pendingExpired, sendMessage, setNotificationsEnabled, type AssistantNotifications, type AssistantStatus, type DecisionProjection, type DeliveryState, type EnvironmentSnapshot, type Memory, type MessageInput, type MessageResult, type Pending, type Session } from './assistantWeb';
import { evidenceNumber, readValueComparison, type ValueComparison } from './valueComparison';
import type { EnergyView } from './ViewSwitcher';
import DecisionStage from './DecisionStage';
import type { EnergyDevice, EnergyWorldFrame } from './energy-world/types';
import './consumer.css';

const EnergyWorld = lazy(() => import('./energy-world/EnergyWorld'));
const notificationState: Record<string, string> = { queued: '等待发送', deferred: '静默期延后', sending: '正在发送', sent: '已提交飞书', unknown: '发送结果未知', failed: '发送失败', cancelled: '已取消', covered_by_reply: '由即时回复覆盖' };
export function NotificationSection({ ready, data, loading, error, onToggle, onRefresh }: { ready: boolean; data: AssistantNotifications | null; loading: boolean; error: string; onToggle: () => void; onRefresh: () => void }) {
  const settings = data?.settings;
  const recordTime = (value: number) => {
    try { return new Intl.DateTimeFormat('zh-CN', { timeZone: settings?.timezone || 'UTC', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(value * 1000)) + (settings?.timezone ? '' : ' UTC'); }
    catch { return new Date(value * 1000).toISOString().slice(5, 16).replace('T', ' ') + ' UTC'; }
  };
  return <section className="consumer-setting-section consumer-notifications" aria-labelledby="notification-heading">
    <h3 id="notification-heading"><SparkleIcon size={18}/>飞书主动提醒</h3>
    <p>重要进展及时提醒，每日 20:00 汇总；22:00–08:00 静默，无新事实不发。</p>
    {!ready ? <p>连接家庭后可查看提醒设置。</p> : loading ? <p role="status">正在核对提醒设置…</p> : settings ? <>
      <div className="consumer-notification-switch"><strong>{settings.enabled ? settings.needsTimezone ? '已开启，当前暂停发送' : '已开启' : '已关闭'}</strong><button type="button" className="consumer-primary" aria-pressed={settings.enabled} disabled={loading || !settings.enabled && settings.needsTimezone} onClick={onToggle}>{settings.enabled ? '关闭提醒' : '开启提醒'}</button></div>
      <p>家庭时区：{settings.timezone || '尚未设置'}</p>{settings.needsTimezone && <p className="consumer-notification-warning">请先在已绑定飞书发送“时区 &lt;IANA&gt;”设置家庭时区，再刷新这里；不会使用浏览器时区。</p>}
    </> : !error && <p>尚未读取提醒设置。</p>}
    {error && <p role="alert">{error}</p>}
    <small>提醒只提供信息，不会替你保存偏好或确认设备控制。已提交飞书不表示你已读；发送结果未知不会自动重发。</small>
    <button type="button" className="consumer-link" disabled={!ready || loading} onClick={onRefresh}>刷新提醒设置与记录<ArrowRightIcon size={17}/></button>
    {ready && data && <><h4>最近20条提醒状态</h4>{data.items.length === 0 ? <p>还没有提醒记录。首次开启不会补发旧历史。</p> : <ul className="consumer-notification-list">{data.items.map(item => <li key={item.id}><details><summary><strong>{item.title}</strong><span data-delivery-state={item.state}>{notificationState[item.state]}</span></summary><p>{item.text}</p><small>{recordTime(item.updatedAt)} · 本机演练{item.state === 'covered_by_reply' ? ' · 仅表示即时回复曾尝试覆盖' : ''}</small></details></li>)}</ul>}</>}
  </section>;
}
export function EnvironmentDataSection({ ready, snapshot, loading, error, onRead }: { ready: boolean; snapshot: EnvironmentSnapshot | null; loading: boolean; error: string; onRead: () => void }) {
  const data = ready && !loading && !error && snapshot && ['ready', 'partial'].includes(snapshot.status) ? snapshot : null;
  const value = (n: number | null, unit: string) => n === null ? '未提供' : `${n.toFixed(unit === '%' ? 1 : 2)} ${unit}`;
  return <section className="consumer-setting-section consumer-environment-data" aria-labelledby="environment-data-heading">
    <h3 id="environment-data-heading"><LightningIcon size={18}/>环境机数据</h3>
    <p>来源：队友 Release 管线 → 官方 HA · 家庭 1</p>
    <small>尚未连通验证；设备身份与时间基准未核验。此处只读，不参与当前本机调度或控制。</small>
    <p role={error ? 'alert' : 'status'}>{!ready ? '请先连接家庭，再查看环境机数据。' : loading ? '正在读取环境机数据…' : error || (snapshot?.status === 'disabled' ? '未启用：尚未配置环境机管线。' : snapshot?.status === 'unavailable' ? '环境机数据暂不可用，未保留旧读数。' : snapshot?.status === 'partial' ? '已读到部分字段，缺失项显示为未提供。' : snapshot?.status === 'ready' ? '已返回只读快照；尚未连通验证。' : '管线默认关闭，点击后仅查询配置与只读快照。')}</p>
    {data && <><dl>{([['电池电量', data.observation.socPercent, '%'], ['电池实际功率（正放负充）', data.observation.batteryActualKw, 'kW'], ['光伏发电', data.observation.pvKw, 'kW'], ['家庭用电', data.observation.loadKw, 'kW'], ['电网购入', data.observation.gridImportKw, 'kW'], ['电网送出', data.observation.gridExportKw, 'kW']] as const).map(([label, n, unit]) => <div key={label}><dt>{label}</dt><dd>{value(n, unit)}</dd></div>)}</dl><p className="consumer-environment-time">设备时间（原样）：{data.deviceTime || '未提供'}<br/>设备本地时间，时区未核验；这是最近一次主动读取的快照，不代表实时监测。</p></>}
    <button className="consumer-link" disabled={!ready || loading} onClick={onRead}>读取环境机数据<ArrowRightIcon size={17}/></button>
  </section>;
}
const kw = (value: number | null | undefined) => value == null ? '未接入' : `${value.toFixed(2)} kW`;
const pct = (value: number | null | undefined) => value == null ? '未接入' : `${value.toFixed(1)}%`;
const targetText = (value: number | null | undefined) => value == null ? '未接入' : value === 0 ? '待机 0 W' : `${value < 0 ? '充电' : '放电'} ${Math.round(Math.abs(value) * 1000)} W`;
const priceText = (value: number | null | undefined) => value == null ? '未接入' : value.toFixed(4);
const priceKind = (frame: EnergyWorldFrame | null) => frame?.kind === 'actual' ? '执行段计划价' : frame?.kind === 'preview' ? '计划采用价' : '模拟时段价';
function WorldHUD({ frame }: { frame: EnergyWorldFrame | null }) {
  const preview = frame?.kind === 'preview';
  return <div className="ew-hud" aria-label="同一时段能源读数" data-frame-kind={frame?.kind ?? 'empty'} data-slot={frame?.interval.slot ?? ''}>
    <div><span>{preview ? '预计时段末电量' : '电池电量'}</span><strong>{pct(frame?.socPercent)}</strong><small>{preview ? '预计' : '实际'}{targetText(frame?.batteryKw)}{preview ? ' · 未执行' : ` · 目标${targetText(frame?.batteryTargetKw)}`}</small></div>
    <div><span>太阳能</span><strong>{kw(frame?.pvKw)}</strong><small>家庭总用电 {kw(frame?.loadKw)}</small></div>
    <div><span>电网购入</span><strong>{kw(frame?.gridImportKw)}</strong><small>送出 {kw(frame?.gridExportKw)}</small></div>
    <div><span>{priceKind(frame)} · EUR/kWh</span><strong>{priceText(frame?.prices.buyEurKwh)}<small>购</small></strong><small>售 {priceText(frame?.prices.sellEurKwh)}</small></div>
  </div>;
}
function DeviceDetail({ frame, selected, onAsk }: { frame: EnergyWorldFrame | null; selected: EnergyDevice; onAsk: () => void }) {
  const preview = frame?.kind === 'preview';
  return <section className="ew-device-detail" aria-label="选中设备数据" data-frame-kind={frame?.kind ?? 'empty'} data-slot={frame?.interval.slot ?? ''}>
    <div className="ew-device-title"><span>{selected === 'battery' ? <BatteryHighIcon size={21}/> : selected === 'pv' ? <LeafIcon size={21}/> : <LightningIcon size={21}/>}<strong>{selected === 'battery' ? '家庭储能' : selected === 'pv' ? '屋顶光伏' : '电网连接'}</strong></span><span>{preview ? '预计／未执行' : frame?.kind === 'actual' ? '已回读' : '最近已读'}</span></div>
    <dl>{selected === 'battery' ? <><div><dt>{preview ? '预计末电量' : '电池电量'}</dt><dd>{pct(frame?.socPercent)}</dd></div><div><dt>{preview ? '预计动作' : '实际功率'}</dt><dd>{targetText(frame?.batteryKw)}</dd></div><div><dt>{preview ? '此时段预计目标' : '设备目标'}</dt><dd>{targetText(frame?.batteryTargetKw)}</dd></div></> : selected === 'pv' ? <><div><dt>{preview ? '预计发电' : '发电回读'}</dt><dd>{kw(frame?.pvKw)}</dd></div><div><dt>家庭总用电</dt><dd>{kw(frame?.loadKw)}</dd></div></> : <><div><dt>购入／送出</dt><dd>{kw(frame?.gridImportKw)} / {kw(frame?.gridExportKw)}</dd></div><div><dt>购／售价 EUR/kWh</dt><dd>{priceText(frame?.prices.buyEurKwh)} / {priceText(frame?.prices.sellEurKwh)}</dd></div></>}</dl>
    <button className="consumer-link" onClick={onAsk}>解释本次安排<ArrowRightIcon size={15}/></button>
  </section>;
}

type Turn = { id: string; role: 'user' | 'assistant'; text: string; source?: 'web' | 'feishu' | 'agent'; createdAt?: number; result?: MessageResult; deliveryState?: DeliveryState };
type MessageBody = { text: string } | { action: Extract<MessageInput, { action: unknown }>['action'] };
type FeedbackDraft = { episodeId: string; rating: 'suitable' | 'unsuitable' };
const unresolvedKey = 'dianji-assistant-unresolved-request';
const suggestions = [
  { icon: LightningIcon, label: '帮我安排用电', text: '帮我安排这一时段' },
  { icon: BatteryHighIcon, label: '调整备用电量', text: '我想设置电池最低保留电量。' },
];
const phaseText: Record<string, string> = { proposed: '方案已算好，等你确认', confirmed: '你已确认这份方案', dispatched: '已提交，正在等设备回读', acknowledged: '本机设备已回读', settled: '这个时段已结算', outcome: '这次执行已有结果', unknown: '执行结果还需核对', failed: '这次执行没有完成', rejected: '这份方案暂时不能执行' };
const memoryKinds: Record<string, string> = { habit: '日常习惯', preference: '用电偏好', activity: '活动安排', fact: '个人资料', reserve_soc: '备用电量' };
const confirmLabels: Record<string, string> = { memory: '记住这件事', revision_candidate: '确认修改并记住', simulation_plan: '确认并执行这15分钟', sample_plan: '确认样例安排', feedback: '保存反馈', forget: '删除全部资料' };
function ResultIcon({ phase }: { phase: string }) { return ['unknown', 'failed', 'rejected'].includes(phase) ? <WarningCircleIcon size={18}/> : ['acknowledged', 'settled', 'outcome', 'confirmed'].includes(phase) ? <CheckIcon size={18}/> : <ClockIcon size={18}/>; }
const money = (n: number) => `€${Math.abs(n).toFixed(2)}`;
function memoryValue(value: unknown): string {
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (value && typeof value === 'object') return Object.values(value).filter(v => typeof v === 'string' || typeof v === 'number').join(' · ');
  return '已保存的资料';
}
function recoverId() { try { return sessionStorage.getItem(unresolvedKey) || ''; } catch { return ''; } }
function rememberId(id: string) { try { if (id) sessionStorage.setItem(unresolvedKey, id); else sessionStorage.removeItem(unresolvedKey); } catch { /* Request stays visible in this page. */ } }

export default function ConsumerHome({ onView }: { onView: (view: EnergyView) => void }) {
  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [connecting, setConnecting] = useState(true);
  const [decision, setDecision] = useState<DecisionProjection | null>(null);
  const [selectedMode, setSelectedMode] = useState<EnergyWorldFrame['kind']>('observation');
  const [selectedSlot, setSelectedSlot] = useState<number | null>(null);
  const [selectedDevice, setSelectedDevice] = useState<EnergyDevice>('battery');
  const [replay, setReplay] = useState(false);
  const lastEvidence = useRef<string | undefined>(undefined);
  const reducedMotion = useReducedMotion();
  const [decisionError, setDecisionError] = useState('');
  const [feedError, setFeedError] = useState('');
  const decisionRead = useRef(0);
  const feedCursor = useRef<string | null>(null);
  const sessionRefreshNeeded = useRef(false);
  const [connectionError, setConnectionError] = useState('');
  const [drawer, setDrawer] = useState(false);
  const [environment, setEnvironment] = useState<EnvironmentSnapshot | null>(null);
  const [environmentLoading, setEnvironmentLoading] = useState(false);
  const [environmentError, setEnvironmentError] = useState('');
  const environmentRead = useRef(0);
  const [notifications, setNotifications] = useState<AssistantNotifications | null>(null);
  const [notificationLoading, setNotificationLoading] = useState(false);
  const [notificationError, setNotificationError] = useState('');
  const notificationRead = useRef(0);
  const notificationSaving = useRef(false);
  const [sceneExpanded, setSceneExpanded] = useState(false);
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const [memories, setMemories] = useState<Memory[] | null>(null);
  const [memoryError, setMemoryError] = useState('');
  const [draft, setDraft] = useState('');
  const [feedbackDraft, setFeedbackDraft] = useState<FeedbackDraft | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [unresolved, setUnresolved] = useState(recoverId);
  const [submitError, setSubmitError] = useState(() => recoverId() ? '上次提交还没有核对结果，请先查询。' : '');
  const [pending, setPending] = useState<Pending | null>(null);
  const [now, setNow] = useState(Date.now());
  const [comparison, setComparison] = useState<ValueComparison | null>(null);
  const [comparisonError, setComparisonError] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const lastTurn = useRef<HTMLDivElement>(null);
  const inFlight = useRef(false);
  // The current structured card is the only confirmable token, including on reload.
  const seenToken = useRef('');
  const ready = session?.status === 'ready';
  const blocked = !ready || busy || !!unresolved;
  const executionInProgress = decision?.presentationState === 'executing' || ['confirmed', 'dispatched', 'acknowledged'].includes(decision?.execution.phase ?? '');
  useEffect(() => {
    setSelectedSlot(decision?.plan?.points[0]?.targetSlot ?? null);
    setSelectedMode(decision?.actual ? 'actual' : decision?.plan?.points.length ? 'preview' : 'observation');
  }, [decision?.episodeId, decision?.previewId]);
  useEffect(() => {
    if (!decision) return;
    const evidence = decision.actual?.evidenceRef ?? '';
    const previous = lastEvidence.current;
    lastEvidence.current = evidence;
    if (previous === undefined || !evidence || previous === evidence) return;
    setSelectedMode('actual'); setReplay(true);
    const timer = window.setTimeout(() => setReplay(false), 1800);
    return () => { clearTimeout(timer); setReplay(false); };
  }, [decision?.actual?.evidenceRef, !!decision]);

  async function connect() {
    setConnecting(true); setConnectionError('');
    try {
      const next = await getSession(); setSession(next); setPending(next.pending);
      try { setStatus(await getStatus()); } catch { setStatus(null); }
    } catch (error) { setSession(null); setStatus(null); setConnectionError(error instanceof Error ? error.message : '助手暂时没有连上。'); }
    finally { setConnecting(false); }
  }
  async function refreshDecision() {
    const read = ++decisionRead.current;
    try { const next = await getDecision(); if (read === decisionRead.current) { setDecision(next); setDecisionError(''); } }
    catch { if (read === decisionRead.current) setDecisionError('数据台暂时没有读到新的结果。'); }
  }
  function changeDrawer(open: boolean) {
    setDrawer(open);
    if (!open) { ++environmentRead.current; setEnvironment(null); setEnvironmentLoading(false); setEnvironmentError(''); ++notificationRead.current; setNotifications(null); setNotificationLoading(false); setNotificationError(''); }
  }
  async function refreshNotifications() {
    if (!ready || notificationSaving.current) return;
    const read = ++notificationRead.current; setNotificationLoading(true); setNotificationError('');
    try { const next = await getNotifications(); if (read === notificationRead.current) setNotifications(next); }
    catch { if (read === notificationRead.current) { setNotifications(null); setNotificationError('提醒设置暂时没有读到，请刷新核对。'); } }
    finally { if (read === notificationRead.current) setNotificationLoading(false); }
  }
  async function toggleNotifications() {
    if (!ready || !notifications || notificationLoading || notificationSaving.current) return;
    notificationSaving.current = true;
    const read = ++notificationRead.current; setNotificationLoading(true); setNotificationError('');
    try {
      const next = await setNotificationsEnabled(!notifications.settings.enabled);
      if (read === notificationRead.current) { setNotifications(next); if (next.error?.code === 'needs_timezone') setNotificationError('尚未开启提醒。请先在已绑定飞书设置家庭时区，再刷新核对。'); }
    } catch {
      try { const latest = await getNotifications(); if (read === notificationRead.current) { setNotifications(latest); setNotificationError('保存没有收到确认，当前按重新读取的设置展示。'); } }
      catch { if (read === notificationRead.current) { setNotifications(null); setNotificationError('保存结果还未确认，请刷新设置核对。'); } }
    } finally { notificationSaving.current = false; if (read === notificationRead.current) setNotificationLoading(false); }
  }
  async function readEnvironmentData() {
    if (!ready || environmentLoading) return;
    const read = ++environmentRead.current;
    setEnvironment(null); setEnvironmentError(''); setEnvironmentLoading(true);
    try { const next = await getEnvironment(); if (read === environmentRead.current) setEnvironment(next); }
    catch (error) { if (read === environmentRead.current) setEnvironmentError(error instanceof Error ? error.message : '环境机数据暂不可用，未保留旧读数。'); }
    finally { if (read === environmentRead.current) setEnvironmentLoading(false); }
  }
  useEffect(() => { void connect(); }, []);
  useEffect(() => {
    if (!ready) return;
    let active = true, running = false;
    async function poll() {
      if (!active || running || document.hidden) return;
      running = true;
      await Promise.allSettled([refreshDecision(), (async () => {
        try {
          const feed = await getFeed(feedCursor.current);
          if (!active) return;
          feedCursor.current = feed.cursor;
          setFeedError('');
          setTurns(current => {
            const keep = feed.reset ? current.filter(t => t.id === recoverId()) : current.filter(t => !t.createdAt || t.createdAt >= Date.now() / 1000 - feed.retentionSeconds || t.id === recoverId());
            for (const item of feed.items) {
              const id = item.sourceChannel === 'agent' ? `notification:${item.notificationId}` : item.requestId ?? item.id;
              for (const role of ['user', 'assistant'] as const) {
                const text = role === 'user' ? item.userText : item.agentText;
                if (!text) continue;
                const index = keep.findIndex(t => t.id === id && t.role === role);
                const turn = { id, role, text, source: item.sourceChannel, createdAt: item.createdAt, deliveryState: item.deliveryState };
                if (index < 0) keep.push(turn); else keep[index] = { ...keep[index], ...turn };
              }
            }
            return [...keep];
          });
          if (feed.reset || feed.items.some(item => item.sourceChannel === 'feishu')) sessionRefreshNeeded.current = true;
          if (sessionRefreshNeeded.current && !inFlight.current) {
            const next = await getSession();
            if (active) { setSession(next); setPending(next.pending); sessionRefreshNeeded.current = false; }
          }
        } catch { if (active) setFeedError('最近10分钟的跨入口记录暂时未读到。'); }
      })()]);
      running = false;
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 1500);
    const visible = () => { if (!document.hidden) void poll(); };
    document.addEventListener('visibilitychange', visible);
    return () => { active = false; ++decisionRead.current; clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [ready]);
  useEffect(() => {
    const controller = new AbortController();
    void fetch('/api/value-comparison?dataset=dataset_mock_1011_10160249', { signal: controller.signal }).then(async r => { if (!r.ok) throw new Error(); return readValueComparison(await r.json()); })
      .then(setComparison).catch(() => { if (!controller.signal.aborted) setComparisonError('历史实验暂时没有读到。'); });
    return () => controller.abort();
  }, []);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  useEffect(() => { seenToken.current = pending?.token ?? ''; }, [pending]);
  useEffect(() => { const thread = lastTurn.current?.parentElement; if (turns.length && thread) thread.scrollTo({ top: thread.scrollHeight, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' }); }, [turns.length]);
  useEffect(() => {
    if (!drawer || !ready) return;
    let active = true; setMemories(null); setMemoryError('');
    void getMemories().then(items => { if (active) setMemories(items); }).catch(() => { if (active) setMemoryError('个人资料暂时没有读到，关闭后可重试。'); });
    return () => { active = false; };
  }, [drawer, ready, session?.memoryVersion]);
  useEffect(() => { if (drawer && ready) void refreshNotifications(); }, [drawer, ready]);
  useEffect(() => {
    const viewport = window.visualViewport;
    const update = () => setKeyboardOpen(!!viewport && window.innerWidth <= 760 && document.activeElement === input.current && window.innerHeight - viewport.height > 120);
    viewport?.addEventListener('resize', update); document.addEventListener('focusin', update); document.addEventListener('focusout', update);
    return () => { viewport?.removeEventListener('resize', update); document.removeEventListener('focusin', update); document.removeEventListener('focusout', update); };
  }, []);

  function applyResult(result: MessageResult) {
    if (result.status !== 'completed') {
      setUnresolved(result.requestId); rememberId(result.requestId);
      setSubmitError(result.status === 'processing' ? '助手还在处理这件事，稍后查询结果。' : result.status === 'expired' ? '请求已过查询期，请先在配对通道核对执行记录，再继续安排。' : '这次提交的结果还不确定，先查询原请求。');
      return;
    }
    setUnresolved(''); rememberId(''); setSubmitError(''); setPending(result.pending);
    setSession(current => current ? { ...current, memoryVersion: result.memoryVersion, pending: result.pending } : current);
    setTurns(current => current.some(turn => turn.id === result.requestId && turn.role === 'assistant') ? current.map(turn => turn.id === result.requestId && turn.role === 'assistant' ? { ...turn, text: result.reply.text, result } : turn) : [...current, { id: result.requestId, role: 'assistant', text: result.reply.text, source: 'web', createdAt: Date.now() / 1000, result }]);
    void getStatus().then(setStatus).catch(() => setStatus(null));
    void refreshDecision();
  }
  async function submit(payload: MessageBody, label: string) {
    if (blocked || inFlight.current) return;
    inFlight.current = true; setBusy(true); setSubmitError('');
    const requestId = crypto.randomUUID(); rememberId(requestId);
    setTurns(current => [...current, { id: requestId, role: 'user', text: label, source: 'web', createdAt: Date.now() / 1000 }]); setDraft(''); setFeedbackDraft(null);
    try { applyResult(await sendMessage({ requestId, ...payload } as MessageInput)); }
    catch { setUnresolved(requestId); setSubmitError('还不能确定这次提交的结果。先查询结果，避免重复安排。'); }
    finally { inFlight.current = false; setBusy(false); }
  }
  async function reconcile() {
    if (!unresolved || inFlight.current || !ready) return;
    inFlight.current = true; setBusy(true);
    try { applyResult(await findMessage(unresolved)); } catch { setSubmitError('暂时还查不到结果，请稍后再查询。'); }
    finally { inFlight.current = false; setBusy(false); }
  }
  function sendDraft() {
    const text = draft.trim();
    if (!text || blocked) return;
    if (feedbackDraft) void submit({ action: { type: 'feedback', ...feedbackDraft, reasonText: text } }, `这次安排${feedbackDraft.rating === 'suitable' ? '合适' : '想调整'}：${text}`);
    else void submit({ text }, text);
  }
  function send(event: FormEvent) { event.preventDefault(); sendDraft(); }
  function startFeedback(episodeId: string, rating: FeedbackDraft['rating']) {
    setFeedbackDraft({ episodeId, rating }); setDraft(''); input.current?.focus();
  }
  function confirm(choiceId?: string) {
    if (executionInProgress || !pending || seenToken.current !== pending.token || pendingExpired(pending) || !choiceId && (!pending.canConfirm || pending.confirmAction !== 'confirm_pending')) return;
    const action = choiceId ? { type: 'choose_pending' as const, token: pending.token, choiceId } : { type: 'confirm_pending' as const, token: pending.token };
    const label = choiceId ? pending.choices?.find(c => c.id === choiceId)?.label ?? '选择这个方案' : confirmLabels[pending.kind] || '确认这件事';
    void submit({ action }, label);
  }

  const gatewayUnavailable = !connecting && !session;
  const home = ready ? status?.home ?? null : null;
  const hasSnapshot = !!home;
  const coreUnavailable = status?.runtime.status === 'unavailable';
  const site = comparison?.sites.find(s => s.id === 'site_1');
  const improvement = evidenceNumber(site?.summary.improvementEur);
  const frame = useMemo(() => ready ? energyWorldFrame(decision, home, selectedMode, selectedSlot, replay && !reducedMotion) : null, [ready, decision, home, selectedMode, selectedSlot, replay, reducedMotion]);
  const summary = homeSummary(decision, pending, unresolved, !!ready, now);
  const previewAvailable = decision?.availability === 'ready' && !!decision.plan?.points.length && !['unknown', 'expired', 'infeasible'].includes(decision.presentationState);
  const askArrangement = () => { setFeedbackDraft(null); setDraft('解释这次安排'); input.current?.focus(); };
  const selectSlot = (slot: number) => { setSelectedSlot(slot); setSelectedMode('preview'); };
  function summaryAction() {
    if (summary.action === 'reconcile') void reconcile();
    else if (summary.action === 'pending') (document.querySelector('.consumer-pending .consumer-primary') ?? document.querySelector('.consumer-pending'))?.scrollIntoView({ block: 'center' });
    else if (summary.action === 'explain') askArrangement();
    else { const evidence = document.querySelector<HTMLDetailsElement>('.rd-evidence'); if (evidence) { evidence.open = true; evidence.scrollIntoView({ block: 'center' }); } }
  }

  return <div className={`consumer-app ew-app ${keyboardOpen ? 'ew-keyboard-open' : ''}`}>
    <a className="consumer-skip" href="#consumer-main">跳到家庭助手</a>
    <header className="consumer-header">
      <a className="consumer-brand" href="?view=family" onClick={e => { e.preventDefault(); onView('family'); }}><span className="consumer-logo"><LightningIcon size={22} weight="fill"/></span><span>电击小子<span className="consumer-brand-caption">家庭能源助手</span></span></a>
      <div className="consumer-header-actions"><button className="consumer-professional-link" onClick={() => onView('professional')}>详细分析<span>独立历史记录</span></button><span className="consumer-environment"><i/>本机演练</span><Dialog.Root open={drawer} onOpenChange={changeDrawer}>
        <Dialog.Trigger asChild><button className="consumer-icon" aria-label="家庭与个人资料"><GearSixIcon size={23}/></button></Dialog.Trigger>
        <Dialog.Portal><Dialog.Overlay className="consumer-overlay"/><Dialog.Content className="consumer-drawer" aria-describedby="consumer-settings-description">
          <div className="consumer-drawer-heading"><div><span className="consumer-eyebrow">YOUR HOME</span><Dialog.Title>家庭与个人资料</Dialog.Title></div><Dialog.Close asChild><button className="consumer-icon" aria-label="关闭家庭设置"><XIcon size={23}/></button></Dialog.Close></div>
          <Dialog.Description id="consumer-settings-description">你的资料跟随已连接的家庭保存。想添加或修改，直接在对话里告诉助手。</Dialog.Description>
          <section className="consumer-setting-section"><h3><HouseLineIcon size={18}/>我的家庭</h3><strong>{ready ? session.ownerLabel : '尚未连接家庭'}</strong><p>{hasSnapshot ? home?.paused ? '设备回读：本机演练已暂停' : '已读到本机设备快照' : coreUnavailable ? '设备演练暂时不可用' : '设备状态尚未确认'}</p><small>本机模拟环境，当前页面不代表真实家庭设备。设备快照不等于预测或执行已经就绪。</small><button className="consumer-link" disabled={connecting} onClick={() => void connect()}>刷新家庭连接<ArrowRightIcon size={17}/></button></section>
          <section className="consumer-setting-section"><h3><LeafIcon size={18}/>记住的日常</h3>{!ready ? <p>连接家庭后可查看个人资料。</p> : memoryError ? <p role="alert">{memoryError}</p> : memories === null ? <p role="status">正在读取个人资料…</p> : memories.length === 0 ? <p>还没有保存的资料。告诉助手一个习惯，确认后会记在这里。</p> : <ul className="consumer-memory-list">{memories.map(m => <li key={`${m.id}-${m.revision}`}><span>{memoryKinds[m.kind] || '已确认资料'}</span><strong>{memoryValue(m.value)}</strong><small>{m.machineConstraint ? '可用于计划的约束' : '作为生活背景参考'} · {m.status === 'active' ? '已保存' : m.status === 'expired' ? '已到期' : '已确认'}</small></li>)}</ul>}</section>
          <NotificationSection ready={!!ready} data={notifications} loading={notificationLoading} error={notificationError} onToggle={() => void toggleNotifications()} onRefresh={() => void refreshNotifications()}/>
          <EnvironmentDataSection ready={ready} snapshot={environment} loading={environmentLoading} error={environmentError} onRead={() => void readEnvironmentData()}/>
          <section className="consumer-setting-section"><h3>更多工具</h3><button className="consumer-link" onClick={() => onView('history')}>历史实验与曲线<ArrowRightIcon size={17}/></button><button className="consumer-link" onClick={() => onView('professional')}>专业历史工作台<ArrowRightIcon size={17}/></button><button className="consumer-link" onClick={() => onView('sample')}>固定 8 小时样例工具<ArrowRightIcon size={17}/></button></section>
        </Dialog.Content></Dialog.Portal>
      </Dialog.Root></div>
    </header>
    <main id="consumer-main" className="consumer-main">
      <section className={`consumer-heading rd-home-heading ew-result-heading is-${summary.kind}`} data-summary-kind={summary.kind} aria-live="polite"><div><div className="consumer-eyebrow"><HouseLineIcon size={15}/>{ready ? session.ownerLabel : '你的家庭能源'}</div><h1>{summary.title}</h1><p>{summary.detail}</p></div>{summary.action && <button className="consumer-link" disabled={busy || connecting} onClick={summaryAction}>{summary.action === 'reconcile' ? '查询原请求' : summary.action === 'pending' ? '查看待确认内容' : summary.action === 'explain' ? '解释本次安排' : '查看原执行记录'}<ArrowRightIcon size={16}/></button>}</section>
      <div className="ew-mode-bar"><span className="rd-home-status">{frame?.timeLabel ?? (connecting ? '正在连接…' : '等待设备读数')}</span><div className="ew-time-modes" aria-label="选择数据时间类型"><button aria-pressed={selectedMode === 'observation'} onClick={() => setSelectedMode('observation')}>最近已读</button><button disabled={!previewAvailable} aria-pressed={selectedMode === 'preview'} onClick={() => setSelectedMode('preview')}>计划预演</button><button disabled={!decision?.actual} aria-pressed={selectedMode === 'actual'} onClick={() => setSelectedMode('actual')}>本次回读</button></div></div>
      <WorldHUD frame={frame}/>
      <div className="consumer-workspace">
      <section className="ew-world-column" aria-label="家庭能源场景与计划">
        <div className={`ew-world-wrap ${sceneExpanded ? 'is-expanded' : ''}`} data-frame-kind={frame?.kind ?? 'empty'} data-slot={frame?.interval.slot ?? ''}>
          <button type="button" className="ew-expand-scene" aria-expanded={sceneExpanded} onClick={() => setSceneExpanded(value => !value)}>{sceneExpanded ? '收起家园' : '展开家园'}</button>
          <Suspense fallback={<div className="ew-scene-loading" role="status">正在展开家庭场景…<span>上方读数与右侧对话仍可使用</span></div>}><EnergyWorld frame={frame} selected={selectedDevice} onSelect={setSelectedDevice} reducedMotion={!!reducedMotion}/></Suspense>
        </div>
        <p className="ew-frame-source">{frame?.kind === 'preview' ? '拖动时间只看预计变化，执行前仍需确认。' : frame?.kind === 'actual' ? '这是刚才15分钟的实际回读，未来仍是预计。' : '最近已读快照，演练暂停时能量流保持静止。'}</p>
        <DeviceDetail frame={frame} selected={selectedDevice} onAsk={askArrangement}/>
        {(decisionError || decision?.availability === 'stale' || decision?.availability === 'unavailable' || ['unknown', 'expired', 'infeasible'].includes(decision?.presentationState ?? '')) && <p className="ew-data-notice" role="status">{decisionError || (decision?.availability === 'stale' ? '来源已变化，旧计划不可预演或执行。' : decision?.presentationState === 'unknown' ? '执行结果未知，请先按原请求查询。' : decision?.presentationState === 'infeasible' ? '这项要求暂时无法满足，未执行。' : decision?.presentationState === 'expired' ? '计划已过期，请与助手重新安排。' : '数据暂时不可用，已有读数保留原时点。')}</p>}
        <DecisionStage decision={ready ? decision : null} frame={frame} selectedSlot={selectedSlot} onSlot={selectSlot} compact error={decisionError} loading={connecting} busy={busy} onRefresh={() => void refreshDecision()} onProfessional={() => onView('professional')} onAsk={askArrangement}/>
      </section>
      <div className="ew-agent-rail">
      <section className="consumer-conversation" aria-label="与家庭能源助手对话">
        <div className="consumer-conversation-top"><span><span className="consumer-assistant-mark"><SparkleIcon size={18} weight="fill"/></span>你的能源助手</span><span className={`consumer-connection ${ready ? 'is-ready' : ''}`}><i/>{connecting ? '连接中' : ready ? '已连接' : '未连接'}</span></div>
        <p className="consumer-feed-note">网页与飞书 · 最近10分钟{feedError && <span role="status">{feedError}</span>}</p>
        <div className="consumer-thread" aria-live="polite" aria-relevant="additions text">
          {!connecting && session && !ready && <div className="consumer-feedback" role="alert"><WarningCircleIcon size={19}/><div><p>请先在已配对的飞书通道确认家庭连接，再回到这里。浏览器不能代选家庭身份。</p><button className="consumer-link" onClick={() => void connect()}>重新检查绑定<ArrowRightIcon size={15}/></button></div></div>}
          {turns.length === 0 && !pending && <div className="consumer-welcome"><p>想多留一些备用电，还是有新的用电安排？<br/>告诉我，先一起把这件事想清楚。</p><div className="consumer-suggestions">{suggestions.map(s => <button key={s.label} disabled={blocked} onClick={() => void submit({ text: s.text }, s.text)}><s.icon size={18}/>{s.label}<ArrowRightIcon size={14}/></button>)}</div></div>}
          {turns.map(turn => { const phase = turn.result?.episode?.phase || turn.result?.episode?.stage || turn.result?.episode?.status || ''; const episodeId = turn.result?.episode?.episodeId || ''; return <div className={`consumer-turn is-${turn.role}`} key={`${turn.role}-${turn.id}`}><span className="consumer-turn-label">{turn.source === 'feishu' && <span className="consumer-source-tag">来自飞书</span>}{turn.source === 'agent' && <span className="consumer-source-tag" data-delivery-state={turn.deliveryState}>主动提醒 · {notificationState[turn.deliveryState ?? 'unknown']}</span>}{turn.role === 'user' ? '你' : <><SparkleIcon size={14}/>能源助手</>}</span><p>{turn.text}</p>{turn.result?.episode && <div className={`consumer-result ${['unknown', 'failed', 'rejected'].includes(phase) ? 'is-caution' : ''}`}><ResultIcon phase={phase}/><span>{phaseText[phase] || '执行记录已更新'}<small>本机演练</small></span><details><summary>详情</summary><p>事件 {turn.result.episode.episodeId || turn.result.episode.id || '已记录'}</p><p>阶段 {phase || '待核对'}</p></details></div>}{['settled', 'outcome'].includes(phase) && /^[A-Za-z0-9_-]{1,100}$/.test(episodeId) && <div className="consumer-result-feedback"><span>这次安排适合你吗？</span><button disabled={blocked} onClick={() => startFeedback(episodeId, 'suitable')}>合适</button><button disabled={blocked} onClick={() => startFeedback(episodeId, 'unsuitable')}>想调整</button></div>}</div>; })}
          {pending && <div className="consumer-pending" key={pending.token}><div className="consumer-pending-title"><span><ClockIcon size={17}/>{pending.kind === 'clarification' ? '还需要你补充一点' : pending.kind.includes('plan') ? '这份安排，请你看看' : '先确认我有没有理解对'}</span><span>等你确认</span></div><p>{pending.summary}</p><details><summary>查看依据与编号</summary><p>{pending.proposalId ? `方案 ${pending.proposalId}` : '这次确认会提交到已连接的家庭。'}</p>{pending.episodeId && <p>事件 {pending.episodeId}</p>}{pending.execution && <p>模拟槽 {pending.execution.sourceSlot} · {pending.execution.intervalMinutes} 分钟 × {pending.execution.intervalCount} 槽 · 目标 {pending.execution.targetPowerW} W</p>}<p>只确认当前展示的这一项。</p></details>{pendingExpired(pending, now) ? <div className="consumer-expired">这件事的确认时间已过，请重新连接后再看看。<button className="consumer-link" onClick={() => void connect()}>刷新安排<ArrowRightIcon size={15}/></button></div> : <div className="consumer-pending-actions">{pending.choices?.length ? pending.choices.map(choice => <div className="consumer-choice" key={choice.id}>{choice.description && <p>{choice.description}</p>}<button className="consumer-primary" disabled={blocked || executionInProgress} onClick={() => confirm(choice.id)}>{choice.label}<ArrowRightIcon size={16}/></button></div>) : pending.canConfirm && pending.confirmAction === 'confirm_pending' ? <button className="consumer-primary" disabled={blocked || executionInProgress} onClick={() => confirm()}>{confirmLabels[pending.kind] || '确认这件事'}<CheckIcon size={17}/></button> : <p>这项建议还需要继续讨论，暂时不能确认。</p>}<span>{executionInProgress ? '本段已提交，正在等待真实回读；不能重复确认' : '想调整，直接在下面告诉我'}</span></div>}</div>}
          {busy && <p className="consumer-working" role="status"><span/>正在处理这件事…</p>}
          {(submitError || connectionError) && <div className="consumer-feedback" role="alert"><WarningCircleIcon size={19}/><div><p>{submitError || connectionError}</p>{unresolved ? <button disabled={busy || !ready} className="consumer-link" onClick={() => void reconcile()}>查询这次结果<ArrowRightIcon size={15}/></button> : <button disabled={connecting} className="consumer-link" onClick={() => void connect()}>重新连接<ArrowRightIcon size={15}/></button>}</div></div>}
          <div ref={lastTurn}/>
        </div>
        <form className="consumer-composer" onSubmit={send}>{feedbackDraft && <div className="consumer-feedback-draft"><span>给这次安排反馈：{feedbackDraft.rating === 'suitable' ? '合适' : '想调整'}</span><button type="button" onClick={() => setFeedbackDraft(null)} aria-label="取消填写反馈"><XIcon size={18}/></button></div>}<label className="consumer-input-label" htmlFor="consumer-input">向能源助手提问</label><div className="consumer-input-wrap"><textarea ref={input} id="consumer-input" rows={2} maxLength={2000} placeholder={feedbackDraft ? '说说这次安排哪里合适，或哪里想调整…' : '说说你的安排，或者问问家里的用电…'} value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); sendDraft(); } }}/><button aria-label="发送给能源助手" className="consumer-send" disabled={blocked || !draft.trim()} type="submit"><ArrowUpIcon size={23} weight="bold"/></button></div><div className="consumer-composer-note"><span>{unresolved ? '先核对上次结果，再继续安排' : feedbackDraft ? '反馈会先复述给你，确认后保存' : '一句话就好，执行前会请你确认'}</span><span>Enter 发送</span></div></form>
      </section>
      </div>
      </div>
      <section className="consumer-history" aria-label="历史实验"><span className="consumer-history-symbol"><ClockIcon size={19}/></span><div><span>历史实验</span><p>{improvement === null ? comparisonError || '正在读取历史对照…' : `家庭 1 在这次离线对照中${improvement >= 0 ? '综合改善' : '综合变差'} ${money(improvement)}`}</p></div><button className="consumer-link" onClick={() => setHistoryOpen(v => !v)} aria-expanded={historyOpen}>{historyOpen ? '收起' : '看看依据'}<ArrowRightIcon size={16}/></button></section>
      {historyOpen && <section className="consumer-history-detail"><h2>一段历史，只按证据说话</h2><p>2026年10月13日 09:15 → 10月17日 08:00 · 94小时45分。离线模型对照，金额包含期末剩余电量的价值修正。</p>{site && <dl><div><dt>家庭 1 现金支出差额</dt><dd>{evidenceNumber(site.summary.cashDeltaEur) === null ? '不可比' : `${evidenceNumber(site.summary.cashDeltaEur)! >= 0 ? '少花' : '多花'} ${money(evidenceNumber(site.summary.cashDeltaEur)!)}`}</dd></div><div><dt>家庭 1 余电估值变化</dt><dd>{evidenceNumber(site.summary.terminalCorrectionEur) === null ? '不可比' : `${evidenceNumber(site.summary.terminalCorrectionEur)! >= 0 ? '+' : '−'}${money(evidenceNumber(site.summary.terminalCorrectionEur)!)}`}</dd></div></dl>}<ul>{comparison?.sites.map(s => { const n = evidenceNumber(s.summary.improvementEur); return <li key={s.id}>家庭 {s.id.slice(-1)}<strong className={n !== null && n < 0 ? 'is-negative' : ''}>{n === null ? '暂时不可比' : `${n >= 0 ? '综合改善' : '综合变差'} ${money(n)}`}</strong></li>; })}</ul><p className="consumer-history-boundary">这是历史实验，不是今天的账单或官方成绩，也不代表这次设备演练的收益。</p><button className="consumer-link" onClick={() => onView('history')}>曲线与完整实验依据<ArrowRightIcon size={17}/></button></section>}
      <footer className="consumer-footer"><span>让用电，简单一点。</span><span><LightningIcon size={12}/>电击小子</span></footer>
    </main>
  </div>;
}
