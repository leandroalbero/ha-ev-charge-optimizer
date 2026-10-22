"""Time platform for the Ready By deadline."""

from __future__ import annotations

from datetime import datetime, time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_READY_BY, DOMAIN
from .coordinator import EVChargeOptimizerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EVChargeOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ReadyByTime(coordinator, entry)])


def _parse_time(value: str) -> time | None:
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


class ReadyByTime(
    CoordinatorEntity[EVChargeOptimizerCoordinator], TimeEntity, RestoreEntity
):
    _attr_has_entity_name = True
    _attr_name = "Ready By"

    def __init__(
        self,
        coordinator: EVChargeOptimizerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ready_by"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "EV Charge Optimizer",
            "manufacturer": "EV Charge Optimizer",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        parsed = _parse_time(last.state) if last is not None else None
        if parsed is None:
            parsed = _parse_time(DEFAULT_READY_BY)
        if parsed is not None:
            self.coordinator.ready_by = parsed

    @property
    def native_value(self) -> time | None:
        return self.coordinator.ready_by

    async def async_set_value(self, value: time) -> None:
        self.coordinator.ready_by = value
        await self.coordinator.async_request_refresh()
