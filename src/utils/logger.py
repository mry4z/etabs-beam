"""Application logging setup shared by command-line workflows."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


def configure_logger(logs_dir: Path, filename_pattern: str, level: str) -> logging.Logger:
    """Configure console and UTF-8 file logging, returning the project logger."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / filename_pattern.format(timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"))
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )
    return logging.getLogger("etabs_beam")
