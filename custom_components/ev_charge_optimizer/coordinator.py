"""Data coordinator for EV Charge Optimizer."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, time, timedelta
from typing import Any, assert_never

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_BATTERY_MAX_DISCHARGE_W,
    CONF_BATTERY_MIN_SOC,
    CONF_BATTERY_POWER_POSITIVE_DISCHARGE,
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_RESERVE_SOC,
    CONF_BATTERY_SOC_SENSOR,
    CONF_BOOST_COOLDOWN_MINUTES,
    CONF_BOOST_FACTOR,
    CONF_BOOST_MAX_MINUTES,
    CONF_CHARGER_NUMBER_ENTITY,
    CONF_CHARGER_SWITCH_ENTITY,
    CONF_CHARGER_WAKE_ENTITY,
    CONF_EV_BATTERY_KWH,
    CONF_EV_CHARGE_LIMIT_ENTITY,
    CONF_EV_CHARGING_STATE_SENSOR,
    CONF_EV_POWER_SENSOR,
    CONF_EV_SOC_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_MAX_POWER,
    CONF_HOUSE_CONSUMPTION_SENSOR,
    CONF_INVERTER_AC_MAX_W,
    CONF_MAX_AMPS,
    CONF_MAX_STEP,
    CONF_MIN_AMPS,
    CONF_PEAK_HOURS,
    CONF_POWER_BUFFER,
    CONF_PRIORITIZE_BATTERY,
    CONF_ROLLING_WINDOW,
    CONF_SHED_LOADS,
    CONF_SHED_MAX_MINUTES,
    CONF_SOLAR_FORECAST_TODAY,
    CONF_SOLAR_FORECAST_TOMORROW,
    CONF_SOLAR_PRODUCTION_SENSOR,
    CONF_STATIC_VOLTAGE,
    CONF_UPDATE_INTERVAL,
    CONF_VALLEY_AMPS,
    CONF_VALLEY_END,
    CONF_VALLEY_START,
    CONF_VOLTAGE_SENSOR,
    CONF_WEEKENDS_VALLEY,
    DEFAULT_BATTERY_MAX_DISCHARGE_W,
    DEFAULT_BATTERY_MIN_SOC,
    DEFAULT_BATTERY_POWER_POSITIVE_DISCHARGE,
    DEFAULT_BATTERY_RESERVE_SOC,
    DEFAULT_BOOST_COOLDOWN_MINUTES,
    DEFAULT_BOOST_FACTOR,
    DEFAULT_BOOST_MAX_MINUTES,
    DEFAULT_EV_BATTERY_KWH,
    DEFAULT_GRID_MAX_POWER,
    DEFAULT_INVERTER_AC_MAX_W,
    DEFAULT_MAX_AMPS,
    DEFAULT_MAX_EV_SOC,
    DEFAULT_MAX_STEP,
    DEFAULT_MIN_AMPS,
    DEFAULT_PEAK_HOURS,
    DEFAULT_POWER_BUFFER,
    DEFAULT_PRIORITIZE_BATTERY,
    DEFAULT_READY_BY,
    DEFAULT_ROLLING_WINDOW,
    DEFAULT_SHED_MAX_MINUTES,
    DEFAULT_STATIC_VOLTAGE,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VALLEY_AMPS,
    DEFAULT_VALLEY_END,
    DEFAULT_VALLEY_START,
    DEFAULT_WEEKENDS_VALLEY,
    DOMAIN,
    ChargeMode,
    coerce_mode,
)
from .ready_by import (
    ReadyByInputs,
    ReadyByPlan,
    is_valley,
    next_valley_start,
    parse_peak_hours,
    periods_from_raw,
    plan_ready_by,
    ready_by_status,
    slot_attributes,
)
from .supply import (
    ASSIST_SUSPEND,
    BOOST_FALLBACK_VOLTAGE,
    BOOST_GUARD,
    BOOST_HARD_FACTOR,
    accrue_boost,
    battery_spare_watts,
    boost_cap_watts,
    boost_status_text,
    budget_seconds,
    contract_amps,
)

_LOGGER = logging.getLogger(__name__)

WAKE_COOLDOWN = timedelta(minutes=15)
SWITCH_OFF_RETRY = timedelta(minutes=5)
SHED_STEP_DWELL = timedelta(seconds=60)
SHED_COOLDOWN = timedelta(minutes=30)
SHED_IDLE_CYCLES = 3
SPARE_MIN_DISCHARGE_W = 200
_SHED_STORE_VERSION = 1
_DRAWING_STATES = frozenset({"charging", "starting"})
_NOT_CHARGEABLE_STATES = frozenset({"complete", "disconnected"})
_KW_UNITS = frozenset({"kw", "kilowatt", "kilowatts"})


def _now() -> datetime:
    """Clock seam for the wake cooldown."""
    return datetime.now()


def _parse_hhmm(value: str):
    """Parse a time string from HA TimeSelector (HH:MM or HH:MM:SS)."""
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Invalid time string: {value!r}")


class EVChargeOptimizerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that runs the regulation loop."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self._entry = entry
        self._readings: deque[float] = deque(
            maxlen=int(self._opt(CONF_ROLLING_WINDOW, DEFAULT_ROLLING_WINDOW))
        )
        self._last_target: float = 0
        self._last_wake_at: datetime | None = None
        self._charger_off_sent_at: datetime | None = None
        self.mode: ChargeMode = coerce_mode(
            entry.data.get("default_mode", ChargeMode.SOLAR)
        )
        self.min_amps: float = self._opt(CONF_MIN_AMPS, DEFAULT_MIN_AMPS)
        self.max_amps: float = self._opt(CONF_MAX_AMPS, DEFAULT_MAX_AMPS)
        self.max_ev_soc: float = DEFAULT_MAX_EV_SOC
        self.solar_floor_amps: float = 0.0
        self.ready_by: time = _parse_hhmm(DEFAULT_READY_BY)
        self._status_text = ""
        self._shed_ids: list[str] = []
        self._shed_started_at: datetime | None = None
        self._last_shed_at: datetime | None = None
        self._shed_cooldown_until: datetime | None = None
        self._zero_ev_cycles = 0
        self._shed_store_loaded = False
        self._defer_shed = False
        self._boost_samples: list[tuple[datetime, float]] = []
        self._boost_sampled_at: datetime | None = None
        self._boost_cooldown_until: datetime | None = None
        self._boost_guard_until: datetime | None = None
        self._assist_over_cycles = 0
        self._assist_suspended_until: datetime | None = None
        self._shed_store: Store[dict[str, Any]] = Store(
            hass,
            _SHED_STORE_VERSION,
            f"{DOMAIN}.shed.{entry.entry_id}",
        )

        interval = int(self._opt(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=2.0, immediate=False
            ),
        )

    def _opt(self, key: str, default: Any) -> Any:
        """Get a value from options, falling back to data, then default."""
        return self._entry.options.get(
            key, self._entry.data.get(key, default)
        )

    def _get_sensor_value(self, entity_id: str | None) -> float | None:
        """Read a numeric sensor value, returning None if unavailable."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _get_voltage(self) -> float:
        """Get current voltage from sensor or static config.

        Car-side charger voltage sensors (e.g. Tesla) read ~1.5 V pilot
        voltage while the car is idle; dividing watts by that commands
        max amps at charge start. Only trust plausible mains readings.
        """
        voltage_entity = self._entry.data.get(CONF_VOLTAGE_SENSOR)
        if voltage_entity:
            val = self._get_sensor_value(voltage_entity)
            if val and 100 <= val <= 260:
                return val
        return float(self._opt(CONF_STATIC_VOLTAGE, DEFAULT_STATIC_VOLTAGE))

    def _rolling_average(self) -> float:
        """Calculate rolling average of export readings."""
        if not self._readings:
            return 0.0
        return sum(self._readings) / len(self._readings)

    def _get_battery_soc(self) -> float | None:
        """Read battery state-of-charge percentage if sensor configured."""
        return self._get_sensor_value(
            self._entry.data.get(CONF_BATTERY_SOC_SENSOR)
        )

    def _get_power_watts(self, entity_id: str | None) -> float | None:
        """Read a power sensor as watts, honouring kW vs W."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            value = float(state.state)
        except (ValueError, TypeError):
            return None
        unit = str(state.attributes.get("unit_of_measurement") or "")
        if unit.casefold() in _KW_UNITS:
            return value * 1000
        return value

    def _battery_positive_discharge(self) -> bool:
        """True when the battery sensor's positive sign means discharge."""
        return bool(
            self._opt(
                CONF_BATTERY_POWER_POSITIVE_DISCHARGE,
                DEFAULT_BATTERY_POWER_POSITIVE_DISCHARGE,
            )
        )

    def _battery_flows(self) -> tuple[float, float]:
        """Return (charge_w, discharge_w), both >= 0."""
        val = self._get_sensor_value(self._entity(CONF_BATTERY_POWER_SENSOR))
        if val is None:
            return 0.0, 0.0
        if self._battery_positive_discharge():
            return max(0.0, -val), max(0.0, val)
        return max(0.0, val), max(0.0, -val)

    def _get_battery_charge_demand(self) -> float:
        """Battery power being absorbed (W); 0 if discharging or unconfigured.

        Only the charging draw competes with the EV for solar. Sign follows
        CONF_BATTERY_POWER_POSITIVE_DISCHARGE (default: positive = charging).
        """
        charge_w, _discharge_w = self._battery_flows()
        return charge_w

    def _measured_ev_watts(self) -> float:
        """Actual EV draw, from the power sensor or the live setpoint."""
        sensor = self._entity(CONF_EV_POWER_SENSOR)
        if sensor:
            watts = self._get_power_watts(sensor)
            if watts is not None:
                watts = max(0.0, watts)
                # The power sensor lags the setpoint by up to ~30 s; after a
                # ramp-down the stale reading would invent headroom.
                if self._is_charger_available():
                    setpoint_w = self._get_actual_charger_amps() * self._get_voltage()
                    return min(watts, setpoint_w)
                return watts
        return self._measured_charger_power(self._get_voltage())

    def _get_ev_soc(self) -> float | None:
        """Read EV state-of-charge percentage if sensor configured."""
        return self._get_sensor_value(
            self._entry.data.get(CONF_EV_SOC_SENSOR)
        )

    def _entity(self, key: str) -> str | None:
        """Entity id from options, else data; blank means unset."""
        for source in (self._entry.options, self._entry.data):
            value = source.get(key)
            if value:
                return str(value)
        return None

    def _charging_state(self) -> str | None:
        """Raw charging-state text, or None if unset or unavailable."""
        return self._get_sensor_state(
            self._entity(CONF_EV_CHARGING_STATE_SENSOR)
        )

    def _state_allows_draw(self) -> bool:
        """True unless a charging-state sensor says the car is idle."""
        entity = self._entity(CONF_EV_CHARGING_STATE_SENSOR)
        if not entity:
            return True
        state = self._get_sensor_state(entity)
        if state is None:
            return False
        return state.strip().casefold() in _DRAWING_STATES

    def _soc_at_charge_limit(self) -> bool:
        """True when EV SoC has reached the tighter of car limit and slider."""
        soc = self._get_ev_soc()
        if soc is None:
            return False
        cap = float(self.max_ev_soc)
        limit = self._get_sensor_value(
            self._entity(CONF_EV_CHARGE_LIMIT_ENTITY)
        )
        if limit is not None:
            cap = min(cap, limit)
        return soc >= cap

    def _car_can_charge(self) -> bool:
        """False when the car is finished, unplugged, or at its limit."""
        state = self._charging_state()
        if (
            state is not None
            and state.strip().casefold() in _NOT_CHARGEABLE_STATES
        ):
            return False
        return not self._soc_at_charge_limit()

    def _measured_charger_power(self, voltage: float) -> float:
        """Setpoint watts only while the car is actually drawing."""
        if self._is_charger_switch_on() is False:
            return 0.0
        if not self._state_allows_draw():
            return 0.0
        return self._get_actual_charger_amps() * voltage

    def _solar_surplus_cap(self, charger_power: float) -> float | None:
        """PV minus household load, or None if PV is unavailable."""
        pv = self._get_sensor_value(
            self._entry.data.get(CONF_SOLAR_PRODUCTION_SENSOR)
        )
        if pv is None:
            return None
        house = (
            self._get_sensor_value(
                self._entry.data.get(CONF_HOUSE_CONSUMPTION_SENSOR)
            )
            or 0.0
        )
        return pv - max(0.0, house - charger_power)

    def _record_reading(self, reading: float, charger_power: float) -> None:
        """Store one surplus sample, capped at real solar production."""
        cap = self._solar_surplus_cap(charger_power)
        if cap is not None:
            # Export can include home-battery discharge; that is not solar.
            reading = min(reading, cap)
        self._readings.append(reading)

    def _wake_due(self) -> bool:
        """True when the car can charge and the wake cooldown has elapsed."""
        if not self._car_can_charge():
            return False
        if self._last_wake_at is None:
            return True
        return _now() - self._last_wake_at >= WAKE_COOLDOWN

    async def _async_stop_unchargeable(self) -> None:
        """Target 0 and switch the charger off, retrying sparingly; never wake or set amps."""
        self._last_target = 0
        if self._is_charger_switch_on() is not True:
            self._charger_off_sent_at = None
            return
        now = _now()
        if self._charger_off_sent_at and now - self._charger_off_sent_at < SWITCH_OFF_RETRY:
            return
        self._charger_off_sent_at = now
        await self._async_set_charger_switch(False)

    def _get_sensor_state(self, entity_id: str | None) -> str | None:
        """Read the raw state string of an entity, or None if unavailable."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        return state.state

    def _battery_blocks_solar_charge(self) -> bool:
        """True when SOC sensor configured and SOC below the min threshold.

        Used to gate Solar-Only and Min-Topup modes so the EV does not
        steal solar that should top up the home battery first.
        """
        soc = self._get_battery_soc()
        if soc is None:
            return False
        threshold = float(
            self._opt(CONF_BATTERY_MIN_SOC, DEFAULT_BATTERY_MIN_SOC)
        )
        return soc < threshold

    def _valley_bounds(self) -> tuple[time, time]:
        return (
            _parse_hhmm(self._opt(CONF_VALLEY_START, DEFAULT_VALLEY_START)),
            _parse_hhmm(self._opt(CONF_VALLEY_END, DEFAULT_VALLEY_END)),
        )

    def _weekends_valley(self) -> bool:
        return bool(self._opt(CONF_WEEKENDS_VALLEY, DEFAULT_WEEKENDS_VALLEY))

    def _is_valley_time(self) -> bool:
        start, end = self._valley_bounds()
        return is_valley(
            _now(), start, end, weekends_valley=self._weekends_valley()
        )

    def _grid_headroom_watts(self) -> float | None:
        """Watts the EV can draw without exceeding the grid contract.

        Battery discharge is not grid capacity. Battery charge counts only
        up to live import: solar-fed charging is not a grid load the EV can
        reclaim. Measured EV draw is added back so a high setpoint that the
        car is not pulling does not invent headroom.
        """
        grid_export = self._get_sensor_value(
            self._entry.data.get(CONF_GRID_EXPORT_SENSOR)
        )
        if grid_export is None:
            return None
        live_import = max(0.0, -grid_export)
        charge_w, discharge_w = self._battery_flows()
        charge_from_grid = min(charge_w, live_import)
        grid_max_power = float(
            self._opt(CONF_GRID_MAX_POWER, DEFAULT_GRID_MAX_POWER)
        )
        measured = self._measured_ev_watts()
        classic = (
            grid_max_power
            - live_import
            - discharge_w
            + charge_from_grid
            + measured
        )
        if self.mode not in (ChargeMode.MAX_POWER, ChargeMode.BOOST):
            return max(0.0, classic)
        now = _now()
        cap = self._active_cap_watts(now)
        if self._inverter_supply_active(now):
            return max(
                0.0,
                cap - live_import + measured + self._battery_spare_watts(),
            )
        return max(
            0.0,
            cap - live_import - discharge_w + charge_from_grid + measured,
        )

    def _contract_amps(self) -> float:
        return contract_amps(
            float(self._opt(CONF_GRID_MAX_POWER, DEFAULT_GRID_MAX_POWER))
        )

    def _boost_limit_seconds(self) -> float:
        return (
            float(self._opt(CONF_BOOST_MAX_MINUTES, DEFAULT_BOOST_MAX_MINUTES))
            * 60.0
        )

    def _boost_budget_seconds(self, now: datetime) -> float:
        return budget_seconds(self._boost_samples, now)

    def _boost_cooling(self, now: datetime) -> bool:
        until = self._boost_cooldown_until
        return until is not None and now < until

    def _boost_guarding(self, now: datetime) -> bool:
        until = self._boost_guard_until
        return until is not None and now < until

    def _assist_suspended(self, now: datetime) -> bool:
        until = self._assist_suspended_until
        return until is not None and now < until

    def _boosting(self, now: datetime) -> bool:
        if self.mode != ChargeMode.BOOST:
            return False
        if self._boost_guarding(now) or self._boost_cooling(now):
            return False
        return self._boost_budget_seconds(now) < self._boost_limit_seconds()

    def _active_cap_watts(self, now: datetime) -> float:
        grid_max = float(self._opt(CONF_GRID_MAX_POWER, DEFAULT_GRID_MAX_POWER))
        if not self._boosting(now):
            return grid_max
        factor = float(self._opt(CONF_BOOST_FACTOR, DEFAULT_BOOST_FACTOR))
        return boost_cap_watts(grid_max, factor, self._boost_voltage())

    def _boost_voltage(self) -> float:
        """Mains volts for the over-contract current limit.

        The static fallback (240 V) would put 1.08×In at ~1.13×In on a real
        230 V supply, past the ICP-M 1.1×In no-trip point. Without a real
        reading, assume a low mains voltage so Boost adds almost nothing.
        """
        val = self._get_sensor_value(self._entry.data.get(CONF_VOLTAGE_SENSOR))
        if val and 100 <= val <= 260:
            return val
        return BOOST_FALLBACK_VOLTAGE

    def _inverter_supply_active(self, now: datetime) -> bool:
        if self.mode not in (ChargeMode.MAX_POWER, ChargeMode.BOOST):
            return False
        if self._assist_suspended(now):
            return False
        if float(self._opt(CONF_BATTERY_MAX_DISCHARGE_W, 0)) <= 0:
            return False
        if float(self._opt(CONF_INVERTER_AC_MAX_W, 0)) <= 0:
            return False
        soc = self._get_sensor_value(self._entity(CONF_BATTERY_SOC_SENSOR))
        if soc is None:
            return False
        reserve = float(
            self._opt(CONF_BATTERY_RESERVE_SOC, DEFAULT_BATTERY_RESERVE_SOC)
        )
        return soc > reserve

    def _battery_spare_watts(self) -> float:
        now = _now()
        if not self._inverter_supply_active(now):
            return 0.0
        pv = self._get_sensor_value(self._entity(CONF_SOLAR_PRODUCTION_SENSOR))
        if pv is None:
            return 0.0
        charge_w, discharge_w = self._battery_flows()
        # A GoodWe reads 0 W for ~35 s after leaving eco_charge; only a battery
        # already discharging proves it will follow the EV. In self-use it starts
        # by itself as soon as import appears, so the climb still begins.
        if charge_w > 0 or discharge_w < SPARE_MIN_DISCHARGE_W:
            return 0.0
        return battery_spare_watts(
            max_discharge_w=float(
                self._opt(
                    CONF_BATTERY_MAX_DISCHARGE_W, DEFAULT_BATTERY_MAX_DISCHARGE_W
                )
            ),
            inverter_ac_max_w=float(
                self._opt(CONF_INVERTER_AC_MAX_W, DEFAULT_INVERTER_AC_MAX_W)
            ),
            pv_w=pv,
            discharge_w=discharge_w,
        )

    def _advance_supply(self, now: datetime) -> None:
        if self.mode != ChargeMode.BOOST:
            # Time outside Boost must not be accrued as one huge sample on re-entry.
            self._boost_sampled_at = None
        if self.mode not in (ChargeMode.MAX_POWER, ChargeMode.BOOST):
            return
        grid_export = self._get_sensor_value(
            self._entry.data.get(CONF_GRID_EXPORT_SENSOR)
        )
        if grid_export is None:
            return
        live_import = max(0.0, -grid_export)
        voltage = self._boost_voltage()
        import_current = live_import / voltage if voltage > 0 else 0.0
        contract = self._contract_amps()
        if self.mode == ChargeMode.BOOST:
            self._boost_samples, self._boost_sampled_at = accrue_boost(
                self._boost_samples,
                self._boost_sampled_at,
                now,
                above_contract=import_current > contract,
            )
            if import_current > BOOST_HARD_FACTOR * contract:
                self._boost_guard_until = now + BOOST_GUARD
            if (
                self._boost_budget_seconds(now) >= self._boost_limit_seconds()
                and not self._boost_cooling(now)
            ):
                minutes = float(
                    self._opt(
                        CONF_BOOST_COOLDOWN_MINUTES,
                        DEFAULT_BOOST_COOLDOWN_MINUTES,
                    )
                )
                self._boost_cooldown_until = now + timedelta(minutes=minutes)
        # Only an overshoot while battery spare is credited says the battery
        # didn't deliver; a house spike during a grid-only start is not that.
        if live_import > self._active_cap_watts(now) and self._battery_spare_watts() > 0:
            self._assist_over_cycles += 1
        else:
            self._assist_over_cycles = 0
        if self._assist_over_cycles >= 2:
            self._assist_suspended_until = now + ASSIST_SUSPEND
            self._assist_over_cycles = 0

    def _boost_status(self, now: datetime) -> str:
        factor = float(self._opt(CONF_BOOST_FACTOR, DEFAULT_BOOST_FACTOR))
        over = (factor - 1.0) * self._contract_amps()
        remaining = self._boost_limit_seconds() - self._boost_budget_seconds(now)
        cooldown_left = 0.0
        if self._boost_cooldown_until is not None:
            cooldown_left = (self._boost_cooldown_until - now).total_seconds()
        return boost_status_text(
            guarding=self._boost_guarding(now),
            cooling=self._boost_cooling(now)
            or self._boost_budget_seconds(now) >= self._boost_limit_seconds(),
            cooldown_remaining_s=cooldown_left,
            over_amps=over,
            minutes_remaining=max(0, round(remaining / 60)),
        )

    def _get_grid_available_amps(self) -> float:
        """Charger amps that keep grid import at or below grid_max_power."""
        voltage = self._get_voltage()
        headroom = self._grid_headroom_watts()
        if voltage <= 0 or headroom is None:
            return 0
        return headroom / voltage

    def _grid_available_power(self) -> float:
        """Headroom watts capped at the amp limit, for the available-power sensor."""
        voltage = self._get_voltage()
        headroom = self._grid_headroom_watts()
        if voltage <= 0 or headroom is None:
            return 0.0
        return min(headroom, self.max_amps * voltage)

    def _grid_limited(self) -> bool:
        """True when raw contract headroom cannot reach max amps."""
        voltage = self._get_voltage()
        headroom = self._grid_headroom_watts()
        if voltage <= 0 or headroom is None:
            return False
        return headroom / voltage < self.max_amps

    def _solar_amps(self, available_power: float, *, floor: float) -> float:
        voltage = self._get_voltage()
        if voltage <= 0:
            return 0
        solar_amps = available_power / voltage
        if floor <= 0:
            return solar_amps
        return min(max(solar_amps, floor), self._get_grid_available_amps())

    def _cheap_target(self, solar_surplus: float) -> tuple[float, float]:
        if self._is_valley_time():
            valley_amps = float(self._opt(CONF_VALLEY_AMPS, DEFAULT_VALLEY_AMPS))
            return (
                min(valley_amps, self._get_grid_available_amps()),
                self._grid_available_power(),
            )
        return self._solar_amps(solar_surplus, floor=0), solar_surplus

    def _ramped_supply_target(self, target: float) -> float:
        """Climb onto battery spare a step at a time.

        A reading of 0 W says nothing about whether the inverter will
        discharge (an MPC can hold it in eco_charge). Jumping to the full
        spare credit spiked import to 1.45 x In live; small steps keep any
        miss within what the overshoot guard catches in two cycles.
        """
        if target <= self._last_target:
            return target
        if self._last_target <= 0:
            voltage = self._get_voltage()
            spare = self._battery_spare_watts()
            headroom = self._grid_headroom_watts()
            if headroom is None or voltage <= 0:
                return 0
            return min(target, max(0.0, headroom - spare) / voltage)
        max_step = float(self._opt(CONF_MAX_STEP, DEFAULT_MAX_STEP))
        return min(target, self._last_target + max_step)

    def _apply_step_limit(self, target: float) -> float:
        """Limit amps change per cycle to prevent oscillation."""
        max_step = float(self._opt(CONF_MAX_STEP, DEFAULT_MAX_STEP))
        diff = target - self._last_target
        if abs(diff) > max_step:
            return self._last_target + (max_step if diff > 0 else -max_step)
        return target

    def _is_charger_switch_on(self) -> bool | None:
        """Read the actual charger switch state from HA."""
        entity_id = self._entry.data.get(CONF_CHARGER_SWITCH_ENTITY)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return state.state == "on"

    async def _async_set_charger_switch(self, on: bool) -> None:
        """Turn charger power switch on/off, checking actual entity state."""
        entity_id = self._entry.data.get(CONF_CHARGER_SWITCH_ENTITY)
        if not entity_id:
            return
        if on == self._is_charger_switch_on():
            return

        await self.hass.services.async_call(
            "switch",
            "turn_on" if on else "turn_off",
            {"entity_id": entity_id},
        )

    def _get_actual_charger_amps(self) -> int:
        """Read the actual charger amps from the number entity."""
        entity_id = self._entry.data.get(CONF_CHARGER_NUMBER_ENTITY)
        if not entity_id:
            return 0
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return 0
        try:
            return round(float(state.state))
        except (ValueError, TypeError):
            return 0

    async def _async_wake_charger(self) -> None:
        """Press the wake button when charger entities are unavailable."""
        entity_id = self._entry.data.get(CONF_CHARGER_WAKE_ENTITY)
        if not entity_id:
            return
        _LOGGER.debug("Charger entity unavailable, pressing wake button")
        self._last_wake_at = _now()
        await self.hass.services.async_call(
            "button",
            "press",
            {"entity_id": entity_id},
        )

    def _is_charger_available(self) -> bool:
        """Check if charger number entity is reachable."""
        entity_id = self._entry.data.get(CONF_CHARGER_NUMBER_ENTITY)
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        return state is not None and state.state not in ("unknown", "unavailable")

    async def _async_set_charger_amps(self, amps: float) -> None:
        """Write target amps to the charger number entity.

        Reads actual entity state each cycle so commands are re-sent
        when a previous call was lost (BLE hiccup, car was asleep, …).

        When a charger switch is configured:
        - amps == 0 → turn switch OFF (avoids idle inverter draw)
        - amps > 0  → turn switch ON, then set amps
        """
        entity_id = self._entry.data.get(CONF_CHARGER_NUMBER_ENTITY)
        if not entity_id:
            return

        rounded = round(amps)

        if rounded == 0:
            await self._async_set_charger_switch(False)
            return

        # Wake the car if charger entity is unavailable (asleep)
        if not self._is_charger_available():
            if self._wake_due():
                await self._async_wake_charger()
            return

        # Ensure switch is on before sending amps
        await self._async_set_charger_switch(True)

        if rounded == self._get_actual_charger_amps():
            return

        domain = entity_id.split(".")[0]

        await self.hass.services.async_call(
            domain,
            "set_value",
            {"entity_id": entity_id, "value": rounded},
        )

    def _shed_load_ids(self) -> list[str]:
        """Configured shed order; absent key means shed nothing."""
        raw = self._opt(CONF_SHED_LOADS, None)
        if not raw:
            return []
        if isinstance(raw, str):
            return [raw]
        return [str(item) for item in raw if item]

    def _shed_max_minutes(self) -> float:
        return float(self._opt(CONF_SHED_MAX_MINUTES, DEFAULT_SHED_MAX_MINUTES))

    def _is_load_on(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == "on"

    async def _async_set_load(self, entity_id: str, on: bool) -> None:
        domain = entity_id.split(".", 1)[0]
        await self.hass.services.async_call(
            domain,
            "turn_on" if on else "turn_off",
            {"entity_id": entity_id},
        )

    def _iso(self, moment: datetime | None) -> str | None:
        return moment.isoformat() if moment else None

    def _runtime_payload(self) -> dict[str, Any]:
        return {
            "entity_ids": list(self._shed_ids),
            "started_at": self._iso(self._shed_started_at),
            "boost_samples": [
                [moment.isoformat(), seconds]
                for moment, seconds in self._boost_samples
            ],
            "boost_cooldown_until": self._iso(self._boost_cooldown_until),
            "boost_guard_until": self._iso(self._boost_guard_until),
            "assist_suspended_until": self._iso(self._assist_suspended_until),
        }

    def _load_supply(self, stored: dict[str, Any]) -> None:
        samples: list[tuple[datetime, float]] = []
        for item in stored.get("boost_samples") or []:
            try:
                at_raw, seconds_raw = item
                samples.append((datetime.fromisoformat(at_raw), float(seconds_raw)))
            except (TypeError, ValueError):
                continue
        self._boost_samples = samples
        if samples:
            self._boost_sampled_at = max(sample[0] for sample in samples)
        self._boost_cooldown_until = self._parse_stored_time(
            stored.get("boost_cooldown_until")
        )
        self._boost_guard_until = self._parse_stored_time(
            stored.get("boost_guard_until")
        )
        self._assist_suspended_until = self._parse_stored_time(
            stored.get("assist_suspended_until")
        )

    def _parse_stored_time(self, value: object) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    async def _async_save_runtime(self) -> None:
        await self._shed_store.async_save(self._runtime_payload())

    async def _async_save_shed(self) -> None:
        await self._async_save_runtime()

    async def _async_restore_persisted_shed_once(self) -> None:
        """Turn shed loads back on after a restart so a fridge is not left off."""
        if self._shed_store_loaded:
            return
        self._shed_store_loaded = True
        stored = await self._shed_store.async_load()
        if not stored:
            return
        self._load_supply(stored)
        entity_ids = [str(item) for item in stored.get("entity_ids") or [] if item]
        if not entity_ids:
            return
        for entity_id in entity_ids:
            await self._async_set_load(entity_id, True)
        self._defer_shed = True
        self._shed_ids.clear()
        self._shed_started_at = None
        await self._async_save_runtime()

    async def _async_restore_shed(self, *, cooldown: bool) -> None:
        if not self._shed_ids and not cooldown:
            return
        for entity_id in list(self._shed_ids):
            await self._async_set_load(entity_id, True)
        self._shed_ids.clear()
        self._shed_started_at = None
        self._last_shed_at = None
        self._zero_ev_cycles = 0
        if cooldown:
            self._shed_cooldown_until = _now() + SHED_COOLDOWN
        await self._async_save_shed()

    async def _async_shed_next(self) -> None:
        if self._defer_shed:
            self._defer_shed = False
            return
        now = _now()
        if self._shed_cooldown_until and now < self._shed_cooldown_until:
            return
        if self._last_shed_at and now - self._last_shed_at < SHED_STEP_DWELL:
            return
        already = set(self._shed_ids)
        for entity_id in self._shed_load_ids():
            if entity_id in already or not self._is_load_on(entity_id):
                continue
            await self._async_set_load(entity_id, False)
            self._shed_ids.append(entity_id)
            if self._shed_started_at is None:
                self._shed_started_at = now
            self._last_shed_at = now
            await self._async_save_shed()
            return

    def _shed_expired(self) -> bool:
        if not self._shed_ids or self._shed_started_at is None:
            return False
        return _now() - self._shed_started_at >= timedelta(
            minutes=self._shed_max_minutes()
        )

    async def _async_sync_shed(self, target: float) -> None:
        """Shed or restore loads for this cycle. Max Power only."""
        ev_watts = self._measured_ev_watts()
        if ev_watts == 0:
            self._zero_ev_cycles += 1
        else:
            self._zero_ev_cycles = 0

        if not self._car_can_charge() or self.mode not in (
            ChargeMode.MAX_POWER,
            ChargeMode.BOOST,
        ):
            await self._async_restore_shed(cooldown=False)
            return
        if self._shed_expired():
            await self._async_restore_shed(cooldown=True)
            return
        if self._zero_ev_cycles >= SHED_IDLE_CYCLES:
            await self._async_restore_shed(cooldown=False)
            return
        # Shedding for a car that is asleep or ignoring commands frees
        # nothing it can use.
        if ev_watts > 0 and self._grid_limited():
            await self._async_shed_next()

    def _status(
        self,
        target: float,
        available: float,
        text: str,
        attrs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        shown = text[:255]
        self._status_text = shown
        return {
            "target_amps": round(target),
            "available_power": round(available, 1),
            "mode": self.mode,
            "ev_power": round(self._measured_ev_watts(), 1),
            "shed_loads": list(self._shed_ids),
            "status": shown,
            "status_attributes": attrs or {},
        }

    def _slider_reached(self) -> bool:
        soc = self._get_ev_soc()
        if soc is None or soc < float(self.max_ev_soc):
            return False
        state = self._charging_state()
        return not (
            state is not None and state.strip().casefold() == "disconnected"
        )

    def _unchargeable_status(self) -> str:
        state = self._charging_state()
        if state is not None and state.strip().casefold() == "disconnected":
            return "Car unplugged"
        return "Car full / at limit"

    def _forecast_periods(self):
        periods = []
        for key in (CONF_SOLAR_FORECAST_TODAY, CONF_SOLAR_FORECAST_TOMORROW):
            entity = self._entity(key)
            if not entity:
                continue
            state = self.hass.states.get(entity)
            if state is None or state.state in ("unknown", "unavailable"):
                continue
            periods.extend(periods_from_raw(state.attributes.get("detailedHourly")))
        return tuple(periods)

    def _ready_inputs(self, now: datetime, soc: float) -> ReadyByInputs:
        voltage = self._get_voltage()
        house = (
            self._get_sensor_value(
                self._entry.data.get(CONF_HOUSE_CONSUMPTION_SENSOR)
            )
            or 0.0
        )
        house_wo = max(0.0, house - self._measured_ev_watts())
        grid_max = float(self._opt(CONF_GRID_MAX_POWER, DEFAULT_GRID_MAX_POWER))
        power_w = min(self.max_amps * voltage, grid_max - house_wo)
        start, end = self._valley_bounds()
        return ReadyByInputs(
            now=now,
            ready=self.ready_by,
            soc=soc,
            target_soc=float(self.max_ev_soc),
            battery_kwh=float(
                self._opt(CONF_EV_BATTERY_KWH, DEFAULT_EV_BATTERY_KWH)
            ),
            power_kw=max(0.0, power_w) / 1000,
            valley_start=start,
            valley_end=end,
            weekends_valley=self._weekends_valley(),
            peak_hours=parse_peak_hours(
                str(self._opt(CONF_PEAK_HOURS, DEFAULT_PEAK_HOURS))
            ),
            house_kw=house_wo / 1000,
            max_kw=max(0.0, self.max_amps * voltage / 1000),
            periods=self._forecast_periods(),
        )

    def _plan_attributes(self, plan: ReadyByPlan) -> dict[str, Any]:
        return {
            "deadline": plan.deadline.isoformat(),
            "need_kwh": plan.need_kwh,
            "chosen_slots": slot_attributes(plan.chosen),
            "shortfall_kwh": plan.shortfall_kwh,
            "projected_soc": plan.projected_soc,
        }

    def _describe(
        self,
        target: float,
        solar_surplus: float,
        plan: ReadyByPlan | None,
        *,
        missing_soc: bool,
        now: datetime,
    ) -> str:
        match self.mode:
            case ChargeMode.OFF:
                return "Off"
            case ChargeMode.SOLAR:
                if round(target) <= 0:
                    return "Solar: waiting for surplus"
                return (
                    f"Solar: {max(solar_surplus, 0) / 1000:.1f} kW surplus "
                    f"→ {round(target)} A"
                )
            case ChargeMode.CHEAP:
                if not self._is_valley_time():
                    start, end = self._valley_bounds()
                    nxt = next_valley_start(
                        now, start, end, weekends_valley=self._weekends_valley()
                    )
                    return f"Cheap: waiting for valley at {nxt:%H:%M}"
                amps = round(target)
                valley_amps = float(self._opt(CONF_VALLEY_AMPS, DEFAULT_VALLEY_AMPS))
                requested = round(min(valley_amps, self.max_amps))
                if amps < requested:
                    return f"Cheap: valley, {amps} A (grid cap)"
                return f"Cheap: valley, {amps} A"
            case ChargeMode.READY_BY:
                if missing_soc or plan is None:
                    return "Ready By: SoC unavailable, using Cheap"
                return ready_by_status(plan, now)
            case ChargeMode.MAX_POWER:
                cap = " (grid cap)" if self._grid_limited() else ""
                count = len(self._shed_ids)
                noun = "load" if count == 1 else "loads"
                return f"Max Power: {round(target)} A{cap}, shed {count} {noun}"
            case ChargeMode.BOOST:
                return self._boost_status(now)
            case unreachable:
                assert_never(unreachable)

    async def _async_update_data(self) -> dict[str, Any]:
        """Run the regulation loop."""
        await self._async_restore_persisted_shed_once()
        try:
            return await self._async_regulate()
        finally:
            self._defer_shed = False
            await self._async_save_runtime()

    async def _async_regulate(self) -> dict[str, Any]:
        """One regulation cycle. Caller clears the restart shed deferral."""
        if self.mode == ChargeMode.OFF:
            self._last_target = 0
            await self._async_stop_unchargeable()
            await self._async_restore_shed(cooldown=False)
            return self._status(0, 0, "Off")

        if not self._car_can_charge():
            await self._async_stop_unchargeable()
            await self._async_restore_shed(cooldown=False)
            self._last_target = 0
            text = self._unchargeable_status()
            if self.mode == ChargeMode.READY_BY and self._slider_reached():
                text = (
                    f"Ready By {self.ready_by:%H:%M}: "
                    f"target {round(self.max_ev_soc)}% reached"
                )
            return self._status(0, 0, text)
        self._charger_off_sent_at = None

        power_buffer = float(self._opt(CONF_POWER_BUFFER, DEFAULT_POWER_BUFFER))
        charger_power = self._measured_ev_watts()

        prioritize_battery = bool(
            self._opt(CONF_PRIORITIZE_BATTERY, DEFAULT_PRIORITIZE_BATTERY)
        )

        if prioritize_battery:
            # Battery-first: only use power that would otherwise go to grid
            # (after battery and house have taken their share).
            #
            # When export > 0 we are definitely not over-budget, so add
            # back the charger's own draw to get the true available surplus.
            # Without this correction the target never converges — each amp
            # increase lowers the export reading, capping the charger well
            # below the real surplus.
            #
            # When export == 0 the sensor has floored (we may be importing).
            # The correction would overshoot here, so feed in the raw 0 to
            # pull the rolling average down and trigger a ramp-down.
            grid_export = self._get_sensor_value(
                self._entry.data.get(CONF_GRID_EXPORT_SENSOR)
            )
            if grid_export is None:
                _LOGGER.debug("Grid export sensor unavailable, skipping cycle")
                return self._status(self._last_target, 0, self._status_text)
            if grid_export > 0:
                self._record_reading(grid_export + charger_power, charger_power)
            else:
                self._record_reading(0, charger_power)
        else:
            # EV-first: use all solar surplus (solar minus house).
            # Subtract charger draw from house consumption to avoid
            # the feedback loop (house sensor includes charger load).
            solar_production = self._get_sensor_value(
                self._entry.data.get(CONF_SOLAR_PRODUCTION_SENSOR)
            )
            if solar_production is None:
                _LOGGER.debug(
                    "Solar production sensor unavailable, skipping cycle"
                )
                return self._status(self._last_target, 0, self._status_text)
            house_consumption = self._get_sensor_value(
                self._entry.data.get(CONF_HOUSE_CONSUMPTION_SENSOR)
            ) or 0
            household_only = max(0, house_consumption - charger_power)
            # Reserve whatever the home battery is currently absorbing —
            # otherwise the EV would steal solar that the inverter is
            # routing to the battery (e.g. while the MPC controller is
            # charging it).
            battery_demand = self._get_battery_charge_demand()
            self._record_reading(
                solar_production - household_only - battery_demand,
                charger_power,
            )

        solar_surplus = self._rolling_average() - power_buffer
        now = _now()
        self._advance_supply(now)
        plan: ReadyByPlan | None = None
        attrs: dict[str, Any] = {}
        missing_soc = False
        charging_grid = False
        battery_blocks = self._battery_blocks_solar_charge()

        match self.mode:
            case ChargeMode.OFF:
                target = 0.0
                available_power = 0.0
            case ChargeMode.SOLAR:
                if battery_blocks:
                    target = 0.0
                    available_power = 0.0
                else:
                    target = self._solar_amps(
                        solar_surplus, floor=self.solar_floor_amps
                    )
                    available_power = solar_surplus
            case ChargeMode.CHEAP:
                target, available_power = self._cheap_target(solar_surplus)
            case ChargeMode.READY_BY:
                soc = self._get_ev_soc()
                if soc is None:
                    missing_soc = True
                    target, available_power = self._cheap_target(solar_surplus)
                else:
                    plan = plan_ready_by(self._ready_inputs(now, soc))
                    attrs = self._plan_attributes(plan)
                    charging_grid = (
                        plan.raw_need_kwh > 0
                        and plan.need_kwh > 0
                        and plan.charges_at(now)
                    )
                    if charging_grid:
                        target = min(self.max_amps, self._get_grid_available_amps())
                        available_power = self._grid_available_power()
                    elif battery_blocks:
                        target = 0.0
                        available_power = 0.0
                    else:
                        target = self._solar_amps(solar_surplus, floor=0)
                        available_power = solar_surplus
            case ChargeMode.MAX_POWER | ChargeMode.BOOST:
                target = self._ramped_supply_target(
                    min(self.max_amps, self._get_grid_available_amps())
                )
                available_power = self._grid_available_power()
            case unreachable:
                assert_never(unreachable)

        target = max(0, min(target, self.max_amps))
        # Step limit only while a solar target is already moving. A start
        # from 0 would deadlock under min_amps; a stop must not ramp down
        # while the house is importing.
        solar_ramp = self.mode == ChargeMode.SOLAR or (
            self.mode == ChargeMode.READY_BY and not charging_grid
        )
        if solar_ramp and self._last_target > 0 and target > 0:
            target = self._apply_step_limit(target)

        if target < self.min_amps:
            target = 0

        self._last_target = target

        await self._async_set_charger_amps(target)
        await self._async_sync_shed(target)

        text = self._describe(
            target,
            solar_surplus,
            plan,
            missing_soc=missing_soc,
            now=now,
        )
        return self._status(target, available_power, text, attrs)
