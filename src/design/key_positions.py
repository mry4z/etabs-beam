"""Select controlling reinforcement demands at left, middle and right beam regions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignStation


@dataclass(frozen=True)
class KeyDemand:
    location: float
    combo: str
    area: float


@dataclass(frozen=True)
class BeamKeyPositions:
    depth: float
    end_zone: float
    left_top_flexural: KeyDemand
    left_bottom_flexural: KeyDemand
    left_shear: KeyDemand
    middle_top_flexural: KeyDemand
    middle_bottom_flexural: KeyDemand
    middle_shear: KeyDemand
    right_top_flexural: KeyDemand
    right_bottom_flexural: KeyDemand
    right_shear: KeyDemand


def key_positions(beam: BeamGeometry, design: BeamDesignResult) -> BeamKeyPositions | None:
    """Return control demands within I-end, middle and J-end regions.

    ``D`` is the section total depth. End zones span ``2D`` from each end. If
    these zones overlap, the middle result is the station closest to midspan.
    """
    if not design.stations or beam.depth is None or beam.depth <= 0:
        return None

    end_zone = min(2.0 * beam.depth, beam.length / 2.0)
    left = [station for station in design.stations if station.location <= end_zone]
    right = [station for station in design.stations if station.location >= beam.length - end_zone]
    if not left:
        left = [min(design.stations, key=lambda station: abs(station.location - end_zone))]
    if not right:
        right = [min(design.stations, key=lambda station: abs(station.location - (beam.length - end_zone)))]
    middle = [station for station in design.stations if end_zone < station.location < beam.length - end_zone]
    if not middle:
        middle = [min(design.stations, key=lambda station: abs(station.location - beam.length / 2.0))]

    return BeamKeyPositions(
        depth=beam.depth,
        end_zone=end_zone,
        left_top_flexural=_maximum(
            left, lambda station: station.top_flexural_area, lambda station: station.top_flexural_combo
        ),
        left_bottom_flexural=_maximum(
            left, lambda station: station.bottom_flexural_area, lambda station: station.bottom_flexural_combo
        ),
        left_shear=_maximum(left, lambda station: station.shear_area, lambda station: station.shear_combo),
        middle_top_flexural=_maximum(
            middle, lambda station: station.top_flexural_area, lambda station: station.top_flexural_combo
        ),
        middle_bottom_flexural=_maximum(
            middle, lambda station: station.bottom_flexural_area, lambda station: station.bottom_flexural_combo
        ),
        middle_shear=_maximum(middle, lambda station: station.shear_area, lambda station: station.shear_combo),
        right_top_flexural=_maximum(
            right, lambda station: station.top_flexural_area, lambda station: station.top_flexural_combo
        ),
        right_bottom_flexural=_maximum(
            right, lambda station: station.bottom_flexural_area, lambda station: station.bottom_flexural_combo
        ),
        right_shear=_maximum(right, lambda station: station.shear_area, lambda station: station.shear_combo),
    )


def _maximum(
    stations: list[DesignStation],
    area: Callable[[DesignStation], float],
    combo: Callable[[DesignStation], str],
) -> KeyDemand:
    station = max(stations, key=area)
    return KeyDemand(station.location, combo(station), area(station))
