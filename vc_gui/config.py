"""Loads config.toml: serial port, poll rate, output labels and analogue scaling."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AnalogChannel:
    key: str                 # firmware name: VLB, VSB or TEMP
    label: str
    unit: str
    decimals: int = 2
    kind: str = "divider"    # "divider" or "linear"
    ratio: float = 1.0       # divider: input volts / pin volts
    offset_mv: float = 0.0   # linear: pin mV at zero
    mv_per_unit: float = 1.0 # linear: pin mV per unit (e.g. per °C)

    def convert(self, mv: int) -> float:
        if self.kind == "linear":
            return (mv - self.offset_mv) / self.mv_per_unit
        return mv / 1000.0 * self.ratio


@dataclass
class Config:
    port: str | None = None
    poll_interval: float = 1.0
    labels: dict[str, str] = field(default_factory=dict)
    toggle_output: str = "BLK4-3"
    toggle_label: str = "PB5 toggle"
    analog: list[AnalogChannel] = field(default_factory=lambda: [
        AnalogChannel("VLB", "Leisure battery", "V"),
        AnalogChannel("VSB", "Starter battery", "V"),
        AnalogChannel("TEMP", "Temperature", "°C", 1, "linear", offset_mv=500, mv_per_unit=10),
    ])


def load(path: Path) -> Config:
    cfg = Config()
    if not path.exists():
        return cfg
    with path.open("rb") as f:
        data = tomllib.load(f)

    serial = data.get("serial", {})
    cfg.port = serial.get("port") or None
    cfg.poll_interval = float(serial.get("poll_interval_s", cfg.poll_interval))

    cfg.labels = {str(k): str(v) for k, v in data.get("labels", {}).items()}

    toggle = data.get("toggle_button", {})
    cfg.toggle_output = toggle.get("output", cfg.toggle_output)
    cfg.toggle_label = toggle.get("label", cfg.toggle_label)

    if "analog" in data:
        defaults = {ch.key: ch for ch in Config().analog}
        cfg.analog = []
        for key, ch in data["analog"].items():
            base = defaults.get(key, AnalogChannel(key, key, "mV"))
            cfg.analog.append(AnalogChannel(
                key=key,
                label=ch.get("label", base.label),
                unit=ch.get("unit", base.unit),
                decimals=int(ch.get("decimals", base.decimals)),
                kind=ch.get("kind", base.kind),
                ratio=float(ch.get("ratio", base.ratio)),
                offset_mv=float(ch.get("offset_mV", base.offset_mv)),
                mv_per_unit=float(ch.get("mV_per_unit", base.mv_per_unit)),
            ))
    return cfg
