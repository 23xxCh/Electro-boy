import type { Point } from './types';

export type Fact = { title: string; detail: string; status: 'known' | 'missing'; evidence: string };
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

/** Presentation facts only. Never infer equipment, backup capability or a bill. */
export function familyFacts(points: (Point | undefined)[]) {
  const ready = points.length > 0 && points.every(p => p != null && p.time === points[0]?.time);
  const total = (key: 'pvKw' | 'loadKw') => ready && points.every(p => finite(p?.[key]))
    ? points.reduce((value, p) => value + p![key]!, 0) : null;
  const pv = total('pvKw');
  const load = total('loadKw');
  const coverage = pv != null && pv >= 0 && load != null && load > 0 ? Math.min(100, pv / load * 100) : null;
  let supply: Fact = { title: '这一刻，还缺少用电记录', detail: '先补齐发电和用电数据，再判断阳光够不够。', status: 'missing', evidence: '缺少同一时刻的发电或用电记录。' };
  if (pv != null && load != null && pv >= 0 && load >= 0) {
    supply = load === 0
      ? { title: '这条记录里，家里没有用电', detail: '没有用电不等于停电；具体电器状态仍需设备记录。', status: 'known', evidence: '家庭用电功率为 0。' }
      : pv >= load
      ? { title: '这一刻，阳光够家里用', detail: '太阳发出的电，比家里此刻用的多。多出的电可能存进电池，也可能送到电网。', status: 'known', evidence: '同一时刻的光伏发电功率不小于家庭用电功率；不是对未来的保证。' }
      : pv > 0.03
      ? { title: '阳光帮一部分，剩下还要补', detail: '此刻太阳发的电不够覆盖全部用电，还需要电池或电网补充。', status: 'known', evidence: '光伏发电功率低于家庭用电功率。' }
      : { title: '这一刻，主要靠电池或电网', detail: '这条记录中的太阳发电很少。有没有停电，不能只看电池电量。', status: 'known', evidence: '光伏发电不超过 0.03 kW；电网是否可用尚无遥测。' };
  }
  let grid = '还不知道有没有买电';
  let gridDetail = '这份记录缺少电网数据。';
  if (ready && points.every(p => finite(p?.gridKw))) {
    // Independent households can simultaneously buy and export. Do not net them.
    const buying = points.some(p => p!.gridKw! > .03);
    const selling = points.some(p => p!.gridKw! < -.03);
    grid = buying && selling ? '有的家庭买电，有的送电' : buying ? '有电从电网买进来' : selling ? '有多余的电送到电网' : '这一刻几乎没和电网交换';
    gridDetail = '根据保存的策略推算，不是实时电表读数；送出电不等于已经赚到钱。';
  }
  const socs = ready ? points.map(p => p?.soc) : [];
  const validSoc = socs.length > 0 && socs.every(s => finite(s) && s >= 0 && s <= 100);
  const soc = validSoc && socs.length === 1 ? socs[0]! : null;
  const battery = !ready || points.some(p => !finite(p?.batteryKw)) ? '电池动作还没有记录'
    : points.some(p => p!.batteryKw! < -.01) && points.some(p => p!.batteryKw! > .01) ? '不同家庭，电池各有安排'
    : points.some(p => p!.batteryKw! < -.01) ? '计划把电存进电池'
    : points.some(p => p!.batteryKw! > .01) ? '计划让电池放电' : '电池暂时歇一歇';
  const duration: Fact = { title: '还能用多久？暂时还算不准', detail: '还要知道能用的电量、预留多少电和接下来怎么用。电池没电，也不等于家里会停电。', status: 'missing', evidence: '未接入完整的可用容量、预留约束、效率、功率限制、负荷预测和离网能力。不能用电量百分比直接承诺供电小时数。' };
  const outlook: Fact = { title: '今晚够不够？还不能打包票', detail: '需要接上接下来的天气和用电预测。这份历史记录不能替你判断今晚。', status: 'missing', evidence: '当前是历史回放，没有当前时刻起的未来预测或设备执行回读。' };
  const money: Fact = { title: '省了多少？等账单算清楚', detail: '发电、卖电收入和省下的电费不是一回事。缺少结算数据，现在不能说赚了多少。', status: 'missing', evidence: '尚无完整费率、购售电结算、对照基线与成本；策略预期费用不是账单。' };
  return { supply, pv, load, coverage, grid, gridDetail, battery, soc, duration, outlook, money };
}
