"""Run logging: a compact, coloured console view plus a full timestamped log file per run.

Pass extra={"status": "..."} to colour a line by outcome, extra={"detail": True} for indented detail lines
(dimmed and cut to terminal width on the console, kept in full in the file).
"""

from __future__ import annotations

import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

from sortie.config import PROJECT_ROOT

LOG_DIR = PROJECT_ROOT / "data" / "logs"
log = logging.getLogger("sortie")

COLOURS = {"applied": "32", "external": "36", "failed": "31", "blocked": "31", "skipped": "2"}


class ConsoleFormatter(logging.Formatter):
    def __init__(self, colour: bool):
        super().__init__("%(message)s")
        self.colour = colour

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if getattr(record, "detail", False):
            width = shutil.get_terminal_size((120, 20)).columns
            text = "\n".join(line if len(line) <= width else line[: width - 1] + "…" for line in text.split("\n"))
        code = ("2" if getattr(record, "detail", False) else COLOURS.get(getattr(record, "status", ""), ""))
        if record.levelno >= logging.WARNING:
            code = "33"
        return f"\033[{code}m{text}\033[0m" if self.colour and code else text


def setup_logging() -> Path:
    """Console + data/logs/run-<timestamp>.log; returns the log file path."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"run-{datetime.now():%Y%m%d-%H%M%S}.log"

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(ConsoleFormatter(colour=sys.stdout.isatty()))
    file = logging.FileHandler(path, encoding="utf-8")
    file.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))

    log.handlers[:] = [console, file]
    log.setLevel(logging.INFO)
    log.propagate = False
    return path
