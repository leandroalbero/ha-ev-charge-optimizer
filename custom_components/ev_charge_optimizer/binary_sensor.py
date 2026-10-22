"""Binary sensor platform for EV Charge Optimizer."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ChargeMode
from .coordinator import EVChargeOptimizerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    coordinator: EVChargeOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BatteryAssistBinarySensor(coordinator, entry)])


class BatteryAssistBinarySensor(
    CoordinatorEntity[EVChargeOptimizerCoordinator], BinarySensorEntity
):
    """On while Max Power or Boost may draw the home battery."""

    _attr_has_entity_name = True
    _attr_name = "Battery Assist"

    def __init__(
        self,
        coordinator: EVChargeOptimizerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_battery_assist"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EV Charge Optimizer",
            "manufacturer": "EV Charge Optimizer",
        }

    @property
    def is_on(self) -> bool:
        return self.coordinator.mode in (
            ChargeMode.MAX_POWER,
            ChargeMode.BOOST,
        ) and self.coordinator._car_can_charge()
