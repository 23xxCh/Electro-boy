import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { ArrowUpIcon, ListIcon, PlusIcon } from '@phosphor-icons/react';
import ViewSwitcher from './ViewSwitcher';
import './agent.css';

type StepState = 'done' | 'doing' | 'todo';
type Step = { label: string; state: StepState };
type Msg = { id: number; role: 'user' | 'agent'; text: string; note?: string };
type Choice = { id: string; label: string; line1: string; line2: string };
type Task = {
  id: number; title: string; job: 'ask' | 'plan'; messages: Msg[]; steps: Step[]; artifact: string;
  householdId: string; household: string; proposalId: string | null;
  trialText: string | null;
  canConfirm: boolean; canPass: boolean; line1: string; line2: string; choices: Choice[];
};
type Props = {
  household: string; householdId: string; siteId: string; onSite: (id: string) => void;
  sites: { id: string; label: string }[]; timeText: string;
  context: Record<string, unknown>;
  reference: { pv: string; load: string; soc: string; grid: string };
  onFamily: () => void; onProfessional: () => void;
  seed: { id: number; text: string } | null; onSeedDone: () => void;
};

const askHints = ['今晚的电够不够？', '电池还能用多久？', '现在在买电，还是卖电？'];
const planHints = ['冰箱必须一直有电', '明天给车充十度电', '出差回来还要留电', '只在便宜的时候充电'];
const seenSeeds = new Set<number>();
function emptyPlan(): Pick<Task, 'canConfirm' | 'canPass' | 'line1' | 'line2' | 'choices' | 'proposalId'> {
  return { canConfirm: false, canPass: false, line1: '', line2: '', choices: [], proposalId: null };
}

function readChoices(body: { choices?: unknown }): Choice[] {
  if (!Array.isArray(body?.choices)) return [];
  return body.choices.flatMap(item => {
    if (!item || typeof item !== 'object') return [];
    const choice = item as { id?: unknown; label?: unknown; line1?: unknown; line2?: unknown };
    if (typeof choice.id !== 'string' || typeof choice.label !== 'string' || !choice.label.trim()) return [];
    return [{
      id: choice.id,
      label: choice.label,
      line1: typeof choice.line1 === 'string' ? choice.line1 : '',
      line2: typeof choice.line2 === 'string' ? choice.line2 : '',
    }];
  });
}

function planKind(text: string): 'fridge' | 'car' | 'trip' | 'cheap' | 'confirm' | null {
  const value = text.trim();
  if (/^(确认|就按这个|就这样|记下)/.test(value)) return 'confirm';
  if (/冰箱/.test(value)) return 'fridge';
  if (/车|十度|10度/.test(value)) return 'car';
  if (/出差|留电/.test(value)) return 'trip';
  if (/便宜|低谷/.test(value)) return 'cheap';
  return null;
}

function workSteps(phase: 'run' | 'trial' | 'confirmed' | 'short' | 'held' | 'passed' | 'error'): Step[] {
  if (phase === 'run') return [
    { label: '听懂这件事', state: 'done' },
    { label: '按八个时段试算', state: 'doing' },
    { label: '等你点头记下', state: 'todo' },
  ];
  if (phase === 'trial') return [
    { label: '听懂这件事', state: 'done' },
    { label: '按八个时段试算', state: 'done' },
    { label: '等你点头记下', state: 'doing' },
  ];
  if (phase === 'confirmed') return [
    { label: '听懂这件事', state: 'done' },
    { label: '按八个时段试算', state: 'done' },
    { label: '没有下发到家里', state: 'done' },
  ];
  if (phase === 'short') return [
    { label: '听懂这件事', state: 'done' },
    { label: '按八个时段试算', state: 'done' },
    { label: '等你决定', state: 'doing' },
  ];
  if (phase === 'held') return [
    { label: '听懂这件事', state: 'done' },
    { label: '没有新的安排', state: 'done' },
  ];
  if (phase === 'passed') return [
    { label: '听懂这件事', state: 'done' },
    { label: '按八个时段试算', state: 'done' },
    { label: '维持现在这样', state: 'done' },
  ];
  return [
    { label: '听懂这件事', state: 'done' },
    { label: '这次没算成', state: 'done' },
  ];
}

