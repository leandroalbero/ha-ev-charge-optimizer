# EV Charge Optimizer

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A Home Assistant integration that optimizes EV charger power based on available solar export, grid conditions, and time-of-use rates. Entity-based and charger-agnostic — works with any EV charger that exposes a number entity in Home Assistant.

## Features

- **Off** — Target 0. The charger switch is turned off (no wake, no amp writes) and any shed loads are restored.
- **Solar** — Charge from surplus solar only. Optional Solar Floor Amps (0–16 A) raises the target to `min(max(solar, floor), grid headroom)`. Floor 0 is pure solar. Home-battery minimum SOC still gates this mode.
- **Cheap** — During the valley window, or all weekend when "weekends are valley" is on, charge at Valley Amps capped by grid headroom. Outside that, surplus solar only (no floor).
- **Ready By** — Finish the Maximum EV Charge slider by a clock time, buying the cheapest hours first (valley, then shoulder, then peak). Solar surplus is used in the other hours. See below.
- **Max Power** — Charge at the maximum amps the grid cap allows, plus inverter supply when battery assist is configured. Load shedding stays in this mode and in Boost.
- **Boost** — Same as Max Power, but the grid cap is a current above the contract for a bounded budget. See below. The only risk on 2.0TD is a trip; there is no excess-power penalty.

<img width="1305" height="951" alt="image" src="https://github.com/user-attachments/assets/de8416b0-2e2d-4d6e-928d-fe7e8e8faff4" />

### Regulation Algorithm

- Rolling average smoothing to handle cloud cover and transient loads
- Configurable power buffer (hysteresis) to prevent oscillation
- Step-limited amp changes per cycle for smooth ramping
- Automatic watts-to-amps conversion using a voltage sensor or static value

## Important: Tesla API Usage

> **Warning:** If you control your Tesla charger via the Tesla Fleet API (cloud), this integration will consume API calls each time it adjusts the charging amps. With a 30-second update interval, this can add up quickly and may hit rate limits.
>
> **Recommended:** Use an ESP32 with Bluetooth to control your Tesla locally, avoiding cloud API calls entirely. The [esphome-tesla-ble](https://github.com/yoziru/esphome-tesla-ble) project provides an ESPHome component that communicates directly with your Tesla over BLE. This gives you a local `number` entity to control charging amps with zero API usage.

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add this repository URL with category **Integration**
4. Search for "EV Charge Optimizer" and install
5. Restart Home Assistant

### Manual

1. Download the `custom_components/ev_charge_optimizer` folder
2. Copy it to your `config/custom_components/` directory
3. Restart Home Assistant

## Configuration

After installation, add the integration via **Settings → Devices & Services → Add Integration → EV Charge Optimizer**.

### Setup Steps

1. **Power Sensors** — Select your grid export, solar production, and house consumption sensor entities
2. **Charger Control** — Select the number entity that controls your charger's amperage
3. **Voltage** — Optionally select a voltage sensor from your inverter, or set a static value (240V / 120V)
4. **Basic Settings** — Set minimum/maximum amps and default charging mode

### Tuning (Options)

After setup, configure advanced parameters via the integration's options:

| Parameter | Default | Description |
|-----------|---------|-------------|
| Update Interval | 30s | How often the regulation loop runs |
| Power Buffer | 100W | Hysteresis to prevent amp toggling at the boundary |
| Rolling Average Window | 5 | Number of readings to average before adjusting |
| Max Step Change | 2A | Maximum amp change per regulation cycle |
| Valley Start/End | 23:00–07:00 | Off-peak window for Cheap, and the valley price class for Ready By |
| Valley Amps | 16A | Charging rate during Cheap valley hours |
| Weekends are valley | on | Saturday and Sunday count as valley for Cheap and Ready By |
| EV battery size | 60 kWh | Used by Ready By to turn a SOC gap into kWh |
| Peak hours | 10-14,18-22 | Local hour ranges `[start, end)` that Ready By treats as peak |
| Solar forecast today/tomorrow | — | Optional Solcast sensors. `detailedHourly` credits expected surplus before the deadline |
| EV Charging State Sensor | — | Optional. Car's charging state (e.g. `Charging`, `Complete`, `Disconnected`). Stops regulation when the car is full or unplugged, and stops counting the amp setpoint as draw while the car is idle |
| EV Charge Limit Entity | — | Optional. Car's charge limit (%). With the EV SoC sensor, charging stops once SoC reaches it |
| EV Power Sensor | — | Optional. Actual EV draw (`kW` or `W`). Grid headroom uses this instead of the amp setpoint. Without it, the setpoint counts only while the car is drawing |
| Battery power positive when discharging | off | Optional. Enable for inverters whose battery power is positive while discharging (e.g. GoodWe). Default keeps positive = charging |
| Loads to shed | — | Optional, Max Power only. Ordered switches, lights, or input booleans turned off one at a time while the grid cap is binding. Only loads this integration turned off are restored |
| Max shed minutes | 120 | Longest a shed load stays off. After that, loads come back on and shedding waits 30 minutes |
| Battery max discharge | 0 W | Extra discharge Max Power and Boost may count as supply. 0 turns the credit off |
| Inverter AC max | 0 W | Combined PV + battery AC limit of the inverter. 0 turns the credit off |
| Battery reserve SOC | 10% | No inverter credit at or below this home-battery SOC |
| Boost factor | 1.08 | Allowed import current as a multiple of In (contract W / 230 V) |
| Boost budget | 50 min | Minutes of import current above In before Boost falls back to the contract |
| Boost cool-down | 60 min | Rest after the budget is used, during which Boost uses the contract cap |

When the car cannot charge (full, unplugged, at its charge limit, or at the Maximum EV Charge slider) the charger switch is turned off (retried at most every 5 minutes), amps are not written and the car is not woken. The wake button is pressed at most once every 15 minutes. Surplus never exceeds solar production minus household load, so home-battery discharge exported for a few seconds is not mistaken for solar.

In Cheap and Ready By (while a chosen slot is active), grid headroom is `grid cap − live import − battery discharge + battery charge taken from the grid + measured EV draw`. A home battery discharging into the house is not treated as spare grid capacity, and a charger setpoint the car is not actually pulling is not treated as draw. Battery charge is yieldable only up to live import, so the EV can take grid power that was charging the home battery, but not solar that was.

### Max Power supply

Max Power is capped at the grid contract plus whatever the inverter can still supply, not at a fixed 5.5 kW. With `battery_max_discharge_w` and `inverter_ac_max_w` both 0 the feature is off and Max Power keeps the headroom above. Once both are set, and home-battery SoC is above `battery_reserve_soc` (default 10%), headroom is `grid cap − live import + measured EV + battery spare`. Spare is `max(0, min(battery max discharge, inverter AC max − PV) − battery discharge already underway)`. If live import stays above the active cap for two cycles, spare is suspended for 5 minutes. The grid cap remains the hard limit.

### Boost

Boost behaves like Max Power (inverter supply, shedding, Battery Assist on) but the grid cap is current-based. The smart meter's integrated ICP follows the ICP-M curve in UNE 20317:2015:

- Int = 1.1·In: no trip for 1 hour from cold
- It ≈ 1.55·In: trip in under 900 s
- 2.475·In: trip in 1–60 s
- 5·In: instant

In = contract W / 230 V (5500 W → 23.9 A). Allowed import current while the budget lasts is `boost_factor` × In (default 1.08), converted to watts with the live mains voltage. Seconds with import current above In are counted in a rolling 2 hour window and kept across restarts. After `boost_max_minutes` (default 50) the cap falls back to the plain contract and cools down for `boost_cooldown_minutes` (default 60). Any cycle above 1.10·In drops to the contract cap for that cycle and the next 60 seconds. Status reports `Boost: +1.9 A over contract, 40 min left`, `Boost: cooling down, 41 min`, or `Boost: over 1.1×In, backing off`.

In Max Power and Boost, while that headroom is below max amps, the optimizer can turn off the next still-on load in the shed list (at most one per minute). Those loads are turned back on when the mode changes, the mode is Off, the car cannot charge, the EV draws nothing for three cycles, the shed reaches its max minutes, or Home Assistant restarts. A shed that hits the time limit is not repeated for 30 minutes.

### Ready By

Set the Ready By time (default 07:30) and the Maximum EV Charge slider. Each cycle the integration computes the energy still needed:

`need_kWh = max(0, (target − SOC) / 100 × battery_kWh) / 0.9`

If SOC is missing it behaves like Cheap. If need is already 0 it stays on solar surplus. Otherwise it builds hourly slots from now until the next occurrence of the Ready By time (tomorrow if that time has passed), prices each hour valley < shoulder < peak, and takes the cheapest hours until they cover the need. A Solcast `detailedHourly` forecast, if configured, reduces that need by the expected surplus (70% of `pv_estimate − house load`, capped at the charger). If even every hour is not enough, it charges in all of them and the Status sensor reports the shortfall.

The charger runs at full grid-headroom amps only while the current hour was chosen. Every other hour is solar surplus. The plan is recomputed every cycle, so charging stops on its own once the need is met.

## Entities

The integration creates the following entities:

| Entity | Type | Description |
|--------|------|-------------|
| Charging Mode | Select | Off, Solar, Cheap, Ready By, Max Power, or Boost |
| Battery Assist | Binary sensor | On while Max Power or Boost is selected and the car can charge. Point the MPC `deferrable_allow_battery_entity_id` at `binary_sensor.ev_charge_optimizer_battery_assist` |
| Status | Sensor | One line for the active mode. Ready By also exposes deadline, need, chosen slots, shortfall, and projected SOC |
| Ready By | Time | Deadline for Ready By. Restored. Default 07:30 |
| Solar Floor Amps | Number | 0–16 A floor for Solar. 0 is pure solar. Restored |
| Target Amps | Sensor | Current amperage target being sent to the charger |
| Available Power | Sensor | Calculated surplus power available for charging (W). In grid modes this is contract headroom, capped at max amps |
| EV Power | Sensor | Measured EV draw (W), updated every cycle, including 0 when not charging |
| Shed Loads | Sensor | Count of loads this optimizer has turned off. Attribute `shed_loads` lists the entity ids |
| Minimum Amps | Number | Below this threshold, charging stops |
| Maximum Amps | Number | Upper amperage cap |
| Maximum EV Charge | Number | Slider (0–100%) persisted across restarts. Charging stops at the lower of this and the car's charge limit |

## How It Works

```
Solar Panel → Inverter → Grid Export Sensor
                                ↓
                    EV Charge Optimizer
                    (rolling avg + buffer)
                                ↓
                    Calculate target amps
                    (mode-specific logic)
                                ↓
                    Clamp + step limit
                                ↓
                    Write to charger entity
```

Each regulation cycle:
1. Reads current sensor values (grid export, solar, consumption, voltage)
2. Adds to rolling average window
3. Subtracts power buffer from averaged export
4. Converts available watts to amps using voltage
5. Applies mode-specific logic
6. Clamps to min/max range and limits step size
7. Writes the target to the charger's number entity

## License

MIT
