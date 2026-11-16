"""Portable static renderer for the PB/SB beam result schedule.

The JSON payload remains the calculation and audit boundary.  This renderer
projects one beam role to one white, print-friendly ``整理梁表`` worksheet and
never writes formulas, helper sheets, hidden columns or Excel tables.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Mapping

from openpyxl import Workbook, load_workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.comments import Comment
from openpyxl.cell.text import InlineFont
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from src.design.no_design import is_explicit_no_design
from src.rebar_workbook import (
    ADJACENT_SPAN_BOTTOM_STRATEGY,
    BOTTOM_REINFORCEMENT_STRATEGY,
    RESULT_WORKBOOK_LAYOUT_VERSION,
    project_result_table_payload,
)

PAYLOAD_KEYS = ("metadata", "raw_rows", "review_rows", "final_rows")
REGIONS = ("left", "middle", "right")

PB_COLUMNS: tuple[tuple[str, str], ...] = (
    ("beam_line", "BEAM LINE"),
    ("beam_no", "SPAN"),
    ("width_mm", "WIDTH"),
    ("depth_mm", "DEPTH"),
    ("et1", "ET1"),
    ("t1", "T1"),
    ("et2", "ET2"),
    ("b1", "B1"),
    ("s2", "NEAR SUPPORT (S2)"),
    ("s1", "CONTINUOUS (S1)"),
    ("legs", "LEGS"),
    ("face", "EACH SIDE"),
)
SB_COLUMNS: tuple[tuple[str, str], ...] = (
    ("beam_line", "BEAM LINE"),
    ("beam_no", "SPAN"),
    ("width_mm", "WIDTH"),
    ("depth_mm", "DEPTH"),
    ("et1", "ET1"),
    ("t1", "T1"),
    ("et2", "ET2"),
    ("b1", "B1"),
    ("stirrups", "STIRRUPS"),
    ("legs", "LEGS"),
    ("face", "EACH SIDE"),
)

COLORS = {
    "navy": "17365D",
    "blue": "1F4E78",
    "light_blue": "DDEBF7",
    "pale_blue": "EEF5FB",
    "gray": "F3F6F8",
    "border": "B7C9D6",
    "red": "C00000",
    "pale_red": "FCE4D6",
    "text": "000000",
}
THIN_BORDER = Border(
    left=Side(style="thin", color=COLORS["border"]),
    right=Side(style="thin", color=COLORS["border"]),
    top=Side(style="thin", color=COLORS["border"]),
    bottom=Side(style="thin", color=COLORS["border"]),
)
_NUMBER_PATTERN = re.compile(r"(\d+)")

# These checks remain in the JSON/audit payload, but are outside the current
# workbook colour gate.  The project has deferred anchorage, lap splice and
# torsion-perimeter verification to a future phase, and has already confirmed
# the ETABS Frame Design design-force source for the current phase.
NON_CURRENT_REVIEW_CODES = frozenset(
    {
        "ANCHORAGE_UNVERIFIED",
        "SPLICE_UNVERIFIED",
        "TORSION_PERIMETER_LAYOUT_UNVERIFIED",
        "CAPACITY_SHEAR_SOURCE_UNVERIFIED",
        "SMF_DESIGN_SOURCE_UNVERIFIED",
        "SMF_SUPPORT_FACE_UNRESOLVED",
        "EXCLUDED_NO_DESIGN",
        "V3_PARENT_CONTINUITY_EXTERNAL_SCOPE_NO_DESIGN",
    }
)


def _natural_key(value: Any) -> tuple[Any, ...]:
    """Sort mixed labels such as ``PB2``, ``PB10`` and ``2-1`` naturally."""

    text = str(value or "").strip()
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in _NUMBER_PATTERN.split(text)
        if part != ""
    )


def _span_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    try:
        object_part_index = int(row.get("object_part_index") or 0)
    except (TypeError, ValueError):
        object_part_index = 0
    try:
        return (0, int(row.get("span_no")), object_part_index)
    except (TypeError, ValueError):
        return (1, _natural_key(row.get("beam_no", "")), object_part_index)


def _as_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _display_face(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.casefold() in {"n.a.", "n/a", "na", "none", "null"}:
        return "—"
    return text


def _display_stirrup(value: Any) -> str:
    """Format visible stirrup spacing with the conventional centre-to-centre suffix."""

    text = str(value or "").strip()
    if text and "@" in text and not re.search(r"c\s*/\s*c\s*$", text, flags=re.IGNORECASE):
        return f"{text} c/c"
    return text


def _cell_value(value: Any) -> Any:
    """Normalize collection values without producing formulas."""

    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return value


def _metadata_text(metadata: Mapping[str, Any], beam_type: str) -> str:
    return (
        f"MODEL: {metadata.get('model', '')} | UNITS: {metadata.get('units', '')} | "
        f"ROLE: {beam_type} | LAYOUT: {metadata.get('rebar_workbook_layout_version', RESULT_WORKBOOK_LAYOUT_VERSION)} | "
        f"BOTTOM: {metadata.get('bottom_reinforcement_strategy', BOTTOM_REINFORCEMENT_STRATEGY)} | "
        f"ADJACENT BOTTOM: {metadata.get('adjacent_span_bottom_strategy', ADJACENT_SPAN_BOTTOM_STRATEGY)} | "
        "STATIC VALUES ONLY; calculation and audit evidence remain in the JSON payload."
    )


def _infer_beam_type(payload: Mapping[str, Any], explicit: str | None) -> str:
    if explicit:
        role = str(explicit).strip().upper()
    else:
        metadata = payload.get("metadata", {})
        role = "SB" if "SECONDARY" in str(metadata.get("schedule_kind", "")).upper() else ""
        final_rows = list(payload.get("final_rows", ()))
        types = {
            str(row.get("beam_type", "")).strip().upper()
            for row in final_rows
            if str(row.get("beam_type", "")).strip()
        }
        if not role:
            role = "SB" if types and types <= {"SB"} else "PB"
    if role not in {"PB", "SB"}:
        raise ValueError(f"unknown beam_type: {role}")
    return role


def _aligned_rows(
    payload: Mapping[str, Any],
    beam_type: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    projected = project_result_table_payload(payload, beam_type)
    return (
        dict(projected.get("metadata", {})),
        [dict(row) for row in projected["raw_rows"]],
        [dict(row) for row in projected["review_rows"]],
        [dict(row) for row in projected["final_rows"]],
    )


def _merged_row(
    raw: Mapping[str, Any],
    review: Mapping[str, Any],
    final: Mapping[str, Any],
) -> dict[str, Any]:
    row = dict(raw)
    row.update(review)
    row.update(final)
    return row


def _current_review_tokens(value: Any) -> list[str]:
    """Return status tokens that still participate in the current colour gate."""

    tokens: list[str] = []
    for raw_token in re.split(r"[;,|]", str(value or "")):
        token = raw_token.strip()
        if token and token.upper() not in NON_CURRENT_REVIEW_CODES:
            tokens.append(token)
    return tokens


def _exception_reason(*rows: Mapping[str, Any]) -> str:
    values: dict[str, Any] = {}
    for row in rows:
        values.update(row)
    status = str(values.get("engine_status", "")).strip()
    reasons: list[str] = []
    if status and status.upper() != "OK" and status.upper() not in NON_CURRENT_REVIEW_CODES:
        reasons.append(status)
    if values.get("coordination_status") == "FEASIBLE_OPTIMIZATION_INCOMPLETE":
        reasons.append("优化未完成")
    blocking = "; ".join(_current_review_tokens(values.get("constructability_blocking_statuses")))
    if blocking:
        reasons.append(blocking)
    if (status != "EXCLUDED_NO_DESIGN"
            and values.get("bottom_reinforcement_strategy") != BOTTOM_REINFORCEMENT_STRATEGY):
        reasons.append("LEGACY_REDETAIL_REQUIRED")
    if values.get("bottom_uniform_regions_match") is False:
        reasons.append("BOTTOM_LAYOUT_NOT_UNIFORM_WITHIN_SPAN")
    warning_text = " ".join(
        str(values.get(key, ""))
        for key in ("warnings", "engine_warnings", "review_note", "capacity_shear_status")
    ).casefold()
    for code in NON_CURRENT_REVIEW_CODES:
        warning_text = warning_text.replace(code.casefold(), "")
    warning_tokens = (
        "review",
        "conflict",
        "failed",
        "fail",
        "unverified",
        "error",
        "block",
        "missing",
        "no_results",
        "not_unified",
        "requires",
        "需复核",
        "阻断",
    )
    for token in warning_tokens:
        if token in warning_text:
            reasons.append(f"WARNING:{token}")
            break
    # A benign Program Determined warning is an audit note, not a failed row.
    return "; ".join(dict.fromkeys(reasons))


def _row_values(row: Mapping[str, Any], beam_type: str) -> dict[str, Any]:
    bottom = row.get("bottom_uniform_text") or row.get("b1") or row.get("eb1") or ""
    values = {
        "beam_line": str(row.get("beam_line", "")),
        "beam_no": str(row.get("beam_no", "")),
        "width_mm": _as_number(row.get("width_mm")),
        "depth_mm": _as_number(row.get("depth_mm")),
        "et1": str(row.get("et1", "")),
        "t1": str(row.get("t1", "")),
        "et2": str(row.get("et2", "")),
        "b1": str(bottom),
        "legs": _as_number(row.get("legs")),
        "face": _display_face(row.get("face")),
    }
    if beam_type == "PB":
        values.update(
            {
                "s2": _display_stirrup(row.get("s2", row.get("s2_text", ""))),
                "s1": _display_stirrup(row.get("s1", row.get("s1_text", ""))),
            }
        )
    else:
        values["stirrups"] = _display_stirrup(
            row.get("stirrups")
            or row.get("s1")
            or row.get("s2")
            or row.get("s1_text")
            or row.get("s2_text")
            or ""
        )
    return values


def _style_range(
    sheet,
    row: int,
    start: int,
    end: int,
    *,
    fill: str,
    font: Font,
    alignment: Alignment,
) -> None:
    for column in range(start, end + 1):
        cell = sheet.cell(row=row, column=column)
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.font = font
        cell.alignment = alignment
        cell.border = THIN_BORDER


def _merge_label(
    sheet,
    row: int,
    start: int,
    end: int,
    label: str,
    *,
    fill: str,
    font: Font,
    alignment: Alignment,
) -> None:
    _style_range(sheet, row, start, end, fill=fill, font=font, alignment=alignment)
    if end > start:
        sheet.merge_cells(
            f"{get_column_letter(start)}{row}:{get_column_letter(end)}{row}"
        )
    sheet.cell(row=row, column=start, value=label)


def _write_headers(sheet, row: int, columns: tuple[tuple[str, str], ...], beam_type: str) -> None:
    group_font = Font(bold=True, color="FFFFFF", size=9)
    leaf_font = Font(bold=True, color="FFFFFF", size=9)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    if beam_type == "PB":
        groups = (
            (1, 2, "BEAM NO."),
            (3, 4, "BEAM SIZE/mm"),
            (5, 7, "TOP REBARS"),
            (8, 8, "BOTTOM REBARS"),
            (9, 10, "STIRRUPS"),
            (11, 11, "No. OF LEGGED STIRRUPS"),
            (12, 12, "FACE REBAR EACH SIDE"),
        )
    else:
        groups = (
            (1, 2, "BEAM NO."),
            (3, 4, "BEAM SIZE/mm"),
            (5, 7, "TOP REBARS"),
            (8, 8, "BOTTOM REBARS"),
            (9, 9, "STIRRUPS"),
            (10, 10, "No. OF LEGGED STIRRUPS"),
            (11, 11, "FACE REBAR EACH SIDE"),
        )
    for start, end, label in groups:
        _merge_label(sheet, row, start, end, label, fill=COLORS["blue"], font=group_font, alignment=center)
    for column, (_key, label) in enumerate(columns, start=1):
        cell = sheet.cell(row=row + 1, column=column, value=label)
        cell.fill = PatternFill("solid", fgColor=COLORS["navy"])
        cell.font = leaf_font
        cell.alignment = center
        cell.border = THIN_BORDER
    sheet.row_dimensions[row].height = 30
    sheet.row_dimensions[row + 1].height = 38


def _style_data_row(sheet, row: int, width: int, *, exception: bool) -> None:
    fill = COLORS["pale_red"] if exception else "FFFFFF"
    color = COLORS["red"] if exception else COLORS["text"]
    for column in range(1, width + 1):
        cell = sheet.cell(row=row, column=column)
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.font = Font(color=color, bold=exception, size=9)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN_BORDER
    sheet.row_dimensions[row].height = 24


def _exception_comment(row: Mapping[str, Any], reason: str) -> str:
    return (
        f"REVIEW REQUIRED: {reason}\n"
        f"Source objects: {row.get('source_objects', '')}\n"
        f"Beam line/span: {row.get('beam_line', '')}/{row.get('beam_no', '')}\n"
        f"Engine status: {row.get('engine_status', '')}\n"
        f"Constructability: {row.get('constructability_blocking_statuses', '')}\n"
        f"Review note: {row.get('review_note', '')}"
    )


def _column_widths(sheet, beam_type: str, *, span_width: int = 16) -> None:
    widths = {
        "A": 17,
        "B": max(16, span_width),
        "C": 10,
        "D": 10,
        "E": 16,
        "F": 16,
        "G": 16,
        "H": 18,
        "I": 19,
        "J": 19,
        "K": 12,
        "L": 19,
    }
    for column, width in widths.items():
        if column == "L" and beam_type == "SB":
            continue
        sheet.column_dimensions[column].width = width


def _assert_static_workbook(workbook: Workbook) -> None:
    if workbook.sheetnames != ["整理梁表"]:
        raise ValueError("result workbook must contain exactly one 整理梁表 sheet")
    sheet = workbook["整理梁表"]
    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                raise ValueError(f"formula is not allowed in static result workbook: {cell.coordinate}")
    for dimension in sheet.column_dimensions.values():
        if dimension.hidden:
            raise ValueError("hidden columns are not allowed in static result workbook")
    for dimension in sheet.row_dimensions.values():
        if dimension.hidden:
            raise ValueError("hidden rows are not allowed in static result workbook")


def render_rebar_workbook_xlsx(
    payload: Mapping[str, Any],
    output_path: str | Path,
    *,
    beam_type: str | None = None,
) -> Path:
    """Write one static PB or SB result worksheet and return its path."""

    for key in PAYLOAD_KEYS:
        if key not in payload:
            raise ValueError(f"payload missing {key}")
    raw_rows = list(payload["raw_rows"])
    review_rows = list(payload["review_rows"])
    final_rows = list(payload["final_rows"])
    if not (len(raw_rows) == len(review_rows) == len(final_rows)):
        raise ValueError("raw_rows, review_rows and final_rows must have the same length")
    role = _infer_beam_type(payload, beam_type)
    metadata, raw_rows, review_rows, final_rows = _aligned_rows(payload, role)
    columns = PB_COLUMNS if role == "PB" else SB_COLUMNS

    entries: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for raw, review, final in zip(raw_rows, review_rows, final_rows):
        merged = _merged_row(raw, review, final)
        if is_explicit_no_design(merged.get("engine_status"), merged.get("status_message")):
            continue
        entries.append((_row_values(merged, role), merged, _exception_reason(raw, review, final)))
    entries.sort(
        key=lambda item: (
            _natural_key(item[1].get("story", "")),
            _natural_key(item[1].get("beam_line", "")),
            _span_key(item[1]),
        )
    )
    span_width = max(
        (
            len(str(values["beam_no"]))
            + len(" (CANTILEVER)")
            + 2
            for values, source, _reason in entries
            if source.get("is_cantilever") is True
        ),
        default=16,
    )

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "整理梁表"
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = "A7"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.page_margins.left = 0.2
    sheet.page_margins.right = 0.2
    sheet.page_margins.top = 0.35
    sheet.page_margins.bottom = 0.35
    sheet.print_options.horizontalCentered = True
    _column_widths(sheet, role, span_width=span_width)

    last_column = len(columns)
    _merge_label(
        sheet,
        1,
        1,
        last_column,
        f"{'PRIMARY' if role == 'PB' else 'SECONDARY'} BEAM REINFORCEMENT SCHEDULE",
        fill=COLORS["navy"],
        font=Font(bold=True, color="FFFFFF", size=15),
        alignment=Alignment(horizontal="left", vertical="center"),
    )
    sheet.row_dimensions[1].height = 28
    _merge_label(
        sheet,
        2,
        1,
        last_column,
        _metadata_text(metadata, role),
        fill=COLORS["light_blue"],
        font=Font(color=COLORS["navy"], size=9),
        alignment=Alignment(vertical="center", wrap_text=True),
    )
    sheet.row_dimensions[2].height = 32
    _merge_label(
        sheet,
        3,
        1,
        last_column,
        "All displayed cells are static values. Red rows require current-phase review; deferred checks remain in the backend JSON audit.",
        fill=COLORS["gray"],
        font=Font(color="595959", italic=True, size=9),
        alignment=Alignment(vertical="center", wrap_text=True),
    )
    sheet.row_dimensions[3].height = 28

    by_story: dict[str, list[tuple[dict[str, Any], dict[str, Any], str]]] = {}
    for entry in entries:
        by_story.setdefault(str(entry[1].get("story", "")), []).append(entry)

    current_row = 4
    if not by_story:
        _merge_label(
            sheet,
            current_row,
            1,
            last_column,
            "NO ROWS FOR THIS BEAM ROLE",
            fill=COLORS["pale_blue"],
            font=Font(color=COLORS["navy"], bold=True, size=10),
            alignment=Alignment(horizontal="left", vertical="center"),
        )
        sheet.row_dimensions[current_row].height = 24
    else:
        stories = sorted(by_story.items(), key=lambda item: _natural_key(item[0]))
        for story_index, (story, story_entries) in enumerate(stories):
            _merge_label(
                sheet,
                current_row,
                1,
                last_column,
                f"FLOOR: {story}",
                fill=COLORS["pale_blue"],
                font=Font(color=COLORS["navy"], bold=True, size=10),
                alignment=Alignment(horizontal="left", vertical="center"),
            )
            sheet.row_dimensions[current_row].height = 22
            _write_headers(sheet, current_row + 1, columns, role)
            data_start = current_row + 3
            row_number = data_start
            for values, source, reason in story_entries:
                for column, (key, _label) in enumerate(columns, start=1):
                    value = _cell_value(values.get(key))
                    if key == "beam_no" and source.get("is_cantilever") is True:
                        value = CellRichText(
                            [
                                str(values["beam_no"]),
                                TextBlock(
                                    InlineFont(color=COLORS["red"]),
                                    " (CANTILEVER)",
                                ),
                            ]
                        )
                    sheet.cell(row=row_number, column=column, value=value)
                exception = bool(reason)
                _style_data_row(sheet, row_number, last_column, exception=exception)
                if exception:
                    sheet.cell(row=row_number, column=2).comment = Comment(
                        _exception_comment(source, reason), "Codex"
                    )
                    sheet.cell(row=row_number, column=8).comment = Comment(
                        f"Bottom layout candidate: {values.get('b1', '')}\nReason: {reason}",
                        "Codex",
                    )
                row_number += 1
            data_end = row_number - 1
            # A is merged only within a floor section and only for contiguous
            # rows of one beam line. Span/B is intentionally never merged.
            line_start = data_start
            previous_line = None
            for offset, (_values, source, _reason) in enumerate(story_entries):
                line = str(source.get("beam_line", ""))
                actual_row = data_start + offset
                if previous_line is None:
                    previous_line = line
                    line_start = actual_row
                    continue
                if line != previous_line:
                    if actual_row - line_start > 1:
                        sheet.merge_cells(f"A{line_start}:A{actual_row - 1}")
                    previous_line = line
                    line_start = actual_row
            if previous_line is not None and data_end - line_start >= 1:
                sheet.merge_cells(f"A{line_start}:A{data_end}")
            current_row = data_end + 2
            if story_index != len(stories) - 1:
                sheet.row_dimensions[data_end + 1].height = 8

    _assert_static_workbook(workbook)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)
    return destination


def _rebar_area_formula(cell: str) -> str:
    """Legacy helper retained for callers that inspect the former formulas.

    It is intentionally not used by :func:`render_rebar_workbook_xlsx`.
    """

    raw = f'LEFT(TRIM({cell})&" ",FIND(" ",TRIM({cell})&" ")-1)'
    token = f'IFERROR(LEFT({raw},FIND("+(",{raw})-1),{raw})'
    first = (
        f'IFERROR(VALUE(LEFT({token},FIND("T",{token})-1))*PI()*VALUE(MID({token},'
        f'FIND("T",{token})+1,FIND("+",{token}&"+")-FIND("T",{token})-1))^2/4,0)'
    )
    second = (
        f'IFERROR(VALUE(MID({token},FIND("+",{token})+1,FIND("T",{token},FIND("+",{token})+1)'
        f'-FIND("+",{token})-1))*PI()*VALUE(MID({token},FIND("T",{token},FIND("+",{token})+1)+1,'
        f"LEN({token}))),0)"
    )
    return f'=IF(OR(TRIM({cell})="",UPPER(TRIM({cell}))="N.A."),0,{first}+{second})'


def _stirrup_at_formula(cell: str) -> str:
    """Legacy helper retained for compatibility; never written to the output."""

    return f'=IFERROR(PI()*VALUE(MID({cell},2,FIND("@",{cell})-2))^2/4/VALUE(MID({cell},FIND("@",{cell})+1,99)),0)'


def _scan_formula_errors(workbook_path: str | Path) -> list[str]:
    """Compatibility helper: scan a workbook for formula error literals."""

    error_pattern = ("#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#N/A")
    errors: list[str] = []
    workbook = load_workbook(workbook_path, data_only=False)
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    if any(token in cell.value for token in error_pattern):
                        errors.append(f"{sheet.title}!{cell.coordinate}: {cell.value}")
    workbook.close()
    return errors
