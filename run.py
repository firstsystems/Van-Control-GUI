"""Start the Van Control GUI.

    python run.py            connect to the real controller
    python run.py --demo     use a simulated controller (no hardware needed)
"""

import argparse
import tkinter as tk
from pathlib import Path

from vc_gui import config
from vc_gui.app import App
from vc_gui.link import ControllerLink
from vc_gui.simulator import SimulatedController


def main() -> None:
    parser = argparse.ArgumentParser(description="Van Control GUI")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.toml"))
    parser.add_argument("--demo", action="store_true", help="use a simulated controller")
    args = parser.parse_args()

    cfg = config.load(args.config)
    if args.demo:
        link = ControllerLink(cfg.poll_interval, serial_factory=SimulatedController)
    else:
        link = ControllerLink(cfg.poll_interval)
    link.start()

    root = tk.Tk()
    App(root, cfg, link, demo_port="(simulated)" if args.demo else None)
    root.mainloop()


if __name__ == "__main__":
    main()
