"""Unit tests for the portable openpyxl SB schedule renderer."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import load_workbook

from src.spreadsheet.render_secondary_beam_xlsx import (
    _natural_key,
    render_secondary_beam_xlsx,
)


def _sb(no: str, line: str, story: str, span: int = 1) -> dict:
    return {
        "beam_no": no,
        "beam_line": line,
        "story": story,
        "span_no": span,
        "beam_type": "SB",
        "section": "250x450",
        "width_mm": 250,
        "depth_mm": 450,
        "et1": "3T16",
        "t1": "3T16",
        "et2": "3T16",
        "eb1": "3T16",
        "b1": "3T16",
        "eb2": "3T16",
        "s1": "T8@200",
        "s2": "T8@200",
        "stirrups": "T8@200",
        "legs": 2,
        "face": "2T12",
        "source_objects": ["O1"],
        "engine_status": "OK",
    }


def _payload(**overrides) -> dict:
    payload = {
        "metadata": {
            "schema_version": "2.0",
            "schedule_kind": "SECONDARY_BEAM_REINFORCEMENT_SCHEDULE",
            "secondary_schedule_rows": 2,
            "secondary_exception_rows": 1,
        },
        "schedule_rows": [
            _sb("F2-1", "F2", "GF", 1),
            _sb("F10-1", "F10", "GF", 1),
            _sb("F1-1", "F1", "GF", 1),
        ],
        "exception_rows": [
            {
                "story": "GF",
                "beam_line": "X",
                "beam_no": "X-1",
                "section": "300x600",
                "engine_status": "SB_SPAN_STIRRUP_NOT_UNIFIED",
                "source_objects": ["O9"],
            }
        ],
    }
    payload.update(overrides)
    return payload


def _render(tmp_path: Path, payload: dict | None = None) -> Path:
    out = tmp_path / "secondary.xlsx"
    render_secondary_beam_xlsx(payload or _payload(), out)
    return out


def test_natural_key_sorts_numbers_naturally() -> None:
    values = ["F10", "F2", "F1", "F10a"]
    assert sorted(values, key=_natural_key) == ["F1", "F2", "F10", "F10a"]


def test_schedule_sorts_naturally_and_renders(tmp_path: Path) -> None:
    out = _render(tmp_path)
    wb = load_workbook(out, data_only=False)
    ws = wb["SB梁表"]
    assert ws.freeze_panes == "C4"
    # One story "GF": title row1, header rows2-3, data rows4-6 ordered F1, F2, F10.
    assert ws["A4"].value == "F1"
    assert ws["A5"].value == "F2"
    assert ws["A6"].value == "F10"
    assert ws["B4"].value == "F1-1"
    assert ws["K4"].value == "T8@200"
    wb.close()


def test_exception_sheet_renders_rows(tmp_path: Path) -> None:
    out = _render(tmp_path)
    wb = load_workbook(out, data_only=False)
    ws = wb["复核异常"]
    assert ws["A1"].value == "SB 梁表复核异常"
    assert ws["A2"].value == "楼层"
    assert ws["A3"].value == "GF"
    assert ws["E3"].value == "SB_SPAN_STIRRUP_NOT_UNIFIED"
    assert ws["F3"].value == "O9"
    wb.close()


def test_empty_schedule_shows_message(tmp_path: Path) -> None:
    payload = _payload(schedule_rows=[])
    out = _render(tmp_path, payload)
    wb = load_workbook(out, data_only=False)
    ws = wb["SB梁表"]
    assert ws["A1"].value == "SECONDARY BEAM REINFORCEMENT SCHEDULE"
    assert "无可交付 SB 行" in str(ws["A2"].value)
    wb.close()


def test_non_unified_stirrup_raises(tmp_path: Path) -> None:
    rows = [_sb("B1-1", "B1", "GF", 1)]
    rows[0]["s1"] = "T10@200"
    payload = _payload(schedule_rows=rows)
    with pytest.raises(ValueError, match="SB stirrup is not span-unified"):
        render_secondary_beam_xlsx(payload, tmp_path / "bad.xlsx")


def test_non_sb_row_raises(tmp_path: Path) -> None:
    rows = [_sb("B1-1", "B1", "GF", 1)]
    rows[0]["beam_type"] = "PB"
    payload = _payload(schedule_rows=rows)
    with pytest.raises(ValueError, match="non-SB row"):
        render_secondary_beam_xlsx(payload, tmp_path / "bad.xlsx")
