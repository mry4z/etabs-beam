"""Independent construction-only side reinforcement.

This module has no longitudinal, transverse, torsion, or demand inputs.  Its
steel is reported separately and contributes no strength credit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol


class SkinSection(Protocol):
    depth_mm: float
    face_rebar_boundary_cover_mm: float


@dataclass(frozen=True)
class SkinConstructionPolicy:
    minimum_depth_mm: float = 800.0
    diameter_mm: int = 10
    maximum_spacing_mm: float = 250.0


@dataclass(frozen=True)
class SkinRebarResult:
    applies: bool
    diameter_mm: int
    count_each_side: int
    area_each_side_mm2: float
    status: str
    message: str


def select_skin_rebar(section: SkinSection, policy: SkinConstructionPolicy) -> SkinRebarResult:
    """Retain the approved depth, default diameter, spacing and count rules."""

    if section.depth_mm < policy.minimum_depth_mm:
        return SkinRebarResult(False, policy.diameter_mm, 0, 0.0, "NOT_APPLICABLE",
                               "face/腰筋仅按梁高及项目构造参数生成")
    face_length = section.depth_mm - 2.0 * section.face_rebar_boundary_cover_mm
    count = max(1, math.ceil(face_length / policy.maximum_spacing_mm) - 1)
    return SkinRebarResult(True, policy.diameter_mm, count,
                          count * math.pi * policy.diameter_mm**2 / 4.0,
                          "OK", "独立构造 face/腰筋；不参与顶底纵筋、剪扭需求或强度验算")
