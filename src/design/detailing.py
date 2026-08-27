"""Rule-driven beam reinforcement detailing.

This module deliberately consumes normalized demands rather than ETABS API
objects.  The ETABS provider and Excel writer can therefore remain unchanged
when a code edition or project bar-selection option is replaced.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable, Sequence

from src.design.bar_layout_capacity import single_layer_max_bars
from src.design.constructability import (
    CapacityShearReview,
    ConstructabilityResult,
    RebarLayoutGeometry,
    RuleCheck,
    SeismicDetailingContext,
    SupportFaceContext,
    build_constructability,
    calculate_smf_capacity_shear,
    evaluate_smf_capacity_shear,
    evaluate_ordinary_constructability,
    evaluate_smf_longitudinal,
)


class DetailingError(ValueError):
    """Raised when a beam cannot be detailed with the selected project rules."""


class ReinforcementMode(str, Enum):
    NORMAL = "NORMAL"
    FACE_TORSION = "FACE_TORSION_MODE"


class FaceRebarMode(str, Enum):
    NOT_APPLICABLE = "N.A."
    SKIN = "SKIN"
    TORSION = "TORSION"
    TORSION_AND_SKIN = "TORSION_AND_SKIN"


class ContinuityOption(str, Enum):
    OPTION_1_STRICT_EQUAL = "OPTION_1_STRICT_EQUAL"
    OPTION_2_COUNT_ADJUSTMENT = "OPTION_2_COUNT_ADJUSTMENT"


class BeamDetailingProfile(str, Enum):
    """Effective seismic frame type used by the detailing engine.

    ``beam_type`` (PB/SB) is deliberately not encoded in this enum.  It is a
    usage classification, while ETABS FrameType is the structural-system
    decision.  The legacy names remain aliases so old payload consumers can
    import them while new runs emit the exact effective type.
    """

    NON_SWAY = "NON_SWAY"
    OMF = "OMF"
    IMF = "IMF"
    SMF = "SMF"
    UNRESOLVED = "UNRESOLVED"

    # Backward-compatible aliases for older callers and snapshots.
    SB_ORDINARY_NO_CH18 = "SB_ORDINARY_NO_CH18"
    PB_OMF = "OMF"
    PB_SMF = "SMF"
    PB_REVIEW_REQUIRED = "UNRESOLVED"

    @property
    def is_smf(self) -> bool:
        return self is BeamDetailingProfile.SMF

    @property
    def is_secondary(self) -> bool:
        # Kept for compatibility with manually constructed legacy sections;
        # live payloads use BeamSection.beam_role as the authority.
        return self is BeamDetailingProfile.SB_ORDINARY_NO_CH18

    @property
    def is_ordinary(self) -> bool:
        return self is BeamDetailingProfile.NON_SWAY

    @property
    def is_omf(self) -> bool:
        return self is BeamDetailingProfile.OMF

    @property
    def is_imf(self) -> bool:
        return self is BeamDetailingProfile.IMF


def resolve_detailing_profile(
    beam_type: str,
    frame_types: Iterable[str],
) -> tuple[BeamDetailingProfile, str]:
    """Resolve ETABS FrameType without allowing PB/SB to override it.

    Explicit model types are preserved exactly.  Only the literal
    ``Program Determined`` value uses the project policy default of SMF.
    Missing or mixed source values remain unresolved and are fail-closed by
    the payload builder.
    """

    normalized_frames = tuple(
        dict.fromkeys(
            str(value).strip().casefold().replace("-", " ") or "unknown"
            for value in frame_types
        )
    )
    if len(normalized_frames) != 1:
        return BeamDetailingProfile.UNRESOLVED, "ETABS FrameType is mixed or missing"
    frame_type = normalized_frames[0]
    mapping = {
        "non sway": (BeamDetailingProfile.NON_SWAY, "model-defined Non Sway"),
        "non-sway": (BeamDetailingProfile.NON_SWAY, "model-defined Non Sway"),
        "sway ordinary": (BeamDetailingProfile.OMF, "model-defined Sway Ordinary"),
        "sway intermediate": (BeamDetailingProfile.IMF, "model-defined Sway Intermediate"),
        "sway special": (BeamDetailingProfile.SMF, "model-defined Sway Special"),
        "program determined": (BeamDetailingProfile.SMF, "project default: Program Determined → SMF"),
    }
    resolved = mapping.get(frame_type)
    if resolved is None or frame_type == "unknown":
        return BeamDetailingProfile.UNRESOLVED, f"unrecognized ETABS FrameType={frame_type!r}"
    return resolved


@dataclass(frozen=True)
class BarSpec:
    """One bar diameter/count group in one beam face or side."""

    diameter_mm: int
    count: int
    layers: tuple[int, ...]
    area_mm2: float
    role: str = "longitudinal"

    @property
    def total_area_mm2(self) -> float:
        return self.count * self.area_mm2

    @property
    def notation(self) -> str:
        base = f"{self.count}T{self.diameter_mm}"
        if len(self.layers) > 1:
            return f"{base} " + "/".join(str(value) for value in self.layers)
        return base


@dataclass(frozen=True)
class BarCatalog:
    """Immutable full diameter catalog; projects select a subset."""

    diameters_mm: tuple[int, ...] = (10, 12, 14, 16, 18, 20, 22, 25, 28, 30, 32, 36, 40)

    def __post_init__(self) -> None:
        if tuple(sorted(set(self.diameters_mm))) != self.diameters_mm:
            raise DetailingError("钢筋直径目录必须唯一且按升序排列。")
        if any(value <= 0 for value in self.diameters_mm):
            raise DetailingError("钢筋直径必须为正数。")

    def area(self, diameter_mm: int) -> float:
        if diameter_mm not in self.diameters_mm:
            raise DetailingError(f"钢筋直径 T{diameter_mm} 不在完整目录中。")
        return math.pi * diameter_mm**2 / 4.0


@dataclass(frozen=True)
class ProjectDetailingPolicy:
    """Project choices, separate from code clauses."""

    version: str = "nasiriyah-smf-sb-2026-08-24-r1"
    catalog: BarCatalog = field(default_factory=BarCatalog)
    enabled_diameters_mm: tuple[int, ...] = (10, 12, 16, 20, 25)
    allowed_spacings_mm: tuple[int, ...] = (100, 150, 200)
    preferred_stirrup_diameters_mm: tuple[int, ...] = (10, 12)
    preferred_longitudinal_diameters_mm: tuple[int, ...] = (20, 25)
    preferred_torsion_face_diameters_mm: tuple[int, ...] = (20, 25, 16, 12, 10)
    default_face_diameter_mm: int = 10
    face_min_depth_mm: float = 800.0
    face_max_spacing_mm: float = 250.0
    max_main_bar_layers: int = 2
    minimum_bars_per_layer: int = 2
    minimum_main_diameter_mm: int = 16
    smf_end_zone_min_flexural_diameter_mm: int = 20
    smf_end_max_spacing_mm: float = 100.0
    smf_max_longitudinal_ratio: float = 0.01613
    sb_hanger_diameter_mm: int = 12
    minimum_credit_hanger_diameter_mm: int = 12
    prefer_two_leg_max_width_mm: float = 300.0
    default_continuity_option: ContinuityOption = ContinuityOption.OPTION_1_STRICT_EQUAL

    def __post_init__(self) -> None:
        full = set(self.catalog.diameters_mm)
        enabled = set(self.enabled_diameters_mm)
        if not enabled or not enabled.issubset(full):
            raise DetailingError("项目启用钢筋直径必须是完整目录的非空子集。")
        if any(value not in enabled for value in self.preferred_stirrup_diameters_mm):
            raise DetailingError("箍筋优选直径必须属于项目启用目录。")
        if any(value not in enabled for value in self.preferred_longitudinal_diameters_mm):
            raise DetailingError("纵筋优选直径必须属于项目启用目录。")
        if any(value not in enabled for value in self.preferred_torsion_face_diameters_mm):
            raise DetailingError("抗扭 face bar 优选直径必须属于项目启用目录。")
        if self.default_face_diameter_mm not in enabled:
            raise DetailingError("默认 face bar 直径必须属于项目启用目录。")
        if self.sb_hanger_diameter_mm not in enabled:
            raise DetailingError("SB 架立筋直径必须属于项目启用目录。")
        if self.minimum_credit_hanger_diameter_mm not in enabled:
            raise DetailingError("最小配筋率补充架立筋直径必须属于项目启用目录。")
        if self.minimum_main_diameter_mm not in enabled:
            raise DetailingError("纵向主筋最小直径必须属于项目启用目录。")
        if self.sb_hanger_diameter_mm >= self.minimum_main_diameter_mm:
            raise DetailingError("SB 架立筋直径必须小于纵向主筋最小直径。")
        if not self.allowed_spacings_mm or any(value <= 0 for value in self.allowed_spacings_mm):
            raise DetailingError("项目箍筋间距目录不能为空且必须为正数。")
        if self.smf_end_max_spacing_mm <= 0:
            raise DetailingError("SMF 端部项目箍筋间距上限必须为正数。")
        if not 0 < self.smf_max_longitudinal_ratio <= 0.025:
            raise DetailingError("SMF 项目最大纵筋率必须位于 (0, 0.025]。")
        if self.max_main_bar_layers < 1:
            raise DetailingError("最大纵筋排数必须至少为一排。")
        if self.minimum_bars_per_layer < 2:
            raise DetailingError("纵筋每排最少根数不得小于 2。")

    def ordered_stirrup_diameters(self) -> tuple[int, ...]:
        return _preferred_order(self.preferred_stirrup_diameters_mm, self.enabled_diameters_mm)

    def ordered_longitudinal_diameters(self) -> tuple[int, ...]:
        # Larger bars first within the user-selected preference group.  This
        # reproduces the project's T25-first examples while retaining a
        # configurable preference interface.
        ordered = _preferred_order(self.preferred_longitudinal_diameters_mm, self.enabled_diameters_mm)
        return tuple(
            sorted(
                (value for value in ordered if value >= self.minimum_main_diameter_mm),
                reverse=True,
            )
        )

    def minimum_main_diameter_for(self, section: Any) -> int:
        """Return the active primary-flexural diameter floor for one section."""

        if getattr(section, "is_smf", False):
            return max(self.minimum_main_diameter_mm, self.smf_end_zone_min_flexural_diameter_mm)
        return self.minimum_main_diameter_mm

    def ordered_longitudinal_diameters_for(self, section: Any) -> tuple[int, ...]:
        floor = self.minimum_main_diameter_for(section)
        return tuple(value for value in self.ordered_longitudinal_diameters() if value >= floor)

    def ordered_torsion_face_diameters(self) -> tuple[int, ...]:
        return _preferred_order(self.preferred_torsion_face_diameters_mm, self.enabled_diameters_mm)


@dataclass(frozen=True)
class ACI318_14_DetailingPolicy:
    """Code-specific limits used by the first engine implementation."""

    edition: str = "ACI 318-14"
    version: str = "aci318-14-si-errata"

    def minimum_longitudinal_area(
        self, width_mm: float, effective_depth_mm: float, fc_mpa: float, fy_mpa: float
    ) -> float:
        if min(width_mm, effective_depth_mm, fc_mpa, fy_mpa) <= 0:
            raise DetailingError("AsMin 输入必须为正数。")
        return max(
            0.25 * math.sqrt(fc_mpa) / fy_mpa * width_mm * effective_depth_mm,
            1.4 / fy_mpa * width_mm * effective_depth_mm,
        )

    def minimum_clear_spacing(self, diameter_mm: float, aggregate_mm: float) -> float:
        return max(25.0, float(diameter_mm), 4.0 * aggregate_mm / 3.0)

    def effective_depth(self, section: "BeamSection", stirrup_diameter_mm: float, main_diameter_mm: float) -> float:
        return section.effective_depth_mm(
            stirrup_diameter_mm,
            main_diameter_mm,
        )

    def hoop_perimeter(
        self,
        section: "BeamSection",
        stirrup_diameter_mm: float,
        main_diameter_mm: float,
    ) -> float:
        centerline_cover = section.stirrup_centerline_cover_mm(
            stirrup_diameter_mm,
            main_diameter_mm,
        )
        width = section.width_mm - 2.0 * centerline_cover
        depth = section.depth_mm - 2.0 * centerline_cover
        return max(0.0, 2.0 * (width + depth))

    def maximum_stirrup_spacing(
        self,
        section: "BeamSection",
        demand: "BeamRegionDemand",
        stirrup_diameter_mm: float,
        main_diameter_mm: float,
        smf_end_project_limit_mm: float | None = None,
    ) -> tuple[float, str, tuple[tuple[str, float], ...], bool]:
        effective_depth = self.effective_depth(section, stirrup_diameter_mm, main_diameter_mm)
        if effective_depth <= 0:
            return 0.0, "INVALID_EFFECTIVE_DEPTH", (("effective_depth", 0.0),), False
        ph = self.hoop_perimeter(
            section,
            stirrup_diameter_mm,
            main_diameter_mm,
        )
        high_shear_limit = 0.33 * math.sqrt(section.fc_mpa) * section.width_mm / section.fyt_mpa
        high_shear = demand.transverse_required_av_over_s_mm2_per_mm > high_shear_limit + 1e-9
        limits: list[tuple[str, float]] = [
            (
                "SHEAR_HIGH_D_OVER_4" if high_shear else "SHEAR_LOW_D_OVER_2",
                effective_depth / (4.0 if high_shear else 2.0),
            ),
            ("SHEAR_HIGH_ABSOLUTE" if high_shear else "SHEAR_LOW_ABSOLUTE", 300.0 if high_shear else 600.0),
        ]
        if demand.at_over_s_mm2_per_mm > 0:
            limits.extend((("TORSION_PH_OVER_8", ph / 8.0), ("TORSION_ABSOLUTE", 300.0)))
        if section.is_smf and demand.name in {"left", "right"}:
            limits.extend(
                (
                    ("SMF_D_OVER_4", effective_depth / 4.0),
                    ("SMF_6DB_FINAL", 6.0 * main_diameter_mm),
                    ("SMF_ABSOLUTE", 150.0),
                )
            )
            if smf_end_project_limit_mm is not None:
                limits.append(("SMF_PROJECT_ABSOLUTE", smf_end_project_limit_mm))
        governing_reason, maximum = min(limits, key=lambda item: item[1])
        return maximum, governing_reason, tuple(limits), high_shear

    def minimum_transverse_area_per_spacing(self, section: "BeamSection") -> float:
        """ACI 318-14 SI minimum Av/s for nonprestressed beam shear reinforcement."""

        return max(
            0.062 * math.sqrt(section.fc_mpa) * section.width_mm / section.fyt_mpa,
            0.35 * section.width_mm / section.fyt_mpa,
        )

    def minimum_legs(self, section: "BeamSection", region: str) -> int:
        # ACI 318-14 requires closed hoops and lateral support of the
        # longitudinal bars; it does not prescribe a universal four-leg
        # minimum.  The actual leg count is selected from shear area and the
        # layout solver.  Four or more legs are therefore still possible when
        # the section width/demand requires them, but never imposed as a proxy
        # for the SMF hoop topology.
        return 2


@dataclass(frozen=True)
class BeamRegionDemand:
    name: str
    location_mm: float
    as_top_total_mm2: float
    as_bottom_total_mm2: float
    av_over_s_mm2_per_mm: float
    at_over_s_mm2_per_mm: float
    al_torsion_mm2: float
    as_top_etabs_mm2: float | None = None
    as_bottom_etabs_mm2: float | None = None
    top_combo: str = ""
    bottom_combo: str = ""
    shear_combo: str = ""
    torsion_combo: str = ""
    source_objects: tuple[str, ...] = ()
    units: str = "N_mm_C"
    shear_source: str = "GetSummaryResultsBeam_2.VMajorArea"
    as_flex_top_override_mm2: float | None = None
    as_flex_bottom_override_mm2: float | None = None
    capacity_shear_av_over_s_mm2_per_mm: float | None = None
    capacity_shear_demand_kN: float | None = None
    capacity_shear_source: str = ""
    capacity_shear_vc_zero: bool = False

    def __post_init__(self) -> None:
        for label, value in (
            ("AsTopTotal", self.as_top_total_mm2),
            ("AsBotTotal", self.as_bottom_total_mm2),
            ("Av/s", self.av_over_s_mm2_per_mm),
            ("At/s", self.at_over_s_mm2_per_mm),
            ("Al", self.al_torsion_mm2),
        ):
            if value < 0:
                raise DetailingError(f"{label} 不能为负数。")
        if self.as_flex_top_override_mm2 is not None and self.as_flex_top_override_mm2 < 0:
            raise DetailingError("AsFlexTop override 不能为负数。")
        if self.as_flex_bottom_override_mm2 is not None and self.as_flex_bottom_override_mm2 < 0:
            raise DetailingError("AsFlexBottom override 不能为负数。")
        if self.capacity_shear_av_over_s_mm2_per_mm is not None and self.capacity_shear_av_over_s_mm2_per_mm < 0:
            raise DetailingError("capacity shear Av/s 不能为负数。")

    @property
    def as_flex_top_mm2(self) -> float:
        if self.as_flex_top_override_mm2 is not None:
            return self.as_flex_top_override_mm2
        return max(0.0, self.as_top_total_mm2 - self.al_torsion_mm2 / 2.0)

    @property
    def as_flex_bottom_mm2(self) -> float:
        if self.as_flex_bottom_override_mm2 is not None:
            return self.as_flex_bottom_override_mm2
        return max(0.0, self.as_bottom_total_mm2 - self.al_torsion_mm2 / 2.0)

    @property
    def transverse_required_av_over_s_mm2_per_mm(self) -> float:
        """Return the governing ETABS/code or SMF capacity-shear Av/s."""

        return max(
            self.av_over_s_mm2_per_mm,
            self.capacity_shear_av_over_s_mm2_per_mm or 0.0,
        )

    @property
    def transverse_shear_source(self) -> str:
        if self.capacity_shear_av_over_s_mm2_per_mm is None:
            return self.shear_source
        return f"{self.shear_source}; {self.capacity_shear_source or 'SMF capacity shear'}"


@dataclass(frozen=True)
class BeamSection:
    name: str
    width_mm: float
    depth_mm: float
    clear_cover_to_stirrup_outer_mm: float | None
    fc_mpa: float
    fy_mpa: float
    fyt_mpa: float
    max_aggregate_size_mm: float
    length_mm: float
    detailing_profile: BeamDetailingProfile = BeamDetailingProfile.PB_OMF
    etabs_longitudinal_cover_mm: float | None = None
    etabs_longitudinal_cover_source: str = ""
    clear_cover_source: str = ""
    max_aggregate_size_source: str = ""
    profile_source: str = ""
    beam_role: str = "PB"

    def __post_init__(self) -> None:
        values = (
            self.width_mm,
            self.depth_mm,
            self.fc_mpa,
            self.fy_mpa,
            self.fyt_mpa,
            self.max_aggregate_size_mm,
            self.length_mm,
        )
        if any(value <= 0 for value in values):
            raise DetailingError("梁截面、材料、骨料和长度必须为正数。")
        if self.clear_cover_to_stirrup_outer_mm is not None and self.clear_cover_to_stirrup_outer_mm <= 0:
            raise DetailingError("箍筋外缘净保护层必须为正数。")
        if self.etabs_longitudinal_cover_mm is not None and self.etabs_longitudinal_cover_mm <= 0:
            raise DetailingError("ETABS 纵筋设计 cover 必须为正数。")
        if self.clear_cover_to_stirrup_outer_mm is None and self.etabs_longitudinal_cover_mm is None:
            raise DetailingError("必须提供 ETABS 纵筋重心 cover 或箍筋外缘净保护层。")

    @property
    def cover_mm(self) -> float:
        """Backward-compatible alias for the active project cover value.

        New calculations use the semantic methods below.  This alias remains
        only for integrations that display a single cover value.
        """

        if self.clear_cover_to_stirrup_outer_mm is not None:
            return self.clear_cover_to_stirrup_outer_mm
        if self.etabs_longitudinal_cover_mm is None:  # pragma: no cover - guarded above
            raise DetailingError("COVER_SEMANTICS_UNRESOLVED")
        return self.etabs_longitudinal_cover_mm

    @property
    def cover_calculation_basis(self) -> str:
        """Return the dimensional basis used by the detailing engine."""

        if self.clear_cover_to_stirrup_outer_mm is not None:
            return "CLEAR_TO_STIRRUP_OUTER"
        return "ETABS_LONGITUDINAL_CENTROID"

    def effective_depth_mm(
        self,
        stirrup_diameter_mm: float,
        main_diameter_mm: float,
    ) -> float:
        """Return flexural ``d`` using the active cover semantics.

        ETABS ``GetRebarBeam`` defines CoverTop/CoverBot as the distance from
        the concrete face to the centroid of the longitudinal reinforcement.
        It can therefore be used directly.  The explicit-clear-cover path is
        retained for projects that separately confirm cover to stirrup outer.
        """

        if self.clear_cover_to_stirrup_outer_mm is None:
            return self.depth_mm - self.cover_mm
        return self.depth_mm - self.clear_cover_to_stirrup_outer_mm - stirrup_diameter_mm - main_diameter_mm / 2.0

    def clear_cover_for_layout_mm(
        self,
        stirrup_diameter_mm: float,
        main_diameter_mm: float,
    ) -> float:
        """Return cover to stirrup outer for one candidate bar arrangement."""

        if self.clear_cover_to_stirrup_outer_mm is not None:
            return self.clear_cover_to_stirrup_outer_mm
        return self.cover_mm - stirrup_diameter_mm - main_diameter_mm / 2.0

    def stirrup_centerline_cover_mm(
        self,
        stirrup_diameter_mm: float,
        main_diameter_mm: float,
    ) -> float:
        """Return concrete-face to stirrup-centerline distance."""

        if self.clear_cover_to_stirrup_outer_mm is not None:
            return self.clear_cover_to_stirrup_outer_mm + stirrup_diameter_mm / 2.0
        return self.cover_mm - main_diameter_mm / 2.0 - stirrup_diameter_mm / 2.0

    @property
    def face_rebar_boundary_cover_mm(self) -> float:
        """Return the cover used to bound side-face bar distribution."""

        return self.cover_mm

    @property
    def aggregate_mm(self) -> float:
        """Backward-compatible alias for the confirmed maximum aggregate size."""

        return self.max_aggregate_size_mm

    @property
    def is_smf(self) -> bool:
        return self.detailing_profile.is_smf

    @property
    def is_secondary(self) -> bool:
        return (
            str(self.beam_role).strip().upper() == "SB"
            or self.detailing_profile.is_secondary
        )


@dataclass(frozen=True)
class BeamDetailingInput:
    section: BeamSection
    regions: tuple[BeamRegionDemand, ...]
    left_support: SupportFaceContext | None = None
    right_support: SupportFaceContext | None = None
    seismic_context: SeismicDetailingContext | None = None
    yield_locations_mm: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        expected = ("left", "middle", "right")
        if tuple(region.name for region in self.regions) != expected:
            raise DetailingError("梁配筋输入必须按 left、middle、right 三个区域提供。")


@dataclass(frozen=True)
class StirrupDesign:
    diameter_mm: int
    spacing_mm: int
    legs: int
    provided_av_over_s_mm2_per_mm: float
    provided_at_over_s_mm2_per_mm: float
    maximum_spacing_mm: float
    governing_reason: str
    required_av_over_s_mm2_per_mm: float = 0.0
    minimum_av_over_s_mm2_per_mm: float = 0.0
    required_combo_over_s_mm2_per_mm: float = 0.0
    final_main_bar_diameter_mm: int | None = None
    high_shear: bool = False
    spacing_limits: tuple[tuple[str, float], ...] = ()

    @property
    def notation(self) -> str:
        return f"T{self.diameter_mm}@{self.spacing_mm}"


@dataclass(frozen=True)
class LongitudinalDesign:
    bars: tuple[BarSpec, ...]
    required_area_mm2: float
    provided_area_mm2: float
    layers: int
    clear_spacing_mm: float
    hanger_bars: BarSpec | None = None

    @property
    def notation(self) -> str:
        flexural = "+".join(bar.notation for bar in self.bars)
        if self.hanger_bars is None:
            return flexural
        return f"{flexural}+({self.hanger_bars.notation})"


@dataclass(frozen=True)
class FaceRebarDesign:
    mode: FaceRebarMode
    bars_each_side: BarSpec | None
    required_each_side_mm2: float
    provided_each_side_mm2: float
    control_reason: str

    @property
    def notation(self) -> str:
        if self.mode is FaceRebarMode.NOT_APPLICABLE:
            return "N.A."
        return self.bars_each_side.notation if self.bars_each_side else "N.A."


@dataclass(frozen=True)
class RegionDetailing:
    name: str
    demand: BeamRegionDemand
    as_min_mm2: float
    as_flex_top_mm2: float
    as_flex_bottom_mm2: float
    stirrup: StirrupDesign
    top: LongitudinalDesign
    bottom: LongitudinalDesign


@dataclass(frozen=True)
class BeamDetailingResult:
    beam_name: str
    code_edition: str
    code_policy_version: str
    project_policy_version: str
    detailing_profile: BeamDetailingProfile
    section: BeamSection
    mode: ReinforcementMode
    regions: tuple[RegionDetailing, ...]
    face_rebar: FaceRebarDesign
    warnings: tuple[str, ...] = ()
    profile_source: str = ""
    iteration_status: str = "STABLE"
    iteration_count: int = 1
    continuity_option: ContinuityOption | None = None
    coordination_status: str = "NOT_APPLIED"
    constructability: ConstructabilityResult | None = None
    smf_checks: tuple[RuleCheck, ...] = ()
    capacity_shear_review: CapacityShearReview | None = None
    seismic_context: SeismicDetailingContext | None = None

    @property
    def errors(self) -> tuple[str, ...]:
        if self.constructability is None:
            return ()
        return self.constructability.blocking_statuses


class BeamDetailingEngine:
    """Design left/middle/right beam reinforcement from normalized demands."""

    def __init__(
        self,
        code_policy: ACI318_14_DetailingPolicy | None = None,
        project_policy: ProjectDetailingPolicy | None = None,
    ) -> None:
        self.code = code_policy or ACI318_14_DetailingPolicy()
        self.project = project_policy or ProjectDetailingPolicy()

    def design(self, input_data: BeamDetailingInput) -> BeamDetailingResult:
        section = input_data.section
        if section.detailing_profile is BeamDetailingProfile.PB_REVIEW_REQUIRED:
            raise DetailingError("DETAILING_PROFILE_REVIEW_REQUIRED")
        # ETABS Av/s remains the source demand.  SMF capacity shear can add a
        # separate governing Av/s after the actual end layouts are known.
        active_regions = tuple(input_data.regions)
        capacity_review: CapacityShearReview | None = None
        # Use the largest enabled diameter only for the preliminary selection,
        # then re-evaluate every region against the actual final flexural db.
        # A result that cannot satisfy the final-db check fails closed below.
        max_main = max(self.project.enabled_diameters_mm)
        if section.is_secondary:
            # SB uses one stirrup specification for the entire structural span.
            # Select it against the envelope of all three regions instead of
            # post-processing independently selected left/middle/right results.
            span_stirrup = self._select_secondary_span_stirrup(
                section,
                active_regions,
                (max_main,) * len(active_regions),
            )
            selected_stirrups = [span_stirrup for _ in active_regions]
        else:
            selected_stirrups = [self._select_stirrup(section, demand, max_main) for demand in active_regions]
            # The PB project schedule has one ``Legs`` field while retaining
            # separate support/middle spacings.  Use the controlling legs for
            # the whole span and recalculate provided capacity.
            span_legs = max(stirrup.legs for stirrup in selected_stirrups)
            selected_stirrups = [self._with_legs(stirrup, span_legs) for stirrup in selected_stirrups]
        mode: ReinforcementMode | None = None
        regions: tuple[RegionDetailing, ...] = ()
        max_rounds = 6
        iteration_count = 0
        iteration_status = "STABLE"
        seen_stirrup_signatures: set[tuple[tuple[int, int, int], ...]] = set()
        for iteration_count in range(1, max_rounds + 1):
            current_signature = tuple(map(_stirrup_signature, selected_stirrups))
            seen_stirrup_signatures.add(current_signature)
            active_input = replace(input_data, regions=active_regions)
            mode, regions = self._design_regions_with_stirrups(
                active_input,
                selected_stirrups,
            )
            regions = self._coordinate_intraspan_longitudinal(
                section,
                regions,
                mode,
            )
            capacity_review = self._calculate_capacity_review(
                section,
                regions,
                active_input.seismic_context,
            )
            active_regions = self._apply_capacity_shear_demands(
                input_data.regions,
                active_regions,
                capacity_review,
                input_data,
            )
            final_diameters = tuple(self._final_main_db(region, section) for region in regions)
            if section.is_secondary:
                span_stirrup = self._select_secondary_span_stirrup(
                    section,
                    active_regions,
                    final_diameters,
                )
                next_stirrups = [span_stirrup for _ in active_regions]
            else:
                next_stirrups = [
                    self._select_stirrup(section, demand, final_db)
                    for demand, final_db in zip(active_regions, final_diameters)
                ]
                span_legs = max(stirrup.legs for stirrup in next_stirrups)
                next_stirrups = [self._with_legs(stirrup, span_legs) for stirrup in next_stirrups]
            next_signature = tuple(map(_stirrup_signature, next_stirrups))
            if next_signature == current_signature:
                break
            if next_signature in seen_stirrup_signatures:
                # A finite catalogue can produce a two-state preference
                # cycle: more stirrup legs permit smaller longitudinal bars;
                # the final-db limit then favors a tighter lower-leg stirrup,
                # which selects larger longitudinal bars. Keep the current
                # state only if it independently satisfies every final-db
                # control. This is a conservative joint solution.
                # Capacity-shear feedback can legitimately alternate between
                # a larger primary bar (higher Mpr) and a tighter hoop.  Merge
                # both states into one conservative catalogue choice so the
                # final layout cannot oscillate or silently under-design the
                # SMF end zone.
                selected_stirrups = [
                    self._conservative_stirrup(current, following)
                    for current, following in zip(selected_stirrups, next_stirrups)
                ]
                iteration_status = "STABLE_CONSERVATIVE_CYCLE_BREAK"
                break
            selected_stirrups = next_stirrups
        else:
            raise DetailingError(f"{section.name}: DETAILING_ITERATION_NOT_CONVERGED")

        if mode is None:
            raise DetailingError(f"{section.name}: DETAILING_RESULT_MISSING")
        # The governing capacity-shear Av/s may be equal to the preliminary
        # stirrup catalogue choice.  In that case the loop legitimately
        # converges in its first pass, but ``regions`` was built from the
        # pre-capacity demand snapshot.  Rebuild once from ``active_regions``
        # so the delivered result and workbook retain the exact demand source
        # that was used for the final stirrup check.
        active_input = replace(input_data, regions=active_regions)
        mode, regions = self._design_regions_with_stirrups(active_input, selected_stirrups)
        regions = self._coordinate_intraspan_longitudinal(section, regions, mode)
        regions = self._finalize_stirrup_controls(section, regions)
        capacity_review = self._calculate_capacity_review(
            section,
            regions,
            input_data.seismic_context,
        )
        final_seismic_context = self._context_with_capacity_review(
            input_data.seismic_context,
            capacity_review,
        )

        face = self._select_face_rebar(section, input_data.regions, mode)
        final_stirrup_diameter = max(region.stirrup.diameter_mm for region in regions)
        final_main_diameter = min(self._final_main_db(region, section) for region in regions)
        effective_depth = min(
            self.code.effective_depth(section, region.stirrup.diameter_mm, self._final_main_db(region, section))
            for region in regions
        )
        layout_geometry = RebarLayoutGeometry(
            width_mm=section.width_mm,
            depth_mm=section.depth_mm,
            effective_depth_mm=effective_depth,
            stirrup_diameter_mm=final_stirrup_diameter,
            clear_cover_mm=section.clear_cover_for_layout_mm(final_stirrup_diameter, final_main_diameter),
            top_layers=tuple(tuple(float(value) for value in region.top.bars[0].layers) for region in regions),
            bottom_layers=tuple(tuple(float(value) for value in region.bottom.bars[0].layers) for region in regions),
            coordinates_resolved=False,
        )
        constructability = build_constructability(
            is_smf=section.is_smf,
            span_length_mm=section.length_mm,
            beam_depth_mm=section.depth_mm,
            effective_depth_mm=effective_depth,
            smallest_primary_bar_diameter_mm=min(self._final_main_db(region, section) for region in regions),
            left_support=input_data.left_support,
            right_support=input_data.right_support,
            left_spacing_mm=regions[0].stirrup.spacing_mm,
            middle_spacing_mm=regions[1].stirrup.spacing_mm,
            right_spacing_mm=regions[2].stirrup.spacing_mm,
            allowed_spacings_mm=self.project.allowed_spacings_mm,
            smf_end_project_limit_mm=self.project.smf_end_max_spacing_mm,
            yield_locations_mm=(
                input_data.yield_locations_mm
                or (
                    final_seismic_context.likely_yield_locations_mm
                    if final_seismic_context is not None
                    else ()
                )
            ),
            seismic_context=final_seismic_context,
            beam_width_mm=section.width_mm,
            concrete_strength_mpa=section.fc_mpa,
            layout_geometry=layout_geometry,
        )
        smf_checks = (
            evaluate_smf_longitudinal(
                width_mm=section.width_mm,
                fc_mpa=section.fc_mpa,
                fy_mpa=section.fy_mpa,
                effective_depth_mm=effective_depth,
                left_top=regions[0].top,
                left_bottom=regions[0].bottom,
                middle_top=regions[1].top,
                middle_bottom=regions[1].bottom,
                right_top=regions[2].top,
                right_bottom=regions[2].bottom,
                maximum_ratio=self.project.smf_max_longitudinal_ratio,
            )
            if section.is_smf
            else ()
        )
        if not section.is_smf:
            ordinary_checks = evaluate_ordinary_constructability(
                depth_mm=section.depth_mm,
                mode=mode.value,
                torsion_longitudinal_area_mm2=max(
                    (region.demand.al_torsion_mm2 for region in regions),
                    default=0.0,
                ),
            )
            blocking = tuple(
                check.status
                for check in ordinary_checks
                if check.status not in {"PASS", "INFO", "WARN"}
            )
            constructability = replace(
                constructability,
                status=("REVIEW" if blocking else constructability.status),
                checks=tuple((*constructability.checks, *ordinary_checks)),
            )
        extra_warnings = list(constructability.blocking_statuses)
        extra_warnings.extend(
            check.status for check in smf_checks if check.status not in {"PASS", "INFO", "WARN"}
        )
        if section.is_smf and (
            final_seismic_context is None
            or (
                capacity_review is not None
                and capacity_review.status not in {"PASS", "NOT_APPLICABLE"}
            )
        ):
            extra_warnings.append("CAPACITY_SHEAR_SOURCE_UNVERIFIED")
        warnings = tuple(
            sorted(
                set(
                    (
                        *(
                            "FLEXURAL_DEMAND_BELOW_AS_MIN"
                            for region in regions
                            if (
                                region.demand.as_flex_top_mm2 < region.as_min_mm2
                                or region.demand.as_flex_bottom_mm2 < region.as_min_mm2
                            )
                        ),
                        *extra_warnings,
                    )
                )
            )
        )
        return BeamDetailingResult(
            beam_name=section.name,
            code_edition=self.code.edition,
            code_policy_version=self.code.version,
            project_policy_version=self.project.version,
            detailing_profile=section.detailing_profile,
            section=section,
            mode=mode,
            regions=regions,
            face_rebar=face,
            warnings=tuple(sorted(set(warnings))),
            profile_source=section.profile_source,
            iteration_status=iteration_status,
            iteration_count=iteration_count,
            constructability=constructability,
            smf_checks=tuple(smf_checks),
            capacity_shear_review=capacity_review,
            seismic_context=input_data.seismic_context,
        )

    def refresh_capacity_shear_after_layout_change(
        self,
        result: BeamDetailingResult,
    ) -> BeamDetailingResult:
        """Recheck SMF capacity shear after adjacent-span bar coordination.

        Coordination changes only longitudinal layouts, so the existing
        stirrup catalogue choice can be retained only when the new
        ``Vu_final`` still passes it.  A failed finalization raises and the
        caller blocks the beam-line result instead of publishing stale
        capacity-shear evidence.
        """

        if not result.section.is_smf or result.seismic_context is None:
            return result
        review = self._calculate_capacity_review(
            result.section,
            result.regions,
            result.seismic_context,
        )
        required = review.required_av_over_s_mm2_per_mm if review is not None else None
        affected = {"left", "right"}
        if result.constructability is not None and any(
            zone.name == "smf_end_hoop_overlap" for zone in result.constructability.zones
        ):
            affected = {"left", "middle", "right"}
        regions: list[RegionDetailing] = []
        for region in result.regions:
            demand = replace(
                region.demand,
                capacity_shear_av_over_s_mm2_per_mm=(required if required is not None and region.name in affected else None),
                capacity_shear_demand_kN=(
                    review.final_capacity_shear_kN
                    if review is not None and region.name in affected
                    else None
                ),
                capacity_shear_source=(review.control_source if review is not None and region.name in affected else ""),
                capacity_shear_vc_zero=(review.vc_zero if review is not None and region.name in affected else False),
            )
            regions.append(replace(region, demand=demand))
        finalized = self._finalize_stirrup_controls(result.section, tuple(regions))
        constructability = result.constructability
        if constructability is not None:
            final_context = self._context_with_capacity_review(result.seismic_context, review)
            capacity_checks = evaluate_smf_capacity_shear(
                span_length_mm=result.section.length_mm,
                context=final_context or result.seismic_context,
            )
            constructability = replace(
                constructability,
                checks=tuple(
                    check
                    for check in constructability.checks
                    if check.rule_id != "SMF-CAPACITY-SHEAR"
                ) + tuple(capacity_checks),
            )
        return replace(
            result,
            regions=finalized,
            capacity_shear_review=review,
            constructability=constructability,
        )

    def _calculate_capacity_review(
        self,
        section: BeamSection,
        regions: tuple[RegionDetailing, ...],
        context: SeismicDetailingContext | None,
    ) -> CapacityShearReview | None:
        if not section.is_smf or context is None or len(regions) < 3:
            return None
        effective_depth = min(
            self.code.effective_depth(section, region.stirrup.diameter_mm, self._final_main_db(region, section))
            for region in regions
        )
        return calculate_smf_capacity_shear(
            span_length_mm=section.length_mm,
            beam_width_mm=section.width_mm,
            fc_mpa=section.fc_mpa,
            fy_mpa=section.fy_mpa,
            fyt_mpa=section.fyt_mpa,
            effective_depth_mm=effective_depth,
            left_region=regions[0],
            right_region=regions[-1],
            context=context,
        )

    @staticmethod
    def _capacity_region_names(input_data: BeamDetailingInput) -> set[str]:
        names = {"left", "right"}
        left = input_data.left_support
        right = input_data.right_support
        if (
            left is not None
            and right is not None
            and left.resolved
            and right.resolved
            and right.face_location_mm - left.face_location_mm
            <= 4.0 * input_data.section.depth_mm + 1e-9
        ):
            # The two 2h regions overlap; the whole calculated span is an
            # end-zone demand for the purpose of the three-point selector.
            names = {"left", "middle", "right"}
        return names

    def _apply_capacity_shear_demands(
        self,
        base_regions: tuple[BeamRegionDemand, ...],
        _prior_regions: tuple[BeamRegionDemand, ...],
        review: CapacityShearReview | None,
        input_data: BeamDetailingInput,
    ) -> tuple[BeamRegionDemand, ...]:
        required = review.required_av_over_s_mm2_per_mm if review is not None else None
        affected = self._capacity_region_names(input_data) if required is not None else set()
        return tuple(
            replace(
                demand,
                capacity_shear_av_over_s_mm2_per_mm=(required if demand.name in affected else None),
                capacity_shear_demand_kN=(
                    review.final_capacity_shear_kN if demand.name in affected and review else None
                ),
                capacity_shear_source=(review.control_source if demand.name in affected and review else ""),
                capacity_shear_vc_zero=(review.vc_zero if demand.name in affected and review else False),
            )
            for demand in base_regions
        )

    @staticmethod
    def _context_with_capacity_review(
        context: SeismicDetailingContext | None,
        review: CapacityShearReview | None,
    ) -> SeismicDetailingContext | None:
        if context is None or review is None:
            return context
        return replace(
            context,
            left_mpr_kNm=(review.left_mpr_kNm if review.left_mpr_kNm is not None else context.left_mpr_kNm),
            right_mpr_kNm=(review.right_mpr_kNm if review.right_mpr_kNm is not None else context.right_mpr_kNm),
            capacity_shear_demand_kN=(
                review.etabs_capacity_shear_kN
                if review.etabs_capacity_shear_kN is not None
                else context.capacity_shear_demand_kN
            ),
            gravity_shear_kN=(
                review.gravity_shear_kN
                if review.gravity_shear_kN is not None
                else context.gravity_shear_kN
            ),
            capacity_shear_final_kN=review.final_capacity_shear_kN,
            capacity_shear_required_av_over_s_mm2_per_mm=review.required_av_over_s_mm2_per_mm,
            capacity_shear_control_source=review.control_source,
        )

    def _design_regions_with_stirrups(
        self,
        input_data: BeamDetailingInput,
        selected_stirrups: Sequence[StirrupDesign],
    ) -> tuple[ReinforcementMode, tuple[RegionDetailing, ...]]:
        section = input_data.section
        normal_regions: list[RegionDetailing] = []
        normal_fails = False
        minimum_main_count = 2 if section.is_secondary else None
        for demand, stirrup in zip(input_data.regions, selected_stirrups):
            effective_depth = self.code.effective_depth(
                section,
                stirrup.diameter_mm,
                self.project.minimum_main_diameter_for(section),
            )
            as_min = self.code.minimum_longitudinal_area(
                section.width_mm, effective_depth, section.fc_mpa, section.fy_mpa
            )
            top = self._select_longitudinal(
                section,
                max(demand.as_top_total_mm2, as_min),
                minimum_main_count or stirrup.legs,
                stirrup.diameter_mm,
                max_layers=self.project.max_main_bar_layers,
                hanger_support_count=stirrup.legs if section.is_secondary else None,
                allow_minimum_credit_hanger=(section.is_secondary and demand.as_flex_top_mm2 <= as_min + 1e-9),
            )
            bottom = self._select_longitudinal(
                section,
                max(demand.as_bottom_total_mm2, as_min),
                minimum_main_count or stirrup.legs,
                stirrup.diameter_mm,
                max_layers=self.project.max_main_bar_layers,
                hanger_support_count=stirrup.legs if section.is_secondary else None,
                allow_minimum_credit_hanger=(section.is_secondary and demand.as_flex_bottom_mm2 <= as_min + 1e-9),
            )
            if top is None or bottom is None:
                normal_fails = True
            else:
                normal_regions.append(
                    RegionDetailing(
                        demand.name,
                        demand,
                        as_min,
                        demand.as_flex_top_mm2,
                        demand.as_flex_bottom_mm2,
                        stirrup,
                        top,
                        bottom,
                    )
                )

        mode = ReinforcementMode.FACE_TORSION if normal_fails else ReinforcementMode.NORMAL
        if mode is ReinforcementMode.NORMAL:
            regions = tuple(normal_regions)
        else:
            regions = tuple(
                self._redesign_face_region(
                    section,
                    demand,
                    stirrup,
                    self.project.minimum_main_diameter_for(section),
                )
                for demand, stirrup in zip(input_data.regions, selected_stirrups)
            )
        return mode, regions

    def _final_main_db(self, region: RegionDetailing, section: BeamSection | None = None) -> int:
        minimum = (
            self.project.minimum_main_diameter_for(section)
            if section is not None
            else self.project.minimum_main_diameter_mm
        )
        return min(
            bar.diameter_mm
            for design in (region.top, region.bottom)
            for bar in design.bars
            if bar.diameter_mm >= minimum
        )

    def _finalize_stirrup_controls(
        self,
        section: BeamSection,
        regions: tuple[RegionDetailing, ...],
    ) -> tuple[RegionDetailing, ...]:
        """Recheck every region with its final as-detailed longitudinal db."""

        output: list[RegionDetailing] = []
        for region in regions:
            final_db = self._final_main_db(region, section)
            maximum, reason, limits, high_shear = self.code.maximum_stirrup_spacing(
                section,
                region.demand,
                region.stirrup.diameter_mm,
                final_db,
                self.project.smf_end_max_spacing_mm,
            )
            minimum_av = self.code.minimum_transverse_area_per_spacing(section)
            required_av = max(region.demand.transverse_required_av_over_s_mm2_per_mm, minimum_av)
            required_combo = required_av + 2.0 * region.demand.at_over_s_mm2_per_mm
            if (
                region.stirrup.spacing_mm > maximum + 1e-9
                or region.stirrup.provided_at_over_s_mm2_per_mm + 1e-9 < region.demand.at_over_s_mm2_per_mm
                or region.stirrup.provided_av_over_s_mm2_per_mm + 1e-9 < required_combo
            ):
                raise DetailingError(f"{section.name}/{region.name}: DETAILING_ITERATION_NOT_CONVERGED")
            stirrup = replace(
                region.stirrup,
                maximum_spacing_mm=maximum,
                governing_reason=reason,
                required_av_over_s_mm2_per_mm=region.demand.transverse_required_av_over_s_mm2_per_mm,
                minimum_av_over_s_mm2_per_mm=minimum_av,
                required_combo_over_s_mm2_per_mm=required_combo,
                final_main_bar_diameter_mm=final_db,
                high_shear=high_shear,
                spacing_limits=limits,
            )
            output.append(replace(region, stirrup=stirrup))
        return tuple(output)

    def _coordinate_intraspan_longitudinal(
        self,
        section: BeamSection,
        regions: tuple[RegionDetailing, ...],
        mode: ReinforcementMode,
    ) -> tuple[RegionDetailing, ...]:
        """Apply the one-span longitudinal rules to the three design stations.

        Bottom reinforcement is deliberately selected once from the complete
        left/middle/right demand envelope.  The selected layout is then used
        at every station in the span, while adjacent spans remain independent.
        The existing top-bar coordination rules are retained for primary
        beams.
        """

        output = list(regions)
        max_layers = self.project.max_main_bar_layers if mode is ReinforcementMode.NORMAL else None
        if section.is_secondary:
            output = self._coordinate_sb_bottom(section, output, max_layers)
        else:
            output = self._coordinate_pb_top(section, output, max_layers)
            output = self._coordinate_pb_bottom(section, output, max_layers)
        return tuple(output)

    def _coordinate_pb_top(
        self,
        section: BeamSection,
        regions: list[RegionDetailing],
        max_layers: int | None,
    ) -> list[RegionDetailing]:
        left = regions[0].top
        middle = regions[1].top
        right = regions[2].top
        left_diameter = self._through_diameter(left)
        right_diameter = self._through_diameter(right)
        minimum_count = regions[1].stirrup.legs
        if left_diameter == right_diameter:
            coordinated = self._fixed_diameter_design(
                section,
                middle.required_area_mm2,
                minimum_count,
                regions[1].stirrup.diameter_mm,
                left_diameter,
                max_layers,
            )
            regions[1] = replace(regions[1], top=coordinated)
            return regions

        overlap = self._lap_overlap_design(
            section,
            middle.required_area_mm2,
            minimum_count,
            regions[1].stirrup.diameter_mm,
            left_diameter,
            right_diameter,
        )
        if overlap is not None:
            regions[1] = replace(regions[1], top=overlap)
            return regions

        # If the two-diameter overlap cannot fit in one practical row, use a
        # zero-transition uniform diameter rather than introducing a second
        # lap or an unverifiable mixed multi-layer layout.
        uniform_diameter = max(left_diameter, right_diameter)
        for index, region in enumerate(regions):
            coordinated = self._fixed_diameter_design(
                section,
                region.top.required_area_mm2,
                region.stirrup.legs,
                region.stirrup.diameter_mm,
                uniform_diameter,
                max_layers,
            )
            regions[index] = replace(region, top=coordinated)
        return regions

    def _coordinate_pb_bottom(
        self,
        section: BeamSection,
        regions: list[RegionDetailing],
        max_layers: int | None,
    ) -> list[RegionDetailing]:
        return self._coordinate_full_span_bottom(section, regions, max_layers)

    def _coordinate_sb_bottom(
        self,
        section: BeamSection,
        regions: list[RegionDetailing],
        max_layers: int | None,
    ) -> list[RegionDetailing]:
        return self._coordinate_full_span_bottom(section, regions, max_layers)

    def _coordinate_full_span_bottom(
        self,
        section: BeamSection,
        regions: list[RegionDetailing],
        max_layers: int | None,
    ) -> list[RegionDetailing]:
        """Select one actual bottom layout for the entire span.

        The largest bottom demand and the strictest stirrup geometry are used
        for the candidate search.  Reusing the same immutable design object at
        all stations makes diameter, count, layering and hanger content
        explicit and prevents the exporter from manufacturing a uniform
        string out of three different calculations.
        """

        if len(regions) != 3:
            raise DetailingError("FULL_SPAN_BOTTOM_REQUIRES_THREE_STATIONS")
        common_stirrup_diameter = max(region.stirrup.diameter_mm for region in regions)
        minimum_count = (
            2
            if section.is_secondary
            else max(region.stirrup.legs for region in regions)
        )
        hanger_support_count = (
            max(region.stirrup.legs for region in regions)
            if section.is_secondary
            else None
        )
        required = max(region.bottom.required_area_mm2 for region in regions)
        allow_minimum_credit_hanger = section.is_secondary and all(
            region.as_flex_bottom_mm2 <= region.as_min_mm2 + 1e-9
            for region in regions
        )
        coordinated = self._select_longitudinal(
            section,
            required,
            minimum_count,
            common_stirrup_diameter,
            max_layers,
            hanger_support_count,
            allow_minimum_credit_hanger,
        )
        if coordinated is None:
            raise DetailingError(f"{section.name}: FULL_SPAN_BOTTOM_LAYOUT_FAILED")
        return [replace(region, bottom=coordinated) for region in regions]

    def _through_diameter(self, design: LongitudinalDesign, section: BeamSection | None = None) -> int:
        minimum = (
            self.project.minimum_main_diameter_for(section)
            if section is not None
            else self.project.minimum_main_diameter_mm
        )
        candidates = [
            bar.diameter_mm for bar in design.bars if bar.diameter_mm >= minimum
        ]
        if not candidates:
            raise DetailingError("LONGITUDINAL_THROUGH_BAR_MISSING")
        return candidates[0]

    def _fixed_diameter_design(
        self,
        section: BeamSection,
        required_area: float,
        minimum_count: int,
        stirrup_diameter_mm: int,
        diameter_mm: int,
        max_layers: int | None,
    ) -> LongitudinalDesign:
        design = self._single_diameter_candidate(
            section,
            required_area,
            minimum_count,
            stirrup_diameter_mm,
            diameter_mm,
            max_layers,
        )
        if design is None:
            raise DetailingError("LONGITUDINAL_FIXED_DIAMETER_LAYOUT_FAILED")
        return design

    def _lap_overlap_design(
        self,
        section: BeamSection,
        required_area: float,
        minimum_count: int,
        stirrup_diameter_mm: int,
        first_diameter: int,
        second_diameter: int,
    ) -> LongitudinalDesign | None:
        first_area = self.project.catalog.area(first_diameter)
        second_area = self.project.catalog.area(second_diameter)
        capacity = self._capacity_per_layer(
            section,
            stirrup_diameter_mm,
            max(first_diameter, second_diameter),
        )
        best = None
        for first_count in range(2, capacity + 1):
            for second_count in range(2, capacity - first_count + 1):
                total_count = first_count + second_count
                if total_count < minimum_count:
                    continue
                provided = first_count * first_area + second_count * second_area
                if provided + 1e-9 < required_area:
                    continue
                score = (provided - required_area, total_count, first_count, second_count)
                if best is None or score < best[0]:
                    best = (score, first_count, second_count, provided)
        if best is None:
            return None
        _, first_count, second_count, provided = best
        return LongitudinalDesign(
            (
                BarSpec(first_diameter, first_count, (first_count,), first_area),
                BarSpec(second_diameter, second_count, (second_count,), second_area),
            ),
            required_area,
            provided,
            1,
            self.code.minimum_clear_spacing(max(first_diameter, second_diameter), section.aggregate_mm),
        )

    def _base_diameter_design(
        self,
        section: BeamSection,
        required_area: float,
        stirrup_diameter_mm: int,
        base_diameter: int,
        max_layers: int | None,
        hanger_support_count: int,
        allow_minimum_credit_hanger: bool,
    ) -> LongitudinalDesign:
        candidates: list[LongitudinalDesign] = []
        fixed = self._single_diameter_candidate(
            section,
            required_area,
            2,
            stirrup_diameter_mm,
            base_diameter,
            max_layers,
        )
        if fixed:
            candidates.append(fixed)
        base_area = self.project.catalog.area(base_diameter)
        capacity = self._capacity_per_layer(section, stirrup_diameter_mm, base_diameter)
        for supplement_diameter in self.project.ordered_longitudinal_diameters_for(section):
            if supplement_diameter <= base_diameter:
                continue
            supplement_area = self.project.catalog.area(supplement_diameter)
            for base_count in range(2, capacity + 1):
                supplement_count = max(
                    1,
                    math.ceil((required_area - base_count * base_area) / supplement_area - 1e-9),
                )
                total_count = base_count + supplement_count
                mixed_capacity = self._capacity_per_layer(section, stirrup_diameter_mm, supplement_diameter)
                if total_count > mixed_capacity:
                    continue
                provided = base_count * base_area + supplement_count * supplement_area
                if provided + 1e-9 < required_area:
                    continue
                candidates.append(
                    LongitudinalDesign(
                        (
                            BarSpec(base_diameter, base_count, (base_count,), base_area),
                            BarSpec(
                                supplement_diameter,
                                supplement_count,
                                (supplement_count,),
                                supplement_area,
                            ),
                        ),
                        required_area,
                        provided,
                        1,
                        self.code.minimum_clear_spacing(supplement_diameter, section.aggregate_mm),
                    )
                )
        if allow_minimum_credit_hanger:
            candidates.extend(
                self._minimum_credit_hanger_candidates(
                    section,
                    required_area,
                    2,
                    stirrup_diameter_mm,
                    main_diameters=(base_diameter,),
                )
            )
        supported = [
            candidate
            for candidate in (
                self._with_sb_hangers(
                    candidate,
                    section,
                    stirrup_diameter_mm,
                    hanger_support_count,
                )
                for candidate in candidates
            )
            if candidate is not None
        ]
        if not supported:
            raise DetailingError("SB_BOTTOM_THROUGH_BAR_LAYOUT_FAILED")
        return min(
            supported,
            key=lambda candidate: (
                candidate.layers,
                candidate.provided_area_mm2 - required_area,
                sum(bar.count for bar in candidate.bars),
                candidate.notation,
            ),
        )

    @staticmethod
    def _with_legs(stirrup: StirrupDesign, legs: int) -> StirrupDesign:
        if legs <= stirrup.legs:
            return stirrup
        return replace(
            stirrup,
            legs=legs,
            provided_av_over_s_mm2_per_mm=legs * stirrup.provided_at_over_s_mm2_per_mm,
        )

    @staticmethod
    def _conservative_stirrup(first: StirrupDesign, second: StirrupDesign) -> StirrupDesign:
        """Merge two oscillating candidates using the stricter geometry."""

        diameter = max(first.diameter_mm, second.diameter_mm)
        spacing = min(first.spacing_mm, second.spacing_mm)
        legs = max(first.legs, second.legs)
        per_leg = math.pi * diameter**2 / 4.0 / spacing
        return replace(
            first,
            diameter_mm=diameter,
            spacing_mm=spacing,
            legs=legs,
            provided_av_over_s_mm2_per_mm=legs * per_leg,
            provided_at_over_s_mm2_per_mm=per_leg,
            final_main_bar_diameter_mm=max(
                first.final_main_bar_diameter_mm or 0,
                second.final_main_bar_diameter_mm or 0,
            ) or None,
            maximum_spacing_mm=min(first.maximum_spacing_mm, second.maximum_spacing_mm),
        )

    def _redesign_face_region(
        self,
        section: BeamSection,
        demand: BeamRegionDemand,
        stirrup: StirrupDesign,
        max_main: int,
    ) -> RegionDetailing:
        effective_depth = self.code.effective_depth(section, stirrup.diameter_mm, max_main)
        as_min = self.code.minimum_longitudinal_area(section.width_mm, effective_depth, section.fc_mpa, section.fy_mpa)
        minimum_main_count = 2 if section.is_secondary else stirrup.legs
        hanger_support_count = stirrup.legs if section.is_secondary else None
        top = self._select_longitudinal(
            section,
            max(demand.as_flex_top_mm2, as_min),
            minimum_main_count,
            stirrup.diameter_mm,
            None,
            hanger_support_count,
            section.is_secondary and demand.as_flex_top_mm2 <= as_min + 1e-9,
        )
        bottom = self._select_longitudinal(
            section,
            max(demand.as_flex_bottom_mm2, as_min),
            minimum_main_count,
            stirrup.diameter_mm,
            None,
            hanger_support_count,
            section.is_secondary and demand.as_flex_bottom_mm2 <= as_min + 1e-9,
        )
        if top is None or bottom is None:
            raise DetailingError(f"{section.name}/{demand.name}: MIN_FLEXURE_CONFLICT")
        return RegionDetailing(
            demand.name,
            demand,
            as_min,
            demand.as_flex_top_mm2,
            demand.as_flex_bottom_mm2,
            stirrup,
            top,
            bottom,
        )

    def _select_stirrup(self, section: BeamSection, demand: BeamRegionDemand, max_main: int) -> StirrupDesign:
        candidates: list[tuple[int, int, StirrupDesign]] = []
        minimum_av = self.code.minimum_transverse_area_per_spacing(section)
        required_av = max(demand.transverse_required_av_over_s_mm2_per_mm, minimum_av)
        combo = required_av + 2.0 * demand.at_over_s_mm2_per_mm
        for diameter_rank, diameter in enumerate(self.project.ordered_stirrup_diameters()):
            area = self.project.catalog.area(diameter)
            for spacing in sorted(self.project.allowed_spacings_mm, reverse=True):
                maximum, reason, limits, high_shear = self.code.maximum_stirrup_spacing(
                    section,
                    demand,
                    diameter,
                    max_main,
                    self.project.smf_end_max_spacing_mm,
                )
                if spacing > maximum + 1e-9:
                    continue
                legs = max(self.code.minimum_legs(section, demand.name), 2, math.ceil(combo * spacing / area - 1e-9))
                if legs % 2:
                    legs += 1
                provided_combo = legs * area / spacing
                provided_at = area / spacing
                if provided_at + 1e-9 < demand.at_over_s_mm2_per_mm:
                    continue
                if provided_combo + 1e-9 < combo:
                    continue
                candidates.append(
                    (
                        diameter_rank,
                        spacing,
                        StirrupDesign(
                            diameter,
                            spacing,
                            legs,
                            legs * area / spacing,
                            provided_at,
                            maximum,
                            reason,
                            demand.transverse_required_av_over_s_mm2_per_mm,
                            minimum_av,
                            combo,
                            max_main,
                            high_shear,
                            limits,
                        ),
                    )
                )
        if candidates:
            return self._preferred_stirrup(section, candidates)
        raise DetailingError(f"{section.name}/{demand.name}: NO_ALLOWED_SPACING")

    def _select_secondary_span_stirrup(
        self,
        section: BeamSection,
        demands: Sequence[BeamRegionDemand],
        main_diameters: Sequence[int],
    ) -> StirrupDesign:
        """Select one SB stirrup diameter/spacing/legs for all span regions."""

        if not demands:
            raise DetailingError(f"{section.name}: SB_SPAN_WITHOUT_REGIONS")
        if len(main_diameters) != len(demands):
            raise DetailingError(f"{section.name}: SB_MAIN_DIAMETER_COUNT_MISMATCH")
        candidates: list[tuple[int, int, StirrupDesign]] = []
        minimum_av = self.code.minimum_transverse_area_per_spacing(section)
        for diameter_rank, diameter in enumerate(self.project.ordered_stirrup_diameters()):
            area = self.project.catalog.area(diameter)
            for spacing in sorted(self.project.allowed_spacings_mm, reverse=True):
                spacing_controls = tuple(
                    self.code.maximum_stirrup_spacing(
                        section,
                        demand,
                        diameter,
                        main_diameter,
                        self.project.smf_end_max_spacing_mm,
                    )
                    for demand, main_diameter in zip(demands, main_diameters)
                )
                maximum, reason, limits, high_shear = min(
                    spacing_controls,
                    key=lambda control: control[0],
                )
                if spacing > maximum + 1e-9:
                    continue
                legs = max(
                    2,
                    *(self.code.minimum_legs(section, demand.name) for demand in demands),
                    *(
                        math.ceil(
                            (max(demand.transverse_required_av_over_s_mm2_per_mm, minimum_av) + 2.0 * demand.at_over_s_mm2_per_mm)
                            * spacing
                            / area
                            - 1e-9
                        )
                        for demand in demands
                    ),
                )
                if legs % 2:
                    legs += 1
                provided_combo = legs * area / spacing
                provided_at = area / spacing
                if any(
                    provided_at + 1e-9 < demand.at_over_s_mm2_per_mm
                    or provided_combo + 1e-9
                    < max(demand.transverse_required_av_over_s_mm2_per_mm, minimum_av) + 2.0 * demand.at_over_s_mm2_per_mm
                    for demand in demands
                ):
                    continue
                candidates.append(
                    (
                        diameter_rank,
                        spacing,
                        StirrupDesign(
                            diameter,
                            spacing,
                            legs,
                            provided_combo,
                            provided_at,
                            maximum,
                            f"SB_SPAN_ENVELOPE:{reason}",
                            max(demand.transverse_required_av_over_s_mm2_per_mm for demand in demands),
                            minimum_av,
                            max(
                                max(demand.transverse_required_av_over_s_mm2_per_mm, minimum_av) + 2.0 * demand.at_over_s_mm2_per_mm
                                for demand in demands
                            ),
                            min(main_diameters),
                            high_shear,
                            limits,
                        ),
                    )
                )
        if candidates:
            return self._preferred_stirrup(section, candidates)
        raise DetailingError(f"{section.name}: NO_ALLOWED_SPACING")

    def _preferred_stirrup(
        self,
        section: BeamSection,
        candidates: Sequence[tuple[int, int, StirrupDesign]],
    ) -> StirrupDesign:
        """Apply the configured stirrup preference to valid candidates."""

        if section.width_mm <= self.project.prefer_two_leg_max_width_mm + 1e-9:
            return min(
                candidates,
                key=lambda item: (
                    item[2].legs,
                    -item[1],
                    item[0],
                ),
            )[2]
        return min(candidates, key=lambda item: (item[0], -item[1], item[2].legs))[2]

    def _select_longitudinal(
        self,
        section: BeamSection,
        required_area: float,
        minimum_count: int,
        stirrup_diameter_mm: int,
        max_layers: int | None,
        hanger_support_count: int | None = None,
        allow_minimum_credit_hanger: bool = False,
    ) -> LongitudinalDesign | None:
        if required_area <= 0:
            required_area = 0.0
        diameters = self.project.ordered_longitudinal_diameters_for(section)
        candidates: list[LongitudinalDesign] = []
        for diameter in diameters:
            candidate = self._single_diameter_candidate(
                section, required_area, minimum_count, stirrup_diameter_mm, diameter, max_layers
            )
            if candidate:
                supported = self._with_sb_hangers(candidate, section, stirrup_diameter_mm, hanger_support_count)
                if supported:
                    candidates.append(supported)
        # Support the user's option-2 mixed-bar pattern without coupling the
        # Excel layer to a particular diameter combination.
        for first_index, first in enumerate(diameters):
            for second in diameters[first_index + 1 :]:
                candidate = self._mixed_candidate(
                    section, required_area, minimum_count, stirrup_diameter_mm, first, second, max_layers
                )
                if candidate:
                    supported = self._with_sb_hangers(candidate, section, stirrup_diameter_mm, hanger_support_count)
                    if supported:
                        candidates.append(supported)
        if allow_minimum_credit_hanger:
            for candidate in self._minimum_credit_hanger_candidates(
                section,
                required_area,
                minimum_count,
                stirrup_diameter_mm,
            ):
                supported = self._with_sb_hangers(
                    candidate,
                    section,
                    stirrup_diameter_mm,
                    hanger_support_count,
                )
                if supported:
                    candidates.append(supported)
        if not candidates:
            return None
        if allow_minimum_credit_hanger:
            return min(
                candidates,
                key=lambda candidate: (
                    candidate.layers,
                    candidate.provided_area_mm2 - required_area,
                    sum(bar.count for bar in candidate.bars),
                    len(candidate.bars),
                    candidate.notation,
                ),
            )
        return min(
            candidates,
            key=lambda candidate: (
                candidate.layers,
                len(candidate.bars),
                sum(bar.count for bar in candidate.bars),
                candidate.provided_area_mm2 - required_area,
                candidate.notation,
            ),
        )

    def _minimum_credit_hanger_candidates(
        self,
        section: BeamSection,
        required_area: float,
        minimum_count: int,
        stirrup_diameter_mm: int,
        main_diameters: Sequence[int] | None = None,
    ) -> tuple[LongitudinalDesign, ...]:
        credit_diameter = self.project.minimum_credit_hanger_diameter_mm
        credit_area = self.project.catalog.area(credit_diameter)
        candidates: list[LongitudinalDesign] = []
        diameters = main_diameters or self.project.ordered_longitudinal_diameters_for(section)
        for main_diameter in diameters:
            main_area = self.project.catalog.area(main_diameter)
            main_count = minimum_count
            remainder = required_area - main_count * main_area
            if remainder <= 1e-9:
                continue
            credit_count = math.ceil(remainder / credit_area - 1e-9)
            total_count = main_count + credit_count
            capacity = self._capacity_per_layer(section, stirrup_diameter_mm, main_diameter)
            if total_count > capacity:
                continue
            provided = main_count * main_area + credit_count * credit_area
            candidates.append(
                LongitudinalDesign(
                    (
                        BarSpec(main_diameter, main_count, (main_count,), main_area),
                        BarSpec(
                            credit_diameter,
                            credit_count,
                            (credit_count,),
                            credit_area,
                            role="minimum_credit_hanger",
                        ),
                    ),
                    required_area,
                    provided,
                    1,
                    self.code.minimum_clear_spacing(main_diameter, section.aggregate_mm),
                )
            )
        return tuple(candidates)

    def _with_sb_hangers(
        self,
        design: LongitudinalDesign,
        section: BeamSection,
        stirrup_diameter_mm: int,
        support_count: int | None,
    ) -> LongitudinalDesign | None:
        if support_count is None:
            return design
        flexural_count = sum(bar.count for bar in design.bars)
        hanger_count = max(0, support_count - flexural_count)
        if hanger_count == 0:
            return design
        hanger_diameter = self.project.sb_hanger_diameter_mm
        largest_diameter = max(
            hanger_diameter,
            *(bar.diameter_mm for bar in design.bars),
        )
        capacity = self._capacity_per_layer(section, stirrup_diameter_mm, largest_diameter)
        if support_count > capacity:
            return None
        hanger = BarSpec(
            hanger_diameter,
            hanger_count,
            (hanger_count,),
            self.project.catalog.area(hanger_diameter),
            role="hanger",
        )
        return replace(design, hanger_bars=hanger)

    def _single_diameter_candidate(self, section, required, minimum_count, stirrup_diameter, diameter, max_layers):
        area = self.project.catalog.area(diameter)
        capacity = self._capacity_per_layer(section, stirrup_diameter, diameter)
        if capacity < 1:
            return None
        max_count = (
            capacity * max_layers if max_layers is not None else max(minimum_count, math.ceil(required / area) + 2)
        )
        count = max(minimum_count, math.ceil(required / area - 1e-9))
        count = _minimum_practical_count(
            count,
            capacity,
            self.project.minimum_bars_per_layer,
            max_count,
        )
        if count is None:
            return None
        if count > max_count:
            return None
        layers = math.ceil(count / capacity)
        if max_layers is not None and layers > max_layers:
            return None
        layer_counts = _split_layers(count, capacity, self.project.minimum_bars_per_layer)
        clear = self.code.minimum_clear_spacing(diameter, section.aggregate_mm)
        return LongitudinalDesign(
            (BarSpec(diameter, count, layer_counts, area),), required, count * area, layers, clear
        )

    def _mixed_candidate(self, section, required, minimum_count, stirrup_diameter, first, second, max_layers):
        area_first = self.project.catalog.area(first)
        area_second = self.project.catalog.area(second)
        capacity = self._capacity_per_layer(section, stirrup_diameter, max(first, second))
        if capacity < 1:
            return None
        max_count = (
            capacity * max_layers
            if max_layers is not None
            else max(minimum_count + 4, math.ceil(required / min(area_first, area_second)) + 2)
        )
        best = None
        for count_first in range(0, max_count + 1):
            count_second = max(0, minimum_count - count_first)
            while count_first * area_first + count_second * area_second < required - 1e-9:
                count_second += 1
            total_count = count_first + count_second
            practical_count = _minimum_practical_count(
                total_count,
                capacity,
                self.project.minimum_bars_per_layer,
                max_count,
            )
            if practical_count is None:
                continue
            count_second += practical_count - total_count
            total_count = practical_count
            if total_count < minimum_count or total_count > max_count:
                continue
            layers = math.ceil(total_count / capacity)
            if max_layers is not None and layers > max_layers:
                continue
            total_area = count_first * area_first + count_second * area_second
            if best is None or (layers, total_area - required, total_count) < best[0]:
                best = ((layers, total_area - required, total_count), count_first, count_second, total_area)
        if best is None or best[1] == 0 and best[2] == 0:
            return None
        _, count_first, count_second, total_area = best
        groups = []
        if count_first:
            # The minimum applies to the combined layer, not independently to
            # each diameter group. A one-bar diameter subgroup can share a
            # layer with bars from the other subgroup.
            groups.append(BarSpec(first, count_first, _split_layers(count_first, capacity, 1), area_first))
        if count_second:
            groups.append(BarSpec(second, count_second, _split_layers(count_second, capacity, 1), area_second))
        return LongitudinalDesign(
            tuple(groups),
            required,
            total_area,
            best[0][0],
            self.code.minimum_clear_spacing(max(first, second), section.aggregate_mm),
        )

    def _capacity_per_layer(self, section, stirrup_diameter, main_diameter) -> int:
        clear_cover = section.clear_cover_for_layout_mm(
            stirrup_diameter,
            main_diameter,
        )
        if clear_cover <= 0:
            return 0
        return single_layer_max_bars(
            section.width_mm,
            clear_cover,
            stirrup_diameter,
            section.aggregate_mm,
            main_diameter,
        )

    def _select_face_rebar(
        self, section: BeamSection, demands: Sequence[BeamRegionDemand], mode: ReinforcementMode
    ) -> FaceRebarDesign:
        max_required = max((demand.al_torsion_mm2 / 2.0 for demand in demands), default=0.0)
        face_length = section.depth_mm - 2.0 * section.face_rebar_boundary_cover_mm
        default_count = max(0, math.ceil(face_length / self.project.face_max_spacing_mm) - 1)
        depth_skin = section.depth_mm >= self.project.face_min_depth_mm
        if mode is ReinforcementMode.FACE_TORSION:
            bars = self._select_face_bars(max_required, max(default_count, 1), section, torsion=True)
            if bars is None:
                raise DetailingError(f"{section.name}: face rebar 无法满足 Al/2。")
            face_mode = FaceRebarMode.TORSION_AND_SKIN if depth_skin else FaceRebarMode.TORSION
            reason = "任一区域 Total 纵筋超过两层；整跨按最大 Al/2"
            return FaceRebarDesign(face_mode, bars, max_required, bars.total_area_mm2, reason)
        if not depth_skin:
            return FaceRebarDesign(FaceRebarMode.NOT_APPLICABLE, None, 0.0, 0.0, "h<800 mm 且未触发抗扭 face 模式")
        bars = self._select_face_bars(0.0, default_count, section, torsion=False)
        if bars is None:
            raise DetailingError(f"{section.name}: 构造 face rebar 无法排布。")
        return FaceRebarDesign(FaceRebarMode.SKIN, bars, 0.0, bars.total_area_mm2, "h>=800 mm 的项目构造 face rebar")

    def _select_face_bars(self, required, minimum_count, section, torsion):
        diameters = (
            self.project.ordered_torsion_face_diameters() if torsion else (self.project.default_face_diameter_mm,)
        )
        diameter = diameters[0]
        area = self.project.catalog.area(diameter)
        count = max(minimum_count, math.ceil(required / area - 1e-9))
        if count <= 0:
            count = 1
        return BarSpec(diameter, count, (count,), area, role="face")


def coordinate_adjacent_spans(
    first: BeamDetailingResult,
    second: BeamDetailingResult,
    option: ContinuityOption,
    engine: BeamDetailingEngine | None = None,
) -> tuple[BeamDetailingResult, BeamDetailingResult]:
    """Coordinate top bars at a shared support without touching ETABS.

    Option 1 uses the larger selected layout on both sides.  Option 2 preserves
    one shared diameter baseline while allowing the higher-demand side to add
    bars.  Bottom layouts are intentionally independent per span; each result
    is re-normalized to its own full-span bottom demand envelope after the
    support top bars are coordinated.  The function is intentionally pure so
    it can be applied before an Excel export or a future model writeback
    transaction.
    """

    if len(first.regions) != len(second.regions):
        raise DetailingError("相邻跨必须具有相同的 left/middle/right 区域结构。")
    if first.detailing_profile != second.detailing_profile:
        raise DetailingError("相邻跨配筋 profile 不一致，不能自动协调。")
    if option not in {
        ContinuityOption.OPTION_1_STRICT_EQUAL,
        ContinuityOption.OPTION_2_COUNT_ADJUSTMENT,
    }:
        raise DetailingError(f"未知相邻跨连续性方案：{option}")
    engine = engine or BeamDetailingEngine()
    first_regions = list(first.regions)
    second_regions = list(second.regions)

    # A shared support is the J end of the first span and I end of the next.
    is_secondary = first.section.is_secondary
    count_adjustment = option is ContinuityOption.OPTION_2_COUNT_ADJUSTMENT and not is_secondary
    first_top, second_top = _common_support_layout(
        first.regions[2].top,
        second.regions[0].top,
        first.regions[2],
        second.regions[0],
        first,
        second,
        engine,
        exact_count=not count_adjustment,
        allow_supplement=not count_adjustment,
    )
    first_regions[2] = replace(first_regions[2], top=first_top)
    second_regions[0] = replace(second_regions[0], top=second_top)

    coordination_warning = ("OPTION_2_COUNT_ADJUSTMENT_APPLIED",) if count_adjustment else ()
    first_output = replace(
        first,
        regions=tuple(first_regions),
        warnings=tuple(sorted(set((*first.warnings, *coordination_warning)))),
        continuity_option=option,
        coordination_status="STABLE",
    )
    second_output = replace(
        second,
        regions=tuple(second_regions),
        warnings=tuple(sorted(set((*second.warnings, *coordination_warning)))),
        continuity_option=option,
        coordination_status="STABLE",
    )
    first_output = coordinate_intraspan_result(first_output, engine)
    second_output = coordinate_intraspan_result(second_output, engine)
    # Shared-support coordination can increase the actual end Mpr.  Recheck
    # capacity shear before the result is returned; if the existing hoops no
    # longer satisfy the new demand, fail closed and let the beam-line caller
    # publish a coordination-blocked status.
    first_output = engine.refresh_capacity_shear_after_layout_change(first_output)
    second_output = engine.refresh_capacity_shear_after_layout_change(second_output)
    return first_output, second_output


def coordinate_intraspan_result(
    result: BeamDetailingResult,
    engine: BeamDetailingEngine | None = None,
) -> BeamDetailingResult:
    """Reapply one-span lap rules after a support or manual layout change."""

    engine = engine or BeamDetailingEngine()
    regions = engine._coordinate_intraspan_longitudinal(
        result.section,
        result.regions,
        result.mode,
    )
    return replace(result, regions=regions)


def _common_support_layout(
    first_design: LongitudinalDesign,
    second_design: LongitudinalDesign,
    first_region: RegionDetailing,
    second_region: RegionDetailing,
    first_result: BeamDetailingResult,
    second_result: BeamDetailingResult,
    engine: BeamDetailingEngine,
    *,
    exact_count: bool,
    allow_supplement: bool = True,
) -> tuple[LongitudinalDesign, LongitudinalDesign]:
    anchor = engine._through_diameter(first_design, first_result.section)
    diameters = (anchor,) + tuple(
        value
        for value in engine.project.ordered_longitudinal_diameters_for(first_result.section)
        if value != anchor
    )
    max_layers = (
        None
        if first_result.mode is ReinforcementMode.FACE_TORSION and second_result.mode is ReinforcementMode.FACE_TORSION
        else engine.project.max_main_bar_layers
    )
    if not exact_count:
        if not allow_supplement:
            first_minimum = 2 if first_result.section.is_secondary else first_region.stirrup.legs
            second_minimum = 2 if second_result.section.is_secondary else second_region.stirrup.legs
            for diameter in diameters:
                first_output = engine._single_diameter_candidate(
                    first_result.section,
                    first_design.required_area_mm2,
                    first_minimum,
                    first_region.stirrup.diameter_mm,
                    diameter,
                    max_layers,
                )
                second_output = engine._single_diameter_candidate(
                    second_result.section,
                    second_design.required_area_mm2,
                    second_minimum,
                    second_region.stirrup.diameter_mm,
                    diameter,
                    max_layers,
                )
                if first_output is not None and second_output is not None:
                    return first_output, second_output
            raise DetailingError("ADJACENT_SUPPORT_OPTION_2_LAYOUT_FAILED")
        first_output = engine._base_diameter_design(
            first_result.section,
            first_design.required_area_mm2,
            first_region.stirrup.diameter_mm,
            anchor,
            max_layers,
            first_region.stirrup.legs,
            first_region.as_flex_bottom_mm2 <= first_region.as_min_mm2 + 1e-9,
        )
        second_output = engine._base_diameter_design(
            second_result.section,
            second_design.required_area_mm2,
            second_region.stirrup.diameter_mm,
            anchor,
            max_layers,
            second_region.stirrup.legs,
            second_region.as_flex_bottom_mm2 <= second_region.as_min_mm2 + 1e-9,
        )
        return first_output, second_output

    minimum_count = (
        2
        if first_result.section.is_secondary
        else max(
            first_region.stirrup.legs,
            second_region.stirrup.legs,
        )
    )
    required = max(first_design.required_area_mm2, second_design.required_area_mm2)
    for diameter in diameters:
        first_trial = engine._single_diameter_candidate(
            first_result.section,
            required,
            minimum_count,
            first_region.stirrup.diameter_mm,
            diameter,
            max_layers,
        )
        second_trial = engine._single_diameter_candidate(
            second_result.section,
            required,
            minimum_count,
            second_region.stirrup.diameter_mm,
            diameter,
            max_layers,
        )
        if first_trial is None or second_trial is None:
            continue
        count = max(first_trial.bars[0].count, second_trial.bars[0].count)
        capacity = min(
            engine._capacity_per_layer(
                first_result.section,
                first_region.stirrup.diameter_mm,
                diameter,
            ),
            engine._capacity_per_layer(
                second_result.section,
                second_region.stirrup.diameter_mm,
                diameter,
            ),
        )
        max_count = capacity * max_layers if max_layers is not None else None
        practical = _minimum_practical_count(
            count,
            capacity,
            engine.project.minimum_bars_per_layer,
            max_count,
        )
        if practical is None:
            continue
        layers = _split_layers(practical, capacity, engine.project.minimum_bars_per_layer)
        area = engine.project.catalog.area(diameter)
        provided = practical * area
        first_output = LongitudinalDesign(
            (BarSpec(diameter, practical, layers, area),),
            required,
            provided,
            len(layers),
            engine.code.minimum_clear_spacing(diameter, first_result.section.aggregate_mm),
        )
        second_output = LongitudinalDesign(
            (BarSpec(diameter, practical, layers, area),),
            required,
            provided,
            len(layers),
            engine.code.minimum_clear_spacing(diameter, second_result.section.aggregate_mm),
        )
        if first_result.section.is_secondary:
            first_output = engine._with_sb_hangers(
                first_output,
                first_result.section,
                first_region.stirrup.diameter_mm,
                first_region.stirrup.legs,
            )
            second_output = engine._with_sb_hangers(
                second_output,
                second_result.section,
                second_region.stirrup.diameter_mm,
                second_region.stirrup.legs,
            )
            if first_output is None or second_output is None:
                continue
        return first_output, second_output
    raise DetailingError("ADJACENT_SUPPORT_LAYOUT_FAILED")


def _preferred_order(preferred: Iterable[int], enabled: Iterable[int]) -> tuple[int, ...]:
    preferred_unique = tuple(dict.fromkeys(preferred))
    remaining = tuple(value for value in enabled if value not in preferred_unique)
    return preferred_unique + remaining


def _stirrup_signature(stirrup: StirrupDesign) -> tuple[int, int, int]:
    return stirrup.diameter_mm, stirrup.spacing_mm, stirrup.legs


def _minimum_practical_count(
    count: int,
    capacity: int,
    minimum_bars_per_layer: int,
    max_count: int | None,
) -> int | None:
    """Return the smallest count that permits a constructible layer layout.

    A single layer keeps its requested count.  For multiple layers, every
    layer must contain at least ``minimum_bars_per_layer`` bars.  Narrow
    sections with a capacity smaller than that minimum cannot form a valid
    second layer; a remainder of one is otherwise cured with at most one
    additional bar when redistribution alone is impossible.
    """

    if count <= capacity:
        return count
    if capacity < minimum_bars_per_layer:
        return None
    adjusted = count
    while True:
        layer_count = math.ceil(adjusted / capacity)
        if adjusted >= layer_count * minimum_bars_per_layer:
            return adjusted
        adjusted += 1
        if max_count is not None and adjusted > max_count:
            return None


def _split_layers(count: int, capacity: int, minimum_bars_per_layer: int = 2) -> tuple[int, ...]:
    if count <= 0:
        return (0,)
    if capacity <= 0:
        raise DetailingError("纵筋每层排布容量不足。")
    layers = []
    remaining = count
    while remaining:
        value = min(capacity, remaining)
        layers.append(value)
        remaining -= value
    if len(layers) > 1 and layers[-1] < minimum_bars_per_layer:
        deficit = minimum_bars_per_layer - layers[-1]
        for index in range(len(layers) - 2, -1, -1):
            movable = max(0, layers[index] - minimum_bars_per_layer)
            transfer = min(deficit, movable)
            layers[index] -= transfer
            layers[-1] += transfer
            deficit -= transfer
            if deficit == 0:
                break
        if deficit:
            raise DetailingError("纵筋分层无法满足每排至少 2 根。")
    return tuple(layers)
