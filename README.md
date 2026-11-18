# Van Control GUI

A desktop app for the [Van Control](../Van-Control) board. It talks to the board over its serial port (38400 8N1). It:

- shows the state of every switched output, with a switch to turn each one on or off;
- has a momentary **PB5 toggle** button, which does the same as pressing the board's PB5 button (inverts `BLK4-3`);
- shows the leisure battery, starter battery and temperature readings;
- polls the board once a second, so changes made on the board itself (such as the PB5 button) appear within about a second;
- shows when the board last reset (power-up, reset button or the hardware watchdog).

Needs Van Control firmware with the `<NAME>_TOGGLE` command, which was added in Van-Control commit `b8e3a19`.

## Setup (macOS)

The Python that comes with macOS (3.9, with Tk 8.5) is too old. Use the Homebrew one:

```sh
brew install python@3.13 python-tk@3.13
cd Van-Control-GUI
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Running

```sh
.venv/bin/python run.py          # real board
.venv/bin/python run.py --demo   # simulated board, no hardware needed
```

On start-up the app connects to `port` from `config.toml`. If that is empty, it uses the first `/dev/cu.usbserial-*` or `/dev/cu.usbmodem-*` port. Use the port list, ↻ and **Connect/Disconnect** to change ports. If the adapter is unplugged, the app keeps retrying and reconnects by itself when it's plugged back in.

### Reading the window

| What you see | Meaning |
|---|---|
| Green switch | Output ON |
| Grey switch | Output OFF |
| Amber switch | Change sent, waiting for the next poll to confirm it. If the board didn't change, the switch goes back. |
| Pale, unclickable switches | Not connected |
| Grey analogue value | No new reading for over 3 s |
| Status dot | Green: connected. Amber: port open but the board isn't replying. Red: disconnected. |

### Buttons and the commands they send

Each switch is labelled with its function name and, in brackets, the output's connector pin name. The command sent uses the pin name, which is the name the firmware knows. Turning a switch on sends `<PIN>_ON`, and turning it off sends `<PIN>_OFF`.

| Switch | Pin name | Switch on | Switch off |
|---|---|---|---|
| PUMP | `BLK7-5` | `BLK7-5_ON` | `BLK7-5_OFF` |
| SPARE | `BLK6-3` | `BLK6-3_ON` | `BLK6-3_OFF` |
| OUTSIDE | `BLK4-3` | `BLK4-3_ON` | `BLK4-3_OFF` |
| LIGHT3 | `BLK4-15` | `BLK4-15_ON` | `BLK4-15_OFF` |
| LIGHT2 | `BLK4-9` | `BLK4-9_ON` | `BLK4-9_OFF` |
| LIGHT1 | `BLK4-6` | `BLK4-6_ON` | `BLK4-6_OFF` |
| CRITICAL | `12V_HS1` | `12V_HS1_ON` | `12V_HS1_OFF` |
| NON_CRITICAL | `12V_HS2` | `12V_HS2_ON` | `12V_HS2_OFF` |

| Button | Command |
|---|---|
| PB5 toggle – OUTSIDE (BLK4-3) | `BLK4-3_TOGGLE` (inverts the output, same as pressing PB5 on the board) |

The function names come from `[labels]` in `config.toml`, and the toggle button's output from `[toggle_button]`. If you change those, the commands follow the pin names, not the labels.

Every poll also sends `READ_ALL`, `READ_VLB`, `READ_VSB` and `READ_TEMP` to read the output states and analogue values.

## Configuration: `config.toml`

- `[serial]`: the port (empty means auto-detect) and the poll interval.
- `[labels]`: function names for outputs, for example `"BLK7-5" = "PUMP"`, shown as "PUMP (BLK7-5)". Output names and order come from the board, so if the firmware renames an output, the GUI still works. Only the label needs updating.
- `[toggle_button]`: which output the momentary button toggles, and its label.
- `[analog.*]`: converts the board's millivolt readings into real units.
  - `kind = "divider"`: volts = reported volts × `ratio`.
  - `kind = "linear"`: value = (reported mV − `offset_mV`) ÷ `mV_per_unit`.

> **Battery readings are calibrated in the firmware** (version 1.1 and later), so `VLB` and `VSB` arrive as battery millivolts and their `ratio` stays 1.0. Recalibrate in the firmware, not here. With firmware 1.0 the batteries show the pin voltage instead.
>
> **The temperature scaling is a placeholder.** It assumes a TMP36-style sensor (500 mV at 0 °C, 10 mV/°C), reading the pin voltage. Replace it with the real sensor details.

## How it works

| File | Role |
|---|---|
| `vc_gui/protocol.py` | Parses replies: `NAME=ON/OFF`, `KEY=nnnnmV`, the start-up banner and the "Unrecognised command" reply. Echoed text is ignored. |
| `vc_gui/link.py` | Background thread that owns the serial port. Each poll sends `READ_ALL`, `READ_VLB`, `READ_VSB` and `READ_TEMP`. Switch commands are sent between polls, and a poll follows straight away. |
| `vc_gui/app.py` | The Tk window. It only receives events from the link through a queue and never touches the serial port. |
| `vc_gui/simulator.py` | A software copy of the board's serial behaviour, used by `--demo` and the tests. |
| `vc_gui/config.py` | Loads `config.toml`. |

The link sends **one command at a time** and waits for the reply, with a 1 ms gap between characters. This is because the firmware echoes characters and sends `READ_ALL` from inside its UART receive interrupt, so a fast stream of commands could lose characters.

## Tests

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

`tests/test_link.py` runs the real serial worker against the simulator. It covers polling, switching, the toggle command, a PB5 press on the board, reset detection and an unknown command.
