"""Regulation-loop cases for the dusk, full-car, and wake bugs."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.ev_charge_optimizer.const import (
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_CHARGER_NUMBER_ENTITY,
    CONF_CHARGER_SWITCH_ENTITY,
    CONF_CHARGER_WAKE_ENTITY,
    CONF_DEFAULT_MODE,
    CONF_EV_CHARGE_LIMIT_ENTITY,
    CONF_EV_CHARGING_STATE_SENSOR,
    CONF_EV_SOC_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_HOUSE_CONSUMPTION_SENSOR,
    CONF_MAX_AMPS,
    CONF_MAX_STEP,
    CONF_MIN_AMPS,
    CONF_POWER_BUFFER,
    CONF_PRIORITIZE_BATTERY,
    CONF_ROLLING_WINDOW,
    CONF_SOLAR_PRODUCTION_SENSOR,
    CONF_STATIC_VOLTAGE,
    CONF_UPDATE_INTERVAL,
    CONF_VOLTAGE_SENSOR,
    DOMAIN,
    ChargeMode,
)
from custom_components.ev_charge_optimizer.coordinator import (
    EVChargeOptimizerCoordinator,
)

GRID = "sensor.grid_export"
PV = "sensor.pv_power"
HOUSE = "sensor.house_consumption"
BATTERY_POWER = "sensor.battery_power"
BATTERY_SOC = "sensor.battery_soc"
EV_SOC = "sensor.ev_soc"
CHARGING = "sensor.ev_charging"
LIMIT = "number.ev_charge_limit"
AMPS = "number.charger_amps"
SWITCH = "switch.charger"
WAKE = "button.charger_wake"
VOLTAGE = "sensor.voltage"


def _data(
    mode: ChargeMode = ChargeMode.SOLAR_ONLY,
    *,
    include_car_sensors: bool = True,
) -> dict[str, object]:
    data: dict[str, object] = {
        CONF_GRID_EXPORT_SENSOR: GRID,
        CONF_SOLAR_PRODUCTION_SENSOR: PV,
        CONF_HOUSE_CONSUMPTION_SENSOR: HOUSE,
        CONF_BATTERY_SOC_SENSOR: BATTERY_SOC,
        CONF_BATTERY_POWER_SENSOR: BATTERY_POWER,
        CONF_EV_SOC_SENSOR: EV_SOC,
        CONF_CHARGER_NUMBER_ENTITY: AMPS,
        CONF_CHARGER_SWITCH_ENTITY: SWITCH,
        CONF_CHARGER_WAKE_ENTITY: WAKE,
        CONF_VOLTAGE_SENSOR: VOLTAGE,
        CONF_STATIC_VOLTAGE: 240,
        CONF_MIN_AMPS: 1,
        CONF_MAX_AMPS: 32,
        CONF_DEFAULT_MODE: mode,
    }
    if include_car_sensors:
        data[CONF_EV_CHARGING_STATE_SENSOR] = CHARGING
        data[CONF_EV_CHARGE_LIMIT_ENTITY] = LIMIT
    return data


def _options() -> dict[str, object]:
    return {
        CONF_PRIORITIZE_BATTERY: True,
        CONF_UPDATE_INTERVAL: 10,
        CONF_POWER_BUFFER: 150,
        CONF_ROLLING_WINDOW: 5,
        CONF_MAX_STEP: 2,
        CONF_MIN_AMPS: 1,
        CONF_MAX_AMPS: 32,
        CONF_STATIC_VOLTAGE: 240,
    }


def _coordinator(
    hass: HomeAssistant,
    *,
    mode: ChargeMode = ChargeMode.SOLAR_ONLY,
    include_car_sensors: bool = True,
) -> EVChargeOptimizerCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_data(mode, include_car_sensors=include_car_sensors),
        options=_options(),
    )
    entry.add_to_hass(hass)
    return EVChargeOptimizerCoordinator(hass, entry)


def _set(hass: HomeAssistant, **states: object) -> None:
    for entity_id, value in states.items():
        hass.states.async_set(entity_id, str(value))


class _Services:
    def __init__(self, hass: HomeAssistant) -> None:
        self.turn_on = async_mock_service(hass, "switch", "turn_on")
        self.turn_off = async_mock_service(hass, "switch", "turn_off")
        self.set_value = async_mock_service(hass, "number", "set_value")
        self.press = async_mock_service(hass, "button", "press")


def _services(hass: HomeAssistant) -> _Services:
    return _Services(hass)


class _Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


async def test_switch_stays_off_when_dusk_export_is_noise(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _coordinator(hass)
    _set(
        hass,
        **{
            PV: 0,
            HOUSE: 450,
            BATTERY_POWER: 430,
            BATTERY_SOC: 98,
            VOLTAGE: 1.7,
            SWITCH: "off",
            AMPS: 16,
            EV_SOC: 40,
            LIMIT: 80,
            CHARGING: "Stopped",
        },
    )

    for cycle in range(20):
        _set(hass, **{GRID: 1 + (cycle % 5)})
        result = await coordinator._async_update_data()
        assert result["target_amps"] == 0

    assert services.turn_on == []


async def test_idle_setpoint_is_not_surplus_when_pv_is_unavailable(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _coordinator(hass)
    _set(
        hass,
        **{
            PV: "unavailable",
            HOUSE: 450,
            GRID: 5,
            BATTERY_SOC: 98,
            BATTERY_POWER: 430,
            VOLTAGE: 1.7,
            SWITCH: "off",
            AMPS: 16,
            CHARGING: "Stopped",
            EV_SOC: 40,
            LIMIT: 80,
        },
    )

    result = await coordinator._async_update_data()

    assert result["target_amps"] == 0
    assert services.turn_on == []


async def test_amps_rise_within_step_limit_when_surplus_is_real(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _coordinator(hass)
    _set(
        hass,
        **{
            PV: 4000,
            HOUSE: 500,
            GRID: 3300,
            BATTERY_SOC: 100,
            BATTERY_POWER: 0,
            VOLTAGE: 240,
            SWITCH: "on",
            AMPS: 6,
            CHARGING: "Charging",
            EV_SOC: 40,
            LIMIT: 80,
        },
    )

    first = await coordinator._async_update_data()
    assert first["target_amps"] > coordinator.min_amps
    assert services.set_value
    first_amps = services.set_value[0].data["value"]
    assert first_amps > coordinator.min_amps

    _set(hass, **{PV: 9000, GRID: 7000})
    await coordinator._async_update_data()
    assert len(services.set_value) >= 2
    second_amps = services.set_value[1].data["value"]
    assert second_amps > first_amps
    assert second_amps - first_amps <= 2


async def test_switch_turns_off_once_when_charging_complete(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _coordinator(hass)
    _set(
        hass,
        **{
            PV: 4000,
            HOUSE: 500,
            GRID: 3300,
            BATTERY_SOC: 100,
            VOLTAGE: 240,
            SWITCH: "on",
            AMPS: 16,
            CHARGING: "Complete",
            EV_SOC: 80,
            LIMIT: 90,
        },
    )

    for _ in range(5):
        result = await coordinator._async_update_data()
        assert result["target_amps"] == 0

    assert len(services.turn_off) == 1
    assert services.set_value == []
    assert services.press == []


async def test_target_stays_zero_when_soc_reaches_charge_limit(
    hass: HomeAssistant,
    monkeypatch: object,
) -> None:
    services = _services(hass)
    coordinator = _coordinator(hass, mode=ChargeMode.VALLEY)
    monkeypatch.setattr(
        "custom_components.ev_charge_optimizer.coordinator._now",
        _Clock(datetime(2026, 6, 15, 2, 0, 0)),
    )
    _set(
        hass,
        **{
            PV: 0,
            HOUSE: 400,
            GRID: 5000,
            BATTERY_SOC: 100,
            VOLTAGE: 240,
            SWITCH: "off",
            AMPS: 6,
            CHARGING: "Stopped",
            EV_SOC: 80,
            LIMIT: 80,
        },
    )

    result = await coordinator._async_update_data()

    assert result["target_amps"] == 0
    assert services.turn_on == []
    assert services.press == []


async def test_wake_is_not_pressed_when_disconnected(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _coordinator(hass)
    _set(
        hass,
        **{
            PV: 4000,
            HOUSE: 500,
            GRID: 3300,
            BATTERY_SOC: 100,
            VOLTAGE: 240,
            SWITCH: "off",
            AMPS: "unavailable",
            CHARGING: "disconnected",
            EV_SOC: 40,
            LIMIT: 80,
        },
    )

    for _ in range(3):
        await coordinator._async_update_data()

    assert services.press == []


async def test_wake_is_pressed_once_per_cooldown_when_asleep(
    hass: HomeAssistant,
    monkeypatch: object,
) -> None:
    services = _services(hass)
    coordinator = _coordinator(hass)
    clock = _Clock(datetime(2026, 6, 15, 12, 0, 0))
    monkeypatch.setattr(
        "custom_components.ev_charge_optimizer.coordinator._now",
        clock,
    )
    _set(
        hass,
        **{
            PV: 4000,
            HOUSE: 500,
            GRID: 3300,
            BATTERY_SOC: 100,
            VOLTAGE: 240,
            SWITCH: "off",
            AMPS: "unavailable",
            CHARGING: "Stopped",
            EV_SOC: 40,
            LIMIT: 80,
        },
    )

    for _ in range(10):
        await coordinator._async_update_data()
    assert len(services.press) == 1

    clock.moment += timedelta(minutes=15)
    await coordinator._async_update_data()
    assert len(services.press) == 2


async def test_update_runs_when_new_keys_are_absent(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _coordinator(hass, include_car_sensors=False)
    _set(
        hass,
        **{
            PV: 4000,
            HOUSE: 500,
            GRID: 3300,
            BATTERY_SOC: 100,
            VOLTAGE: 240,
            SWITCH: "on",
            AMPS: 6,
            EV_SOC: 40,
        },
    )

    result = await coordinator._async_update_data()

    assert result["target_amps"] > coordinator.min_amps
    assert services.set_value
    assert services.press == []


async def test_switch_off_is_retried_after_five_minutes(
    hass: HomeAssistant,
    monkeypatch: object,
) -> None:
    services = _services(hass)
    coordinator = _coordinator(hass)
    clock = _Clock(datetime(2026, 6, 15, 12, 0, 0))
    monkeypatch.setattr(
        "custom_components.ev_charge_optimizer.coordinator._now",
        clock,
    )
    _set(hass, **{PV: 4000, HOUSE: 500, GRID: 3300, BATTERY_SOC: 100, SWITCH: "on",
                  AMPS: 16, CHARGING: "Complete", EV_SOC: 80, LIMIT: 90})

    await coordinator._async_update_data()
    clock.moment += timedelta(minutes=4)
    await coordinator._async_update_data()
    assert len(services.turn_off) == 1

    clock.moment += timedelta(minutes=1)
    await coordinator._async_update_data()
    assert len(services.turn_off) == 2


async def test_setpoint_counts_as_draw_without_a_charger_switch(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass, include_car_sensors=False)
    entry = coordinator._entry
    hass.config_entries.async_update_entry(
        entry, data={k: v for k, v in entry.data.items() if k != CONF_CHARGER_SWITCH_ENTITY},
    )
    _set(hass, **{AMPS: 10})

    assert coordinator._measured_charger_power(240) == 2400


async def test_valley_mode_outside_window_ignores_battery_export(
    hass: HomeAssistant,
    monkeypatch: object,
) -> None:
    services = _services(hass)
    coordinator = _coordinator(hass, mode=ChargeMode.VALLEY)
    monkeypatch.setattr(
        "custom_components.ev_charge_optimizer.coordinator._now",
        _Clock(datetime(2026, 10, 12, 18, 16, 0)),
    )
    _set(hass, **{PV: 0, HOUSE: 450, BATTERY_POWER: 3300, BATTERY_SOC: 98, SWITCH: "off",
                  AMPS: 16, CHARGING: "Stopped", EV_SOC: 38, LIMIT: 100})

    for export in (3390, 3389, 2882, 1550, 900):
        _set(hass, **{GRID: export})
        result = await coordinator._async_update_data()
        assert result["target_amps"] == 0

    assert services.turn_on == []
