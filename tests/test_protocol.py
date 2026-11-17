from vc_gui.protocol import Analog, Banner, LineSplitter, Output, Unrecognised, parse_line


def lines_of(raw: bytes) -> list[str]:
    s = LineSplitter()
    return s.feed(raw)


def test_read_all_reply_with_echo():
    raw = (b"READ_ALL\r"
           b"\r\nBLK7-5=ON\r\nBLK6-3=OFF\r\nBLK4-15=ON\r\n12V_HS1=ON\r\n")
    msgs = [m for m in map(parse_line, lines_of(raw)) if m]
    assert msgs == [Output("BLK7-5", True), Output("BLK6-3", False),
                    Output("BLK4-15", True), Output("12V_HS1", True)]


def test_analog_reply():
    msgs = [m for m in map(parse_line, lines_of(b"READ_VLB\r\r\nVLB=2874mV\r\n")) if m]
    assert msgs == [Analog("VLB", 2874)]


def test_banner():
    assert parse_line("Van Control v1.0") == Banner("1.0")


def test_unrecognised():
    assert parse_line("Unrecognised command. Use HELP to list valid commands.") == Unrecognised()


def test_ignored_lines():
    for line in ["", "BLK4-3_TOGGLE", "  <NAME>_ON    Turn output on (high)",
                 "--- Van Control Commands ---", "VLB=abcmV"]:
        assert parse_line(line) is None


def test_splitter_keeps_partial_line():
    s = LineSplitter()
    assert s.feed(b"\r\nBLK4-") == []
    assert s.feed(b"3=ON\r\n") == ["BLK4-3=ON"]
