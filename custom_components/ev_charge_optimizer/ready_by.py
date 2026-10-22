"""Ready By slot choice. Callers pass now from coordinator._now()."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from .const import CHARGE_EFFICIENCY

SOLAR_FORECAST_DERATE = 0.7
PRICE_VALLEY = "valley"
PRICE_SHOULDER = "shoulder"
PRICE_PEAK = "peak"
_PRICE_RANK = {PRICE_VALLEY: 0, PRICE_SHOULDER: 1, PRICE_PEAK: 2}


@dataclass(frozen=True, slots=True)
class ChargeSlot:
    start: datetime
    end: datetime
    price_class: str
    power_kw: float

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    @property
    def energy_kwh(self) -> float:
        return self.power_kw * self.hours


@dataclass(frozen=True, slots=True)
class ForecastPeriod:
    start: datetime
    end: datetime
    pv_kw: float


@dataclass(frozen=True, slots=True)
class ReadyByInputs:
    now: datetime
    ready: time
    soc: float
    target_soc: float
    battery_kwh: float
    power_kw: float
    valley_start: time
    valley_end: time
    weekends_valley: bool
    peak_hours: frozenset[int]
    house_kw: float
    max_kw: float
    periods: tuple[ForecastPeriod, ...]


@dataclass(frozen=True, slots=True)
class ReadyByPlan:
    deadline: datetime
    ready: time
    target_soc: float
    raw_need_kwh: float
    need_kwh: float
    solar_credit_kwh: float
    chosen: tuple[ChargeSlot, ...]
    shortfall_kwh: float
    projected_soc: float

    def charges_at(self, now: datetime) -> bool:
        return any(slot.start <= now < slot.end for slot in self.chosen)

    def price_at(self, now: datetime) -> str:
        for slot in self.chosen:
            if slot.start <= now < slot.end:
                return slot.price_class
        return PRICE_SHOULDER

    def next_chosen(self, now: datetime) -> ChargeSlot | None:
        upcoming = [slot for slot in self.chosen if slot.start > now]
        if not upcoming:
            return None
        return min(upcoming, key=lambda slot: slot.start)


def parse_peak_hours(text: str) -> frozenset[int]:
    hours: set[int] = set()
    for part in text.split(","):
        piece = part.strip()
        if "-" not in piece:
            continue
        start_s, end_s = piece.split("-", 1)
        try:
            start_h = int(start_s)
            end_h = int(end_s)
        except ValueError:
            continue
        if not (0 <= start_h <= 24 and 0 <= end_h <= 24):
            continue
        if start_h == end_h:
            continue
        if start_h < end_h:
            hours.update(range(start_h, end_h))
        else:
            hours.update(range(start_h, 24))
            hours.update(range(end_h))
    return frozenset(hours)


def in_valley_window(moment: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= moment < end
    return moment >= start or moment < end


def is_valley(
    moment: datetime,
    start: time,
    end: time,
    *,
    weekends_valley: bool,
) -> bool:
    if weekends_valley and moment.weekday() >= 5:
        return True
    return in_valley_window(moment.time(), start, end)


def price_class(
    moment: datetime,
    *,
    valley_start: time,
    valley_end: time,
    weekends_valley: bool,
    peak_hours: frozenset[int],
) -> str:
    if is_valley(
        moment, valley_start, valley_end, weekends_valley=weekends_valley
    ):
        return PRICE_VALLEY
    if moment.hour in peak_hours:
        return PRICE_PEAK
    return PRICE_SHOULDER


def next_deadline(now: datetime, ready: time) -> datetime:
    candidate = now.replace(
        hour=ready.hour,
        minute=ready.minute,
        second=ready.second,
        microsecond=0,
    )
    if candidate <= now:
        return candidate + timedelta(days=1)
    return candidate


def next_valley_start(
    now: datetime,
    start: time,
    end: time,
    *,
    weekends_valley: bool,
) -> datetime:
    for minutes in range(1, 8 * 24 * 60):
        moment = now + timedelta(minutes=minutes)
        if is_valley(moment, start, end, weekends_valley=weekends_valley):
            return moment.replace(second=0, microsecond=0)
    return now


def wall_need_kwh(soc: float, target_soc: float, battery_kwh: float) -> float:
    gap = max(0.0, (target_soc - soc) / 100 * battery_kwh)
    if CHARGE_EFFICIENCY <= 0:
        return gap
    return gap / CHARGE_EFFICIENCY


def _parse_local(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.replace(tzinfo=None)
    return parsed


def periods_from_raw(items: object) -> tuple[ForecastPeriod, ...]:
    if not isinstance(items, list):
        return ()
    parsed: list[tuple[datetime, float]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        start = _parse_local(item.get("period_start"))
        pv = item.get("pv_estimate")
        if start is None or isinstance(pv, bool) or not isinstance(pv, (int, float)):
            continue
        parsed.append((start, float(pv)))
    parsed.sort(key=lambda pair: pair[0])
    periods: list[ForecastPeriod] = []
    for index, (start, pv) in enumerate(parsed):
        if index + 1 < len(parsed):
            nxt = parsed[index + 1][0]
            if nxt <= start or nxt - start > timedelta(hours=2):
                end = start + timedelta(minutes=30)
            else:
                end = nxt
        else:
            end = start + timedelta(minutes=30)
        periods.append(ForecastPeriod(start, end, pv))
    return tuple(periods)


def build_slots(
    now: datetime,
    deadline: datetime,
    power_kw: float,
    price_of,
) -> list[ChargeSlot]:
    if deadline <= now:
        return []
    slots: list[ChargeSlot] = []
    cursor = now
    while cursor < deadline:
        hour_end = (cursor + timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0
        )
        end = min(hour_end, deadline)
        if end <= cursor:
            break
        slots.append(ChargeSlot(cursor, end, price_of(cursor), power_kw))
        cursor = end
    return slots


def _overlap_hours(
    left_start: datetime,
    left_end: datetime,
    right_start: datetime,
    right_end: datetime,
) -> float:
    start = max(left_start, right_start)
    end = min(left_end, right_end)
    if end <= start:
        return 0.0
    return (end - start).total_seconds() / 3600


def solar_credit_kwh(
    slots: list[ChargeSlot],
    periods: tuple[ForecastPeriod, ...],
    house_kw: float,
    max_kw: float,
) -> float:
    credit = 0.0
    for slot in slots:
        for period in periods:
            overlap = _overlap_hours(
                slot.start, slot.end, period.start, period.end
            )
            if overlap <= 0:
                continue
            surplus_kw = min(max(0.0, period.pv_kw - house_kw), max(0.0, max_kw))
            credit += surplus_kw * SOLAR_FORECAST_DERATE * overlap
    return credit


def choose_slots(
    slots: list[ChargeSlot], need_kwh: float
) -> tuple[tuple[ChargeSlot, ...], float]:
    if need_kwh <= 0:
        return (), 0.0
    ordered = sorted(slots, key=lambda slot: (_PRICE_RANK[slot.price_class], slot.start))
    chosen: list[ChargeSlot] = []
    total = 0.0
    for slot in ordered:
        if total >= need_kwh:
            break
        chosen.append(slot)
        total += slot.energy_kwh
    capacity = sum(slot.energy_kwh for slot in slots)
    return tuple(chosen), max(0.0, need_kwh - capacity)


def plan_ready_by(inputs: ReadyByInputs) -> ReadyByPlan:
    deadline = next_deadline(inputs.now, inputs.ready)
    raw_need = wall_need_kwh(inputs.soc, inputs.target_soc, inputs.battery_kwh)

    def price_of(moment: datetime) -> str:
        return price_class(
            moment,
            valley_start=inputs.valley_start,
            valley_end=inputs.valley_end,
            weekends_valley=inputs.weekends_valley,
            peak_hours=inputs.peak_hours,
        )

    slots = build_slots(inputs.now, deadline, inputs.power_kw, price_of)
    credit = solar_credit_kwh(
        slots, inputs.periods, inputs.house_kw, inputs.max_kw
    )
    need = max(0.0, raw_need - credit)
    chosen, shortfall = choose_slots(slots, need)
    delivered = credit + sum(slot.energy_kwh for slot in chosen)
    projected = inputs.soc
    if inputs.battery_kwh > 0:
        projected = (
            inputs.soc
            + delivered * CHARGE_EFFICIENCY / inputs.battery_kwh * 100
        )
    projected = min(inputs.target_soc, max(inputs.soc, projected))
    return ReadyByPlan(
        deadline=deadline,
        ready=inputs.ready,
        target_soc=inputs.target_soc,
        raw_need_kwh=raw_need,
        need_kwh=need,
        solar_credit_kwh=credit,
        chosen=chosen,
        shortfall_kwh=shortfall,
        projected_soc=projected,
    )


def ready_by_status(plan: ReadyByPlan, now: datetime) -> str:
    clock = plan.ready.strftime("%H:%M")
    if plan.raw_need_kwh <= 0:
        return f"Ready By {clock}: target {round(plan.target_soc)}% reached"
    if f"{plan.shortfall_kwh:.1f}" != "0.0":
        return (
            f"Ready By {clock}: {plan.shortfall_kwh:.1f} kWh short "
            "even charging every hour"
        )
    if plan.charges_at(now):
        return (
            f"Ready By {clock}: need {plan.need_kwh:.1f} kWh, "
            f"charging now ({plan.price_at(now)})"
        )
    nxt = plan.next_chosen(now)
    if nxt is not None:
        return (
            f"Ready By {clock}: need {plan.need_kwh:.1f} kWh, "
            f"next charge {nxt.start:%H:%M} ({nxt.price_class})"
        )
    return f"Ready By {clock}: need {plan.need_kwh:.1f} kWh, solar covers it"


def slot_attributes(slots: tuple[ChargeSlot, ...]) -> list[dict[str, str]]:
    ordered = sorted(slots, key=lambda slot: slot.start)
    return [
        {
            "start": slot.start.isoformat(),
            "end": slot.end.isoformat(),
            "price_class": slot.price_class,
        }
        for slot in ordered
    ]
