"""Shared logging utilities for flood-ops.

Provides a package-local logger namespace so standalone runs do not depend on
the philflood package.
"""

from __future__ import annotations

import atexit
import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(log_level: str = "INFO") -> None:
    """Initialise the flood_ops logger tree with a console handler."""
    root_logger = logging.getLogger("flood_ops")
    level = logging._nameToLevel.get(log_level.upper(), logging.INFO)
    root_logger.setLevel(level)

    if root_logger.handlers:
        return

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s.%(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the flood_ops namespace."""
    if not logging.getLogger("flood_ops").handlers:
        setup_logging()

    if name.startswith("flood_ops"):
        return logging.getLogger(name)
    return logging.getLogger(f"flood_ops.{name}")


def setup_pipeline_file_log(
    log_dir: Path,
    run_name: str = "etl",
    log_level: str = "DEBUG",
) -> Path:
    """Attach a run-scoped file handler to the flood_ops root logger."""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    log_file = log_dir / f"{run_name}_{timestamp}.txt"

    root = logging.getLogger("flood_ops")
    if not root.handlers:
        setup_logging()

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(logging._nameToLevel.get(log_level.upper(), logging.DEBUG))
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s.%(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)

    def _cleanup() -> None:
        handler.flush()
        handler.close()
        if handler in root.handlers:
            root.removeHandler(handler)

    atexit.register(_cleanup)
    return log_file