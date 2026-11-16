"""Helpers for recognizing explicit ETABS No Design exclusions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def is_explicit_no_design(status: Any, reason: Any = "") -> bool:
    """Return whether a status explicitly identifies an ETABS No Design object."""

    status_name = getattr(status, "name", status)
    status_text = str(status_name or "").strip().upper()
    if status_text == "EXCLUDED_NO_DESIGN":
        return True
    if status_text not in {"SKIPPED", "不纳入混凝土设计"}:
        return False
    reason_text = str(reason or "").casefold()
    return "no design" in reason_text or "excluded_no_design" in reason_text


def excluded_no_design_source_names(
    exclusions: Iterable[Mapping[str, Any]],
) -> frozenset[str]:
    """Return object names explicitly excluded by the source's design audit."""

    return frozenset(
        name
        for item in exclusions
        if is_explicit_no_design(item.get("status"), item.get("reason"))
        if (name := str(item.get("object_name", "")).strip())
    )
