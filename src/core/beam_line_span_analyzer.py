"""Identify drawing beam lines and structural spans from ETABS beam geometry.

This module deliberately sits above :mod:`beam_merger`: Beam Object fragments
are first used to form candidate paths, then structural supports decide spans.
It does not read ETABS or design results and is therefore straightforward to
test with small coordinate-only models.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable, Mapping

from src.core.beam_merger import BeamMerger
from src.core.geometry_extractor import (
    BeamGeometry,
    RigidLinkGeometry,
    relevant_anchored_rigid_links,
)
from src.design.provider import BeamDesignResult, DesignResultStatus
from src.utils.geometry import Point3D, distance, plan_angle_deg


@dataclass(frozen=True)
class BeamLineSpanSettings:
    coordinate_tolerance_mm: float = 1.0
    source_span_overlap_tolerance_mm: float = 2.0
    merge_angle_deg: float = 15.0
    through_support_angle_deg: float = 3.0
    intersection_grid_mm: float = 5000.0
    intersection_z_tolerance_mm: float = 1.0
    story_workers: int = 1


@dataclass(frozen=True)
class TopologyDiagnostic:
    """Machine-readable explanation of a non-trivial topology decision."""

    code: str
    severity: str
    story: str
    source_objects: tuple[str, ...]
    message: str
    candidates: tuple[tuple[str, ...], ...] = ()

    def as_record(self) -> str:
        objects = ",".join(self.source_objects)
        return f"{self.severity}/{self.code}: {self.message} [{objects}]"


@dataclass(frozen=True)
class BeamLine:
    story: str
    beam_type: str  # PB or SB
    line_number: int
    section: str
    source_objects: tuple[str, ...]
    path_points: tuple[Point3D, ...]
    is_curved: bool
    path_parts: tuple[tuple[Point3D, ...], ...] = ()

    @property
    def beam_line_id(self) -> str:
        return f"{self.story}-{self.beam_type}{self.line_number}"


@dataclass(frozen=True)
class BeamSpan:
    story: str
    beam_type: str
    line_number: int
    span_index: int
    source_objects: tuple[str, ...]
    path_points: tuple[Point3D, ...]
    is_curved: bool
    is_cantilever: bool

    @property
    def beam_line_id(self) -> str:
        return f"{self.story}-{self.beam_type}{self.line_number}"

    @property
    def span_id(self) -> str:
        return f"{self.beam_line_id}-S{self.span_index}"

    @property
    def i_point(self) -> Point3D:
        return self.path_points[0]

    @property
    def j_point(self) -> Point3D:
        return self.path_points[-1]


@dataclass(frozen=True)
class BeamLineSpanResult:
    lines: tuple[BeamLine, ...]
    spans: tuple[BeamSpan, ...]
    records: tuple[str, ...]
    diagnostics: tuple[TopologyDiagnostic, ...] = ()
    rigid_links: tuple[RigidLinkGeometry, ...] = ()


@dataclass(frozen=True)
class _UnnumberedLine:
    story: str
    beam_type: str
    section: str
    source_objects: tuple[str, ...]
    span_source_objects: tuple[tuple[str, ...], ...]
    path_points: tuple[Point3D, ...]
    span_paths: tuple[tuple[Point3D, ...], ...]
    support_points: tuple[Point3D, ...]
    is_curved: bool
    path_parts: tuple[tuple[Point3D, ...], ...] = ()
    start_point_name: str = ""
    end_point_name: str = ""


def analyze_beam_lines(
    beams: list[BeamGeometry],
    column_point_names: set[str] | None = None,
    settings: BeamLineSpanSettings | None = None,
    column_support_coordinates: Mapping[str, Point3D] | None = None,
    column_anchor_names: set[str] | None = None,
    rigid_links: Iterable[RigidLinkGeometry] = (),
) -> BeamLineSpanResult:
    """Return numbered Beam Lines and support-defined Spans.

    ``column_point_names`` contains ETABS points where a lower column segment
    supports the beam, plus any rigid-end equivalent points. A candidate
    touching any such point is a primary line. Secondary paths are split only
    at intersections with already identified primary paths.
    """
    settings = settings or BeamLineSpanSettings()
    column_point_names = column_point_names or set()
    column_support_coordinates = column_support_coordinates or {}
    column_anchor_names = column_anchor_names or set()
    rigid_links = relevant_anchored_rigid_links(
        rigid_links,
        (point for beam in beams for point in (beam.point_i, beam.point_j)),
        column_anchor_names,
    )
    by_story: dict[str, list[BeamGeometry]] = {}
    for beam in beams:
        by_story.setdefault(beam.story, []).append(beam)
    story_inputs = [
        (
            story_beams,
            column_point_names,
            column_support_coordinates,
            settings,
            tuple(link for link in rigid_links if link.story == story),
            column_anchor_names,
        )
        for story, story_beams in by_story.items()
    ]
    if settings.story_workers > 1 and len(story_inputs) > 1:
        with ThreadPoolExecutor(max_workers=settings.story_workers) as executor:
            story_results = list(executor.map(_analyze_story, story_inputs))
    else:
        story_results = [_analyze_story(item) for item in story_inputs]
    unnumbered_lines = [line for lines, _, _ in story_results for line in lines]
    records = [record for _, story_records, _ in story_results for record in story_records]
    diagnostics = [item for _, _, story_diagnostics in story_results for item in story_diagnostics]
    numbered_lines, spans = _number_lines(unnumbered_lines, settings)
    coverage_diagnostics = _source_coverage_diagnostics(beams, spans)
    diagnostics.extend(coverage_diagnostics)
    records.extend(item.as_record() for item in coverage_diagnostics)
    records.extend(_validation_records(numbered_lines, spans, settings))
    return BeamLineSpanResult(tuple(numbered_lines), tuple(spans), tuple(records), tuple(diagnostics), tuple(rigid_links))


def _analyze_story(
    input_data: tuple[
        list[BeamGeometry],
        set[str],
        Mapping[str, Point3D],
        BeamLineSpanSettings,
        tuple[RigidLinkGeometry, ...],
        set[str],
    ],
) -> tuple[list[_UnnumberedLine], list[str], list[TopologyDiagnostic]]:
    """Analyze one story; this function is isolated so stories can run in parallel."""
    beams, column_point_names, column_support_coordinates, settings, rigid_links, column_anchor_names = input_data
    column_points = _column_coordinates(
        beams,
        column_point_names,
        settings.coordinate_tolerance_mm,
        column_support_coordinates,
    )
    designs = {beam.name: BeamDesignResult(beam.name, status=DesignResultStatus.SKIPPED) for beam in beams}
    merge_result = BeamMerger(angle_limit_deg=settings.merge_angle_deg).merge(beams, designs)
    candidates, stitch_records, stitch_diagnostics = _stitch_candidates(merge_result.beams, settings)
    records = [
        f"{record.severity}/{record.category}: {record.message} [{record.beam_names}]"
        for record in merge_result.records
    ]
    records.extend(stitch_records)
    source_by_name = {beam.name: beam for beam in beams}
    seed_primary_indices = {
        index
        for index, candidate in enumerate(candidates)
        if _candidate_is_primary(candidate, source_by_name, column_point_names, column_points, settings)
    }
    anchor_primary_indices = {
        index
        for index in seed_primary_indices
        if _candidate_column_count(candidates[index], column_points, settings) >= 2
    }
    if not anchor_primary_indices:
        anchor_primary_indices = set(seed_primary_indices)
    # Every column-connected candidate is a potential main-beam support for a
    # neighboring path.  Do not restrict this index to multi-column anchors:
    # a long candidate itself may need to change from PB to SB at an interior
    # main-beam intersection (for example GF-GRD-F-PB17).
    primary_paths = tuple(
        _oriented_path(candidates[index].path_points or (candidates[index].start, candidates[index].end))
        for index in sorted(seed_primary_indices)
    )
    primary_index = _build_segment_index(primary_paths, settings.intersection_grid_mm)
    final_lines = _split_candidates_by_role(
        candidates,
        seed_primary_indices,
        anchor_primary_indices,
        column_points,
        primary_index,
        source_by_name,
        settings,
    )
    # A one-column candidate can contribute a PB portion before it changes to
    # SB. That PB portion is itself a valid support for other secondary beams.
    # Refine to a fixed point: a single second pass can leave an SB split at a
    # preliminary PB path that disappeared during role reclassification.
    support_paths = primary_paths
    support_signature = _support_path_signature(support_paths, settings.coordinate_tolerance_mm)
    seen_support_signatures: set[tuple[tuple[tuple[int, int, int], ...], ...]] = set()
    refinement_diagnostics: list[TopologyDiagnostic] = []
    for refinement in range(1, 7):
        resolved_primary_paths = tuple(line.path_points for line in final_lines if line.beam_type == "PB")
        resolved_signature = _support_path_signature(resolved_primary_paths, settings.coordinate_tolerance_mm)
        if resolved_signature == support_signature:
            if refinement > 1:
                refinement_diagnostics.append(
                    TopologyDiagnostic(
                        "ROLE_REFINEMENT_CONVERGED",
                        "INFO",
                        candidates[0].story if candidates else "",
                        tuple(
                            name for candidate in candidates for name in (candidate.source_names or (candidate.name,))
                        ),
                        f"PB/SB 支承角色在第 {refinement} 轮达到稳定。",
                    )
                )
            break
        if resolved_signature in seen_support_signatures:
            refinement_diagnostics.append(
                TopologyDiagnostic(
                    "ROLE_REFINEMENT_NOT_CONVERGED",
                    "BLOCKED",
                    candidates[0].story if candidates else "",
                    tuple(name for candidate in candidates for name in (candidate.source_names or (candidate.name,))),
                    "PB/SB 支承角色出现循环，未达到唯一稳定拓扑。",
                )
            )
            break
        seen_support_signatures.add(support_signature)
        support_paths = resolved_primary_paths
        support_signature = resolved_signature
        expanded_index = _build_segment_index(support_paths, settings.intersection_grid_mm)
        final_lines = _split_candidates_by_role(
            candidates,
            seed_primary_indices,
            anchor_primary_indices,
            column_points,
            expanded_index,
            source_by_name,
            settings,
            seed_intersection_index=primary_index,
        )
    else:
        refinement_diagnostics.append(
            TopologyDiagnostic(
                "ROLE_REFINEMENT_NOT_CONVERGED",
                "BLOCKED",
                candidates[0].story if candidates else "",
                tuple(name for candidate in candidates for name in (candidate.source_names or (candidate.name,))),
                "PB/SB 支承角色在 6 轮内未达到唯一稳定拓扑。",
            )
        )
    support_diagnostics = _support_boundary_diagnostics(
        final_lines,
        column_points,
        tuple(line.path_points for line in final_lines if line.beam_type == "PB"),
        settings,
    )
    rigid_stitched_lines, rigid_stitch_diagnostics, rigid_stitch_records = _join_pb_lines_across_rigid_links(
        final_lines,
        rigid_links,
        column_anchor_names,
        settings,
    )
    records.extend(item.as_record() for item in (*refinement_diagnostics, *support_diagnostics, *rigid_stitch_diagnostics))
    records.extend(rigid_stitch_records)
    return (
        rigid_stitched_lines,
        records,
        [
            *stitch_diagnostics,
            *refinement_diagnostics,
            *support_diagnostics,
            *rigid_stitch_diagnostics,
        ],
    )


def _stitch_candidates(
    candidates: list[BeamGeometry], settings: BeamLineSpanSettings
) -> tuple[list[BeamGeometry], list[str], list[TopologyDiagnostic]]:
    """Join candidate paths that are one geometric Beam Line across a support.

    ``BeamMerger`` intentionally keeps section changes separate because design
    data is section-sensitive. Drawing identity is different: a continuous
    primary/secondary line may change section at a column or other support and
    must still receive one PB/SB number with multiple spans. This second-stage
    graph uses endpoint proximity and local tangent continuity, while refusing
    to join a curved candidate to a straight candidate (curved primary lines
    have an independent drawing identity).
    """
    if len(candidates) < 2:
        return candidates, [], []

    endpoint_links: dict[tuple[int, int], tuple[int, int]] = {}
    pair_candidates: list[tuple[float, float, int, int, int, int]] = []
    for left_index, left in enumerate(candidates):
        for right_index in range(left_index + 1, len(candidates)):
            right = candidates[right_index]
            if left.story != right.story:
                continue
            if _candidate_curvature(left, settings) != _candidate_curvature(right, settings):
                continue
            for left_endpoint in (0, 1):
                for right_endpoint in (0, 1):
                    gap = distance(_candidate_endpoint(left, left_endpoint), _candidate_endpoint(right, right_endpoint))
                    if gap > settings.coordinate_tolerance_mm:
                        continue
                    angle = plan_angle_deg(
                        *_candidate_tangent(left, left_endpoint),
                        *_candidate_tangent(right, right_endpoint),
                    )
                    if angle <= settings.merge_angle_deg:
                        pair_candidates.append((angle, gap, left_index, left_endpoint, right_index, right_endpoint))

    diagnostics: list[TopologyDiagnostic] = []
    alternatives_by_endpoint: dict[tuple[int, int], list[tuple[float, float, int, int]]] = {}
    for angle, gap, left_index, left_endpoint, right_index, right_endpoint in pair_candidates:
        alternatives_by_endpoint.setdefault((left_index, left_endpoint), []).append(
            (angle, gap, right_index, right_endpoint)
        )
        alternatives_by_endpoint.setdefault((right_index, right_endpoint), []).append(
            (angle, gap, left_index, left_endpoint)
        )
    ambiguous_endpoints = {
        endpoint
        for endpoint, alternatives in alternatives_by_endpoint.items()
        if len({(item[2], item[3]) for item in alternatives}) > 1
    }
    for candidate_index, endpoint in sorted(ambiguous_endpoints):
        candidate = candidates[candidate_index]
        alternatives = sorted(alternatives_by_endpoint[(candidate_index, endpoint)])
        candidate_groups = tuple(
            tuple(candidates[other_index].source_names or (candidates[other_index].name,))
            for _, _, other_index, _ in alternatives
        )
        diagnostics.append(
            TopologyDiagnostic(
                code="STITCH_MULTIPLE_CANDIDATES",
                severity="BLOCKED",
                story=candidate.story,
                source_objects=tuple(candidate.source_names or (candidate.name,)),
                message=("15° 梁线串接端点存在多个近似共线候选；已 fail-closed，" "不自动选择延续路径。"),
                candidates=candidate_groups,
            )
        )

    # At a support there can be several objects meeting at one point. Consume
    # each physical endpoint once. Ambiguous endpoints are deliberately left
    # unlinked; selecting the smallest angle there would silently choose one
    # branch and make later numbering look authoritative when it is not.
    pair_candidates.sort()
    for _, _, left_index, left_endpoint, right_index, right_endpoint in pair_candidates:
        left_key = (left_index, left_endpoint)
        right_key = (right_index, right_endpoint)
        if left_key in ambiguous_endpoints or right_key in ambiguous_endpoints:
            continue
        if left_key in endpoint_links or right_key in endpoint_links:
            continue
        endpoint_links[left_key] = right_key
        endpoint_links[right_key] = left_key

    components: list[list[tuple[int, bool]]] = []
    visited: set[int] = set()
    for index, candidate in enumerate(candidates):
        if index in visited:
            continue
        component_indices = _candidate_component(index, endpoint_links)
        visited.update(component_indices)
        if len(component_indices) == 1:
            components.append([(index, False)])
            continue
        ordered = _order_candidate_component(component_indices, endpoint_links)
        if ordered is None:
            # A cycle is not a drawable open Beam Line. Preserve its candidates
            # and emit an explicit machine-readable blocking diagnostic.
            member_names = tuple(
                name
                for member in component_indices
                for name in (candidates[member].source_names or (candidates[member].name,))
            )
            diagnostics.append(
                TopologyDiagnostic(
                    code="STITCH_CYCLE_OR_BRANCH",
                    severity="BLOCKED",
                    story=candidates[component_indices[0]].story,
                    source_objects=member_names,
                    message="梁线串接图不是唯一开链（闭环或分叉）；已保留独立候选。",
                )
            )
            components.extend([[(member, False)] for member in component_indices])
        else:
            components.append(ordered)

    stitched: list[BeamGeometry] = []
    records: list[str] = []
    for ordered in components:
        if len(ordered) == 1:
            stitched.append(candidates[ordered[0][0]])
            continue
        merged = _stitch_geometry([candidates[index] for index, _ in ordered], [reverse for _, reverse in ordered])
        stitched.append(merged)
        if len(ordered) >= 4:
            diagnostics.append(
                TopologyDiagnostic(
                    code="STITCH_LONG_CHAIN",
                    severity="INFO",
                    story=merged.story,
                    source_objects=tuple(merged.source_names),
                    message=f"15° 梁线由 {len(ordered)} 个候选路径串接，已保留完整审计链。",
                )
            )
        sections = tuple(
            dict.fromkeys(
                candidate.section for candidate, _ in zip([candidates[index] for index, _ in ordered], ordered)
            )
        )
        if len(sections) > 1:
            records.append(
                "信息/梁线串接：{} [{}]；跨截面变化保留为同一 Beam Line。".format(
                    ",".join(merged.source_names), " / ".join(sections)
                )
            )
    records.extend(diagnostic.as_record() for diagnostic in diagnostics)
    return stitched, records, diagnostics


def _candidate_component(index: int, links: dict[tuple[int, int], tuple[int, int]]) -> list[int]:
    component: list[int] = []
    stack = [index]
    while stack:
        current = stack.pop()
        if current in component:
            continue
        component.append(current)
        for endpoint in (0, 1):
            link = links.get((current, endpoint))
            if link is not None and link[0] not in component:
                stack.append(link[0])
    return component


def _order_candidate_component(
    component: list[int], links: dict[tuple[int, int], tuple[int, int]]
) -> list[tuple[int, bool]] | None:
    endpoints = [(index, endpoint) for index in component for endpoint in (0, 1) if (index, endpoint) not in links]
    if len(endpoints) != 2:
        return None
    start_index, start_endpoint = endpoints[0]
    ordered: list[tuple[int, bool]] = []
    previous: tuple[int, int] | None = None
    current_index, incoming_endpoint = start_index, start_endpoint
    while True:
        reverse = incoming_endpoint == 1
        ordered.append((current_index, reverse))
        outgoing_endpoint = 1 - incoming_endpoint
        link = links.get((current_index, outgoing_endpoint))
        if link is None:
            break
        if previous == link or link[0] in {index for index, _ in ordered}:
            return None
        previous = (current_index, outgoing_endpoint)
        current_index, incoming_endpoint = link
    return ordered if len(ordered) == len(component) else None


def _candidate_endpoint(candidate: BeamGeometry, endpoint: int) -> Point3D:
    path = candidate.path_points or (candidate.start, candidate.end)
    return path[0] if endpoint == 0 else path[-1]


def _candidate_tangent(candidate: BeamGeometry, endpoint: int) -> tuple[Point3D, Point3D]:
    path = candidate.path_points or (candidate.start, candidate.end)
    if len(path) < 2:
        return candidate.start, candidate.end
    return (path[0], path[1]) if endpoint == 0 else (path[-1], path[-2])


def _candidate_curvature(candidate: BeamGeometry, settings: BeamLineSpanSettings) -> bool:
    return _is_curved(candidate.path_points or (candidate.start, candidate.end), settings)


def _stitch_geometry(candidates: list[BeamGeometry], reverses: list[bool]) -> BeamGeometry:
    points: list[Point3D] = []
    source_names: list[str] = []
    for candidate, reverse in zip(candidates, reverses):
        path = candidate.path_points or (candidate.start, candidate.end)
        segment = tuple(reversed(path)) if reverse else path
        if not points:
            points.extend(segment)
        else:
            points.extend(segment[1:])
        source_names.extend(candidate.source_names or (candidate.name,))
    first_path = candidates[0].path_points or (candidates[0].start, candidates[0].end)
    last_path = candidates[-1].path_points or (candidates[-1].start, candidates[-1].end)
    first_path = tuple(reversed(first_path)) if reverses[0] else first_path
    last_path = tuple(reversed(last_path)) if reverses[-1] else last_path
    sections = tuple(dict.fromkeys(candidate.section for candidate in candidates))
    depths = [candidate.depth for candidate in candidates if candidate.depth is not None]
    depth = depths[0] if depths and all(abs(value - depths[0]) <= 1.0 for value in depths) else None
    return BeamGeometry(
        name="STITCHED[" + "+".join(source_names) + "]",
        label="STITCHED[" + "+".join(source_names) + "]",
        story=candidates[0].story,
        point_i=candidates[0].point_j if reverses[0] else candidates[0].point_i,
        point_j=candidates[-1].point_i if reverses[-1] else candidates[-1].point_j,
        start=first_path[0],
        end=last_path[-1],
        section=sections[0] if len(sections) == 1 else "VARIES",
        depth=depth,
        source_names=tuple(source_names),
        path_points=tuple(points),
    )


def _split_candidates_by_role(
    candidates: list[BeamGeometry],
    seed_primary_indices: set[int],
    anchor_primary_indices: set[int],
    column_points: tuple[Point3D, ...],
    primary_index: dict[tuple[int, int], tuple[tuple[Point3D, Point3D], ...]],
    source_by_name: dict[str, BeamGeometry],
    settings: BeamLineSpanSettings,
    seed_intersection_index: dict[tuple[int, int], tuple[tuple[Point3D, Point3D], ...]] | None = None,
) -> list[_UnnumberedLine]:
    """Split continuous paths and classify each support interval as PB or SB.

    A candidate that reaches a column is only primary from that column to the
    first main-beam intersection. Once it passes a primary intersection, any
    following interval without a column is a secondary-beam interval. This is
    required for real models where one geometrically continuous line contains
    both a column-connected main-beam segment and a beam-supported segment.
    """
    lines: list[_UnnumberedLine] = []
    for candidate_index, candidate in enumerate(candidates):
        path = _oriented_path(candidate.path_points or (candidate.start, candidate.end))
        self_segments = {_segment_key(left, right) for left, right in zip(path, path[1:])}
        # Intersections are needed for seeded primary paths as well: a path can
        # have a column-supported core and continue past the first main-beam
        # intersection as a secondary extension.  Intersections inside the
        # column-to-column core do not create an extra PB span because adjacent
        # parts are merged again when their role remains PB.
        support_intersections = _path_intersections(
            path,
            primary_index,
            settings,
            self_segments,
        )
        role_intersections = support_intersections
        if candidate_index in seed_primary_indices and seed_intersection_index is not None:
            role_intersections = _path_intersections(
                path,
                seed_intersection_index,
                settings,
                self_segments,
            )
        is_seed_primary = candidate_index in seed_primary_indices
        # A primary Beam Line is divided into structural spans by columns.  A
        # crossing with another beam is only a role boundary when the path is
        # outside its column-supported core (for example a one-column
        # extension that continues as a secondary beam).  Do not turn an
        # interior PB/SB crossing into an extra PB span: an object such as
        # ``302`` can run from column to column while a secondary beam meets
        # it at an intermediate point.
        if is_seed_primary:
            column_positions = [
                position
                for point in column_points
                if (position := _path_position(path, point, settings.coordinate_tolerance_mm)) is not None
            ]
            if column_positions:
                min_column, max_column = min(column_positions), max(column_positions)
                role_boundary_intersections = tuple(
                    point
                    for point in role_intersections
                    if (position := _path_position(path, point, settings.coordinate_tolerance_mm)) is not None
                    and (position < min_column or position > max_column)
                )
                support_boundary_intersections = tuple(
                    point
                    for point in support_intersections
                    if (position := _path_position(path, point, settings.coordinate_tolerance_mm)) is not None
                    and (position < min_column or position > max_column)
                )
            else:
                role_boundary_intersections = role_intersections
                support_boundary_intersections = support_intersections
            marker_points = (
                *column_points,
                *role_boundary_intersections,
                *support_boundary_intersections,
            )
        else:
            marker_points = (*column_points, *support_intersections)
        markers = _unique_points(marker_points, settings.coordinate_tolerance_mm)
        parts = _split_path(path, markers, settings.coordinate_tolerance_mm)
        if not parts:
            continue
        roles = _classify_part_roles(
            path,
            parts,
            is_seed_primary,
            column_points,
            role_intersections,
            settings,
        )
        grouped = _group_parts_by_role(parts, roles, settings)
        grouped_spans: list[tuple[str, tuple[tuple[Point3D, ...], ...]]] = []
        for beam_type, line_parts in grouped:
            valid_internal_supports = column_points if beam_type == "PB" else support_intersections
            span_paths = _coalesce_parts_without_support(
                line_parts,
                valid_internal_supports,
                settings.coordinate_tolerance_mm,
            )
            grouped_spans.append((beam_type, span_paths))
        all_span_paths = tuple(span_path for _, span_paths in grouped_spans for span_path in span_paths)
        all_span_sources = _source_names_by_paths(
            candidate,
            _join_paths(all_span_paths),
            all_span_paths,
            source_by_name,
            settings,
        )
        source_offset = 0
        for beam_type, span_paths in grouped_spans:
            line_path = _join_paths(span_paths)
            span_sources = all_span_sources[source_offset : source_offset + len(span_paths)]
            source_offset += len(span_paths)
            support_points = (*column_points, *support_intersections) if beam_type == "PB" else support_intersections
            lines.append(
                _UnnumberedLine(
                    candidate.story,
                    beam_type,
                    candidate.section,
                    tuple(name for group in span_sources for name in group),
                    span_sources,
                    line_path,
                    span_paths,
                    support_points,
                    any(_is_curved(part, settings) for part in span_paths),
                    (line_path,),
                    _source_endpoint_name(line_path[0], candidate.source_names or (candidate.name,), source_by_name, settings),
                    _source_endpoint_name(line_path[-1], candidate.source_names or (candidate.name,), source_by_name, settings),
                )
            )
    return lines


def _group_parts_by_role(
    parts: list[tuple[Point3D, ...]], roles: list[str], settings: BeamLineSpanSettings
) -> list[tuple[str, list[tuple[Point3D, ...]]]]:
    grouped: list[tuple[str, list[tuple[Point3D, ...]]]] = []
    for part, role in zip(parts, roles):
        if not grouped:
            grouped.append((role, [part]))
            continue
        current_role, current_parts = grouped[-1]
        previous = current_parts[-1]
        turn = plan_angle_deg(previous[-2], previous[-1], part[0], part[1]) if len(previous) > 1 else 180.0
        curved_change = _is_curved(previous, settings) != _is_curved(part, settings)
        if role == current_role and turn <= settings.through_support_angle_deg and not curved_change:
            current_parts.append(part)
        else:
            grouped.append((role, [part]))
    return grouped


def _coalesce_parts_without_support(
    parts: list[tuple[Point3D, ...]],
    support_points: Iterable[Point3D],
    tolerance: float,
) -> tuple[tuple[Point3D, ...], ...]:
    """Keep only structural supports as internal Span boundaries.

    Role-classification markers may come from an earlier PB support hypothesis.
    If that hypothesis disappears after refinement, adjacent parts of the same
    role must become one Span rather than retaining a stale boundary.
    """

    if not parts:
        return ()
    supports = tuple(support_points)
    spans: list[tuple[Point3D, ...]] = [parts[0]]
    for part in parts[1:]:
        boundary = spans[-1][-1]
        if any(distance(boundary, point) <= tolerance for point in supports):
            spans.append(part)
        else:
            spans[-1] = _join_paths((spans[-1], part))
    return tuple(spans)


def _classify_part_roles(
    path: tuple[Point3D, ...],
    parts: list[tuple[Point3D, ...]],
    is_seed_primary: bool,
    column_points: tuple[Point3D, ...],
    intersections: tuple[Point3D, ...],
    settings: BeamLineSpanSettings,
) -> list[str]:
    """Classify elementary path intervals without using source-object boundaries.

    For a primary candidate, the interval between its first and last column
    supports is the PB core.  On either side, the nearest primary-beam
    intersection is the role boundary: the interval from the extreme column to
    that intersection remains PB, while anything farther out is SB.  This
    captures a one-column main-beam extension and the GF-PB17 pattern where a
    long object path contains both a column-supported PB portion and a
    beam-supported SB portion.
    """
    if not is_seed_primary:
        return ["SB"] * len(parts)

    column_positions = [
        position
        for point in column_points
        if (position := _path_position(path, point, settings.coordinate_tolerance_mm)) is not None
    ]
    if not column_positions:
        return ["SB"] * len(parts)
    min_column, max_column = min(column_positions), max(column_positions)
    intersection_positions = [
        position
        for point in intersections
        if (position := _path_position(path, point, settings.coordinate_tolerance_mm)) is not None
    ]
    left_intersections = [position for position in intersection_positions if position < min_column]
    right_intersections = [position for position in intersection_positions if position > max_column]
    left_boundary = max(left_intersections) if left_intersections else None
    right_boundary = min(right_intersections) if right_intersections else None

    roles: list[str] = []
    for part in parts:
        start = _path_position(path, part[0], settings.coordinate_tolerance_mm) or 0.0
        end = _path_position(path, part[-1], settings.coordinate_tolerance_mm) or start
        midpoint = (start + end) / 2.0
        if min_column <= midpoint <= max_column:
            roles.append("PB")
        elif midpoint < min_column:
            roles.append("PB" if left_boundary is None or midpoint >= left_boundary else "SB")
        else:
            roles.append("PB" if right_boundary is None or midpoint <= right_boundary else "SB")
    return roles


def _column_coordinates(
    beams: list[BeamGeometry],
    names: set[str],
    tolerance: float,
    explicit_coordinates: Mapping[str, Point3D] | None = None,
) -> tuple[Point3D, ...]:
    """Resolve every known column/equivalent-column point to coordinates.

    Historically only support names that happened to be Beam Object endpoints
    could be resolved here. A real column point lying inside a long Beam Object
    was therefore lost before path splitting. The explicit mapping comes from
    ``GeometryExtractor.column_supports`` and is authoritative; endpoint lookup
    remains as a backwards-compatible fallback for existing pure-geometry use.
    """
    if beams:
        story_z_min = min(min(beam.start[2], beam.end[2]) for beam in beams) - tolerance
        story_z_max = max(max(beam.start[2], beam.end[2]) for beam in beams) + tolerance
    else:
        story_z_min, story_z_max = float("-inf"), float("inf")
    # ``explicit_coordinates`` contains supports from every storey.  Filter by
    # the current story's three-dimensional envelope before point-on-path
    # projection; otherwise each story repeatedly scans every column endpoint
    # in the whole model and a 4,000-beam run becomes needlessly quadratic.
    coordinates: list[Point3D] = [
        (float(point[0]), float(point[1]), float(point[2]))
        for name, point in (explicit_coordinates or {}).items()
        if name in names and story_z_min <= float(point[2]) <= story_z_max
    ]
    for beam in beams:
        if beam.point_i in names:
            coordinates.append(beam.start)
        if beam.point_j in names:
            coordinates.append(beam.end)
    return _unique_points(coordinates, tolerance)


def _candidate_is_primary(
    candidate: BeamGeometry,
    source_by_name: dict[str, BeamGeometry],
    column_names: set[str],
    column_points: tuple[Point3D, ...],
    settings: BeamLineSpanSettings,
) -> bool:
    source_names = candidate.source_names or (candidate.name,)
    if any(
        source_by_name[name].point_i in column_names or source_by_name[name].point_j in column_names
        for name in source_names
        if name in source_by_name
    ):
        return True
    path = candidate.path_points or (candidate.start, candidate.end)
    return any(_path_position(path, point, settings.coordinate_tolerance_mm) is not None for point in column_points)


def _candidate_column_count(
    candidate: BeamGeometry, column_points: tuple[Point3D, ...], settings: BeamLineSpanSettings
) -> int:
    path = candidate.path_points or (candidate.start, candidate.end)
    return sum(_path_position(path, point, settings.coordinate_tolerance_mm) is not None for point in column_points)


def _split_candidates(
    candidates: list[BeamGeometry],
    beam_type: str,
    column_points: tuple[Point3D, ...],
    primary_index: dict[tuple[int, int], tuple[tuple[Point3D, Point3D], ...]],
    source_by_name: dict[str, BeamGeometry],
    settings: BeamLineSpanSettings,
) -> list[_UnnumberedLine]:
    lines: list[_UnnumberedLine] = []
    for candidate in candidates:
        path = _oriented_path(candidate.path_points or (candidate.start, candidate.end))
        markers = column_points if beam_type == "PB" else _path_intersections(path, primary_index, settings)
        parts = _split_path(path, markers, settings.coordinate_tolerance_mm)
        groups = _group_parts(parts, settings, beam_type)
        all_span_paths = tuple(part for group in groups for part in group)
        all_span_sources = _source_names_by_paths(
            candidate,
            _join_paths(all_span_paths),
            all_span_paths,
            source_by_name,
            settings,
        )
        source_offset = 0
        for line_parts in groups:
            span_paths = tuple(line_parts)
            line_path = _join_paths(span_paths)
            span_sources = all_span_sources[source_offset : source_offset + len(span_paths)]
            source_offset += len(span_paths)
            lines.append(
                _UnnumberedLine(
                    candidate.story,
                    beam_type,
                    candidate.section,
                    tuple(name for group in span_sources for name in group),
                    span_sources,
                    line_path,
                    span_paths,
                    _unique_points(markers, settings.coordinate_tolerance_mm),
                    any(_is_curved(part, settings) for part in span_paths),
                )
            )
    return lines


def _source_names_by_paths(
    candidate: BeamGeometry,
    line_path: tuple[Point3D, ...],
    span_paths: tuple[tuple[Point3D, ...], ...],
    source_by_name: dict[str, BeamGeometry],
    settings: BeamLineSpanSettings,
) -> tuple[tuple[str, ...], ...]:
    names = candidate.source_names or (candidate.name,)
    boundaries = [_path_length(path) for path in span_paths]
    starts: list[float] = []
    ends: list[float] = []
    total = 0.0
    for value in boundaries:
        starts.append(total)
        total += value
        ends.append(total)
    selected: list[list[str]] = [[] for _ in span_paths]
    for name in names:
        source = source_by_name.get(name)
        if source is None:
            continue
        midpoint = (
            (source.start[0] + source.end[0]) / 2,
            (source.start[1] + source.end[1]) / 2,
            (source.start[2] + source.end[2]) / 2,
        )
        source_start = _path_position(line_path, source.start, settings.coordinate_tolerance_mm * 5)
        source_end = _path_position(line_path, source.end, settings.coordinate_tolerance_mm * 5)
        if source_start is None or source_end is None:
            # A source Object may carry a small modelling gap; use the nearest
            # projection for its endpoints rather than dropping its mapping.
            source_start = _path_position(line_path, source.start, float("inf"))
            source_end = _path_position(line_path, source.end, float("inf"))
        if source_start is not None and source_end is not None:
            source_low, source_high = sorted((source_start, source_end))
            assigned = False
            for index, (start, end) in enumerate(zip(starts, ends)):
                overlap = min(source_high, end) - max(source_low, start)
                # A support centreline can fall a millimetre or two beyond an
                # existing Object endpoint because of modelling offsets or a
                # slightly skew support.  Do not make the whole Object belong
                # to two Spans for such a negligible end sliver; otherwise a
                # harmless offset blocks Unique Name writeback as
                # REQUIRES_SPLIT.  Material overlap still maps to every Span.
                overlap_tolerance = max(
                    settings.source_span_overlap_tolerance_mm,
                    settings.coordinate_tolerance_mm * 0.5,
                )
                if overlap > overlap_tolerance:
                    selected[index].append(name)
                    assigned = True
            if assigned:
                continue
        position = _path_position(line_path, midpoint, float("inf"))
        if position is None:
            continue
        index = next(
            (
                i
                for i, (start, end) in enumerate(zip(starts, ends))
                if start - settings.coordinate_tolerance_mm <= position <= end + settings.coordinate_tolerance_mm
            ),
            len(selected) - 1,
        )
        selected[index].append(name)
    return tuple(tuple(group) for group in selected)


def _source_endpoint_name(
    point: Point3D,
    source_names: tuple[str, ...],
    source_by_name: Mapping[str, BeamGeometry],
    settings: BeamLineSpanSettings,
) -> str:
    names: set[str] = set()
    for name in source_names:
        source = source_by_name.get(name)
        if source is None:
            continue
        if distance(point, source.start) <= settings.coordinate_tolerance_mm:
            names.add(source.point_i)
        if distance(point, source.end) <= settings.coordinate_tolerance_mm:
            names.add(source.point_j)
    return next(iter(names)) if len(names) == 1 else ""


def _join_pb_lines_across_rigid_links(
    lines: list[_UnnumberedLine],
    rigid_links: tuple[RigidLinkGeometry, ...],
    column_anchor_names: set[str],
    settings: BeamLineSpanSettings,
) -> tuple[list[_UnnumberedLine], list[TopologyDiagnostic], list[str]]:
    """Share one PB line identity across a real, unambiguous rigid support gap.

    This runs after role classification and support-boundary validation. The
    rigid frame is never added to a BeamSpan path; it only links the PB line
    identities at its two source-object endpoints.
    """
    if len(lines) < 2 or not rigid_links or not column_anchor_names:
        return lines, [], []

    links_by_story: dict[str, list[RigidLinkGeometry]] = {}
    for link in rigid_links:
        links_by_story.setdefault(link.story, []).append(link)

    endpoint_links: dict[tuple[int, int], tuple[int, int]] = {}
    pair_candidates: list[tuple[float, float, int, int, int, int]] = []
    for story, story_links in links_by_story.items():
        adjacency: dict[str, list[int]] = {}
        component_edges: list[set[int]] = []
        component_nodes: list[set[str]] = []
        unseen = set(range(len(story_links)))
        while unseen:
            seed = min(unseen)
            nodes: set[str] = set()
            edges: set[int] = set()
            stack: list[str] = []
            link = story_links[seed]
            stack.extend((link.point_i, link.point_j))
            while stack:
                point = stack.pop()
                if point in nodes:
                    continue
                nodes.add(point)
                for index, current in enumerate(story_links):
                    if current.point_i == point or current.point_j == point:
                        edges.add(index)
                        other = current.point_j if current.point_i == point else current.point_i
                        if other not in nodes:
                            stack.append(other)
            unseen.difference_update(edges)
            component_nodes.append(nodes)
            component_edges.append(edges)

        for nodes, edges in zip(component_nodes, component_edges):
            anchors = nodes & column_anchor_names
            if not (len(anchors) == 1 and len(edges) == len(nodes) - 1):
                continue
            anchor = next(iter(anchors))
            adjacency = {node: [] for node in nodes}
            coordinates: dict[str, Point3D] = {}
            inconsistent_coordinates = False
            for index in edges:
                link = story_links[index]
                adjacency[link.point_i].append(index)
                adjacency[link.point_j].append(index)
                for point_name, point in ((link.point_i, link.start), (link.point_j, link.end)):
                    existing = coordinates.get(point_name)
                    if existing is not None and distance(existing, point) > settings.coordinate_tolerance_mm:
                        inconsistent_coordinates = True
                    else:
                        coordinates[point_name] = point
            if inconsistent_coordinates or any(len(neighbors) > 2 for neighbors in adjacency.values()):
                continue
            endpoints = []
            for line_index, line in enumerate(lines):
                if line.story != story or line.beam_type != "PB":
                    continue
                for endpoint in (0, 1):
                    point_name = line.start_point_name if endpoint == 0 else line.end_point_name
                    if point_name in nodes:
                        endpoints.append((line_index, endpoint, point_name))
            for left_offset, (left_index, left_endpoint, left_point) in enumerate(endpoints):
                if not left_point:
                    continue
                for right_index, right_endpoint, right_point in endpoints[left_offset + 1 :]:
                    if left_index == right_index or not right_point:
                        continue
                    left_line, right_line = lines[left_index], lines[right_index]
                    if left_point == right_point or left_line.is_curved != right_line.is_curved:
                        continue
                    path_nodes = _rigid_graph_path(left_point, right_point, adjacency, story_links)
                    if not path_nodes or len(path_nodes) < 2:
                        continue
                    if anchor not in path_nodes or len(path_nodes) > 3:
                        continue
                    anchor_index = path_nodes.index(anchor)
                    if anchor_index > 1 or len(path_nodes) - anchor_index - 1 > 1:
                        continue
                    path_points = tuple(coordinates[name] for name in path_nodes)
                    compatible, angle, gap = _rigid_join_tangents(
                        left_line,
                        left_endpoint,
                        right_line,
                        right_endpoint,
                        path_points,
                        settings,
                    )
                    if compatible:
                        pair_candidates.append(
                            (angle, gap, left_index, left_endpoint, right_index, right_endpoint)
                        )

    diagnostics: list[TopologyDiagnostic] = []
    records: list[str] = []
    alternatives_by_endpoint: dict[tuple[int, int], list[tuple[float, float, int, int]]] = {}
    for angle, gap, left_index, left_endpoint, right_index, right_endpoint in pair_candidates:
        alternatives_by_endpoint.setdefault((left_index, left_endpoint), []).append(
            (angle, gap, right_index, right_endpoint)
        )
        alternatives_by_endpoint.setdefault((right_index, right_endpoint), []).append(
            (angle, gap, left_index, left_endpoint)
        )
    ambiguous_endpoints = {
        endpoint
        for endpoint, alternatives in alternatives_by_endpoint.items()
        if len({(item[2], item[3]) for item in alternatives}) > 1
    }
    for line_index, endpoint in sorted(ambiguous_endpoints):
        alternatives = sorted(alternatives_by_endpoint[(line_index, endpoint)])
        diagnostics.append(
            TopologyDiagnostic(
                "RIGID_STITCH_MULTIPLE_CANDIDATES",
                "BLOCKED",
                lines[line_index].story,
                lines[line_index].source_objects,
                "rigid 支承两侧存在多个共线 PB 梁线候选；已保留独立编号。",
                tuple(lines[index].source_objects for _, _, index, _ in alternatives),
            )
        )

    pair_candidates.sort()
    for _, _, left_index, left_endpoint, right_index, right_endpoint in pair_candidates:
        left_key, right_key = (left_index, left_endpoint), (right_index, right_endpoint)
        if left_key in ambiguous_endpoints or right_key in ambiguous_endpoints:
            continue
        if left_key in endpoint_links or right_key in endpoint_links:
            continue
        endpoint_links[left_key] = right_key
        endpoint_links[right_key] = left_key

    components: list[list[tuple[int, bool]]] = []
    visited: set[int] = set()
    for index in range(len(lines)):
        if index in visited:
            continue
        component = _candidate_component(index, endpoint_links)
        visited.update(component)
        if len(component) == 1:
            components.append([(index, False)])
            continue
        ordered = _order_candidate_component(component, endpoint_links)
        if ordered is None:
            names = tuple(name for member in component for name in lines[member].source_objects)
            diagnostics.append(
                TopologyDiagnostic(
                    "RIGID_STITCH_CYCLE_OR_BRANCH",
                    "BLOCKED",
                    lines[component[0]].story,
                    names,
                    "rigid 支承串接图不是唯一开链；已保留独立 PB 梁线。",
                )
            )
            components.extend([[(member, False)] for member in component])
        else:
            components.append(ordered)

    joined: list[_UnnumberedLine] = []
    for ordered in components:
        if len(ordered) == 1:
            joined.append(lines[ordered[0][0]])
            continue
        merged = _merge_rigid_linked_lines(lines, ordered, settings.coordinate_tolerance_mm)
        joined.append(merged)
        records.append(
            f"信息/rigid 梁线串接：{','.join(merged.source_objects)}；保留 {len(merged.span_paths)} 个真实混凝土跨。"
        )
    return joined, diagnostics, records


def _rigid_graph_path(
    start: str,
    end: str,
    adjacency: Mapping[str, list[int]],
    links: list[RigidLinkGeometry],
) -> tuple[str, ...]:
    pending = [start]
    previous: dict[str, str | None] = {start: None}
    while pending:
        point = pending.pop(0)
        if point == end:
            break
        for edge_index in adjacency.get(point, ()):
            link = links[edge_index]
            other = link.point_j if link.point_i == point else link.point_i
            if other not in previous:
                previous[other] = point
                pending.append(other)
    if end not in previous:
        return ()
    path = [end]
    while path[-1] != start:
        parent = previous[path[-1]]
        if parent is None:
            return ()
        path.append(parent)
    return tuple(reversed(path))


def _rigid_join_tangents(
    left: _UnnumberedLine,
    left_endpoint: int,
    right: _UnnumberedLine,
    right_endpoint: int,
    connector: tuple[Point3D, ...],
    settings: BeamLineSpanSettings,
) -> tuple[bool, float, float]:
    gap = sum(distance(start, end) for start, end in zip(connector, connector[1:]))
    if gap <= settings.coordinate_tolerance_mm:
        return False, 180.0, gap
    start, end = connector[0], connector[-1]
    overall_dx, overall_dy = end[0] - start[0], end[1] - start[1]
    if overall_dx == 0.0 and overall_dy == 0.0:
        return False, 180.0, gap
    for first, second in zip(connector, connector[1:]):
        if plan_angle_deg(start, end, first, second) > settings.merge_angle_deg:
            return False, 180.0, gap
        if (second[0] - first[0]) * overall_dx + (second[1] - first[1]) * overall_dy <= 0.0:
            return False, 180.0, gap

    left_path = left.path_points
    right_path = right.path_points
    if len(left_path) < 2 or len(right_path) < 2:
        return False, 180.0, gap
    left_joint, left_inner = (left_path[0], left_path[1]) if left_endpoint == 0 else (left_path[-1], left_path[-2])
    right_joint, right_inner = (right_path[0], right_path[1]) if right_endpoint == 0 else (right_path[-1], right_path[-2])
    if distance(left_joint, start) > settings.coordinate_tolerance_mm:
        return False, 180.0, gap
    if distance(right_joint, end) > settings.coordinate_tolerance_mm:
        return False, 180.0, gap
    left_angle = plan_angle_deg(left_joint, left_inner, start, connector[1])
    right_angle = plan_angle_deg(right_joint, right_inner, connector[-2], end)
    if max(left_angle, right_angle) > settings.merge_angle_deg:
        return False, max(left_angle, right_angle), gap
    left_dot = (left_inner[0] - left_joint[0]) * overall_dx + (left_inner[1] - left_joint[1]) * overall_dy
    right_dot = (right_inner[0] - right_joint[0]) * overall_dx + (right_inner[1] - right_joint[1]) * overall_dy
    compatible = left_dot < 0.0 < right_dot
    return compatible, max(left_angle, right_angle), gap


def _merge_rigid_linked_lines(
    lines: list[_UnnumberedLine], ordered: list[tuple[int, bool]], tolerance: float
) -> _UnnumberedLine:
    first_line = lines[ordered[0][0]]
    first_reverse = ordered[0][1]
    last_line = lines[ordered[-1][0]]
    last_reverse = ordered[-1][1]
    outer_start = first_line.path_points[-1] if first_reverse else first_line.path_points[0]
    outer_end = last_line.path_points[0] if last_reverse else last_line.path_points[-1]
    if _oriented_path((outer_start, outer_end))[0] != outer_start:
        ordered = [(index, not reverse) for index, reverse in reversed(ordered)]

    span_paths: list[tuple[Point3D, ...]] = []
    span_source_objects: list[tuple[str, ...]] = []
    path_parts: list[tuple[Point3D, ...]] = []
    sections: list[str] = []
    support_points: list[Point3D] = []
    for index, reverse in ordered:
        line = lines[index]
        sections.extend(line.section.split(" / "))
        line_span_paths = tuple(reversed(line.span_paths)) if reverse else line.span_paths
        line_span_sources = tuple(reversed(line.span_source_objects)) if reverse else line.span_source_objects
        span_paths.extend(tuple(reversed(path)) if reverse else path for path in line_span_paths)
        span_source_objects.extend(line_span_sources)
        line_parts = line.path_parts or (line.path_points,)
        if reverse:
            path_parts.extend(tuple(reversed(path)) for path in reversed(line_parts))
        else:
            path_parts.extend(line_parts)
        support_points.extend(line.support_points)
    unique_sections = tuple(dict.fromkeys(section for section in sections if section))
    section = unique_sections[0] if len(unique_sections) == 1 else "VARIES"
    source_objects = tuple(dict.fromkeys(name for group in span_source_objects for name in group))
    first_index, first_reverse = ordered[0]
    last_index, last_reverse = ordered[-1]
    first_line, last_line = lines[first_index], lines[last_index]
    return _UnnumberedLine(
        story=first_line.story,
        beam_type="PB",
        section=section,
        source_objects=source_objects,
        span_source_objects=tuple(span_source_objects),
        path_points=path_parts[0],
        span_paths=tuple(span_paths),
        support_points=_unique_points(support_points, tolerance),
        is_curved=first_line.is_curved,
        path_parts=tuple(path_parts),
        start_point_name=(first_line.end_point_name if first_reverse else first_line.start_point_name),
        end_point_name=(last_line.start_point_name if last_reverse else last_line.end_point_name),
    )


def _group_parts(
    parts: list[tuple[Point3D, ...]], settings: BeamLineSpanSettings, beam_type: str
) -> list[list[tuple[Point3D, ...]]]:
    if not parts:
        return []
    groups: list[list[tuple[Point3D, ...]]] = [[parts[0]]]
    for part in parts[1:]:
        previous = groups[-1][-1]
        turn = plan_angle_deg(previous[-2], previous[-1], part[0], part[1]) if len(previous) > 1 else 180.0
        curved_change = _is_curved(previous, settings) != _is_curved(part, settings)
        if turn <= settings.through_support_angle_deg and not curved_change:
            groups[-1].append(part)
        else:
            groups.append([part])
    return groups


def _number_lines(
    lines: list[_UnnumberedLine], settings: BeamLineSpanSettings
) -> tuple[list[BeamLine], list[BeamSpan]]:
    numbered: list[BeamLine] = []
    spans: list[BeamSpan] = []
    for beam_type in ("PB", "SB"):
        by_story: dict[str, list[_UnnumberedLine]] = {}
        for line in lines:
            if line.beam_type == beam_type:
                by_story.setdefault(line.story, []).append(line)
        for story, story_lines in by_story.items():
            story_lines.sort(key=_line_sort_key)
            for line_number, line in enumerate(story_lines, start=1):
                beam_line = BeamLine(
                    story,
                    beam_type,
                    line_number,
                    line.section,
                    line.source_objects,
                    line.path_points,
                    line.is_curved,
                    line.path_parts or (line.path_points,),
                )
                numbered.append(beam_line)
                for span_index, path in enumerate(line.span_paths, start=1):
                    spans.append(
                        BeamSpan(
                            story,
                            beam_type,
                            line_number,
                            span_index,
                            line.span_source_objects[span_index - 1],
                            path,
                            line.is_curved,
                            _is_cantilever(path, line.support_points, settings),
                        )
                    )
    return numbered, spans


def _line_sort_key(line: _UnnumberedLine) -> tuple[float, float, str]:
    points = tuple(point for path in (line.path_parts or (line.path_points,)) for point in path)
    start, end = line.path_points[0], line.path_points[-1]
    horizontal = abs(end[0] - start[0]) >= abs(end[1] - start[1])
    if horizontal:
        return (
            -max(point[1] for point in points),
            min(point[0] for point in points),
            line.source_objects[0],
        )
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        line.source_objects[0],
    )


def _support_path_signature(
    paths: Iterable[tuple[Point3D, ...]], tolerance: float
) -> tuple[tuple[tuple[int, int, int], ...], ...]:
    """Canonicalise a PB support set independent of path order and I/J."""

    scale = max(float(tolerance), 1e-6)
    canonical: list[tuple[tuple[int, int, int], ...]] = []
    for path in paths:
        forward = tuple(tuple(int(round(value / scale)) for value in point) for point in path)
        reverse = tuple(reversed(forward))
        canonical.append(min(forward, reverse))
    return tuple(sorted(canonical))


def _is_cantilever(
    path: tuple[Point3D, ...], support_points: tuple[Point3D, ...], settings: BeamLineSpanSettings
) -> bool:
    if not support_points:
        return False
    start_supported = any(distance(path[0], point) <= settings.coordinate_tolerance_mm for point in support_points)
    end_supported = any(distance(path[-1], point) <= settings.coordinate_tolerance_mm for point in support_points)
    return start_supported != end_supported


def _support_boundary_diagnostics(
    lines: Iterable[_UnnumberedLine],
    column_points: tuple[Point3D, ...],
    primary_paths: tuple[tuple[Point3D, ...], ...],
    settings: BeamLineSpanSettings,
) -> list[TopologyDiagnostic]:
    """Verify that every internal Span boundary has its required support."""

    diagnostics: list[TopologyDiagnostic] = []
    for line in lines:
        for left, right in zip(line.span_paths, line.span_paths[1:]):
            boundary = left[-1]
            if distance(boundary, right[0]) > settings.coordinate_tolerance_mm:
                diagnostics.append(
                    TopologyDiagnostic(
                        "SPAN_PATH_DISCONNECTED",
                        "BLOCKED",
                        line.story,
                        line.source_objects,
                        "同一 Beam Line 的相邻 Span 路径不连续。",
                    )
                )
                continue
            if line.beam_type == "PB":
                supported = any(
                    distance(boundary, point) <= settings.coordinate_tolerance_mm for point in column_points
                )
                code = "PB_INTERNAL_BOUNDARY_WITHOUT_COLUMN"
                message = "PB 内部分跨边界不是柱或 rigid 等效柱支承。"
            else:
                supported = any(
                    _path_position(path, boundary, settings.coordinate_tolerance_mm) is not None
                    for path in primary_paths
                )
                code = "SB_INTERNAL_BOUNDARY_WITHOUT_PRIMARY"
                message = "SB 内部分跨边界不在主梁路径上。"
            if not supported:
                diagnostics.append(
                    TopologyDiagnostic(
                        code,
                        "BLOCKED",
                        line.story,
                        line.source_objects,
                        message,
                    )
                )
    return diagnostics


def _source_coverage_diagnostics(beams: Iterable[BeamGeometry], spans: Iterable[BeamSpan]) -> list[TopologyDiagnostic]:
    """Fail closed if any eligible source Object disappears from all Spans."""

    beam_list = tuple(beams)
    covered = {source_name for span in spans for source_name in span.source_objects}
    by_story: dict[str, list[str]] = {}
    for beam in beam_list:
        if beam.name not in covered:
            by_story.setdefault(beam.story, []).append(beam.name)
    return [
        TopologyDiagnostic(
            "SOURCE_OBJECT_UNCOVERED",
            "BLOCKED",
            story,
            tuple(sorted(names)),
            f"{len(names)} 个混凝土梁 Object 未映射到任何结构 Span。",
        )
        for story, names in sorted(by_story.items())
    ]


def _validation_records(lines: list[BeamLine], spans: list[BeamSpan], settings: BeamLineSpanSettings) -> list[str]:
    records: list[str] = []
    if len(lines) != len({(line.story, line.beam_type, line.line_number) for line in lines}):
        records.append("编号冲突：同楼层同主次梁类型出现重复 Beam Line 编号。")
    for span in spans:
        if len(span.path_points) < 2 or _path_length(span.path_points) <= settings.coordinate_tolerance_mm:
            records.append(f"零长度或无效跨：{span.span_id}。")
    return records


def _oriented_path(path: tuple[Point3D, ...]) -> tuple[Point3D, ...]:
    if len(path) < 2:
        return path
    start, end = path[0], path[-1]
    horizontal = abs(end[0] - start[0]) >= abs(end[1] - start[1])
    reverse = (start[0], start[1]) > (end[0], end[1]) if horizontal else (start[1], start[0]) > (end[1], end[0])
    return tuple(reversed(path)) if reverse else path


def _join_paths(paths: tuple[tuple[Point3D, ...], ...]) -> tuple[Point3D, ...]:
    points: list[Point3D] = []
    for path in paths:
        if not points:
            points.extend(path)
        else:
            points.extend(path[1:])
    return tuple(points)


def _split_path(path: tuple[Point3D, ...], markers: Iterable[Point3D], tolerance: float) -> list[tuple[Point3D, ...]]:
    total = _path_length(path)
    positions = [(0.0, path[0]), (total, path[-1])]
    for marker in markers:
        position = _path_position(path, marker, tolerance)
        if position is not None:
            positions.append((position, _point_at_distance(path, position)))
    positions.sort(key=lambda item: item[0])
    unique: list[tuple[float, Point3D]] = []
    for position, point in positions:
        if not unique or position - unique[-1][0] > tolerance:
            unique.append((position, point))
    parts: list[tuple[Point3D, ...]] = []
    for (start_distance, start), (end_distance, end) in zip(unique, unique[1:]):
        if end_distance - start_distance <= tolerance:
            continue
        parts.append(_subpath(path, start_distance, end_distance, start, end))
    return parts


def _subpath(
    path: tuple[Point3D, ...],
    start_distance: float,
    end_distance: float,
    start: Point3D,
    end: Point3D,
) -> tuple[Point3D, ...]:
    points = [start]
    distance_so_far = 0.0
    for left, right in zip(path, path[1:]):
        segment = distance(left, right)
        distance_so_far += segment
        if start_distance < distance_so_far < end_distance:
            points.append(right)
    if distance(points[-1], end) > 1e-6:
        points.append(end)
    return tuple(points)


def _path_position(path: tuple[Point3D, ...], point: Point3D, tolerance: float) -> float | None:
    position = 0.0
    best: float | None = None
    best_gap = float("inf")
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


def _point_at_distance(path: tuple[Point3D, ...], target: float) -> Point3D:
    remaining = target
    for left, right in zip(path, path[1:]):
        segment_length = distance(left, right)
        if remaining <= segment_length:
            ratio = 0.0 if segment_length == 0 else remaining / segment_length
            return tuple(left[i] + (right[i] - left[i]) * ratio for i in range(3))  # type: ignore[return-value]
        remaining -= segment_length
    return path[-1]


def _path_length(path: tuple[Point3D, ...]) -> float:
    return sum(distance(left, right) for left, right in zip(path, path[1:]))


def _path_intersections(
    path: tuple[Point3D, ...],
    primary_index: dict[tuple[int, int], tuple[tuple[Point3D, Point3D], ...]],
    settings: BeamLineSpanSettings,
    excluded_segments: set[frozenset[Point3D]] | None = None,
) -> tuple[Point3D, ...]:
    intersections: list[Point3D] = []
    for left, right in zip(path, path[1:]):
        for p_left, p_right in _indexed_candidates(left, right, primary_index, settings.intersection_grid_mm, settings.coordinate_tolerance_mm):
            if excluded_segments:
                if _segment_key(p_left, p_right) in excluded_segments:
                    continue
                # A refined PB support path can contain only a subsegment of
                # the candidate currently being classified.  Exact segment
                # keys then differ and the shared vertex of a curved path was
                # previously mistaken for an external PB intersection.  That
                # made PB/SB roles depend on whether I/J had already been
                # normalised.  Exclude every support segment wholly carried
                # by the current path, not only byte-identical segments.
                if (
                    _path_position(path, p_left, settings.coordinate_tolerance_mm) is not None
                    and _path_position(path, p_right, settings.coordinate_tolerance_mm) is not None
                ):
                    continue
            point = _segment_intersection(
                left,
                right,
                p_left,
                p_right,
                settings.coordinate_tolerance_mm,
                settings.intersection_z_tolerance_mm,
            )
            if point is not None:
                intersections.append(point)
    return _unique_points(intersections, settings.coordinate_tolerance_mm)


def _segment_key(left: Point3D, right: Point3D) -> frozenset[Point3D]:
    return frozenset((left, right))


def _build_segment_index(
    paths: tuple[tuple[Point3D, ...], ...], grid_size: float
) -> dict[tuple[int, int], tuple[tuple[Point3D, Point3D], ...]]:
    cells: dict[tuple[int, int], list[tuple[Point3D, Point3D]]] = {}
    for path in paths:
        for left, right in zip(path, path[1:]):
            min_x, max_x = sorted((left[0], right[0]))
            min_y, max_y = sorted((left[1], right[1]))
            for cell_x in range(_grid_index(min_x, grid_size), _grid_index(max_x, grid_size) + 1):
                for cell_y in range(_grid_index(min_y, grid_size), _grid_index(max_y, grid_size) + 1):
                    cells.setdefault((cell_x, cell_y), []).append((left, right))
    return {key: tuple(value) for key, value in cells.items()}


def _indexed_candidates(
    left: Point3D,
    right: Point3D,
    index: dict[tuple[int, int], tuple[tuple[Point3D, Point3D], ...]],
    grid_size: float,
    tolerance: float = 0.0,
) -> tuple[tuple[Point3D, Point3D], ...]:
    min_x, max_x = sorted((left[0], right[0]))
    min_y, max_y = sorted((left[1], right[1]))
    candidates: list[tuple[Point3D, Point3D]] = []
    seen: set[tuple[Point3D, Point3D]] = set()
    for cell_x in range(_grid_index(min_x - tolerance, grid_size), _grid_index(max_x + tolerance, grid_size) + 1):
        for cell_y in range(_grid_index(min_y - tolerance, grid_size), _grid_index(max_y + tolerance, grid_size) + 1):
            for segment in index.get((cell_x, cell_y), ()):
                if segment not in seen:
                    seen.add(segment)
                    candidates.append(segment)
    return tuple(candidates)


def _grid_index(value: float, grid_size: float) -> int:
    return int(value // grid_size)


def _segment_intersection(
    a: Point3D,
    b: Point3D,
    c: Point3D,
    d: Point3D,
    tolerance: float,
    z_tolerance: float,
) -> Point3D | None:
    r_x, r_y = b[0] - a[0], b[1] - a[1]
    s_x, s_y = d[0] - c[0], d[1] - c[1]
    denominator = r_x * s_y - r_y * s_x
    if abs(denominator) <= tolerance * 1e-6:
        return None
    q_x, q_y = c[0] - a[0], c[1] - a[1]
    t = (q_x * s_y - q_y * s_x) / denominator
    u = (q_x * r_y - q_y * r_x) / denominator
    t_clamped = max(0.0, min(1.0, t))
    u_clamped = max(0.0, min(1.0, u))
    if ((t - t_clamped) ** 2 * (r_x * r_x + r_y * r_y) <= tolerance ** 2
            and (u - u_clamped) ** 2 * (s_x * s_x + s_y * s_y) <= tolerance ** 2):
        z_a = a[2] + t * (b[2] - a[2])
        z_c = c[2] + u * (d[2] - c[2])
        if abs(z_a - z_c) > z_tolerance:
            return None
        return (a[0] + t_clamped * r_x, a[1] + t_clamped * r_y, a[2] + t_clamped * (b[2] - a[2]))
    return None


def _unique_points(points: Iterable[Point3D], tolerance: float) -> tuple[Point3D, ...]:
    unique: list[Point3D] = []
    for point in points:
        if not any(distance(point, existing) <= tolerance for existing in unique):
            unique.append(point)
    return tuple(unique)


def _is_curved(path: tuple[Point3D, ...], settings: BeamLineSpanSettings) -> bool:
    if len(path) <= 2:
        return False
    chord = distance(path[0], path[-1])
    if chord <= settings.coordinate_tolerance_mm:
        return False
    polyline = _path_length(path)
    return polyline - chord > max(settings.coordinate_tolerance_mm, chord * 1e-4)
