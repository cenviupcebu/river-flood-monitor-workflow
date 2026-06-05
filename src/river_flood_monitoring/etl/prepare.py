"""Prepare stage for daily flood monitoring ETL.

This module hosts pre-forecast preparation tasks, such as resolving or
downloading required forecast inputs before extraction and evaluation.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional
import json

from river_flood_monitoring.config import BasinConfig
from river_flood_monitoring.logging import get_logger
from .run_spec import PipelineRunSpec
from .utils import expand_template

logger = get_logger(__name__)


def prepare():
    """
    Prepare forecast data
    """
    _download_glofas()
    pass


def _download_glofas():
    """
    Download GloFAS forecast files
    """
    pass