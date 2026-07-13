"""Contracts shared by ETABS-result and future code-based design providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class DesignResultStatus(str, Enum):
    """Availability state of a beam's ETABS concrete-design result."""

    AVAILABLE = "有设计结果"
    NO_RESULTS = "无设计结果"
    SKIPPED = "不纳入混凝土设计"
    API_ERROR = "读取失败"
    PARTIAL = "部分梁段缺设计结果"


@dataclass(frozen=True)
class DesignStation:
    location: float
    top_flexural_combo: str
    top_flexural_area: float
    bottom_flexural_combo: str
    bottom_flexural_area: float
    shear_combo: str
    shear_area: float
    torsion_longitudinal_combo: str
    torsion_longitudinal_area: float
    torsion_trans_combo: str
    torsion_trans_area: float
    error: str = ""
    warning: str = ""


@dataclass(frozen=True)
class BeamDesignResult:
    beam_name: str
    stations: tuple[DesignStation, ...] = field(default_factory=tuple)
    status: DesignResultStatus = DesignResultStatus.AVAILABLE
    status_message: str = ""

    @property
    def has_results(self) -> bool:
        return bool(self.stations)

    @property
    def max_top_flexural_area(self) -> float | None:
        return max((station.top_flexural_area for station in self.stations), default=None)

    @property
    def max_bottom_flexural_area(self) -> float | None:
        return max((station.bottom_flexural_area for station in self.stations), default=None)

    @property
    def max_shear_area(self) -> float | None:
        return max((station.shear_area for station in self.stations), default=None)

    @property
    def max_torsion_longitudinal_area(self) -> float | None:
        return max((station.torsion_longitudinal_area for station in self.stations), default=None)

    @property
    def max_torsion_trans_area(self) -> float | None:
        return max((station.torsion_trans_area for station in self.stations), default=None)


class IDesignProvider(Protocol):
    def get_beam_design(self, beam_name: str) -> BeamDesignResult:
        """Return a normalized design result for one beam."""
