"""Per-story DXF exports for beam design demands and true section sizes."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import ceil, cos, isfinite, radians, sin
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping

from src.core.geometry_extractor import BeamGeometry, ColumnOutline
from src.core.numbering import canonical_object_part_id, canonical_span_id
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation
from src.design.no_design import is_explicit_no_design
from src.design.region_results import (
    DRAWING_DETAILING_POLICY_ID,
    DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE,
    LEGACY_DETAILING_POLICY_ID,
    SUPPORTED_DETAILING_POLICY_IDS,
    V3_DETAILING_POLICY_ID,
    build_region_controls,
    group_design_stations,
)
from src.drawing.dxf_export import (
    _header,
    _midpoint,
    _polyline,
    _column_outline_entities,
    _safe_text,
    _tables,
    _text_angle,
)
from src.excel_span_adapter import SpanObjectPart, SpanScheduleMapping, SpanScheduleRecord, section_runs
from src.utils.geometry import Point3D, distance

_RESULT_LAYERS = (
    ("col", 2),
    ("BEAM_RESULT_LINE", 7),
    ("dsptext_beam", 7),
    ("BEAM_RESULT_ERROR", 1),
)
_SECTION_LAYERS = (
    ("col", 2),
    ("BEAM_SECTION_LINE", 7),
    ("dsptext_beam", 7),
    ("BEAM_SECTION_ERROR", 1),
)
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TOWER_STORY = re.compile(r"^(?P<tower>T\d+)-(?P<story>.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class BeamDxfIssue:
    output: str
    story: str
    span_id: str
    code: str
    message: str


@dataclass(frozen=True)
class BeamDxfSpanSummary:
    """Auditable status for one span in the two per-story DXF outputs."""

    output: str
    story: str
    span_id: str
    status: str
    reason: str = ""


@dataclass(frozen=True)
class BeamDxfExport:
    result_paths: tuple[Path, ...]
    section_paths: tuple[Path, ...]
    issues: tuple[BeamDxfIssue, ...]
    summaries: tuple[BeamDxfSpanSummary, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class _SourceInterval:
    """One source BeamGeometry projected onto a Span path."""

    order: int
    name: str
    start: float
    end: float
    section: str
    geometry_resolved: bool
    section_error: tuple[str, str] | None = None


@dataclass(frozen=True)
class _SpanSegment:
    """A continuous, same-section part of one structural Span."""

    source_objects: tuple[str, ...]
    path_points: tuple[Point3D, ...]
    start: float
    end: float
    section: str
    section_error: tuple[str, str] | None = None
    design: BeamDesignResult | None = None
    object_part_index: int | None = None
    valid_domain_status: str = ""


def export_beam_result_and_section_dxfs(
    schedule: SpanScheduleMapping,
    beams: Mapping[str, BeamGeometry] | list[BeamGeometry] | tuple[BeamGeometry, ...],
    section_loader: Callable[[str], Mapping[str, Any]],
    output_dir: str | Path,
    *,
    text_height: float = 50.0,
    column_outlines: Iterable[ColumnOutline] = (),
    detailing_policy_id: str | None = None,
    region_controls_by_row_id: Mapping[str, Mapping[str, Mapping[str, Any] | None] | None] | None = None,
) -> BeamDxfExport:
    """Write calculation-result and section-only DXFs for every story."""

    if text_height not in {25.0, 50.0, 75.0, 100.0}:
        raise ValueError("DXF text height must be one of 25, 50, 75 or 100 mm.")
    policy_id = detailing_policy_id or schedule.detailing_policy_id
    if policy_id not in SUPPORTED_DETAILING_POLICY_IDS:
        raise ValueError(f"Unsupported detailing policy: {policy_id}")
    if policy_id != schedule.detailing_policy_id:
        raise ValueError(
            f"SPAN_SCHEDULE_POLICY_MISMATCH: schedule={schedule.detailing_policy_id}, drawing={policy_id}"
        )
    destination = Path(output_dir)
    # ``output_dir`` is the already-resolved model delivery root.  Both DXF
    # families are final per-story deliverables and therefore live directly
    # below that root; audits/intermediate payloads are owned by the workflow's
    # ``_data`` directory instead.
    destination.mkdir(parents=True, exist_ok=True)
    by_name = dict(beams) if isinstance(beams, Mapping) else {beam.name: beam for beam in beams}
    section_cache: dict[str, tuple[Mapping[str, Any] | None, str]] = {}
    by_story = _group_logical_stories(schedule.records)
    all_column_outlines = tuple(column_outlines)

    result_paths: list[Path] = []
    section_paths: list[Path] = []
    issues: list[BeamDxfIssue] = []
    used_names: set[str] = set()
    summaries: list[BeamDxfSpanSummary] = []
    for story in sorted(by_story):
        records = by_story[story]
        result_entities: list[str] = []
        section_entities: list[str] = []
        story_elevations = {
            float(point[2]) for record in records for point in record.span.path_points
        }
        story_outlines = tuple(
            outline
            for outline in all_column_outlines
            if any(abs(float(outline.support[2]) - elevation) <= 1.0 for elevation in story_elevations)
        )
        result_entities.extend(_column_outline_entities(story_outlines))
        section_entities.extend(_column_outline_entities(story_outlines))
        for record in records:
            base_span_id = canonical_span_id(
                record.span.story,
                record.span.beam_type,
                record.span.line_number,
                record.span.span_index,
            )
            part_sections = {
                part.section.strip()
                or (part.geometry.section.strip() if part.geometry is not None else "")
                for part in record.object_parts
            }
            part_sections.discard("")
            mixed_sections = len(part_sections) > 1
            use_object_parts = mixed_sections and bool(record.object_parts)
            segments = _object_part_segments(record.object_parts) if use_object_parts else _span_segments(record, by_name)
            parent_end_depths = None
            if use_object_parts and segments:
                parent_domain_segments = tuple(
                    segment for segment in segments
                    if segment.valid_domain_status != "OUTSIDE_VALID_STATION_DOMAIN"
                )
                first_depth = _segment_section_label(parent_domain_segments[0], section_loader, section_cache)[1] if parent_domain_segments else None
                last_depth = _segment_section_label(parent_domain_segments[-1], section_loader, section_cache)[1] if parent_domain_segments else None
                if first_depth is not None and last_depth is not None:
                    parent_end_depths = (first_depth, last_depth)
            if use_object_parts:
                for part in record.object_parts:
                    if part.geometry is not None:
                        continue
                    span_id = part.object_part_id
                    reason = part.geometry_error or "Object 子段几何无法定位，未绘制该子段。"
                    code = "OBJECT_PART_GEOMETRY_UNRESOLVED"
                    for output in ("section", "result"):
                        issues.append(BeamDxfIssue(output, story, span_id, code, reason))
                        summaries.append(BeamDxfSpanSummary(output, story, span_id, code, reason))
            for segment_index, segment in enumerate(segments, start=1):
                part_index = segment.object_part_index or segment_index
                span_id = canonical_object_part_id(base_span_id, part_index)
                points = _flatten(segment.path_points)
                segment_record = _segment_record(record, segment, span_id)
                excluded_no_design = _is_excluded_no_design(segment_record.design)
                if excluded_no_design:
                    reason = segment_record.design.status_message
                    summaries.append(
                        BeamDxfSpanSummary("section", story, span_id, "EXCLUDED_NO_DESIGN", reason)
                    )
                    summaries.append(
                        BeamDxfSpanSummary("result", story, span_id, "EXCLUDED_NO_DESIGN", reason)
                    )
                    continue
                section_label, depth_mm, section_error = _segment_section_label(
                    segment,
                    section_loader,
                    section_cache,
                )
                if section_error:
                    issues.append(BeamDxfIssue("section", story, span_id, section_error[0], section_error[1]))
                    summaries.append(BeamDxfSpanSummary("section", story, span_id, section_error[0], section_error[1]))
                    _append_section_annotation(
                        section_entities,
                        points,
                        "ERR",
                        text_height,
                        error=True,
                    )
                else:
                    summaries.append(BeamDxfSpanSummary("section", story, span_id, "AVAILABLE"))
                    _append_section_annotation(
                        section_entities,
                        points,
                        section_label or "ERR",
                        text_height,
                        error=False,
                    )

                control_row_id = span_id if use_object_parts else base_span_id
                domain_error = _station_domain_error(record, segment)
                if (region_controls_by_row_id is not None and control_row_id not in region_controls_by_row_id
                        and not domain_error):
                    raise KeyError(f"FROZEN_REGION_CONTROLS_MISSING_ROW: {control_row_id}")
                if domain_error:
                    result_labels, result_error = ("", "", "", ""), domain_error
                elif use_object_parts and parent_end_depths is None:
                    result_labels, result_error = ("", "", "", ""), (
                        "PARENT_END_SECTION_UNRESOLVED", "无法确定父跨两端真实截面高度。")
                elif region_controls_by_row_id is not None:
                    result_labels, result_error = _result_labels_from_region_controls(
                        region_controls_by_row_id[control_row_id],
                        segment_record.geometry.length,
                        depth_mm,
                    )
                else:
                    result_labels, result_error = _result_labels(
                        segment_record, depth_mm, detailing_policy_id=policy_id,
                        parent_length_mm=record.geometry.length, segment_start_mm=segment.start,
                        parent_end_depths_mm=parent_end_depths,
                    )
                if result_error:
                    issues.append(BeamDxfIssue("result", story, span_id, result_error[0], result_error[1]))
                    summaries.append(
                        BeamDxfSpanSummary(
                            "result",
                            story,
                            span_id,
                            _result_summary_status(segment_record.design, result_error[0]),
                            result_error[1],
                        )
                    )
                    result_labels = ("GERR-ERR", "ERR-ERR-ERR", "ERR-ERR-ERR", "VTERR-ERR")
                else:
                    summaries.append(BeamDxfSpanSummary("result", story, span_id, "AVAILABLE"))
                _append_result_annotation(
                    result_entities,
                    points,
                    result_labels,
                    text_height,
                    error=result_error is not None,
                )

        safe_name = _unique_story_name(story, used_names)
        result_paths.append(_write_dxf(destination / f"wpj-{safe_name}.dxf", _RESULT_LAYERS, result_entities))
        section_paths.append(_write_dxf(destination / f"构件编号-{safe_name}.dxf", _SECTION_LAYERS, section_entities))

    # A repeated run reuses the model output directory.  Do not remove any
    # prior deliverable until every result and section file for this run has
    # been written successfully; an exception above deliberately leaves the
    # previous complete set recoverable.
    if result_paths and len(result_paths) == len(section_paths) == len(by_story):
        _remove_stale_dxf_files(destination, "wpj-*.dxf", result_paths)
        _remove_stale_dxf_files(destination, "构件编号-*.dxf", section_paths)

    return BeamDxfExport(tuple(result_paths), tuple(section_paths), tuple(issues), tuple(summaries))


def _remove_stale_dxf_files(destination: Path, pattern: str, generated: list[Path]) -> None:
    """Remove only old same-family files after a complete successful export."""

    # Windows output directories are case-insensitive; compare names that
    # way so a story whose casing changed between runs is not deleted after
    # being freshly overwritten.
    generated_names = {path.name.casefold() for path in generated}
    for candidate in destination.glob(pattern):
        if candidate.is_file() and candidate.name.casefold() not in generated_names:
            candidate.unlink()


def _group_logical_stories(records: tuple[SpanScheduleRecord, ...]) -> dict[str, list[SpanScheduleRecord]]:
    """Group tower-qualified records only when the suffix is demonstrably shared.

    ETABS Tower models expose equivalent floors as ``T1-L1``, ``T2-L1`` and
    so on.  A lone ordinary story such as ``Wing-A`` must remain untouched;
    therefore a qualified name is folded to its suffix only when that suffix
    occurs under at least two distinct ``T<number>`` prefixes in this export.
    """

    qualified: dict[str, dict[str, list[SpanScheduleRecord]]] = {}
    ordinary: dict[str, list[SpanScheduleRecord]] = {}
    for record in records:
        story = str(record.span.story).strip()
        match = _TOWER_STORY.fullmatch(story)
        if match:
            suffix = match.group("story")
            tower = match.group("tower").upper()
            qualified.setdefault(suffix, {}).setdefault(tower, []).append(record)
        else:
            ordinary.setdefault(story, []).append(record)

    grouped: dict[str, list[SpanScheduleRecord]] = {}
    for suffix, by_tower in qualified.items():
        if len(by_tower) >= 2:
            grouped[suffix] = [record for tower in sorted(by_tower) for record in by_tower[tower]]
        else:
            for tower_records in by_tower.values():
                original_story = str(tower_records[0].span.story).strip()
                grouped[original_story] = list(tower_records)
    for story, story_records in ordinary.items():
        grouped.setdefault(story, []).extend(story_records)
    return {story: grouped[story] for story in sorted(grouped)}


def _result_summary_status(design: BeamDesignResult, code: str) -> str:
    if code.startswith("DESIGN_RESULT_"):
        return str(getattr(design.status, "name", design.status))
    return code


def _station_domain_error(
    record: SpanScheduleRecord,
    segment: _SpanSegment,
) -> tuple[str, str] | None:
    if segment.valid_domain_status == "OUTSIDE_VALID_STATION_DOMAIN":
        return (
            "OUTSIDE_VALID_STATION_DOMAIN",
            "该子段完全位于父跨有效原生站点域之外；不生成需求。",
        )
    domain = record.station_domain
    if domain is None:
        return None
    if domain.status != "AVAILABLE":
        return (
            "NO_VALID_STATION_DOMAIN",
            f"没有可构建设计长度的有效站点域：{domain.reason or domain.status}",
        )
    if domain.invalid_in_domain_count:
        return (
            "VALID_STATION_DOMAIN_SOURCE_STATION_INVALID",
            "有效站点域内保留了存在无效必需值或源设计错误的原始站点。",
        )
    if domain.unmapped_station_count:
        return (
            "VALID_STATION_DOMAIN_SOURCE_STATION_UNMAPPED",
            "存在无法投影到父跨的源站点；不宣称需求输入完整。",
        )
    return None


def _is_excluded_no_design(design: BeamDesignResult) -> bool:
    return is_explicit_no_design(design.status, design.status_message)


def _span_segments(
    record: SpanScheduleRecord,
    by_name: Mapping[str, BeamGeometry],
) -> tuple[_SpanSegment, ...]:
    """Split a Span at source-object section interfaces.

    ``BeamSpan.source_objects`` is already ordered along the actual line path
    by the topology adapter.  The source endpoints are projected onto that
    path so curved/rotated geometry remains the same geometry that is used by
    the other DXF exporter.  Adjacent objects with the same section are
    coalesced; a later object with the same section is deliberately not.
    """

    raw_points = tuple(record.geometry.path_points or record.span.path_points)
    span_length = _path_length(raw_points)
    if len(raw_points) < 2 or not isfinite(span_length) or span_length <= 0.0:
        section = str(record.geometry.section).strip()
        return (
            _SpanSegment(
                (),
                raw_points,
                0.0,
                max(0.0, span_length),
                section,
                ("INVALID_SPAN_PATH", "梁跨路径不足两个不同点，无法生成子段。"),
            ),
        )

    names = tuple(str(name) for name in record.span.source_objects if str(name).strip())
    if not names:
        section = str(record.geometry.section).strip()
        error = None if section else ("MISSING_SECTION", "梁跨没有可确定的真实截面名称。")
        return (_SpanSegment((), raw_points, 0.0, span_length, section, error),)

    intervals: list[_SourceInterval | None] = []
    for order, name in enumerate(names):
        source = by_name.get(name)
        if source is None:
            intervals.append(
                _SourceInterval(
                    order,
                    name,
                    0.0,
                    0.0,
                    "",
                    False,
                    ("MISSING_SECTION_OBJECT", f"源梁对象 {name} 缺少几何/截面信息。"),
                )
            )
            continue
        section = str(source.section).strip()
        source_path = tuple(source.path_points) or (source.start, source.end)
        positions = tuple(_path_position(raw_points, point) for point in source_path)
        if not positions or any(value is None for value in positions):
            intervals.append(
                _SourceInterval(
                    order,
                    name,
                    0.0,
                    0.0,
                    section,
                    False,
                    ("SOURCE_PATH_UNRESOLVED", f"源梁对象 {name} 无法定位到当前梁跨路径。"),
                )
            )
            continue
        low = max(0.0, min(span_length, min(float(value) for value in positions if value is not None)))
        high = max(0.0, min(span_length, max(float(value) for value in positions if value is not None)))
        section_error = None if section else ("MISSING_SECTION", f"源梁对象 {name} 没有真实截面名称。")
        intervals.append(_SourceInterval(order, name, low, high, section, True, section_error))

    resolved = [item for item in intervals if item is not None and item.geometry_resolved]
    if not resolved:
        # There is no defensible interface location.  Keep one auditable bad
        # subsegment rather than fabricating a first-section replacement.
        first_error = next(
            (item.section_error for item in intervals if item is not None and item.section_error),
            ("SOURCE_PATH_UNRESOLVED", "梁跨源对象无法定位到真实路径。"),
        )
        return (_SpanSegment(tuple(names), raw_points, 0.0, span_length, "", first_error),)

    # Infer only the interval of an unresolved object from its ordered
    # neighbours.  The segment remains ERR, but healthy neighbours can still
    # be rendered independently.
    for index, item in enumerate(intervals):
        if item is None or item.geometry_resolved:
            continue
        previous = next(
            (candidate for candidate in reversed(intervals[:index]) if candidate and candidate.geometry_resolved), None
        )
        following = next(
            (candidate for candidate in intervals[index + 1 :] if candidate and candidate.geometry_resolved), None
        )
        left = previous.end if previous else 0.0
        right = following.start if following else span_length
        unknown_count = sum(
            1
            for candidate in intervals
            if candidate is not None
            and not candidate.geometry_resolved
            and (previous is None or candidate.order > previous.order)
            and (following is None or candidate.order < following.order)
        )
        unknown_count = max(1, unknown_count)
        rank = sum(
            1
            for candidate in intervals[:index]
            if candidate is not None
            and not candidate.geometry_resolved
            and (previous is None or candidate.order > previous.order)
            and (following is None or candidate.order < following.order)
        )
        step = max(0.0, right - left) / unknown_count
        intervals[index] = replace(item, start=left + step * rank, end=left + step * (rank + 1))

    ordered = sorted(
        (item for item in intervals if item is not None), key=lambda item: (item.start, item.end, item.order)
    )
    grouped: list[list[_SourceInterval]] = []
    tolerance = 1.0
    for item in ordered:
        if (
            grouped
            and item.section
            and grouped[-1][0].section == item.section
            and all(member.section_error is None for member in grouped[-1])
            and item.section_error is None
            and abs(item.start - grouped[-1][-1].end) <= tolerance
        ):
            grouped[-1].append(item)
        else:
            grouped.append([item])

    segments: list[_SpanSegment] = []
    for members in grouped:
        start = max(0.0, min(span_length, min(item.start for item in members)))
        end = max(start, min(span_length, max(item.end for item in members)))
        points = _slice_path(raw_points, start, end)
        error = next((item.section_error for item in members if item.section_error), None)
        section = members[0].section if all(item.section == members[0].section for item in members) else ""
        segments.append(
            _SpanSegment(
                tuple(item.name for item in members),
                points,
                start,
                end,
                section,
                error,
            )
        )
    return tuple(segments)


def _object_part_segments(parts: tuple[SpanObjectPart, ...]) -> tuple[_SpanSegment, ...]:
    segments: list[_SpanSegment] = []
    for part in section_runs(parts):
        geometry = part.geometry
        if geometry is None:
            continue
        points = tuple(geometry.path_points) or (geometry.start, geometry.end)
        segments.append(
            _SpanSegment(
                geometry.source_names or (part.source_object,),
                points,
                part.parent_offset_mm,
                part.parent_offset_mm + _path_length(points),
                geometry.section,
                None,
                part.design,
                part.object_part_index,
                part.valid_domain_status,
            )
        )
    return tuple(segments)


def _segment_section_label(
    segment: _SpanSegment,
    section_loader: Callable[[str], Mapping[str, Any]],
    cache: dict[str, tuple[Mapping[str, Any] | None, str]],
) -> tuple[str | None, float | None, tuple[str, str] | None]:
    if segment.section_error:
        return None, None, segment.section_error
    section_name = segment.section.strip()
    if not section_name:
        return None, None, ("MISSING_SECTION", "梁子段没有真实截面名称。")
    if section_name not in cache:
        try:
            cache[section_name] = (section_loader(section_name), "")
        except Exception as exc:
            cache[section_name] = (None, str(exc))
    section, error = cache[section_name]
    if section is None:
        return None, None, ("SECTION_INFO_ERROR", f"{section_name}: {error}")
    if not isinstance(section, Mapping):
        return None, None, ("SECTION_INFO_ERROR", f"{section_name}: 截面信息不是键值对象。")
    try:
        width = float(_section_dimension(section, "width_mm", "width", "b"))
        depth = float(_section_dimension(section, "depth_mm", "depth", "h"))
    except (KeyError, TypeError, ValueError):
        return None, None, ("SECTION_DIMENSION_ERROR", f"{section_name}: 截面宽高不可解析。")
    if not all(isfinite(value) and value > 0 for value in (width, depth)):
        return None, None, ("SECTION_DIMENSION_ERROR", f"{section_name}: 截面宽高必须为正有限值。")
    return f"{_format_dimension(width)}*{_format_dimension(depth)}", depth, None


def _segment_record(record: SpanScheduleRecord, segment: _SpanSegment, span_id: str) -> SpanScheduleRecord:
    design = segment.design or _segment_design(record, segment, span_id)
    span = replace(record.span, source_objects=segment.source_objects, path_points=segment.path_points)
    geometry = replace(
        record.geometry,
        name=span_id,
        label=span_id,
        start=segment.path_points[0] if segment.path_points else record.geometry.start,
        end=segment.path_points[-1] if segment.path_points else record.geometry.end,
        section=segment.section,
        depth=None,
        source_names=segment.source_objects,
        path_points=segment.path_points,
    )
    return SpanScheduleRecord(span, geometry, design)


def _segment_design(record: SpanScheduleRecord, segment: _SpanSegment, span_id: str) -> BeamDesignResult:
    segment_length = max(0.0, segment.end - segment.start)
    stations: list[DesignStation] = []
    source_names = set(segment.source_objects)
    for station in record.design.stations:
        try:
            location = float(station.location)
        except (TypeError, ValueError):
            continue
        if location < segment.start - 1.0 or location > segment.end + 1.0:
            continue
        source_object = str(station.source_object).strip()
        if source_object:
            if source_object not in source_names:
                continue
        elif source_names and len(source_names) > 1:
            # Legacy/offline records may not carry SourceObject per station;
            # location is the only safe fallback when the interface is known.
            pass
        stations.append(replace(station, location=max(0.0, min(segment_length, location - segment.start))))

    original_status = record.design.status
    if stations:
        assigned_sources = {
            str(station.source_object).strip() for station in stations if str(station.source_object).strip()
        }
        source_coverage_missing = (
            bool(source_names) and bool(assigned_sources) and not source_names.issubset(assigned_sources)
        )
        if source_coverage_missing or (
            original_status == DesignResultStatus.PARTIAL and len(source_names) > 1 and not assigned_sources
        ):
            status = DesignResultStatus.PARTIAL
            status_message = record.design.status_message or "梁子段缺少部分源梁对象的设计站点。"
        else:
            status = DesignResultStatus.AVAILABLE
            status_message = ""
    elif original_status in {
        DesignResultStatus.API_ERROR,
        DesignResultStatus.NO_RESULTS,
        DesignResultStatus.SKIPPED,
    }:
        status = original_status
        status_message = record.design.status_message
    else:
        status = DesignResultStatus.NO_RESULTS
        status_message = "梁子段没有属于其源梁对象的设计站点。"
    units = tuple(dict.fromkeys(station.units for station in stations if station.units))
    normalized_units = units[0] if len(units) == 1 else ("MIXED" if units else record.design.units)
    return replace(
        record.design,
        beam_name=span_id,
        stations=tuple(stations),
        status=status,
        status_message=status_message,
        source_objects=segment.source_objects or record.design.source_objects,
        units=normalized_units,
    )


def _path_length(points: tuple[Point3D, ...]) -> float:
    return sum(distance(left, right) for left, right in zip(points, points[1:]))


def _path_position(points: tuple[Point3D, ...], point: Point3D) -> float | None:
    position = 0.0
    best: float | None = None
    best_gap = float("inf")
    for left, right in zip(points, points[1:]):
        segment_length = distance(left, right)
        if segment_length <= 0.0:
            continue
        vector = tuple(right[index] - left[index] for index in range(3))
        ratio = sum((point[index] - left[index]) * vector[index] for index in range(3)) / segment_length**2
        ratio = max(0.0, min(1.0, ratio))
        projected = tuple(left[index] + vector[index] * ratio for index in range(3))
        gap = distance(projected, point)
        if gap < best_gap:
            best_gap = gap
            best = position + ratio * segment_length
        position += segment_length
    return best


def _slice_path(points: tuple[Point3D, ...], start: float, end: float) -> tuple[Point3D, ...]:
    if len(points) < 2:
        return points
    total = _path_length(points)
    if total <= 0.0:
        return (points[0], points[-1])
    low = max(0.0, min(total, start))
    high = max(low, min(total, end))
    values: list[Point3D] = [_point_at_distance(points, low)]
    cumulative = 0.0
    for left, right in zip(points, points[1:]):
        segment_length = distance(left, right)
        next_cumulative = cumulative + segment_length
        if segment_length > 0.0 and low < next_cumulative < high:
            values.append(right)
        cumulative = next_cumulative
    values.append(_point_at_distance(points, high))
    deduped: list[Point3D] = []
    for point in values:
        if not deduped or distance(deduped[-1], point) > 1e-7:
            deduped.append(point)
    return tuple(deduped)


def _point_at_distance(points: tuple[Point3D, ...], target: float) -> Point3D:
    remaining = max(0.0, target)
    for left, right in zip(points, points[1:]):
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


def _section_dimension(section: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in section:
            return section[name]
    raise KeyError(names[0])


def _result_labels(
    record: SpanScheduleRecord,
    depth_mm: float | None,
    *,
    detailing_policy_id: str = LEGACY_DETAILING_POLICY_ID,
    parent_length_mm: float | None = None,
    segment_start_mm: float = 0.0,
    parent_end_depths_mm: tuple[float, float] | None = None,
) -> tuple[tuple[str, str, str, str], tuple[str, str] | None]:
    design = record.design
    if _is_excluded_no_design(design):
        return ("", "", "", ""), None
    if design.status != DesignResultStatus.AVAILABLE:
        status_name = str(getattr(design.status, "name", design.status))
        status_value = str(getattr(design.status, "value", design.status))
        return ("", "", "", ""), (
            f"DESIGN_RESULT_{status_name}",
            design.status_message or status_value,
        )
    if depth_mm is None:
        return ("", "", "", ""), ("SECTION_DIMENSION_ERROR", "缺少真实梁高，无法划分 2h 区域。")
    if design.units != "N_mm_C" or any(station.units != "N_mm_C" for station in design.stations):
        return ("", "", "", ""), ("DESIGN_UNITS_UNSUPPORTED", "DXF 结果要求 N_mm_C 设计单位。")
    station_errors = tuple(str(station.error).strip() for station in design.stations if str(station.error).strip())
    if station_errors:
        return ("", "", "", ""), (
            "DESIGN_STATION_ERROR",
            "; ".join(dict.fromkeys(station_errors)),
        )
    try:
        length = float(record.geometry.length)
    except (TypeError, ValueError):
        length = float("nan")
    if not isfinite(length) or length <= 0.0:
        return ("", "", "", ""), ("INVALID_SPAN_LENGTH", "梁跨长度必须为正有限值。")
    for station in design.stations:
        if not _valid_station(station, length):
            return ("", "", "", ""), (
                "INVALID_DESIGN_VALUE",
                "设计站点位置或面积包含负值、非有限值或超出梁跨。",
            )
    controls = build_region_controls(
        design,
        record.geometry.length,
        depth_mm,
        detailing_policy_id=detailing_policy_id,
        parent_length_mm=parent_length_mm,
        segment_start_mm=segment_start_mm,
        parent_end_depths_mm=parent_end_depths_mm,
        projection_mode=(
            DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE
            if detailing_policy_id == V3_DETAILING_POLICY_ID
            else "APPROVED_REGION_CONTROLS"
        ),
    )
    if detailing_policy_id == V3_DETAILING_POLICY_ID:
        return _result_labels_from_region_controls(controls, record.geometry.length, depth_mm)
    groups = group_design_stations(design, record.geometry.length, depth_mm,
        parent_length_mm=parent_length_mm, segment_start_mm=segment_start_mm,
        parent_end_depths_mm=parent_end_depths_mm)
    if controls is None or groups is None:
        return ("", "", "", ""), ("NO_DESIGN_STATIONS", "梁跨没有可用设计站点。")
    if any(not groups[name] for name in ("left", "middle", "right")):
        return ("", "", "", ""), ("NO_DESIGN_STATIONS", "梁跨某个计算区域没有可用设计站点。")
    if detailing_policy_id in {DRAWING_DETAILING_POLICY_ID, V3_DETAILING_POLICY_ID}:
        invalid_reason = str(controls["middle"].get("middle_top_window_invalid_reason", "")).strip()
        if invalid_reason:
            return ("", "", "", ""), (
                "DRAWING_MIDDLE_TOP_DEMAND_INVALID",
                invalid_reason,
            )
        if controls["middle"].get("middle_top_window_station_count") == 0:
            return ("", "", "", ""), (
                "DRAWING_MIDDLE_QUARTER_NO_STATIONS",
                "中央四分之一顶筋需求窗口没有原始设计站点。",
            )

    top = tuple(
        float(
            controls[name].get("top_demand_mm2", controls[name]["top_total_mm2"])
            if name == "middle"
            else controls[name]["top_total_mm2"]
        )
        / 100.0
        for name in ("left", "middle", "right")
    )
    bottom = tuple(
        float(controls[name]["bottom_total_mm2"]) / 100.0 for name in ("left", "middle", "right")
    )
    end_stations = (*groups["left"], *groups["right"])
    asv = max(_combined_transverse(station) for station in end_stations) * 10.0
    has_middle_zone = length > 4.0 * depth_mm
    asv0 = max(_combined_transverse(station) for station in groups["middle"]) * 10.0 if has_middle_zone else 0.0
    ast = max(station.torsion_longitudinal_area for station in design.stations) / 100.0
    ast1 = max(station.torsion_trans_area for station in design.stations) * 10.0
    values = (*top, *bottom, asv, asv0, ast, ast1)
    if not all(isfinite(value) for value in values):
        return ("", "", "", ""), ("NON_FINITE_DESIGN_VALUE", "设计结果包含非有限面积值。")

    labels = (
        f"G{_ceil_text(asv, 0.1)}-{_ceil_text(asv0, 0.1)}",
        "-".join(_ceil_text(value, 1.0) for value in top),
        "-".join(_ceil_text(value, 1.0) for value in bottom),
        f"VT{_ceil_text(ast, 0.1)}-{_ceil_text(ast1, 0.1)}",
    )
    return labels, None


def _result_labels_from_region_controls(
    region_controls: Mapping[str, Mapping[str, Any] | None] | None,
    length_mm: float,
    depth_mm: float | None,
) -> tuple[tuple[str, str, str, str], tuple[str, str] | None]:
    """Render labels only from an explicitly supplied frozen row control view."""

    error_labels = ("", "", "", "")
    if not isinstance(region_controls, Mapping):
        return error_labels, ("FROZEN_CONTROL_UNKNOWN", "当前行没有可用的冻结需求控制。")
    if depth_mm is None or not isfinite(float(depth_mm)) or float(depth_mm) <= 0.0:
        return error_labels, ("SECTION_DIMENSION_ERROR", "缺少真实梁高，无法划分 2h 区域。")
    try:
        length = float(length_mm)
    except (TypeError, ValueError):
        length = float("nan")
    if not isfinite(length) or length <= 0.0:
        return error_labels, ("INVALID_SPAN_LENGTH", "梁跨长度必须为正有限值。")

    controls: dict[str, Mapping[str, Any]] = {}
    for name in ("left", "middle", "right"):
        value = region_controls.get(name)
        if not isinstance(value, Mapping):
            return error_labels, ("FROZEN_CONTROL_UNKNOWN", f"冻结需求控制缺少 {name} 区。")
        controls[name] = value
        if str(value.get("design_errors", "")).strip():
            return error_labels, ("FROZEN_CONTROL_INVALID", f"{name} 区冻结需求包含设计错误。")

    if str(controls["middle"].get("middle_top_window_invalid_reason", "")).strip():
        return error_labels, (
            "DRAWING_MIDDLE_TOP_DEMAND_INVALID",
            str(controls["middle"]["middle_top_window_invalid_reason"]),
        )
    if controls["middle"].get("middle_top_window_station_count") == 0:
        return error_labels, ("DRAWING_MIDDLE_QUARTER_NO_STATIONS", "中央顶筋需求窗口没有原始设计站点。")

    def number(region: str, field: str) -> float | None:
        try:
            value = float(controls[region][field])
        except (KeyError, TypeError, ValueError):
            return None
        if not isfinite(value) or value < 0.0:
            return None
        return value

    top_values: list[float] = []
    bottom_values: list[float] = []
    for region, field in (("left", "top_total_mm2"), ("middle", "top_demand_mm2"), ("right", "top_total_mm2")):
        value = number(region, field)
        if value is None:
            return error_labels, ("FROZEN_CONTROL_UNKNOWN", f"{region}.{field} 冻结需求未知。")
        top_values.append(value / 100.0)
    for region in ("left", "middle", "right"):
        value = number(region, "bottom_total_mm2")
        if value is None:
            return error_labels, ("FROZEN_CONTROL_UNKNOWN", f"{region}.bottom_total_mm2 冻结需求未知。")
        bottom_values.append(value / 100.0)

    transverse: dict[str, float] = {}
    for region in ("left", "middle", "right"):
        av = number(region, "av_over_s")
        at = number(region, "at_over_s")
        if av is None or at is None:
            return error_labels, ("FROZEN_CONTROL_UNKNOWN", f"{region} 区 Av/At 冻结需求未知。")
        transverse[region] = av + 2.0 * at
    al_values = [number(region, "al_mm2") for region in ("left", "middle", "right")]
    at_values = [number(region, "at_over_s") for region in ("left", "middle", "right")]
    if any(value is None for value in (*al_values, *at_values)):
        return error_labels, ("FROZEN_CONTROL_UNKNOWN", "冻结扭转需求未知。")

    asv = max(transverse["left"], transverse["right"]) * 10.0
    asv0 = transverse["middle"] * 10.0 if length > 4.0 * float(depth_mm) else 0.0
    ast = max(value for value in al_values if value is not None) / 100.0
    ast1 = max(value for value in at_values if value is not None) * 10.0
    labels = (
        f"G{_ceil_text(asv, 0.1)}-{_ceil_text(asv0, 0.1)}",
        "-".join(_ceil_text(value, 1.0) for value in top_values),
        "-".join(_ceil_text(value, 1.0) for value in bottom_values),
        f"VT{_ceil_text(ast, 0.1)}-{_ceil_text(ast1, 0.1)}",
    )
    return labels, None


def _valid_station(station: DesignStation, length: float) -> bool:
    try:
        location = float(station.location)
        if not isfinite(location) or location < -1e-6 or location > length + 1e-6:
            return False
        values = (
            station.as_total_top_mm2,
            station.as_total_bottom_mm2,
            station.shear_area,
            station.torsion_longitudinal_area,
            station.torsion_trans_area,
        )
        return all(isfinite(float(value)) and float(value) >= 0.0 for value in values)
    except (TypeError, ValueError):
        return False


def _combined_transverse(station: DesignStation) -> float:
    return station.shear_area + 2.0 * station.torsion_trans_area


def _append_result_annotation(
    entities: list[str],
    points: tuple[Point3D, ...],
    labels: tuple[str, str, str, str],
    text_height: float,
    *,
    error: bool,
) -> None:
    layer = "BEAM_RESULT_ERROR" if error else "BEAM_RESULT_LINE"
    text_layer = "dsptext_beam"
    color = 1 if error else 7
    entities.extend(_polyline(layer, color, points))
    midpoint, angle, normal = _layout(points)
    for value, offset in zip(labels, (2.25, 1.0, -1.0, -2.25)):
        entities.extend(
            _centered_text(
                text_layer,
                color,
                _offset(midpoint, normal, offset * text_height),
                value,
                text_height,
                angle,
            )
        )


def _append_section_annotation(
    entities: list[str],
    points: tuple[Point3D, ...],
    label: str,
    text_height: float,
    *,
    error: bool,
) -> None:
    layer = "BEAM_SECTION_ERROR" if error else "BEAM_SECTION_LINE"
    text_layer = "dsptext_beam"
    color = 1 if error else 7
    entities.extend(_polyline(layer, color, points))
    midpoint, angle, normal = _layout(points)
    entities.extend(
        _centered_text(
            text_layer,
            color,
            _offset(midpoint, normal, -text_height),
            label,
            text_height,
            angle,
        )
    )


def _layout(points: tuple[Point3D, ...]) -> tuple[Point3D, float, tuple[float, float]]:
    midpoint = _midpoint(points)
    angle = _text_angle(points)
    theta = radians(angle)
    return midpoint, angle, (-sin(theta), cos(theta))


def _centered_text(
    layer: str,
    color: int,
    point: Point3D,
    value: str,
    height: float,
    angle: float,
) -> list[str]:
    return [
        "0",
        "TEXT",
        "8",
        layer,
        "62",
        str(color),
        "10",
        f"{point[0]:.3f}",
        "20",
        f"{point[1]:.3f}",
        "30",
        f"{point[2]:.3f}",
        "40",
        f"{height:.3f}",
        "1",
        _safe_text(value),
        "50",
        f"{angle:.3f}",
        "7",
        "ARIAL",
        "72",
        "1",
        "73",
        "2",
        "11",
        f"{point[0]:.3f}",
        "21",
        f"{point[1]:.3f}",
        "31",
        f"{point[2]:.3f}",
    ]


def _write_dxf(path: Path, layers: tuple[tuple[str, int], ...], entities: list[str]) -> Path:
    values = _header() + _tables(layers) + ["0", "SECTION", "2", "ENTITIES"] + entities
    values.extend(["0", "ENDSEC", "0", "EOF"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(values) + "\n", encoding="ascii")
    return path


def _flatten(points: tuple[Point3D, ...]) -> tuple[Point3D, ...]:
    return tuple((float(point[0]), float(point[1]), 0.0) for point in points)


def _offset(point: Point3D, normal: tuple[float, float], distance: float) -> Point3D:
    return (point[0] + normal[0] * distance, point[1] + normal[1] * distance, 0.0)


def _ceil_text(value: float, step: float) -> str:
    value = max(0.0, value)
    rounded = ceil(value / step - 1e-12) * step
    if step == 1.0:
        return str(int(round(rounded)))
    return f"{rounded:.1f}".rstrip("0").rstrip(".")


def _format_dimension(value: float) -> str:
    rounded = round(value, 3)
    if abs(rounded - round(rounded)) <= 1e-6:
        return str(int(round(rounded)))
    return f"{rounded:.3f}".rstrip("0").rstrip(".")


def _unique_story_name(story: str, used: set[str]) -> str:
    base = _INVALID_FILENAME.sub("_", str(story)).strip(" .") or "story"
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate
