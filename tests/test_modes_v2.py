"""Modes v2: five modes, Ready By, status, and the removed Enabled switch."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
    mock_restore_cache,
    mock_restore_cache_with_extra_data,
)

from custom_components.ev_charge_optimizer.binary_sensor import (
    BatteryAssistBinarySensor,
)
from custom_components.ev_charge_optimizer.const import (
    CONF_BATTERY_MAX_DISCHARGE_W,
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_RESERVE_SOC,
    CONF_BATTERY_SOC_SENSOR,
    CONF_BOOST_COOLDOWN_MINUTES,
    CONF_BOOST_FACTOR,
    CONF_BOOST_MAX_MINUTES,
    CONF_CHARGER_NUMBER_ENTITY,
    CONF_CHARGER_SWITCH_ENTITY,
    CONF_CHARGER_WAKE_ENTITY,
    CONF_DEFAULT_MODE,
    CONF_EV_BATTERY_KWH,
    CONF_EV_CHARGE_LIMIT_ENTITY,
    CONF_EV_CHARGING_STATE_SENSOR,
    CONF_EV_POWER_SENSOR,
    CONF_EV_SOC_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_GRID_MAX_POWER,
    CONF_MAX_STEP,
    CONF_GUARANTEED_MIN_AMPS,
    CONF_HOUSE_CONSUMPTION_SENSOR,
    CONF_INVERTER_AC_MAX_W,
    CONF_MAX_AMPS,
    CONF_MIN_AMPS,
    CONF_PEAK_HOURS,
    CONF_POWER_BUFFER,
    CONF_PRIORITIZE_BATTERY,
    CONF_SHED_LOADS,
    CONF_SOLAR_FORECAST_TODAY,
    CONF_SOLAR_FORECAST_TOMORROW,
    CONF_SOLAR_PRODUCTION_SENSOR,
    CONF_STATIC_VOLTAGE,
    CONF_VALLEY_AMPS,
    CONF_VALLEY_END,
    CONF_VALLEY_START,
    CONF_VALLEY_TARGET_SOC,
    CONF_VOLTAGE_SENSOR,
    CONF_WEEKENDS_VALLEY,
    DOMAIN,
    MODE_LABELS,
    ChargeMode,
)
from custom_components.ev_charge_optimizer.coordinator import (
    EVChargeOptimizerCoordinator,
)
from custom_components.ev_charge_optimizer.number import SolarFloorAmpsNumber
from custom_components.ev_charge_optimizer.sensor import StatusSensor

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
FORECAST = "sensor.solcast_forecast_today"
SHED_A = "switch.shed_a"
SHED_B = "switch.shed_b"


def _data(mode: ChargeMode | str = ChargeMode.SOLAR) -> dict[str, object]:
    return {
        CONF_GRID_EXPORT_SENSOR: GRID,
        CONF_SOLAR_PRODUCTION_SENSOR: PV,
        CONF_HOUSE_CONSUMPTION_SENSOR: HOUSE,
        CONF_EV_SOC_SENSOR: EV_SOC,
        CONF_EV_CHARGING_STATE_SENSOR: CHARGING,
        CONF_EV_CHARGE_LIMIT_ENTITY: LIMIT,
        CONF_CHARGER_NUMBER_ENTITY: AMPS,
        CONF_CHARGER_SWITCH_ENTITY: SWITCH,
        CONF_CHARGER_WAKE_ENTITY: WAKE,
        CONF_VOLTAGE_SENSOR: VOLTAGE,
        CONF_STATIC_VOLTAGE: 240,
        CONF_MIN_AMPS: 1,
        CONF_MAX_AMPS: 32,
        CONF_DEFAULT_MODE: mode,
    }


def _options(**extra: object) -> dict[str, object]:
    return {
        CONF_PRIORITIZE_BATTERY: False,
        CONF_POWER_BUFFER: 0,
        CONF_MIN_AMPS: 1,
        CONF_MAX_AMPS: 32,
        CONF_STATIC_VOLTAGE: 240,
        CONF_GRID_MAX_POWER: 9600,
        **extra,
    }


def _coordinator(
    hass: HomeAssistant,
    mode: ChargeMode | str = ChargeMode.SOLAR,
    **options: object,
) -> EVChargeOptimizerCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN, data=_data(mode), options=_options(**options)
    )
    entry.add_to_hass(hass)
    return EVChargeOptimizerCoordinator(hass, entry)


def _set(hass: HomeAssistant, **states: object) -> None:
    for entity_id, value in states.items():
        hass.states.async_set(entity_id, str(value))


def _services(hass: HomeAssistant):
    return type(
        "Services",
        (),
        {
            "turn_on": async_mock_service(hass, "switch", "turn_on"),
            "turn_off": async_mock_service(hass, "switch", "turn_off"),
            "set_value": async_mock_service(hass, "number", "set_value"),
            "press": async_mock_service(hass, "button", "press"),
        },
    )()


class _Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


def _freeze(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> _Clock:
    clock = _Clock(moment)
    monkeypatch.setattr(
        "custom_components.ev_charge_optimizer.coordinator._now",
        clock,
    )
    return clock


def _chargeable(hass: HomeAssistant, **states: object) -> None:
    _set(
        hass,
        **{
            PV: 0,
            HOUSE: 400,
            GRID: 0,
            BATTERY_SOC: 100,
            BATTERY_POWER: 0,
            VOLTAGE: 240,
            SWITCH: "on",
            AMPS: 6,
            CHARGING: "Charging",
            EV_SOC: 40,
            LIMIT: 80,
            **states,
        },
    )


def _calls(service_calls: list, entity_id: str) -> list:
    return [call for call in service_calls if call.data.get("entity_id") == entity_id]


async def test_each_mode_target_in_valley_shoulder_and_peak(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valley is 23:00-07:00, peak is 10-14 and 18-22, 15:00 is shoulder."""
    services = _services(hass)
    periods = {
        "valley": datetime(2026, 10, 21, 2, 0),
        "shoulder": datetime(2026, 10, 21, 15, 0),
        "peak": datetime(2026, 10, 21, 12, 0),
    }
    expected = {
        ChargeMode.OFF: {"valley": 0, "shoulder": 0, "peak": 0},
        ChargeMode.SOLAR: {"valley": 0, "shoulder": 0, "peak": 0},
        ChargeMode.CHEAP: {"valley": 16, "shoulder": 0, "peak": 0},
        ChargeMode.MAX_POWER: {"valley": 32, "shoulder": 32, "peak": 32},
    }

    for period, moment in periods.items():
        _freeze(monkeypatch, moment)
        for mode, targets in expected.items():
            coordinator = _coordinator(hass, mode, **{CONF_VALLEY_AMPS: 16})
            _chargeable(hass)
            result = await coordinator._async_update_data()
            assert result["target_amps"] == targets[period], (mode, period)

    ready = _coordinator(hass, ChargeMode.READY_BY, **{CONF_EV_BATTERY_KWH: 60})
    ready.max_ev_soc = 60
    ready.ready_by = time(7, 30)
    _freeze(monkeypatch, periods["valley"])
    _chargeable(hass, **{EV_SOC: 40})
    valley = await ready._async_update_data()
    assert valley["target_amps"] == 32

    ready_later = _coordinator(hass, ChargeMode.READY_BY, **{CONF_EV_BATTERY_KWH: 60})
    ready_later.max_ev_soc = 60
    ready_later.ready_by = time(7, 30)
    _freeze(monkeypatch, periods["peak"])
    _chargeable(hass, **{EV_SOC: 40})
    peak = await ready_later._async_update_data()
    assert peak["target_amps"] == 0

    ready_shoulder = _coordinator(
        hass, ChargeMode.READY_BY, **{CONF_EV_BATTERY_KWH: 60}
    )
    ready_shoulder.max_ev_soc = 60
    ready_shoulder.ready_by = time(7, 30)
    _freeze(monkeypatch, periods["shoulder"])
    _chargeable(hass, **{EV_SOC: 40})
    shoulder = await ready_shoulder._async_update_data()
    assert shoulder["target_amps"] == 0
    assert services.press == []


