"""Excel beam-schedule export."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from src.core.beam_merger import MergeRecord
from src.core.continuity_analyzer import ContinuityGroup
from src.core.geometry_extractor import BeamGeometry
from src.design.key_positions import key_positions
from src.design.provider import BeamDesignResult
from src.excel_span_adapter import SpanScheduleMapping
from src.utils.io import available_output_path


def export_beam_schedule(
    beams: list[BeamGeometry],
    groups: list[ContinuityGroup],
    designs: dict[str, BeamDesignResult],
    reports_dir: Path,
    filename_pattern: str = "beam_schedule_{timestamp}.xlsx",
    special_records: list[MergeRecord] | None = None,
    span_schedule: SpanScheduleMapping | None = None,
    *,
    output_path: Path | None = None,
    beam_type: str | None = None,
) -> Path:
    """Write the legacy beam schedule and, optionally, structural span rows.

    ``span_schedule`` is an adapter boundary: future numbering rules can
    replace it without changing the established 梁表/设计站点 columns.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = output_path or available_output_path(
        reports_dir,
        filename_pattern.format(timestamp=timestamp),
    )
    if beam_type is not None:
        if beam_type not in {"PB", "SB"}:
            raise ValueError(f"unsupported beam_type: {beam_type}")
        if span_schedule is not None:
            selected_records = tuple(
                record for record in span_schedule.records if record.span.beam_type == beam_type
            )
            selected_names = {
                name
                for record in selected_records
                for name in record.span.source_objects
            }
            beams = [
                beam
                for beam in beams
                if beam.name in selected_names
                or any(name in selected_names for name in (beam.source_names or (beam.name,)))
            ]
            source_to_span_ids = {
                name: values
                for name, values in span_schedule.source_to_span_ids.items()
                if name in selected_names
            }
            source_to_line_ids = {
                name: values
                for name, values in span_schedule.source_to_line_ids.items()
                if name in selected_names
            }
            span_schedule = replace(
                span_schedule,
                records=selected_records,
                source_to_span_ids=source_to_span_ids,
                source_to_line_ids=source_to_line_ids,
            )
        else:
            # Without a structural schedule there is no safe PB/SB mapping;
            # keep the legacy object rows rather than guessing from names.
            span_schedule = None

    memberships = {
        name: (group.group_id, position + 1, len(group.members), _neighbors(group.members, position))
        for group in groups
        for position, name in enumerate(group.members)
    }
    rows, station_rows = [], []
    for beam in beams:
        design = designs.get(beam.name, BeamDesignResult(beam.name))
        group_id, position, group_size, neighbors = memberships.get(beam.name, (0, 1, 1, (None, None)))
        row = {
            "梁对象": beam.name,
            "原始梁对象": "+".join(beam.source_names or (beam.name,)),
            "标签": beam.label,
            "楼层": beam.story,
            "截面": beam.section,
            "长度 (mm)": beam.length,
            "起点 X (mm)": beam.start[0],
            "起点 Y (mm)": beam.start[1],
            "起点 Z (mm)": beam.start[2],
            "终点 X (mm)": beam.end[0],
            "终点 Y (mm)": beam.end[1],
            "终点 Z (mm)": beam.end[2],
            "连续组": group_id,
            "组内位置": position,
            "组内梁数": group_size,
            "左邻梁": neighbors[0] or "",
            "右邻梁": neighbors[1] or "",
            "设计结果状态": design.status.value,
            "设计结果说明": design.status_message,
            "设计站点数": len(design.stations),
            "左端顶部弯矩纵筋面积（2D 内）": None,
            "跨中顶部弯矩纵筋面积": None,
            "右端顶部弯矩纵筋面积（2D 内）": None,
            "左端底部弯矩纵筋面积（2D 内）": None,
            "跨中底部弯矩纵筋面积": None,
            "右端底部弯矩纵筋面积（2D 内）": None,
            "左端抗剪筋 Av/s（2D 内）": None,
            "跨中抗剪筋 Av/s": None,
            "右端抗剪筋 Av/s（2D 内）": None,
            "最大受扭纵筋面积（仅受扭需求）": design.max_torsion_longitudinal_area,
            "最大扭转横向筋 At/s": design.max_torsion_trans_area,
        }
        if span_schedule is not None:
            source_names = beam.source_names or (beam.name,)
            line_ids = [line_id for name in source_names for line_id in span_schedule.source_to_line_ids.get(name, ())]
            span_ids = [span_id for name in source_names for span_id in span_schedule.source_to_span_ids.get(name, ())]
            row["结构梁线编号"] = "；".join(dict.fromkeys(line_ids))
            row["结构跨号"] = "；".join(dict.fromkeys(span_ids))
        positions = key_positions(beam, design)
        if positions:
            row.update(_key_position_values(positions))
        rows.append(row)
        for station in design.stations:
            station_rows.append(
                {
                    "梁对象": beam.name,
                    "楼层": beam.story,
                    "位置": station.location,
                    "顶部弯矩组合": station.top_flexural_combo,
                    "顶部弯矩纵筋面积（不含受扭纵筋）": station.top_flexural_area,
                    "底部弯矩组合": station.bottom_flexural_combo,
                    "底部弯矩纵筋面积（不含受扭纵筋）": station.bottom_flexural_area,
                    "剪力组合": station.shear_combo,
                    "剪力筋 Av/s": station.shear_area,
                    "受扭纵筋组合": station.torsion_longitudinal_combo,
                    "受扭纵筋面积（仅受扭需求）": station.torsion_longitudinal_area,
                    "扭转横向筋组合": station.torsion_trans_combo,
                    "扭转横向筋 At/s": station.torsion_trans_area,
                    "错误": station.error,
                    "警告": station.warning,
                }
            )

    destination.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(destination, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="梁表")
        pd.DataFrame(station_rows).to_excel(writer, index=False, sheet_name="设计站点")
        if span_schedule is not None:
            pd.DataFrame([_span_row(record) for record in span_schedule.records]).to_excel(
                writer, index=False, sheet_name="梁分跨"
            )
        pd.DataFrame(
            [
                {"项目": "导出单位", "说明": "N_mm_C；工作流在导出后恢复 ETABS 原始当前单位。"},
                {"项目": "设计结果来源", "说明": "ETABS DesignConcrete.GetSummaryResultsBeam"},
                {"项目": "长度与坐标", "说明": "mm"},
                {"项目": "顶部/底部纵筋", "说明": "ETABS TopArea/BotArea：弯矩需求，不含受扭钢筋。"},
                {"项目": "受扭纵筋", "说明": "ETABS TLArea：仅受扭所需纵筋，不是总纵筋。"},
                {
                    "项目": "梁表关键位置列",
                    "说明": "D 为梁高；R/S/T 类字段已分别展开为左端 2D、跨中、右端 2D 控制面积。",
                },
            ]
        ).to_excel(writer, index=False, sheet_name="导出说明")
        records = special_records or []
        record_rows = [
            {
                "级别": record.severity,
                "类别": record.category,
                "梁对象": record.beam_names,
                "说明": record.message,
            }
            for record in records
        ] or [{"级别": "信息", "类别": "检查", "梁对象": "", "说明": "本次导出未记录特殊情况。"}]
        pd.DataFrame(record_rows).to_excel(writer, index=False, sheet_name="异常记录")
        for sheet in writer.book.worksheets:
            _format_sheet(sheet)
    return destination


def _span_row(record: object) -> dict[str, object]:
    span = record.span
    beam = record.geometry
    design = record.design
    row: dict[str, object] = {
        "梁编号": span.span_id,
        "梁线编号": span.beam_line_id,
        "楼层": span.story,
        "主次梁": span.beam_type,
        "跨号": span.span_index,
        "弧形": "是" if span.is_curved else "否",
        "悬挑": "是" if span.is_cantilever else "否",
        "源对象": "+".join(span.source_objects),
        "截面": beam.section,
        "梁高 D (mm)": beam.depth,
        "长度 (mm)": beam.length,
        "I端 X (mm)": span.i_point[0],
        "I端 Y (mm)": span.i_point[1],
        "I端 Z (mm)": span.i_point[2],
        "J端 X (mm)": span.j_point[0],
        "J端 Y (mm)": span.j_point[1],
        "J端 Z (mm)": span.j_point[2],
        "设计结果状态": design.status.value,
        "设计结果说明": design.status_message,
        "设计站点数": len(design.stations),
    }
    positions = key_positions(beam, design)
    if positions:
        row.update(_key_position_values(positions))
    else:
        row.update({name: None for name in _key_position_headers()})
    return row


def _key_position_headers() -> tuple[str, ...]:
    return (
        "左端顶部弯矩纵筋面积（2D 内）",
        "跨中顶部弯矩纵筋面积",
        "右端顶部弯矩纵筋面积（2D 内）",
        "左端底部弯矩纵筋面积（2D 内）",
        "跨中底部弯矩纵筋面积",
        "右端底部弯矩纵筋面积（2D 内）",
        "左端抗剪筋 Av/s（2D 内）",
        "跨中抗剪筋 Av/s",
        "右端抗剪筋 Av/s（2D 内）",
    )


def _key_position_values(positions: object) -> dict[str, float]:
    return {
        "左端顶部弯矩纵筋面积（2D 内）": positions.left_top_flexural.area,
        "跨中顶部弯矩纵筋面积": positions.middle_top_flexural.area,
        "右端顶部弯矩纵筋面积（2D 内）": positions.right_top_flexural.area,
        "左端底部弯矩纵筋面积（2D 内）": positions.left_bottom_flexural.area,
        "跨中底部弯矩纵筋面积": positions.middle_bottom_flexural.area,
        "右端底部弯矩纵筋面积（2D 内）": positions.right_bottom_flexural.area,
        "左端抗剪筋 Av/s（2D 内）": positions.left_shear.area,
        "跨中抗剪筋 Av/s": positions.middle_shear.area,
        "右端抗剪筋 Av/s（2D 内）": positions.right_shear.area,
    }


def _neighbors(members: tuple[str, ...], position: int) -> tuple[str | None, str | None]:
    return (
        members[position - 1] if position else None,
        members[position + 1] if position + 1 < len(members) else None,
    )


def _format_sheet(sheet: object) -> None:
    sheet.freeze_panes = "A2"
    header_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    sheet.auto_filter.ref = sheet.dimensions
    for column in range(1, sheet.max_column + 1):
        values = [str(sheet.cell(row=row, column=column).value or "") for row in range(1, sheet.max_row + 1)]
        sheet.column_dimensions[get_column_letter(column)].width = min(max(map(len, values)) + 2, 24)
