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
    CONF_CHARGER_NUMBER_ENTITY,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_MAX_POWER,
    CONF_GUARANTEED_MIN_AMPS,
    CONF_HOUSE_CONSUMPTION_SENSOR,
    CONF_MAX_AMPS,
    CONF_MAX_STEP,
    CONF_MIN_AMPS,
    CONF_POWER_BUFFER,
    CONF_ROLLING_WINDOW,
    CONF_STATIC_VOLTAGE,
    CONF_UPDATE_INTERVAL,
    CONF_VALLEY_AMPS,
    CONF_VALLEY_END,
    CONF_VALLEY_START,
    CONF_VOLTAGE_SENSOR,
    DEFAULT_GRID_MAX_POWER,
    DEFAULT_GUARANTEED_MIN_AMPS,
    DEFAULT_MAX_AMPS,
    DEFAULT_MAX_STEP,
    DEFAULT_MIN_AMPS,
    DEFAULT_POWER_BUFFER,
    DEFAULT_ROLLING_WINDOW,
    DEFAULT_STATIC_VOLTAGE,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VALLEY_AMPS,
    DEFAULT_VALLEY_END,
    DEFAULT_VALLEY_START,
    DOMAIN,
    ChargeMode,
)

_LOGGER = logging.getLogger(__name__)


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
        self._last_sent_amps: int = -1
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

    def _is_valley_time(self) -> bool:
        """Check if current time is within valley/off-peak window."""
        now = datetime.now().time()
        start_str = self._opt(CONF_VALLEY_START, DEFAULT_VALLEY_START)
        end_str = self._opt(CONF_VALLEY_END, DEFAULT_VALLEY_END)
        start = datetime.strptime(start_str, "%H:%M").time()
        end = datetime.strptime(end_str, "%H:%M").time()

        if start <= end:
            return start <= now <= end
        # Overnight window (e.g., 23:00 - 07:00)
        return now >= start or now <= end

    def _get_grid_available_amps(self) -> float:
        """Calculate max amps available from grid without overloading.

        House consumption includes charger draw, so we subtract the
        current charger target to get the non-charger household load.
        Otherwise the formula creates a feedback loop that keeps
        reducing the target.
        """
        voltage = self._get_voltage()
        if voltage <= 0:
            return 0
        grid_max_power = float(
            self._opt(CONF_GRID_MAX_POWER, DEFAULT_GRID_MAX_POWER)
        )
        house_consumption = self._get_sensor_value(
            self._entry.data.get(CONF_HOUSE_CONSUMPTION_SENSOR)
        ) or 0
        charger_power = self._last_target * voltage
        household_only = house_consumption - charger_power
        available_watts = grid_max_power - household_only
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

    async def _async_set_charger_amps(self, amps: float) -> None:
        """Write target amps to the charger number entity, only if changed."""
        entity_id = self._entry.data.get(CONF_CHARGER_NUMBER_ENTITY)
        if not entity_id:
            return

        rounded = round(amps)
        if rounded == self._last_sent_amps:
            return

        self._last_sent_amps = rounded
        domain = entity_id.split(".")[0]

        await self.hass.services.async_call(
            domain,
            "set_value",
            {"entity_id": entity_id, "value": rounded},
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Run the regulation loop."""
        if not self.enabled:
            return {
                "target_amps": self._last_target,
                "available_power": 0,
                "mode": self.mode,
                "enabled": False,
            }

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

        self._readings.append(grid_export)

        power_buffer = float(self._opt(CONF_POWER_BUFFER, DEFAULT_POWER_BUFFER))
        solar_surplus = self._rolling_average() - power_buffer
        voltage = self._get_voltage()
        grid_capacity = self._get_grid_available_amps() * voltage

        # Mode dispatch
        if self.mode == ChargeMode.SOLAR_ONLY:
            target = self._calculate_solar_only(solar_surplus)
            available_power = solar_surplus
        elif self.mode == ChargeMode.MAX_SOLAR_GRID:
            target = self._calculate_max_grid()
            available_power = grid_capacity
        elif self.mode == ChargeMode.VALLEY:
            target = self._calculate_valley(solar_surplus)
            available_power = grid_capacity if self._is_valley_time() else solar_surplus
        elif self.mode == ChargeMode.MIN_SOLAR_TOPUP:
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

        # Apply step limiting only for solar-based modes (prevents oscillation)
        if self.mode in (ChargeMode.SOLAR_ONLY, ChargeMode.MIN_SOLAR_TOPUP):
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
