"""Logging Setup

Rotating file + console logging. A SystemEvent DB bridge is added in Phase 10.

Implemented in Phase 5. Interface: docs/architecture/02-low-level-architecture.md
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FMT = "%(asctime)s %(levelname)-7s %(name)-16s %(message)s"


def setup_logging(logs_dir: str | Path, level: str = "INFO",
                  rotate_mb: int = 5, backups: int = 5) -> logging.Logger:
    """Configure the root logger with console + rotating file handlers."""
    logs = Path(logs_dir)
    logs.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    # Avoid duplicate handlers if called twice.
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(_FMT)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    fileh = RotatingFileHandler(
        logs / "bottle-vendo.log",
        maxBytes=rotate_mb * 1024 * 1024,
        backupCount=backups,
        encoding="utf-8",
    )
    fileh.setFormatter(fmt)
    root.addHandler(fileh)

    return logging.getLogger("bottle-vendo")
