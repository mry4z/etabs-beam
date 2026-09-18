"""Stable output layout for a read-only beam workflow run.

The workflow writes only deliverables to :attr:`OutputLayout.root`.  Audits,
payloads and other intermediate files belong below ``_data`` so a repeated
run can reuse the same model directory without mixing delivery files with
run bookkeeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


_INVALID_WINDOWS_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def sanitize_model_name(value: str | Path | None) -> str:
    """Return a deterministic Windows-safe model stem for output names."""

    raw = Path(str(value or "model")).stem
    cleaned = _INVALID_WINDOWS_NAME.sub("_", raw).strip(" .") or "model"
    if cleaned.upper() in _RESERVED_WINDOWS_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def model_name_from_path(model_path: str | Path | None) -> str:
    """Use the durable EDB stem (or a stable snapshot equivalent)."""

    return sanitize_model_name(model_path)


@dataclass(frozen=True)
class OutputLayout:
    """The model delivery directory and its private audit/data directory."""

    root: Path
    data: Path
    model_name: str

    @property
    def primary_schedule(self) -> Path:
        return self.root / f"{self.model_name}_primarybeam_schedule.xlsx"

    @property
    def secondary_schedule(self) -> Path:
        return self.root / f"{self.model_name}_secondarybeam_schedule.xlsx"

    @property
    def span_note(self) -> Path:
        return self.root / f"{self.model_name}_span_note.dxf"


def resolve_output_layout(
    output_dir: str | Path,
    model_path: str | Path | None = None,
    *,
    create: bool = True,
) -> OutputLayout:
    """Resolve an output parent or an already-resolved model output directory.

    ``output_dir`` is intentionally idempotent: a caller may pass either the
    user-selected parent directory or the exact ``<model>_beam_output``
    directory returned by a previous run.  Comparison is case-insensitive to
    match Windows path semantics.
    """

    model_name = model_name_from_path(model_path)
    target_name = f"{model_name}_beam_output"
    requested = Path(output_dir).expanduser()
    if requested.name.casefold() == target_name.casefold():
        root = requested
    else:
        root = requested / target_name
    data = root / "_data"
    if create:
        data.mkdir(parents=True, exist_ok=True)
    return OutputLayout(root=root, data=data, model_name=model_name)


def layout_for_model(
    output_dir: str | Path,
    model_path: str | Path | None = None,
    *,
    create: bool = True,
) -> OutputLayout:
    """Compatibility alias used by workflow callers."""

    return resolve_output_layout(output_dir, model_path, create=create)
