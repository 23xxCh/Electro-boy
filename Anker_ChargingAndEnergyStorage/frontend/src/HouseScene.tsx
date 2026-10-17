import { BatteryHighIcon, HouseLineIcon, PlugIcon, SunIcon } from '@phosphor-icons/react';
import { number, type Point } from './types';
import EnergyAtmosphere from './EnergyAtmosphere';

export default function HouseScene({ point }: { point: Point | null }) {
  const battery = point?.batteryKw;
  const grid = point?.gridKw;
  const active = (v: number | null | undefined) => v != null && Math.abs(v) > .03;
  return <div className="energy-scene">
    <EnergyAtmosphere/>
    <div className="scene-ground" />
    <svg className="house-illustration" viewBox="0 0 640 340" role="img" aria-label="家庭光伏、住宅和储能系统示意图">
      <defs>
        <marker id="flow-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M1 1 7 4 1 7" fill="none" stroke="context-stroke" strokeWidth="1.5"/></marker>
        <linearGradient id="wall" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#ffffff"/><stop offset="1" stopColor="#e0e5df"/></linearGradient>
        <linearGradient id="roof" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#657b6b"/><stop offset="1" stopColor="#354f40"/></linearGradient>
        <linearGradient id="lawn" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#d9e4cf"/><stop offset="1" stopColor="#e9edde"/></linearGradient>
        <pattern id="solar-grid" width="15" height="19" patternUnits="userSpaceOnUse"><rect width="15" height="19" fill="#314b49"/><path d="M0 0H15V19" fill="none" stroke="#97b2a6" strokeWidth=".65"/></pattern>
        <filter id="soft-shadow"><feDropShadow dx="0" dy="15" stdDeviation="10" floodColor="#244231" floodOpacity=".12"/></filter>
      </defs>
      <ellipse cx="320" cy="283" rx="198" ry="36" fill="#304a39" opacity=".045"/>
      <g filter="url(#soft-shadow)">
        <path d="M104 242 320 143 542 244 325 339Z" fill="#c4cfbd"/>
        <path d="M104 234 320 136 542 235 325 330Z" fill="url(#lawn)"/>
        <path d="m316 257 42-19 129 60-42 18Z" fill="#f7f7f0"/>
        <path d="M188 155 327 91 452 150 315 216Z" fill="#eeede3"/>
        <path d="M188 155v93l127 60v-92Z" fill="url(#wall)"/>
        <path d="M315 216v92l137-63v-95Z" fill="#d5ded5"/>
        <path d="M178 151 280 65 339 91 315 217Z" fill="url(#roof)"/>
        <path d="m280 65 139 64 47 24-151 69 24-131Z" fill="#7c8b7a"/>
        <path d="m185 153 95-81 56 25-23 116Z" fill="#4e6755"/>
        <path d="m294 99 96 44-26 15-82-38Z" fill="url(#solar-grid)" stroke="#becbc1" strokeWidth="2"/>
        <path d="m280 126 78 36-29 16-67-31Z" fill="url(#solar-grid)" stroke="#becbc1" strokeWidth="2"/>
        <path d="m259 153 63 29-16 19-68-31Z" fill="url(#solar-grid)" stroke="#becbc1" strokeWidth="2"/>
        <path d="m213 185 39 18v48l-39-18Z" fill="#aac5ba" stroke="#f7f8f3" strokeWidth="5"/>
        <path d="m233 195 0 46m-19-31 38 18" stroke="#f7f8f3" strokeWidth="3"/>
        <path d="m272 213 23 11v70l-23-11Z" fill="#6f8270"/>
        <path d="m330 226 43-20v43l-43 20Z" fill="#9bb8aa" stroke="#eaf0e7" strokeWidth="5"/>
        <path d="m351 216v44m-21-12 43-20" stroke="#eaf0e7" strokeWidth="3"/>
        <path d="m391 198 39-18v43l-39 18Z" fill="#9bb8aa" stroke="#eaf0e7" strokeWidth="5"/>
        <path d="m410 190 0 42m-19-12 39-18" stroke="#eaf0e7" strokeWidth="3"/>
        <path d="m402 250 25-12 26 12-25 12Z" fill="#fdfefb"/>
        <path d="m402 250v44l26 12v-44Z" fill="#f2f4ef"/>
        <path d="m428 262 25-12v44l-25 12Z" fill="#b7c6ba"/>
        <path d="m408 264 13 6v17l-13-6Z" fill="#284c38"/>
        <path d="m411 269 7 3v9l-7-3Z" fill="#b4d58c"/>
        <path d="M412 287 420 291" stroke="#95a493" strokeWidth="1.5"/>
        <path d="m187 252 32-15 17 8-33 15Z" fill="#a7ba95"/>
        <g fill="#a8bb98"><ellipse cx="170" cy="240" rx="20" ry="10"/><ellipse cx="183" cy="246" rx="18" ry="8"/><ellipse cx="466" cy="231" rx="21" ry="10"/></g>
        <path d="M479 216v-62" stroke="#7e8d6b" strokeWidth="5"/>
        <ellipse cx="479" cy="163" rx="24" ry="37" fill="#a9bd97"/><ellipse cx="485" cy="159" rx="15" ry="28" fill="#b8cba6"/>
        <path d="M151 209v-44" stroke="#7e8d6b" strokeWidth="4"/>
        <ellipse cx="151" cy="174" rx="17" ry="27" fill="#b7c9a7"/>
      </g>
      <g fill="none" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2">
        <path className={`flow-route solar-route ${active(point?.pvKw) ? "is-active" : ""}`} d="M98 89H168L226 116" markerEnd={active(point?.pvKw) ? "url(#flow-arrow)" : undefined}/>
        <path className={`flow-route grid-route ${active(grid) ? "is-active" : ""}`} d={(grid ?? 0) < 0 ? "M210 242H127L97 256" : "M97 256 127 242H210"} markerEnd={active(grid) ? "url(#flow-arrow)" : undefined}/>
        <path className={`flow-route battery-route ${active(battery) ? "is-active" : ""}`} d={(battery ?? 0) < 0 ? "M450 281 499 302H554" : "M554 302H499L450 281"} markerEnd={active(battery) ? "url(#flow-arrow)" : undefined}/>
        <path className={`flow-route home-route ${active(point?.loadKw) ? "is-active" : ""}`} d="M446 175 492 153H553" markerEnd={active(point?.loadKw) ? "url(#flow-arrow)" : undefined}/>
      </g>
    </svg>
    <div className="flow-label flow-solar"><span className="flow-icon"><SunIcon size={17}/></span><div><span>光伏发电</span><strong>{number(point?.pvKw)} <small>kW</small></strong></div></div>
    <div className="flow-label flow-home"><span className="flow-icon neutral"><HouseLineIcon size={17}/></span><div><span>家庭用电</span><strong>{number(point?.loadKw)} <small>kW</small></strong></div></div>
    <div className="flow-label flow-grid"><span className="flow-icon neutral"><PlugIcon size={17}/></span><div><span>{grid == null ? '电网功率' : grid < 0 ? '向电网送电' : '从电网取电'}</span><strong>{number(grid == null ? null : Math.abs(grid))} <small>kW</small></strong></div></div>
    <div className="flow-label flow-battery"><span className="flow-icon"><BatteryHighIcon size={17}/></span><div><span>{battery == null ? '储能功率' : battery < -.01 ? '储能充电' : battery > .01 ? '储能放电' : '储能待机'}</span><strong>{number(battery == null ? null : Math.abs(battery))} <small>kW</small></strong></div></div>
  </div>;
}
