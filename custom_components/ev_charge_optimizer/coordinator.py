"""Data coordinator for EV Charge Optimizer."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    BATTERY_MODES_NOT_DISCHARGING,
    CONF_BATTERY_MIN_SOC,
    CONF_BATTERY_MODE_SENSOR,
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_EV_SOC_SENSOR,
    CONF_CHARGER_NUMBER_ENTITY,
    CONF_CHARGER_SWITCH_ENTITY,
    CONF_CHARGER_WAKE_ENTITY,
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
    CONF_SOLAR_PRODUCTION_SENSOR,
    CONF_STATIC_VOLTAGE,
    CONF_UPDATE_INTERVAL,
    CONF_VALLEY_AMPS,
    CONF_VALLEY_TARGET_SOC,
    CONF_VALLEY_END,
    CONF_VALLEY_START,
    CONF_VOLTAGE_SENSOR,
    DEFAULT_BATTERY_MIN_SOC,
    DEFAULT_GRID_MAX_POWER,
    DEFAULT_GUARANTEED_MIN_AMPS,
    DEFAULT_MAX_AMPS,
    DEFAULT_MAX_STEP,
    DEFAULT_MIN_AMPS,
    DEFAULT_POWER_BUFFER,
    DEFAULT_PRIORITIZE_BATTERY,
    DEFAULT_ROLLING_WINDOW,
    DEFAULT_STATIC_VOLTAGE,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VALLEY_AMPS,
    DEFAULT_VALLEY_TARGET_SOC,
    DEFAULT_VALLEY_END,
    DEFAULT_VALLEY_START,
    DOMAIN,
    ChargeMode,
)

_LOGGER = logging.getLogger(__name__)


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
        self.enabled: bool = True
        self.mode: ChargeMode = ChargeMode(
            entry.data.get("default_mode", ChargeMode.SOLAR_ONLY)
        )
        self.min_amps: float = self._opt(CONF_MIN_AMPS, DEFAULT_MIN_AMPS)
        self.max_amps: float = self._opt(CONF_MAX_AMPS, DEFAULT_MAX_AMPS)

        interval = int(self._opt(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
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
        """Get current voltage from sensor or static config."""
        voltage_entity = self._entry.data.get(CONF_VOLTAGE_SENSOR)
        if voltage_entity:
            val = self._get_sensor_value(voltage_entity)
            if val and val > 0:
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

    def _get_battery_charge_demand(self) -> float:
        """Battery power being absorbed (W); 0 if discharging or unconfigured.

        Sign convention: positive = charging (sink), negative = discharging
        (source). Only the charging draw competes with the EV for solar, so
        we clamp to zero when the battery is exporting to the house.
        """
        val = self._get_sensor_value(
            self._entry.data.get(CONF_BATTERY_POWER_SENSOR)
        )
        if val is None:
            return 0
        return max(0, val)

    def _get_ev_soc(self) -> float | None:
        """Read EV state-of-charge percentage if sensor configured."""
        return self._get_sensor_value(
            self._entry.data.get(CONF_EV_SOC_SENSOR)
        )

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

    def _battery_is_discharging(self) -> bool:
        """True only when the home battery is actively sourcing power.

        The grid_export reading silently includes battery discharge, so
        the EV optimizer must not derive headroom from it unless the
        battery is committed to keep discharging. Inverter mode is the
        authoritative signal; signed battery power is the fallback when
        no mode sensor is configured.
        """
        mode = self._get_sensor_state(
            self._entry.data.get(CONF_BATTERY_MODE_SENSOR)
        )
        if mode is not None:
            return mode not in BATTERY_MODES_NOT_DISCHARGING
        # Sign convention: positive = charging, negative = discharging.
        power = self._get_sensor_value(
            self._entry.data.get(CONF_BATTERY_POWER_SENSOR)
        )
        if power is not None:
            return power < -10
        return False

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
        now = datetime.now().time()
        start_str = self._opt(CONF_VALLEY_START, DEFAULT_VALLEY_START)
        end_str = self._opt(CONF_VALLEY_END, DEFAULT_VALLEY_END)
        start = _parse_hhmm(start_str)
        end = _parse_hhmm(end_str)

        if start <= end:
            return start <= now <= end
        # Overnight window (e.g., 23:00 - 07:00)
        return now >= start or now <= end

    def _get_grid_available_amps(self) -> float:
        """Calculate max amps available from grid without overloading.

        Two views of the non-EV load:
          * grid_export-based: credits whatever the battery is currently
            sourcing. Only valid while the battery is committed to
            discharge — otherwise the credit is phantom and will vanish
            (depleted SOC, inverter flipping to charge) leaving the EV
            ramped past the fuse cap.
          * house_consumption-based: battery-independent. Always safe.

        Takes the worse case so phantom battery credit cannot overstate
        headroom, while still subtracting any active battery-charge draw
        from the grid headroom.
        """
        voltage = self._get_voltage()
        if voltage <= 0:
            return 0
        grid_max_power = float(
            self._opt(CONF_GRID_MAX_POWER, DEFAULT_GRID_MAX_POWER)
        )
        charger_power = self._get_actual_charger_amps() * voltage

        house_consumption = self._get_sensor_value(
            self._entry.data.get(CONF_HOUSE_CONSUMPTION_SENSOR)
        )
        grid_export = self._get_sensor_value(
            self._entry.data.get(CONF_GRID_EXPORT_SENSOR)
        )

        # Battery-independent view of the non-EV load.
        if house_consumption is not None:
            non_ev_household = max(0, house_consumption - charger_power)
        else:
            non_ev_household = None

        # Grid-meter view, only trusted when battery is committed to
        # keep discharging.
        if grid_export is not None and self._battery_is_discharging():
            current_import = max(0, -grid_export)
            non_ev_grid = max(0, current_import - charger_power)
        else:
            non_ev_grid = None

        # Pick the worse case so we never overestimate headroom.
        candidates = [
            c for c in (non_ev_household, non_ev_grid) if c is not None
        ]
        if candidates:
            non_ev_load = max(candidates)
        elif grid_export is not None:
            # No consumption sensor and battery not discharging: use raw
            # import minus charger as a coarse proxy (still safe — does
            # not credit phantom battery contribution).
            current_import = max(0, -grid_export)
            non_ev_load = max(0, current_import - charger_power)
        else:
            non_ev_load = 0

        available_watts = grid_max_power - non_ev_load
        return max(0, available_watts / voltage)

    def _calculate_solar_only(self, available_power: float) -> float:
        """Solar Only: charge from surplus only."""
        voltage = self._get_voltage()
        if voltage <= 0:
            return 0
        return available_power / voltage

    def _calculate_max_grid(self) -> float:
        """Max Solar + Grid: charge at max without exceeding grid capacity."""
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

    async def _async_update_data(self) -> dict[str, Any]:
        """Run the regulation loop."""
        if not self.enabled:
            self._last_target = 0
            await self._async_set_charger_amps(0)
            return {
                "target_amps": 0,
                "available_power": 0,
                "mode": self.mode,
                "enabled": False,
            }

        voltage = self._get_voltage()
        power_buffer = float(self._opt(CONF_POWER_BUFFER, DEFAULT_POWER_BUFFER))

        # Use actual charger draw (from entity state), not the theoretical
        # target.  If the charger didn't accept the command (BLE glitch,
        # car asleep, unplugged …) the real draw is 0 and we must not
        # pretend otherwise — that inflates the surplus with phantom watts.
        actual_charger_amps = self._get_actual_charger_amps()
        charger_power = actual_charger_amps * voltage

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
                return {
                    "target_amps": self._last_target,
                    "available_power": 0,
                    "mode": self.mode,
                    "enabled": True,
                }
            if grid_export > 0:
                self._readings.append(grid_export + charger_power)
            else:
                self._readings.append(0)
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
                return {
                    "target_amps": self._last_target,
                    "available_power": 0,
                    "mode": self.mode,
                    "enabled": True,
                }
            house_consumption = self._get_sensor_value(
                self._entry.data.get(CONF_HOUSE_CONSUMPTION_SENSOR)
            ) or 0
            household_only = max(0, house_consumption - charger_power)
            # Reserve whatever the home battery is currently absorbing —
            # otherwise the EV would steal solar that the inverter is
            # routing to the battery (e.g. while the MPC controller is
            # charging it).
            battery_demand = self._get_battery_charge_demand()
            self._readings.append(
                solar_production - household_only - battery_demand
            )

        solar_surplus = self._rolling_average() - power_buffer
        grid_capacity = self._get_grid_available_amps() * voltage

        # Battery-priority gate: in pure solar modes, hold off charging
        # until the home battery has reached its minimum SOC. Grid-backed
        # modes (Max Solar+Grid, Valley) are unaffected because the user
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

        return {
            "target_amps": round(target),
            "available_power": round(available_power, 1),
            "mode": self.mode,
            "enabled": True,
        }
