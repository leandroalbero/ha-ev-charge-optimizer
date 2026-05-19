"""EV Charge Optimizer integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_GRID_EXPORT_SENSOR,
    CONF_HOUSE_CONSUMPTION_SENSOR,
    CONF_SOLAR_PRODUCTION_SENSOR,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import EVChargeOptimizerCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EV Charge Optimizer from a config entry."""
    coordinator = EVChargeOptimizerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Drive regulation cycles from inverter sensor updates so the loop
    # reacts as fast as fresh data arrives. The coordinator's debouncer
    # collapses bursts to ≤1 refresh per 2s; the timer-based interval
    # in the coordinator remains as a max-staleness heartbeat.
    trigger_entities = [
        e
        for e in (
            entry.data.get(CONF_GRID_EXPORT_SENSOR),
            entry.data.get(CONF_SOLAR_PRODUCTION_SENSOR),
            entry.data.get(CONF_HOUSE_CONSUMPTION_SENSOR),
        )
        if e
    ]
    if trigger_entities:

        @callback
        def _on_sensor_update(_event: Event) -> None:
            hass.async_create_task(coordinator.async_request_refresh())

        entry.async_on_unload(
            async_track_state_change_event(
                hass, trigger_entities, _on_sensor_update
            )
        )

    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    ):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
