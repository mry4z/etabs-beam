"""Adapt structural Beam Line/Span results to the stable Excel export contract.

The drawing/numbering algorithm is intentionally kept outside ``excel_export``.
Future numbering rules can replace the adapter while the workbook layout and
the existing object-level ``梁表`` remain unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import Mapping

from src.core.beam_line_span_analyzer import BeamLineSpanResult, BeamSpan
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation
from src.utils.geometry import Point3D, distance


@dataclass(frozen=True)
class SpanScheduleRecord:
    """One Excel-ready structural span and its clipped design result."""

    span: BeamSpan
    geometry: BeamGeometry
    design: BeamDesignResult


@dataclass(frozen=True)
class SpanScheduleMapping:
    """Stable lookup data used by both the span sheet and legacy 梁表 rows."""

    records: tuple[SpanScheduleRecord, ...]
    source_to_span_ids: Mapping[str, tuple[str, ...]]
    source_to_line_ids: Mapping[str, tuple[str, ...]]


def build_span_schedule(
    result: BeamLineSpanResult,
    beams: Mapping[str, BeamGeometry] | list[BeamGeometry],
    designs: Mapping[str, BeamDesignResult],
    tolerance_mm: float = 1.0,
) -> SpanScheduleMapping:
    """Create span records without changing the existing Excel data model.

    ETABS design stations are local to each source Object.  The adapter maps
    them to the complete Beam Line path, clips them to the current Span and
    re-bases the station position to the Span I end.  A source Object crossing
    a support can therefore contribute stations to two neighboring spans.
    """
    by_name = {beam.name: beam for beam in beams} if not isinstance(beams, Mapping) else dict(beams)
    lines = {(line.story, line.beam_type, line.line_number): line for line in result.lines}
    records: list[SpanScheduleRecord] = []
    source_to_spans: dict[str, list[str]] = {}
    source_to_lines: dict[str, list[str]] = {}
    for span in result.spans:
        line = lines[(span.story, span.beam_type, span.line_number)]
        source_beams = [by_name[name] for name in span.source_objects if name in by_name]
        geometry = _span_geometry(span, source_beams)
        design = _clip_design_to_span(span, line.path_points, source_beams, designs, tolerance_mm)
        records.append(SpanScheduleRecord(span, geometry, design))
        for name in span.source_objects:
            source_to_spans.setdefault(name, []).append(span.span_id)
            source_to_lines.setdefault(name, []).append(span.beam_line_id)
    return SpanScheduleMapping(
        tuple(records),
        {name: tuple(dict.fromkeys(values)) for name, values in source_to_spans.items()},
        {name: tuple(dict.fromkeys(values)) for name, values in source_to_lines.items()},
    )


def _span_geometry(span: BeamSpan, source_beams: list[BeamGeometry]) -> BeamGeometry:
    source = source_beams[0] if source_beams else None
    return BeamGeometry(
        name=span.span_id,
        label=span.span_id,
        story=span.story,
        point_i=source.point_i if source else "",
        point_j=source.point_j if source else "",
        start=span.path_points[0],
        end=span.path_points[-1],
        section=source.section if source else "",
        depth=source.depth if source else None,
        source_names=span.source_objects,
        path_points=span.path_points,
    )


def _clip_design_to_span(
    span: BeamSpan,
    line_path: tuple[Point3D, ...],
    source_beams: list[BeamGeometry],
    designs: Mapping[str, BeamDesignResult],
    tolerance_mm: float,
) -> BeamDesignResult:
    span_start = _path_position(line_path, span.path_points[0], tolerance_mm)
    span_end = _path_position(line_path, span.path_points[-1], tolerance_mm)
    if span_start is None or span_end is None:
        span_start, span_end = 0.0, _path_length(span.path_points)
    low, high = sorted((span_start, span_end))
    stations: list[DesignStation] = []
    source_results: list[BeamDesignResult] = []
    source_by_name = {beam.name: beam for beam in source_beams}
    for name in span.source_objects:
        source = source_by_name.get(name)
        design = designs.get(
            name,
            BeamDesignResult(
                name,
                status=DesignResultStatus.API_ERROR,
                status_message="缺少源梁设计结果。",
            ),
        )
        source_results.append(design)
        if source is None or not design.stations or source.length <= 0:
            continue
        source_start = _path_position(line_path, source.start, inf)
        source_end = _path_position(line_path, source.end, inf)
        if source_start is None or source_end is None:
            continue
        for station in design.stations:
            ratio = max(0.0, min(1.0, station.location / source.length))
            global_position = source_start + ratio * (source_end - source_start)
            if low - tolerance_mm <= global_position <= high + tolerance_mm:
                stations.append(_offset_station(station, max(0.0, min(high - low, global_position - low))))
    stations.sort(key=lambda station: station.location)
    deduped: list[DesignStation] = []
    for station in stations:
        if deduped and abs(station.location - deduped[-1].location) <= tolerance_mm:
            continue
        deduped.append(station)
    status, message = _status(source_results, bool(deduped))
    return BeamDesignResult(span.span_id, tuple(deduped), status, message)


def _status(results: list[BeamDesignResult], has_stations: bool) -> tuple[DesignResultStatus, str]:
    if not results:
        return DesignResultStatus.NO_RESULTS, "跨没有映射到源梁设计结果。"
    statuses = {result.status for result in results}
    messages = [result.status_message for result in results if result.status_message]
    if has_stations:
        return (
            (DesignResultStatus.AVAILABLE, "")
            if statuses == {DesignResultStatus.AVAILABLE}
            else (DesignResultStatus.PARTIAL, "；".join(dict.fromkeys(messages)))
        )
    if statuses == {DesignResultStatus.SKIPPED}:
        return DesignResultStatus.SKIPPED, "；".join(dict.fromkeys(messages))
    if DesignResultStatus.API_ERROR in statuses:
        return DesignResultStatus.API_ERROR, "；".join(dict.fromkeys(messages))
    return DesignResultStatus.NO_RESULTS, "；".join(dict.fromkeys(messages)) or "ETABS 未返回该跨设计站点。"


def _offset_station(station: DesignStation, location: float) -> DesignStation:
    return DesignStation(
        location,
        station.top_flexural_combo,
        station.top_flexural_area,
        station.bottom_flexural_combo,
        station.bottom_flexural_area,
        station.shear_combo,
        station.shear_area,
        station.torsion_longitudinal_combo,
        station.torsion_longitudinal_area,
        station.torsion_trans_combo,
        station.torsion_trans_area,
        station.error,
        station.warning,
    )


def _path_position(path: tuple[Point3D, ...], point: Point3D, tolerance: float) -> float | None:
    position = 0.0
    best: float | None = None
    best_gap = inf
    for left, right in zip(path, path[1:]):
        segment_length = distance(left, right)
        if segment_length == 0:
            continue
        vector = tuple(right[i] - left[i] for i in range(3))
        projection = sum((point[i] - left[i]) * vector[i] for i in range(3)) / segment_length**2
        projection = max(0.0, min(1.0, projection))
        projected = tuple(left[i] + projection * vector[i] for i in range(3))
        gap = distance(projected, point)
        if gap < best_gap:
            best_gap = gap
            best = position + projection * segment_length
        position += segment_length
    return best if best_gap <= tolerance else None


def _path_length(path: tuple[Point3D, ...]) -> float:
    return sum(distance(left, right) for left, right in zip(path, path[1:]))
