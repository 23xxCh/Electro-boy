export type Evidence = { value: number | null; status: 'computed' | 'assumed' | 'unavailable' | 'invalid'; reason: string | null };
export type ComparisonSelection = { runId: string | null; siteId: string; day: string; slotIndex: number };
export type ComparisonSummary = {
  policyCashCostEur: Evidence; nativeCashCostEur: Evidence; cashDeltaEur: Evidence;
  policyFinalEnergyKwh: Evidence; nativeFinalEnergyKwh: Evidence; terminalEnergyDeltaKwh: Evidence;
  terminalCorrectionEur: Evidence; improvementEur: Evidence;
};
export type ComparisonFlow = {
  requestedBatteryKw: number | null; batteryKw: number | null; gridImportKw: number | null; gridExportKw: number | null;
  socStartPercent: number | null; socEndPercent: number | null; energyStartKwh: number | null; energyEndKwh: number | null;
  curtailedPvKw: number | null; unservedLoadKw: number | null; costEur: number | null; reason: string | null;
};
export type ComparisonIssue = { code: string; siteId: string | null; slotIndex: number | null; message: string };
export type ComparisonSlot = {
  pairId: string; slotIndex: number; start: string; end: string; decisionAsOf: string; historyEndExclusive: string;
  actualHorizonSlots: number; status: 'valid' | 'invalid';
  common: { pvKw: number | null; loadKw: number | null; buyPriceEurPerKwh: number | null; sellPriceEurPerKwh: number | null };
  policy: ComparisonFlow; native: ComparisonFlow; issues: ComparisonIssue[];
};
export type ComparisonSite = {
  id: string; status: 'valid' | 'invalid'; initialSocPercent: Evidence; slots: ComparisonSlot[];
  summary: ComparisonSummary & { valuationPriceEurPerKwh: Evidence }; issues: ComparisonIssue[];
};
export type ValueComparison = {
  schemaVersion: 'fv01-comparison-v1'; runId: string; status: 'valid' | 'invalid'; datasetId: string;
  scope: { start: string; endExclusive: string; expectedSlots: number; intervalMinutes: number; durationHours: number; currency: 'EUR'; timeBasis: string; sourceKind: string };
  sites: ComparisonSite[]; summary: ComparisonSummary; issues: ComparisonIssue[];
  assumptions: { id: string; statement: string; status: Evidence['status']; impact: string }[];
  provenance: {
    inputFiles: { path: string; sha256: string }[]; modelFiles: { path: string; sha256: string }[];
    codeFiles: { path: string; sha256: string }[]; configSha256: string;
    tariffHashes: { siteId: string; policySha256: string; nativeSha256: string }[];
    fixedModelTrainingWindow: { status: string; statement: string };
  };
};

const summaryKeys = ['policyCashCostEur', 'nativeCashCostEur', 'cashDeltaEur', 'policyFinalEnergyKwh', 'nativeFinalEnergyKwh', 'terminalEnergyDeltaKwh', 'terminalCorrectionEur', 'improvementEur'] as const;
const flowKeys = ['requestedBatteryKw', 'batteryKw', 'gridImportKw', 'gridExportKw', 'socStartPercent', 'socEndPercent', 'energyStartKwh', 'energyEndKwh', 'curtailedPvKw', 'unservedLoadKw', 'costEur'] as const;
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const state = (value: unknown) => value === 'valid' || value === 'invalid';
const evidenceStates = ['computed', 'assumed', 'unavailable', 'invalid'];
const sha256 = (value: unknown) => typeof value === 'string' && /^[a-fA-F0-9]{64}$/.test(value);
function issues(value: unknown) {
  return Array.isArray(value) && value.every(item => object(item) && typeof item.code === 'string' && typeof item.message === 'string' &&
    (item.siteId === null || typeof item.siteId === 'string') && (item.slotIndex === null || Number.isInteger(item.slotIndex)));
}
function evidence(value: unknown): value is Evidence {
  if (!object(value) || !evidenceStates.includes(String(value.status))) return false;
  return (value.reason === null || typeof value.reason === 'string') &&
    (value.status !== 'invalid' || typeof value.reason === 'string' && value.reason.trim().length > 0) &&
    (value.status === 'computed' || value.status === 'assumed' ? finite(value.value) : value.value === null);
}
function summary(value: unknown, valid: boolean) {
  return object(value) && summaryKeys.every(key => evidence(value[key]) && (value[key] as Evidence).status === (valid ? 'computed' : 'invalid'));
}

