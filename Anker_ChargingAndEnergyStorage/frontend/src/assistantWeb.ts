import type { EnergyWorldFrame } from './energy-world/types';

export type Pending = {
  kind: string; token: string; expiresAt: string | number; summary: string; canConfirm: boolean; confirmAction: 'confirm_pending' | null;
  episodeId?: string; proposalId?: string; choices?: { id: string; label: string; description?: string }[];
  execution?: { targetPowerW: number; sourceSlot: number; intervalMinutes: number; intervalCount: number; signConvention: string };
};
export type Session = { schemaVersion: 'assistant-web-v1'; status: 'ready' | 'unbound' | 'ambiguous'; ownerLabel: string; memoryVersion: number; pending: Pending | null };
export type Episode = { id?: string; episodeId?: string; phase?: string; stage?: string; status?: string; proposalId?: string; sourceSlot?: number; policyId?: string };
export type MessageResult = { requestId: string; status: 'completed' | 'processing' | 'unknown' | 'expired'; reply: { text: string; kind: string }; pending: Pending | null; memoryVersion: number; episode?: Episode | null };
export type Memory = { id: string; kind: string; key: string; value: unknown; startsAt?: string | number | null; expiresAt?: string | number | null; revision: number; status: string; machineConstraint?: unknown };
export type HomeSnapshot = { environment: 'local_debug1_sim'; deviceTime: string; timeBasis: 'device_local_naive'; observedAt: number; paused: boolean | null; socPercent: number | null; batteryPowerKw: number | null; pvKw: number | null; loadKw: number | null; physicalInputsKind: 'previous_ended_interval_hold'; measurementSourceSlot: number; socBoundarySlot?: number; controlSourceSlot: number };
export type AssistantStatus = { home: HomeSnapshot | null; runtime: { status: string; forecastStatus: string; executionStatus: string } };
export type MessageInput = { requestId: string; text: string } | { requestId: string; action: { type: 'confirm_pending'; token: string } | { type: 'choose_pending'; token: string; choiceId: string } | { type: 'feedback'; episodeId: string; rating: 'suitable' | 'unsuitable'; reasonText: string } };

