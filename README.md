# EV Charge Optimizer

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A Home Assistant integration that optimizes EV charger power based on available solar export, grid conditions, and time-of-use rates. Entity-based and charger-agnostic — works with any EV charger that exposes a number entity in Home Assistant.

## Features

- **Solar Only** — Charge exclusively from surplus solar power. Ramps amps up/down based on export, stops when insufficient.
- **Max Solar + Grid** — Charge at maximum amps regardless of solar availability.
- **Valley / Off-Peak** — Schedule charging at a fixed rate during configurable off-peak hours. Reverts to Solar Only outside the window.
- **Min Solar + Grid Top-up** — Use solar surplus when available, with a guaranteed minimum charge rate from the grid.

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
| Valley Start/End | 23:00–07:00 | Off-peak time window for Valley mode |
| Valley Amps | 16A | Charging rate during Valley hours |
| Guaranteed Min Amps | 8A | Minimum amps for Min Solar + Grid Top-up mode |

## Entities

The integration creates the following entities:

| Entity | Type | Description |
|--------|------|-------------|
| Charging Mode | Select | Choose between Solar Only, Max Solar + Grid, Valley, Min Solar + Grid Top-up |
| Enabled | Switch | Master on/off for the optimizer |
| Target Amps | Sensor | Current amperage target being sent to the charger |
| Available Power | Sensor | Calculated surplus power available for charging (W) |
| Minimum Amps | Number | Below this threshold, charging stops |
| Maximum Amps | Number | Upper amperage cap |

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
