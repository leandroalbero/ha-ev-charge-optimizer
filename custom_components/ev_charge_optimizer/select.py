"""Select platform for EV Charge Optimizer."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ChargeMode, MODE_LABELS
from .coordinator import EVChargeOptimizerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities."""
    coordinator: EVChargeOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ChargeModeSelect(coordinator, entry)])


class ChargeModeSelect(
    CoordinatorEntity[EVChargeOptimizerCoordinator], SelectEntity
):
    """Select entity for choosing the charging mode."""

    _attr_has_entity_name = True
    _attr_name = "Charging Mode"

    def __init__(
        self,
        coordinator: EVChargeOptimizerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_mode"
        self._attr_options = [label for label in MODE_LABELS.values()]
        self._label_to_mode = {v: k for k, v in MODE_LABELS.items()}
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EV Charge Optimizer",
            "manufacturer": "EV Charge Optimizer",
        }

    @property
    def current_option(self) -> str | None:
        """Return the current mode label."""
        return MODE_LABELS.get(self.coordinator.mode)

    async def async_select_option(self, option: str) -> None:
        """Change the charging mode."""
        mode = self._label_to_mode.get(option)
        if mode:
            self.coordinator.mode = mode
            await self.coordinator.async_request_refresh()
