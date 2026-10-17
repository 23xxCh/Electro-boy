import { useEffect, useId, useState } from 'react';
import { number, timeLabel, type Point } from './types';

const signals = [
  { key: 'pvKw' as const, label: '光伏发电', color: '#a76a16', dash: '' },
  { key: 'loadKw' as const, label: '家庭用电', color: '#384e5d', dash: '' },
  { key: 'gridKw' as const, label: '电网交换', color: '#657c68', dash: '5 5' },
];
export default function EnergyChart({ points }: { points: Point[] }) {
  const id = useId().replaceAll(':', '');
  const [visible, setVisible] = useState(['pvKw', 'loadKw', 'gridKw']);
  const [cursor, setCursor] = useState(0);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => { setCursor(0); setHover(null); }, [points]);
  const values = points.flatMap(p => signals.filter(s => visible.includes(s.key)).map(s => p[s.key] ?? 0));
  const tickMax = Math.ceil(Math.max(1, ...values) / 2) * 2;
  const tickMin = Math.floor(Math.min(0, ...values) / 2) * 2;
  const y = (v: number) => 190 - (v - tickMin) / (tickMax - tickMin) * 154;
  const x = (i: number) => 46 + i / Math.max(1, points.length - 1) * 660;
  const path = (key: typeof signals[number]['key']) => {
    let previous = false;
    return points.map((p, i) => {
      if (p[key] == null) { previous = false; return ''; }
      const gap = i > 0 && (new Date(p.time).getTime() - new Date(points[i - 1].time).getTime()) > 16 * 60_000;
      const command = previous && !gap ? 'L' : 'M'; previous = true;
      return `${command}${x(i).toFixed(1)},${y(p[key]!).toFixed(1)}`;
    }).join(' ');
  };
  const position = Math.min(points.length - 1, hover ?? cursor);
  const active = points[position];
  return <div className="chart-container">
    <div className="chart-legend">{signals.map(s => <button key={s.key} aria-pressed={visible.includes(s.key)} onClick={() => setVisible(v => v.includes(s.key) ? v.length > 1 ? v.filter(k => k !== s.key) : v : [...v, s.key])}><i style={{ background: s.color, opacity: visible.includes(s.key) ? 1 : .25 }}/><span>{s.label}</span></button>)}<span className="chart-unit">kW</span></div>
    {points.length < 2 ? <div className="empty-chart">当前范围没有足够的连续记录，请切换日期。</div> : <>
      <div className="chart-plot" role="group" tabIndex={0} aria-label="交互式功率图，左右方向键选择记录时刻"
        onKeyDown={event => {
          let next = position;
          if (event.key === 'ArrowRight') next++;
          else if (event.key === 'ArrowLeft') next--;
          else if (event.key === 'Home') next = 0;
          else if (event.key === 'End') next = points.length - 1;
          else return;
          event.preventDefault(); setHover(null); setCursor(Math.max(0, Math.min(points.length - 1, next)));
        }}>
        <svg viewBox="0 0 730 230" role="img" aria-label="光伏发电、家庭用电与电网交换功率曲线，单位千瓦"
          onPointerMove={e => {
            const box = e.currentTarget.getBoundingClientRect();
            const percent = ((e.clientX - box.left) / box.width * 730 - 46) / 660;
            const index = Math.max(0, Math.min(points.length - 1, Math.round(percent * (points.length - 1))));
            if (e.pointerType === 'mouse') setHover(index); else setCursor(index);
          }}
          onPointerDown={e => {
            const box = e.currentTarget.getBoundingClientRect();
            const percent = ((e.clientX - box.left) / box.width * 730 - 46) / 660;
            setCursor(Math.max(0, Math.min(points.length - 1, Math.round(percent * (points.length - 1)))));
          }} onPointerLeave={() => setHover(null)}>
          <defs><linearGradient id={`chart-${id}`} x1="0" x2="0" y1="0" y2="1"><stop stopColor="#c39543" stopOpacity=".14"/><stop offset="1" stopColor="#c39543" stopOpacity=".01"/></linearGradient></defs>
          {[0, 1, 2, 3, 4].map(i => {const v = tickMin + (tickMax - tickMin) * i / 4; return <g key={i}><line x1="46" x2="706" y1={y(v)} y2={y(v)} stroke="#e5e9e4" strokeDasharray="3 5"/><text x="28" y={y(v) + 4} textAnchor="end" fill="#70796e" fontSize="11">{number(v, 1)}</text></g>;})}
          {visible.includes('pvKw') && points.every((p, i) => p.pvKw != null && (i === 0 || new Date(p.time).getTime() - new Date(points[i - 1].time).getTime() <= 16 * 60_000)) && <path d={`${path('pvKw')} L${x(points.length - 1)},${y(0)} L46,${y(0)} Z`} fill={`url(#chart-${id})`}/>}
          {signals.filter(s => visible.includes(s.key)).map(s => <path key={s.key} d={path(s.key)} fill="none" stroke={s.color} strokeWidth={s.key === 'pvKw' ? 2.6 : 1.8} strokeDasharray={s.dash} strokeLinecap="round" strokeLinejoin="round"/>)}
          {[0, .2, .4, .6, .8, 1].map(percent => { const idx = Math.round(percent * (points.length - 1)); return <text key={percent} x={x(idx)} y="218" textAnchor="middle" fill="#70796e" fontSize="11">{timeLabel(points[idx]?.time)}</text>; })}
          {active && <g><line x1={x(position)} x2={x(position)} y1="30" y2="193" stroke="#657c68" strokeDasharray="3 4"/>{signals.filter(s => visible.includes(s.key) && active[s.key] != null).map(s => <circle key={s.key} cx={x(position)} cy={y(active[s.key]!)} r="4" fill={s.color} stroke="white" strokeWidth="2"/>)}</g>}
        </svg>
      </div>
      <div className="chart-readout" aria-live="polite" aria-atomic="true"><time>{timeLabel(active?.time)}</time>{signals.map(s => <div key={s.key}><span><i style={{background:s.color}}/>{s.label}</span><b>{number(active?.[s.key])}<small> kW</small></b></div>)}</div>
      <label className="chart-scrubber"><span>滑动查看时刻</span><input type="range" aria-label="曲线读数时刻" min={0} max={points.length - 1} value={Math.max(0, position)} onChange={e => { setHover(null); setCursor(Number(e.target.value)); }}/><time>{timeLabel(points.at(-1)?.time)}</time></label>
    </>}
  </div>;
}
