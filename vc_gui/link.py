"""Background serial worker that talks to the Van Controller.

One thread owns the serial port. It sends one command at a time and waits for
the reply before sending the next: the firmware handles echo and READ_ALL inside
its receive interrupt, so commands sent back to back can lose characters.

The GUI calls :meth:`ControllerLink.send` and :meth:`ControllerLink.set_port`,
and reads events from :attr:`ControllerLink.events`. Nothing in this module
touches Tk.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import serial
from serial.tools import list_ports

from .protocol import Analog, Banner, LineSplitter, Message, Output, Unrecognised, parse_line

BAUD = 38400
READ_TIMEOUT_S = 0.02      # serial read timeout: how long one read() may block
REPLY_TIMEOUT_S = 0.3      # give up waiting for a reply after this long
BURST_GAP_S = 0.06         # a reply is complete after this long with no new data
CHAR_DELAY_S = 0.001       # gap between sent characters, so the firmware can keep up
ANALOG_KEYS = ("VLB", "VSB", "TEMP")


# ---- Events posted to the GUI ------------------------------------------------

@dataclass(frozen=True)
class StatusEvent:
    """Link state. ``state`` is "closed", "waiting" (port open, no reply yet) or "ok"."""
    state: str
    detail: str = ""


@dataclass(frozen=True)
class ValueEvent:
    """A fresh output state or analogue reading. ``t`` is time.monotonic()."""
    message: Output | Analog
    t: float


@dataclass(frozen=True)
class SentEvent:
    """A user command has been sent and the controller has finished handling it."""
    command: str


@dataclass(frozen=True)
class ResetEvent:
    """The controller sent its start-up banner. ``wall_time`` is time.time()."""
    version: str
    wall_time: float


@dataclass(frozen=True)
class ErrorEvent:
    text: str


Event = StatusEvent | ValueEvent | SentEvent | ResetEvent | ErrorEvent


# ---- Port discovery ----------------------------------------------------------

def find_ports() -> list[str]:
    return sorted(p.device for p in list_ports.comports())


def default_port(ports: list[str]) -> str | None:
    """Pick the most likely USB-serial adapter, if any."""
    for p in ports:
        if "usbserial" in p or "usbmodem" in p:
            return p
    return None


# ---- Worker ------------------------------------------------------------------

def _open_serial(port: str):
    return serial.Serial(port, BAUD, timeout=READ_TIMEOUT_S)


class ControllerLink:
    def __init__(self, poll_interval: float = 1.0,
                 serial_factory: Callable[[str], object] = _open_serial) -> None:
        self.events: queue.Queue[Event] = queue.Queue()
        self._commands: queue.Queue[str] = queue.Queue()
        self._poll_interval = poll_interval
        self._factory = serial_factory
        self._port: str | None = None
        self._port_changed = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._ser = None
        self._splitter = LineSplitter()
        self._outputs: set[str] = set()
        self._status: tuple[str, str] | None = None
        self._thread = threading.Thread(target=self._run, name="vc-link", daemon=True)

    # -- Called from the GUI thread --

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def set_port(self, port: str | None) -> None:
        """Connect to ``port``, or disconnect if None. Takes effect on the worker thread."""
        with self._lock:
            self._port = port
        self._port_changed.set()

    def send(self, command: str) -> None:
        self._commands.put(command)

    # -- Worker thread --

    def _run(self) -> None:
        next_poll = 0.0
        while not self._stop.is_set():
            if self._port_changed.is_set():
                self._port_changed.clear()
                self._close()
            if self._ser is None:
                if not self._open():
                    self._stop.wait(1.0)
                    continue
                next_poll = 0.0
            try:
                try:
                    cmd = self._commands.get_nowait()
                except queue.Empty:
                    cmd = None
                if cmd is not None:
                    self._command(cmd)
                    # Poll straight away so the GUI sees the result quickly.
                    next_poll = min(next_poll, time.monotonic() + 0.05)
                elif time.monotonic() >= next_poll:
                    next_poll = time.monotonic() + self._poll_interval
                    self._poll()
                else:
                    # Idle: pick up anything unsolicited, such as a reset banner.
                    self._read(lambda *_: False, READ_TIMEOUT_S)
            except (serial.SerialException, OSError) as exc:
                self._close(f"Lost connection: {exc}")
                self._stop.wait(1.0)
        self._close()

    def _set_status(self, state: str, detail: str = "") -> None:
        if self._status != (state, detail):
            self._status = (state, detail)
            self.events.put(StatusEvent(state, detail))

    def _open(self) -> bool:
        with self._lock:
            port = self._port
        if not port:
            self._set_status("closed")
            return False
        try:
            self._ser = self._factory(port)
        except (serial.SerialException, OSError) as exc:
            self._set_status("closed", f"Can't open {port}: {exc}")
            return False
        self._splitter = LineSplitter()
        self._set_status("waiting", port)
        return True

    def _close(self, detail: str = "") -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except (serial.SerialException, OSError):
                pass
            self._ser = None
        self._set_status("closed", detail)

    def _write(self, cmd: str) -> None:
        for ch in (cmd + "\r").encode("ascii"):
            self._ser.write(bytes([ch]))
            time.sleep(CHAR_DELAY_S)

    def _read(self, done: Callable[[list[Message], list[str], float], bool],
              timeout: float) -> tuple[list[Message], list[str]]:
        """Read and handle lines until ``done(messages, lines, idle_s)`` or timeout."""
        deadline = time.monotonic() + timeout
        last_rx = time.monotonic()
        msgs: list[Message] = []
        lines: list[str] = []
        while True:
            data = self._ser.read(max(1, self._ser.in_waiting))
            now = time.monotonic()
            if data:
                last_rx = now
                for line in self._splitter.feed(data):
                    lines.append(line)
                    msg = parse_line(line)
                    if msg is not None:
                        msgs.append(msg)
                        self._dispatch(msg, now)
            if done(msgs, lines, now - last_rx) or now >= deadline:
                return msgs, lines

    def _dispatch(self, msg: Message, t: float) -> None:
        if isinstance(msg, Banner):
            self.events.put(ResetEvent(msg.version, time.time()))
        elif isinstance(msg, Unrecognised):
            self.events.put(ErrorEvent("Controller did not recognise a command"))
        else:
            if isinstance(msg, Output):
                self._outputs.add(msg.name)
            self.events.put(ValueEvent(msg, t))

    def _poll(self) -> None:
        def all_outputs(msgs, _lines, idle):
            names = {m.name for m in msgs if isinstance(m, Output)}
            return bool(names) and (names >= self._outputs or idle >= BURST_GAP_S)

        self._write("READ_ALL")
        msgs, _ = self._read(all_outputs, REPLY_TIMEOUT_S)
        if not any(isinstance(m, Output) for m in msgs):
            self._set_status("waiting", "No reply from controller")
            return
        self._set_status("ok")

        for key in ANALOG_KEYS:
            self._write(f"READ_{key}")
            self._read(lambda msgs, *_: any(isinstance(m, Analog) and m.name == key
                                            for m in msgs), REPLY_TIMEOUT_S)

    def _command(self, cmd: str) -> None:
        # Wait for the echo, then a short quiet gap so any error reply is caught
        # and the firmware has finished before the next command goes out.
        def finished(_msgs, lines, idle):
            return any(line.endswith(cmd) for line in lines) and idle >= BURST_GAP_S

        self._write(cmd)
        self._read(finished, REPLY_TIMEOUT_S)
        self.events.put(SentEvent(cmd))
