"""Constants for the EV Charge Optimizer integration."""

from enum import StrEnum

DOMAIN = "ev_charge_optimizer"

# Config keys - sensors
CONF_GRID_EXPORT_SENSOR = "grid_export_sensor"
CONF_SOLAR_PRODUCTION_SENSOR = "solar_production_sensor"
CONF_HOUSE_CONSUMPTION_SENSOR = "house_consumption_sensor"
CONF_VOLTAGE_SENSOR = "voltage_sensor"

# Config keys - charger
CONF_CHARGER_NUMBER_ENTITY = "charger_number_entity"
CONF_CHARGER_SWITCH_ENTITY = "charger_switch_entity"

# Config keys - settings
CONF_STATIC_VOLTAGE = "static_voltage"
CONF_MIN_AMPS = "min_amps"
CONF_MAX_AMPS = "max_amps"
CONF_DEFAULT_MODE = "default_mode"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_POWER_BUFFER = "power_buffer"
CONF_ROLLING_WINDOW = "rolling_window"
CONF_MAX_STEP = "max_step"
CONF_VALLEY_START = "valley_start"
CONF_VALLEY_END = "valley_end"
CONF_VALLEY_AMPS = "valley_amps"
CONF_GUARANTEED_MIN_AMPS = "guaranteed_min_amps"
CONF_GRID_MAX_POWER = "grid_max_power"

# Defaults
DEFAULT_MIN_AMPS = 6
DEFAULT_MAX_AMPS = 32
DEFAULT_GRID_MAX_POWER = 9600
DEFAULT_STATIC_VOLTAGE = 240
DEFAULT_UPDATE_INTERVAL = 30
DEFAULT_POWER_BUFFER = 100
DEFAULT_ROLLING_WINDOW = 5
DEFAULT_MAX_STEP = 2
DEFAULT_VALLEY_AMPS = 16
DEFAULT_GUARANTEED_MIN_AMPS = 8
DEFAULT_VALLEY_START = "23:00"
DEFAULT_VALLEY_END = "07:00"


class ChargeMode(StrEnum):
    """Charging modes."""

    SOLAR_ONLY = "solar_only"
    MAX_SOLAR_GRID = "max_solar_grid"
    VALLEY = "valley"
    MIN_SOLAR_TOPUP = "min_solar_topup"


MODE_LABELS = {
    ChargeMode.SOLAR_ONLY: "Solar Only",
    ChargeMode.MAX_SOLAR_GRID: "Max Solar + Grid",
    ChargeMode.VALLEY: "Valley / Off-Peak",
    ChargeMode.MIN_SOLAR_TOPUP: "Min Solar + Grid Top-up",
}

PLATFORMS = ["sensor", "select", "switch", "number"]
