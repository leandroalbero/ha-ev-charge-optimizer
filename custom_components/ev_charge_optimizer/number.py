"""Number platform for EV Charge Optimizer."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_MAX_EV_SOC, DOMAIN
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
            MaxEvSocNumber(coordinator, entry),
            SolarFloorAmpsNumber(coordinator, entry),
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


class MaxEvSocNumber(
    CoordinatorEntity[EVChargeOptimizerCoordinator], RestoreNumber
):
    """Slider cap below the car's own charge limit. Tesla will not go under 50%."""

    _attr_has_entity_name = True
    _attr_name = "Maximum EV Charge"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: EVChargeOptimizerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_max_ev_soc"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EV Charge Optimizer",
            "manufacturer": "EV Charge Optimizer",
        }

    async def async_added_to_hass(self) -> None:
        """Restore the slider so a restart keeps the user's cap."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self.coordinator.max_ev_soc = float(last.native_value)
        else:
            self.coordinator.max_ev_soc = DEFAULT_MAX_EV_SOC

    @property
    def native_value(self) -> float:
        """Return the SOC cap."""
        return self.coordinator.max_ev_soc

    async def async_set_native_value(self, value: float) -> None:
        """Update the SOC cap."""
        self.coordinator.max_ev_soc = value
        await self.coordinator.async_request_refresh()


class SolarFloorAmpsNumber(
    CoordinatorEntity[EVChargeOptimizerCoordinator], RestoreNumber
):
    _attr_has_entity_name = True
    _attr_name = "Solar Floor Amps"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 16
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: EVChargeOptimizerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_solar_floor_amps"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EV Charge Optimizer",
            "manufacturer": "EV Charge Optimizer",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self.coordinator.solar_floor_amps = float(last.native_value)
        else:
            self.coordinator.solar_floor_amps = 0

    @property
    def native_value(self) -> float:
        return self.coordinator.solar_floor_amps

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.solar_floor_amps = value
        await self.coordinator.async_request_refresh()