export type PlanPoint = { targetSlot: number; start: string; end: string; chargeKw: number; dischargeKw: number; importKw: number; exportKw: number; curtailKw: number; energyEndKwh: number; socEndPercent: number };
export type DecisionPlan = { status: 'ok' | 'infeasible'; reserveEnergyKwh: number; minimumPercent: number; hypothetical: boolean; executable: false; expectedHorizonCostEur: number | null; firstAction: Pending['execution'] | null; points: PlanPoint[] };
export type ForecastPoint = { targetSlot: number; start: string; end: string; pvKw: number; loadKw: number; buyEurKwh: number; sellEurKwh: number; inputKind: 'held_anchor' | 'model_forecast' };
export type Observation = { kind: 'decision_origin' | 'execution_readback' | 'current_snapshot'; lastKnown: true; sourceSlot: number | null; measurementSourceSlot: number | null; socBoundarySlot: number | null; physicalInputsKind: string | null; observedAt: number | null; paused: boolean | null; socPercent: number | null; pvKw: number | null; loadKw: number | null; batteryActualKw: number | null; batteryTargetKw: number | null; gridImportKw: number | null; gridExportKw: number | null; prices: { buyEurKwh: number | null; sellEurKwh: number | null; sourceSlot: number | null; kind: string | null }; fieldSources: { grid: string | null; power: string | null } };
export type DecisionProjection = {
  schemaVersion: 'assistant-decision-v1'; viewVersion: string; availability: 'ready' | 'empty' | 'stale' | 'unavailable';
  presentationState: 'preview' | 'proposed' | 'executing' | 'settled' | 'unknown' | 'infeasible' | 'unchanged' | 'expired' | 'empty'; reasonCode: string | null;
  episodeId: string | null; proposalId: string | null; previewId: string | null; expiresAt: number | null;
  origin: null | { environment: 'local_debug1_sim'; policyId: string; sourceSlot: number; deviceTime: string; timeBasis: 'device_local_naive'; dtHours: number; initialEnergyKwh?: number | null; socRoundingMarginPercent?: number | null; measurementSourceSlot: number | null; socBoundarySlot: number; physicalInputsKind: string; sourceSnapshotDigest: string; forecastDigest: string; physics: { capacityKwh: number }; observedAt: number; sourceValidation: 'last_known' };
  observation: Observation | null; constraints: null | { minimumPercent: number; reserveEnergyKwh: number; consumedMemoryIds: string[]; missingFields: unknown[] }; currentConstraints: unknown;
  pendingRef: null | { kind: string; previewId?: string; episodeId?: string; proposalId?: string };
  forecast: null | { points: ForecastPoint[]; provenance: Record<string, unknown> }; plan: DecisionPlan | null;
  preview: null | { status: 'changed' | 'unchanged' | 'infeasible'; parentEpisodeId: string | null; candidateDigest: string; sourceSnapshotDigest: string; forecastDigest: string; sameOrigin: true; baseline: DecisionPlan; candidate: DecisionPlan };
  execution: { phase: string; events: { sequence: number; phase: string; eventAt: number; evidenceKind: string | null }[]; requestId: string | null; executedSlot: number | null; observedBoundarySlot: number | null; readbackMatches: boolean | null; settlementAvailable: boolean; failureCode: string | null };
  actual: null | { executedSlot: number; observedBoundarySlot: number; start: string; end: string; pvKw: number; loadKw: number; batteryActualKw: number; gridImportKw: number; gridExportKw: number; socStartPercent: number; socEndPercent: number; intervalCostEur: number | null; reserveMet: boolean | null; readbackMatches: boolean; settlementAvailable: boolean; evidenceRef: string };
  savings: 'not_evaluated';
};
export type FeedItem = { id: string; requestId: string | null; sourceChannel: 'web' | 'feishu'; userText: string; agentText: string; createdAt: number; episodeId: string | null; previewId: string | null };
export type AssistantFeed = { schemaVersion: 'assistant-feed-v1'; retentionSeconds: 600; reset: boolean; cursor: string | null; hasMore: boolean; items: FeedItem[] };
export type EnvironmentSnapshot = {
  schemaVersion: 'environment-pipeline-v1'; environment: 'release_official_ha_unverified';
  status: 'disabled' | 'ready' | 'partial' | 'unavailable'; reasonCode: string | null; householdId: 'site_1';
  validation: { connectedTested: false; identityVerified: false; timeBasisVerified: false };
  deviceTime: string | null; timeBasis: 'device_local_naive'; observedAt: number | null;
  units: { power: 'kW'; soc: '%'; controlPower: 'W' };
  observation: { socPercent: number | null; pvKw: number | null; loadKw: number | null; chargingKw: number | null; dischargingKw: number | null; batteryActualKw: number | null; gridImportKw: number | null; gridExportKw: number | null };
  control: { enabled: false; reasonCode: 'release_execution_contract_unverified'; operatingMode: string | null; gridFlow: 'charge' | 'discharge' | null; powerControlW: number | null; targetSemantics: 'battery_total_charge_or_grid_export' };
  fieldSources: { device: 'official_ha'; prices: null }; prices: null;
};
export function decisionHomeFallback(view: DecisionProjection | null, home: HomeSnapshot | null) {
  return view?.observation || view?.origin || view?.episodeId || view?.previewId ? null : home;
}
export function energyWorldFrame(view: DecisionProjection | null, home: HomeSnapshot | null, mode: EnergyWorldFrame['kind'], slot: number | null, replay = false): EnergyWorldFrame | null {
  const fallback = decisionHomeFallback(view, home);
  const observation = view?.observation;
  const emptyPrices = { buyEurKwh: null, sellEurKwh: null, sourceSlot: null, kind: null };
  const label = (start: string, end: string) => `${start.replace('T', ' ').slice(5, 16)}–${end.slice(11, 16)}`;
  if (mode === 'actual' && view?.actual) {
    const actual = view.actual;
    const price = view.forecast?.points.find(point => point.targetSlot === actual.executedSlot);
    const target = view.plan?.firstAction;
    return { viewVersion: view.viewVersion, kind: 'actual', timeLabel: `已回读 · ${label(actual.start, actual.end)}`,
      interval: { slot: actual.executedSlot, start: actual.start, end: actual.end, measurementSlot: actual.executedSlot, socBoundarySlot: actual.observedBoundarySlot }, paused: null,
      pvKw: actual.pvKw, loadKw: actual.loadKw, batteryKw: actual.batteryActualKw,
      batteryTargetKw: target?.sourceSlot === actual.executedSlot ? target.targetPowerW / 1000 : null,
      gridImportKw: actual.gridImportKw, gridExportKw: actual.gridExportKw, socPercent: actual.socEndPercent,
      prices: price ? { buyEurKwh: price.buyEurKwh, sellEurKwh: price.sellEurKwh, sourceSlot: actual.executedSlot, kind: 'execution_forecast' } : emptyPrices,
      motion: replay ? 'receipt_replay' : 'none' };
  }
  const point = view?.plan?.points.find(point => point.targetSlot === slot);
  const forecast = view?.forecast?.points.find(point => point.targetSlot === slot);
  if (mode === 'preview' && view?.availability === 'ready' && point && forecast && !['unknown', 'expired', 'infeasible'].includes(view.presentationState)) {
    const power = point.dischargeKw - point.chargeKw;
    return { viewVersion: view.viewVersion, kind: 'preview', timeLabel: `预计／未执行 · ${label(point.start, point.end)}`,
      interval: { slot: point.targetSlot, start: point.start, end: point.end, measurementSlot: point.targetSlot, socBoundarySlot: point.targetSlot + 1 }, paused: observation?.paused ?? null,
      pvKw: forecast.pvKw, loadKw: forecast.loadKw, batteryKw: power, batteryTargetKw: power,
      gridImportKw: point.importKw, gridExportKw: point.exportKw, socPercent: point.socEndPercent,
      prices: { buyEurKwh: forecast.buyEurKwh, sellEurKwh: forecast.sellEurKwh, sourceSlot: point.targetSlot, kind: 'plan_forecast' }, motion: 'preview' };
  }
  if (!view && !fallback) return null;
  const sourceSlot = observation ? observation.sourceSlot : fallback?.controlSourceSlot ?? null;
  const paused = observation ? observation.paused : fallback?.paused ?? null;
  const initialTime = !view?.origin && !view?.episodeId && !view?.previewId && home?.controlSourceSlot === sourceSlot ? home?.deviceTime : undefined;
  const deviceTime = view?.origin?.sourceSlot === sourceSlot ? view.origin.deviceTime : fallback?.deviceTime ?? initialTime;
  return { viewVersion: view?.viewVersion ?? null, kind: 'observation', timeLabel: `${deviceTime ? deviceTime.replace('T', ' ').slice(5, 16) : '模拟时点未提供'} · 最近已读${paused ? ' · 暂停' : ''}`,
    interval: { slot: sourceSlot, start: null, end: null, measurementSlot: observation ? observation.measurementSourceSlot : fallback?.measurementSourceSlot ?? null, socBoundarySlot: observation ? observation.socBoundarySlot : fallback?.socBoundarySlot ?? null }, paused,
    pvKw: observation ? observation.pvKw : fallback?.pvKw ?? null, loadKw: observation ? observation.loadKw : fallback?.loadKw ?? null,
    batteryKw: observation ? observation.batteryActualKw : fallback?.batteryPowerKw ?? null, batteryTargetKw: observation?.batteryTargetKw ?? null,
    gridImportKw: observation?.gridImportKw ?? null, gridExportKw: observation?.gridExportKw ?? null, socPercent: observation ? observation.socPercent : fallback?.socPercent ?? null,
    prices: observation ? observation.prices : emptyPrices, motion: 'none' };
}
export function decisionPrices(view: DecisionProjection | null, previewSlot?: number) {
  const slot = view?.actual?.executedSlot ?? previewSlot;
  return slot === undefined ? view?.observation?.prices : view?.forecast?.points.find(point => point.targetSlot === slot);
}

