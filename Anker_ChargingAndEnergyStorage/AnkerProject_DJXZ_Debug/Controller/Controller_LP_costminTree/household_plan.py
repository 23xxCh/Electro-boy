# -*- coding: utf-8 -*-
"""家庭声明如何进入现有 LP。

只生成试算和确认对象。不写保护阈值，不调用 Home Assistant。
数学与 model_lp.solve 的六个变量一致；新设备多一列功率。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
from scipy.sparse import coo_matrix, vstack

from .model_lp import _solve_highs
from .site_params import SiteParams


class PlanRejected(ValueError):
    """声明在求解前被拒绝。sentence 是给这一户的人看的。"""

    def __init__(self, sentence: str):
        super().__init__(sentence)
        self.sentence = sentence


@dataclass(frozen=True)
class MustRun:
    name: str
    power_kw: float
    slots: tuple[int, ...]


@dataclass(frozen=True)
class NewDevice:
    name: str
    rated_kw: float
    energy_kwh: float
    slots: tuple[int, ...]


@dataclass(frozen=True)
class Ask:
    must_run: tuple[MustRun, ...] = ()
    new_devices: tuple[NewDevice, ...] = ()
    reserve_kwh_at: tuple[tuple[int, float], ...] = ()
    charge_slots: tuple[int, ...] | None = None


@dataclass(frozen=True)
class Proposal:
    sentence: str
    status: str
    expected_cost: float
    baseline_cost: float
    plan: tuple
    ask: Ask
    expires_at: float = 0.0
    choices: tuple[Choice, ...] = ()


@dataclass(frozen=True)
class Choice:
    """一个已经求解成功的退让。点它只换到这份试算，不记下。"""

    label: str
    proposal: Proposal


@dataclass(frozen=True)
class Record:
    ask: Ask = Ask()
    noted_cost: float | None = None


_COMMON_WATTS = {"冰箱": 100, "监控": 20}


def assume_power(name: str) -> tuple[float, str]:
    """功率没说定时用常见值，并把假设说出来。返回千瓦和给用户的句子。"""
    watts = _COMMON_WATTS.get(name, 100)
    words = {100: "一百瓦", 20: "二十瓦"}.get(watts, f"{watts}瓦")
    return watts / 1000.0, f"先按{name}大约{words}算，不对再改。"


def _change_count(ask: Ask) -> int:
    return (
        len(ask.must_run) + len(ask.new_devices) + len(ask.reserve_kwh_at)
        + (0 if ask.charge_slots is None else 1)
    )


def _merge(base: Ask, extra: Ask) -> Ask:
    return Ask(
        must_run=base.must_run + extra.must_run,
        new_devices=base.new_devices + extra.new_devices,
        reserve_kwh_at=base.reserve_kwh_at + extra.reserve_kwh_at,
        charge_slots=base.charge_slots if extra.charge_slots is None else extra.charge_slots,
    )


def _power_limit(site: SiteParams) -> float:
    return site.p_grid_import_max_kw + site.p_discharge_max_kw


def _check_slots(slots: tuple[int, ...], horizon: int, name: str) -> None:
    if not slots or any(slot < 0 or slot >= horizon for slot in slots):
        raise PlanRejected(f"{name}的时段不在这次计划里。")


def validate(ask: Ask, site: SiteParams, horizon: int) -> None:
    limit = _power_limit(site)
    for device in ask.must_run:
        _check_slots(device.slots, horizon, device.name)
        if device.power_kw < 0 or device.power_kw > limit:
            raise PlanRejected(
                f"{device.name}要的功率超过了这户电网和电池能一起供上的电，没有放宽设备上限。")
    for device in ask.new_devices:
        _check_slots(device.slots, horizon, device.name)
        if device.rated_kw < 0 or device.rated_kw > limit or device.energy_kwh < 0:
            raise PlanRejected(
                f"{device.name}要的功率超过了这户电网和电池能一起供上的电，没有放宽设备上限。")
    for slot, energy in ask.reserve_kwh_at:
        if slot < 0 or slot >= horizon:
            raise PlanRejected("留电的时段不在这次计划里。")
        if energy < 0 or energy > site.capacity_kwh:
            raise PlanRejected("要留的电超过了电池能装的量，没有把电池容量改大。")
    if ask.charge_slots:
        _check_slots(ask.charge_slots, horizon, "充电")


def _names(ask: Ask) -> str:
    names = [item.name for item in ask.must_run]
    names.extend(item.name for item in ask.new_devices)
    if ask.reserve_kwh_at:
        names.append("留电")
    return "、".join(names)


def trial(e0_kwh: float, pv, load, buy, sell, site: SiteParams, ask: Ask,
          dt_h: float = 0.25, record: Record | None = None,
          now: float = 0.0, ttl: float = 3600.0) -> Proposal:
    """比较已留下的声明和这一处新声明。放不下时 sentence 说明不够，不改站点参数。"""
    if _change_count(ask) > 1:
        raise PlanRejected("一次只安排一件事。")
    horizon = len(buy)
    base_ask = record.ask if record else Ask()
    candidate = _merge(base_ask, ask)
    validate(candidate, site, horizon)
    expires_at = now + ttl
    baseline = _dispatch(e0_kwh, pv, load, buy, sell, site, base_ask, dt_h)
    result = _dispatch(e0_kwh, pv, load, buy, sell, site, candidate, dt_h)
    if result["status"] != "ok" or baseline["status"] != "ok":
        choices = ()
        if result["status"] != "ok":
            choices = _short_choices(
                e0_kwh, pv, load, buy, sell, site, candidate, ask, baseline, dt_h, expires_at,
            )
        return Proposal(
            sentence="这些时段不够。",
            status="short",
            expected_cost=float("nan"),
            baseline_cost=float(baseline.get("expected_cost", float("nan"))),
            plan=tuple(),
            ask=candidate,
            expires_at=expires_at,
            choices=choices,
        )
    return _accepted(baseline, result, ask, candidate, expires_at)


def confirm(proposal: Proposal, ask: Ask | None = None, now: float | None = None) -> Proposal:
    """确认只改状态。过期、声明对不上或算不下的提案不能变成新计划。"""
    if proposal.status != "trial":
        raise PlanRejected("这份安排还不能下发。")
    if ask is not None and ask != proposal.ask:
        raise PlanRejected("这份安排和你刚说的不是同一件事。")
    if now is not None and now > proposal.expires_at:
        raise PlanRejected("这份安排已经过期了。")
    return replace(proposal, status="confirmed")


def keep(record: Record, proposal: Proposal) -> Record:
    """只留下已确认的声明。不记录模型权重，也不下发设备。"""
    if proposal.status != "confirmed":
        raise PlanRejected("还没确认的安排不能留下。")
    return Record(ask=proposal.ask, noted_cost=record.noted_cost)


def note_outcome(record: Record, actual_cost: float) -> Record:
    """执行后的费用由调用方记入。这里不连接设备。"""
    return replace(record, noted_cost=actual_cost)


def _accepted(baseline: dict, result: dict, spoken: Ask, candidate: Ask, expires_at: float) -> Proposal:
    base_cost = float(baseline.get("expected_cost", float("nan")))
    cost = float(result["expected_cost"])
    covered = _names(spoken)
    tail = f"{covered}按你说的安排。" if covered else ""
    if math.isfinite(base_cost) and math.isfinite(cost):
        delta = cost - base_cost
        verb = "多花" if delta >= 0 else "少花"
        sentence = f"按这个安排，这些时段大约{verb} {abs(delta):.1f}。{tail}"
    else:
        sentence = f"按这个安排。{tail}"
    return Proposal(
        sentence=sentence.strip(),
        status="trial",
        expected_cost=cost,
        baseline_cost=base_cost,
        plan=tuple(result["plan"]),
        ask=candidate,
        expires_at=expires_at,
    )


def _set_must(ask: Ask, index: int, power: float) -> Ask:
    devices = list(ask.must_run)
    devices[index] = replace(devices[index], power_kw=power)
    return replace(ask, must_run=tuple(devices))


def _set_energy(ask: Ask, index: int, energy: float) -> Ask:
    devices = list(ask.new_devices)
    devices[index] = replace(devices[index], energy_kwh=energy)
    return replace(ask, new_devices=tuple(devices))


def _set_reserve(ask: Ask, index: int, energy: float) -> Ask:
    floors = list(ask.reserve_kwh_at)
    slot, _energy = floors[index]
    floors[index] = (slot, energy)
    return replace(ask, reserve_kwh_at=tuple(floors))


def _axes(candidate: Ask, new: Ask):
    """先看这一次新声明的轴，再看已经留下的。充电时段不放松。"""
    axes = []
    must_from = len(candidate.must_run) - len(new.must_run)
    device_from = len(candidate.new_devices) - len(new.new_devices)
    reserve_from = len(candidate.reserve_kwh_at) - len(new.reserve_kwh_at)

    def add_must(index: int) -> None:
        device = candidate.must_run[index]
        axes.append((f"{device.name}少开一点", device.power_kw, 0.01, lambda value, index=index: _set_must(candidate, index, value)))

    def add_energy(index: int) -> None:
        device = candidate.new_devices[index]
        axes.append((f"{device.name}少充一点", device.energy_kwh, 0.1, lambda value, index=index: _set_energy(candidate, index, value)))

    def add_reserve(index: int) -> None:
        _slot, energy = candidate.reserve_kwh_at[index]
        axes.append(("留电少一点", energy, 0.1, lambda value, index=index: _set_reserve(candidate, index, value)))

    for index in range(must_from, len(candidate.must_run)):
        add_must(index)
    for index in range(device_from, len(candidate.new_devices)):
        add_energy(index)
    for index in range(reserve_from, len(candidate.reserve_kwh_at)):
        add_reserve(index)
    for index in range(must_from):
        add_must(index)
    for index in range(device_from):
        add_energy(index)
    for index in range(reserve_from):
        add_reserve(index)
    return axes


def _largest_retention(declared: float, step: float, solves) -> float | None:
    """对半找还能排开的最大保留量。中间某档反常时，按步长往下试。"""
    if declared <= 0 or step <= 0:
        return None
    if solves(declared):
        return None
    if not solves(0.0):
        return _step_down(declared, step, solves)
    best = 0.0
    lo = 0.0
    hi = declared
    failures = [declared]
    successes = [0.0]
    abnormal = False
    for _ in range(24):
        if hi - lo <= step / 2:
            break
        mid = (lo + hi) / 2
        ok = solves(mid)
        if ok and any(fail < mid - 1e-9 for fail in failures):
            abnormal = True
            break
        if not ok and any(success > mid + 1e-9 for success in successes):
            abnormal = True
            break
        if ok:
            successes.append(mid)
            best = mid
            lo = mid
        else:
            failures.append(mid)
            hi = mid
    if abnormal or not solves(best):
        return _step_down(declared, step, solves)
    if best > step and not solves(best / 2):
        return _step_down(declared, step, solves)
    return best


def _step_down(declared: float, step: float, solves) -> float | None:
    count = int(math.floor((declared - 1e-9) / step))
    for k in range(count, -1, -1):
        probe = round(k * step, 6)
        if probe >= declared:
            continue
        if solves(probe):
            return probe
    return None


def _short_choices(e0_kwh, pv, load, buy, sell, site, candidate: Ask, spoken: Ask,
                   baseline: dict, dt_h: float, expires_at: float) -> tuple[Choice, ...]:
    found = []
    for label, declared, step, apply in _axes(candidate, spoken):
        def solves(value: float, apply=apply) -> bool:
            return _dispatch(e0_kwh, pv, load, buy, sell, site, apply(value), dt_h)["status"] == "ok"

        retained = _largest_retention(declared, step, solves)
        if retained is None or retained >= declared - 1e-9:
            continue
        relaxed = apply(retained)
        result = _dispatch(e0_kwh, pv, load, buy, sell, site, relaxed, dt_h)
        if result["status"] != "ok":
            continue
        found.append(Choice(label, _accepted(baseline, result, spoken, relaxed, expires_at)))
        if len(found) == 2:
            break
    return tuple(found)


def _dispatch(e0_kwh, pv, load, buy, sell, site: SiteParams, ask: Ask, dt_h: float) -> dict:
    horizon = len(buy)
    eta = site.eff_charge
    capacity = site.capacity_kwh
    must = np.zeros(horizon)
    for device in ask.must_run:
        for slot in device.slots:
            must[slot] += device.power_kw
    load = np.asarray(load, dtype=float) + must
    pv = np.asarray(pv, dtype=float)
    buy = np.asarray(buy, dtype=float)
    sell = np.asarray(sell, dtype=float)
    devices = ask.new_devices
    n_base = 6 * horizon
    n_flex = len(devices) * horizon
    n = n_base + n_flex
    m_bind = site.p_discharge_max_kw / 0.01

    rows_eq, cols_eq, data_eq = [], [], []
    rows_ub, cols_ub, data_ub = [], [], []
    for t in range(horizon):
        b = t * 6
        rows_ub.append(t); cols_ub.append(b + 0); data_ub.append(eta * dt_h)
        if t > 0:
            rows_ub.append(t); cols_ub.append((t - 1) * 6 + 4); data_ub.append(1.0)
        rows_ub.append(horizon + t); cols_ub.append(b + 1); data_ub.append(1.0)
        rows_ub.append(horizon + t); cols_ub.append(b + 3); data_ub.append(-m_bind)
        rows_eq.append(t); cols_eq.append(b + 0); data_eq.append(-1.0)
        rows_eq.append(t); cols_eq.append(b + 1); data_eq.append(1.0)
        rows_eq.append(t); cols_eq.append(b + 2); data_eq.append(1.0)
        rows_eq.append(t); cols_eq.append(b + 3); data_eq.append(-1.0)
        rows_eq.append(t); cols_eq.append(b + 5); data_eq.append(-1.0)
        row = horizon + t
        rows_eq.append(row); cols_eq.append(b + 0); data_eq.append(-eta * dt_h)
        rows_eq.append(row); cols_eq.append(b + 1); data_eq.append(dt_h)
        rows_eq.append(row); cols_eq.append(b + 4); data_eq.append(1.0)
        if t > 0:
            rows_eq.append(row); cols_eq.append((t - 1) * 6 + 4); data_eq.append(-1.0)
        for index, device in enumerate(devices):
            column = n_base + index * horizon + t
            rows_eq.append(t); cols_eq.append(column); data_eq.append(-1.0)
            if t in device.slots:
                rows_ub.append(2 * horizon + index)
                cols_ub.append(column)
                data_ub.append(-dt_h)

    n_ub = 2 * horizon + len(devices)
    A_eq = coo_matrix((data_eq, (rows_eq, cols_eq)), shape=(2 * horizon, n))
    A_ub = coo_matrix((data_ub, (rows_ub, cols_ub)), shape=(n_ub, n))
    A = vstack([A_ub, A_eq]).tocsc()

    c = np.zeros(n)
    b_eq = np.zeros(2 * horizon)
    b_ub = np.zeros(n_ub)
    lb = np.zeros(n)
    ub = np.zeros(n)
    allow = None if ask.charge_slots is None else set(ask.charge_slots)
    floors = {slot: energy for slot, energy in ask.reserve_kwh_at}
    for t in range(horizon):
        b = t * 6
        cap = site.p_charge_max_kw if allow is None or t in allow else 0.0
        lb[b + 0], ub[b + 0] = 0.0, cap
        lb[b + 1], ub[b + 1] = 0.0, site.p_discharge_max_kw
        lb[b + 2], ub[b + 2] = 0.0, site.p_grid_import_max_kw
        lb[b + 3], ub[b + 3] = 0.0, site.p_grid_export_max_kw
        lb[b + 4], ub[b + 4] = floors.get(t, 0.0), capacity
        lb[b + 5], ub[b + 5] = 0.0, max(float(pv[t]), 0.0)
        if sell[t] < 0.0:
            ub[b + 1] = 0.0
            ub[b + 3] = 0.0
        c[b + 2] += buy[t] * dt_h
        c[b + 3] -= sell[t] * dt_h
        c[b + 0] += 1e-5 * dt_h
        c[b + 1] += 1e-5 * dt_h
        b_ub[t] = capacity - (e0_kwh if t == 0 else 0.0)
        b_eq[t] = load[t] - pv[t]
        for index, device in enumerate(devices):
            column = n_base + index * horizon + t
            ub[column] = device.rated_kw if t in device.slots else 0.0
            c[column] += 1e-6 * dt_h
    for index, device in enumerate(devices):
        b_ub[2 * horizon + index] = -device.energy_kwh
    b_eq[horizon] = e0_kwh

    res = _solve_highs(c, A, b_ub, b_eq, lb, ub)
    if not res.success:
        return {"status": "short", "expected_cost": float("nan"), "plan": []}
    base = res.x[:n_base].reshape(horizon, 6)
    flex = []
    if devices:
        flex = res.x[n_base:].reshape(len(devices), horizon)
    plan = []
    for t in range(horizon):
        slot = {
            "charge_kw": float(base[t, 0]),
            "discharge_kw": float(base[t, 1]),
            "import_kw": float(base[t, 2]),
            "export_kw": float(base[t, 3]),
            "energy_kwh": float(base[t, 4]),
            "curtail_kw": float(base[t, 5]),
            "must_kw": float(must[t]),
            "new_kw": [float(flex[index, t]) for index in range(len(devices))],
        }
        plan.append(slot)
    cost = float(sum((base[t, 2] * buy[t] - base[t, 3] * sell[t]) * dt_h for t in range(horizon)))
    return {"status": "ok", "expected_cost": cost, "plan": plan}
