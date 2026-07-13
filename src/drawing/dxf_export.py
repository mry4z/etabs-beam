"""Export merge-angle comparison geometry as a lightweight ASCII DXF.

The exporter intentionally has no CAD/Python package dependency.  The resulting
R12-compatible DXF can be opened by AutoCAD, BricsCAD and other CAD viewers.
Plan X/Y coordinates are kept in the model's millimetre export unit.
"""

from __future__ import annotations

import csv
from math import atan2, degrees
from pathlib import Path
from typing import Iterable

from src.core.merge_comparison import MergeAngleDifference
from src.utils.geometry import Point3D


_LAYERS = (
    ("MERGE15_NEW", 1),       # red: newly connected at 15 degrees
    ("MERGE3_GROUP", 5),      # blue: original 3-degree groups
    ("MERGE15_MID", 2),       # yellow: 15-degree logical midpoint
    ("MERGE3_MID", 4),        # cyan: 3-degree group midpoint
    ("MERGE_LEGEND", 7),      # white: legend and metadata
)


def _pairwise(points: tuple[Point3D, ...]) -> Iterable[tuple[Point3D, Point3D]]:
    return zip(points, points[1:])


def _safe_text(value: str) -> str:
    """Keep labels ASCII so older CAD readers do not misread DXF encoding."""
    return value.encode("ascii", "replace").decode("ascii").replace("\n", " ")


def _midpoint(points: tuple[Point3D, ...]) -> Point3D:
    if not points:
        return (0.0, 0.0, 0.0)
    return points[len(points) // 2]


def _text_angle(points: tuple[Point3D, ...]) -> float:
    if len(points) < 2:
        return 0.0
    start, end = points[0], points[-1]
    angle = degrees(atan2(end[1] - start[1], end[0] - start[0]))
    # Keep labels readable rather than upside down.
    if angle > 90.0:
        angle -= 180.0
    elif angle <= -90.0:
        angle += 180.0
    return angle


def _line(layer: str, color: int, start: Point3D, end: Point3D) -> list[str]:
    return [
        "0", "LINE", "8", layer, "62", str(color),
        "10", f"{start[0]:.3f}", "20", f"{start[1]:.3f}", "30", f"{start[2]:.3f}",
        "11", f"{end[0]:.3f}", "21", f"{end[1]:.3f}", "31", f"{end[2]:.3f}",
    ]


def _circle(layer: str, color: int, center: Point3D, radius: float) -> list[str]:
    return [
        "0", "CIRCLE", "8", layer, "62", str(color),
        "10", f"{center[0]:.3f}", "20", f"{center[1]:.3f}", "30", f"{center[2]:.3f}",
        "40", f"{radius:.3f}",
    ]


def _text(layer: str, color: int, point: Point3D, value: str, height: float, angle: float = 0.0) -> list[str]:
    return [
        "0", "TEXT", "8", layer, "62", str(color),
        "10", f"{point[0]:.3f}", "20", f"{point[1]:.3f}", "30", f"{point[2]:.3f}",
        "40", f"{height:.3f}", "1", _safe_text(value), "50", f"{angle:.3f}",
    ]


def _header() -> list[str]:
    return [
        "0", "SECTION", "2", "HEADER",
        "9", "$ACADVER", "1", "AC1009",
        "9", "$INSUNITS", "70", "4",  # millimetres
        "0", "ENDSEC",
    ]


def _tables() -> list[str]:
    values = ["0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", str(len(_LAYERS))]
    for name, color in _LAYERS:
        values.extend(["0", "LAYER", "2", name, "70", "0", "62", str(color), "6", "CONTINUOUS"])
    values.extend(["0", "ENDTAB", "0", "ENDSEC"])
    return values


def export_merge_angle_dxf(
    differences: Iterable[MergeAngleDifference],
    output_path: str | Path,
    *,
    model_name: str = "",
    text_height: float = 150.0,
) -> Path:
    """Write a CAD-checking DXF for every 3°/15° merge difference.

    Red segments are the portions connected only by the 15° rule. Blue segments
    show the original 3° groups. Yellow/cyan markers and short ASCII labels make
    the one-to-many mapping visible without opening the Excel report.
    """
    differences = list(differences)
    entities: list[str] = []
    for index, difference in enumerate(differences, start=1):
        new_label = f"15D-{index:03d} {difference.story} {difference.section}"
        for start, end in _pairwise(difference.path_points):
            entities.extend(_line("MERGE15_NEW", 1, start, end))
        high_mid = _midpoint(difference.path_points)
        entities.extend(_circle("MERGE15_MID", 2, high_mid, max(text_height * 0.35, 25.0)))
        entities.extend(_text("MERGE15_MID", 2, high_mid, new_label, text_height, _text_angle(difference.path_points)))
        for group_index, path in enumerate(difference.lower_paths, start=1):
            for start, end in _pairwise(path):
                entities.extend(_line("MERGE3_GROUP", 5, start, end))
            low_mid = _midpoint(path)
            entities.extend(_circle("MERGE3_MID", 4, low_mid, max(text_height * 0.22, 15.0)))
            entities.extend(
                _text(
                    "MERGE3_MID", 4, low_mid, f"3D-{index:03d}-{group_index}",
                    text_height * 0.7, _text_angle(path),
                )
            )

    legend_x, legend_y = _legend_origin(differences)
    legend = [
        f"MERGE ANGLE CHECK 3D vs 15D  |  differences={len(differences)}",
        "RED MERGE15_NEW = newly connected by 15-degree rule",
        "BLUE MERGE3_GROUP = original 3-degree logical group",
        "YELLOW 15D / CYAN 3D = midpoint and mapping labels",
    ]
    for offset, value in enumerate(legend):
        entities.extend(
            _text(
                "MERGE_LEGEND", 7,
                (legend_x, legend_y - offset * text_height * 1.3, 0.0),
                value, text_height,
            )
        )
    if model_name:
        entities.extend(
            _text(
                "MERGE_LEGEND", 7,
                (legend_x, legend_y - len(legend) * text_height * 1.3, 0.0),
                f"MODEL {_safe_text(model_name)}", text_height * 0.8,
            )
        )

    values = _header() + _tables() + ["0", "SECTION", "2", "ENTITIES"] + entities + ["0", "ENDSEC", "0", "EOF"]
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(values) + "\n", encoding="ascii")
    return path


def export_merge_angle_csv(
    differences: Iterable[MergeAngleDifference], output_path: str | Path
) -> Path:
    """Write the DXF label-to-object/coordinate mapping for audit and CAD plugins."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["差异编号", "楼层", "截面", "15度源对象", "3度梁组", "15度点坐标"])
        for index, difference in enumerate(differences, start=1):
            points = ";".join(
                f"{point[0]:.3f},{point[1]:.3f},{point[2]:.3f}"
                for point in difference.path_points
            )
            groups = " | ".join("+".join(group) for group in difference.lower_angle_groups)
            writer.writerow([
                f"15D-{index:03d}", difference.story, difference.section,
                "+".join(difference.source_objects), groups, points,
            ])
    return path


def _legend_origin(differences: list[MergeAngleDifference]) -> tuple[float, float]:
    points = [point for difference in differences for point in difference.path_points]
    if not points:
        return (0.0, 0.0)
    return (min(point[0] for point in points), max(point[1] for point in points))
