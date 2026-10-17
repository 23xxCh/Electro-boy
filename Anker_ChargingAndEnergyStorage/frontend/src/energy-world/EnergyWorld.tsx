import { useEffect, useRef, useState } from 'react';
import { ArrowsClockwiseIcon, BatteryHighIcon, MinusIcon, PlugIcon, PlusIcon, SunIcon } from '@phosphor-icons/react';
import type { WorldHandle } from './scene';
import type { EnergyWorldProps } from './types';
import './world.css';

function StaticIsland() {
  return <svg viewBox="0 0 640 430" className="ew-static-island" aria-hidden="true">
    <defs>
      <linearGradient id="ew-rock" x2="0" y2="1"><stop stopColor="#52607b"/><stop offset="1" stopColor="#17233d"/></linearGradient>
      <linearGradient id="ew-room" x2="0" y2="1"><stop stopColor="#eed8ac"/><stop offset="1" stopColor="#ae784e"/></linearGradient>
    </defs>
    <path d="M73 253 324 156 572 264 393 398 217 374Z" fill="url(#ew-rock)"/>
    <path d="M73 253 324 156 572 264 327 359Z" fill="#2e635d" stroke="#668d85"/>
    <path d="M170 206 328 139 485 209 328 276Z" fill="#c7b491"/>
    <path d="M170 206v91l158 72v-93Z" fill="#b3926a"/>
    <path d="M328 276v93l157-67v-93Z" fill="#e6d2ae"/>
    <path d="M190 169 328 112 467 172v129l-139 59-138-63Z" fill="url(#ew-room)"/>
    <path d="M206 202v80l122 54 123-52v-80l-123 52Z" fill="#8a664f"/>
    <path d="M211 204v73l117 51 118-50v-71l-118 49Z" fill="#e6c99b"/>
    <path d="M213 175 328 126 447 177v14l-119 50-115-50Z" fill="#d6b781"/>
    <path d="M187 159 328 93 477 160 328 226Z" fill="#122e4d" stroke="#90a9b6" strokeWidth="5"/>
    <g stroke="#7897ad" fill="none" strokeWidth="2"><path d="m207 158 121-55 127 57-127 56Z"/><path d="m250 139 127 57m-84-76 127 57m-170 2 122-55m-82 74 122-55"/></g>
    <g stroke="#805b41" strokeWidth="5"><path d="M202 180v109m126-52v110m122-151v94"/></g>
    <path d="m259 276 55-22 57 25-55 23Z" fill="#b86c54"/><path d="m252 269v-14l52-22 3 18Z" fill="#eddec0"/>
    <path d="m359 233 47-20 30 13-46 21Z" fill="#eddec0"/>
    <path d="M116 242 142 230 165 241v70l-26 12-23-11Z" fill="#dbe6ec" stroke="#7f9faf" strokeWidth="2"/><path d="M126 250v50" stroke="#182f45" strokeWidth="5"/>
    <path d="M507 269 529 260 550 269v50l-22 11-21-9Z" fill="#c6dce5"/><path d="M529 264v-54m-19 6h37" stroke="#91b4cd" strokeWidth="5"/>
    <path className="ew-static-bus" d="M143 319v14l182 51 196-55v-12" fill="none" stroke="#7b999b" strokeWidth="3"/>
    <g fill="#548864"><ellipse cx="114" cy="204" rx="34" ry="28"/><ellipse cx="511" cy="196" rx="31" ry="29"/></g>
    <g stroke="#8b6a50" strokeWidth="8"><path d="M113 223v30m397-39v47"/></g>
    <ellipse cx="204" cy="321" rx="14" ry="19" fill="#d9e5ee"/><ellipse cx="204" cy="302" rx="22" ry="18" fill="#e8f3f6"/><rect x="187" y="296" width="34" height="15" rx="7" fill="#15374b"/><path d="M195 301v5m18-5v5" stroke="#7be2ed" strokeWidth="3"/>
  </svg>;
}

