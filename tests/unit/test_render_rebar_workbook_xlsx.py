"""Tests for the portable static PB/SB result-table renderer."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook
from openpyxl.cell.rich_text import CellRichText

from src.spreadsheet.render_rebar_workbook_xlsx import (
    _display_stirrup,
    _exception_reason,
    _rebar_area_formula,
    _scan_formula_errors,
    _span_key,
    _stirrup_at_formula,
    render_rebar_workbook_xlsx,
)
from src.rebar_workbook import _apply_v3_recommendation_row_audits


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
    assert sheet["I7"].value == "2T10@100 c/c"
    assert sheet["J7"].value == "2T10@200 c/c"
    assert sheet["K7"].value == 2 and isinstance(sheet["K7"].value, int)
    assert sheet["L7"].value == "—"
    assert not any(str(value).startswith("B7:B8") for value in sheet.merged_cells.ranges)
    workbook.close()


def test_object_part_sorting_is_numeric_not_lexical() -> None:
    rows = [
        {"span_no": 2, "object_part_index": 10, "beam_no": "B-2.10"},
        {"span_no": 2, "object_part_index": 2, "beam_no": "B-2.2"},
        {"span_no": 1, "object_part_index": 4, "beam_no": "B-1.4"},
    ]
    assert [row["beam_no"] for row in sorted(rows, key=_span_key)] == ["B-1.4", "B-2.2", "B-2.10"]


def test_exception_candidate_is_retained_red_and_commented(tmp_path: Path) -> None:
    output = _render(tmp_path)
    workbook = load_workbook(output, data_only=False)
    sheet = workbook["整理梁表"]
    normal_row = next(row for row in _data_rows(sheet) if sheet.cell(row=row, column=2).value == "B2-2")
    assert sheet.cell(row=normal_row, column=2).font.color.type == "rgb"
    assert sheet.cell(row=normal_row, column=2).font.color.rgb.endswith("000000")
    row = next(row for row in _data_rows(sheet) if sheet.cell(row=row, column=2).value == "B11-1")
    assert sheet.cell(row=row, column=8).value == "4T25"
    assert sheet.cell(row=row, column=2).comment is not None
    assert "NO_RESULTS" in sheet.cell(row=row, column=2).comment.text
    assert sheet.cell(row=row, column=2).font.color.type == "rgb"
    assert sheet.cell(row=row, column=2).font.color.rgb.endswith("C00000")
    workbook.close()


@pytest.mark.parametrize("beam_type", ["PB", "SB"])
def test_final_workbook_omits_only_explicit_no_design_rows(
    tmp_path: Path,
    beam_type: str,
) -> None:
    rows = [
        _row(beam_type=beam_type, line="B1", status="OK"),
        _row(beam_type=beam_type, line="B2", status="EXCLUDED_NO_DESIGN"),
        _row(beam_type=beam_type, line="B3", status="NO_RESULTS", face="N.A."),
    ]
    payload = _payload(rows)
    output = _render(tmp_path, payload, beam_type=beam_type)
    workbook = load_workbook(output, data_only=True)
    sheet = workbook["整理梁表"]
    displayed = [sheet.cell(row=row, column=2).value for row in _data_rows(sheet)]

    assert displayed == ["B1-1", "B3-1"]
    assert payload["final_rows"][1]["engine_status"] == "EXCLUDED_NO_DESIGN"
    assert payload["final_rows"][2]["face"] == "N.A."
    workbook.close()


@pytest.mark.parametrize("beam_type", ["PB", "SB"])
def test_cantilever_span_suffix_is_red_and_preserves_span_number(
    tmp_path: Path,
    beam_type: str,
) -> None:
    rows = [
        _row(beam_type=beam_type, story="GF", line="T1-LG-PB23", span=1),
        _row(beam_type=beam_type, story="GF", line="T1-LG-PB23", span=2),
        _row(beam_type=beam_type, story="GF", line="T1-LG-PB23", span=3),
    ]
    rows[0].update({"is_cantilever": True, "width_mm": 400, "depth_mm": 1000})
    rows[1].update({"is_cantilever": False, "width_mm": 400, "depth_mm": 750})
    rows[2].update({"is_cantilever": False, "width_mm": 400, "depth_mm": 750})
    output = _render(tmp_path, _payload(rows), beam_type=beam_type)
    workbook = load_workbook(output, data_only=False, rich_text=True)
    sheet = workbook["整理梁表"]
    data_rows = [
        row
        for row in range(1, sheet.max_row + 1)
        if str(sheet.cell(row=row, column=2).value).startswith("T1-LG-PB23-")
    ]

    cantilever_cell = sheet.cell(row=data_rows[0], column=2)
    assert isinstance(cantilever_cell.value, CellRichText)
    assert str(cantilever_cell.value) == "T1-LG-PB23-1 (CANTILEVER)"
    assert cantilever_cell.value[0] == "T1-LG-PB23-1"
    assert cantilever_cell.value[1].text == " (CANTILEVER)"
    assert cantilever_cell.value[1].font.color.rgb.endswith("C00000")
    assert cantilever_cell.font.color.rgb.endswith("000000")
    assert [str(sheet.cell(row=row, column=2).value) for row in data_rows] == [
        "T1-LG-PB23-1 (CANTILEVER)",
        "T1-LG-PB23-2",
        "T1-LG-PB23-3",
    ]
    assert [sheet.cell(row=row, column=4).value for row in data_rows] == [1000, 750, 750]
    assert sheet.column_dimensions["B"].width >= len(str(cantilever_cell.value)) + 2
    assert "A7:A9" in {str(value) for value in sheet.merged_cells.ranges}
    workbook.close()


def test_cantilever_marker_keeps_existing_exception_row_comment(tmp_path: Path) -> None:
    row = _row(status="NO_RESULTS")
    row["is_cantilever"] = True
    output = _render(tmp_path, _payload([row]))
    workbook = load_workbook(output, data_only=False, rich_text=True)
    sheet = workbook["整理梁表"]
    data_row = next(
        row_number
        for row_number in range(1, sheet.max_row + 1)
        if str(sheet.cell(row=row_number, column=2).value).startswith("B2-1")
    )
    cell = sheet.cell(row=data_row, column=2)

    assert str(cell.value) == "B2-1 (CANTILEVER)"
    assert cell.font.color.rgb.endswith("C00000")
    assert cell.value[1].font.color.rgb.endswith("C00000")
    assert cell.fill.fgColor.rgb.endswith("FCE4D6")
    assert cell.comment is not None
    assert "NO_RESULTS" in cell.comment.text
    workbook.close()


def test_stirrup_display_adds_center_to_center_suffix_once() -> None:
    assert _display_stirrup("T10@200") == "T10@200 c/c"
    assert _display_stirrup("T10@200 c/c") == "T10@200 c/c"
    assert _display_stirrup("T10@200 C / C") == "T10@200 C / C"


def test_deferred_constructability_and_confirmed_force_source_are_not_current_red_gate() -> None:
    row = _row()
    row.update(
        {
            "constructability_blocking_statuses": (
                "ANCHORAGE_UNVERIFIED; SPLICE_UNVERIFIED; "
                "TORSION_PERIMETER_LAYOUT_UNVERIFIED"
            ),
            "engine_warnings": (
                "CAPACITY_SHEAR_SOURCE_UNVERIFIED; SMF_DESIGN_SOURCE_UNVERIFIED; "
                "SMF_SUPPORT_FACE_UNRESOLVED"
            ),
            "capacity_shear_status": "CAPACITY_SHEAR_SOURCE_UNVERIFIED",
        }
    )

    assert _exception_reason(row) == ""


def test_current_phase_engine_or_layout_issue_still_marks_row_for_review() -> None:
    row = _row()
    row["constructability_blocking_statuses"] = "ANCHORAGE_UNVERIFIED; BEAM_LINE_COORDINATION_BLOCKED"

    reason = _exception_reason(row)

    assert "BEAM_LINE_COORDINATION_BLOCKED" in reason


def test_partial_v3_line_keeps_local_bars_and_marks_selected_row_in_xlsx(tmp_path: Path) -> None:
    row = _row()
    row.update({
        "coordination_status": "V3_RECOMMENDATION_SET_ONLY",
        "mode": "NORMAL",
        "engine_warnings": "ANCHORAGE_UNVERIFIED",
        "longitudinal_rows": {
            region: {
                "top": [{"count": 4, "diameter_mm": 20, "role": "longitudinal"}],
                "bottom": [{"count": 4, "diameter_mm": 25, "role": "longitudinal"}],
            }
            for region in ("left", "middle", "right")
        },
        "region_stirrups": {
            region: {"diameter_mm": 10, "spacing_mm": 150, "legs": 2}
            for region in ("left", "middle", "right")
        },
    })
    raw = [dict(row)]
    review = [dict(row)]
    review[0]["longitudinal_rows"] = None
    review[0]["region_stirrups"] = None
    final = [dict(row)]
    final[0]["longitudinal_rows"] = None
    engine = SimpleNamespace(_last_v3_recommendation_row_audits={
        0: {
            "v3_recommendation_status": "PARTIAL_RECOMMENDATION_UNKNOWN_ACROSS_UNSOLVED_SPAN",
            "v3_recommendation_rank": 1,
            "v3_candidate_count": 10,
            "v3_candidate_limit": 10,
        }
    })

    _apply_v3_recommendation_row_audits(raw, engine, review, final)

    assert final[0]["engine_status"] == "OK"
    assert final[0]["et1"] == "4T20"
    assert "ANCHORAGE_UNVERIFIED" in final[0]["engine_warnings"]
    assert "局部配筋已通过当前方案校核" in final[0]["review_note"]
    assert "跨缺口连续性未知" in final[0]["review_note"]
    payload = _payload()
    payload.update({"raw_rows": raw, "review_rows": review, "final_rows": final})

    output = _render(tmp_path, payload)
    workbook = load_workbook(output, data_only=False)
    sheet = workbook["整理梁表"]
    data_row = next(
        row_number
        for row_number in range(1, sheet.max_row + 1)
        if str(sheet.cell(row=row_number, column=2).value).startswith("B2-1")
    )
    span_cell = sheet.cell(row=data_row, column=2)
    et1_cell = sheet.cell(row=data_row, column=5)
    b1_cell = sheet.cell(row=data_row, column=8)
    assert et1_cell.value == "4T20"
    assert b1_cell.value == "4T25"
    assert span_cell.fill.fgColor.rgb.endswith("FCE4D6")
    assert span_cell.comment is not None
    assert "PARTIAL_RECOMMENDATION_UNKNOWN_ACROSS_UNSOLVED_SPAN" in span_cell.comment.text
    assert "跨缺口连续性未知" in span_cell.comment.text
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
    assert sheet["I7"].value == "2T10@200 c/c"
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
