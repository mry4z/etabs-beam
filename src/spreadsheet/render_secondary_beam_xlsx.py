"""Render the secondary-beam static result schedule.

The SB adapter keeps the legacy schedule/exception diagnostics, validates the
single-stirrup contract for accepted rows, and delegates the final eleven-
column worksheet to the canonical static renderer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from src.rebar_workbook import (
    ADJACENT_SPAN_BOTTOM_STRATEGY,
    BOTTOM_REINFORCEMENT_STRATEGY,
    PAYLOAD_SCHEMA_VERSION,
    RESULT_WORKBOOK_LAYOUT_VERSION,
)
from src.spreadsheet.render_rebar_workbook_xlsx import render_rebar_workbook_xlsx

PAYLOAD_KEYS = ("metadata", "schedule_rows", "exception_rows")


def _natural_key(value: Any) -> tuple:
    """Natural sort key matching the workbook's floor/beam ordering."""

    parts = re.split(r"(\d+)", str(value or ""))
    result: list[tuple] = []
    for part in parts:
        if part.isdigit():
            result.append((1, int(part)))
        elif part:
            result.append((0, part.casefold()))
    return tuple(result)


def _sorted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            _natural_key(row.get("story")),
            _natural_key(row.get("beam_line")),
            _span_sort_key(row),
        ),
    )


def _span_sort_key(row: Mapping[str, Any]) -> tuple[int, Any]:
    try:
        return (0, int(row.get("span_no")))
    except (TypeError, ValueError):
        return (1, _natural_key(row.get("beam_no")))


def _data_row(row: dict[str, Any]) -> list[Any]:
    """Return the eleven visible SB values for diagnostic callers."""

    return [
        row.get("beam_line") or "",
        row.get("beam_no") or "",
        int(row.get("width_mm") or 0),
        int(row.get("depth_mm") or 0),
        row.get("et1") or "",
        row.get("t1") or "",
        row.get("et2") or "",
        row.get("bottom_uniform_text") or row.get("b1") or row.get("eb1") or "",
        row.get("stirrups") or row.get("s1") or row.get("s2") or "",
        int(row.get("legs") or 0),
        row.get("face") or "—",
    ]


def _static_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    metadata = dict(payload.get("metadata", {}))
    metadata.setdefault("schema_version", PAYLOAD_SCHEMA_VERSION)
    metadata["schedule_kind"] = "SECONDARY_BEAM_REINFORCEMENT_SCHEDULE"
    metadata["secondary_workbook_layout"] = RESULT_WORKBOOK_LAYOUT_VERSION
    metadata["rebar_workbook_layout_version"] = RESULT_WORKBOOK_LAYOUT_VERSION
    metadata["rebar_workbook_layout"] = "one-sheet-static-values"
    metadata["bottom_reinforcement_strategy"] = metadata.get(
        "bottom_reinforcement_strategy", BOTTOM_REINFORCEMENT_STRATEGY
    )
    metadata["adjacent_span_bottom_strategy"] = metadata.get(
        "adjacent_span_bottom_strategy", ADJACENT_SPAN_BOTTOM_STRATEGY
    )
    return metadata


