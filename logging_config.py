from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(level: int = logging.WARNING, log_file: Path | str | None = None) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)

    # Formatter for all handlers
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler (stdout) - always added
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    # File handler - only added if log_file is specified
    if log_file is not None:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
