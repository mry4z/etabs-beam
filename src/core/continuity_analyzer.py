"""Continuous-beam grouping based on story, shared endpoint and plan alignment."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.core.geometry_extractor import BeamGeometry
from src.utils.geometry import distance, plan_angle_deg


@dataclass(frozen=True)
class ContinuityGroup:
    group_id: int
    story: str
    members: tuple[str, ...]


class ContinuityAnalyzer:
    def __init__(self, coordinate_tolerance_mm: float = 1.0, slope_limit_deg: float = 1.0) -> None:
        self.coordinate_tolerance_mm = coordinate_tolerance_mm
        self.slope_limit_deg = slope_limit_deg

    def analyze(self, beams: list[BeamGeometry]) -> list[ContinuityGroup]:
        by_story: dict[str, list[BeamGeometry]] = {}
        for beam in beams:
            by_story.setdefault(beam.story, []).append(beam)

        groups: list[ContinuityGroup] = []
        group_id = 1
        for story, story_beams in by_story.items():
            adjacency = {beam.name: set() for beam in story_beams}
            for index, left in enumerate(story_beams):
                for right in story_beams[index + 1 :]:
                    if self._are_continuous(left, right):
                        adjacency[left.name].add(right.name)
                        adjacency[right.name].add(left.name)

            visited: set[str] = set()
            for beam in story_beams:
                if beam.name in visited:
                    continue
                component = self._component(beam.name, adjacency, visited)
                if self._is_simple_path(component, adjacency):
                    groups.append(ContinuityGroup(group_id, story, tuple(self._ordered(component, adjacency))))
                    group_id += 1
                    continue

                logging.getLogger(__name__).warning(
                    "楼层 %s 的连续梁拓扑含分支或环，已降级为独立梁组：%s",
                    story,
                    ", ".join(sorted(component)),
                )
                for name in sorted(component):
                    groups.append(ContinuityGroup(group_id, story, (name,)))
                    group_id += 1
        return groups

    def _are_continuous(self, left: BeamGeometry, right: BeamGeometry) -> bool:
        endpoints_touch = any(
            distance(a, b) <= self.coordinate_tolerance_mm
            for a in (left.start, left.end)
            for b in (right.start, right.end)
        )
        return endpoints_touch and plan_angle_deg(left.start, left.end, right.start, right.end) <= self.slope_limit_deg

    @staticmethod
    def _component(start: str, adjacency: dict[str, set[str]], visited: set[str]) -> set[str]:
        stack, component = [start], set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            visited.add(current)
            stack.extend(adjacency[current] - component)
        return component

    @staticmethod
    def _ordered(component: set[str], adjacency: dict[str, set[str]]) -> list[str]:
        endpoints = sorted(name for name in component if len(adjacency[name] & component) <= 1)
        start = endpoints[0] if endpoints else min(component)
        ordered, previous, current = [], None, start
        while current is not None and current not in ordered:
            ordered.append(current)
            candidates = sorted((adjacency[current] & component) - ({previous} if previous else set()))
            previous, current = current, (candidates[0] if candidates else None)
        return ordered

    @staticmethod
    def _is_simple_path(component: set[str], adjacency: dict[str, set[str]]) -> bool:
        """Return true only for a connected non-branching path.

        Branches and cycles have no unambiguous left/right relationship for the
        current export format. They are deliberately downgraded to singleton
        groups rather than silently dropping members during ordering.
        """
        if len(component) == 1:
            return True
        degrees = [len(adjacency[name] & component) for name in component]
        return max(degrees) <= 2 and degrees.count(1) == 2
