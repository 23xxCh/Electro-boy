export type Point = {
  time: string;
  pvKw: number | null;
  loadKw: number | null;
  batteryKw?: number | null;
  gridKw?: number | null;
  soc?: number | null;
  reason?: string;
  capacityKwh?: number | null;
};
export type Totals = { pvKwh?: number | null; loadKwh?: number | null; importKwh?: number | null; exportKwh?: number | null };
export type Dataset = { id: string; label: string; timeRange?: { start: string; end: string }; source?: string; pointCount?: number; isLive?: boolean; [key: string]: unknown };
export type Site = { id: string; name: string; series: Point[]; totals?: Totals };
export type Dashboard = { dataset: Dataset; sites: Site[]; totals?: Totals; savings: number | null; warnings?: string[] };

export const number = (value: number | null | undefined, digits = 2) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
export const timeLabel = (time?: string) => time ? time.slice(11, 16) : '—';
export const dateLabel = (time?: string) => time ? time.slice(0, 10).replaceAll('-', '.') : '—';

/** Integrate only adjacent observations; do not bridge missing intervals. */
export function integrate(points: Point[], key: 'pvKw' | 'loadKw' | 'gridKw', direction: 'all' | 'import' | 'export' = 'all', day = '') {
  let result = 0;
  let intervals = 0;
  for (let i = 0; i < points.length - 1; i++) {
    if (day && !points[i].time.startsWith(day)) continue;
    const hours = (new Date(points[i + 1].time).getTime() - new Date(points[i].time).getTime()) / 3_600_000;
    const power = points[i][key];
    if (power == null || hours <= 0 || hours > 0.26) continue;
    const p = direction === 'import' ? Math.max(0, power) : direction === 'export' ? Math.max(0, -power) : power;
    result += p * hours;
    intervals++;
  }
  return intervals ? result : null;
}

/** Each household has its own meter: never net different households' bills. */
export function siteEnergy(sites: Site[], day: string) {
  const total = (key: 'pvKw' | 'loadKw' | 'gridKw', direction: 'all' | 'import' | 'export' = 'all') => {
    const values = sites.map(site => integrate(site.series, key, direction, day));
    return values.length && values.every(v => v != null) ? values.reduce<number>((a, b) => a + b!, 0) : null;
  };
  return { pv: total('pvKw'), load: total('loadKw'), imported: total('gridKw', 'import'), exported: total('gridKw', 'export') };
}
