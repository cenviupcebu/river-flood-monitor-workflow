from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional
import json

from flood_ops.config import BasinConfig
from flood_ops.logging import get_logger
from .run_spec import PipelineRunSpec
from .utils import expand_template

logger = get_logger(__name__)


"""Step 1 — Prepare:
- download the GloFAS ensemble forecast files.
- load OEP thresholds from the provided OEP JSON file.
"""


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