export default function AgentChat(props: Props) {
  const { household, householdId, sites, siteId, onSite, timeText, context, reference, onFamily, onProfessional, seed, onSeedDone } = props;
  const [tasks, setTasks] = useState<Task[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [nav, setNav] = useState(false);
  const [rail, setRail] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const nextId = useRef(1);
  const active = tasks.find(task => task.id === activeId) ?? null;

  useEffect(() => {
    if (!seed || seenSeeds.has(seed.id)) return;
    seenSeeds.add(seed.id);
    onSeedDone();
    void submit(seed.text);
  }, [seed]);

  function patch(taskId: number, change: (task: Task) => Task) {
    setTasks(list => list.map(task => task.id === taskId ? change(task) : task));
  }

  function receivePlan(taskId: number, body: unknown, ok: boolean, requestHousehold: string, confirmedId?: string) {
    const reply = body !== null && typeof body === 'object' && !Array.isArray(body) ? body as Record<string, unknown> : {};
    const status = ok && typeof reply.status === 'string' ? reply.status : 'error';
    const proposalId = typeof reply.proposalId === 'string' && reply.proposalId.trim() ? reply.proposalId : null;
    const bound = proposalId !== null && reply.household === requestHousehold;
    const usable = (status === 'trial' || status === 'short' || status === 'confirmed') && bound &&
      (status !== 'confirmed' || proposalId === confirmedId);
    const sentence = usable ? (typeof reply.line1 === 'string' && reply.line1 ? reply.line1 : typeof reply.sentence === 'string' ? reply.sentence : '这次没有算成，请重新试算。')
      : (typeof reply.sentence === 'string' && !ok ? reply.sentence : '没有收到与这张卡对应的方案编号，请重新试算。');
    patch(taskId, task => ({
      ...task, ...emptyPlan(),
      proposalId: usable ? proposalId : null,
      canConfirm: usable && status === 'trial', canPass: usable && (status === 'trial' || status === 'short'),
      choices: usable && status === 'short' ? readChoices(reply) : [],
      steps: workSteps(usable ? status as 'trial' | 'short' | 'confirmed' : 'error'),
      line1: sentence || '这次没有算成，请重新试算。',
      line2: usable && status !== 'short' && typeof reply.line2 === 'string' ? reply.line2 : '',
      artifact: sentence || '',
      messages: [...task.messages, { id: nextId.current++, role: 'agent', text: sentence || '请重新试算。', note: status === 'confirmed' && usable ? '方案已记下，尚未下发。' : '' }],
    }));
  }

  async function submit(text: string, owner?: Pick<Task, 'householdId' | 'household'>) {
    const value = text.trim();
    if (!value || busy) return;
    setDraft(''); setNav(false);
    const kind = planKind(value);
    if (kind === 'confirm' && active) {
      patch(active.id, task => ({ ...task, messages: [...task.messages, { id: nextId.current++, role: 'user', text: value }] }));
      await changePlan(active.id, 'confirm');
      return;
    }
    const job = kind ? 'plan' as const : 'ask' as const;
    const requestHousehold = owner?.householdId ?? householdId;
    const requestLabel = owner?.household ?? household;
    const id = nextId.current++;
    const created: Task = {
      id, title: value.slice(0, 22), job, householdId: requestHousehold, household: requestLabel,
      trialText: job === 'plan' && kind !== 'confirm' ? value : null,
      messages: [{ id: nextId.current++, role: 'user', text: value }], artifact: '', ...emptyPlan(),
      steps: job === 'plan' ? workSteps('run') : [{ label: '读固定样例', state: 'done' }, { label: '用生活里的话讲', state: 'doing' }],
    };
    setTasks(list => [created, ...list]); setActiveId(id); setBusy(true);
    try {
      if (kind === 'confirm') {
        receivePlan(id, { sentence: '这张卡没有方案编号，请先重新试算。' }, false, requestHousehold);
        return;
      }
      const response = job === 'plan'
        ? await fetch('/api/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'trial', kind, household: requestHousehold }) })
        : await fetch('/api/explain', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: value, context }) });
      const body = await response.json().catch(() => null);
      if (job === 'plan') receivePlan(id, body, response.ok, requestHousehold);
      else {
        const answer = response.ok && typeof body?.answer === 'string' ? body.answer : '这次没有讲成。可以直接说一件固定样例需求，比如冰箱要一直有电。';
        patch(id, task => ({ ...task, steps: [{ label: '读固定样例', state: 'done' }, { label: '用生活里的话讲', state: 'done' }], artifact: answer,
          messages: [...task.messages, { id: nextId.current++, role: 'agent', text: answer, note: `${requestLabel} · 固定8小时样例` }] }));
      }
    } catch {
      patch(id, task => ({ ...task, ...emptyPlan(), steps: workSteps('error'), messages: [...task.messages, { id: nextId.current++, role: 'agent', text: '这次没连上，请重新试算。' }] }));
    } finally { setBusy(false); }
  }

  async function changePlan(taskId: number, action: 'choose' | 'confirm' | 'pass', choice?: Choice) {
    if (busy) return;
    const seen = tasks.find(task => task.id === taskId);
    if (!seen?.proposalId || action === 'confirm' && !seen.canConfirm) {
      patch(taskId, task => ({ ...task, ...emptyPlan(), steps: workSteps('error'), messages: [...task.messages, { id: nextId.current++, role: 'agent', text: '这张卡没有可用方案编号，请重新试算。' }] }));
      return;
    }
    setBusy(true);
    try {
      const response = await fetch('/api/plan', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, proposalId: seen.proposalId, household: seen.householdId, ...(choice ? { choice: choice.id } : {}) }),
      });
      const body = await response.json().catch(() => null);
      if (response.ok && action === 'pass' && body?.status === 'passed') {
        patch(taskId, task => ({ ...task, ...emptyPlan(), steps: workSteps('passed'), artifact: '', messages: [...task.messages, { id: nextId.current++, role: 'agent', text: '这份固定样例方案没有记下，尚未下发。' }] }));
      } else receivePlan(taskId, body, response.ok, seen.householdId, action === 'confirm' ? seen.proposalId : undefined);
    } catch {
      patch(taskId, task => ({ ...task, steps: workSteps('error'), messages: [...task.messages, { id: nextId.current++, role: 'agent', text: action === 'confirm' ? '连接中断，确认结果还未知。再次确认会核对同一方案编号。' : '连接中断，方案状态还未知，请重新试算。' }] }));
    } finally { setBusy(false); }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit(draft); }
  }

  function onCompose(event: FormEvent) {
    event.preventDefault();
    void submit(draft);
  }

  return <div className={`agent-shell ${nav ? 'is-nav' : ''} ${rail ? 'is-rail' : ''}`}>
    {(nav || rail) && <button className="agent-backdrop" aria-label="关闭侧栏" onClick={() => { setNav(false); setRail(false); }}/>}
    <ViewSwitcher value="assistant" onChange={next => { if (next === 'family') onFamily(); if (next === 'professional') onProfessional(); }}/>
    <aside className="agent-side">
      <button className="agent-side-btn" type="button" onClick={() => { setActiveId(null); setDraft(''); box.current?.focus(); }}><PlusIcon size={15}/>新建任务</button>
      <div className="agent-side-label"><span>任务列表</span></div>
      <div className="agent-tasks">
        {tasks.length === 0 ? <p className="agent-empty-tasks">暂无任务</p> : tasks.map(task => <button key={task.id} type="button" className="agent-task" aria-current={task.id === activeId} onClick={() => { setActiveId(task.id); setNav(false); }}><span>{task.title}</span><small>{task.household}</small></button>)}
      </div>
      <div className="agent-side-foot">
        <span className="agent-avatar">电</span>
        <label>样例家庭
          <select aria-label="选择家庭" value={sites.some(site => site.id === siteId) ? siteId : sites[0]?.id} onChange={event => onSite(event.target.value)}>
            {sites.map(site => <option key={site.id} value={site.id}>{site.label}</option>)}
          </select>
        </label>
      </div>
    </aside>
    <section className="agent-main">
      <header className="agent-top">
        <button className="agent-menu" type="button" aria-label="打开任务栏" onClick={() => setNav(true)}><ListIcon size={18}/></button>
        <strong>{active?.title ?? '新任务'}</strong>
        <span>{active?.household ?? household} · {timeText}</span>
        <button className="agent-home-link" type="button" onClick={() => setRail(open => !open)}>待办</button>
      </header>
      <div className="agent-stage">
        {active ? <div className="agent-thread" aria-live="polite">
          {active.messages.filter(message => message.role === 'user').slice(-1).map(message => <p key={message.id} className="agent-user">{message.text}</p>)}
          <div>
            <div className="agent-run-label"><span>A</span>固定8小时样例</div><p className="agent-note">{active.household} · 只记下方案，尚未下发</p>
            {active.steps.map(step => <div key={step.label} className={`agent-step is-${step.state}`}><i/>{step.label}</div>)}
            {active.job === 'plan' && active.line1 ? active.messages.filter(message => message.role === 'agent').slice(-1).map(message => <div key={message.id}>
              <p className="agent-answer">{message.text}</p>
              {active.line2 ? <p className="agent-answer">{active.line2}</p> : null}
              {message.note && <p className="agent-note">{message.note}</p>}
              {(active.canConfirm || active.canPass || active.choices.length > 0) && <div className="agent-choices">
                {active.canConfirm && <button className="agent-confirm" type="button" disabled={busy} onClick={() => void changePlan(active.id, 'confirm')}>就按这个记下</button>}
                {active.choices.map(choice => <button key={choice.id} className="agent-choice" type="button" disabled={busy} onClick={() => void changePlan(active.id, 'choose', choice)}>{choice.label}</button>)}
                {active.canPass && <button className="agent-choice" type="button" disabled={busy} onClick={() => void changePlan(active.id, 'pass')}>先不记</button>}
              </div>}
            </div>) : active.messages.filter(message => message.role === 'agent').slice(-1).map(message => <div key={message.id}><p className="agent-answer">{message.text}</p>{message.note && <p className="agent-note">{message.note}</p>}</div>)}
            {active.job === 'plan' && !active.proposalId && active.trialText && active.steps.some(step => step.label === '这次没算成') && <button className="agent-choice" type="button" disabled={busy} onClick={() => void submit(active.trialText!, active)}>重新试算这份需求</button>}
          </div>
        </div> : <div className="agent-empty">
          <p className="agent-eyebrow">一次一件</p>
          <h1>试一件固定样例需求</h1>
          <p>独立的固定8小时输入，不使用上方回放家庭的真实参数。一次试一件事；确认只记下方案，尚未下发。</p>
          <div className="agent-hint-group"><p>问问</p><div className="agent-suggestions">{askHints.map(hint => <button key={hint} type="button" onClick={() => void submit(hint)}>{hint}</button>)}</div></div>
          <div className="agent-hint-group"><p>安排</p><div className="agent-suggestions">{planHints.map(hint => <button key={hint} type="button" onClick={() => void submit(hint)}>{hint}</button>)}</div></div>
        </div>}
      </div>
      <form className="agent-dock" onSubmit={onCompose}>
        <div className="agent-composer">
          <label className="sr-only" htmlFor="agent-draft">问记录，或说一件安排</label>
          <textarea id="agent-draft" ref={box} rows={2} maxLength={300} value={draft} placeholder="说一件固定样例需求，比如冰箱要一直有电" onChange={event => setDraft(event.target.value)} onKeyDown={onKeyDown}/>
          <div className="agent-composer-bar">
            <span className="agent-chip">{household} · 固定样例</span>
            <button className="agent-send" type="submit" aria-label="发送" disabled={!draft.trim() || busy}><ArrowUpIcon size={16} weight="bold"/></button>
          </div>
        </div>
      </form>
    </section>
    <aside className="agent-rail" aria-label="任务侧栏">
      <section className="agent-panel">
        <h2>固定样例 · 尚未下发</h2>
        {active ? <ul>{active.steps.map(step => <li key={step.label}><span className={`agent-dot is-${step.state}`}/>{step.label}</li>)}</ul> : <p className="agent-note">还没有进行中的任务。</p>}
      </section>
      <section className="agent-panel">
        <h2>任务产物</h2>
        {active?.artifact ? <div className="agent-artifact"><strong>{active.job === 'plan' ? '这份安排' : '这次说明'}</strong>{active.artifact}</div> : <div className="agent-artifact">暂无产物<br/>说完并算完，结果会出现在这里。</div>}
      </section>
      <section className="agent-panel">
        <h2>参考</h2>
        <div className="agent-ref"><span>家庭</span><b>{active?.household ?? household}</b></div>
        <div className="agent-ref"><span>范围</span><b>{timeText}</b></div>
        <div className="agent-ref"><span>发电</span><b>{reference.pv}</b></div>
        <div className="agent-ref"><span>用电</span><b>{reference.load}</b></div>
        <div className="agent-ref"><span>电量</span><b>{reference.soc}</b></div>
        <div className="agent-ref"><span>电网</span><b>{reference.grid}</b></div>
      </section>
    </aside>
  </div>;
}
