"""Inverter spare and Boost budget.

In is contract watts / 230 V, the UNE 20317:2015 reference for the ICP-M curve.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

CONTRACT_VOLTAGE: Final = 230.0
BOOST_FALLBACK_VOLTAGE: Final = 215.0
BOOST_HARD_FACTOR: Final = 1.10
BOOST_WINDOW: Final = timedelta(hours=2)
ASSIST_SUSPEND: Final = timedelta(minutes=5)
BOOST_GUARD: Final = timedelta(seconds=60)

BoostSample = tuple[datetime, float]


def contract_amps(grid_max_w: float) -> float:
    return grid_max_w / CONTRACT_VOLTAGE


def battery_spare_watts(
    *,
    max_discharge_w: float,
    inverter_ac_max_w: float,
    pv_w: float,
    discharge_w: float,
) -> float:
    room = min(max_discharge_w, inverter_ac_max_w - pv_w)
    return max(0.0, room - discharge_w)


def boost_cap_watts(grid_max_w: float, factor: float, voltage: float) -> float:
    return factor * contract_amps(grid_max_w) * voltage


def budget_seconds(samples: list[BoostSample], now: datetime) -> float:
    cutoff = now - BOOST_WINDOW
    return sum(seconds for at, seconds in samples if at > cutoff)


def accrue_boost(
    samples: list[BoostSample],
    previous: datetime | None,
    now: datetime,
    *,
    above_contract: bool,
) -> tuple[list[BoostSample], datetime]:
    cutoff = now - BOOST_WINDOW
    kept = [(at, seconds) for at, seconds in samples if at > cutoff]
    if previous is not None and above_contract:
        delta = (now - previous).total_seconds()
        if delta > 0:
            kept.append((now, delta))
    return kept, now


def boost_status_text(
    *,
    guarding: bool,
    cooling: bool,
    cooldown_remaining_s: float,
    over_amps: float,
    minutes_remaining: int,
) -> str:
    if guarding:
        return "Boost: over 1.1×In, backing off"
    if cooling:
        minutes = max(1, round(cooldown_remaining_s / 60))
        return f"Boost: cooling down, {minutes} min"
    return f"Boost: +{over_amps:.1f} A over contract, {minutes_remaining} min left"
