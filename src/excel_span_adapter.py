"""Adapt structural Beam Line/Span results to the stable Excel export contract.

The drawing/numbering algorithm is intentionally kept outside ``excel_export``.
Future numbering rules can replace the adapter while the workbook layout and
the existing object-level ``梁表`` remain unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import inf, isfinite
from typing import Iterable, Mapping

from src.core.beam_line_span_analyzer import BeamLineSpanResult, BeamSpan
from src.core.geometry_extractor import BeamGeometry
from src.core.numbering import canonical_object_part_id, canonical_span_id
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation
from src.design.no_design import is_explicit_no_design
from src.design.region_results import (
    DRAWING_DETAILING_POLICY_ID,
    LEGACY_DETAILING_POLICY_ID,
    SUPPORTED_DETAILING_POLICY_IDS,
    V3_DETAILING_POLICY_ID,
)
from src.utils.geometry import Point3D, distance


@dataclass(frozen=True)
class SpanScheduleRecord:
    """One Excel-ready structural span and its clipped design result."""

    span: BeamSpan
    geometry: BeamGeometry
    design: BeamDesignResult
    object_parts: tuple["SpanObjectPart", ...] = ()
    object_part: "SpanObjectPart | None" = None
    station_domain: "ValidStationDomain | None" = None
    original_span: BeamSpan | None = None
    original_geometry: BeamGeometry | None = None
    original_design: BeamDesignResult | None = None


@dataclass(frozen=True)
class SpanObjectPart:
    """One source Object projected onto its parent structural Span."""

    parent_span_id: str
    object_part_id: str
    object_part_index: int
    source_object: str
    geometry: BeamGeometry | None
    design: BeamDesignResult
    is_span_start: bool
    is_span_end: bool
    geometry_error: str = ""
    section: str = ""
    parent_offset_mm: float = 0.0
    valid_domain_status: str = ""
    original_parent_offset_mm: float | None = None
    original_object_part_id: str = ""
    original_object_part_index: int | None = None
    original_geometry_length_mm: float | None = None


@dataclass(frozen=True)
class ValidStationDomain:
    """Auditable effective calculation interval projected on a structural span."""

    status: str
    geometry_length_mm: float
    start_parent_mm: float | None
    end_parent_mm: float | None
    effective_length_mm: float | None
    usable_station_count: int
    invalid_in_domain_count: int
    unmapped_station_count: int
    station_trace: tuple[Mapping[str, object], ...]
    reason: str = ""


@dataclass(frozen=True)
class SpanScheduleMapping:
    """Stable lookup data used by both the span sheet and legacy 梁表 rows."""

    records: tuple[SpanScheduleRecord, ...]
    source_to_span_ids: Mapping[str, tuple[str, ...]]
    source_to_line_ids: Mapping[str, tuple[str, ...]]
    source_to_object_part_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    detailing_policy_id: str = LEGACY_DETAILING_POLICY_ID


def build_span_schedule(
    result: BeamLineSpanResult,
    beams: Mapping[str, BeamGeometry] | list[BeamGeometry],
    designs: Mapping[str, BeamDesignResult],
    tolerance_mm: float = 1.0,
    *,
    detailing_policy_id: str = LEGACY_DETAILING_POLICY_ID,
    valid_station_domain: bool = False,
) -> SpanScheduleMapping:
    """Create span records without changing the existing Excel data model.

    ETABS design stations are local to each source Object.  The adapter maps
    them to the complete Beam Line path, clips them to the current Span and
    re-bases the station position to the Span I end.  A source Object crossing
    a support can therefore contribute stations to two neighboring spans.
    """
    if detailing_policy_id not in SUPPORTED_DETAILING_POLICY_IDS:
        raise ValueError(f"Unsupported detailing policy: {detailing_policy_id}")
    by_name = {beam.name: beam for beam in beams} if not isinstance(beams, Mapping) else dict(beams)
    lines = {(line.story, line.beam_type, line.line_number): line for line in result.lines}
    records: list[SpanScheduleRecord] = []
    source_to_spans: dict[str, list[str]] = {}
    source_to_lines: dict[str, list[str]] = {}
    source_to_parts: dict[str, list[str]] = {}
    for span in result.spans:
        line = lines[(span.story, span.beam_type, span.line_number)]
        source_beams = [by_name[name] for name in span.source_objects if name in by_name]
        line_path = _line_path_for_span(line, span, tolerance_mm)
        geometry = _span_geometry(span, source_beams)
        design = _clip_design_to_span(
            span,
            line_path,
            source_beams,
            designs,
            tolerance_mm,
            deduplicate_stations=detailing_policy_id not in {
                DRAWING_DETAILING_POLICY_ID,
                V3_DETAILING_POLICY_ID,
            },
        )
        object_parts = _span_object_parts(span, line_path, by_name, designs, tolerance_mm)
        records.append(SpanScheduleRecord(span, geometry, design, object_parts))
        for name in span.source_objects:
            source_to_spans.setdefault(name, []).append(span.span_id)
            source_to_lines.setdefault(name, []).append(span.beam_line_id)
        mixed = len({part.section.strip() for part in object_parts if part.section.strip()}) > 1
        for part in section_runs(object_parts) if mixed else object_parts:
            names = part.geometry.source_names if part.geometry else (part.source_object,)
            for name in names:
                source_to_parts.setdefault(name, []).append(part.object_part_id)
    schedule = SpanScheduleMapping(
        tuple(records),
        {name: tuple(dict.fromkeys(values)) for name, values in source_to_spans.items()},
        {name: tuple(dict.fromkeys(values)) for name, values in source_to_lines.items()},
        {name: tuple(dict.fromkeys(values)) for name, values in source_to_parts.items()},
        detailing_policy_id,
    )
    if valid_station_domain:
        if detailing_policy_id != V3_DETAILING_POLICY_ID:
            raise ValueError("Valid native-station domains are available only for the revised V3 policy.")
        return apply_valid_station_domains(schedule)
    return schedule


def _line_path_for_span(line: Any, span: BeamSpan, tolerance_mm: float) -> tuple[Point3D, ...]:
    """Return the continuous concrete path component containing this Span."""

    paths = tuple(getattr(line, "path_parts", ()) or (line.path_points,))
    for path in paths:
        if (
            _path_position(path, span.path_points[0], tolerance_mm) is not None
            and _path_position(path, span.path_points[-1], tolerance_mm) is not None
        ):
            return path
    return line.path_points


def apply_valid_station_domains(schedule: SpanScheduleMapping) -> SpanScheduleMapping:
    """Build a calculation view whose parent interval is bounded by usable native stations.

    The BeamSpan identity and endpoint topology remain source values.  Only
    the schedule geometry and design coordinates used by demand consumers are
    sliced and rebased; original geometry and station coordinates remain on
    each record/domain for audit.
    """
    records: list[SpanScheduleRecord] = []
    source_to_parts: dict[str, list[str]] = {}
    for record in schedule.records:
        domain = _valid_station_domain(record)
        if domain.status != "AVAILABLE":
            records.append(replace(
                record,
                station_domain=domain,
                original_span=record.span,
                original_geometry=record.geometry,
                original_design=record.design,
            ))
            for part in record.object_parts:
                _map_source_part(source_to_parts, part)
            continue

        assert domain.start_parent_mm is not None and domain.end_parent_mm is not None
        start = domain.start_parent_mm
        end = domain.end_parent_mm
        points = _slice_path(tuple(record.span.path_points), start, end)
        effective_geometry = replace(
            record.geometry,
            start=points[0],
            end=points[-1],
            path_points=points,
        )
        effective_design = _rebase_design_to_valid_domain(record.design, start, end, domain)
        sections = {part.section.strip() for part in record.object_parts if part.section.strip()}
        original_parts = section_runs(record.object_parts) if len(sections) > 1 else record.object_parts
        parts = tuple(_clip_object_part_to_valid_domain(part, start, end) for part in original_parts)
        effective_record = replace(
            record,
            geometry=effective_geometry,
            design=effective_design,
            object_parts=parts,
            station_domain=domain,
            original_span=record.span,
            original_geometry=record.geometry,
            original_design=record.design,
        )
        records.append(effective_record)
        for part in parts:
            _map_source_part(source_to_parts, part)

    return replace(
        schedule,
        records=tuple(records),
        source_to_object_part_ids={
            name: tuple(dict.fromkeys(values)) for name, values in source_to_parts.items()
        },
    )


def _valid_station_domain(record: SpanScheduleRecord) -> ValidStationDomain:
    stations = tuple(record.design.stations)
    geometry_length = float(record.geometry.length)
    candidates: list[float] = []
    station_states: list[tuple[DesignStation, float | None, tuple[str, ...]]] = []
    unmapped = 0
    for station in stations:
        parent_value = station.parent_location_mm
        if parent_value is None:
            parent_value = station.location
        try:
            parent_location = float(parent_value)
        except (TypeError, ValueError):
            parent_location = None
        if parent_location is not None and not isfinite(parent_location):
            parent_location = None
        invalid = _invalid_station_fields(station)
        if parent_location is None:
            invalid = tuple(dict.fromkeys((*invalid, "PARENT_STATION_LOCATION")))
            unmapped += 1
        elif not invalid:
            candidates.append(parent_location)
        station_states.append((station, parent_location, invalid))

    unique_locations = sorted(set(candidates))
    if len(unique_locations) < 2 or unique_locations[-1] - unique_locations[0] <= 1e-9:
        return ValidStationDomain(
            status="NO_VALID_STATION_DOMAIN",
            geometry_length_mm=geometry_length,
            start_parent_mm=None,
            end_parent_mm=None,
            effective_length_mm=None,
            usable_station_count=len(candidates),
            invalid_in_domain_count=0,
            unmapped_station_count=unmapped,
            station_trace=tuple(
                _station_trace(station, parent, None, invalid, False)
                for station, parent, invalid in station_states
            ),
            reason=("NO_VALID_STATIONS" if not candidates else "FEWER_THAN_TWO_DISTINCT_VALID_STATIONS"),
        )

    start, end = unique_locations[0], unique_locations[-1]
    invalid_in_domain = sum(
        1 for _station, parent, invalid in station_states
        if parent is not None and start - 1e-9 <= parent <= end + 1e-9 and invalid
    )
    trace = tuple(
        _station_trace(
            station,
            parent,
            parent - start if parent is not None and start - 1e-9 <= parent <= end + 1e-9 else None,
            invalid,
            parent is not None and start - 1e-9 <= parent <= end + 1e-9,
        )
        for station, parent, invalid in station_states
    )
    return ValidStationDomain(
        status="AVAILABLE",
        geometry_length_mm=geometry_length,
        start_parent_mm=start,
        end_parent_mm=end,
        effective_length_mm=end - start,
        usable_station_count=len(candidates),
        invalid_in_domain_count=invalid_in_domain,
        unmapped_station_count=unmapped,
        station_trace=trace,
        reason=("INVALID_STATION_WITHIN_DOMAIN" if invalid_in_domain else
                "UNMAPPED_NATIVE_STATION" if unmapped else ""),
    )


def _invalid_station_fields(station: DesignStation) -> tuple[str, ...]:
    fields = {
        "TopFlex": station.top_flexural_area,
        "TopTotal": station.as_total_top_mm2,
        "BottomFlex": station.bottom_flexural_area,
        "BottomTotal": station.as_total_bottom_mm2,
        "Shear": station.shear_area,
        "TorsionLongitudinal": station.torsion_longitudinal_area,
        "TorsionTransverse": station.torsion_trans_area,
    }
    invalid = [name for name, value in fields.items() if not _finite_nonnegative(value)]
    for name, value in (("TopAsMin", station.top_min_area), ("BottomAsMin", station.bottom_min_area)):
        if value is not None and not _finite_nonnegative(value):
            invalid.append(name)
    if station.error.strip():
        invalid.append("DESIGN_ERROR")
    if station.station_projection_error.strip():
        invalid.append(station.station_projection_error.strip())
    return tuple(dict.fromkeys(invalid))


def _finite_nonnegative(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return isfinite(number) and number >= 0.0


def _station_trace(
    station: DesignStation,
    parent_location: float | None,
    calculation_location: float | None,
    invalid_fields: tuple[str, ...],
    in_domain: bool,
) -> Mapping[str, object]:
    native_location = station.native_location_mm
    if native_location is None:
        native_location = station.location
    try:
        native = float(native_location)
    except (TypeError, ValueError):
        native = None
    if native is not None and not isfinite(native):
        native = None
    return {
        "source_object": station.source_object,
        "native_location_mm": native,
        "original_parent_location_mm": parent_location,
        "calculation_location_mm": calculation_location,
        "in_effective_domain": in_domain,
        "usable_for_domain": not invalid_fields and parent_location is not None,
        "invalid_fields": list(invalid_fields),
        "top_combo": station.top_flexural_combo,
        "bottom_combo": station.bottom_flexural_combo,
        "shear_combo": station.shear_combo,
        "torsion_longitudinal_combo": station.torsion_longitudinal_combo,
        "torsion_transverse_combo": station.torsion_trans_combo,
        "error": station.error,
        "warning": station.warning,
    }


def _rebase_design_to_valid_domain(
    design: BeamDesignResult,
    start: float,
    end: float,
    domain: ValidStationDomain,
) -> BeamDesignResult:
    stations: list[DesignStation] = []
    for station in design.stations:
        parent_location = station.parent_location_mm
        if parent_location is None:
            parent_location = station.location
        try:
            parent = float(parent_location)
        except (TypeError, ValueError):
            stations.append(station)
            continue
        if not isfinite(parent) or parent < start - 1e-9 or parent > end + 1e-9:
            continue
        stations.append(replace(station, location=max(0.0, min(end - start, parent - start))))
    status = design.status
    message = design.status_message
    if status is DesignResultStatus.AVAILABLE and (domain.invalid_in_domain_count or domain.unmapped_station_count):
        status = DesignResultStatus.PARTIAL
        message = "; ".join(filter(None, (message, domain.reason)))
    return replace(design, stations=tuple(stations), status=status, status_message=message)


def _clip_object_part_to_valid_domain(
    part: SpanObjectPart,
    start: float,
    end: float,
) -> SpanObjectPart:
    if part.geometry is None or part.geometry_error:
        return replace(
            part,
            original_parent_offset_mm=(part.parent_offset_mm if part.original_parent_offset_mm is None
                                       else part.original_parent_offset_mm),
            original_object_part_id=part.original_object_part_id or part.object_part_id,
            original_object_part_index=(part.object_part_index if part.original_object_part_index is None
                                        else part.original_object_part_index),
        )
    original_offset = part.parent_offset_mm
    original_length = float(part.geometry.length)
    part_end = original_offset + original_length
    overlap_start = max(start, original_offset)
    overlap_end = min(end, part_end)
    base = replace(
        part,
        original_parent_offset_mm=(original_offset if part.original_parent_offset_mm is None
                                   else part.original_parent_offset_mm),
        original_object_part_id=part.original_object_part_id or part.object_part_id,
        original_object_part_index=(part.object_part_index if part.original_object_part_index is None
                                    else part.original_object_part_index),
        original_geometry_length_mm=(original_length if part.original_geometry_length_mm is None
                                     else part.original_geometry_length_mm),
    )
    if overlap_end <= overlap_start + 1e-9:
        design = replace(
            part.design,
            stations=(),
            status=(DesignResultStatus.NO_RESULTS
                    if part.design.status is DesignResultStatus.AVAILABLE else part.design.status),
            status_message=("OUTSIDE_VALID_STATION_DOMAIN"
                            if part.design.status is DesignResultStatus.AVAILABLE
                            else part.design.status_message),
        )
        return replace(base, design=design, valid_domain_status="OUTSIDE_VALID_STATION_DOMAIN")

    local_start = overlap_start - original_offset
    local_end = overlap_end - original_offset
    source_points = tuple(part.geometry.path_points) or (part.geometry.start, part.geometry.end)
    points = _slice_path(source_points, local_start, local_end)
    effective_geometry = replace(
        part.geometry,
        start=points[0],
        end=points[-1],
        path_points=points,
    )
    stations: list[DesignStation] = []
    for station in part.design.stations:
        try:
            local = float(station.location)
        except (TypeError, ValueError):
            stations.append(station)
            continue
        if not isfinite(local) or local < local_start - 1e-9 or local > local_end + 1e-9:
            continue
        stations.append(replace(station, location=max(0.0, min(local_end - local_start, local - local_start))))
    return replace(
        base,
        geometry=effective_geometry,
        design=replace(part.design, stations=tuple(stations)),
        parent_offset_mm=overlap_start - start,
        is_span_start=part.is_span_start and local_start <= 1e-9,
        is_span_end=part.is_span_end and local_end >= original_length - 1e-9,
        valid_domain_status="IN_VALID_STATION_DOMAIN",
    )


def _map_source_part(mapping: dict[str, list[str]], part: SpanObjectPart) -> None:
    names = part.geometry.source_names if part.geometry else (part.source_object,)
    for name in names:
        mapping.setdefault(name, []).append(part.object_part_id)


def span_object_part_paths(
    schedule: SpanScheduleMapping,
    excluded_source_names: Iterable[str] = (),
) -> dict[str, tuple[tuple[str, tuple[Point3D, ...]], ...]]:
    """Return Object-part paths for mixed-section structural-span labels."""

    excluded_sources = {str(name) for name in excluded_source_names}
    output: dict[str, tuple[tuple[str, tuple[Point3D, ...]], ...]] = {}
    for record in schedule.records:
        sections = {part.section.strip() for part in record.object_parts if part.section.strip()}
        if len(sections) <= 1:
            continue
        label = canonical_span_id(
            record.span.story,
            record.span.beam_type,
            record.span.line_number,
            record.span.span_index,
        )
        paths = []
        for part in section_runs(record.object_parts):
            names = tuple(part.geometry.source_names) if part.geometry else ()
            names = names or (part.source_object,)
            if names and all(name in excluded_sources for name in names):
                continue
            if part.geometry is not None:
                paths.append(
                    (part.object_part_id, tuple(part.geometry.path_points or (part.geometry.start, part.geometry.end)))
                )
        if paths:
            output[label] = tuple(paths)
    return output


def _span_object_parts(
    span: BeamSpan,
    line_path: tuple[Point3D, ...],
    by_name: Mapping[str, BeamGeometry],
    designs: Mapping[str, BeamDesignResult],
    tolerance_mm: float,
) -> tuple[SpanObjectPart, ...]:
    span_start = _path_position(line_path, span.path_points[0], tolerance_mm)
    span_end = _path_position(line_path, span.path_points[-1], tolerance_mm)
    if span_start is None or span_end is None:
        return tuple(
            _unresolved_object_part(span, name, index, "结构跨端点无法定位到Beam Line。", designs)
            for index, name in enumerate(span.source_objects, start=1)
        )
    low_span, high_span = sorted((span_start, span_end))
    span_path = span.path_points
    parts: list[SpanObjectPart] = []
    for index, name in enumerate(span.source_objects, start=1):
        source = by_name.get(name)
        if source is None:
            parts.append(_unresolved_object_part(span, name, index, f"源梁对象 {name} 缺少几何信息。", designs))
            continue
        source_path = tuple(source.path_points) or (source.start, source.end)
        if len(source_path) < 2 or source.length <= 0:
            parts.append(
                _unresolved_object_part(span, name, index, f"源梁对象 {name} 的路径无效。", designs, source.section)
            )
            continue
        if distance(source_path[0], source.start) > distance(source_path[-1], source.start):
            source_path = tuple(reversed(source_path))
        source_positions = tuple(_path_position(line_path, point, tolerance_mm) for point in source_path)
        if any(position is None for position in source_positions):
            parts.append(
                _unresolved_object_part(span, name, index, f"源梁对象 {name} 无法定位到Beam Line。", designs, source.section)
            )
            continue
        object_low = max(low_span, min(float(position) for position in source_positions if position is not None))
        object_high = min(high_span, max(float(position) for position in source_positions if position is not None))
        if object_high - object_low <= tolerance_mm:
            parts.append(
                _unresolved_object_part(
                    span, name, index, f"源梁对象 {name} 在父跨内没有可识别的有效长度。", designs, source.section
                )
            )
            continue

        forward = span_start <= span_end
        offset = object_low - low_span if forward else high_span - object_high
        points = _slice_path(span_path, offset, offset + object_high - object_low)
        parent_id = canonical_span_id(span.story, span.beam_type, span.line_number, span.span_index)
        part_id = canonical_object_part_id(parent_id, index)
        geometry = BeamGeometry(
            name=part_id,
            label=part_id,
            story=span.story,
            point_i=_part_endpoint_name(source, points[0], tolerance_mm),
            point_j=_part_endpoint_name(source, points[-1], tolerance_mm),
            start=points[0],
            end=points[-1],
            section=source.section,
            depth=source.depth,
            source_names=(name,),
            path_points=points,
        )
        design = _clip_design_to_object_part(
            line_path,
            source,
            source_path,
            object_low,
            object_high,
            designs.get(name),
            part_id,
            tolerance_mm,
        )
        if not forward:
            design = replace(design, stations=tuple(sorted(
                (replace(station, location=object_high - object_low - station.location)
                 for station in design.stations), key=lambda station: station.location)))
        mapped_part_stations: list[DesignStation] = []
        for station in design.stations:
            try:
                local_location = float(station.location)
            except (TypeError, ValueError):
                local_location = float("nan")
            mapped_part_stations.append(
                replace(
                    station,
                    parent_location_mm=offset + local_location if isfinite(local_location) else None,
                    station_projection_error=(station.station_projection_error or
                                              "INVALID_NATIVE_STATION_LOCATION" if not isfinite(local_location)
                                              else station.station_projection_error),
                )
            )
        design = replace(design, stations=tuple(mapped_part_stations))
        parts.append(
            SpanObjectPart(
                parent_id,
                part_id,
                index,
                name,
                geometry,
                design,
                offset <= tolerance_mm,
                abs(offset + object_high - object_low - (high_span - low_span)) <= tolerance_mm,
                section=source.section,
                parent_offset_mm=offset,
            )
        )
    return tuple(parts)


def section_runs(parts: tuple[SpanObjectPart, ...], tolerance_mm: float = 1.0) -> tuple[SpanObjectPart, ...]:
    """Combine touching same-section parts in parent I-to-J order."""
    if parts and any(part.valid_domain_status for part in parts):
        return parts
    if any(part.geometry is None or part.geometry_error for part in parts):
        return parts
    ordered = sorted(parts, key=lambda part: part.parent_offset_mm)
    groups: list[list[SpanObjectPart]] = []
    for part in ordered:
        previous = groups[-1][-1] if groups else None
        if (previous is not None and part.section.strip() and part.section == previous.section
                and abs(previous.parent_offset_mm + previous.geometry.length - part.parent_offset_mm) <= tolerance_mm):
            groups[-1].append(part)
        else:
            groups.append([part])
    runs = []
    for index, group in enumerate(groups, 1):
        first, last = group[0], group[-1]
        run_id = canonical_object_part_id(first.parent_span_id, index)
        names = tuple(part.source_object for part in group)
        points = tuple(point for n, part in enumerate(group)
                       for point in (part.geometry.path_points if n == 0 else part.geometry.path_points[1:]))
        geometry = replace(first.geometry, name=run_id, label=run_id, end=last.geometry.end,
                           point_j=last.geometry.point_j, source_names=names, path_points=points)
        stations = tuple(sorted((replace(station,
            location=station.location + part.parent_offset_mm - first.parent_offset_mm,
            source_object=station.source_object or part.source_object)
            for part in group for station in part.design.stations), key=lambda station: station.location))
        status, message = _status([part.design for part in group], bool(stations))
        design = replace(first.design, beam_name=run_id, stations=stations, status=status,
                         status_message=message, source_objects=names)
        runs.append(replace(first, object_part_id=run_id, object_part_index=index,
                            geometry=geometry, design=design, is_span_end=last.is_span_end))
    return tuple(runs)


def _unresolved_object_part(
    span: BeamSpan,
    name: str,
    index: int,
    reason: str,
    designs: Mapping[str, BeamDesignResult],
    section: str = "",
) -> SpanObjectPart:
    parent_id = canonical_span_id(span.story, span.beam_type, span.line_number, span.span_index)
    part_id = canonical_object_part_id(parent_id, index)
    design = designs.get(name)
    if design is None:
        design = BeamDesignResult(
            name,
            status=DesignResultStatus.API_ERROR,
            status_message="缺少源梁设计结果。",
            source_objects=(name,),
        )
    return SpanObjectPart(
        parent_id,
        part_id,
        index,
        name,
        None,
        replace(
            design,
            beam_name=part_id,
            stations=(),
            status=DesignResultStatus.API_ERROR,
            status_message=reason,
            source_objects=(name,),
        ),
        False,
        False,
        reason,
        section,
    )


def _part_endpoint_name(source: BeamGeometry, point: Point3D, tolerance_mm: float) -> str:
    if distance(point, source.start) <= tolerance_mm:
        return source.point_i
    if distance(point, source.end) <= tolerance_mm:
        return source.point_j
    return ""


def _clip_design_to_object_part(
    line_path: tuple[Point3D, ...],
    source: BeamGeometry,
    source_path: tuple[Point3D, ...],
    part_start: float,
    part_end: float,
    design: BeamDesignResult | None,
    part_id: str,
    tolerance_mm: float,
) -> BeamDesignResult:
    if design is None:
        return BeamDesignResult(
            part_id,
            status=DesignResultStatus.API_ERROR,
            status_message="缺少源梁设计结果。",
            source_objects=(source.name,),
        )
    stations: list[DesignStation] = []
    source_length = _path_length(source_path)
    if source_length > 0:
        for station in design.stations:
            station_source = station.source_object.strip()
            if station_source and station_source != source.name:
                continue
            try:
                native_location = float(station.location)
            except (TypeError, ValueError):
                native_location = float("nan")
            if not isfinite(native_location):
                stations.append(replace(
                    station,
                    native_location_mm=native_location,
                    parent_location_mm=None,
                    station_projection_error="INVALID_NATIVE_STATION_LOCATION",
                ))
                continue
            outside_source = native_location < -tolerance_mm or native_location > source_length + tolerance_mm
            ratio = max(0.0, min(1.0, native_location / source_length))
            point = _point_at_distance(source_path, ratio * source_length)
            global_position = _path_position(line_path, point, tolerance_mm)
            if global_position is None or global_position < part_start - tolerance_mm or global_position > part_end + tolerance_mm:
                continue
            mapped_station = _offset_station(
                station,
                max(0.0, min(part_end - part_start, global_position - part_start)),
                parent_location_mm=global_position,
                native_location_mm=native_location,
            )
            if outside_source:
                mapped_station = replace(mapped_station, station_projection_error="NATIVE_STATION_OUTSIDE_SOURCE_GEOMETRY")
            stations.append(mapped_station)
    stations.sort(key=lambda station: station.location)
    status = design.status
    message = design.status_message
    if not stations and status is DesignResultStatus.AVAILABLE:
        status = DesignResultStatus.NO_RESULTS
        message = f"源梁对象 {source.name} 在该结构跨子段内没有设计站点。"
    return replace(
        design,
        beam_name=part_id,
        stations=tuple(stations),
        status=status,
        status_message=message,
        source_objects=(source.name,),
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
    *,
    deduplicate_stations: bool = True,
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
            try:
                native_location = float(station.location)
            except (TypeError, ValueError):
                native_location = float("nan")
            if not isfinite(native_location):
                stations.append(replace(
                    station,
                    native_location_mm=native_location,
                    parent_location_mm=None,
                    station_projection_error="INVALID_NATIVE_STATION_LOCATION",
                ))
                continue
            outside_source = native_location < -tolerance_mm or native_location > source.length + tolerance_mm
            ratio = max(0.0, min(1.0, native_location / source.length))
            global_position = source_start + ratio * (source_end - source_start)
            if low - tolerance_mm <= global_position <= high + tolerance_mm:
                position = global_position - low if span_start <= span_end else high - global_position
                mapped_station = _offset_station(
                    station,
                    max(0.0, min(high - low, position)),
                    parent_location_mm=max(0.0, min(high - low, position)),
                    native_location_mm=native_location,
                )
                if outside_source:
                    mapped_station = replace(mapped_station, station_projection_error="NATIVE_STATION_OUTSIDE_SOURCE_GEOMETRY")
                stations.append(mapped_station)
    stations.sort(key=lambda station: station.location)
    selected = stations
    if deduplicate_stations:
        deduped: list[DesignStation] = []
        for station in stations:
            if deduped and abs(station.location - deduped[-1].location) <= tolerance_mm:
                continue
            deduped.append(station)
        selected = deduped
    status, message = _status(source_results, bool(selected))
    sources = tuple(
        dict.fromkeys(source for result in source_results for source in (result.source_objects or (result.beam_name,)))
    )
    result_source = ",".join(dict.fromkeys(result.result_source for result in source_results if result.result_source))
    units = tuple(dict.fromkeys(result.units for result in source_results if result.units))
    normalized_units = units[0] if len(units) == 1 else ("MIXED" if units else "")
    return BeamDesignResult(
        span.span_id,
        tuple(selected),
        status,
        message,
        sources,
        normalized_units,
        result_source,
    )


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


def _offset_station(
    station: DesignStation,
    location: float,
    *,
    parent_location_mm: float | None = None,
    native_location_mm: float | None = None,
) -> DesignStation:
    return replace(
        station,
        location=location,
        native_location_mm=(station.native_location_mm if station.native_location_mm is not None
                            else native_location_mm if native_location_mm is not None else station.location),
        parent_location_mm=(station.parent_location_mm if station.parent_location_mm is not None
                            else parent_location_mm),
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


def _point_at_distance(path: tuple[Point3D, ...], target: float) -> Point3D:
    if not path:
        raise ValueError("Cannot locate a point on an empty path.")
    if len(path) == 1:
        return path[0]
    remaining = max(0.0, min(_path_length(path), target))
    for left, right in zip(path, path[1:]):
        segment_length = distance(left, right)
        if segment_length <= 0.0:
            continue
        if remaining <= segment_length:
            fraction = remaining / segment_length
            return tuple(left[index] + fraction * (right[index] - left[index]) for index in range(3))  # type: ignore[return-value]
        remaining -= segment_length
    return path[-1]


def _slice_path(path: tuple[Point3D, ...], start: float, end: float) -> tuple[Point3D, ...]:
    if len(path) < 2:
        return path
    low = max(0.0, min(_path_length(path), start))
    high = max(low, min(_path_length(path), end))
    points: list[Point3D] = [_point_at_distance(path, low)]
    position = 0.0
    for left, right in zip(path, path[1:]):
        position += distance(left, right)
        if low < position < high:
            points.append(right)
    final_point = _point_at_distance(path, high)
    if distance(points[-1], final_point) > 0.0:
        points.append(final_point)
    return tuple(points)
