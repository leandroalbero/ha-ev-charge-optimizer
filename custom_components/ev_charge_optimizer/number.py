"""Number platform for EV Charge Optimizer."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_MAX_AMPS, DEFAULT_MIN_AMPS, DOMAIN
from .coordinator import EVChargeOptimizerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities."""
    coordinator: EVChargeOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            MinAmpsNumber(coordinator, entry),
            MaxAmpsNumber(coordinator, entry),
        ]
    )


class MinAmpsNumber(
    CoordinatorEntity[EVChargeOptimizerCoordinator], NumberEntity
):
    """Number entity for minimum amps threshold."""

    _attr_has_entity_name = True
    _attr_name = "Minimum Amps"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = 48
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: EVChargeOptimizerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_min_amps"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EV Charge Optimizer",
            "manufacturer": "EV Charge Optimizer",
        }

    @property
    def native_value(self) -> float:
        """Return current min amps."""
        return self.coordinator.min_amps

    async def async_set_native_value(self, value: float) -> None:
        """Update min amps."""
        self.coordinator.min_amps = value
        await self.coordinator.async_request_refresh()


class MaxAmpsNumber(
    CoordinatorEntity[EVChargeOptimizerCoordinator], NumberEntity
):
    """Number entity for maximum amps cap."""

    _attr_has_entity_name = True
    _attr_name = "Maximum Amps"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = 48
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: EVChargeOptimizerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_max_amps"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EV Charge Optimizer",
            "manufacturer": "EV Charge Optimizer",
        }

    @property
    def native_value(self) -> float:
        """Return current max amps."""
        return self.coordinator.max_amps

    async def async_set_native_value(self, value: float) -> None:
        """Update max amps."""
        self.coordinator.max_amps = value
        await self.coordinator.async_request_refresh()
