"""Switch platform for EV Charge Optimizer."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
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
    """Set up switch entities."""
    coordinator: EVChargeOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OptimizerSwitch(coordinator, entry)])


class OptimizerSwitch(
    CoordinatorEntity[EVChargeOptimizerCoordinator], SwitchEntity
):
    """Switch to enable/disable the optimizer."""

    _attr_has_entity_name = True
    _attr_name = "Enabled"

    def __init__(
        self,
        coordinator: EVChargeOptimizerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_enabled"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EV Charge Optimizer",
            "manufacturer": "EV Charge Optimizer",
        }

    @property
    def is_on(self) -> bool:
        """Return true if optimizer is enabled."""
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs) -> None:
        """Enable the optimizer."""
        self.coordinator.enabled = True
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Disable the optimizer."""
        self.coordinator.enabled = False
        await self.coordinator.async_request_refresh()
