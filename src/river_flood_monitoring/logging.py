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


LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(major)-10s | %(subfunc)-28s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _major_from_logger_name(logger_name: str) -> str:
    return logger_name.rsplit(".", 1)[-1]


class PipelineFormatter(logging.Formatter):
    """Adds derived logging context fields used by the flood-ops format."""

    def format(self, record: logging.LogRecord) -> str:
        record.major = _major_from_logger_name(record.name)
        record.subfunc = f"{record.funcName}:{record.lineno}"
        return super().format(record)


def _build_formatter() -> logging.Formatter:
    return PipelineFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)


def setup_logging(log_level: str = "INFO") -> None:
    """Initialise the river_flood_monitoring logger tree with a console handler."""
    root_logger = logging.getLogger("river_flood_monitoring")
    level = logging._nameToLevel.get(log_level.upper(), logging.INFO)
    root_logger.setLevel(level)

    if root_logger.handlers:
        return

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(_build_formatter())
    root_logger.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the river_flood_monitoring namespace."""
    if not logging.getLogger("river_flood_monitoring").handlers:
        setup_logging()

    if name.startswith("river_flood_monitoring"):
        return logging.getLogger(name)
    return logging.getLogger(f"river_flood_monitoring.{name}")


def setup_pipeline_file_log(
    log_dir: Path,
    run_name: str = "etl",
    log_level: str = "DEBUG",
) -> Path:
    """Attach a run-scoped file handler to the river_flood_monitoring root logger."""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    log_file = log_dir / f"{run_name}_{timestamp}.txt"

    root = logging.getLogger("river_flood_monitoring")
    if not root.handlers:
        setup_logging()

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(logging._nameToLevel.get(log_level.upper(), logging.DEBUG))
    handler.setFormatter(_build_formatter())
    root.addHandler(handler)

    def _cleanup() -> None:
        handler.flush()
        handler.close()
        if handler in root.handlers:
            root.removeHandler(handler)

    atexit.register(_cleanup)
    return log_file