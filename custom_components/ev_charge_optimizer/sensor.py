"""Sensor platform for EV Charge Optimizer."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EVChargeOptimizerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator: EVChargeOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            TargetAmpsSensor(coordinator, entry),
            AvailablePowerSensor(coordinator, entry),
            EVPowerSensor(coordinator, entry),
            ShedLoadsSensor(coordinator, entry),
        ]
    )


class TargetAmpsSensor(CoordinatorEntity[EVChargeOptimizerCoordinator], SensorEntity):
    """Sensor showing current target amps."""

    _attr_has_entity_name = True
    _attr_name = "Target Amps"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: EVChargeOptimizerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_target_amps"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EV Charge Optimizer",
            "manufacturer": "EV Charge Optimizer",
        }

    @property
    def native_value(self) -> float | None:
        """Return current target amps."""
        if self.coordinator.data:
            return self.coordinator.data.get("target_amps", 0)
        return 0


class AvailablePowerSensor(
    CoordinatorEntity[EVChargeOptimizerCoordinator], SensorEntity
):
    """Sensor showing available power for charging."""

    _attr_has_entity_name = True
    _attr_name = "Available Power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: EVChargeOptimizerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_available_power"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EV Charge Optimizer",
            "manufacturer": "EV Charge Optimizer",
        }

    @property
    def native_value(self) -> float | None:
        """Return available power."""
        if self.coordinator.data:
            return self.coordinator.data.get("available_power", 0)
        return 0


class EVPowerSensor(CoordinatorEntity[EVChargeOptimizerCoordinator], SensorEntity):
    """Measured EV draw, for other controllers such as the battery MPC."""

    _attr_has_entity_name = True
    _attr_name = "EV Power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: EVChargeOptimizerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ev_power"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EV Charge Optimizer",
            "manufacturer": "EV Charge Optimizer",
        }

    @property
    def native_value(self) -> float | None:
        """Return measured EV watts."""
        if self.coordinator.data:
            return self.coordinator.data.get("ev_power", 0)
        return 0


class ShedLoadsSensor(CoordinatorEntity[EVChargeOptimizerCoordinator], SensorEntity):
    """How many loads the optimizer has currently shed."""

    _attr_has_entity_name = True
    _attr_name = "Shed Loads"

    def __init__(
        self,
        coordinator: EVChargeOptimizerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_shed_loads"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EV Charge Optimizer",
            "manufacturer": "EV Charge Optimizer",
        }

    @property
    def native_value(self) -> int:
        """Return the count of loads this optimizer turned off."""
        if self.coordinator.data:
            return len(self.coordinator.data.get("shed_loads", []))
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        """List the entity ids currently shed."""
        loads: list[str] = []
        if self.coordinator.data:
            loads = list(self.coordinator.data.get("shed_loads", []))
        return {"shed_loads": loads}
