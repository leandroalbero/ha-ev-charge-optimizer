"""Data coordinator for EV Charge Optimizer."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_BATTERY_MIN_SOC,
    CONF_BATTERY_POWER_POSITIVE_DISCHARGE,
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_CHARGER_NUMBER_ENTITY,
    CONF_CHARGER_SWITCH_ENTITY,
    CONF_CHARGER_WAKE_ENTITY,
    CONF_EV_CHARGE_LIMIT_ENTITY,
    CONF_EV_CHARGING_STATE_SENSOR,
    CONF_EV_POWER_SENSOR,
    CONF_EV_SOC_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_MAX_POWER,
    CONF_GUARANTEED_MIN_AMPS,
    CONF_HOUSE_CONSUMPTION_SENSOR,
    CONF_MAX_AMPS,
    CONF_MAX_STEP,
    CONF_MIN_AMPS,
    CONF_POWER_BUFFER,
    CONF_PRIORITIZE_BATTERY,
    CONF_ROLLING_WINDOW,
    CONF_SHED_LOADS,
    CONF_SHED_MAX_MINUTES,
    CONF_SOLAR_PRODUCTION_SENSOR,
    CONF_STATIC_VOLTAGE,
    CONF_UPDATE_INTERVAL,
    CONF_VALLEY_AMPS,
    CONF_VALLEY_END,
    CONF_VALLEY_START,
    CONF_VALLEY_TARGET_SOC,
    CONF_VOLTAGE_SENSOR,
    DEFAULT_BATTERY_MIN_SOC,
    DEFAULT_BATTERY_POWER_POSITIVE_DISCHARGE,
    DEFAULT_GRID_MAX_POWER,
    DEFAULT_GUARANTEED_MIN_AMPS,
    DEFAULT_MAX_AMPS,
    DEFAULT_MAX_EV_SOC,
    DEFAULT_MAX_STEP,
    DEFAULT_MIN_AMPS,
    DEFAULT_POWER_BUFFER,
    DEFAULT_PRIORITIZE_BATTERY,
    DEFAULT_ROLLING_WINDOW,
    DEFAULT_SHED_MAX_MINUTES,
    DEFAULT_STATIC_VOLTAGE,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VALLEY_AMPS,
    DEFAULT_VALLEY_END,
    DEFAULT_VALLEY_START,
    DEFAULT_VALLEY_TARGET_SOC,
    DOMAIN,
    ChargeMode,
)

_LOGGER = logging.getLogger(__name__)

WAKE_COOLDOWN = timedelta(minutes=15)
SWITCH_OFF_RETRY = timedelta(minutes=5)
SHED_STEP_DWELL = timedelta(seconds=60)
SHED_COOLDOWN = timedelta(minutes=30)
SHED_IDLE_CYCLES = 3
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
        self.enabled: bool = True
        self.mode: ChargeMode = ChargeMode(
            entry.data.get("default_mode", ChargeMode.SOLAR_ONLY)
        )
        self.min_amps: float = self._opt(CONF_MIN_AMPS, DEFAULT_MIN_AMPS)
        self.max_amps: float = self._opt(CONF_MAX_AMPS, DEFAULT_MAX_AMPS)
        self.max_ev_soc: float = DEFAULT_MAX_EV_SOC
        self._shed_ids: list[str] = []
        self._shed_started_at: datetime | None = None
        self._last_shed_at: datetime | None = None
        self._shed_cooldown_until: datetime | None = None
        self._zero_ev_cycles = 0
        self._shed_store_loaded = False
        self._defer_shed = False
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

    def _ev_at_valley_target(self) -> bool:
        """True when EV SOC sensor configured and SOC has hit the valley cap.

        Returns False (no cap) if no EV SOC sensor is configured or its
        value is unavailable, so behavior is unchanged for users who
        haven't set one up.
        """
        soc = self._get_ev_soc()
        if soc is None:
            return False
        target = float(
            self._opt(CONF_VALLEY_TARGET_SOC, DEFAULT_VALLEY_TARGET_SOC)
        )
        return soc >= target

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

    def _is_valley_time(self) -> bool:
        """Check if current time is within valley/off-peak window."""
        now = _now().time()
        start_str = self._opt(CONF_VALLEY_START, DEFAULT_VALLEY_START)
        end_str = self._opt(CONF_VALLEY_END, DEFAULT_VALLEY_END)
        start = _parse_hhmm(start_str)
        end = _parse_hhmm(end_str)

        if start <= end:
            return start <= now <= end
        # Overnight window (e.g., 23:00 - 07:00)
        return now >= start or now <= end

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
        return max(
            0.0,
            grid_max_power
            - live_import
            - discharge_w
            + charge_from_grid
            + self._measured_ev_watts(),
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

    def _calculate_solar_only(self, available_power: float) -> float:
        """Solar Only: charge from surplus only."""
        voltage = self._get_voltage()
        if voltage <= 0:
            return 0
        return available_power / voltage

    def _calculate_max_grid(self) -> float:
        """Max Power: charge at max without exceeding grid capacity."""
        return min(self.max_amps, self._get_grid_available_amps())

    def _calculate_valley(self, available_power: float) -> float:
        """Valley: charge at configured amps during off-peak, solar otherwise."""
        if self._is_valley_time():
            if self._ev_at_valley_target():
                return 0
            valley_amps = float(self._opt(CONF_VALLEY_AMPS, DEFAULT_VALLEY_AMPS))
            return min(valley_amps, self._get_grid_available_amps())
        return self._calculate_solar_only(available_power)

    def _calculate_min_topup(self, available_power: float) -> float:
        """Min Solar + Grid Top-up: solar surplus with guaranteed minimum."""
        solar_amps = self._calculate_solar_only(available_power)
        guaranteed = float(
            self._opt(CONF_GUARANTEED_MIN_AMPS, DEFAULT_GUARANTEED_MIN_AMPS)
        )
        grid_cap = self._get_grid_available_amps()
        return min(max(solar_amps, guaranteed), grid_cap)

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

    async def _async_save_shed(self) -> None:
        started = (
            self._shed_started_at.isoformat() if self._shed_started_at else None
        )
        await self._shed_store.async_save(
            {"entity_ids": list(self._shed_ids), "started_at": started}
        )

    async def _async_restore_persisted_shed_once(self) -> None:
        """Turn shed loads back on after a restart so a fridge is not left off."""
        if self._shed_store_loaded:
            return
        self._shed_store_loaded = True
        stored = await self._shed_store.async_load()
        if not stored:
            return
        entity_ids = [str(item) for item in stored.get("entity_ids") or [] if item]
        if not entity_ids:
            return
        for entity_id in entity_ids:
            await self._async_set_load(entity_id, True)
        self._defer_shed = True
        await self._shed_store.async_save({"entity_ids": [], "started_at": None})

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

        if (
            not self.enabled
            or not self._car_can_charge()
            or self.mode != ChargeMode.MAX_SOLAR_GRID
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
        self, target: float, available: float, *, enabled: bool
    ) -> dict[str, Any]:
        return {
            "target_amps": round(target),
            "available_power": round(available, 1),
            "mode": self.mode,
            "enabled": enabled,
            "ev_power": round(self._measured_ev_watts(), 1),
            "shed_loads": list(self._shed_ids),
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Run the regulation loop."""
        await self._async_restore_persisted_shed_once()
        try:
            return await self._async_regulate()
        finally:
            self._defer_shed = False

    async def _async_regulate(self) -> dict[str, Any]:
        """One regulation cycle. Caller clears the restart shed deferral."""
        if not self.enabled:
            self._last_target = 0
            await self._async_set_charger_amps(0)
            await self._async_restore_shed(cooldown=False)
            return self._status(0, 0, enabled=False)

        if not self._car_can_charge():
            await self._async_stop_unchargeable()
            await self._async_restore_shed(cooldown=False)
            return self._status(0, 0, enabled=True)
        self._charger_off_sent_at = None

        voltage = self._get_voltage()
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
                return self._status(self._last_target, 0, enabled=True)
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
                return self._status(self._last_target, 0, enabled=True)
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
        grid_capacity = self._grid_available_power()

        # Battery-priority gate: in pure solar modes, hold off charging
        # until the home battery has reached its minimum SOC. Grid-backed
        # modes (Max Power, Valley) are unaffected because the user
        # explicitly opted into pulling from the grid.
        battery_blocks = self._battery_blocks_solar_charge()

        # Mode dispatch
        if self.mode == ChargeMode.SOLAR_ONLY:
            if battery_blocks:
                target = 0
                available_power = 0
            else:
                target = self._calculate_solar_only(solar_surplus)
                available_power = solar_surplus
        elif self.mode == ChargeMode.MAX_SOLAR_GRID:
            target = self._calculate_max_grid()
            available_power = grid_capacity
        elif self.mode == ChargeMode.VALLEY:
            target = self._calculate_valley(solar_surplus)
            available_power = grid_capacity if self._is_valley_time() else solar_surplus
        elif self.mode == ChargeMode.MIN_SOLAR_TOPUP:
            if battery_blocks:
                target = 0
                available_power = 0
            else:
                target = self._calculate_min_topup(solar_surplus)
                guaranteed_power = float(
                    self._opt(CONF_GUARANTEED_MIN_AMPS, DEFAULT_GUARANTEED_MIN_AMPS)
                ) * voltage
                available_power = max(solar_surplus, guaranteed_power)
        else:
            target = 0
            available_power = 0

        # Clamp to min/max
        target = max(0, min(target, self.max_amps))

        # Apply step limiting only for solar-based modes (prevents oscillation).
        # Skip when starting from zero (avoids min_amps deadlock) and when
        # stopping (allows immediate stop instead of 5-min ramp-down while
        # importing from the grid).
        if self.mode in (ChargeMode.SOLAR_ONLY, ChargeMode.MIN_SOLAR_TOPUP):
            if self._last_target > 0 and target > 0:
                target = self._apply_step_limit(target)

        # Below minimum → stop charging
        if target < self.min_amps:
            target = 0

        self._last_target = target

        await self._async_set_charger_amps(target)
        await self._async_sync_shed(target)

        return self._status(target, available_power, enabled=True)