/** Reject incompatible evidence; never fall back to the old dashboard for money or curves. */
export function readValueComparison(value: unknown): ValueComparison {
  const fail = () => { throw new Error('对照文件不符合配对证据契约，暂时不能比较。'); };
  if (!object(value) || value.schemaVersion !== 'fv01-comparison-v1' || typeof value.runId !== 'string' || !value.runId ||
    value.datasetId !== 'dataset_mock_1011_10160249' || !state(value.status) || !object(value.scope)) return fail();
  const scope = value.scope;
  if (scope.start !== '2026-10-13 09:15:00' || scope.endExclusive !== '2026-10-17 08:00:00' || scope.expectedSlots !== 379 ||
    scope.intervalMinutes !== 15 || scope.durationHours !== 94.75 || scope.currency !== 'EUR' || scope.timeBasis !== 'device_local_naive' ||
    scope.sourceKind !== 'causal_offline_model_comparison' || !Array.isArray(value.sites) || value.sites.length !== 3 || !summary(value.summary, value.status === 'valid')) return fail();
  if (!issues(value.issues) || !Array.isArray(value.assumptions) || !value.assumptions.every(item => object(item) &&
    typeof item.id === 'string' && typeof item.statement === 'string' && typeof item.impact === 'string' && evidenceStates.includes(String(item.status))) || !object(value.provenance)) return fail();
  const provenance = value.provenance;
  if (!['inputFiles', 'modelFiles', 'codeFiles'].every(key => Array.isArray(provenance[key]) &&
    (provenance[key] as unknown[]).every(item => object(item) && typeof item.path === 'string' && sha256(item.sha256))) || !sha256(provenance.configSha256) ||
    !Array.isArray(provenance.tariffHashes) || !provenance.tariffHashes.every(item => object(item) && typeof item.siteId === 'string' && sha256(item.policySha256) && sha256(item.nativeSha256)) ||
    !object(provenance.fixedModelTrainingWindow) || !evidenceStates.includes(String(provenance.fixedModelTrainingWindow.status)) || typeof provenance.fixedModelTrainingWindow.statement !== 'string') return fail();
  for (const [index, site] of value.sites.entries()) {
    if (!object(site) || site.id !== `site_${index + 1}` || !state(site.status) || !evidence(site.initialSocPercent) || !Array.isArray(site.slots) ||
      !summary(site.summary, site.status === 'valid') || !evidence((site.summary as Record<string, unknown>).valuationPriceEurPerKwh) ||
      (site.summary as Record<string, Evidence>).valuationPriceEurPerKwh.status !== (site.status === 'valid' ? 'computed' : 'invalid') ||
      !issues(site.issues) || site.slots.length > 379 || (site.status === 'valid' && site.slots.length !== 379) || (value.status === 'valid' && site.status !== 'valid')) return fail();
    for (const [slotIndex, slot] of site.slots.entries()) {
      if (!object(slot) || slot.slotIndex !== slotIndex || slot.pairId !== `${value.runId}:${site.id}:${slotIndex}` || !state(slot.status) ||
        typeof slot.start !== 'string' || typeof slot.end !== 'string' || slot.decisionAsOf !== slot.start ||
        typeof slot.historyEndExclusive !== 'string' || slot.historyEndExclusive > slot.start || !object(slot.common) || !issues(slot.issues)) return fail();
      for (const key of ['policy', 'native']) {
        const flow = slot[key];
        if (!object(flow) || !flowKeys.every(field => finite(flow[field]) || slot.status === 'invalid' && flow[field] === null) || !(flow.reason === null || typeof flow.reason === 'string')) return fail();
      }
      if (slot.status === 'valid') {
        if (!Number.isInteger(slot.actualHorizonSlots) || !finite(slot.actualHorizonSlots) || slot.actualHorizonSlots < 1 || slot.actualHorizonSlots > 192 ||
          !['pvKw', 'loadKw', 'buyPriceEurPerKwh', 'sellPriceEurPerKwh'].every(key => finite((slot.common as Record<string, unknown>)[key]))) return fail();
      }
      if (site.status === 'valid' && (slot.status !== 'valid' || (slotIndex === 0 ? slot.start !== scope.start : (site.slots[slotIndex - 1] as ComparisonSlot).end !== slot.start) ||
        (slotIndex === 378 && slot.end !== scope.endExclusive))) return fail();
    }
  }
  if (value.status === 'invalid' && summaryKeys.some(key => (value.summary as Record<string, Evidence>)[key].status !== 'invalid')) return fail();
  return value as unknown as ValueComparison;
}

export const evidenceNumber = (item?: Evidence): number | null => item?.status === 'computed' && finite(item.value) ? item.value : null;
export const evidenceLabel = (item?: Evidence) => ({ computed: '离线计算', assumed: '实验约定', unavailable: '缺少证据', invalid: '不可比' }[item?.status ?? 'unavailable']);
export function changeDirection(item?: Evidence): 'positive' | 'negative' | 'zero' | 'unknown' {
  const value = evidenceNumber(item);
  return value === null ? 'unknown' : Math.abs(value) < 0.005 ? 'zero' : value > 0 ? 'positive' : 'negative';
}
export function changeText(item: Evidence | undefined, kind: 'cash' | 'total' | 'inventory') {
  const direction = changeDirection(item);
  if (direction === 'unknown') return '暂时不可比较';
  if (direction === 'zero') return '基本相同';
  if (kind === 'inventory') return direction === 'positive' ? '期末估值增加' : '期末估值减少';
  return kind === 'cash' ? direction === 'positive' ? '少花' : '多花' : direction === 'positive' ? '综合改善' : '综合变差';
}
export const householdName = (id: string) => `家庭 ${id.slice(-1).padStart(2, '0')}`;
export const actionText = (power: number | null | undefined) => !finite(power) ? '安排还未知' : power < -.01 ? '计划充电' : power > .01 ? '计划放电' : '计划待机';
