"""Parsing for the Van Controller serial protocol. No I/O here, so it is easy to test.

The controller echoes every character it receives, and its replies are lines of
the form ``NAME=ON``, ``NAME=OFF`` or ``KEY=1234mV``, usually with a blank line
in front. Anything that does not match one of the recognised forms (echoed
commands, HELP text) is ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VALUE_RE = re.compile(r"^([A-Za-z0-9_\-]+)=(ON|OFF|\d+mV)$")
_BANNER_RE = re.compile(r"^Van Control v(\S+)$")
_UNRECOGNISED = "Unrecognised command"


@dataclass(frozen=True)
class Output:
    """State of one switched output, from READ_ALL, READ_<NAME> or the power-up report."""
    name: str
    on: bool


@dataclass(frozen=True)
class Analog:
    """One analogue reading in millivolts at the MCU pin (VLB, VSB or TEMP)."""
    name: str
    mv: int


@dataclass(frozen=True)
class Banner:
    """Start-up banner: the controller has just reset."""
    version: str


@dataclass(frozen=True)
class Unrecognised:
    """The controller rejected the last command."""


Message = Output | Analog | Banner | Unrecognised


def parse_line(line: str) -> Message | None:
    """Turn one received line into a message, or None if it carries no data."""
    line = line.strip()
    if not line:
        return None
    if m := _BANNER_RE.match(line):
        return Banner(m.group(1))
    if line.startswith(_UNRECOGNISED):
        return Unrecognised()
    m = _VALUE_RE.match(line)
    if not m:
        return None
    key, value = m.groups()
    if value in ("ON", "OFF"):
        return Output(key, value == "ON")
    return Analog(key, int(value[:-2]))


class LineSplitter:
    """Collects received bytes and returns complete, non-empty lines.

    Lines end with CR or LF. A partial line is kept until the rest arrives.
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, data: bytes) -> list[str]:
        self._buf += data.decode("ascii", errors="replace")
        parts = re.split(r"[\r\n]", self._buf)
        self._buf = parts.pop()
        return [p for p in parts if p]
