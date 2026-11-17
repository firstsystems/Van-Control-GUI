"""Runs the real ControllerLink worker against the simulated controller."""

import queue
import time

import pytest

from vc_gui.link import ControllerLink, ErrorEvent, ResetEvent, SentEvent, StatusEvent, ValueEvent
from vc_gui.protocol import Analog, Output
from vc_gui.simulator import POWER_ON, SimulatedController


@pytest.fixture
def board_and_link():
    board = SimulatedController()
    link = ControllerLink(poll_interval=0.2, serial_factory=lambda _port: board)
    link.start()
    link.set_port("sim")
    yield board, link
    link.stop()


def wait_for(link, predicate, timeout=3.0):
    """Collect events until predicate(event) is true; return all events seen."""
    seen = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ev = link.events.get(timeout=0.05)
        except queue.Empty:
            continue
        seen.append(ev)
        if predicate(ev):
            return seen
    raise AssertionError(f"timed out; saw {seen[-10:]}")


def output_value(ev, name):
    if isinstance(ev, ValueEvent) and isinstance(ev.message, Output) and ev.message.name == name:
        return ev.message.on
    return None


def test_reset_banner_and_power_on_states(board_and_link):
    _board, link = board_and_link
    seen = wait_for(link, lambda ev: isinstance(ev, StatusEvent) and ev.state == "ok")
    assert any(isinstance(ev, ResetEvent) and ev.version == "1.0" for ev in seen)
    states = {ev.message.name: ev.message.on for ev in seen
              if isinstance(ev, ValueEvent) and isinstance(ev.message, Output)}
    assert states == dict(POWER_ON)


def test_poll_reads_analogue(board_and_link):
    _board, link = board_and_link
    keys = set()
    wait_for(link, lambda ev: isinstance(ev, ValueEvent) and isinstance(ev.message, Analog)
             and (keys.add(ev.message.name) or keys == {"VLB", "VSB", "TEMP"}))


def test_switch_and_toggle(board_and_link):
    board, link = board_and_link
    wait_for(link, lambda ev: isinstance(ev, StatusEvent) and ev.state == "ok")

    link.send("BLK7-5_OFF")
    wait_for(link, lambda ev: isinstance(ev, SentEvent))
    wait_for(link, lambda ev: output_value(ev, "BLK7-5") is False)
    assert board.outputs["BLK7-5"] is False

    link.send("BLK4-3_TOGGLE")
    wait_for(link, lambda ev: output_value(ev, "BLK4-3") is True)


def test_physical_button_seen_by_poll(board_and_link):
    board, link = board_and_link
    wait_for(link, lambda ev: isinstance(ev, StatusEvent) and ev.state == "ok")
    board.press_button()
    wait_for(link, lambda ev: output_value(ev, "BLK4-3") is True)


def test_reset_detected_while_running(board_and_link):
    board, link = board_and_link
    wait_for(link, lambda ev: isinstance(ev, StatusEvent) and ev.state == "ok")
    board.reset()
    wait_for(link, lambda ev: isinstance(ev, ResetEvent))


def test_unknown_command_reported(board_and_link):
    _board, link = board_and_link
    wait_for(link, lambda ev: isinstance(ev, StatusEvent) and ev.state == "ok")
    link.send("NOPE")
    wait_for(link, lambda ev: isinstance(ev, ErrorEvent))
