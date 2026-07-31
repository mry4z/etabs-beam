"""Unit tests for the portable openpyxl rebar-workbook renderer."""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from src.spreadsheet.render_rebar_workbook_xlsx import (
    _rebar_area_formula,
    _scan_formula_errors,
    _stirrup_at_formula,
    render_rebar_workbook_xlsx,
)


def _base_row() -> dict:
    return {
        "beam_no": "B1-1",
        "beam_line": "B1",
        "span_no": 1,
        "story": "GF",
        "beam_type": "PB",
        "section": "300x600",
        "width_mm": 300,
        "depth_mm": 600,
        "length_mm": 6000,
        "is_smf": False,
        "source_objects": ["OBJ1"],
        "detailing_profile": "PB_OMF",
        "fc_mpa": 30,
        "fy_mpa": 420,
        "fyt_mpa": 420,
        "etabs_longitudinal_cover_mm": 45.0,
        "clear_cover_to_stirrup_outer_mm": 40.0,
        "max_aggregate_size_mm": 25.0,
        "clear_cover_source": "Project",
        "max_aggregate_size_source": "Project default",
        "top_total_mm2": 1200,
        "bottom_total_mm2": 900,
        "av_over_s": 0.5,
        "al_mm2": 100,
        "at_over_s": 0.1,
        "engine_status": "OK",
    }


def _make_payload(rows: list[dict] | None = None) -> dict:
    if rows is None:
        rows = [_review_row(), _review_row_engine_blocked()]
    return {
        "metadata": {
            "schema_version": "2.0",
            "model": "test.EDB",
            "units": "N_mm_C",
            "design_code": "ACI 318-14",
            "frame_objects": 2,
            "beam_lines": 1,
            "spans": 2,
            "cover_calculation_basis": "CLEAR_TO_STIRRUP_OUTER",
            "clear_cover_to_stirrup_outer_mm": 40.0,
            "clear_cover_source": "Project",
            "max_aggregate_size_mm": 25.0,
            "max_aggregate_size_source": "Project default",
            "minimum_bars_per_layer": 2,
            "minimum_main_diameter_mm": 16,
            "hanger_diameter_mm": 12,
        },
        "raw_rows": [_raw_row()],
        "review_rows": rows,
        "final_rows": [_final_row(r) for r in rows],
    }


def _review_row() -> dict:
    base = _base_row()
    base.update(
        {
            "mode": "NORMAL",
            "legs": 2,
            "face_text": "2T12",
            "face_required_mm2": 200,
            "et1_text": "8T25 6/2",
            "et1_required_mm2": 1200,
            "t1_text": "4T25",
            "t1_required_mm2": 800,
            "et2_text": "8T25 6/2",
            "et2_required_mm2": 1100,
            "eb1_text": "4T25",
            "eb1_required_mm2": 900,
            "b1_text": "4T25",
            "b1_required_mm2": 900,
            "eb2_text": "4T25",
            "eb2_required_mm2": 900,
            "s2_text": "T10@200",
            "s1_text": "T10@200",
            "s2_left_at_required": 0.35,
            "s2_left_combo_required": 0.35,
            "s2_right_at_required": 0.35,
            "s2_right_combo_required": 0.35,
            "s1_at_required": 0.3,
            "s1_combo_required": 0.3,
        }
    )
    return base


def _review_row_engine_blocked() -> dict:
    row = _review_row()
    row["engine_status"] = "NO_RESULTS"
    return row


def _final_row(review: dict) -> dict:
    row = dict(review)
    row.update(
        {
            "et1": "8T25 6/2",
            "t1": "4T25",
            "et2": "8T25 6/2",
            "eb1": "4T25",
            "b1": "4T25",
            "eb2": "4T25",
            "s2": "T10@200",
            "s1": "T10@200",
            "face": "2T12",
            "legs": 2,
            "mode": "NORMAL",
        }
    )
    return row


def _raw_row() -> dict:
    row = _base_row()
    for prefix in ("left", "middle", "right"):
        for key, value in _base_row().items():
            row[f"{prefix}_{key}"] = value
    return row


def _render(tmp_path: Path, payload: dict | None = None) -> Path:
    out = tmp_path / "workbook.xlsx"
    render_rebar_workbook_xlsx(payload or _make_payload(), out)
    return out


def test_render_produces_three_sheets_with_layout(tmp_path: Path) -> None:
    out = _render(tmp_path)
    wb = load_workbook(out, data_only=False)
    assert wb.sheetnames == ["原始分跨数据", "配筋调整复核", "整理梁表"]
    raw = wb["原始分跨数据"]
    assert raw["A5"].value == "梁编号"
    assert raw["A6"].value is not None
    assert raw.freeze_panes == "G6"
    review = wb["配筋调整复核"]
    assert review["A5"].value == "梁编号"
    assert review.freeze_panes == "G6"
    final = wb["整理梁表"]
    assert final["A5"].value == "梁线编号"
    assert final.freeze_panes == "C6"
    tables = {name: ref for ws in wb.worksheets for name, ref in ws.tables.items()}
    assert tables["RawSpanData"].startswith("A5:")
    assert tables["RebarReviewTable"].startswith("A5:")
    assert tables["FinalBeamSchedule"].startswith("A5:")
    wb.close()