async def test_cheap_weekend_is_valley_unless_disabled(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _services(hass)
    _freeze(monkeypatch, datetime(2026, 10, 24, 12, 0))
    weekend = _coordinator(hass, ChargeMode.CHEAP, **{CONF_VALLEY_AMPS: 16})
    _chargeable(hass)
    assert (await weekend._async_update_data())["target_amps"] == 16

    weekday = _coordinator(hass, ChargeMode.CHEAP, **{CONF_VALLEY_AMPS: 16})
    _freeze(monkeypatch, datetime(2026, 10, 21, 12, 0))
    _chargeable(hass)
    assert (await weekday._async_update_data())["target_amps"] == 0

    no_weekend = _coordinator(
        hass,
        ChargeMode.CHEAP,
        **{CONF_VALLEY_AMPS: 16, CONF_WEEKENDS_VALLEY: False},
    )
    _freeze(monkeypatch, datetime(2026, 10, 24, 12, 0))
    _chargeable(hass)
    assert (await no_weekend._async_update_data())["target_amps"] == 0
    assert services.press == []


async def test_solar_floor_lifts_target_and_stays_inside_grid_headroom(
    hass: HomeAssistant,
) -> None:
    _services(hass)
    coordinator = _coordinator(hass, ChargeMode.SOLAR)
    coordinator.solar_floor_amps = 8
    _chargeable(hass, **{PV: 0, GRID: 0})

    floored = await coordinator._async_update_data()
    assert floored["target_amps"] == 8

    coordinator.solar_floor_amps = 0
    pure = await coordinator._async_update_data()
    assert pure["target_amps"] == 0

    capped = _coordinator(
        hass, ChargeMode.SOLAR, **{CONF_GRID_MAX_POWER: 10 * 240}
    )
    capped.solar_floor_amps = 16
    _chargeable(
        hass,
        **{PV: 20000, GRID: 0, HOUSE: 0, SWITCH: "off", AMPS: 0, CHARGING: "Stopped"},
    )
    result = await capped._async_update_data()
    assert result["target_amps"] == 10


async def test_off_stops_charger_without_wake_and_restores_shed_loads(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _services(hass)
    clock = _freeze(monkeypatch, datetime(2026, 10, 21, 12, 0))
    coordinator = _coordinator(
        hass,
        ChargeMode.MAX_POWER,
        **{CONF_GRID_MAX_POWER: 2000, "shed_loads": [SHED_A]},
    )
    coordinator.hass.config_entries.async_update_entry(
        coordinator._entry,
        options={**coordinator._entry.options, CONF_SHED_LOADS: [SHED_A]},
    )
    _chargeable(hass, **{GRID: -500, SWITCH: "on", AMPS: 16})
    _set(hass, **{SHED_A: "on"})
    await coordinator._async_update_data()
    assert _calls(services.turn_off, SHED_A)
    amp_writes = len(_calls(services.set_value, AMPS))

    coordinator.mode = ChargeMode.OFF
    _set(hass, **{SHED_A: "off", SWITCH: "on"})
    stopped = await coordinator._async_update_data()
    clock.moment += timedelta(minutes=1)
    await coordinator._async_update_data()

    assert stopped["target_amps"] == 0
    assert stopped["status"] == "Off"
    assert stopped["shed_loads"] == []
    assert _calls(services.turn_on, SHED_A)
    assert len(_calls(services.turn_off, SWITCH)) == 1
    assert len(_calls(services.set_value, AMPS)) == amp_writes
    assert services.press == []


def _ready(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    moment: datetime,
    **options: object,
) -> EVChargeOptimizerCoordinator:
    _freeze(monkeypatch, moment)
    coordinator = _coordinator(
        hass,
        ChargeMode.READY_BY,
        **{
            CONF_EV_BATTERY_KWH: 60,
            CONF_VALLEY_START: "02:00",
            CONF_VALLEY_END: "07:00",
            CONF_PEAK_HOURS: "10-14,18-22",
            **options,
        },
    )
    coordinator.ready_by = time(7, 30)
    coordinator.max_ev_soc = 60
    return coordinator


async def test_ready_by_picks_valley_before_shoulder_before_peak_earliest_first(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _services(hass)
    coordinator = _ready(
        hass,
        monkeypatch,
        datetime(2026, 10, 21, 18, 0),
        **{CONF_GRID_MAX_POWER: 1400, CONF_VALLEY_END: "03:00"},
    )
    coordinator.max_ev_soc = 43.75
    _chargeable(
        hass,
        **{
            EV_SOC: 40,
            HOUSE: 400,
            GRID: 0,
            SWITCH: "off",
            AMPS: 0,
            CHARGING: "Stopped",
        },
    )

    result = await coordinator._async_update_data()
    slots = result["status_attributes"]["chosen_slots"]
    classes = {slot["price_class"] for slot in slots}

    assert result["target_amps"] == 0
    assert "valley" in classes
    assert "shoulder" in classes
    assert "peak" not in classes
    assert any(slot["start"].startswith("2026-10-22T02:00") for slot in slots)
    assert any(slot["start"].startswith("2026-10-21T22:00") for slot in slots)

    earliest = _ready(hass, monkeypatch, datetime(2026, 10, 21, 22, 0))
    earliest.max_ev_soc = 60
    _chargeable(hass, **{EV_SOC: 59, HOUSE: 400})
    only = await earliest._async_update_data()
    chosen = only["status_attributes"]["chosen_slots"]
    assert len(chosen) == 1
    assert chosen[0]["price_class"] == "valley"
    assert chosen[0]["start"].startswith("2026-10-22T02:00")


async def test_ready_by_charges_now_only_when_the_current_slot_is_chosen(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _services(hass)
    waiting = _ready(hass, monkeypatch, datetime(2026, 10, 21, 22, 0))
    _chargeable(hass, **{EV_SOC: 42, HOUSE: 400})
    before = await waiting._async_update_data()
    assert before["target_amps"] == 0
    assert before["status"] == (
        "Ready By 07:30: need 12.0 kWh, next charge 02:00 (valley)"
    )
    assert services.set_value == []

    charging = _ready(hass, monkeypatch, datetime(2026, 10, 22, 2, 0))
    _chargeable(hass, **{EV_SOC: 42, HOUSE: 400, SWITCH: "on", AMPS: 6})
    now = await charging._async_update_data()
    assert now["target_amps"] == 32
    assert now["status"] == (
        "Ready By 07:30: need 12.0 kWh, charging now (valley)"
    )
    assert services.set_value[-1].data["value"] == 32


async def test_ready_by_uses_every_slot_when_the_deadline_is_short(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _services(hass)
    coordinator = _ready(
        hass,
        monkeypatch,
        datetime(2026, 10, 21, 6, 30),
        **{CONF_GRID_MAX_POWER: 1400},
    )
    coordinator.max_ev_soc = 56.15
    _chargeable(
        hass,
        **{
            EV_SOC: 50,
            HOUSE: 400,
            GRID: 0,
            SWITCH: "off",
            AMPS: 0,
            CHARGING: "Stopped",
        },
    )

    result = await coordinator._async_update_data()
    attrs = result["status_attributes"]

    assert attrs["shortfall_kwh"] == pytest.approx(3.1, abs=0.05)
    assert result["status"] == (
        "Ready By 07:30: 3.1 kWh short even charging every hour"
    )
    assert len(attrs["chosen_slots"]) >= 1
    assert result["target_amps"] > 0
    assert attrs["projected_soc"] < coordinator.max_ev_soc


async def test_ready_by_need_zero_is_solar_only(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _services(hass)
    coordinator = _ready(hass, monkeypatch, datetime(2026, 10, 22, 2, 0))
    coordinator.max_ev_soc = 60
    _chargeable(hass, **{EV_SOC: 60, PV: 0, GRID: 0})

    result = await coordinator._async_update_data()

    assert result["target_amps"] == 0
    assert result["status"] == "Ready By 07:30: target 60% reached"
    assert services.set_value == []
    assert services.press == []


async def test_ready_by_deadline_rolls_to_tomorrow(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _services(hass)
    coordinator = _ready(hass, monkeypatch, datetime(2026, 10, 21, 8, 0))
    _chargeable(hass, **{EV_SOC: 40})

    result = await coordinator._async_update_data()

    assert result["status_attributes"]["deadline"] == "2026-10-22T07:30:00"


async def test_solcast_credit_reduces_need_and_missing_attribute_does_not(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _services(hass)
    moment = datetime(2026, 10, 22, 2, 0)
    bare = _ready(
        hass,
        monkeypatch,
        moment,
        **{CONF_SOLAR_FORECAST_TODAY: FORECAST},
    )
    _chargeable(hass, **{EV_SOC: 42, HOUSE: 400})
    hass.states.async_set(FORECAST, "12.0")
    without = await bare._async_update_data()
    assert without["status_attributes"]["need_kwh"] == pytest.approx(12.0, abs=0.05)
    assert without["target_amps"] == 32

    credited = _ready(
        hass,
        monkeypatch,
        moment,
        **{CONF_SOLAR_FORECAST_TODAY: FORECAST},
    )
    hourly = []
    cursor = moment
    while cursor < datetime(2026, 10, 22, 7, 30):
        hourly.append(
            {"period_start": cursor.isoformat(), "pv_estimate": 8.0}
        )
        cursor += timedelta(minutes=30)
    hass.states.async_set(FORECAST, "12.0", {"detailedHourly": hourly})
    _chargeable(hass, **{EV_SOC: 42, HOUSE: 400, PV: 0, GRID: 0})
    with_credit = await credited._async_update_data()

    assert with_credit["status_attributes"]["need_kwh"] < without["status_attributes"][
        "need_kwh"
    ]
    assert with_credit["status_attributes"]["need_kwh"] == pytest.approx(0, abs=0.05)
    assert with_credit["target_amps"] == 0


async def test_ready_by_missing_soc_falls_back_to_cheap(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _services(hass)
    _freeze(monkeypatch, datetime(2026, 10, 22, 2, 0))
    cheap = _coordinator(hass, ChargeMode.CHEAP, **{CONF_VALLEY_AMPS: 16})
    ready = _coordinator(hass, ChargeMode.READY_BY, **{CONF_VALLEY_AMPS: 16})
    ready.ready_by = time(7, 30)
    _chargeable(hass, **{EV_SOC: "unavailable"})

    cheap_result = await cheap._async_update_data()
    ready_result = await ready._async_update_data()

    assert ready_result["target_amps"] == cheap_result["target_amps"] == 16
    assert "SoC unavailable" in ready_result["status"]
    assert "Cheap" in ready_result["status"]


async def test_status_sensor_strings_for_each_mode(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _services(hass)
    clock = _freeze(monkeypatch, datetime(2026, 10, 21, 15, 0))

    solar = _coordinator(hass, ChargeMode.SOLAR)
    _chargeable(hass, **{PV: 2500, HOUSE: 1840, GRID: 2000, AMPS: 6, SWITCH: "on"})
    solar_on = await solar._async_update_data()
    assert solar_on["status"] == "Solar: 2.1 kW surplus → 9 A"
    solar.data = solar_on
    sensor = StatusSensor(solar, solar._entry)
    assert sensor.native_value == solar_on["status"]
    assert len(sensor.native_value) <= 255

    solar_wait = _coordinator(hass, ChargeMode.SOLAR)
    _chargeable(hass, **{PV: 0, GRID: 0, HOUSE: 400})
    assert (await solar_wait._async_update_data())["status"] == (
        "Solar: waiting for surplus"
    )

    cheap_wait = _coordinator(
        hass,
        ChargeMode.CHEAP,
        **{CONF_VALLEY_START: "00:00", CONF_VALLEY_END: "07:00"},
    )
    _chargeable(hass, **{PV: 0, GRID: 0})
    assert (await cheap_wait._async_update_data())["status"] == (
        "Cheap: waiting for valley at 00:00"
    )

    clock.moment = datetime(2026, 10, 21, 2, 0)
    cheap_valley = _coordinator(
        hass,
        ChargeMode.CHEAP,
        **{CONF_VALLEY_AMPS: 32, CONF_GRID_MAX_POWER: 19 * 240},
    )
    _chargeable(hass, **{GRID: 0, HOUSE: 0, SWITCH: "off", AMPS: 0, CHARGING: "Stopped"})
    assert (await cheap_valley._async_update_data())["status"] == (
        "Cheap: valley, 19 A (grid cap)"
    )

    clock.moment = datetime(2026, 10, 21, 12, 0)
    maxed = _coordinator(
        hass,
        ChargeMode.MAX_POWER,
        **{
            CONF_GRID_MAX_POWER: 3560,
            CONF_EV_POWER_SENSOR: "sensor.wall_connector_power",
            CONF_SHED_LOADS: [SHED_A, SHED_B],
        },
    )
    _chargeable(
        hass,
        **{GRID: 0, HOUSE: 0, SWITCH: "on", AMPS: 16, CHARGING: "Charging"},
    )
    hass.states.async_set(
        "sensor.wall_connector_power", "1.0", {"unit_of_measurement": "kW"}
    )
    _set(hass, **{SHED_A: "on", SHED_B: "on"})
    await maxed._async_update_data()
    clock.moment += timedelta(seconds=60)
    _set(hass, **{SHED_A: "off"})
    max_result = await maxed._async_update_data()
    assert max_result["status"] == "Max Power: 19 A (grid cap), shed 2 loads"

    off = _coordinator(hass, ChargeMode.OFF)
    _chargeable(hass)
    assert (await off._async_update_data())["status"] == "Off"

    unplugged = _coordinator(hass, ChargeMode.SOLAR)
    _chargeable(hass, **{CHARGING: "Disconnected"})
    assert (await unplugged._async_update_data())["status"] == "Car unplugged"

    full = _coordinator(hass, ChargeMode.MAX_POWER)
    _chargeable(hass, **{CHARGING: "Complete", EV_SOC: 80, LIMIT: 90})
    assert (await full._async_update_data())["status"] == "Car full / at limit"


@pytest.mark.parametrize(
    ("restored", "expected"),
    [
        ("Solar Only", ChargeMode.SOLAR),
        ("Min Solar + Grid Top-up", ChargeMode.SOLAR),
        ("Valley / Off-Peak", ChargeMode.CHEAP),
        ("Max Solar + Grid", ChargeMode.MAX_POWER),
        ("Max Power", ChargeMode.MAX_POWER),
        ("solar_only", ChargeMode.SOLAR),
        ("min_solar_topup", ChargeMode.SOLAR),
        ("valley", ChargeMode.CHEAP),
        ("max_solar_grid", ChargeMode.MAX_POWER),
        ("Nope", ChargeMode.SOLAR),
    ],
)
async def test_old_mode_labels_and_values_restore(
    hass: HomeAssistant,
    restored: str,
    expected: ChargeMode,
) -> None:
    _services(hass)
    mock_restore_cache(
        hass, [State("select.ev_charge_optimizer_charging_mode", restored)]
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_data(ChargeMode.MAX_POWER),
        options=_options(),
    )
    entry.add_to_hass(hass)
    _chargeable(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    state = hass.states.get("select.ev_charge_optimizer_charging_mode")
    assert coordinator.mode == expected
    assert state is not None
    assert state.state == MODE_LABELS[expected]


async def test_old_default_mode_value_migrates_without_a_restored_select(
    hass: HomeAssistant,
) -> None:
    _services(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_data("valley"),
        options=_options(),
    )
    entry.add_to_hass(hass)
    _chargeable(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.data[DOMAIN][entry.entry_id].mode == ChargeMode.CHEAP


async def test_enabled_switch_is_removed_from_the_registry(
    hass: HomeAssistant,
) -> None:
    _services(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data=_data(ChargeMode.SOLAR), options=_options()
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{entry.entry_id}_enabled",
        config_entry=entry,
    )
    assert (
        registry.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_enabled")
        is not None
    )
    _chargeable(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert (
        registry.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_enabled")
        is None
    )
    assert hass.states.get("switch.ev_charge_optimizer_enabled") is None
    assert not hasattr(hass.data[DOMAIN][entry.entry_id], "enabled")


async def test_solar_floor_and_ready_by_restore_or_default(
    hass: HomeAssistant,
) -> None:
    _services(hass)
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("number.ev_charge_optimizer_solar_floor_amps", "8"),
                {
                    "native_value": 8,
                    "native_max_value": 16,
                    "native_min_value": 0,
                    "native_step": 1,
                    "native_unit_of_measurement": "A",
                },
            ),
            (State("time.ev_charge_optimizer_ready_by", "06:15:00"), {}),
        ],
    )
    entry = MockConfigEntry(
        domain=DOMAIN, data=_data(ChargeMode.SOLAR), options=_options()
    )
    entry.add_to_hass(hass)
    _chargeable(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    floor = SolarFloorAmpsNumber(coordinator, entry)
    assert floor.native_min_value == 0
    assert floor.native_max_value == 16
    assert floor.native_step == 1
    assert coordinator.solar_floor_amps == 8
    assert coordinator.ready_by == time(6, 15)


def _marker(schema: vol.Schema, key: str):
    for marker in schema.schema:
        if getattr(marker, "schema", None) == key:
            return marker
    raise AssertionError(key)


async def test_options_drop_unused_keys_and_offer_ready_by_settings(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_data(ChargeMode.SOLAR),
        options={CONF_VALLEY_TARGET_SOC: 80, CONF_GUARANTEED_MIN_AMPS: 8},
    )
    entry.add_to_hass(hass)
    form = await hass.config_entries.options.async_init(entry.entry_id)
    keys = [getattr(marker, "schema", None) for marker in form["data_schema"].schema]
    assert CONF_VALLEY_TARGET_SOC not in keys
    assert CONF_GUARANTEED_MIN_AMPS not in keys
    for key in (
        CONF_EV_BATTERY_KWH,
        CONF_PEAK_HOURS,
        CONF_WEEKENDS_VALLEY,
        CONF_SOLAR_FORECAST_TODAY,
        CONF_SOLAR_FORECAST_TOMORROW,
    ):
        assert key in keys
    assert _marker(form["data_schema"], CONF_SOLAR_FORECAST_TODAY).default is (
        vol.UNDEFINED
    )


def _assist(**extra: object) -> dict[str, object]:
    return {
        CONF_GRID_MAX_POWER: 5500,
        CONF_BATTERY_MAX_DISCHARGE_W: 4600,
        CONF_INVERTER_AC_MAX_W: 5450,
        CONF_BATTERY_RESERVE_SOC: 10,
        CONF_BATTERY_POWER_SENSOR: BATTERY_POWER,
        CONF_BATTERY_SOC_SENSOR: BATTERY_SOC,
        CONF_STATIC_VOLTAGE: 240,
        **extra,
    }


def _idle_power(hass: HomeAssistant, **states: object) -> None:
    _chargeable(
        hass,
        **{
            PV: 0,
            GRID: 0,
            HOUSE: 0,
            BATTERY_POWER: -1000,
            BATTERY_SOC: 50,
            VOLTAGE: 240,
            SWITCH: "off",
            AMPS: 0,
            CHARGING: "Stopped",
            **states,
        },
    )


async def test_max_power_headroom_credits_battery_spare(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass, ChargeMode.MAX_POWER, **_assist())
    _idle_power(hass)

    assert coordinator._battery_spare_watts() == pytest.approx(3600)
    assert coordinator._grid_headroom_watts() == pytest.approx(5500 + 3600)


async def test_battery_spare_is_zero_when_unconfigured_or_below_reserve(
    hass: HomeAssistant,
) -> None:
    unconfigured = _coordinator(
        hass,
        ChargeMode.MAX_POWER,
        **{CONF_GRID_MAX_POWER: 5500, CONF_BATTERY_POWER_SENSOR: BATTERY_POWER},
    )
    _idle_power(hass, **{BATTERY_SOC: 80})
    assert unconfigured._battery_spare_watts() == 0

    inverter_only = _coordinator(
        hass,
        ChargeMode.MAX_POWER,
        **_assist(**{CONF_BATTERY_MAX_DISCHARGE_W: 0}),
    )
    assert inverter_only._battery_spare_watts() == 0

    discharge_only = _coordinator(
        hass,
        ChargeMode.MAX_POWER,
        **_assist(**{CONF_INVERTER_AC_MAX_W: 0}),
    )
    assert discharge_only._battery_spare_watts() == 0

    low = _coordinator(hass, ChargeMode.MAX_POWER, **_assist())
    _idle_power(hass, **{BATTERY_SOC: 9})
    assert low._battery_spare_watts() == 0
    _set(hass, **{BATTERY_SOC: 10})
    assert low._battery_spare_watts() == 0


async def test_other_modes_still_subtract_battery_discharge(
    hass: HomeAssistant,
) -> None:
    cheap = _coordinator(hass, ChargeMode.CHEAP, **_assist())
    _idle_power(hass)
    assert cheap._battery_spare_watts() == 0
    assert cheap._grid_headroom_watts() == pytest.approx(5500 - 1000)


async def test_overshoot_suspends_battery_spare_for_five_minutes(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _freeze(monkeypatch, datetime(2026, 10, 22, 12, 0))
    coordinator = _coordinator(hass, ChargeMode.MAX_POWER, **_assist())
    _idle_power(hass, **{GRID: -5600, SWITCH: "on", AMPS: 6, CHARGING: "Charging"})
    _services(hass)

    await coordinator._async_update_data()
    assert coordinator._battery_spare_watts() == pytest.approx(3600)

    clock.moment += timedelta(seconds=30)
    await coordinator._async_update_data()
    assert coordinator._battery_spare_watts() == 0

    clock.moment += timedelta(minutes=5) - timedelta(seconds=1)
    _set(hass, **{GRID: 0, SWITCH: "off", AMPS: 0, CHARGING: "Stopped"})
    await coordinator._async_update_data()
    assert coordinator._battery_spare_watts() == 0

    clock.moment += timedelta(seconds=1)
    await coordinator._async_update_data()
    assert coordinator._battery_spare_watts() == pytest.approx(3600)


async def test_battery_assist_follows_mode_and_car(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass, ChargeMode.SOLAR)
    entry = coordinator._entry
    _chargeable(hass)
    sensor = BatteryAssistBinarySensor(coordinator, entry)
    assert sensor.unique_id == f"{entry.entry_id}_battery_assist"
    assert sensor.name == "Battery Assist"
    assert sensor.is_on is False

    coordinator.mode = ChargeMode.MAX_POWER
    assert sensor.is_on is True
    _set(hass, **{CHARGING: "Complete"})
    assert sensor.is_on is False

    coordinator.mode = ChargeMode.BOOST
    _set(hass, **{CHARGING: "Charging"})
    assert sensor.is_on is True
    _set(hass, **{CHARGING: "Disconnected"})
    assert sensor.is_on is False


async def test_battery_assist_entity_id(
    hass: HomeAssistant,
) -> None:
    _services(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_data(ChargeMode.MAX_POWER),
        options=_options(),
    )
    entry.add_to_hass(hass)
    _chargeable(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.ev_charge_optimizer_battery_assist")
    assert state is not None
    assert state.state == "on"
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"{entry.entry_id}_battery_assist"
        )
        == "binary_sensor.ev_charge_optimizer_battery_assist"
    )


def _boost_cap(voltage: float, grid_max: float = 5500, factor: float = 1.08) -> float:
    return factor * (grid_max / 230) * voltage


async def test_boost_cap_watts_follow_live_voltage(
    hass: HomeAssistant,
) -> None:
    _services(hass)
    watts = {}
    for voltage in (220, 240):
        coordinator = _coordinator(
            hass,
            ChargeMode.BOOST,
            **{
                CONF_GRID_MAX_POWER: 5500,
                CONF_BOOST_FACTOR: 1.08,
                CONF_STATIC_VOLTAGE: voltage,
            },
        )
        _idle_power(hass, **{VOLTAGE: voltage, BATTERY_POWER: 0, BATTERY_SOC: 80})
        result = await coordinator._async_update_data()
        watts[voltage] = result["available_power"]
        assert result["available_power"] == pytest.approx(
            _boost_cap(voltage), abs=1
        )
    assert watts[220] != watts[240]
    assert watts[240] > watts[220]


async def test_boost_budget_accrues_only_above_contract_then_cools(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _freeze(monkeypatch, datetime(2026, 10, 22, 12, 0))
    _services(hass)
    coordinator = _coordinator(
        hass,
        ChargeMode.BOOST,
        **{
            CONF_GRID_MAX_POWER: 5500,
            CONF_BOOST_FACTOR: 1.08,
            CONF_BOOST_MAX_MINUTES: 50,
            CONF_BOOST_COOLDOWN_MINUTES: 60,
            CONF_STATIC_VOLTAGE: 240,
        },
    )
    _idle_power(hass, **{BATTERY_POWER: 0, VOLTAGE: 240})
    opened = await coordinator._async_update_data()
    assert opened["available_power"] == pytest.approx(_boost_cap(240), abs=1)

    clock.moment += timedelta(minutes=40)
    _set(hass, **{GRID: -5000})
    under = await coordinator._async_update_data()
    assert "40 min" not in under["status"]
    assert under["available_power"] == pytest.approx(
        _boost_cap(240) - 5000, abs=1
    )

    clock.moment += timedelta(minutes=10)
    _set(hass, **{GRID: -6000})
    over = await coordinator._async_update_data()
    assert over["status"] == "Boost: +1.9 A over contract, 40 min left"

    started = clock.moment
    clock.moment += timedelta(minutes=40)
    cooled = await coordinator._async_update_data()
    assert cooled["status"].startswith("Boost: cooling down")
    assert cooled["available_power"] == pytest.approx(0, abs=1)
    assert coordinator._boost_cooldown_until == started + timedelta(minutes=40 + 60)

    clock.moment = started + timedelta(minutes=40 + 19)
    _set(hass, **{GRID: 0})
    mid = await coordinator._async_update_data()
    assert mid["status"] == "Boost: cooling down, 41 min"
    assert mid["available_power"] == pytest.approx(5500, abs=1)

    clock.moment = started + timedelta(minutes=40 + 59)
    still = await coordinator._async_update_data()
    assert still["status"].startswith("Boost: cooling down")
    assert still["available_power"] == pytest.approx(5500, abs=1)


async def test_boost_budget_survives_restart(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _freeze(monkeypatch, datetime(2026, 10, 22, 12, 0))
    _services(hass)
    coordinator = _coordinator(
        hass,
        ChargeMode.BOOST,
        **{CONF_GRID_MAX_POWER: 5500, CONF_BOOST_MAX_MINUTES: 50},
    )
    _idle_power(hass, **{BATTERY_POWER: 0, VOLTAGE: 240, GRID: -6000})
    await coordinator._async_update_data()
    clock.moment += timedelta(minutes=10)
    accrued = await coordinator._async_update_data()
    assert accrued["status"] == "Boost: +1.9 A over contract, 40 min left"

    restarted = EVChargeOptimizerCoordinator(hass, coordinator._entry)
    restarted.mode = ChargeMode.BOOST
    kept = await restarted._async_update_data()
    assert kept["status"] == "Boost: +1.9 A over contract, 40 min left"


async def test_boost_hard_guard_backs_off_for_sixty_seconds(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _freeze(monkeypatch, datetime(2026, 10, 22, 12, 0))
    _services(hass)
    coordinator = _coordinator(
        hass,
        ChargeMode.BOOST,
        **{
            CONF_GRID_MAX_POWER: 5500,
            CONF_BOOST_FACTOR: 1.08,
            CONF_STATIC_VOLTAGE: 230,
        },
    )
    _idle_power(hass, **{VOLTAGE: 230, BATTERY_POWER: 0, GRID: -6100})
    breached = await coordinator._async_update_data()
    assert breached["status"] == "Boost: over 1.1×In, backing off"

    clock.moment += timedelta(seconds=30)
    _set(hass, **{GRID: 0})
    held = await coordinator._async_update_data()
    assert held["status"] == "Boost: over 1.1×In, backing off"
    assert held["available_power"] == pytest.approx(5500, abs=1)

    clock.moment += timedelta(seconds=31)
    resumed = await coordinator._async_update_data()
    assert resumed["available_power"] == pytest.approx(_boost_cap(230), abs=1)
    assert resumed["status"].startswith("Boost: +")


async def test_boost_sheds_when_the_boost_cap_is_binding(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch, datetime(2026, 10, 22, 12, 0))
    services = _services(hass)
    coordinator = _coordinator(
        hass,
        ChargeMode.BOOST,
        **{
            CONF_GRID_MAX_POWER: 5500,
            CONF_EV_POWER_SENSOR: "sensor.wall_connector_power",
            CONF_SHED_LOADS: [SHED_A],
            CONF_STATIC_VOLTAGE: 240,
        },
    )
    _chargeable(
        hass,
        **{GRID: -4000, HOUSE: 0, PV: 0, SWITCH: "on", AMPS: 16, CHARGING: "Charging"},
    )
    hass.states.async_set(
        "sensor.wall_connector_power", "1.0", {"unit_of_measurement": "kW"}
    )
    _set(hass, **{SHED_A: "on"})

    result = await coordinator._async_update_data()

    assert result["target_amps"] < 32
    assert result["shed_loads"] == [SHED_A]
    assert _calls(services.turn_off, SHED_A)


async def test_supply_and_boost_options_are_in_config_and_options(
    hass: HomeAssistant,
) -> None:
    user = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    charger = await hass.config_entries.flow.async_configure(
        user["flow_id"],
        {
            CONF_GRID_EXPORT_SENSOR: GRID,
            CONF_SOLAR_PRODUCTION_SENSOR: PV,
            CONF_HOUSE_CONSUMPTION_SENSOR: HOUSE,
        },
    )
    voltage = await hass.config_entries.flow.async_configure(
        charger["flow_id"],
        {CONF_CHARGER_NUMBER_ENTITY: AMPS},
    )
    basics = await hass.config_entries.flow.async_configure(voltage["flow_id"], {})
    basics_keys = [
        getattr(marker, "schema", None) for marker in basics["data_schema"].schema
    ]
    for key in (
        CONF_BATTERY_MAX_DISCHARGE_W,
        CONF_INVERTER_AC_MAX_W,
        CONF_BATTERY_RESERVE_SOC,
        CONF_BOOST_FACTOR,
        CONF_BOOST_MAX_MINUTES,
        CONF_BOOST_COOLDOWN_MINUTES,
    ):
        assert key in basics_keys

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_data(),
        options=_options(),
    )
    entry.add_to_hass(hass)
    form = await hass.config_entries.options.async_init(entry.entry_id)
    option_keys = [
        getattr(marker, "schema", None) for marker in form["data_schema"].schema
    ]
    for key in (
        CONF_BATTERY_MAX_DISCHARGE_W,
        CONF_INVERTER_AC_MAX_W,
        CONF_BATTERY_RESERVE_SOC,
        CONF_BOOST_FACTOR,
        CONF_BOOST_MAX_MINUTES,
        CONF_BOOST_COOLDOWN_MINUTES,
    ):
        assert key in option_keys


def test_valley_window_end_is_exclusive() -> None:
    from datetime import time as dtime

    from custom_components.ev_charge_optimizer.ready_by import in_valley_window

    start, end = dtime(23, 59), dtime(8, 0)
    assert in_valley_window(dtime(7, 59), start, end)
    assert not in_valley_window(dtime(8, 0), start, end)
    assert in_valley_window(dtime(0, 0), dtime(0, 0), dtime(8, 0))
    assert not in_valley_window(dtime(8, 0), dtime(0, 0), dtime(8, 0))


async def test_boost_without_a_mains_reading_assumes_low_voltage(
    hass: HomeAssistant,
) -> None:
    _services(hass)
    coordinator = _coordinator(
        hass,
        ChargeMode.BOOST,
        **{CONF_GRID_MAX_POWER: 5500, CONF_BOOST_FACTOR: 1.08, CONF_STATIC_VOLTAGE: 240},
    )
    _idle_power(hass, **{VOLTAGE: 1.55, BATTERY_POWER: 0, BATTERY_SOC: 80})

    result = await coordinator._async_update_data()

    assert result["available_power"] == pytest.approx(_boost_cap(215), abs=1)


async def test_reentering_boost_does_not_count_time_spent_in_other_modes(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _freeze(monkeypatch, datetime(2026, 10, 22, 12, 0))
    _services(hass)
    coordinator = _coordinator(
        hass, ChargeMode.BOOST, **{CONF_GRID_MAX_POWER: 5500, CONF_BOOST_MAX_MINUTES: 50}
    )
    _idle_power(hass, **{BATTERY_POWER: 0, VOLTAGE: 240, GRID: -6000})
    await coordinator._async_update_data()

    coordinator.mode = ChargeMode.MAX_POWER
    clock.moment += timedelta(minutes=5)
    await coordinator._async_update_data()

    coordinator.mode = ChargeMode.BOOST
    clock.moment += timedelta(hours=3)
    back = await coordinator._async_update_data()

    assert back["status"] == "Boost: +1.9 A over contract, 50 min left"


async def test_no_battery_spare_while_the_battery_is_charging(
    hass: HomeAssistant,
) -> None:
    coordinator = _coordinator(hass, ChargeMode.MAX_POWER, **_assist())
    _idle_power(hass, **{BATTERY_POWER: 500})

    assert coordinator._battery_spare_watts() == 0


async def test_max_power_ramps_up_by_max_step_and_starts_on_grid_only(
    hass: HomeAssistant,
) -> None:
    _services(hass)
    coordinator = _coordinator(hass, ChargeMode.MAX_POWER, **_assist(**{CONF_MAX_STEP: 2}))
    _idle_power(hass, **{BATTERY_POWER: 0, HOUSE: 3500, GRID: -3500})

    first = await coordinator._async_update_data()
    assert first["target_amps"] == round((5500 - 3500) / 240)

    second = await coordinator._async_update_data()
    assert second["target_amps"] == round((5500 - 3500) / 240) + 2

    _set(hass, **{GRID: -9000})
    dropped = await coordinator._async_update_data()
    assert dropped["target_amps"] < second["target_amps"] - 2
