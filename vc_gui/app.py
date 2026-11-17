"""Tk window for the Van Controller."""

from __future__ import annotations

import queue
import time
import tkinter as tk
from tkinter import ttk

from .config import Config
from .link import (ControllerLink, ErrorEvent, ResetEvent, SentEvent, StatusEvent,
                   ValueEvent, default_port, find_ports)
from .protocol import Analog, Output

STALE_S = 3.0

# Dark, high-contrast palette. The "clam" theme is used so these colours hold
# when the window is not focused (the macOS native theme greys text out then).
BG = "#1b1f27"
CARD = "#272c36"
FIELD = "#323844"
BORDER = "#454c5a"
TEXT = "#f2f4f7"
MUTED = "#a9b1be"
DISABLED = "#6f7785"
ACCENT = "#3d7be0"
ON = "#34c26b"
OFF = "#5b6372"
PENDING = "#f0b429"
UNKNOWN = "#3a404c"
STATUS_COLOURS = {"ok": ON, "waiting": PENDING, "closed": "#e5534b"}
STATUS_TEXT = {"ok": "Connected", "waiting": "Waiting for controller", "closed": "Disconnected"}


class Switch(tk.Canvas):
    """A sliding on/off switch drawn on a canvas.

    ``on`` is True, False or None (state not known yet). ``pending`` shows that a
    change has been asked for but the controller hasn't confirmed it.
    """

    W, H, PAD = 50, 26, 3

    def __init__(self, master, command) -> None:
        super().__init__(master, width=self.W, height=self.H, bg=CARD,
                         highlightthickness=0, cursor="hand2")
        self._command = command
        self.on: bool | None = None
        self.pending = False
        self.enabled = False
        self.bind("<Button-1>", lambda _e: self.enabled and self._command())
        self._draw()

    def show(self, on: bool | None, pending: bool = False, enabled: bool = True) -> None:
        if (on, pending, enabled) != (self.on, self.pending, self.enabled):
            self.on, self.pending, self.enabled = on, pending, enabled
            self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h, p = self.W, self.H, self.PAD
        if self.on is None or not self.enabled:
            fill = UNKNOWN
        elif self.pending:
            fill = PENDING
        else:
            fill = ON if self.on else OFF
        r = h / 2
        self.create_oval(0, 0, h, h, fill=fill, outline=fill)
        self.create_oval(w - h, 0, w, h, fill=fill, outline=fill)
        self.create_rectangle(r, 0, w - r, h, fill=fill, outline=fill)
        if self.on is not None:
            x0 = w - h + p if self.on else p
            self.create_oval(x0, p, x0 + h - 2 * p, h - p, fill="white", outline="white")
        self.configure(cursor="hand2" if self.enabled else "arrow")


