"""Options flow accepts the optional car sensors empty or filled."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_charge_optimizer.const import (
    CONF_CHARGER_NUMBER_ENTITY,
    CONF_EV_CHARGE_LIMIT_ENTITY,
    CONF_EV_CHARGING_STATE_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_HOUSE_CONSUMPTION_SENSOR,
    CONF_SOLAR_PRODUCTION_SENSOR,
    DOMAIN,
)
from custom_components.ev_charge_optimizer.coordinator import (
    EVChargeOptimizerCoordinator,
)


def _marker(schema: vol.Schema, key: str) -> vol.Optional:
    for marker in schema.schema:
        if getattr(marker, "schema", None) == key:
            return marker
    raise AssertionError(key)


async def test_user_step_offers_optional_car_sensors(
    hass: HomeAssistant,
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "form"
    keys = [getattr(marker, "schema", None) for marker in result["data_schema"].schema]
    assert CONF_EV_CHARGING_STATE_SENSOR in keys
    assert CONF_EV_CHARGE_LIMIT_ENTITY in keys
    for key in (CONF_EV_CHARGING_STATE_SENSOR, CONF_EV_CHARGE_LIMIT_ENTITY):
        assert _marker(result["data_schema"], key).default is vol.UNDEFINED


async def test_options_form_submits_empty_and_filled(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_GRID_EXPORT_SENSOR: "sensor.grid_export",
            CONF_SOLAR_PRODUCTION_SENSOR: "sensor.pv_power",
            CONF_HOUSE_CONSUMPTION_SENSOR: "sensor.house_consumption",
            CONF_CHARGER_NUMBER_ENTITY: "number.charger_amps",
        },
    )
    entry.add_to_hass(hass)

    empty = await hass.config_entries.options.async_init(entry.entry_id)
    assert empty["type"] == "form"
    for key in (CONF_EV_CHARGING_STATE_SENSOR, CONF_EV_CHARGE_LIMIT_ENTITY):
        marker = _marker(empty["data_schema"], key)
        assert marker.default is vol.UNDEFINED
        assert "suggested_value" in marker.description

    saved = await hass.config_entries.options.async_configure(
        empty["flow_id"], user_input={}
    )
    assert saved["type"] == "create_entry"
    assert CONF_EV_CHARGING_STATE_SENSOR not in saved["data"]
    assert CONF_EV_CHARGE_LIMIT_ENTITY not in saved["data"]

    filled_form = await hass.config_entries.options.async_init(entry.entry_id)
    filled = await hass.config_entries.options.async_configure(
        filled_form["flow_id"],
        user_input={
            CONF_EV_CHARGING_STATE_SENSOR: "sensor.ev_charging",
            CONF_EV_CHARGE_LIMIT_ENTITY: "number.ev_charge_limit",
        },
    )
    assert filled["type"] == "create_entry"
    assert filled["data"][CONF_EV_CHARGING_STATE_SENSOR] == "sensor.ev_charging"
    assert filled["data"][CONF_EV_CHARGE_LIMIT_ENTITY] == "number.ev_charge_limit"

    coordinator = EVChargeOptimizerCoordinator(hass, entry)
    assert coordinator._entity(CONF_EV_CHARGING_STATE_SENSOR) == "sensor.ev_charging"
    assert coordinator._entity(CONF_EV_CHARGE_LIMIT_ENTITY) == "number.ev_charge_limit"