def test_area_formula_parses_notation_and_ignores_layering() -> None:
    formula = _rebar_area_formula("Z6")
    assert formula.startswith('=IF(OR(TRIM(Z6)="",UPPER(TRIM(Z6))="N.A."),0,')
    assert 'FIND("+(",' in formula  # bracket hanger stripping
    assert "PI()" in formula
    assert "8T25" not in formula  # token is positional, not the literal notation


def test_stirrup_formula_parses_at_notation() -> None:
    formula = _stirrup_at_formula("BC6")
    assert formula == ('=IFERROR(PI()*VALUE(MID(BC6,2,FIND("@",BC6)-2))^2/4/' 'VALUE(MID(BC6,FIND("@",BC6)+1,99)),0)')


def test_review_ok_row_writes_formulas_and_quoted_engine_row(tmp_path: Path) -> None:
    out = _render(tmp_path)
    wb = load_workbook(out, data_only=False)
    review = wb["配筋调整复核"]
    # Row 6 is the OK row: actual/diff/status are formulas.
    assert review["AA6"].value.startswith("=IF(OR(TRIM(Z6)=")
    assert review["AB6"].value == "=AA6-Y6"
    assert review["AC6"].value == '=IF($M6<>"OK","ENGINE",IF(AA6+0.001>=Y6,"OK","NG"))'
    # Row 7 is ENGINE-blocked: actual/diff are quoted empty, status formula remains.
    assert review["AA7"].value == '=""'
    assert review["AB7"].value == '=""'
    assert review["AC7"].value == '=IF($M7<>"OK","ENGINE",IF(AA7+0.001>=Y7,"OK","NG"))'
    wb.close()


def test_face_status_keeps_na_branch(tmp_path: Path) -> None:
    out = _render(tmp_path)
    wb = load_workbook(out, data_only=False)
    review = wb["配筋调整复核"]
    assert '"N.A."' in review["X6"].value
    assert 'UPPER(TRIM(U6))="N.A."' in review["X6"].value
    wb.close()


def test_stirrup_status_uses_and_dual_judgement(tmp_path: Path) -> None:
    out = _render(tmp_path)
    wb = load_workbook(out, data_only=False)
    review = wb["配筋调整复核"]
    # s1_status is BQ; s1_at_actual=BO, s1_at_required=BM, s1_combo_actual=BP, s1_combo_required=BN
    assert review["BQ6"].value == ('=IF($M6<>"OK","ENGINE",IF(AND(BO6+0.000001>=BM6,BP6+0.000001>=BN6),"OK","NG"))')
    wb.close()


def test_final_uses_guarded_cross_sheet_references(tmp_path: Path) -> None:
    out = _render(tmp_path)
    wb = load_workbook(out, data_only=False)
    final = wb["整理梁表"]
    # A6 (beam_line) is an unguarded direct reference to review column B (beam_line).
    assert final["A6"].value == "='配筋调整复核'!B6"
    # G6 (et1) is guarded by the review engine status.
    assert final["G6"].value == "=IF('配筋调整复核'!M6<>\"OK\",\"\",'配筋调整复核'!Z6)"
    wb.close()


def test_qc_formula_covers_all_status_columns(tmp_path: Path) -> None:
    out = _render(tmp_path)
    wb = load_workbook(out, data_only=False)
    final = wb["整理梁表"]
    qc = final["S6"].value
    assert qc.startswith('=IF(\'配筋调整复核\'!M6<>"OK","ENGINE_ERROR",IF(OR(')
    # 10 status columns each referenced with ="NG"
    assert qc.count('="NG"') == 10
    assert "'配筋调整复核'!X6=\"NG\"" in qc  # face_status
    assert "'配筋调整复核'!BQ6=\"NG\"" in qc  # s1_status
    wb.close()


def test_conditional_formatting_present(tmp_path: Path) -> None:
    out = _render(tmp_path)
    wb = load_workbook(out, data_only=False)
    review = wb["配筋调整复核"]
    rules: list = []
    for cell_range in review.conditional_formatting:
        for rule in cell_range.rules:
            rules.append(rule.formula[0] if rule.formula else "")
    assert any('SEARCH("OK"' in formula for formula in rules)
    assert any('SEARCH("NG"' in formula for formula in rules)
    assert any('SEARCH("ENGINE"' in formula for formula in rules)
    wb.close()


def test_scan_formula_errors_clean_and_detects_injection(tmp_path: Path) -> None:
    out = _render(tmp_path)
    assert _scan_formula_errors(out) == []
    wb = load_workbook(out, data_only=False)
    wb["配筋调整复核"]["Z99"] = "=#REF!Z99"
    wb.save(out)
    wb.close()
    errors = _scan_formula_errors(out)
    assert any("#REF!" in error for error in errors)


def test_empty_rows_render_headers_only(tmp_path: Path) -> None:
    payload = _make_payload(rows=[])
    out = _render(tmp_path, payload)
    wb = load_workbook(out, data_only=False)
    review = wb["配筋调整复核"]
    assert review["A6"].value is None  # no data rows
    assert review["A5"].value == "梁编号"
    wb.close()


def test_raw_expands_three_positions_per_row(tmp_path: Path) -> None:
    out = _render(tmp_path)
    wb = load_workbook(out, data_only=False)
    raw = wb["原始分跨数据"]
    assert raw.max_row == 8  # 1 payload row * 3 positions + 5 header rows
    positions = {raw.cell(row=row, column=6).value for row in range(6, raw.max_row + 1)}
    assert positions == {"左端2D", "跨中", "右端2D"}
    wb.close()
