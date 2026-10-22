"""Select platform for EV Charge Optimizer."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LEGACY_MODE_LABELS, MODE_LABELS, coerce_mode
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
    CoordinatorEntity[EVChargeOptimizerCoordinator], SelectEntity, RestoreEntity
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

    async def async_added_to_hass(self) -> None:
        """Restore the last mode; falling back to the default stops a night charge."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None:
            return
        mode = self._label_to_mode.get(last.state) or LEGACY_MODE_LABELS.get(
            last.state
        )
        if mode is None:
            mode = coerce_mode(last.state)
        self.coordinator.mode = mode

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