class App:
    def __init__(self, root: tk.Tk, cfg: Config, link: ControllerLink,
                 demo_port: str | None = None) -> None:
        self.root, self.cfg, self.link = root, cfg, link
        self.status = "closed"
        self.outputs: dict[str, dict] = {}      # name -> {switch, state_lbl, on}
        self.pending: dict[str, dict] = {}      # name -> {want, unsent}
        self.analog_rows: dict[str, dict] = {}  # key -> {value, raw, t}

        root.title("Van Control")
        root.configure(bg=BG)
        root.minsize(620, 420)
        root.protocol("WM_DELETE_WINDOW", self._quit)

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, troughcolor=FIELD,
                        focuscolor=ACCENT, selectbackground=ACCENT, selectforeground=TEXT)
        style.map(".", foreground=[("disabled", DISABLED)])
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("TButton", background=FIELD, foreground=TEXT, padding=(8, 3))
        style.map("TButton",
                  background=[("disabled", CARD), ("pressed", ACCENT), ("active", BORDER)],
                  foreground=[("disabled", DISABLED)])
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD,
                        foreground=TEXT, arrowcolor=TEXT, insertcolor=TEXT)
        style.map("TCombobox",
                  fieldbackground=[("disabled", CARD), ("readonly", FIELD)],
                  foreground=[("disabled", MUTED)],
                  arrowcolor=[("disabled", DISABLED)])
        root.option_add("*TCombobox*Listbox.background", FIELD)
        root.option_add("*TCombobox*Listbox.foreground", TEXT)
        root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        style.configure("Card.TFrame", background=CARD)
        style.configure("Card.TLabel", background=CARD, foreground=TEXT)
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("TkDefaultFont", 11))
        style.configure("Head.TLabel", background=CARD, foreground=TEXT, font=("TkDefaultFont", 15, "bold"))
        style.configure("Big.TLabel", background=CARD, foreground=TEXT, font=("TkDefaultFont", 26, "bold"))
        style.configure("Toggle.TButton", font=("TkDefaultFont", 13), padding=(10, 8))

        self._build_top_bar(demo_port)
        body = ttk.Frame(root, padding=(14, 4, 14, 14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1, uniform="col")
        body.columnconfigure(1, weight=1, uniform="col")
        body.rowconfigure(0, weight=1)
        self._build_outputs(body)
        self._build_analog(body)

        self.message = ttk.Label(root, text="", foreground=MUTED, padding=(16, 0, 16, 10))
        self.message.pack(fill="x")

        self._refresh_enabled()
        root.after(50, self._drain_events)
        root.after(500, self._tick)

        # Connect straight away if we know which port to use.
        port = demo_port or self.port_var.get()
        if port:
            self._connect(port)

    # ---- Layout ----

    def _build_top_bar(self, demo_port: str | None) -> None:
        bar = ttk.Frame(self.root, padding=(14, 12, 14, 8))
        bar.pack(fill="x")

        ttk.Label(bar, text="Port").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(bar, textvariable=self.port_var, width=28)
        self.port_box.pack(side="left", padx=(6, 4))
        ttk.Button(bar, text="↻", width=2, command=self._refresh_ports).pack(side="left")
        self.connect_btn = ttk.Button(bar, text="Connect", command=self._toggle_connect)
        self.connect_btn.pack(side="left", padx=(8, 0))
        if demo_port:
            self.port_box.configure(values=[demo_port], state="disabled")
            self.port_var.set(demo_port)
        else:
            self._refresh_ports(prefer=self.cfg.port)

        self.reset_lbl = ttk.Label(bar, text="Last reset: —", foreground=MUTED)
        self.reset_lbl.pack(side="right")
        self.status_lbl = ttk.Label(bar, text="Disconnected")
        self.status_lbl.pack(side="right", padx=(0, 16))
        self.status_dot = tk.Canvas(bar, width=12, height=12, bg=BG, highlightthickness=0)
        self.status_dot.pack(side="right", padx=(0, 6))
        self._draw_status()

    def _card(self, parent, column: int, title: str) -> ttk.Frame:
        card = ttk.Frame(parent, style="Card.TFrame", padding=16)
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 7, 7 if column == 0 else 0))
        ttk.Label(card, text=title, style="Head.TLabel").pack(anchor="w", pady=(0, 10))
        return card

    def _build_outputs(self, body) -> None:
        card = self._card(body, 0, "Outputs")
        self.outputs_frame = ttk.Frame(card, style="Card.TFrame")
        self.outputs_frame.pack(fill="x")
        self.outputs_frame.columnconfigure(0, weight=1)
        self.outputs_hint = ttk.Label(self.outputs_frame, text="Waiting for controller…",
                                      style="Muted.TLabel")
        self.outputs_hint.grid(row=0, column=0, sticky="w")

        ttk.Frame(card, style="Card.TFrame", height=14).pack()
        self.toggle_btn = ttk.Button(
            card, style="Toggle.TButton",
            text=f"{self.cfg.toggle_label}  –  {self._output_label(self.cfg.toggle_output)}",
            command=lambda: self.link.send(f"{self.cfg.toggle_output}_TOGGLE"))
        self.toggle_btn.pack(fill="x")

    def _add_output_row(self, name: str) -> None:
        self.outputs_hint.grid_remove()
        row = len(self.outputs)
        frame = self.outputs_frame
        ttk.Label(frame, text=self._output_label(name), style="Card.TLabel").grid(
            row=row, column=0, sticky="w", pady=4)
        state_lbl = ttk.Label(frame, text="", width=4, style="Muted.TLabel")
        state_lbl.grid(row=row, column=2, sticky="e", padx=(0, 8))
        switch = Switch(frame, command=lambda n=name: self._on_switch(n))
        switch.grid(row=row, column=3, sticky="e")
        self.outputs[name] = {"switch": switch, "state_lbl": state_lbl, "on": None}

    def _output_label(self, name: str) -> str:
        """'PUMP (BLK7-5)', or just the pin name if no function name is configured."""
        label = self.cfg.labels.get(name, name)
        return f"{label} ({name})" if label != name else name

    def _build_analog(self, body) -> None:
        card = self._card(body, 1, "Analogue inputs")
        for ch in self.cfg.analog:
            ttk.Label(card, text=ch.label, style="Muted.TLabel").pack(anchor="w")
            value = ttk.Label(card, text="—", style="Big.TLabel")
            value.pack(anchor="w")
            raw = ttk.Label(card, text="", style="Muted.TLabel")
            raw.pack(anchor="w", pady=(0, 12))
            self.analog_rows[ch.key] = {"channel": ch, "value": value, "raw": raw, "t": None}

    # ---- Connection ----

    def _refresh_ports(self, prefer: str | None = None) -> None:
        ports = find_ports()
        self.port_box.configure(values=ports)
        current = prefer or self.port_var.get()
        if current and (current in ports or prefer):
            self.port_var.set(current)
        else:
            self.port_var.set(default_port(ports) or (ports[0] if ports else ""))

    def _connect(self, port: str) -> None:
        self.link.set_port(port)
        self.connect_btn.configure(text="Disconnect")

    def _toggle_connect(self) -> None:
        if self.connect_btn.cget("text") == "Disconnect":
            self.link.set_port(None)
            self.connect_btn.configure(text="Connect")
        elif self.port_var.get():
            self._connect(self.port_var.get())

    def _draw_status(self) -> None:
        self.status_dot.delete("all")
        c = STATUS_COLOURS[self.status]
        self.status_dot.create_oval(1, 1, 11, 11, fill=c, outline=c)

    # ---- Outputs ----

    def _on_switch(self, name: str) -> None:
        p = self.pending.get(name)
        current = p["want"] if p else self.outputs[name]["on"]
        want = not current
        self.link.send(f"{name}_{'ON' if want else 'OFF'}")
        self.pending[name] = {"want": want, "unsent": (p["unsent"] if p else 0) + 1}
        self._show_output(name)

    def _show_output(self, name: str) -> None:
        row = self.outputs[name]
        p = self.pending.get(name)
        on = p["want"] if p else row["on"]
        row["switch"].show(on, pending=bool(p), enabled=self.status == "ok")
        row["state_lbl"].configure(text="" if on is None else ("ON" if on else "OFF"))

    def _refresh_enabled(self) -> None:
        for name in self.outputs:
            self._show_output(name)
        self.toggle_btn.state(["!disabled"] if self.status == "ok" else ["disabled"])

    # ---- Events from the link ----

    def _drain_events(self) -> None:
        try:
            while True:
                self._handle(self.link.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(50, self._drain_events)

    def _handle(self, ev) -> None:
        if isinstance(ev, StatusEvent):
            self.status = ev.state
            text = STATUS_TEXT[ev.state]
            self.status_lbl.configure(text=text)
            self._draw_status()
            if ev.detail and ev.state != "ok":
                self.message.configure(text=ev.detail)
            elif ev.state == "ok":
                self.message.configure(text="")
            if ev.state != "ok":
                self.pending.clear()
            self._refresh_enabled()
        elif isinstance(ev, ValueEvent):
            msg = ev.message
            if isinstance(msg, Output):
                if msg.name not in self.outputs:
                    self._add_output_row(msg.name)
                self.outputs[msg.name]["on"] = msg.on
                p = self.pending.get(msg.name)
                if p and p["unsent"] == 0:
                    # Board has had the command: show what it really did.
                    del self.pending[msg.name]
                self._show_output(msg.name)
            elif isinstance(msg, Analog) and msg.name in self.analog_rows:
                row = self.analog_rows[msg.name]
                ch = row["channel"]
                row["value"].configure(text=f"{ch.convert(msg.mv):.{ch.decimals}f} {ch.unit}",
                                       foreground=TEXT)
                row["raw"].configure(text=f"{msg.mv} mV reported")
                row["t"] = ev.t
        elif isinstance(ev, SentEvent):
            name = ev.command.rsplit("_", 1)[0]
            if name in self.pending:
                self.pending[name]["unsent"] -= 1
        elif isinstance(ev, ResetEvent):
            stamp = time.strftime("%H:%M:%S", time.localtime(ev.wall_time))
            self.reset_lbl.configure(text=f"Last reset: {stamp} (v{ev.version})")
        elif isinstance(ev, ErrorEvent):
            self.message.configure(text=ev.text)

    def _tick(self) -> None:
        """Grey out analogue readings that have stopped updating."""
        now = time.monotonic()
        for row in self.analog_rows.values():
            if row["t"] is not None and now - row["t"] > STALE_S:
                row["value"].configure(foreground=MUTED)
        self.root.after(500, self._tick)

    def _quit(self) -> None:
        self.link.stop()
        self.root.destroy()
