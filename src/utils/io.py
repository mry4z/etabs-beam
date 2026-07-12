"""Output-path helpers that avoid overwriting a previously generated report."""

from __future__ import annotations

from pathlib import Path


def available_output_path(directory: Path, filename: str) -> Path:
    """Return an unused path by adding a two-digit suffix when necessary."""
    candidate = directory / filename
    sequence = 1
    while candidate.exists():
        candidate = directory / f"{Path(filename).stem}_{sequence:02d}{Path(filename).suffix}"
        sequence += 1
    return candidate
