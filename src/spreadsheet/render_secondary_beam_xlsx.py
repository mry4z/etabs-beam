"""openpyxl renderer for the standalone secondary (SB) beam schedule.

Mirror of ``src/spreadsheet/build_secondary_beam_workbook.mjs`` for the
portable workflow (no node).  The payload is ``build_secondary_schedule_payload``
output (metadata / schedule_rows / exception_rows); no preview PNGs are rendered.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

PAYLOAD_KEYS = ("metadata", "schedule_rows", "exception_rows")
MAGENTA = "FF00FF"
PALE_PINK = "FFF2F8"
PALE_BLUE = "DDEBF7"
NAVY = "17365D"
LIGHT_BORDER = "B4C6E7"
TOTAL_COLUMNS = 13  # A..M

MAGENTA_BORDER = Border(
    left=Side(style="thin", color=MAGENTA),
    right=Side(style="thin", color=MAGENTA),
    top=Side(style="thin", color=MAGENTA),
    bottom=Side(style="thin", color=MAGENTA),
)
LIGHT_OUTSIDE_BORDER = Border(
    left=Side(style="thin", color=LIGHT_BORDER),
    right=Side(style="thin", color=LIGHT_BORDER),
    top=Side(style="thin", color=LIGHT_BORDER),
    bottom=Side(style="thin", color=LIGHT_BORDER),
)


def _natural_key(value: Any) -> tuple:
    """Natural sort key matching JS ``localeCompare(numeric: true)``."""
    parts = re.split(r"(\d+)", str(value))
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
            int(row.get("span_no") or 0),
        ),
    )


def _data_row(row: dict[str, Any]) -> list[Any]:
    return [
        row.get("beam_line") or "",
        row.get("beam_no") or "",
        int(row.get("width_mm") or 0),
        int(row.get("depth_mm") or 0),
        row.get("et1") or "",
        row.get("t1") or "",
        row.get("et2") or "",
        row.get("eb1") or "",
        row.get("b1") or "",
        row.get("eb2") or "",
        row.get("stirrups") or "",
        int(row.get("legs") or 0),
        row.get("face") or "",
    ]


def _set_row_border(sheet, start_row: int, end_row: int, columns: int, border: Border) -> None:
    for row in range(start_row, end_row + 1):
        for column in range(1, columns + 1):
            sheet.cell(row=row, column=column).border = border


def render_secondary_beam_xlsx(payload: Mapping[str, Any], output_path: str | Path) -> Path:
    """Write the SB schedule workbook (SB梁表 + 复核异常) and return the path."""
    for key in PAYLOAD_KEYS:
        if key not in payload:
            raise ValueError(f"payload missing {key}")

    schedule_rows = _sorted_rows(list(payload["schedule_rows"]))
    exception_rows = _sorted_rows(list(payload["exception_rows"]))

    for row in schedule_rows:
        if str(row.get("beam_type", "")).upper() != "SB":
            raise ValueError(f"non-SB row in secondary schedule: {row.get('beam_no')}")
        stirrups = row.get("stirrups")
        if not stirrups or stirrups != row.get("s1") or stirrups != row.get("s2"):
            raise ValueError(f"SB stirrup is not span-unified: {row.get('beam_no')}")

    workbook = Workbook()
    schedule_sheet = workbook.active
    schedule_sheet.title = "SB梁表"
    exception_sheet = workbook.create_sheet("复核异常")
    schedule_sheet.show_grid_lines = False
    exception_sheet.show_grid_lines = False

    stories = list(dict.fromkeys(str(row.get("story", "")) for row in schedule_rows))
    cursor = 1

    if not stories:
        schedule_sheet.merge_cells("A1:M1")
        schedule_sheet["A1"] = "SECONDARY BEAM REINFORCEMENT SCHEDULE"
        schedule_sheet["A1"].fill = PatternFill("solid", fgColor=NAVY)
        schedule_sheet["A1"].font = Font(bold=True, size=12, color="FFFFFF")
        schedule_sheet["A1"].alignment = Alignment(horizontal="center", vertical="center")
        _set_row_border(schedule_sheet, 1, 1, TOTAL_COLUMNS, LIGHT_OUTSIDE_BORDER)
        schedule_sheet.merge_cells("A2:M3")
        schedule_sheet["A2"] = (
            f"无可交付 SB 行；{len(exception_rows)} 行已保留在“复核异常”。"
            " 未确认保护层、骨料或其他工程输入时，不生成零值配筋。"
        )
        schedule_sheet["A2"].fill = PatternFill("solid", fgColor=PALE_BLUE)
        schedule_sheet["A2"].font = Font(color=NAVY, size=10)
        schedule_sheet["A2"].alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        for row in range(2, 4):
            for column in range(1, TOTAL_COLUMNS + 1):
                schedule_sheet.cell(row=row, column=column).border = LIGHT_OUTSIDE_BORDER
        cursor = 5

    for story in stories:
        rows = [row for row in schedule_rows if str(row.get("story", "")) == story]
        title_row = cursor
        group_row = cursor + 1
        subheader_row = cursor + 2
        data_start = cursor + 3
        data_end = data_start + len(rows) - 1

        schedule_sheet.merge_cells(f"A{title_row}:M{title_row}")
        schedule_sheet.cell(row=title_row, column=1).value = f"SECONDARY BEAM REINFORCEMENT SCHEDULE ({story} LEVEL)"
        schedule_sheet.cell(row=title_row, column=1).font = Font(bold=True, size=12, color="000000")
        schedule_sheet.cell(row=title_row, column=1).alignment = Alignment(horizontal="center", vertical="center")
        _set_row_border(schedule_sheet, title_row, title_row, TOTAL_COLUMNS, MAGENTA_BORDER)

        for range_spec in (
            (f"A{group_row}:B{subheader_row}",),
            (f"C{group_row}:D{group_row}",),
            (f"E{group_row}:G{group_row}",),
            (f"H{group_row}:J{group_row}",),
            (f"K{group_row}:K{subheader_row}",),
            (f"L{group_row}:L{subheader_row}",),
        ):
            schedule_sheet.merge_cells(range_spec[0])
        header_values = {
            f"A{group_row}": "BEAM NO.",
            f"C{group_row}": "BEAM SIZE/mm",
            f"E{group_row}": "TOP REBARS",
            f"H{group_row}": "BOTTOM REBARS",
            f"K{group_row}": "STIRRUPS",
            f"L{group_row}": "NO. OF LEGGED\nSTIRRUPS",
            f"M{group_row}": "FACE REBAR",
        }
        for coordinate, value in header_values.items():
            schedule_sheet[coordinate] = value
        for column, value in zip(
            range(3, 11),  # C..J subheader: WIDTH DEPTH ET1 T1 ET2 EB1 B1 EB2
            ("WIDTH", "DEPTH", "ET1", "T1", "ET2", "EB1", "B1", "EB2"),
        ):
            schedule_sheet.cell(row=subheader_row, column=column, value=value)
        schedule_sheet[f"M{subheader_row}"] = "EACH SIDE"

        _set_row_border(schedule_sheet, group_row, subheader_row, TOTAL_COLUMNS, MAGENTA_BORDER)
        for row in range(group_row, subheader_row + 1):
            for column in range(1, TOTAL_COLUMNS + 1):
                cell = schedule_sheet.cell(row=row, column=column)
                cell.fill = PatternFill("solid", fgColor="FFFFFF")
                cell.font = Font(bold=True, size=9, color="000000")
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = MAGENTA_BORDER
        schedule_sheet.row_dimensions[group_row].height = 24
        schedule_sheet.row_dimensions[subheader_row].height = 24

        for row_index, row in enumerate(rows, start=data_start):
            for column, value in enumerate(_data_row(row), start=1):
                cell = schedule_sheet.cell(row=row_index, column=column, value=value)
                cell.font = Font(size=9, color="000000")
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = MAGENTA_BORDER
            schedule_sheet.cell(row=row_index, column=3).number_format = "0"
            schedule_sheet.cell(row=row_index, column=4).number_format = "0"
            schedule_sheet.cell(row=row_index, column=12).number_format = "0"
            for column in (1, 2):
                cell = schedule_sheet.cell(row=row_index, column=column)
                cell.fill = PatternFill("solid", fgColor=PALE_PINK)
                cell.alignment = Alignment(horizontal="left", vertical="center")
            schedule_sheet.row_dimensions[row_index].height = 20

        # Merge the A column across contiguous same-beam-line rows.
        group_start = data_start
        for index in range(1, len(rows) + 1):
            changed = index == len(rows) or str(rows[index].get("beam_line", "")) != str(
                rows[index - 1].get("beam_line", "")
            )
            if not changed:
                continue
            group_end = data_start + index - 1
            if group_end > group_start:
                schedule_sheet.merge_cells(f"A{group_start}:A{group_end}")
            group_start = group_end + 1

        schedule_sheet.row_dimensions[title_row].height = 24
        cursor = data_end + 2

    schedule_sheet.column_dimensions["A"].width = 18
    schedule_sheet.column_dimensions["B"].width = 18
    for column in range(3, 5):
        schedule_sheet.column_dimensions[chr(ord("A") + column - 1)].width = 10
    for column in range(5, 11):
        schedule_sheet.column_dimensions[chr(ord("A") + column - 1)].width = 14
    schedule_sheet.column_dimensions["K"].width = 16
    schedule_sheet.column_dimensions["L"].width = 15
    schedule_sheet.column_dimensions["M"].width = 14
    schedule_sheet.freeze_panes = "C4"

    # ---- 复核异常 ------------------------------------------------------------
    exception_columns = [
        ("story", "楼层"),
        ("beam_line", "梁线编号"),
        ("beam_no", "梁编号"),
        ("section", "截面"),
        ("engine_status", "阻断/复核原因"),
        ("source_objects", "源 Object"),
    ]
    exception_sheet.merge_cells("A1:F1")
    exception_sheet["A1"] = "SB 梁表复核异常"
    exception_sheet["A1"].fill = PatternFill("solid", fgColor=NAVY)
    exception_sheet["A1"].font = Font(bold=True, size=12, color="FFFFFF")
    exception_sheet["A1"].alignment = Alignment(horizontal="center", vertical="center")
    for column, (_key, header) in enumerate(exception_columns, start=1):
        cell = exception_sheet.cell(row=2, column=column, value=header)
        cell.fill = PatternFill("solid", fgColor=PALE_BLUE)
        cell.font = Font(bold=True, color=NAVY)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = LIGHT_OUTSIDE_BORDER
    for row_index, row in enumerate(exception_rows, start=3):
        for column, (key, _header) in enumerate(exception_columns, start=1):
            value = row.get(key)
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(item) for item in value)
            cell = exception_sheet.cell(row=row_index, column=column, value=value or "")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.border = LIGHT_OUTSIDE_BORDER
    exception_sheet.column_dimensions["A"].width = 18
    exception_sheet.column_dimensions["B"].width = 18
    exception_sheet.column_dimensions["C"].width = 24
    exception_sheet.column_dimensions["D"].width = 16
    exception_sheet.column_dimensions["E"].width = 56
    exception_sheet.column_dimensions["F"].width = 40
    exception_sheet.freeze_panes = "A3"

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)
    return destination