export default function EnergyWorld(props: EnergyWorldProps) {
  const { frame, selected, reducedMotion, onSelect } = props;
  const container = useRef<HTMLDivElement>(null);
  const world = useRef<WorldHandle | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const [phase, setPhase] = useState<'loading' | 'ready' | 'fallback'>('loading');
  const [retry, setRetry] = useState(0);
  const [asset, setAsset] = useState<'loading' | 'lux3d' | 'unavailable'>('loading');
  const kw = (value: number | null | undefined) => value == null ? '未提供' : `${value.toFixed(2)} kW`;
  const battery = frame?.batteryKw == null ? '未提供' : frame.batteryKw === 0 ? '待机 0 W' : `${frame.batteryKw < 0 ? '充电' : '放电'} ${Math.round(Math.abs(frame.batteryKw) * 1000)} W`;

  useEffect(() => {
    let cancelled = false;
    setPhase('loading');
    const fail = () => {
      world.current?.dispose(); world.current = null;
      if (!cancelled) setPhase('fallback');
    };
    import('./scene.js').then(({ createWorld }) => {
      if (cancelled || !container.current) return;
      try {
        const handle = createWorld(container.current, device => latest.current.onSelect(device), fail, state => { if (!cancelled) setAsset(state); });
        world.current = handle;
        handle.update(latest.current.frame, latest.current.selected, latest.current.reducedMotion);
        setPhase('ready');
      } catch { fail(); }
    }).catch(fail);
    return () => { cancelled = true; world.current?.dispose(); world.current = null; };
  }, [retry]);

  useEffect(() => { world.current?.update(frame, selected, reducedMotion); }, [frame, selected, reducedMotion]);

  return <section className={`ew-world ew-${frame?.kind ?? 'empty'}`} aria-label="家庭能源立体场景" data-ew-phase={phase} data-ew-frame-kind={frame?.kind ?? 'empty'}>
    <div className="ew-world-glow" aria-hidden="true"/>
    <div className="ew-canvas" ref={container} aria-hidden="true"/>
    {phase !== 'ready' && <div className="ew-fallback"><StaticIsland/></div>}
    <div className="ew-world-caption"><span className="ew-world-title">你的能源小世界</span><span>房屋与灯光为场景示意</span></div>
    {phase === 'ready' ? <div className="ew-camera-controls" aria-label="场景视角">
      <button type="button" onClick={() => world.current?.zoom(1)} aria-label="放大场景"><PlusIcon size={19}/></button>
      <button type="button" onClick={() => world.current?.zoom(-1)} aria-label="缩小场景"><MinusIcon size={19}/></button>
      <button type="button" onClick={() => world.current?.reset()} aria-label="还原场景视角"><ArrowsClockwiseIcon size={19}/></button>
    </div> : phase === 'fallback' && <button className="ew-retry" type="button" onClick={() => setRetry(value => value + 1)}>重试 3D</button>}
    <div className="ew-device-controls" aria-label="选择设备">
      <button type="button" aria-pressed={selected === 'pv'} onClick={() => onSelect('pv')}><SunIcon size={21}/><span>光伏<small>{kw(frame?.pvKw)}</small></span></button>
      <button type="button" aria-pressed={selected === 'battery'} onClick={() => onSelect('battery')}><BatteryHighIcon size={21}/><span>电池<small>{battery}</small></span></button>
      <button type="button" aria-pressed={selected === 'grid'} onClick={() => onSelect('grid')}><PlugIcon size={21}/><span>电网<small>购 {kw(frame?.gridImportKw)}<br/>出 {kw(frame?.gridExportKw)}</small></span></button>
    </div>
    <div className="ew-scene-note"><span>{!frame ? '暂无能源读数 · 场景示意' : frame.kind === 'preview' ? '虚线总线 · 预计，尚未执行' : frame.kind === 'actual' ? frame.motion === 'receipt_replay' && !reducedMotion ? '本次15分钟 · 实际回放' : '本次15分钟 · 已回读' : '保持最近读数 · 总线静止'}</span><span role="status">{phase === 'loading' ? '准备3D · 暂用二维示意' : phase === 'fallback' ? '二维保底 · 数据与选择可用' : asset === 'lux3d' ? 'Lux3D 房屋＋原生设备' : asset === 'unavailable' ? '模型未加载 · 原生3D保底' : '原生3D · 房屋模型加载中'}</span></div>
  </section>;
}
