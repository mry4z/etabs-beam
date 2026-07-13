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

from src.core.beam_line_span_analyzer import BeamSpan
from src.core.merge_comparison import MergeAngleDifference
from src.utils.geometry import Point3D, distance


_LAYERS = (
    ("BEAM_PRIMARY", 2),       # yellow: beam touching a column joint
    ("BEAM_SECONDARY", 3),     # green: beam not touching a column joint
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
    if len(points) == 1:
        return points[0]
    total = sum(distance(left, right) for left, right in _pairwise(points))
    if total <= 0.0:
        return points[0]
    return _point_at_distance(points, total / 2.0)


def _text_angle(points: tuple[Point3D, ...]) -> float:
    if len(points) < 2:
        return 0.0
    start, end = _midpoint_segment(points)
    angle = degrees(atan2(end[1] - start[1], end[0] - start[0]))
    # Keep labels readable rather than upside down.
    if angle > 90.0:
        angle -= 180.0
    elif angle <= -90.0:
        angle += 180.0
    return angle


def _point_at_distance(points: tuple[Point3D, ...], target: float) -> Point3D:
    remaining = max(0.0, target)
    for left, right in _pairwise(points):
        segment_length = distance(left, right)
        if segment_length <= 0.0:
            continue
        if remaining <= segment_length:
            ratio = remaining / segment_length
            return tuple(
                left[index] + (right[index] - left[index]) * ratio for index in range(3)
            )  # type: ignore[return-value]
        remaining -= segment_length
    return points[-1]


def _midpoint_segment(points: tuple[Point3D, ...]) -> tuple[Point3D, Point3D]:
    """Return the segment containing the true path-length midpoint."""
    if len(points) < 2:
        point = points[0] if points else (0.0, 0.0, 0.0)
        return point, point
    total = sum(distance(left, right) for left, right in _pairwise(points))
    if total <= 0.0:
        return points[0], points[0]
    remaining = total / 2.0
    last_nonzero = (points[0], points[-1])
    for left, right in _pairwise(points):
        segment_length = distance(left, right)
        if segment_length <= 0.0:
            continue
        last_nonzero = (left, right)
        if remaining <= segment_length:
            return left, right
        remaining -= segment_length
    return last_nonzero


def _line(layer: str, color: int, start: Point3D, end: Point3D) -> list[str]:
    return [
        "0", "LINE", "8", layer, "62", str(color),
        "10", f"{start[0]:.3f}", "20", f"{start[1]:.3f}", "30", f"{start[2]:.3f}",
        "11", f"{end[0]:.3f}", "21", f"{end[1]:.3f}", "31", f"{end[2]:.3f}",
    ]


def _polyline(layer: str, color: int, points: tuple[Point3D, ...]) -> list[str]:
    """Write one classic POLYLINE entity so all fragments remain one CAD object."""
    if len(points) < 2:
        return []
    values = ["0", "POLYLINE", "8", layer, "62", str(color), "66", "1", "70", "0"]
    for point in points:
        values.extend([
            "0", "VERTEX", "8", layer, "10", f"{point[0]:.3f}",
            "20", f"{point[1]:.3f}", "30", f"{point[2]:.3f}",
        ])
    values.extend(["0", "SEQEND", "8", layer])
    return values


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
        "7", "ARIAL",
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
    values.extend([
        "0", "ENDTAB", "0", "TABLE", "2", "STYLE", "70", "1",
        "0", "STYLE", "2", "ARIAL", "70", "0", "40", "0",
        "41", "1", "50", "0", "71", "0", "42", "2.5",
        "3", "arial.ttf", "4", "",
        "0", "ENDTAB", "0", "ENDSEC",
    ])
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


def export_beam_groups_dxf(
    logical_beams: Iterable[object],
    source_beams: Iterable[object],
    column_point_names: set[str],
    output_path: str | Path,
    *,
    model_name: str = "",
    text_height: float = 150.0,
) -> Path:
    """Export every logical beam as one polyline with primary/secondary labels.

    Primary classification is explicit and auditable: if any source Object's I
    or J point is touched by a column frame, the logical beam is primary (yellow);
    otherwise it is secondary (green). IDs use ``[story]-PB/SB<n>-S1`` and are
    deterministic within each story/category for downstream CAD checks.
    """
    logical_beams = list(logical_beams)
    source_by_name = {beam.name: beam for beam in source_beams}
    enriched: list[tuple[object, bool]] = []
    for beam in logical_beams:
        source_names = beam.source_names or (beam.name,)
        primary = any(
            source_by_name[name].point_i in column_point_names
            or source_by_name[name].point_j in column_point_names
            for name in source_names
            if name in source_by_name
        )
        enriched.append((beam, primary))
    counters: dict[tuple[str, bool], int] = {}
    ordered = sorted(
        enriched,
        key=lambda item: (
            str(item[0].story), not item[1],
            min(point[0] for point in (item[0].path_points or (item[0].start, item[0].end))),
            min(point[1] for point in (item[0].path_points or (item[0].start, item[0].end))),
            item[0].name,
        ),
    )
    entities: list[str] = []
    for beam, primary in ordered:
        key = (str(beam.story), primary)
        counters[key] = counters.get(key, 0) + 1
        prefix = "PB" if primary else "SB"
        number = counters[key]
        beam_id = f"{beam.story}-{prefix}{number}-S1"
        points = beam.path_points or (beam.start, beam.end)
        color = 2 if primary else 3
        layer = "BEAM_PRIMARY" if primary else "BEAM_SECONDARY"
        entities.extend(_polyline(layer, color, points))
        entities.extend(_text(layer, color, _midpoint(points), beam_id, text_height, _text_angle(points)))

    legend_x, legend_y = _legend_origin([  # type: ignore[arg-type]
        _as_difference_stub(beam) for beam, _ in ordered
    ])
    legend = [
        f"FULL BEAM GROUPS | total={len(ordered)} primary={sum(primary for _, primary in ordered)}",
        "YELLOW BEAM_PRIMARY = source beam touches a column joint",
        "GREEN BEAM_SECONDARY = no column joint at source endpoints",
        "ID format STORY-PB/SB-number-S1; each logical path is one polyline",
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


def export_beam_groups_csv(
    logical_beams: Iterable[object],
    source_beams: Iterable[object],
    column_point_names: set[str],
    output_path: str | Path,
) -> Path:
    """Write the generated PB/SB IDs and source/coordinate mapping beside DXF."""
    logical_beams = list(logical_beams)
    source_by_name = {beam.name: beam for beam in source_beams}
    enriched: list[tuple[object, bool]] = []
    for beam in logical_beams:
        names = beam.source_names or (beam.name,)
        primary = any(
            source_by_name[name].point_i in column_point_names
            or source_by_name[name].point_j in column_point_names
            for name in names if name in source_by_name
        )
        enriched.append((beam, primary))
    ordered = sorted(enriched, key=lambda item: (str(item[0].story), not item[1], item[0].name))
    counters: dict[tuple[str, bool], int] = {}
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["梁编号", "楼层", "主次梁", "源对象", "路径点坐标"])
        for beam, primary in ordered:
            key = (str(beam.story), primary)
            counters[key] = counters.get(key, 0) + 1
            prefix = "PB" if primary else "SB"
            beam_id = f"{beam.story}-{prefix}{counters[key]}-S1"
            points = beam.path_points or (beam.start, beam.end)
            serialized = ";".join(f"{p[0]:.3f},{p[1]:.3f},{p[2]:.3f}" for p in points)
            writer.writerow([beam_id, beam.story, prefix, "+".join(beam.source_names or (beam.name,)), serialized])
    return path


def export_structural_spans_dxf(
    spans: Iterable[BeamSpan], output_path: str | Path, *, model_name: str = "", text_height: float = 150.0
) -> Path:
    """Export one colored polyline and same-colored Arial label per structural span."""
    spans = list(spans)
    entities: list[str] = []
    for span in spans:
        primary = span.beam_type == "PB"
        layer = "BEAM_PRIMARY" if primary else "BEAM_SECONDARY"
        color = 2 if primary else 3
        entities.extend(_polyline(layer, color, span.path_points))
        entities.extend(
            _text(layer, color, _midpoint(span.path_points), span.span_id, text_height, _text_angle(span.path_points))
        )
    legend_x, legend_y = _legend_origin_from_paths([span.path_points for span in spans])
    legend = [
        f"STRUCTURAL SPANS | total={len(spans)} primary={sum(span.beam_type == 'PB' for span in spans)}",
        "YELLOW BEAM_PRIMARY = PB span; GREEN BEAM_SECONDARY = SB span",
        "Every POLYLINE is one support-defined span; labels use ARIAL",
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


def export_structural_spans_csv(spans: Iterable[BeamSpan], output_path: str | Path) -> Path:
    """Write the support-defined span IDs, source Objects and path coordinates."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["梁编号", "楼层", "主次梁", "弧形", "悬挑", "源对象", "路径点坐标"])
        for span in spans:
            coordinates = ";".join(f"{p[0]:.3f},{p[1]:.3f},{p[2]:.3f}" for p in span.path_points)
            writer.writerow([
                span.span_id, span.story, span.beam_type,
                "是" if span.is_curved else "否", "是" if span.is_cantilever else "否",
                "+".join(span.source_objects), coordinates,
            ])
    return path


def _legend_origin_from_paths(paths: Iterable[tuple[Point3D, ...]]) -> tuple[float, float]:
    points = [point for path in paths for point in path]
    if not points:
        return (0.0, 0.0)
    return (min(point[0] for point in points), max(point[1] for point in points))


def _as_difference_stub(beam: object) -> MergeAngleDifference:
    """Adapt a BeamGeometry for the shared legend-origin helper."""
    points = beam.path_points or (beam.start, beam.end)
    return MergeAngleDifference(str(beam.story), str(beam.section), (), (), beam.length, points, ())


def _legend_origin(differences: list[MergeAngleDifference]) -> tuple[float, float]:
    points = [point for difference in differences for point in difference.path_points]
    if not points:
        return (0.0, 0.0)
    return (min(point[0] for point in points), max(point[1] for point in points))
