"""openpyxl renderer for the three-sheet rebar workbook.

Mirror of ``src/spreadsheet/build_rebar_workbook.mjs`` that runs inside the
portable workflow without node.  Formulas are written as Excel formula strings
(they recompute when the workbook is opened in Excel); no preview PNGs are
rendered here.  The payload schema is unchanged (2.0).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

PAYLOAD_KEYS = ("metadata", "raw_rows", "review_rows", "final_rows")
REGIONS = ("left", "middle", "right")

# Palette mirrored from build_rebar_workbook.mjs.
COLORS = {
    "navy": "17365D",
    "blue": "1F4E78",
    "lightBlue": "DDEBF7",
    "input": "FFF2CC",
    "formula": "E2F0D9",
    "warning": "FCE4D6",
    "gray": "F2F2F2",
    "ok": "C6E0B4",
    "ng": "F4CCCC",
}
BORDER_COLOR = "B4C6E7"
THIN_BORDER = Border(
    left=Side(style="thin", color=BORDER_COLOR),
    right=Side(style="thin", color=BORDER_COLOR),
    top=Side(style="thin", color=BORDER_COLOR),
    bottom=Side(style="thin", color=BORDER_COLOR),
)

# 1:1 column maps from build_rebar_workbook.mjs (key, header).  Column letters
# are computed from these lists so they can never be hand-transcribed wrong.
RAW_COLUMNS: list[tuple[str, str]] = [
    ("beam_no", "梁编号"),
    ("beam_line", "梁线编号"),
    ("span_no", "跨号"),
    ("story", "楼层"),
    ("beam_type", "主次梁"),
    ("position", "位置"),
    ("section", "采用截面"),
    ("all_sections", "本跨全部截面"),
    ("width_mm", "梁宽/mm"),
    ("depth_mm", "梁高D/mm"),
    ("length_mm", "跨长/mm"),
    ("etabs_longitudinal_cover_mm", "ETABS纵筋设计cover/mm"),
    ("cover_calculation_basis", "保护层计算口径"),
    ("etabs_longitudinal_cover_source", "ETABS cover来源"),
    ("clear_cover_to_stirrup_outer_mm", "箍筋外缘净保护层/mm"),
    ("clear_cover_source", "净保护层来源"),
    ("fc_mpa", "fc'/MPa"),
    ("fy_mpa", "fy/MPa"),
    ("fyt_mpa", "fyt/MPa"),
    ("max_aggregate_size_mm", "最大骨料/mm"),
    ("max_aggregate_size_source", "骨料来源"),
    ("frame_type", "ETABS FrameType"),
    ("detailing_profile", "配筋Profile"),
    ("profile_source", "Profile来源"),
    ("is_smf", "是否SMF"),
    ("source_objects", "源 Object"),
    ("design_status", "设计结果状态"),
    ("design_status_message", "设计状态说明"),
    ("design_result_source", "设计字段来源"),
    ("design_units", "设计结果单位"),
    ("engine_status", "引擎状态"),
    ("warnings", "警告"),
    ("top_total_mm2", "顶部AsTotal/mm²"),
    ("top_total_station_mm", "顶部Total站点/mm"),
    ("top_total_combo", "顶部Total组合"),
    ("top_flex_mm2", "顶部AsFlex/mm²"),
    ("top_flex_station_mm", "顶部Flex站点/mm"),
    ("top_flex_combo", "顶部Flex组合"),
    ("bottom_total_mm2", "底部AsTotal/mm²"),
    ("bottom_total_station_mm", "底部Total站点/mm"),
    ("bottom_total_combo", "底部Total组合"),
    ("bottom_flex_mm2", "底部AsFlex/mm²"),
    ("bottom_flex_station_mm", "底部Flex站点/mm"),
    ("bottom_flex_combo", "底部Flex组合"),
    ("as_min_top_mm2", "AsMinTop/mm²"),
    ("as_min_bottom_mm2", "AsMinBot/mm²"),
    ("as_provided_top_mm2", "AsProvidedTop/mm²"),
    ("as_provided_bottom_mm2", "AsProvidedBot/mm²"),
    ("raw_as_provided_top", "ETABS顶部Provided原始值"),
    ("raw_as_provided_bottom", "ETABS底部Provided原始值"),
    ("av_over_s", "VMajorArea Av/s"),
    ("av_station_mm", "Av/s站点/mm"),
    ("av_combo", "Av/s组合"),
    ("al_mm2", "TLArea Al/mm²"),
    ("al_station_mm", "Al站点/mm"),
    ("al_combo", "Al组合"),
    ("at_over_s", "TTArea At/s"),
    ("at_station_mm", "At/s站点/mm"),
    ("at_combo", "At/s组合"),
    ("source_station_count", "区域站点数"),
    ("source_objects", "区域来源Object"),
    ("units", "区域单位"),
    ("result_source", "区域结果API"),
    ("design_errors", "区域设计Error"),
    ("design_warnings", "区域设计Warning"),
]

CALCULATED_REVIEW_STATUS_KEYS = (
    "face_status",
    "et1_status",
    "t1_status",
    "et2_status",
    "eb1_status",
    "b1_status",
    "eb2_status",
    "s2_left_status",
    "s2_right_status",
    "s1_status",
)


def _review_columns() -> list[tuple[str, str]]:
    columns: list[tuple[str, str]] = [
        ("beam_no", "梁编号"),
        ("beam_line", "梁线编号"),
        ("span_no", "跨号"),
        ("story", "楼层"),
        ("beam_type", "主次梁"),
        ("section", "截面"),
        ("width_mm", "梁宽/mm"),
        ("depth_mm", "梁高/mm"),
        ("frame_type", "FrameType"),
        ("detailing_profile", "配筋Profile"),
        ("is_smf", "SMF"),
        ("mode", "引擎模式"),
        ("engine_status", "引擎状态"),
        ("warnings", "警告"),
        ("legs", "Legs（可编辑）"),
        ("iteration_status", "纵筋/箍筋迭代状态"),
        ("iteration_count", "迭代次数"),
        ("coordination_status", "相邻跨协调状态"),
        ("continuity_option", "公共支座方案"),
        ("face_required_mm2", "Face计算面积/侧"),
        ("face_text", "Face配筋文本/侧"),
        ("face_actual", "Face实配面积/侧"),
        ("face_diff", "Face差值"),
        ("face_status", "Face状态"),
    ]
    for key, label in (
        ("et1", "ET1"),
        ("t1", "T1"),
        ("et2", "ET2"),
        ("eb1", "EB1"),
        ("b1", "B1"),
        ("eb2", "EB2"),
    ):
        columns.extend(
            [
                (f"{key}_required_mm2", f"{label}计算面积"),
                (f"{key}_text", f"{label}配筋文本"),
                (f"{key}_actual", f"{label}实配面积"),
                (f"{key}_diff", f"{label}差值"),
                (f"{key}_status", f"{label}状态"),
            ]
        )
    columns.extend(
        [
            ("s2_text", "S2配筋文本"),
            ("s2_left_at_required", "左At/s计算"),
            ("s2_left_combo_required", "左剪扭计算"),
            ("s2_at_actual", "S2单腿At/s实配"),
            ("s2_combo_actual", "S2总腿实配"),
            ("s2_left_status", "左S2状态"),
            ("s2_right_at_required", "右At/s计算"),
            ("s2_right_combo_required", "右剪扭计算"),
            ("s2_right_status", "右S2状态"),
            ("s1_text", "S1配筋文本"),
            ("s1_at_required", "中At/s计算"),
            ("s1_combo_required", "中剪扭计算"),
            ("s1_at_actual", "S1单腿At/s实配"),
            ("s1_combo_actual", "S1总腿实配"),
            ("s1_status", "中S1状态"),
        ]
    )
    for prefix, label in (("s2_left", "左端"), ("s1", "跨中"), ("s2_right", "右端")):
        columns.extend(
            [
                (f"{prefix}_av_required", f"{label}原始Av/s"),
                (f"{prefix}_av_min", f"{label}最小Av/s"),
                (f"{prefix}_at_provided", f"{label}引擎单腿At/s实配"),
                (f"{prefix}_combo_provided", f"{label}引擎总腿实配"),
                (f"{prefix}_maximum_spacing_mm", f"{label}最大允许间距/mm"),
                (f"{prefix}_governing_reason", f"{label}间距控制原因"),
                (f"{prefix}_final_main_db_mm", f"{label}最终纵筋db/mm"),
                (f"{prefix}_high_shear", f"{label}高剪分支"),
                (f"{prefix}_spacing_limits", f"{label}全部间距限值"),
                (f"{prefix}_shear_source", f"{label}剪力需求来源"),
            ]
        )
    return columns


FINAL_COLUMNS: list[tuple[str, str]] = [
    ("beam_line", "梁线编号"),
    ("beam_no", "梁编号"),
    ("story", "楼层"),
    ("beam_type", "主次梁"),
    ("width_mm", "梁宽/mm"),
    ("depth_mm", "梁高/mm"),
    ("et1", "ET1"),
    ("t1", "T1"),
    ("et2", "ET2"),
    ("eb1", "EB1"),
    ("b1", "B1"),
    ("eb2", "EB2"),
    ("s2", "S2"),
    ("s1", "S1"),
    ("legs", "Legs"),
    ("face", "Face/side"),
    ("mode", "模式"),
    ("engine_status", "引擎状态"),
    ("qc_status", "复核状态"),
]

# Final-sheet columns guarded by the review engine status (empty when blocked).
GUARDED_FINAL_FIELDS = {"et1", "t1", "et2", "eb1", "b1", "eb2", "s2", "s1", "legs", "face", "mode"}

REVIEW_TEXT_FIELDS = (
    "legs",
    "face_text",
    "et1_text",
    "t1_text",
    "et2_text",
    "eb1_text",
    "b1_text",
    "eb2_text",
    "s2_text",
    "s1_text",
)


def _col_letter(index: int) -> str:
    """0-based column index -> Excel letter (colLetter in the mjs)."""
    return get_column_letter(index + 1)


def _rebar_area_formula(cell: str) -> str:
    """Parse ``8T25 6/2``, ``2T25+2T20`` or ``2T16+(2T12)`` to total area (mm2)."""
    raw = f'LEFT(TRIM({cell})&" ",FIND(" ",TRIM({cell})&" ")-1)'
    token = f'IFERROR(LEFT({raw},FIND("+(",{raw})-1),{raw})'
    first = (
        f'IFERROR(VALUE(LEFT({token},FIND("T",{token})-1))*PI()*VALUE(MID({token},'
        f'FIND("T",{token})+1,FIND("+",{token}&"+")-FIND("T",{token})-1))^2/4,0)'
    )
    second = (
        f'IFERROR(VALUE(MID({token},FIND("+",{token})+1,FIND("T",{token},FIND("+",{token})+1)'
        f'-FIND("+",{token})-1))*PI()*VALUE(MID({token},FIND("T",{token},FIND("+",{token})+1)+1,'
        f"LEN({token})))^2/4,0)"
    )
    return f'=IF(OR(TRIM({cell})="",UPPER(TRIM({cell}))="N.A."),0,{first}+{second})'


def _stirrup_at_formula(cell: str) -> str:
    """Parse ``T10@200`` -> pi*d^2/4/spacing."""
    return f'=IFERROR(PI()*VALUE(MID({cell},2,FIND("@",{cell})-2))^2/4/VALUE(MID({cell},FIND("@",{cell})+1,99)),0)'


def _cell_value(value: Any) -> Any:
    """Normalize payload values for an openpyxl cell (list/tuple -> string)."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "是" if value else "否"
    return value


def _metadata_text(metadata: Mapping[str, Any]) -> str:
    cover_basis = metadata.get("cover_calculation_basis")
    if cover_basis == "ETABS_LONGITUDINAL_CENTROID":
        cover_text = "保护层计算口径：ETABS CoverTop/CoverBot（混凝土面至纵筋重心，逐截面取控制值）"
    else:
        cover_text = (
            f"箍筋外缘净保护层：{metadata.get('clear_cover_to_stirrup_outer_mm') or '未确认'} mm"
            f"（{metadata.get('clear_cover_source') or '来源未记录'}）"
        )
    return (
        f"模型：{metadata.get('model', '')}｜单位：{metadata.get('units', '')}｜"
        f"规范：{metadata.get('design_code', '')}"
        f"｜梁 Object：{metadata.get('frame_objects', '')}｜梁线：{metadata.get('beam_lines', '')}｜"
        f"结构跨：{metadata.get('spans', '')}"
        f"｜Profile 来源：{metadata.get('detailing_profile_source') or metadata.get('is_smf_source') or '未记录'}"
        f"｜最大骨料：{metadata.get('max_aggregate_size_mm') or '未确认'} mm"
        f"（{metadata.get('max_aggregate_size_source') or '来源未记录'}）"
        f"｜多排纵筋每排≥{metadata.get('minimum_bars_per_layer') or 2}根"
        f"｜受弯主筋≥T{metadata.get('minimum_main_diameter_mm') or 16}"
        f"｜架立筋T{metadata.get('hanger_diameter_mm') or 12}"
        f"｜{cover_text}"
    )


def _style_title(sheet, end_col_index: int, title: str, subtitle: str, metadata: str) -> None:
    title_end = _col_letter(min(end_col_index, 17))  # 0-based R
    sheet.merge_cells(f"A1:{title_end}1")
    sheet["A1"] = title
    sheet["A1"].font = Font(bold=True, color="FFFFFF", size=16)
    sheet["A1"].fill = PatternFill("solid", fgColor=COLORS["navy"])
    sheet["A1"].alignment = Alignment(horizontal="left", vertical="center")
    sheet.row_dimensions[1].height = 28
    sheet.merge_cells(f"A2:{title_end}2")
    sheet["A2"] = subtitle
    sheet["A2"].font = Font(color=COLORS["navy"], size=10)
    sheet["A2"].fill = PatternFill("solid", fgColor=COLORS["lightBlue"])
    sheet["A2"].alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[2].height = 32
    sheet.merge_cells(f"A3:{title_end}3")
    sheet["A3"] = metadata
    sheet["A3"].font = Font(color="595959", size=9)
    sheet["A3"].fill = PatternFill("solid", fgColor=COLORS["gray"])
    sheet["A3"].alignment = Alignment(wrap_text=True)
    sheet.row_dimensions[3].height = 32


def _style_header(sheet, start_col: int, end_col: int, row: int) -> None:
    for column in range(start_col, end_col + 1):
        cell = sheet.cell(row=row, column=column)
        cell.fill = PatternFill("solid", fgColor=COLORS["blue"])
        cell.font = Font(bold=True, color="FFFFFF", size=9)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN_BORDER
    sheet.row_dimensions[row].height = 34


def _add_status_formats(sheet, start_col: int, end_col: int, start_row: int, end_row: int) -> None:
    # start_col/end_col are 1-based Excel column numbers.
    if end_row < start_row:
        return
    top_left = f"{get_column_letter(start_col)}{start_row}"
    for text, fill, font_color in (
        ("OK", COLORS["ok"], "375623"),
        ("NG", COLORS["ng"], "9C0006"),
        ("ENGINE", COLORS["warning"], "9C5700"),
    ):
        rule = FormulaRule(
            formula=[f'NOT(ISERROR(SEARCH("{text}",{top_left})))'],
            fill=PatternFill("solid", fgColor=fill),
            font=Font(color=font_color, bold=True),
        )
        sheet.conditional_formatting.add(
            f"{get_column_letter(start_col)}{start_row}:{get_column_letter(end_col)}{end_row}",
            rule,
        )


def _add_table(sheet, start_col: int, end_col: int, start_row: int, end_row: int, name: str, style: str) -> None:
    # start_col/end_col are 1-based Excel column numbers.
    if end_row < start_row:
        return
    ref = f"{get_column_letter(start_col)}{start_row}:{get_column_letter(end_col)}{end_row}"
    table = Table(displayName=name, ref=ref)
    table.tableStyleInfo = TableStyleInfo(
        name=style,
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=True,
    )
    sheet.add_table(table)


def _set_freeze(sheet, columns: int, rows: int) -> None:
    sheet.freeze_panes = f"{_col_letter(columns)}{rows + 1}"


def render_rebar_workbook_xlsx(payload: Mapping[str, Any], output_path: str | Path) -> Path:
    """Write the three-sheet rebar workbook and return the output path."""
    for key in PAYLOAD_KEYS:
        if key not in payload:
            raise ValueError(f"payload missing {key}")

    raw_rows = payload["raw_rows"]
    review_rows = payload["review_rows"]
    final_rows = payload["final_rows"]
    if len(review_rows) != len(final_rows):
        raise ValueError("review_rows and final_rows must have the same length")

    metadata = payload.get("metadata", {})
    metadata_text = _metadata_text(metadata)

    review_columns = _review_columns()
    review_index = {key: index for index, (key, _header) in enumerate(review_columns)}
    engine_status_col = _col_letter(review_index["engine_status"])

    workbook = Workbook()
    raw_sheet = workbook.active
    raw_sheet.title = "原始分跨数据"
    review_sheet = workbook.create_sheet("配筋调整复核")
    final_sheet = workbook.create_sheet("整理梁表")
    for sheet in (raw_sheet, review_sheet, final_sheet):
        sheet.show_grid_lines = False

    # ---- 原始分跨数据 -------------------------------------------------------
    raw_rows_expanded: list[dict[str, Any]] = []
    for row in raw_rows:
        for prefix, label in (("left", "左端2D"), ("middle", "跨中"), ("right", "右端2D")):
            output = {
                **row,
                "position": label,
                "is_smf": "是" if row.get("is_smf") else "否",
            }
            for key, _header in RAW_COLUMNS:
                source_key = f"{prefix}_{key}"
                if source_key in row:
                    output[key] = row[source_key]
            raw_rows_expanded.append(output)
    raw_end_row = len(raw_rows_expanded) + 5

    _style_title(
        raw_sheet,
        len(RAW_COLUMNS) - 1,
        "ETABS 梁跨原始设计数据",
        "一行代表一个梁跨位置（左端2D / 跨中 / 右端2D）。AsFlex 在每个 station 按 AsTotal−Al/2 计算后再取区域最大值；控制组合和 station 保留供校核。",
        metadata_text,
    )
    for column, (_key, header) in enumerate(RAW_COLUMNS, start=1):
        raw_sheet.cell(row=5, column=column, value=header)
    _style_header(raw_sheet, 1, len(RAW_COLUMNS), 5)
    for row_index, row in enumerate(raw_rows_expanded, start=6):
        for column, (key, _header) in enumerate(RAW_COLUMNS, start=1):
            raw_sheet.cell(row=row_index, column=column, value=_cell_value(row.get(key)))
    _add_table(raw_sheet, 1, len(RAW_COLUMNS), 5, raw_end_row, "RawSpanData", "TableStyleMedium2")
    _set_freeze(raw_sheet, 6, 5)
    for column in range(9, 17):  # I..P "0.0"
        for row in range(6, raw_end_row + 1):
            raw_sheet.cell(row=row, column=column).number_format = "0.0"
    for column in range(25, 51):  # Y..AX "0.000"
        for row in range(6, raw_end_row + 1):
            raw_sheet.cell(row=row, column=column).number_format = "0.000"
    for row in range(6, raw_end_row + 1):
        for column in range(1, len(RAW_COLUMNS) + 1):
            raw_sheet.cell(row=row, column=column).font = Font(size=9)
    for col in ("A", "B", "G"):
        raw_sheet.column_dimensions[col].width = 22
    for col in ("H", "N", "P", "U", "V"):
        raw_sheet.column_dimensions[col].width = 32
    raw_sheet.column_dimensions["M"].width = 28
    for column in range(3, 19):  # C..R
        raw_sheet.column_dimensions[_col_letter(column - 1)].width = 11
    for column in range(23, 48):  # W..AV
        raw_sheet.column_dimensions[_col_letter(column - 1)].width = 13

    # ---- 配筋调整复核 -------------------------------------------------------
    review_start = 6
    review_end_row = review_start + len(review_rows) - 1
    _style_title(
        review_sheet,
        len(review_columns) - 1,
        "梁配筋调整与计算面积复核",
        "黄色单元格可编辑：纵筋如 8T25 6/2、2T25+2T20 或 2T16+(2T12)；层数说明写在第一个空格后，"
        "括号内为架立筋。受弯面积公式忽略分层后缀及括号架立筋。每排至少2根，不允许单根末排。"
        "S2 同时校核左右端，S1 校核跨中。",
        metadata_text,
    )
    for column, (_key, header) in enumerate(review_columns, start=1):
        review_sheet.cell(row=5, column=column, value=header)
    _style_header(review_sheet, 1, len(review_columns), 5)
    for row_index, source in enumerate(review_rows):
        initial = final_rows[row_index] if row_index < len(final_rows) else {}
        values = {
            **source,
            "is_smf": "是" if source.get("is_smf") else "否",
            "s2_text": initial.get("s2", ""),
        }
        row_number = review_start + row_index
        for column, (key, _header) in enumerate(review_columns, start=1):
            if key.endswith("_actual") or key.endswith("_diff") or key in CALCULATED_REVIEW_STATUS_KEYS:
                continue
            review_sheet.cell(row=row_number, column=column, value=_cell_value(values.get(key)))

    for group in ("face", "et1", "t1", "et2", "eb1", "b1", "eb2"):
        text_col = _col_letter(review_index[f"{group}_text"])
        actual_col = _col_letter(review_index[f"{group}_actual"])
        required_key = "face_required_mm2" if group == "face" else f"{group}_required_mm2"
        required_col = _col_letter(review_index[required_key])
        for row_index, source in enumerate(review_rows):
            row_number = review_start + row_index
            if source.get("engine_status") != "OK":
                review_sheet.cell(row=row_number, column=review_index[f"{group}_actual"] + 1, value='=""')
                review_sheet.cell(row=row_number, column=review_index[f"{group}_diff"] + 1, value='=""')
                continue
            review_sheet.cell(
                row=row_number,
                column=review_index[f"{group}_actual"] + 1,
                value=_rebar_area_formula(f"{text_col}{row_number}"),
            )
            review_sheet.cell(
                row=row_number,
                column=review_index[f"{group}_diff"] + 1,
                value=f"={actual_col}{row_number}-{required_col}{row_number}",
            )
        for row_index, source in enumerate(review_rows):
            row_number = review_start + row_index
            if group == "face":
                formula = (
                    f'=IF(${engine_status_col}{row_number}<>"OK","ENGINE",'
                    f'IF(AND(UPPER(TRIM({text_col}{row_number}))="N.A.",{required_col}{row_number}<=0),"N.A.",'
                    f'IF({actual_col}{row_number}+0.001>={required_col}{row_number},"OK","NG")))'
                )
            else:
                formula = (
                    f'=IF(${engine_status_col}{row_number}<>"OK","ENGINE",'
                    f'IF({actual_col}{row_number}+0.001>={required_col}{row_number},"OK","NG"))'
                )
            review_sheet.cell(row=row_number, column=review_index[f"{group}_status"] + 1, value=formula)

    legs_col = _col_letter(review_index["legs"])
    for group in ("s2", "s1"):
        text_col = _col_letter(review_index[f"{group}_text"])
        at_col = _col_letter(review_index[f"{group}_at_actual"])
        for row_index, source in enumerate(review_rows):
            row_number = review_start + row_index
            if source.get("engine_status") != "OK":
                review_sheet.cell(row=row_number, column=review_index[f"{group}_at_actual"] + 1, value='=""')
                review_sheet.cell(row=row_number, column=review_index[f"{group}_combo_actual"] + 1, value='=""')
                continue
            review_sheet.cell(
                row=row_number,
                column=review_index[f"{group}_at_actual"] + 1,
                value=_stirrup_at_formula(f"{text_col}{row_number}"),
            )
            review_sheet.cell(
                row=row_number,
                column=review_index[f"{group}_combo_actual"] + 1,
                value=f"={at_col}{row_number}*{legs_col}{row_number}",
            )

    for status_key, at_req_key, combo_req_key, at_actual_key, combo_actual_key in (
        ("s2_left_status", "s2_left_at_required", "s2_left_combo_required", "s2_at_actual", "s2_combo_actual"),
        ("s2_right_status", "s2_right_at_required", "s2_right_combo_required", "s2_at_actual", "s2_combo_actual"),
        ("s1_status", "s1_at_required", "s1_combo_required", "s1_at_actual", "s1_combo_actual"),
    ):
        at_req_col = _col_letter(review_index[at_req_key])
        combo_req_col = _col_letter(review_index[combo_req_key])
        at_actual_col = _col_letter(review_index[at_actual_key])
        combo_actual_col = _col_letter(review_index[combo_actual_key])
        for row_index in range(len(review_rows)):
            row_number = review_start + row_index
            formula = (
                f'=IF(${engine_status_col}{row_number}<>"OK","ENGINE",'
                f"IF(AND({at_actual_col}{row_number}+0.000001>={at_req_col}{row_number},"
                f'{combo_actual_col}{row_number}+0.000001>={combo_req_col}{row_number}),"OK","NG"))'
            )
            review_sheet.cell(row=row_number, column=review_index[status_key] + 1, value=formula)

    _add_table(review_sheet, 1, len(review_columns), 5, review_end_row, "RebarReviewTable", "TableStyleMedium4")
    _set_freeze(review_sheet, 6, 5)
    for row in range(review_start, review_end_row + 1):
        for column in range(1, len(review_columns) + 1):
            review_sheet.cell(row=row, column=column).font = Font(size=9)
    for key in REVIEW_TEXT_FIELDS:
        col = _col_letter(review_index[key])
        for row in range(review_start, review_end_row + 1):
            review_sheet.cell(row=row, column=review_index[key] + 1).fill = PatternFill(
                "solid", fgColor=COLORS["input"]
            )
        review_sheet.column_dimensions[col].width = 16 if "text" in key else 10
    for key, _header in review_columns:
        col = _col_letter(review_index[key])
        if "required" in key or key.endswith("_actual") or key.endswith("_diff"):
            for row in range(review_start, review_end_row + 1):
                review_sheet.cell(row=row, column=review_index[key] + 1).number_format = "0.000"
            if key.endswith("_actual") or key.endswith("_diff"):
                for row in range(review_start, review_end_row + 1):
                    review_sheet.cell(row=row, column=review_index[key] + 1).fill = PatternFill(
                        "solid", fgColor=COLORS["formula"]
                    )
        if key.endswith("_status"):
            _add_status_formats(
                review_sheet,
                review_index[key] + 1,
                review_index[key] + 1,
                review_start,
                review_end_row,
            )
    for row in range(review_start, review_end_row + 1):
        for column in range(1, len(review_columns) + 1):
            review_sheet.cell(row=row, column=column).alignment = Alignment(vertical="center")
    review_sheet.column_dimensions["A"].width = 24
    review_sheet.column_dimensions["B"].width = 22
    review_sheet.column_dimensions["F"].width = 18
    review_sheet.column_dimensions["I"].width = 18
    review_sheet.column_dimensions["K"].width = 20
    review_sheet.column_dimensions["L"].width = 20
    review_sheet.column_dimensions["M"].width = 34
    review_sheet.column_dimensions["N"].width = 26

    # ---- 整理梁表 -----------------------------------------------------------
    final_index = {key: index for index, (key, _header) in enumerate(FINAL_COLUMNS)}
    final_start = 6
    final_end_row = final_start + len(final_rows) - 1
    _style_title(
        final_sheet,
        len(FINAL_COLUMNS) - 1,
        "整理后的混凝土梁配筋表",
        "本表由“配筋调整复核”黄色输入单元格驱动。修改配筋文本或 Legs 后，实配面积、状态和本表同步更新；S2 为左右端共用，S1 为跨中。",
        metadata_text,
    )
    for column, (_key, header) in enumerate(FINAL_COLUMNS, start=1):
        final_sheet.cell(row=5, column=column, value=header)
    _style_header(final_sheet, 1, len(FINAL_COLUMNS), 5)

    review_refs = {
        "beam_line": "beam_line",
        "beam_no": "beam_no",
        "story": "story",
        "beam_type": "beam_type",
        "width_mm": "width_mm",
        "depth_mm": "depth_mm",
        "et1": "et1_text",
        "t1": "t1_text",
        "et2": "et2_text",
        "eb1": "eb1_text",
        "b1": "b1_text",
        "eb2": "eb2_text",
        "s2": "s2_text",
        "s1": "s1_text",
        "legs": "legs",
        "face": "face_text",
        "mode": "mode",
        "engine_status": "engine_status",
    }
    for row_index in range(len(final_rows)):
        row_number = final_start + row_index
        review_row_number = review_start + row_index
        engine_ref = f"'配筋调整复核'!{engine_status_col}{review_row_number}"
        for key, review_key in review_refs.items():
            review_col = _col_letter(review_index[review_key])
            value_ref = f"'配筋调整复核'!{review_col}{review_row_number}"
            if key in GUARDED_FINAL_FIELDS:
                formula = f'=IF({engine_ref}<>"OK","",{value_ref})'
            else:
                formula = f"={value_ref}"
            final_sheet.cell(row=row_number, column=final_index[key] + 1, value=formula)
        checks = ",".join(
            f"'配筋调整复核'!{_col_letter(review_index[status_key])}{review_row_number}=\"NG\""
            for status_key in CALCULATED_REVIEW_STATUS_KEYS
        )
        qc_formula = f'=IF({engine_ref}<>"OK","ENGINE_ERROR",' f'IF(OR({checks}),"CHECK","OK"))'
        final_sheet.cell(row=row_number, column=final_index["qc_status"] + 1, value=qc_formula)

    _add_table(final_sheet, 1, len(FINAL_COLUMNS), 5, final_end_row, "FinalBeamSchedule", "TableStyleMedium9")
    _set_freeze(final_sheet, 2, 5)
    for row in range(final_start, final_end_row + 1):
        for column in range(1, len(FINAL_COLUMNS) + 1):
            final_sheet.cell(row=row, column=column).font = Font(size=9)
    for column in (5, 6):  # E..F "0"
        for row in range(final_start, final_end_row + 1):
            final_sheet.cell(row=row, column=column).number_format = "0"
    _add_status_formats(
        final_sheet,
        final_index["qc_status"] + 1,
        final_index["qc_status"] + 1,
        final_start,
        final_end_row,
    )
    final_sheet.column_dimensions["A"].width = 24
    final_sheet.column_dimensions["B"].width = 24
    for col in ("C", "D", "E", "F"):
        final_sheet.column_dimensions[col].width = 11
    for col in ("G", "H", "I", "J", "K", "L", "M", "N", "O", "P"):
        final_sheet.column_dimensions[col].width = 15
    final_sheet.column_dimensions["Q"].width = 18
    final_sheet.column_dimensions["R"].width = 34
    final_sheet.column_dimensions["S"].width = 20

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)
    return destination


def _scan_formula_errors(workbook_path: str | Path) -> list[str]:
    """Scan formula cells for error literals without evaluating them.

    openpyxl does not compute formula values, so this only detects literals
    that would render as #REF!/#VALUE!/#DIV/0!/#NAME?/#N/A when opened in Excel.
    """
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
