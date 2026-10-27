"""Regulation-loop cases for the dusk, full-car, and wake bugs."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from homeassistant.components.number import NumberMode
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
    mock_restore_cache,
)

from custom_components.ev_charge_optimizer.const import (
    CONF_BATTERY_POWER_POSITIVE_DISCHARGE,
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_CHARGER_NUMBER_ENTITY,
    CONF_CHARGER_SWITCH_ENTITY,
    CONF_CHARGER_WAKE_ENTITY,
    CONF_DEFAULT_MODE,
    CONF_EV_CHARGE_LIMIT_ENTITY,
    CONF_EV_CHARGING_STATE_SENSOR,
    CONF_EV_POWER_SENSOR,
    CONF_EV_SOC_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_MAX_POWER,
    CONF_HOUSE_CONSUMPTION_SENSOR,
    CONF_MAX_AMPS,
    CONF_MAX_STEP,
    CONF_MIN_AMPS,
    CONF_POWER_BUFFER,
    CONF_PRIORITIZE_BATTERY,
    CONF_ROLLING_WINDOW,
    CONF_SHED_LOADS,
    CONF_SHED_MAX_MINUTES,
    CONF_SOLAR_PRODUCTION_SENSOR,
    CONF_STATIC_VOLTAGE,
    CONF_UPDATE_INTERVAL,
    CONF_VOLTAGE_SENSOR,
    DOMAIN,
    ChargeMode,
)
from custom_components.ev_charge_optimizer.coordinator import (
    EVChargeOptimizerCoordinator,
    _now,
)
from custom_components.ev_charge_optimizer.number import MaxEvSocNumber
from custom_components.ev_charge_optimizer.sensor import EVPowerSensor, ShedLoadsSensor

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
EV_POWER = "sensor.wall_connector_power"
SHED_A = "switch.shed_a"
SHED_B = "switch.shed_b"
SHED_C = "switch.shed_c"
FRIDGE = "switch.fridge"


def _data(
    mode: ChargeMode = ChargeMode.SOLAR,
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
    mode: ChargeMode = ChargeMode.SOLAR,
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


async def test_published_entities_match_the_mpc_contract(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass)
    entry = coordinator._entry
    coordinator.data = {"ev_power": 3700, "shed_loads": [FRIDGE]}

    ev_power = EVPowerSensor(coordinator, entry)
    shed = ShedLoadsSensor(coordinator, entry)
    slider = MaxEvSocNumber(coordinator, entry)

    assert ev_power.unique_id == f"{entry.entry_id}_ev_power"
    assert ev_power.device_class == SensorDeviceClass.POWER
    assert ev_power.state_class == SensorStateClass.MEASUREMENT
    assert ev_power.native_unit_of_measurement == UnitOfPower.WATT
    assert ev_power.native_value == 3700

    assert shed.unique_id == f"{entry.entry_id}_shed_loads"
    assert shed.native_value == 1
    assert shed.extra_state_attributes == {"shed_loads": [FRIDGE]}

    assert slider.unique_id == f"{entry.entry_id}_max_ev_soc"
    assert slider.mode == NumberMode.SLIDER
    assert slider.native_min_value == 0
    assert slider.native_max_value == 100
    assert slider.native_step == 1
    assert slider.native_unit_of_measurement == PERCENTAGE
    assert slider.native_value == 100


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
    coordinator = _coordinator(hass, mode=ChargeMode.CHEAP)
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
    coordinator = _coordinator(hass, mode=ChargeMode.CHEAP)
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


def _set_options(coordinator: EVChargeOptimizerCoordinator, **options: object) -> None:
    coordinator.hass.config_entries.async_update_entry(
        coordinator._entry,
        options={**coordinator._entry.options, **options},
    )


def _charging_grid(hass: HomeAssistant, **states: object) -> None:
    _set(
        hass,
        **{
            PV: 1000,
            HOUSE: 400,
            GRID: 0,
            BATTERY_SOC: 100,
            BATTERY_POWER: 0,
            VOLTAGE: 240,
            SWITCH: "on",
            AMPS: 16,
            CHARGING: "Charging",
            EV_SOC: 40,
            LIMIT: 80,
            **states,
        },
    )


def _calls(service_calls: list, entity_id: str) -> list:
    return [call for call in service_calls if call.data.get("entity_id") == entity_id]


async def test_grid_headroom_uses_measured_ev_and_battery_discharge(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _coordinator(hass, mode=ChargeMode.MAX_POWER)
    _set_options(
        coordinator,
        **{
            CONF_GRID_MAX_POWER: 5500,
            CONF_BATTERY_POWER_POSITIVE_DISCHARGE: True,
            CONF_EV_POWER_SENSOR: EV_POWER,
        },
    )
    _charging_grid(hass, **{GRID: -360, BATTERY_POWER: 4600, AMPS: 32})
    hass.states.async_set(EV_POWER, "3.7", {"unit_of_measurement": "kW"})

    result = await coordinator._async_update_data()

    assert result["target_amps"] in (17, 18)
    assert result["target_amps"] != 32
    assert result["available_power"] == pytest.approx(4240, abs=1)
    assert result["ev_power"] == pytest.approx(3700, abs=1)
    assert services.set_value
    assert services.set_value[-1].data["value"] in (17, 18)


async def test_ev_may_take_battery_charge_that_is_coming_from_the_grid(
    hass: HomeAssistant,
) -> None:
    _services(hass)
    coordinator = _coordinator(hass, mode=ChargeMode.MAX_POWER)
    _set_options(
        coordinator,
        **{
            CONF_GRID_MAX_POWER: 5500,
            CONF_BATTERY_POWER_POSITIVE_DISCHARGE: True,
            CONF_EV_POWER_SENSOR: EV_POWER,
        },
    )
    _charging_grid(
        hass,
        **{GRID: -3500, BATTERY_POWER: -3000, SWITCH: "off", AMPS: 32, CHARGING: "Stopped"},
    )
    hass.states.async_set(EV_POWER, "0", {"unit_of_measurement": "W"})

    result = await coordinator._async_update_data()

    assert coordinator._get_battery_charge_demand() == 3000
    assert result["available_power"] == pytest.approx(5000, abs=1)
    assert coordinator._get_grid_available_amps() == pytest.approx(5000 / 240, abs=0.05)


async def test_battery_charge_above_import_is_not_grid_yieldable(
    hass: HomeAssistant,
) -> None:
    _services(hass)
    coordinator = _coordinator(hass, mode=ChargeMode.MAX_POWER)
    _set_options(
        coordinator,
        **{
            CONF_GRID_MAX_POWER: 5500,
            CONF_BATTERY_POWER_POSITIVE_DISCHARGE: False,
        },
    )
    _charging_grid(
        hass,
        **{GRID: -500, BATTERY_POWER: 3000, SWITCH: "off", AMPS: 0, CHARGING: "Stopped"},
    )

    result = await coordinator._async_update_data()

    assert coordinator._get_battery_charge_demand() == 3000
    assert result["available_power"] == pytest.approx(5500, abs=1)


async def test_missing_power_sensors_count_setpoint_only_while_drawing(
    hass: HomeAssistant,
) -> None:
    entry_data = _data(ChargeMode.MAX_POWER)
    entry_data.pop(CONF_BATTERY_POWER_SENSOR)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
        options={**_options(), CONF_GRID_MAX_POWER: 5500},
    )
    entry.add_to_hass(hass)
    coordinator = EVChargeOptimizerCoordinator(hass, entry)
    _charging_grid(hass, **{GRID: -360, AMPS: 10, SWITCH: "on", CHARGING: "Charging"})

    drawing = coordinator._get_grid_available_amps()

    assert drawing == pytest.approx(10 + (5500 - 360) / 240, abs=0.05)

    _set(hass, **{SWITCH: "on", CHARGING: "Stopped", AMPS: 32})
    idle = coordinator._get_grid_available_amps()
    assert idle == pytest.approx((5500 - 360) / 240, abs=0.05)
    assert idle < 32


async def test_ev_power_is_published_when_off_or_not_charging(
    hass: HomeAssistant,
) -> None:
    _services(hass)
    coordinator = _coordinator(hass, mode=ChargeMode.OFF)
    _set_options(coordinator, **{CONF_EV_POWER_SENSOR: EV_POWER})
    _charging_grid(hass, **{PV: 0, GRID: 0, SWITCH: "off", CHARGING: "Stopped"})
    hass.states.async_set(EV_POWER, "0", {"unit_of_measurement": "kW"})

    disabled = await coordinator._async_update_data()

    assert "enabled" not in disabled
    assert disabled["target_amps"] == 0
    assert disabled["status"] == "Off"
    assert disabled["ev_power"] == 0
    assert disabled["shed_loads"] == []

    coordinator.mode = ChargeMode.SOLAR
    _set(hass, **{CHARGING: "Complete", SWITCH: "on", EV_SOC: 80, LIMIT: 90})
    stopped = await coordinator._async_update_data()
    assert stopped["target_amps"] == 0
    assert stopped["ev_power"] == 0


async def test_max_ev_soc_stops_below_the_car_charge_limit(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _coordinator(hass, mode=ChargeMode.MAX_POWER)
    coordinator.max_ev_soc = 30
    _set_options(coordinator, **{CONF_GRID_MAX_POWER: 5500})
    _charging_grid(hass, **{GRID: 0, EV_SOC: 30, LIMIT: 50, SWITCH: "on", AMPS: 10})

    result = await coordinator._async_update_data()

    assert result["target_amps"] == 0
    assert services.set_value == []
    assert services.turn_on == []


async def test_car_limit_still_allows_charge_when_slider_is_higher(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _coordinator(hass, mode=ChargeMode.MAX_POWER)
    coordinator.max_ev_soc = 100
    _set_options(coordinator, **{CONF_GRID_MAX_POWER: 5500})
    _charging_grid(hass, **{GRID: 0, EV_SOC: 40, LIMIT: 50, SWITCH: "on", AMPS: 6})

    result = await coordinator._async_update_data()

    assert result["target_amps"] > coordinator.min_amps
    assert services.set_value


def _shed_coordinator(
    hass: HomeAssistant,
    loads: list[str],
    *,
    max_minutes: int = 120,
) -> EVChargeOptimizerCoordinator:
    coordinator = _coordinator(hass, mode=ChargeMode.MAX_POWER)
    _set_options(
        coordinator,
        **{
            CONF_GRID_MAX_POWER: 5500,
            CONF_EV_POWER_SENSOR: EV_POWER,
            CONF_SHED_LOADS: loads,
            CONF_SHED_MAX_MINUTES: max_minutes,
        },
    )
    return coordinator


def _limited_grid(hass: HomeAssistant) -> None:
    _charging_grid(hass, **{GRID: -360, AMPS: 16, SWITCH: "on", CHARGING: "Charging"})
    hass.states.async_set(EV_POWER, "1.0", {"unit_of_measurement": "kW"})


async def test_max_mode_sheds_on_loads_one_per_dwell_and_skips_off(
    hass: HomeAssistant,
    monkeypatch: object,
) -> None:
    services = _services(hass)
    clock = _Clock(datetime(2026, 10, 22, 12, 0, 0))
    monkeypatch.setattr(
        "custom_components.ev_charge_optimizer.coordinator._now",
        clock,
    )
    coordinator = _shed_coordinator(hass, [SHED_A, SHED_B, SHED_C])
    _limited_grid(hass)
    _set(hass, **{SHED_A: "off", SHED_B: "on", SHED_C: "on"})

    first = await coordinator._async_update_data()
    assert _calls(services.turn_off, SHED_A) == []
    assert len(_calls(services.turn_off, SHED_B)) == 1
    assert _calls(services.turn_off, SHED_C) == []
    assert first["shed_loads"] == [SHED_B]

    clock.moment += timedelta(seconds=59)
    await coordinator._async_update_data()
    assert _calls(services.turn_off, SHED_C) == []

    clock.moment += timedelta(seconds=1)
    _set(hass, **{SHED_B: "off"})
    second = await coordinator._async_update_data()
    assert len(_calls(services.turn_off, SHED_C)) == 1
    assert second["shed_loads"] == [SHED_B, SHED_C]


async def test_leaving_max_mode_restores_only_shed_loads(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _shed_coordinator(hass, [SHED_A, SHED_B])
    _limited_grid(hass)
    _set(hass, **{SHED_A: "on", SHED_B: "off"})

    shed = await coordinator._async_update_data()
    assert shed["shed_loads"] == [SHED_A]

    coordinator.mode = ChargeMode.SOLAR
    _set(hass, **{PV: 0, GRID: 0, SHED_A: "off"})
    restored = await coordinator._async_update_data()

    assert _calls(services.turn_on, SHED_A)
    assert _calls(services.turn_on, SHED_B) == []
    assert restored["shed_loads"] == []


async def test_shed_restores_after_max_minutes_and_respects_cooldown(
    hass: HomeAssistant,
    monkeypatch: object,
) -> None:
    services = _services(hass)
    clock = _Clock(datetime(2026, 10, 22, 12, 0, 0))
    monkeypatch.setattr(
        "custom_components.ev_charge_optimizer.coordinator._now",
        clock,
    )
    coordinator = _shed_coordinator(hass, [FRIDGE], max_minutes=120)
    _limited_grid(hass)
    _set(hass, **{FRIDGE: "on"})

    await coordinator._async_update_data()
    assert _calls(services.turn_off, FRIDGE)

    clock.moment += timedelta(minutes=120)
    _set(hass, **{FRIDGE: "off"})
    expired = await coordinator._async_update_data()
    assert _calls(services.turn_on, FRIDGE)
    assert expired["shed_loads"] == []

    clock.moment += timedelta(minutes=29)
    _set(hass, **{FRIDGE: "on"})
    await coordinator._async_update_data()
    assert len(_calls(services.turn_off, FRIDGE)) == 1

    clock.moment += timedelta(minutes=1)
    cooled = await coordinator._async_update_data()
    assert len(_calls(services.turn_off, FRIDGE)) == 2
    assert cooled["shed_loads"] == [FRIDGE]


async def test_restart_turns_persisted_shed_loads_back_on(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _shed_coordinator(hass, [FRIDGE, SHED_A])
    _limited_grid(hass)
    _set(hass, **{FRIDGE: "on", SHED_A: "off"})
    shed = await coordinator._async_update_data()
    assert shed["shed_loads"] == [FRIDGE]

    restarted = EVChargeOptimizerCoordinator(hass, coordinator._entry)
    _set(hass, **{FRIDGE: "off", SHED_A: "off"})
    result = await restarted._async_update_data()

    assert _calls(services.turn_on, FRIDGE)
    assert _calls(services.turn_on, SHED_A) == []
    assert result["shed_loads"] == []


async def test_stale_ev_sensor_above_setpoint_does_not_invent_headroom(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass, mode=ChargeMode.MAX_POWER)
    _set_options(
        coordinator,
        **{CONF_GRID_MAX_POWER: 5500, CONF_EV_POWER_SENSOR: EV_POWER},
    )
    _charging_grid(hass, **{GRID: -360, AMPS: 16, SWITCH: "on", CHARGING: "Charging"})
    hass.states.async_set(EV_POWER, "7.68", {"unit_of_measurement": "kW"})

    assert coordinator._measured_ev_watts() == pytest.approx(16 * 240)

    hass.states.async_set(AMPS, "unavailable")
    assert coordinator._measured_ev_watts() == pytest.approx(7680)


async def test_no_shedding_while_the_car_draws_nothing(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _shed_coordinator(hass, [SHED_A])
    _limited_grid(hass)
    hass.states.async_set(EV_POWER, "0", {"unit_of_measurement": "kW"})
    _set(hass, **{SHED_A: "on"})

    result = await coordinator._async_update_data()

    assert result["target_amps"] > 0
    assert _calls(services.turn_off, SHED_A) == []


async def test_shed_loads_return_after_three_idle_cycles_even_with_a_target(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _shed_coordinator(hass, [FRIDGE])
    _limited_grid(hass)
    _set(hass, **{FRIDGE: "on"})
    await coordinator._async_update_data()
    assert _calls(services.turn_off, FRIDGE)

    _set(hass, **{FRIDGE: "off"})
    hass.states.async_set(EV_POWER, "0", {"unit_of_measurement": "kW"})
    for _ in range(2):
        held = await coordinator._async_update_data()
        assert held["target_amps"] > 0
        assert _calls(services.turn_on, FRIDGE) == []

    restored = await coordinator._async_update_data()
    assert _calls(services.turn_on, FRIDGE)
    assert restored["shed_loads"] == []


async def test_old_valley_label_restores_as_cheap(hass: HomeAssistant) -> None:
    _services(hass)
    mock_restore_cache(
        hass,
        [
            State("select.ev_charge_optimizer_charging_mode", "Valley / Off-Peak"),
            State("switch.ev_charge_optimizer_enabled", "off"),
        ],
    )
    entry = MockConfigEntry(
        domain=DOMAIN, data=_data(ChargeMode.SOLAR), options=_options()
    )
    entry.add_to_hass(hass)
    _charging_grid(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.mode == ChargeMode.CHEAP
    assert not hasattr(coordinator, "enabled")
    assert hass.states.get("select.ev_charge_optimizer_charging_mode").state == "Cheap"
    assert hass.states.get("switch.ev_charge_optimizer_enabled") is None


async def test_max_mode_is_labelled_max_power_and_old_label_still_restores(
    hass: HomeAssistant,
) -> None:
    _services(hass)
    mock_restore_cache(
        hass, [State("select.ev_charge_optimizer_charging_mode", "Max Solar + Grid")]
    )
    entry = MockConfigEntry(
        domain=DOMAIN, data=_data(ChargeMode.SOLAR), options=_options()
    )
    entry.add_to_hass(hass)
    _charging_grid(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.mode == ChargeMode.MAX_POWER
    state = hass.states.get("select.ev_charge_optimizer_charging_mode")
    assert state.state == "Max Power"
    assert "Max Solar + Grid" not in state.attributes["options"]


async def test_restart_retries_restore_until_the_load_is_really_on(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _shed_coordinator(hass, [FRIDGE])
    _limited_grid(hass)
    _set(hass, **{FRIDGE: "on"})
    shed = await coordinator._async_update_data()
    assert shed["shed_loads"] == [FRIDGE]

    restarted = EVChargeOptimizerCoordinator(hass, coordinator._entry)
    hass.states.async_set(FRIDGE, "unavailable")
    await restarted._async_update_data()
    assert _calls(services.turn_on, FRIDGE) == []
    assert restarted._pending_restore == [FRIDGE]

    _set(hass, **{FRIDGE: "off"})
    await restarted._async_update_data()
    assert len(_calls(services.turn_on, FRIDGE)) == 1
    assert restarted._pending_restore == [FRIDGE]

    restarted.mode = ChargeMode.CHEAP
    _set(hass, **{FRIDGE: "on"})
    await restarted._async_update_data()
    assert restarted._pending_restore == []

    again = EVChargeOptimizerCoordinator(hass, coordinator._entry)
    _set(hass, **{FRIDGE: "off"})
    await again._async_update_data()
    assert len(_calls(services.turn_on, FRIDGE)) == 1


async def test_leaving_max_mode_retries_an_unavailable_shed_load(
    hass: HomeAssistant,
) -> None:
    services = _services(hass)
    coordinator = _shed_coordinator(hass, [FRIDGE])
    _limited_grid(hass)
    _set(hass, **{FRIDGE: "on"})
    await coordinator._async_update_data()

    coordinator.mode = ChargeMode.CHEAP
    hass.states.async_set(FRIDGE, "unavailable")
    await coordinator._async_update_data()
    assert _calls(services.turn_on, FRIDGE) == []

    _set(hass, **{FRIDGE: "off"})
    await coordinator._async_update_data()
    assert len(_calls(services.turn_on, FRIDGE)) == 1


async def test_cheap_mode_leaves_battery_grid_charging_alone(
    hass: HomeAssistant,
) -> None:
    _services(hass)
    coordinator = _coordinator(hass, mode=ChargeMode.CHEAP)
    _set_options(
        coordinator,
        **{
            CONF_GRID_MAX_POWER: 5500,
            CONF_BATTERY_POWER_POSITIVE_DISCHARGE: True,
            CONF_EV_POWER_SENSOR: EV_POWER,
        },
    )
    _charging_grid(
        hass,
        **{GRID: -3500, BATTERY_POWER: -3000, SWITCH: "off", AMPS: 32, CHARGING: "Stopped"},
    )
    hass.states.async_set(EV_POWER, "0", {"unit_of_measurement": "W"})

    assert coordinator._grid_headroom_watts() == pytest.approx(2000, abs=1)


async def test_clock_follows_home_assistant_time_zone(
    hass: HomeAssistant,
    freezer: object,
) -> None:
    await hass.config.async_set_time_zone("Europe/Madrid")
    freezer.move_to("2026-06-15 00:30:00+00:00")

    now = _now()

    assert now == datetime(2026, 6, 15, 2, 30, 0)
    assert now.tzinfo is None
    assert now != datetime.now()
