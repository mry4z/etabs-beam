"""Compare logical-beam merge results under two angle limits."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.beam_merger import BeamMerger
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignResultStatus
from src.utils.geometry import Point3D


@dataclass(frozen=True)
class MergeAngleDifference:
    story: str
    section: str
    source_objects: tuple[str, ...]
    lower_angle_groups: tuple[tuple[str, ...], ...]
    logical_length_mm: float
    path_points: tuple[Point3D, ...] = ()
    lower_paths: tuple[tuple[Point3D, ...], ...] = ()


def compare_merge_angles(
    beams: list[BeamGeometry], lower_angle_deg: float = 3.0, higher_angle_deg: float = 15.0
) -> list[MergeAngleDifference]:
    """Return higher-angle logical beams that combine multiple lower-angle groups."""
    if higher_angle_deg <= lower_angle_deg:
        raise ValueError("higher_angle_deg 必须大于 lower_angle_deg。")
    designs = {
        beam.name: BeamDesignResult(beam.name, status=DesignResultStatus.SKIPPED)
        for beam in beams
    }
    lower = BeamMerger(angle_limit_deg=lower_angle_deg).merge(beams, designs)
    higher = BeamMerger(angle_limit_deg=higher_angle_deg).merge(beams, designs)
    lower_by_source = {
        source: _source_names(beam)
        for beam in lower.beams
        for source in _source_names(beam)
    }
    lower_path_by_source = {
        source: (beam.path_points or (beam.start, beam.end))
        for beam in lower.beams
        for source in _source_names(beam)
    }

    differences: list[MergeAngleDifference] = []
    for beam in higher.beams:
        sources = _source_names(beam)
        lower_groups = tuple(sorted({lower_by_source[source] for source in sources}))
        if len(lower_groups) > 1:
            differences.append(
                MergeAngleDifference(
                    beam.story,
                    beam.section,
                    sources,
                    lower_groups,
                    beam.length,
                    beam.path_points or (beam.start, beam.end),
                    tuple(lower_path_by_source[group[0]] for group in lower_groups),
                )
            )
    return differences


def _source_names(beam: BeamGeometry) -> tuple[str, ...]:
    return beam.source_names or (beam.name,)