const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const fail = () => { throw new Error('助手返回的信息不完整，请重新连接。'); };
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const nullableNumber = (v: unknown) => v === null || finite(v);
const nullableString = (v: unknown) => v === null || typeof v === 'string';
const percent = (v: unknown) => finite(v) && v >= 0 && v <= 100;
const digest = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
export function readEnvironment(v: unknown): EnvironmentSnapshot {
  const failEnvironment = (): never => { throw new Error('环境机数据格式不符合只读约定，未显示旧读数。'); };
  // UTC is used only to reject calendar rollover; the device time remains unchanged.
  const naiveTime = (value: unknown) => value === null || typeof value === 'string' && /^(?!0000)\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(value) &&
    Number.isFinite(Date.parse(`${value}Z`)) && new Date(`${value}Z`).toISOString().slice(0, 19) === value;
  if (!object(v) || v.schemaVersion !== 'environment-pipeline-v1' || v.environment !== 'release_official_ha_unverified' ||
    !['disabled', 'ready', 'partial', 'unavailable'].includes(String(v.status)) || !nullableString(v.reasonCode) || v.householdId !== 'site_1' ||
    !object(v.validation) || ['connectedTested', 'identityVerified', 'timeBasisVerified'].some(k => (v.validation as Record<string, unknown>)[k] !== false) ||
    !naiveTime(v.deviceTime) || v.timeBasis !== 'device_local_naive' || !nullableNumber(v.observedAt) ||
    !object(v.units) || v.units.power !== 'kW' || v.units.soc !== '%' || v.units.controlPower !== 'W' ||
    !object(v.observation) || !['socPercent', 'pvKw', 'loadKw', 'chargingKw', 'dischargingKw', 'batteryActualKw', 'gridImportKw', 'gridExportKw'].every(k => {
      const n = (v.observation as Record<string, unknown>)[k]; return n === null || finite(n) && (k === 'batteryActualKw' || n >= 0);
    }) ||
    v.observation.socPercent !== null && !percent(v.observation.socPercent) ||
    !object(v.control) || v.control.enabled !== false || v.control.reasonCode !== 'release_execution_contract_unverified' ||
    !nullableString(v.control.operatingMode) || ![null, 'charge', 'discharge'].includes(v.control.gridFlow as null | string) ||
    !nullableNumber(v.control.powerControlW) || v.control.powerControlW !== null && ((v.control.powerControlW as number) < 0 || (v.control.powerControlW as number) > 5000) || v.control.targetSemantics !== 'battery_total_charge_or_grid_export' ||
    !object(v.fieldSources) || v.fieldSources.device !== 'official_ha' || v.fieldSources.prices !== null || v.prices !== null) return failEnvironment();
  if (v.status === 'ready' && (Object.values(v.observation).some(n => n === null) || !v.deviceTime || v.observedAt === null ||
    !v.control.operatingMode || v.control.gridFlow === null || v.control.powerControlW === null)) return failEnvironment();
  return v as EnvironmentSnapshot;
}
function readPlan(v: unknown): DecisionPlan {
  if (!object(v) || !['ok', 'infeasible'].includes(String(v.status)) || !finite(v.reserveEnergyKwh) || v.reserveEnergyKwh < 0 || !percent(v.minimumPercent) ||
    typeof v.hypothetical !== 'boolean' || v.executable !== false || !nullableNumber(v.expectedHorizonCostEur) || !Array.isArray(v.points) ||
    !v.points.every(p => object(p) && Number.isInteger(p.targetSlot) && typeof p.start === 'string' && typeof p.end === 'string' &&
      ['chargeKw', 'dischargeKw', 'importKw', 'exportKw', 'curtailKw', 'energyEndKwh'].every(k => finite(p[k]) && p[k] >= 0) && percent(p.socEndPercent))) return fail();
  if (v.firstAction !== null && (!object(v.firstAction) || !finite(v.firstAction.targetPowerW) || !Number.isInteger(v.firstAction.sourceSlot) ||
    v.firstAction.intervalMinutes !== 15 || v.firstAction.intervalCount !== 1 || v.firstAction.signConvention !== 'negative_charge_positive_discharge')) return fail();
  if (v.status === 'infeasible' && (v.points.length || v.firstAction !== null || v.expectedHorizonCostEur !== null)) return fail();
  return v as DecisionPlan;
}
export function readDecision(v: unknown): DecisionProjection {
  if (!object(v) || v.schemaVersion !== 'assistant-decision-v1' || !digest(v.viewVersion) || !['ready', 'empty', 'stale', 'unavailable'].includes(String(v.availability)) ||
    !['preview', 'proposed', 'executing', 'settled', 'unknown', 'infeasible', 'unchanged', 'expired', 'empty'].includes(String(v.presentationState)) ||
    !['reasonCode', 'episodeId', 'proposalId', 'previewId'].every(k => nullableString(v[k])) || !nullableNumber(v.expiresAt) || v.savings !== 'not_evaluated') return fail();
  const o = v.origin;
  if (o !== null && (!object(o) || o.environment !== 'local_debug1_sim' || o.timeBasis !== 'device_local_naive' || !Number.isInteger(o.sourceSlot) || typeof o.deviceTime !== 'string' ||
    !finite(o.dtHours) || o.dtHours <= 0 || !object(o.physics) || !finite(o.physics.capacityKwh) || o.physics.capacityKwh <= 0 ||
    !digest(o.sourceSnapshotDigest) || !digest(o.forecastDigest) || o.sourceValidation !== 'last_known' ||
    o.initialEnergyKwh !== undefined && !nullableNumber(o.initialEnergyKwh) || o.socRoundingMarginPercent !== undefined && !nullableNumber(o.socRoundingMarginPercent))) return fail();
  const observation = v.observation;
  if (observation !== null && (!object(observation) || !['decision_origin', 'execution_readback', 'current_snapshot'].includes(String(observation.kind)) || observation.lastKnown !== true ||
    !['sourceSlot', 'measurementSourceSlot', 'socBoundarySlot', 'observedAt', 'socPercent', 'pvKw', 'loadKw', 'batteryActualKw', 'batteryTargetKw', 'gridImportKw', 'gridExportKw'].every(k => nullableNumber(observation[k])) ||
    !(observation.paused === null || typeof observation.paused === 'boolean') || observation.socPercent !== null && !percent(observation.socPercent) ||
    !object(observation.prices) || !nullableNumber(observation.prices.buyEurKwh) || !nullableNumber(observation.prices.sellEurKwh) || !nullableNumber(observation.prices.sourceSlot))) return fail();
  if (v.plan !== null) readPlan(v.plan);
  if (v.forecast !== null && (!object(v.forecast) || !Array.isArray(v.forecast.points) || !v.forecast.points.every(p => object(p) && Number.isInteger(p.targetSlot) &&
    typeof p.start === 'string' && typeof p.end === 'string' && ['pvKw', 'loadKw', 'buyEurKwh', 'sellEurKwh'].every(k => finite(p[k])) && ['held_anchor', 'model_forecast'].includes(String(p.inputKind))))) return fail();
  const preview = v.preview;
  if (preview !== null) {
    if (!object(preview) || !object(o) || preview.sameOrigin !== true || !['changed', 'unchanged', 'infeasible'].includes(String(preview.status)) ||
      preview.sourceSnapshotDigest !== o.sourceSnapshotDigest || preview.forecastDigest !== o.forecastDigest) return fail();
    const before = readPlan(preview.baseline), after = readPlan(preview.candidate);
    if (!after.hypothetical || after.points.some((p, i) => before.points[i]?.targetSlot !== p.targetSlot || before.points[i]?.start !== p.start || before.points[i]?.end !== p.end)) return fail();
  }
  const execution = v.execution;
  if (!object(execution) || typeof execution.phase !== 'string' || typeof execution.settlementAvailable !== 'boolean' || !Array.isArray(execution.events) ||
    !execution.events.every(e => object(e) && Number.isInteger(e.sequence) && typeof e.phase === 'string' && finite(e.eventAt)) ||
    !(execution.readbackMatches === null || typeof execution.readbackMatches === 'boolean')) return fail();
  const a = v.actual;
  if (a !== null && (!object(a) || !Number.isInteger(a.executedSlot) || a.observedBoundarySlot !== (a.executedSlot as number) + 1 || !object(o) || a.executedSlot !== o.sourceSlot ||
    typeof a.start !== 'string' || typeof a.end !== 'string' || !['pvKw', 'loadKw', 'batteryActualKw', 'gridImportKw', 'gridExportKw'].every(k => finite(a[k])) ||
    !percent(a.socStartPercent) || !percent(a.socEndPercent) || a.readbackMatches !== true || typeof a.settlementAvailable !== 'boolean' ||
    !nullableNumber(a.intervalCostEur) || a.intervalCostEur !== null && !a.settlementAvailable || typeof a.evidenceRef !== 'string')) return fail();
  if (v.constraints !== null && (!object(v.constraints) || !percent(v.constraints.minimumPercent) || !finite(v.constraints.reserveEnergyKwh) ||
    !Array.isArray(v.constraints.consumedMemoryIds) || !Array.isArray(v.constraints.missingFields))) return fail();
  if (v.pendingRef !== null && (!object(v.pendingRef) || typeof v.pendingRef.kind !== 'string')) return fail();
  return v as DecisionProjection;
}
export function readFeed(v: unknown): AssistantFeed {
  if (!object(v) || v.schemaVersion !== 'assistant-feed-v1' || v.retentionSeconds !== 600 || typeof v.reset !== 'boolean' || !nullableString(v.cursor) ||
    typeof v.hasMore !== 'boolean' || !Array.isArray(v.items) || !v.items.every(i => object(i) && typeof i.id === 'string' && !!i.id && nullableString(i.requestId) &&
      ['web', 'feishu'].includes(String(i.sourceChannel)) && typeof i.userText === 'string' && typeof i.agentText === 'string' && finite(i.createdAt) && nullableString(i.episodeId) && nullableString(i.previewId))) return fail();
  return v as AssistantFeed;
}
export function readPending(v: unknown): Pending | null {
  if (v === null) return null;
  if (!object(v) || typeof v.kind !== 'string' || typeof v.token !== 'string' || !v.token || typeof v.summary !== 'string' || !v.summary.trim() ||
    !(typeof v.expiresAt === 'string' || typeof v.expiresAt === 'number' && Number.isFinite(v.expiresAt)) || typeof v.canConfirm !== 'boolean' ||
    !(v.confirmAction === 'confirm_pending' || v.confirmAction === null)) return fail();
  if (v.choices !== undefined && (!Array.isArray(v.choices) || !v.choices.every(c => object(c) && typeof c.id === 'string' && typeof c.label === 'string' && (c.description === undefined || typeof c.description === 'string')))) return fail();
  if (v.execution !== undefined && (!object(v.execution) || !['targetPowerW', 'sourceSlot', 'intervalMinutes', 'intervalCount'].every(k => typeof v.execution === 'object' && v.execution !== null && typeof (v.execution as Record<string, unknown>)[k] === 'number' && Number.isFinite((v.execution as Record<string, unknown>)[k])) || typeof v.execution.signConvention !== 'string')) return fail();
  if (['episodeId', 'proposalId'].some(k => v[k] !== undefined && typeof v[k] !== 'string')) return fail();
  return v as Pending;
}
export function readSession(v: unknown): Session {
  if (object(v) && v.schemaVersion === 'assistant-web-v1' && (v.status === 'unbound' || v.status === 'ambiguous')) return { schemaVersion: 'assistant-web-v1', status: v.status, ownerLabel: '', memoryVersion: 0, pending: null };
  if (!object(v) || v.schemaVersion !== 'assistant-web-v1' || !['ready', 'unbound', 'ambiguous'].includes(String(v.status)) ||
    typeof v.ownerLabel !== 'string' || !Number.isInteger(v.memoryVersion) || (v.memoryVersion as number) < 0) return fail();
  return { ...v, pending: readPending(v.pending) } as Session;
}
export function readMessage(v: unknown, requestId: string): MessageResult {
  if (object(v) && v.schemaVersion === 'assistant-web-v1' && v.requestId === requestId && v.status === 'processing' && v.reply === undefined && v.pending === undefined) return { requestId, status: 'processing', reply: { text: '', kind: 'processing' }, pending: null, memoryVersion: 0 };
  if (object(v) && v.schemaVersion === 'assistant-web-v1' && v.requestId === requestId && v.status === 'processing' && v.reply === null && v.pending === null && v.memoryVersion === null) return { requestId, status: 'processing', reply: { text: '', kind: 'processing' }, pending: null, memoryVersion: 0 };
  if (!object(v) || v.schemaVersion !== 'assistant-web-v1' || v.requestId !== requestId || !['completed', 'processing', 'unknown', 'expired'].includes(String(v.status)) ||
    !(v.status !== 'completed' && v.reply === null || object(v.reply) && typeof v.reply.text === 'string' && typeof v.reply.kind === 'string') || !Number.isInteger(v.memoryVersion) ||
    (v.episode !== undefined && v.episode !== null && (!object(v.episode) || ['id', 'episodeId', 'phase', 'stage', 'status', 'proposalId', 'policyId'].some(k => (v.episode as Record<string, unknown>)[k] !== undefined && typeof (v.episode as Record<string, unknown>)[k] !== 'string')))) return fail();
  return { ...v, reply: v.reply ?? { text: '', kind: v.status }, pending: readPending(v.pending) } as MessageResult;
}
export function readStatus(v: unknown): AssistantStatus {
  if (!object(v) || !object(v.runtime) || typeof v.runtime.status !== 'string' || typeof v.runtime.forecastStatus !== 'string' || typeof v.runtime.executionStatus !== 'string') return fail();
  const home = v.home;
  if (home !== null && (!object(home) || home.environment !== 'local_debug1_sim' || home.timeBasis !== 'device_local_naive' || typeof home.deviceTime !== 'string' || !home.deviceTime ||
    home.physicalInputsKind !== 'previous_ended_interval_hold' || !Number.isInteger(home.measurementSourceSlot) || !Number.isInteger(home.controlSourceSlot) ||
    typeof home.observedAt !== 'number' || !Number.isFinite(home.observedAt) || !(home.paused === null || typeof home.paused === 'boolean') ||
    !['socPercent', 'batteryPowerKw', 'pvKw', 'loadKw'].every(key => home[key] === null || typeof home[key] === 'number' && Number.isFinite(home[key])) ||
    typeof home.socPercent === 'number' && (home.socPercent < 0 || home.socPercent > 100))) return fail();
  return v as AssistantStatus;
}
export function pendingExpired(pending: Pending, now = Date.now()): boolean {
  const expiry = typeof pending.expiresAt === 'number' ? pending.expiresAt * (pending.expiresAt < 1e12 ? 1000 : 1) : Date.parse(pending.expiresAt);
  return !Number.isFinite(expiry) || now >= expiry;
}
async function request(path: string, init?: RequestInit): Promise<unknown> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), init?.method === 'POST' ? 30000 : 10000);
  try {
    const response = await fetch(`/api/assistant/${path}`, { ...init, credentials: 'same-origin', signal: controller.signal });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      if (path === 'session' && object(body) && (body.status === 'unbound' || body.status === 'ambiguous')) return body;
      throw new Error(response.status === 401 || response.status === 403 ? '家庭连接需要重新确认，请重新连接。' : '助手暂时没连上，请稍后重试。');
    }
    return body;
  } finally { clearTimeout(timer); }
}
export const getSession = async () => readSession(await request('session'));
export const getStatus = async () => readStatus(await request('status'));
export const getEnvironment = async () => readEnvironment(await request('environment'));
export const getDecision = async () => readDecision(await request('decision'));
export const getFeed = async (cursor: string | null) => readFeed(await request(`feed${cursor ? `?after=${encodeURIComponent(cursor)}` : ''}`));
export const sendMessage = async (input: MessageInput) => readMessage(await request('messages', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) }), input.requestId);
export const findMessage = async (id: string) => readMessage(await request(`messages?requestId=${encodeURIComponent(id)}`), id);
export const getMemories = async (): Promise<Memory[]> => {
  const v = await request('memories');
  if (!object(v) || !Array.isArray(v.items) || !v.items.every(m => object(m) && typeof m.id === 'string' && typeof m.kind === 'string' && typeof m.key === 'string' && typeof m.status === 'string' && Number.isInteger(m.revision))) return fail();
  return v.items as Memory[];
};