def render_secondary_beam_xlsx(payload: Mapping[str, Any], output_path: str | Path) -> Path:
    """Write the SB workbook as one static eleven-column worksheet."""

    for key in PAYLOAD_KEYS:
        if key not in payload:
            raise ValueError(f"payload missing {key}")

    schedule_rows = _sorted_rows(list(payload["schedule_rows"]))
    for row in schedule_rows:
        if str(row.get("beam_type", "")).upper() != "SB":
            raise ValueError(f"non-SB row in secondary schedule: {row.get('beam_no')}")
        stirrups = str(row.get("stirrups", "")).strip()
        if not stirrups or stirrups != str(row.get("s1", "")).strip() or stirrups != str(row.get("s2", "")).strip():
            raise ValueError(f"SB stirrup is not span-unified: {row.get('beam_no')}")

    if all(key in payload for key in ("raw_rows", "review_rows", "final_rows")):
        raw_rows = list(payload["raw_rows"])
        review_rows = list(payload["review_rows"])
        final_rows = list(payload["final_rows"])
        if not (len(raw_rows) == len(review_rows) == len(final_rows)):
            raise ValueError("raw_rows, review_rows and final_rows must have the same length")
        exception_status = {
            str(row.get("beam_no", "")): str(row.get("engine_status", ""))
            for row in payload.get("exception_rows", [])
            if row.get("beam_no")
        }
        source_rows: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        for raw, review, final in zip(raw_rows, review_rows, final_rows):
            if str(final.get("beam_type", "")).strip().upper() != "SB":
                continue
            raw_copy = dict(raw)
            review_copy = dict(review)
            final_copy = dict(final)
            override = exception_status.get(str(final_copy.get("beam_no", "")))
            if override:
                raw_copy["engine_status"] = override
                review_copy["engine_status"] = override
                final_copy["engine_status"] = override
            source_rows.append((raw_copy, review_copy, final_copy))
        source_rows.sort(
            key=lambda row: (
                _natural_key(row[2].get("story")),
                _natural_key(row[2].get("beam_line")),
                _span_sort_key(row[2]),
            )
        )
        filtered_payload = {
            "metadata": _static_metadata(payload),
            "raw_rows": [row[0] for row in source_rows],
            "review_rows": [row[1] for row in source_rows],
            "final_rows": [row[2] for row in source_rows],
        }
        return render_rebar_workbook_xlsx(filtered_payload, output_path, beam_type="SB")

    # Legacy schedule-only payloads remain readable, but their rows are
    # explicitly marked as candidates requiring redetail before delivery.
    return _render_legacy_schedule_payload(payload, output_path)


def _render_legacy_schedule_payload(payload: Mapping[str, Any], output_path: str | Path) -> Path:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in [*payload.get("schedule_rows", []), *payload.get("exception_rows", [])]:
        row = dict(source)
        key = str(row.get("beam_no", ""))
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    rows = _sorted_rows(rows)

    def common(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: row.get(key, "")
            for key in (
                "beam_no", "beam_line", "span_no", "story", "beam_type", "section",
                "width_mm", "depth_mm", "frame_type", "detailing_profile",
                "source_objects", "engine_status", "warnings", "legs", "mode",
            )
        } | {
            "beam_type": "SB",
            "bottom_reinforcement_strategy": f"{BOTTOM_REINFORCEMENT_STRATEGY}_LEGACY_REQUIRES_REDETAIL",
            "bottom_uniform_regions_match": None,
        }

    raw_rows = [common(row) for row in rows]
    review_rows: list[dict[str, Any]] = []
    final_rows: list[dict[str, Any]] = []
    for row in rows:
        base = common(row)
        review = dict(base)
        final = dict(base)
        for group in ("face", "et1", "t1", "et2", "eb1", "b1", "eb2"):
            review[f"{group}_text"] = row.get(group, "")
            review[f"{group}_required_mm2"] = 0
        review["s2_text"] = row.get("s2", row.get("stirrups", ""))
        review["s1_text"] = row.get("s1", row.get("stirrups", ""))
        for key in (
            "s2_left_at_required", "s2_left_combo_required", "s2_right_at_required",
            "s2_right_combo_required", "s1_at_required", "s1_combo_required",
        ):
            review[key] = 0
        final.update(
            {
                "et1": row.get("et1", ""), "t1": row.get("t1", ""),
                "et2": row.get("et2", ""),
                "eb1": row.get("eb1", row.get("b1", "")),
                "b1": row.get("b1", row.get("eb1", "")),
                "eb2": row.get("eb2", row.get("b1", "")),
                "bottom_uniform_text": row.get("b1", row.get("eb1", "")),
                "s2": row.get("s2", row.get("stirrups", "")),
                "s1": row.get("s1", row.get("stirrups", "")),
                "stirrups": row.get("stirrups", row.get("s1", "")),
                "face": row.get("face", ""),
                "engine_status": row.get("engine_status") or "LEGACY_REDETAIL_REQUIRED",
            }
        )
        review_rows.append(review)
        final_rows.append(final)

    return render_rebar_workbook_xlsx(
        {
            "metadata": _static_metadata(payload),
            "raw_rows": raw_rows,
            "review_rows": review_rows,
            "final_rows": final_rows,
        },
        output_path,
        beam_type="SB",
    )
