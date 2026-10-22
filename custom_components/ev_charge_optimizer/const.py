"""Constants for the EV Charge Optimizer integration."""

from enum import StrEnum

DOMAIN = "ev_charge_optimizer"

# Config keys - sensors
CONF_GRID_EXPORT_SENSOR = "grid_export_sensor"
CONF_SOLAR_PRODUCTION_SENSOR = "solar_production_sensor"
CONF_HOUSE_CONSUMPTION_SENSOR = "house_consumption_sensor"
CONF_VOLTAGE_SENSOR = "voltage_sensor"
CONF_BATTERY_SOC_SENSOR = "battery_soc_sensor"
CONF_BATTERY_POWER_SENSOR = "battery_power_sensor"
CONF_EV_SOC_SENSOR = "ev_soc_sensor"
CONF_EV_CHARGING_STATE_SENSOR = "ev_charging_state_sensor"
CONF_EV_CHARGE_LIMIT_ENTITY = "ev_charge_limit_entity"
CONF_EV_POWER_SENSOR = "ev_power_sensor"
CONF_BATTERY_POWER_POSITIVE_DISCHARGE = "battery_power_positive_discharge"
CONF_SHED_LOADS = "shed_loads"
CONF_SHED_MAX_MINUTES = "shed_max_minutes"

# Config keys - charger
CONF_CHARGER_NUMBER_ENTITY = "charger_number_entity"
CONF_CHARGER_SWITCH_ENTITY = "charger_switch_entity"
CONF_CHARGER_WAKE_ENTITY = "charger_wake_entity"

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
CONF_VALLEY_TARGET_SOC = "valley_target_soc"
CONF_GUARANTEED_MIN_AMPS = "guaranteed_min_amps"
CONF_GRID_MAX_POWER = "grid_max_power"
CONF_PRIORITIZE_BATTERY = "prioritize_battery"
CONF_BATTERY_MIN_SOC = "battery_min_soc"
CONF_BATTERY_MAX_DISCHARGE_W = "battery_max_discharge_w"
CONF_INVERTER_AC_MAX_W = "inverter_ac_max_w"
CONF_BATTERY_RESERVE_SOC = "battery_reserve_soc"
CONF_BOOST_FACTOR = "boost_factor"
CONF_BOOST_MAX_MINUTES = "boost_max_minutes"
CONF_BOOST_COOLDOWN_MINUTES = "boost_cooldown_minutes"
CONF_EV_BATTERY_KWH = "ev_battery_kwh"
CONF_PEAK_HOURS = "peak_hours"
CONF_WEEKENDS_VALLEY = "weekends_valley"
CONF_SOLAR_FORECAST_TODAY = "solar_forecast_today_entity"
CONF_SOLAR_FORECAST_TOMORROW = "solar_forecast_tomorrow_entity"

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
DEFAULT_VALLEY_TARGET_SOC = 100
DEFAULT_GUARANTEED_MIN_AMPS = 8
DEFAULT_PRIORITIZE_BATTERY = False
DEFAULT_BATTERY_MIN_SOC = 95
DEFAULT_BATTERY_MAX_DISCHARGE_W = 0
DEFAULT_INVERTER_AC_MAX_W = 0
DEFAULT_BATTERY_RESERVE_SOC = 10
DEFAULT_BOOST_FACTOR = 1.08
DEFAULT_BOOST_MAX_MINUTES = 50
DEFAULT_BOOST_COOLDOWN_MINUTES = 60
DEFAULT_VALLEY_START = "23:00"
DEFAULT_VALLEY_END = "07:00"
DEFAULT_BATTERY_POWER_POSITIVE_DISCHARGE = False
DEFAULT_SHED_MAX_MINUTES = 120
DEFAULT_MAX_EV_SOC = 100
DEFAULT_EV_BATTERY_KWH = 60
DEFAULT_PEAK_HOURS = "10-14,18-22"
DEFAULT_WEEKENDS_VALLEY = True
DEFAULT_READY_BY = "07:30"
CHARGE_EFFICIENCY = 0.9


class ChargeMode(StrEnum):
    """Charging modes."""

    OFF = "off"
    SOLAR = "solar"
    CHEAP = "cheap"
    READY_BY = "ready_by"
    MAX_POWER = "max_power"
    BOOST = "boost"


MODE_LABELS = {
    ChargeMode.OFF: "Off",
    ChargeMode.SOLAR: "Solar",
    ChargeMode.CHEAP: "Cheap",
    ChargeMode.READY_BY: "Ready By",
    ChargeMode.MAX_POWER: "Max Power",
    ChargeMode.BOOST: "Boost",
}

# Values and labels from the four-mode release, so a restore still maps.
LEGACY_MODE_VALUES = {
    "solar_only": ChargeMode.SOLAR,
    "min_solar_topup": ChargeMode.SOLAR,
    "valley": ChargeMode.CHEAP,
    "max_solar_grid": ChargeMode.MAX_POWER,
}

LEGACY_MODE_LABELS = {
    "Solar Only": ChargeMode.SOLAR,
    "Min Solar + Grid Top-up": ChargeMode.SOLAR,
    "Valley / Off-Peak": ChargeMode.CHEAP,
    "Max Solar + Grid": ChargeMode.MAX_POWER,
}


def coerce_mode(value: str | ChargeMode | None) -> ChargeMode:
    if isinstance(value, ChargeMode):
        return value
    if value is None:
        return ChargeMode.SOLAR
    text = str(value)
    try:
        return ChargeMode(text)
    except ValueError:
        return LEGACY_MODE_VALUES.get(text, ChargeMode.SOLAR)


PLATFORMS = ["sensor", "binary_sensor", "select", "number", "time"]
