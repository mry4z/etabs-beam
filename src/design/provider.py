"""Contracts shared by ETABS-result and future code-based design providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class DesignStation:
    location: float
    top_combo: str
    top_area: float
    bottom_combo: str
    bottom_area: float
    shear_combo: str
    shear_area: float
    torsion_long_combo: str
    torsion_long_area: float
    torsion_trans_combo: str
    torsion_trans_area: float
    error: str = ""
    warning: str = ""


@dataclass(frozen=True)
class BeamDesignResult:
    beam_name: str
    stations: tuple[DesignStation, ...] = field(default_factory=tuple)

    @property
    def has_results(self) -> bool:
        return bool(self.stations)

    @property
    def max_top_area(self) -> float | None:
        return max((station.top_area for station in self.stations), default=None)

    @property
    def max_bottom_area(self) -> float | None:
        return max((station.bottom_area for station in self.stations), default=None)

    @property
    def max_shear_area(self) -> float | None:
        return max((station.shear_area for station in self.stations), default=None)

    @property
    def max_torsion_long_area(self) -> float | None:
        return max((station.torsion_long_area for station in self.stations), default=None)

    @property
    def max_torsion_trans_area(self) -> float | None:
        return max((station.torsion_trans_area for station in self.stations), default=None)


class IDesignProvider(Protocol):
    def get_beam_design(self, beam_name: str) -> BeamDesignResult:
        """Return a normalized design result for one beam."""
