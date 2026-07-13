"""Identify drawing beam lines and structural spans from ETABS beam geometry.

This module deliberately sits above :mod:`beam_merger`: Beam Object fragments
are first used to form candidate paths, then structural supports decide spans.
It does not read ETABS or design results and is therefore straightforward to
test with small coordinate-only models.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable

from src.core.beam_merger import BeamMerger
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignResultStatus
from src.utils.geometry import Point3D, distance, plan_angle_deg


@dataclass(frozen=True)
class BeamLineSpanSettings:
    coordinate_tolerance_mm: float = 1.0
    merge_angle_deg: float = 15.0
    through_support_angle_deg: float = 3.0
    intersection_grid_mm: float = 5000.0
    story_workers: int = 1


@dataclass(frozen=True)
class BeamLine:
    story: str
    beam_type: str  # PB or SB
    line_number: int
    section: str
    source_objects: tuple[str, ...]
    path_points: tuple[Point3D, ...]
    is_curved: bool

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


def analyze_beam_lines(
    beams: list[BeamGeometry],
    column_point_names: set[str] | None = None,
    settings: BeamLineSpanSettings | None = None,
) -> BeamLineSpanResult:
    """Return numbered Beam Lines and support-defined Spans.

    ``column_point_names`` contains ETABS points touched by column Objects (and
    may include rigid-end equivalent points). A candidate touching any such
    point is a primary line. Secondary paths are split only at intersections
    with already identified primary paths.
    """
    settings = settings or BeamLineSpanSettings()
    column_point_names = column_point_names or set()
    by_story: dict[str, list[BeamGeometry]] = {}
    for beam in beams:
        by_story.setdefault(beam.story, []).append(beam)
    story_inputs = [(story_beams, column_point_names, settings) for story_beams in by_story.values()]
    if settings.story_workers > 1 and len(story_inputs) > 1:
        with ThreadPoolExecutor(max_workers=settings.story_workers) as executor:
            story_results = list(executor.map(_analyze_story, story_inputs))
    else:
        story_results = [_analyze_story(item) for item in story_inputs]
    unnumbered_lines = [line for lines, _ in story_results for line in lines]
    records = [record for _, story_records in story_results for record in story_records]
    numbered_lines, spans = _number_lines(unnumbered_lines, settings)
    records.extend(_validation_records(numbered_lines, spans, settings))
    return BeamLineSpanResult(tuple(numbered_lines), tuple(spans), tuple(records))


def _analyze_story(
    input_data: tuple[list[BeamGeometry], set[str], BeamLineSpanSettings]
) -> tuple[list[_UnnumberedLine], list[str]]:
    """Analyze one story; this function is isolated so stories can run in parallel."""
    beams, column_point_names, settings = input_data
    column_points = _column_coordinates(beams, column_point_names, settings.coordinate_tolerance_mm)
    designs = {
        beam.name: BeamDesignResult(beam.name, status=DesignResultStatus.SKIPPED)
        for beam in beams
    }
    merge_result = BeamMerger(angle_limit_deg=settings.merge_angle_deg).merge(beams, designs)
    candidates, stitch_records = _stitch_candidates(merge_result.beams, settings)
    records = [
        f"{record.severity}/{record.category}: {record.message} [{record.beam_names}]"
        for record in merge_result.records
    ]
    records.extend(stitch_records)
    source_by_name = {beam.name: beam for beam in beams}
    primary_candidates = [
        beam for beam in candidates
        if _candidate_is_primary(beam, source_by_name, column_point_names, column_points, settings)
    ]
    primary_lines = _split_candidates(
        primary_candidates, "PB", column_points, {}, source_by_name, settings,
    )
    primary_paths = tuple(line.path_points for line in primary_lines)
    primary_index = _build_segment_index(primary_paths, settings.intersection_grid_mm)
    secondary_candidates = [beam for beam in candidates if beam not in primary_candidates]
    secondary_lines = _split_candidates(
        secondary_candidates, "SB", (), primary_index, source_by_name, settings,
    )
    return primary_lines + secondary_lines, records


def _stitch_candidates(
    candidates: list[BeamGeometry], settings: BeamLineSpanSettings
) -> tuple[list[BeamGeometry], list[str]]:
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
        return candidates, []

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
                        pair_candidates.append(
                            (angle, gap, left_index, left_endpoint, right_index, right_endpoint)
                        )

    # At a support there can be several objects meeting at one point. Consume
    # each physical endpoint once and select the most collinear continuation.
    pair_candidates.sort()
    for _, _, left_index, left_endpoint, right_index, right_endpoint in pair_candidates:
        left_key = (left_index, left_endpoint)
        right_key = (right_index, right_endpoint)
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
            # and let the existing merge records expose the topology for review.
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
        sections = tuple(dict.fromkeys(candidate.section for candidate, _ in zip(
            [candidates[index] for index, _ in ordered], ordered
        )))
        if len(sections) > 1:
            records.append(
                "信息/梁线串接：{} [{}]；跨截面变化保留为同一 Beam Line。".format(
                    ",".join(merged.source_names), " / ".join(sections)
                )
            )
    return stitched, records


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
    endpoints = [
        (index, endpoint)
        for index in component
        for endpoint in (0, 1)
        if (index, endpoint) not in links
    ]
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


def _column_coordinates(beams: list[BeamGeometry], names: set[str], tolerance: float) -> tuple[Point3D, ...]:
    coordinates: list[Point3D] = []
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
        for name in source_names if name in source_by_name
    ):
        return True
    path = candidate.path_points or (candidate.start, candidate.end)
    return any(_path_position(path, point, settings.coordinate_tolerance_mm) is not None for point in column_points)


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
            span_sources = all_span_sources[source_offset:source_offset + len(span_paths)]
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
        position = _path_position(line_path, midpoint, settings.coordinate_tolerance_mm * 5)
        if position is None:
            # A source Object may cross a support point or carry a small
            # modelling gap. Assign it to the nearest span rather than
            # duplicating the whole candidate into every split line.
            position = _path_position(line_path, midpoint, float("inf"))
        if position is None:
            continue
        index = next(
            (i for i, (start, end) in enumerate(zip(starts, ends))
             if start - settings.coordinate_tolerance_mm <= position <= end + settings.coordinate_tolerance_mm),
            len(selected) - 1,
        )
        selected[index].append(name)
    return tuple(tuple(group) for group in selected)


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
                    story, beam_type, line_number, line.section,
                    line.source_objects, line.path_points, line.is_curved,
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
    start, end = line.path_points[0], line.path_points[-1]
    horizontal = abs(end[0] - start[0]) >= abs(end[1] - start[1])
    if horizontal:
        return (
            -max(point[1] for point in line.path_points),
            min(point[0] for point in line.path_points),
            line.source_objects[0],
        )
    return (
        min(point[0] for point in line.path_points),
        min(point[1] for point in line.path_points),
        line.source_objects[0],
    )


def _is_cantilever(
    path: tuple[Point3D, ...], support_points: tuple[Point3D, ...], settings: BeamLineSpanSettings
) -> bool:
    if not support_points:
        return False
    start_supported = any(distance(path[0], point) <= settings.coordinate_tolerance_mm for point in support_points)
    end_supported = any(distance(path[-1], point) <= settings.coordinate_tolerance_mm for point in support_points)
    return start_supported != end_supported


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
    path: tuple[Point3D, ...], start_distance: float, end_distance: float,
    start: Point3D, end: Point3D,
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
) -> tuple[Point3D, ...]:
    intersections: list[Point3D] = []
    for left, right in zip(path, path[1:]):
        for p_left, p_right in _indexed_candidates(left, right, primary_index, settings.intersection_grid_mm):
            point = _segment_intersection(left, right, p_left, p_right, settings.coordinate_tolerance_mm)
            if point is not None:
                intersections.append(point)
    return _unique_points(intersections, settings.coordinate_tolerance_mm)


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
) -> tuple[tuple[Point3D, Point3D], ...]:
    min_x, max_x = sorted((left[0], right[0]))
    min_y, max_y = sorted((left[1], right[1]))
    candidates: list[tuple[Point3D, Point3D]] = []
    seen: set[tuple[Point3D, Point3D]] = set()
    for cell_x in range(_grid_index(min_x, grid_size), _grid_index(max_x, grid_size) + 1):
        for cell_y in range(_grid_index(min_y, grid_size), _grid_index(max_y, grid_size) + 1):
            for segment in index.get((cell_x, cell_y), ()):
                if segment not in seen:
                    seen.add(segment)
                    candidates.append(segment)
    return tuple(candidates)


def _grid_index(value: float, grid_size: float) -> int:
    return int(value // grid_size)


def _segment_intersection(a: Point3D, b: Point3D, c: Point3D, d: Point3D, tolerance: float) -> Point3D | None:
    r_x, r_y = b[0] - a[0], b[1] - a[1]
    s_x, s_y = d[0] - c[0], d[1] - c[1]
    denominator = r_x * s_y - r_y * s_x
    if abs(denominator) <= tolerance * 1e-6:
        return None
    q_x, q_y = c[0] - a[0], c[1] - a[1]
    t = (q_x * s_y - q_y * s_x) / denominator
    u = (q_x * r_y - q_y * r_x) / denominator
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return (a[0] + t * r_x, a[1] + t * r_y, a[2] + t * (b[2] - a[2]))
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
