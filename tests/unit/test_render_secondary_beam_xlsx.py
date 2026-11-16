"""Tests for the SB static single-sheet renderer."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import load_workbook

from src.spreadsheet.render_secondary_beam_xlsx import (
    _data_row,
    _natural_key,
    _span_sort_key,
    render_secondary_beam_xlsx,
)


def _base(no: str = "SB1-1", line: str = "SB1", status: str = "OK") -> dict:
    return {
        "beam_no": no,
        "beam_line": line,
        "span_no": int(no.rsplit("-", 1)[-1]),
        "story": "GF",
        "beam_type": "SB",
        "section": "250x450",
        "width_mm": 250,
        "depth_mm": 450,
        "source_objects": "O1",
        "engine_status": status,
        "bottom_reinforcement_strategy": "FULL_SPAN_UNIFORM_PER_SPAN",
        "bottom_uniform_regions_match": True,
        "bottom_uniform_text": "3T16",
        "et1": "3T16",
        "t1": "3T16",
        "et2": "3T16",
        "eb1": "3T16",
        "b1": "3T16",
        "eb2": "3T16",
        "s2": "2T8@200",
        "s1": "2T8@200",
        "stirrups": "2T8@200",
        "legs": 2,
        "face": "N.A.",
        "warnings": "",
        "engine_warnings": "",
        "constructability_blocking_statuses": "",
        "review_note": "",
    }


def _payload(rows: list[dict] | None = None) -> dict:
    rows = rows or [_base("SB1-1", "SB1"), _base("SB2-1", "SB2")]
    return {
        "metadata": {
            "schema_version": "2.1",
            "schedule_kind": "SECONDARY_BEAM_REINFORCEMENT_SCHEDULE",
            "rebar_workbook_layout_version": "single-sheet-static-v1",
            "bottom_reinforcement_strategy": "FULL_SPAN_UNIFORM_PER_SPAN",
            "adjacent_span_bottom_strategy": "INDEPENDENT_PER_SPAN",
        },
        "raw_rows": [dict(row) for row in rows],
        "review_rows": [dict(row) for row in rows],
        "final_rows": [dict(row) for row in rows],
        "schedule_rows": [dict(row, stirrups="2T8@200") for row in rows if row.get("engine_status") == "OK"],
        "exception_rows": [],
    }


def _render(tmp_path: Path, payload: dict | None = None) -> Path:
    output = tmp_path / "secondary.xlsx"
    render_secondary_beam_xlsx(payload or _payload(), output)
    return output


def test_natural_key_sorts_numbers_naturally() -> None:
    values = ["F10", "F2", "F1", "F10a"]
    assert sorted(values, key=_natural_key) == ["F1", "F2", "F10", "F10a"]


def test_object_part_sorting_is_numeric_not_lexical() -> None:
    rows = [
        {"span_no": 2, "object_part_index": 10, "beam_no": "SB1-2.10"},
        {"span_no": 2, "object_part_index": 2, "beam_no": "SB1-2.2"},
        {"span_no": 1, "object_part_index": 4, "beam_no": "SB1-1.4"},
    ]
    assert [row["beam_no"] for row in sorted(rows, key=_span_sort_key)] == [
        "SB1-1.4",
        "SB1-2.2",
        "SB1-2.10",
    ]


def test_secondary_has_one_static_eleven_column_sheet(tmp_path: Path) -> None:
    output = _render(tmp_path)
    workbook = load_workbook(output, data_only=False)
    assert workbook.sheetnames == ["整理梁表"]
    sheet = workbook["整理梁表"]
    assert sheet.max_column == 11
    assert sheet["A1"].value == "SECONDARY BEAM REINFORCEMENT SCHEDULE"
    assert sheet["A5"].value == "BEAM NO."
    assert sheet["I6"].value == "STIRRUPS"
    assert sheet["J6"].value == "LEGS"
    assert sheet.tables == {}
    assert all(not (isinstance(cell.value, str) and cell.value.startswith("=")) for row in sheet.iter_rows() for cell in row)
    workbook.close()


def test_exception_status_retains_candidate_in_red_row_and_comment(tmp_path: Path) -> None:
    rows = [_base("SB1-1", "SB1"), _base("SB1-2", "SB1", "SB_SPAN_STIRRUP_NOT_UNIFIED")]
    rows[1]["bottom_reinforcement_strategy"] = "FULL_SPAN_UNIFORM_PER_SPAN_CANDIDATE_REQUIRES_REVIEW"
    payload = _payload(rows)
    payload["exception_rows"] = [{**rows[1], "engine_status": "SB_SPAN_STIRRUP_NOT_UNIFIED"}]
    output = _render(tmp_path, payload)
    workbook = load_workbook(output, data_only=False)
    sheet = workbook["整理梁表"]
    row = next(row for row in range(1, sheet.max_row + 1) if sheet.cell(row=row, column=2).value == "SB1-2")
    assert sheet.cell(row=row, column=8).value == "3T16"
    assert sheet.cell(row=row, column=2).comment is not None
    assert "SB_SPAN_STIRRUP_NOT_UNIFIED" in sheet.cell(row=row, column=2).comment.text
    workbook.close()


def test_legacy_schedule_payload_is_readable_but_marked_candidate(tmp_path: Path) -> None:
    row = _base()
    row.pop("bottom_reinforcement_strategy")
    output = _render(
        tmp_path,
        {
            "metadata": {"schema_version": "2.0"},
            "schedule_rows": [{**row, "stirrups": "2T8@200"}],
            "exception_rows": [],
        },
    )
    workbook = load_workbook(output, data_only=False)
    sheet = workbook["整理梁表"]
    data_row = next(row for row in range(1, sheet.max_row + 1) if sheet.cell(row=row, column=2).value == "SB1-1")
    assert sheet.cell(row=data_row, column=2).comment is not None
    assert "LEGACY_REDETAIL_REQUIRED" in sheet.cell(row=data_row, column=2).comment.text
    workbook.close()


def test_data_row_is_eleven_values() -> None:
    assert len(_data_row(_base())) == 11
    assert _data_row(_base())[7] == "3T16"


def test_non_unified_stirrup_raises(tmp_path: Path) -> None:
    rows = [{**_base(), "stirrups": "2T8@200", "s1": "2T8@200", "s2": "2T10@200"}]
    with pytest.raises(ValueError, match="SB stirrup is not span-unified"):
        render_secondary_beam_xlsx(
            {"metadata": {}, "schedule_rows": rows, "exception_rows": []},
            tmp_path / "bad.xlsx",
        )


def test_non_sb_row_raises(tmp_path: Path) -> None:
    row = {**_base(), "beam_type": "PB", "stirrups": "2T8@200"}
    with pytest.raises(ValueError, match="non-SB row"):
        render_secondary_beam_xlsx(
            {"metadata": {}, "schedule_rows": [row], "exception_rows": []},
            tmp_path / "bad.xlsx",
        )
