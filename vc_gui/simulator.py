"""A software stand-in for the Van Controller, for tests and ``run.py --demo``.

It has the same methods as the parts of ``serial.Serial`` the link uses, and
copies the firmware's behaviour: character echo, the start-up banner and
power-up report, and the READ_*, _ON, _OFF and _TOGGLE commands.
"""

from __future__ import annotations

import random
import threading
import time

POWER_ON = [
    ("BLK7-5", True), ("BLK6-3", False), ("BLK4-3", False), ("BLK4-15", True),
    ("BLK4-9", True), ("BLK4-6", True), ("12V_HS2", True), ("12V_HS1", True),
]
ANALOG_MV = {"VLB": 2874, "VSB": 2810, "TEMP": 742}
CMD_MAX = 23


class SimulatedController:
    def __init__(self, port: str | None = None, timeout: float = 0.02,
                 version: str = "1.0") -> None:
        self.timeout = timeout
        self.version = version
        self.is_open = True
        self._lock = threading.Lock()
        self._out = bytearray()
        self._cmd = ""
        self.outputs: dict[str, bool] = {}
        self.reset()

    # -- Things a test or the demo can do to the "board" --

    def reset(self) -> None:
        """Power-on reset: restore defaults and send the banner and pin states."""
        with self._lock:
            self.outputs = dict(POWER_ON)
            self._cmd = ""
            self._send(f"\r\nVan Control v{self.version}\r\n")
            self._send_states()

    def press_button(self) -> None:
        """The physical PB5 button: toggles BLK4-3."""
        with self._lock:
            self.outputs["BLK4-3"] = not self.outputs["BLK4-3"]

    # -- serial.Serial interface --

    @property
    def in_waiting(self) -> int:
        with self._lock:
            return len(self._out)

    def read(self, size: int = 1) -> bytes:
        deadline = time.monotonic() + self.timeout
        while True:
            with self._lock:
                if self._out:
                    data = bytes(self._out[:size])
                    del self._out[:size]
                    return data
            if time.monotonic() >= deadline:
                return b""
            time.sleep(0.002)

    def write(self, data: bytes) -> int:
        with self._lock:
            for ch in data.decode("ascii"):
                self._send(ch)  # echo
                if ch in "\r\n":
                    if self._cmd:
                        self._process(self._cmd)
                    self._cmd = ""
                elif len(self._cmd) < CMD_MAX:
                    self._cmd += ch
        return len(data)

    def close(self) -> None:
        self.is_open = False

    # -- Firmware behaviour (called with the lock held) --

    def _send(self, text: str) -> None:
        self._out += text.encode("ascii")

    def _send_states(self) -> None:
        for i, (name, on) in enumerate(self.outputs.items()):
            self._send(f"{'\r\n' if i == 0 else ''}{name}={'ON' if on else 'OFF'}\r\n")

    def _process(self, cmd: str) -> None:
        if cmd == "READ_ALL":
            self._send_states()
            return
        for key, mv in ANALOG_MV.items():
            if cmd == f"READ_{key}":
                self._send(f"\r\n{key}={max(0, mv + random.randint(-6, 6))}mV\r\n")
                return
        if cmd == "HELP":
            self._send("\r\n--- Van Control Commands ---\r\n")
            return
        for name, on in self.outputs.items():
            if cmd == f"{name}_ON":
                self.outputs[name] = True
                return
            if cmd == f"{name}_OFF":
                self.outputs[name] = False
                return
            if cmd == f"{name}_TOGGLE":
                self.outputs[name] = not on
                return
            if cmd == f"READ_{name}":
                self._send(f"\r\n{name}={'ON' if on else 'OFF'}\r\n")
                return
        self._send("\r\nUnrecognised command. Use HELP to list valid commands.\r\n")
