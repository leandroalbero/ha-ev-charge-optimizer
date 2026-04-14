"""Config flow for EV Charge Optimizer."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TimeSelector,
)

from .const import (
    CONF_CHARGER_NUMBER_ENTITY,
    CONF_CHARGER_SWITCH_ENTITY,
    CONF_DEFAULT_MODE,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_MAX_POWER,
    CONF_GUARANTEED_MIN_AMPS,
    CONF_HOUSE_CONSUMPTION_SENSOR,
    CONF_MAX_AMPS,
    CONF_MAX_STEP,
    CONF_MIN_AMPS,
    CONF_POWER_BUFFER,
    CONF_ROLLING_WINDOW,
    CONF_SOLAR_PRODUCTION_SENSOR,
    CONF_STATIC_VOLTAGE,
    CONF_UPDATE_INTERVAL,
    CONF_VALLEY_AMPS,
    CONF_VALLEY_END,
    CONF_VALLEY_START,
    CONF_VOLTAGE_SENSOR,
    DEFAULT_GRID_MAX_POWER,
    DEFAULT_GUARANTEED_MIN_AMPS,
    DEFAULT_MAX_AMPS,
    DEFAULT_MAX_STEP,
    DEFAULT_MIN_AMPS,
    DEFAULT_POWER_BUFFER,
    DEFAULT_ROLLING_WINDOW,
    DEFAULT_STATIC_VOLTAGE,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VALLEY_AMPS,
    DEFAULT_VALLEY_END,
    DEFAULT_VALLEY_START,
    DOMAIN,
    ChargeMode,
    MODE_LABELS,
)

CONF_DEFAULT_MODE = "default_mode"


class EVChargeOptimizerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Step 1: Select sensor entities."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_charger()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GRID_EXPORT_SENSOR): EntitySelector(
                        EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(CONF_SOLAR_PRODUCTION_SENSOR): EntitySelector(
                        EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(CONF_HOUSE_CONSUMPTION_SENSOR): EntitySelector(
                        EntitySelectorConfig(domain="sensor")
                    ),
                }
            ),
        )

    async def async_step_charger(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Step 2: Select charger control entity."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_voltage()

        return self.async_show_form(
            step_id="charger",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CHARGER_NUMBER_ENTITY): EntitySelector(
                        EntitySelectorConfig(domain="number")
                    ),
                    vol.Optional(CONF_CHARGER_SWITCH_ENTITY): EntitySelector(
                        EntitySelectorConfig(domain="switch")
                    ),
                }
            ),
        )

    async def async_step_voltage(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Step 3: Voltage configuration."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_basics()

        return self.async_show_form(
            step_id="voltage",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_VOLTAGE_SENSOR): EntitySelector(
                        EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Optional(
                        CONF_STATIC_VOLTAGE, default=DEFAULT_STATIC_VOLTAGE
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=100, max=400, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                }
            ),
        )

    async def async_step_basics(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Step 4: Basic settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            min_a = user_input.get(CONF_MIN_AMPS, DEFAULT_MIN_AMPS)
            max_a = user_input.get(CONF_MAX_AMPS, DEFAULT_MAX_AMPS)
            if min_a >= max_a:
                errors["base"] = "min_amps_exceeds_max"
            else:
                self._data.update(user_input)
                return self.async_create_entry(
                    title="EV Charge Optimizer", data=self._data
                )

        return self.async_show_form(
            step_id="basics",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_MIN_AMPS, default=DEFAULT_MIN_AMPS
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=48, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_MAX_AMPS, default=DEFAULT_MAX_AMPS
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=48, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_GRID_MAX_POWER, default=DEFAULT_GRID_MAX_POWER
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1000,
                            max=50000,
                            step=100,
                            unit_of_measurement="W",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_DEFAULT_MODE,
                        default=ChargeMode.SOLAR_ONLY,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": mode.value, "label": label}
                                for mode, label in MODE_LABELS.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Allow reconfiguration of entities and basic settings."""
        reconfigure_entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            min_a = user_input.get(CONF_MIN_AMPS, DEFAULT_MIN_AMPS)
            max_a = user_input.get(CONF_MAX_AMPS, DEFAULT_MAX_AMPS)
            if min_a >= max_a:
                errors["base"] = "min_amps_exceeds_max"
            else:
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data_updates=user_input,
                )

        current = reconfigure_entry.data

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GRID_EXPORT_SENSOR,
                        default=current.get(CONF_GRID_EXPORT_SENSOR),
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Required(
                        CONF_SOLAR_PRODUCTION_SENSOR,
                        default=current.get(CONF_SOLAR_PRODUCTION_SENSOR),
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Required(
                        CONF_HOUSE_CONSUMPTION_SENSOR,
                        default=current.get(CONF_HOUSE_CONSUMPTION_SENSOR),
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Required(
                        CONF_CHARGER_NUMBER_ENTITY,
                        default=current.get(CONF_CHARGER_NUMBER_ENTITY),
                    ): EntitySelector(EntitySelectorConfig(domain="number")),
                    vol.Optional(
                        CONF_CHARGER_SWITCH_ENTITY,
                        description={
                            "suggested_value": current.get(
                                CONF_CHARGER_SWITCH_ENTITY
                            )
                        },
                    ): EntitySelector(EntitySelectorConfig(domain="switch")),
                    vol.Optional(
                        CONF_VOLTAGE_SENSOR,
                        description={
                            "suggested_value": current.get(CONF_VOLTAGE_SENSOR)
                        },
                    ): EntitySelector(EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_STATIC_VOLTAGE,
                        default=current.get(
                            CONF_STATIC_VOLTAGE, DEFAULT_STATIC_VOLTAGE
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=100, max=400, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_MIN_AMPS,
                        default=current.get(CONF_MIN_AMPS, DEFAULT_MIN_AMPS),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=48, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_MAX_AMPS,
                        default=current.get(CONF_MAX_AMPS, DEFAULT_MAX_AMPS),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=48, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_GRID_MAX_POWER,
                        default=current.get(
                            CONF_GRID_MAX_POWER, DEFAULT_GRID_MAX_POWER
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1000,
                            max=50000,
                            step=100,
                            unit_of_measurement="W",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_DEFAULT_MODE,
                        default=current.get(
                            CONF_DEFAULT_MODE, ChargeMode.SOLAR_ONLY
                        ),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": mode.value, "label": label}
                                for mode, label in MODE_LABELS.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow."""
        return EVChargeOptimizerOptionsFlow(config_entry)


class EVChargeOptimizerOptionsFlow(OptionsFlow):
    """Handle options flow for tuning parameters."""

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Manage all options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self._entry.data, **self._entry.options}

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_UPDATE_INTERVAL,
                        default=current.get(
                            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=10,
                            max=300,
                            step=5,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Optional(
                        CONF_POWER_BUFFER,
                        default=current.get(
                            CONF_POWER_BUFFER, DEFAULT_POWER_BUFFER
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=500,
                            step=10,
                            unit_of_measurement="W",
                            mode=NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Optional(
                        CONF_ROLLING_WINDOW,
                        default=current.get(
                            CONF_ROLLING_WINDOW, DEFAULT_ROLLING_WINDOW
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=20, step=1, mode=NumberSelectorMode.SLIDER
                        )
                    ),
                    vol.Optional(
                        CONF_MAX_STEP,
                        default=current.get(CONF_MAX_STEP, DEFAULT_MAX_STEP),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=10,
                            step=1,
                            unit_of_measurement="A",
                            mode=NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Optional(
                        CONF_VALLEY_START,
                        default=current.get(
                            CONF_VALLEY_START, DEFAULT_VALLEY_START
                        ),
                    ): TimeSelector(),
                    vol.Optional(
                        CONF_VALLEY_END,
                        default=current.get(
                            CONF_VALLEY_END, DEFAULT_VALLEY_END
                        ),
                    ): TimeSelector(),
                    vol.Optional(
                        CONF_VALLEY_AMPS,
                        default=current.get(
                            CONF_VALLEY_AMPS, DEFAULT_VALLEY_AMPS
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=48,
                            step=1,
                            unit_of_measurement="A",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_GUARANTEED_MIN_AMPS,
                        default=current.get(
                            CONF_GUARANTEED_MIN_AMPS, DEFAULT_GUARANTEED_MIN_AMPS
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=48,
                            step=1,
                            unit_of_measurement="A",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_GRID_MAX_POWER,
                        default=current.get(
                            CONF_GRID_MAX_POWER, DEFAULT_GRID_MAX_POWER
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1000,
                            max=50000,
                            step=100,
                            unit_of_measurement="W",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )
