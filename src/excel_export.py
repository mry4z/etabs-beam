"""Excel beam-schedule export."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from src.core.continuity_analyzer import ContinuityGroup
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult
from src.utils.io import available_output_path


def export_beam_schedule(
    beams: list[BeamGeometry],
    groups: list[ContinuityGroup],
    designs: dict[str, BeamDesignResult],
    reports_dir: Path,
    filename_pattern: str = "beam_schedule_{timestamp}.xlsx",
) -> Path:
    """Write one row per beam plus a station-detail worksheet."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = available_output_path(reports_dir, filename_pattern.format(timestamp=timestamp))

    memberships = {
        name: (group.group_id, position + 1, len(group.members), _neighbors(group.members, position))
        for group in groups
        for position, name in enumerate(group.members)
    }
    rows, station_rows = [], []
    for beam in beams:
        design = designs.get(beam.name, BeamDesignResult(beam.name))
        group_id, position, group_size, neighbors = memberships[beam.name]
        rows.append(
            {
                "梁对象": beam.name,
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
                "设计站点数": len(design.stations),
                "最大顶筋面积": design.max_top_area,
                "最大底筋面积": design.max_bottom_area,
                "最大剪力筋 Av/s": design.max_shear_area,
                "最大扭转横向筋 At/s": design.max_torsion_trans_area,
            }
        )
        for station in design.stations:
            station_rows.append(
                {
                    "梁对象": beam.name,
                    "楼层": beam.story,
                    "位置": station.location,
                    "顶筋组合": station.top_combo,
                    "顶筋面积": station.top_area,
                    "底筋组合": station.bottom_combo,
                    "底筋面积": station.bottom_area,
                    "剪力组合": station.shear_combo,
                    "剪力筋 Av/s": station.shear_area,
                    "扭转纵筋组合": station.torsion_long_combo,
                    "扭转纵筋面积": station.torsion_long_area,
                    "扭转横向筋组合": station.torsion_trans_combo,
                    "扭转横向筋 At/s": station.torsion_trans_area,
                    "错误": station.error,
                    "警告": station.warning,
                }
            )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="梁表")
        pd.DataFrame(station_rows).to_excel(writer, index=False, sheet_name="设计站点")
        pd.DataFrame(
            [
                {"项目": "导出单位", "说明": "N_mm_C；工作流在导出后恢复 ETABS 原始当前单位。"},
                {"项目": "设计结果来源", "说明": "ETABS DesignConcrete.GetSummaryResultsBeam"},
                {"项目": "长度与坐标", "说明": "mm"},
                {"项目": "设计站点字段", "说明": "遵循 ETABS Summary Results Beam 的 N_mm_C 当前单位语义。"},
            ]
        ).to_excel(writer, index=False, sheet_name="导出说明")
        for sheet in writer.book.worksheets:
            _format_sheet(sheet)
    return output_path


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
