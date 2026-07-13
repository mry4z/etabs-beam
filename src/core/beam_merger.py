"""Merge over-segmented same-section beam objects into logical beam spans."""

from __future__ import annotations

from dataclasses import dataclass
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation
from src.utils.geometry import distance, plan_angle_deg


@dataclass(frozen=True)
class MergeRecord:
    severity: str
    category: str
    beam_names: str
    message: str


@dataclass(frozen=True)
class BeamMergeResult:
    beams: list[BeamGeometry]
    designs: dict[str, BeamDesignResult]
    records: list[MergeRecord]


@dataclass(frozen=True)
class _Link:
    other: str
    self_endpoint: int
    other_endpoint: int


class BeamMerger:
    """Collapse same-section endpoint-connected beam chains.

    A component is merged only when its object graph is a simple path. Branches
    and cycles remain as original objects and are reported for manual review.
    This prevents a junction from being silently represented by an arbitrary
    left/middle/right ordering.
    """

    def __init__(
        self,
        coordinate_tolerance_mm: float = 1.0,
        angle_limit_deg: float = 15.0,
        depth_tolerance_mm: float = 1.0,
    ) -> None:
        self.coordinate_tolerance_mm = coordinate_tolerance_mm
        self.angle_limit_deg = angle_limit_deg
        self.depth_tolerance_mm = depth_tolerance_mm

    def merge(
        self,
        beams: list[BeamGeometry],
        designs: dict[str, BeamDesignResult],
        excluded_design_names: set[str] | None = None,
    ) -> BeamMergeResult:
        excluded_design_names = excluded_design_names or set()
        links = self._build_links(beams)
        components = self._components(beams, links)
        merged_beams: list[BeamGeometry] = []
        merged_designs: dict[str, BeamDesignResult] = {}
        records: list[MergeRecord] = []
        by_name = {beam.name: beam for beam in beams}

        for component in components:
            if len(component) == 1:
                beam = by_name[component[0]]
                merged_beams.append(self._with_source_names(beam))
                merged_designs[beam.name] = designs.get(beam.name, BeamDesignResult(beam.name))
                continue

            if not self._is_simple_path(component, links):
                names = ", ".join(component)
                records.append(MergeRecord("警告", "合并拓扑", names, "同截面候选梁形成分支或环，保留原始梁对象。"))
                for name in component:
                    beam = by_name[name]
                    merged_beams.append(self._with_source_names(beam))
                    merged_designs[name] = designs.get(name, BeamDesignResult(name))
                continue

            ordered = self._ordered_segments(component, links, by_name)
            merged = self._merged_geometry(ordered, by_name)
            design = self._merged_design(merged.name, ordered, designs, records, excluded_design_names)
            merged_beams.append(merged)
            merged_designs[merged.name] = design
            records.append(
                MergeRecord(
                    "信息",
                    "梁段合并",
                    ", ".join(name for name, _, _ in ordered),
                    f"按同楼层、同截面、端点相接、方向夹角≤{self.angle_limit_deg:g}°合并为一跨逻辑梁。",
                )
            )

        return BeamMergeResult(merged_beams, merged_designs, records)

    def _build_links(self, beams: list[BeamGeometry]) -> dict[str, list[_Link]]:
        links = {beam.name: [] for beam in beams}
        groups: dict[tuple[str, str], list[BeamGeometry]] = {}
        for beam in beams:
            groups.setdefault((beam.story, beam.section.strip().casefold()), []).append(beam)
        checked: set[tuple[str, str]] = set()
        for group in groups.values():
            endpoint_index: dict[tuple[int, int, int], list[tuple[str, int]]] = {}
            by_name = {beam.name: beam for beam in group}
            for beam in group:
                for endpoint_index_value, point in enumerate((beam.start, beam.end)):
                    cell = self._endpoint_cell(point)
                    nearby: set[tuple[str, int]] = set()
                    for neighbor in self._neighbor_cells(cell):
                        nearby.update(endpoint_index.get(neighbor, ()))
                    for other_name, _ in nearby:
                        pair_key = tuple(sorted((beam.name, other_name)))
                        if pair_key in checked:
                            continue
                        checked.add(pair_key)
                        other = by_name[other_name]
                        if not self._compatible(beam, other):
                            continue
                        pairs = [
                            (0, 0, distance(beam.start, other.start)),
                            (0, 1, distance(beam.start, other.end)),
                            (1, 0, distance(beam.end, other.start)),
                            (1, 1, distance(beam.end, other.end)),
                        ]
                        beam_endpoint, other_endpoint, gap = min(pairs, key=lambda pair: pair[2])
                        if gap <= self.coordinate_tolerance_mm:
                            links[beam.name].append(_Link(other.name, beam_endpoint, other_endpoint))
                            links[other.name].append(_Link(beam.name, other_endpoint, beam_endpoint))
                    endpoint_index.setdefault(cell, []).append((beam.name, endpoint_index_value))
        return links

    def _endpoint_cell(self, point: tuple[float, float, float]) -> tuple[int, int, int]:
        tolerance = max(self.coordinate_tolerance_mm, 1e-6)
        return tuple(int(round(value / tolerance)) for value in point)

    @staticmethod
    def _neighbor_cells(cell: tuple[int, int, int]) -> tuple[tuple[int, int, int], ...]:
        return tuple(
            (cell[0] + dx, cell[1] + dy, cell[2] + dz)
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for dz in (-1, 0, 1)
        )

    def _compatible(self, left: BeamGeometry, right: BeamGeometry) -> bool:
        if left.story != right.story or left.section.strip().casefold() != right.section.strip().casefold():
            return False
        if left.depth is not None and right.depth is not None:
            if abs(left.depth - right.depth) > self.depth_tolerance_mm:
                return False
        return plan_angle_deg(left.start, left.end, right.start, right.end) <= self.angle_limit_deg

    @staticmethod
    def _components(beams: list[BeamGeometry], links: dict[str, list[_Link]]) -> list[list[str]]:
        components, visited = [], set()
        for beam in beams:
            if beam.name in visited:
                continue
            stack, component = [beam.name], []
            while stack:
                name = stack.pop()
                if name in visited:
                    continue
                visited.add(name)
                component.append(name)
                stack.extend(link.other for link in links[name] if link.other not in visited)
            components.append(component)
        return components

    @staticmethod
    def _is_simple_path(component: list[str], links: dict[str, list[_Link]]) -> bool:
        if len(component) <= 1:
            return True
        degrees = [len([link for link in links[name] if link.other in component]) for name in component]
        return max(degrees, default=0) <= 2 and degrees.count(1) == 2

    @staticmethod
    def _ordered_segments(
        component: list[str], links: dict[str, list[_Link]], by_name: dict[str, BeamGeometry]
    ) -> list[tuple[str, bool, float]]:
        start = next(name for name in component if len(links[name]) == 1)
        ordered: list[tuple[str, bool, float]] = []
        previous: str | None = None
        current = start
        incoming_endpoint: int | None = None
        while current is not None:
            beam = by_name[current]
            next_links = [link for link in links[current] if link.other != previous]
            next_link = next_links[0] if next_links else None
            if incoming_endpoint is None:
                endpoint = next_link.self_endpoint if next_link else 0
                reverse = endpoint == 0
            else:
                endpoint = next_link.self_endpoint if next_link else 1 - incoming_endpoint
                reverse = incoming_endpoint == 1
            ordered.append((current, reverse, beam.length))
            if next_link is None:
                break
            previous, current, incoming_endpoint = current, next_link.other, next_link.other_endpoint
        return ordered

    @staticmethod
    def _merged_geometry(ordered: list[tuple[str, bool, float]], by_name: dict[str, BeamGeometry]) -> BeamGeometry:
        points: list[tuple[float, float, float]] = []
        for name, reverse, _ in ordered:
            beam = by_name[name]
            segment_points = (beam.end, beam.start) if reverse else (beam.start, beam.end)
            if not points:
                points.extend(segment_points)
            else:
                points.append(segment_points[1])
        first = by_name[ordered[0][0]]
        last = by_name[ordered[-1][0]]
        source_names = tuple(name for name, _, _ in ordered)
        merged_name = "MERGED[" + "+".join(source_names) + "]"
        first_reverse = ordered[0][1]
        last_reverse = ordered[-1][1]
        return BeamGeometry(
            name=merged_name,
            label=merged_name,
            story=first.story,
            point_i=first.point_j if first_reverse else first.point_i,
            point_j=last.point_i if last_reverse else last.point_j,
            start=points[0],
            end=points[-1],
            section=first.section,
            depth=first.depth,
            source_names=source_names,
            path_points=tuple(points),
        )

    @staticmethod
    def _with_source_names(beam: BeamGeometry) -> BeamGeometry:
        return BeamGeometry(
            beam.name,
            beam.label,
            beam.story,
            beam.point_i,
            beam.point_j,
            beam.start,
            beam.end,
            beam.section,
            beam.depth,
            beam.source_names or (beam.name,),
            beam.path_points,
        )

    @staticmethod
    def _merged_design(
        name: str,
        ordered: list[tuple[str, bool, float]],
        designs: dict[str, BeamDesignResult],
        records: list[MergeRecord],
        excluded_design_names: set[str],
    ) -> BeamDesignResult:
        stations: list[DesignStation] = []
        offset = 0.0
        source_results: list[BeamDesignResult] = []
        for source, reverse, segment_length in ordered:
            design = designs.get(source)
            if design is None:
                design = BeamDesignResult(
                    source,
                    status=DesignResultStatus.API_ERROR,
                    status_message="合并梁段没有可用的 ETABS 设计结果。",
                )
                if source not in excluded_design_names:
                    records.append(MergeRecord("警告", "设计结果", source, design.status_message))
            source_results.append(design)
            if design.stations:
                for station in design.stations:
                    local = segment_length - station.location if reverse else station.location
                    stations.append(_offset_station(station, offset + local))
            offset += segment_length
        stations.sort(key=lambda station: station.location)
        deduped: list[DesignStation] = []
        for station in stations:
            if deduped and abs(deduped[-1].location - station.location) <= 1.0:
                continue
            deduped.append(station)
        status, message = _merged_status(source_results, bool(deduped))
        return BeamDesignResult(name, tuple(deduped), status, message)


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


def _merged_status(
    source_results: list[BeamDesignResult], has_stations: bool
) -> tuple[DesignResultStatus, str]:
    statuses = {result.status for result in source_results}
    messages = [result.status_message for result in source_results if result.status_message]
    if has_stations:
        if statuses == {DesignResultStatus.AVAILABLE}:
            return DesignResultStatus.AVAILABLE, ""
        return DesignResultStatus.PARTIAL, "；".join(dict.fromkeys(messages))
    if statuses == {DesignResultStatus.SKIPPED}:
        return DesignResultStatus.SKIPPED, "；".join(dict.fromkeys(messages))
    if DesignResultStatus.API_ERROR in statuses:
        return DesignResultStatus.API_ERROR, "；".join(dict.fromkeys(messages))
    return DesignResultStatus.NO_RESULTS, "；".join(dict.fromkeys(messages)) or "ETABS 未返回混凝土梁设计站点。"
