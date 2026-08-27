"""Tests for the portable static PB/SB result-table renderer."""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from src.spreadsheet.render_rebar_workbook_xlsx import (
    _rebar_area_formula,
    _scan_formula_errors,
    _stirrup_at_formula,
    render_rebar_workbook_xlsx,
)


def _row(
    *,
    beam_type: str = "PB",
    story: str = "L1",
    line: str = "B2",
    span: int = 1,
    status: str = "OK",
    face: str = "N.A.",
) -> dict:
    row = {
        "beam_no": f"{line}-{span}",
        "beam_line": line,
        "span_no": span,
        "story": story,
        "beam_type": beam_type,
        "section": "300x600",
        "width_mm": 300,
        "depth_mm": 600,
        "length_mm": 6000,
        "source_objects": "OBJ1, OBJ2",
        "engine_status": status,
        "et1": "4T20",
        "t1": "4T20",
        "et2": "4T20",
        "eb1": "4T25",
        "b1": "4T25",
        "eb2": "4T25",
        "bottom_uniform_text": "4T25",
        "bottom_uniform_regions_match": True,
        "bottom_reinforcement_strategy": "FULL_SPAN_UNIFORM_PER_SPAN",
        "s2": "2T10@100",
        "s1": "2T10@200",
        "stirrups": "2T10@200",
        "legs": 2,
        "face": face,
        "constructability_blocking_statuses": "",
        "warnings": "",
        "engine_warnings": "",
        "review_note": "",
    }
    if status != "OK":
        row["bottom_reinforcement_strategy"] = "FULL_SPAN_UNIFORM_PER_SPAN_CANDIDATE_REQUIRES_REVIEW"
        row["bottom_uniform_regions_match"] = None
        row["review_note"] = "candidate retained"
    return row


def _payload(rows: list[dict] | None = None) -> dict:
    rows = rows or [
        _row(story="L1", line="B2", span=10),
        _row(story="L1", line="B2", span=2),
        _row(story="L2", line="B10", span=1),
        _row(story="L2", line="B11", span=1, status="NO_RESULTS", face="N.A."),
    ]
    return {
        "metadata": {
            "schema_version": "2.1",
            "model": "test.EDB",
            "units": "N_mm_C",
            "design_code": "ACI 318-14",
            "rebar_workbook_layout_version": "single-sheet-static-v1",
            "bottom_reinforcement_strategy": "FULL_SPAN_UNIFORM_PER_SPAN",
            "adjacent_span_bottom_strategy": "INDEPENDENT_PER_SPAN",
        },
        "raw_rows": [dict(row) for row in rows],
        "review_rows": [dict(row) for row in rows],
        "final_rows": [dict(row) for row in rows],
    }


def _render(tmp_path: Path, payload: dict | None = None, *, beam_type: str | None = None) -> Path:
    output = tmp_path / "workbook.xlsx"
    render_rebar_workbook_xlsx(payload or _payload(), output, beam_type=beam_type)
    return output


def _data_rows(sheet):
    return [
        row
        for row in range(1, sheet.max_row + 1)
        if isinstance(sheet.cell(row=row, column=2).value, str)
        and "-" in sheet.cell(row=row, column=2).value
    ]


def test_render_produces_one_static_pb_sheet_with_grouped_headers(tmp_path: Path) -> None:
    output = _render(tmp_path)
    workbook = load_workbook(output, data_only=False)
    assert workbook.sheetnames == ["整理梁表"]
    sheet = workbook["整理梁表"]
    assert sheet.max_column == 12
    assert sheet["A1"].value == "PRIMARY BEAM REINFORCEMENT SCHEDULE"
    assert sheet["A5"].value == "BEAM NO."
    assert sheet["A6"].value == "BEAM LINE"
    assert sheet["H6"].value == "B1"
    assert sheet["I6"].value == "NEAR SUPPORT (S2)"
    assert sheet["J6"].value == "CONTINUOUS (S1)"
    assert "A5:B5" in {str(value) for value in sheet.merged_cells.ranges}
    assert "A7:A8" in {str(value) for value in sheet.merged_cells.ranges}
    assert all(not (isinstance(cell.value, str) and cell.value.startswith("=")) for row in sheet.iter_rows() for cell in row)
    assert sheet.tables == {}
    assert sheet.freeze_panes == "A7"
    workbook.close()


def test_floor_and_numeric_span_sorting_and_merge_scope(tmp_path: Path) -> None:
    output = _render(tmp_path)
    workbook = load_workbook(output, data_only=False)
    sheet = workbook["整理梁表"]
    data_rows = _data_rows(sheet)
    assert [sheet.cell(row=row, column=2).value for row in data_rows[:3]] == ["B2-2", "B2-10", "B10-1"]
    assert sheet["A7"].value == "B2"
    assert sheet["B7"].value == "B2-2"
    assert sheet["B8"].value == "B2-10"
    assert sheet["C7"].value == 300 and isinstance(sheet["C7"].value, int)
    assert sheet["D7"].value == 600 and isinstance(sheet["D7"].value, int)
    assert sheet["K7"].value == 2 and isinstance(sheet["K7"].value, int)
    assert sheet["L7"].value == "—"
    assert not any(str(value).startswith("B7:B8") for value in sheet.merged_cells.ranges)
    workbook.close()


def test_exception_candidate_is_retained_red_and_commented(tmp_path: Path) -> None:
    output = _render(tmp_path)
    workbook = load_workbook(output, data_only=False)
    sheet = workbook["整理梁表"]
    row = next(row for row in _data_rows(sheet) if sheet.cell(row=row, column=2).value == "B11-1")
    assert sheet.cell(row=row, column=8).value == "4T25"
    assert sheet.cell(row=row, column=2).comment is not None
    assert "NO_RESULTS" in sheet.cell(row=row, column=2).comment.text
    assert sheet.cell(row=row, column=2).font.color.type == "rgb"
    assert sheet.cell(row=row, column=2).font.color.rgb.endswith("C00000")
    workbook.close()


def test_sb_projection_has_exactly_eleven_columns(tmp_path: Path) -> None:
    rows = [
        _row(beam_type="SB", story="GF", line="SB1", span=1),
        _row(beam_type="PB", story="GF", line="B1", span=1),
    ]
    output = _render(tmp_path, _payload(rows), beam_type="SB")
    workbook = load_workbook(output, data_only=False)
    sheet = workbook["整理梁表"]
    assert sheet.max_column == 11
    assert sheet["A1"].value == "SECONDARY BEAM REINFORCEMENT SCHEDULE"
    assert sheet["I6"].value == "STIRRUPS"
    assert sheet["J6"].value == "LEGS"
    assert sheet["K6"].value == "EACH SIDE"
    assert sheet["L1"].value is None
    workbook.close()


def test_area_formula_helper_is_legacy_only() -> None:
    formula = _rebar_area_formula("Z6")
    assert formula.startswith('=IF(OR(TRIM(Z6)="",UPPER(TRIM(Z6))="N.A."),0,')
    assert 'FIND("+(",' in formula
    assert "PI()" in formula


def test_stirrup_formula_helper_is_legacy_only() -> None:
    formula = _stirrup_at_formula("BC6")
    assert formula == ('=IFERROR(PI()*VALUE(MID(BC6,2,FIND("@",BC6)-2))^2/4/' 'VALUE(MID(BC6,FIND("@",BC6)+1,99)),0)')


def test_scan_formula_errors_clean_and_detects_injection(tmp_path: Path) -> None:
    output = _render(tmp_path)
    assert _scan_formula_errors(output) == []
    workbook = load_workbook(output, data_only=False)
    workbook["整理梁表"]["Z99"] = "=#REF!Z99"
    workbook.save(output)
    workbook.close()
    assert any("#REF!" in error for error in _scan_formula_errors(output))


def test_empty_role_keeps_single_sheet_and_headers(tmp_path: Path) -> None:
    output = _render(tmp_path, _payload([]), beam_type="SB")
    workbook = load_workbook(output, data_only=False)
    sheet = workbook["整理梁表"]
    assert workbook.sheetnames == ["整理梁表"]
    assert sheet.max_column == 11
    assert sheet["A4"].value == "NO ROWS FOR THIS BEAM ROLE"
    workbook.close()
