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
    """Explicit project/code profile; topology type alone is not a code system."""

    SB_ORDINARY_NO_CH18 = "SB_ORDINARY_NO_CH18"
    PB_OMF = "PB_OMF"
    PB_SMF = "PB_SMF"
    PB_REVIEW_REQUIRED = "PB_REVIEW_REQUIRED"

    @property
    def is_smf(self) -> bool:
        return self is BeamDetailingProfile.PB_SMF

    @property
    def is_secondary(self) -> bool:
        return self is BeamDetailingProfile.SB_ORDINARY_NO_CH18


def resolve_detailing_profile(
    beam_type: str,
    frame_types: Iterable[str],
) -> tuple[BeamDetailingProfile, str]:
    """Resolve the confirmed project profile without promoting any SB to Ch. 18.

    The user-confirmed project rule treats every non-SMF PB as OMF.  Unknown,
    mixed, or Program Determined PB inputs remain review-required so an ETABS
    design option is never silently invented.
    """

    normalized_type = str(beam_type).strip().upper()
    normalized_frames = tuple(dict.fromkeys(str(value).strip() or "Unknown" for value in frame_types))
    if normalized_type == "SB":
        return (
            BeamDetailingProfile.SB_ORDINARY_NO_CH18,
            "beam_type=SB; ETABS FrameType does not enable Chapter 18",
        )
    if normalized_type != "PB":
        return BeamDetailingProfile.PB_REVIEW_REQUIRED, f"unknown beam_type={beam_type!r}"
    if len(normalized_frames) != 1:
        return BeamDetailingProfile.PB_REVIEW_REQUIRED, "PB source FrameType is mixed"
    frame_type = normalized_frames[0]
    if frame_type in {"Unknown", "Program Determined"}:
        return BeamDetailingProfile.PB_REVIEW_REQUIRED, f"PB FrameType={frame_type}"
    if frame_type == "Sway Special":
        return BeamDetailingProfile.PB_SMF, "PB FrameType=Sway Special"
    return (
        BeamDetailingProfile.PB_OMF,
        f"PB non-SMF project rule; source FrameType={frame_type}",
    )


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

    version: str = "project-2026-07"
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
        high_shear = demand.av_over_s_mm2_per_mm > high_shear_limit + 1e-9
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
        governing_reason, maximum = min(limits, key=lambda item: item[1])
        return maximum, governing_reason, tuple(limits), high_shear

    def minimum_transverse_area_per_spacing(self, section: "BeamSection") -> float:
        """ACI 318-14 SI minimum Av/s for nonprestressed beam shear reinforcement."""

        return max(
            0.062 * math.sqrt(section.fc_mpa) * section.width_mm / section.fyt_mpa,
            0.35 * section.width_mm / section.fyt_mpa,
        )

    def minimum_legs(self, section: "BeamSection", region: str) -> int:
        return 4 if section.is_smf and region in {"left", "right"} else 2


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
        return self.detailing_profile.is_secondary


@dataclass(frozen=True)
class BeamDetailingInput:
    section: BeamSection
    regions: tuple[BeamRegionDemand, ...]

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

    @property
    def errors(self) -> tuple[str, ...]:
        return ()


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
                input_data.regions,
                (max_main,) * len(input_data.regions),
            )
            selected_stirrups = [span_stirrup for _ in input_data.regions]
        else:
            selected_stirrups = [self._select_stirrup(section, demand, max_main) for demand in input_data.regions]
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
            mode, regions = self._design_regions_with_stirrups(
                input_data,
                selected_stirrups,
            )
            regions = self._coordinate_intraspan_longitudinal(
                section,
                regions,
                mode,
            )
            final_diameters = tuple(self._final_main_db(region) for region in regions)
            if section.is_secondary:
                span_stirrup = self._select_secondary_span_stirrup(
                    section,
                    input_data.regions,
                    final_diameters,
                )
                next_stirrups = [span_stirrup for _ in input_data.regions]
            else:
                next_stirrups = [
                    self._select_stirrup(section, demand, final_db)
                    for demand, final_db in zip(input_data.regions, final_diameters)
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
                try:
                    self._finalize_stirrup_controls(section, regions)
                except DetailingError:
                    pass
                else:
                    iteration_status = "STABLE_CONSERVATIVE_CYCLE_BREAK"
                    break
            selected_stirrups = next_stirrups
        else:
            raise DetailingError(f"{section.name}: DETAILING_ITERATION_NOT_CONVERGED")

        if mode is None:
            raise DetailingError(f"{section.name}: DETAILING_RESULT_MISSING")
        regions = self._finalize_stirrup_controls(section, regions)

        face = self._select_face_rebar(section, input_data.regions, mode)
        warnings = tuple(
            "FLEXURAL_DEMAND_BELOW_AS_MIN"
            for region in regions
            if (
                region.demand.as_flex_top_mm2 < region.as_min_mm2
                or region.demand.as_flex_bottom_mm2 < region.as_min_mm2
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
                self.project.minimum_main_diameter_mm,
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
                    self.project.minimum_main_diameter_mm,
                )
                for demand, stirrup in zip(input_data.regions, selected_stirrups)
            )
        return mode, regions

    def _final_main_db(self, region: RegionDetailing) -> int:
        return min(
            bar.diameter_mm
            for design in (region.top, region.bottom)
            for bar in design.bars
            if bar.diameter_mm >= self.project.minimum_main_diameter_mm
        )

    def _finalize_stirrup_controls(
        self,
        section: BeamSection,
        regions: tuple[RegionDetailing, ...],
    ) -> tuple[RegionDetailing, ...]:
        """Recheck every region with its final as-detailed longitudinal db."""

        output: list[RegionDetailing] = []
        for region in regions:
            final_db = self._final_main_db(region)
            maximum, reason, limits, high_shear = self.code.maximum_stirrup_spacing(
                section,
                region.demand,
                region.stirrup.diameter_mm,
                final_db,
            )
            minimum_av = self.code.minimum_transverse_area_per_spacing(section)
            required_av = max(region.demand.av_over_s_mm2_per_mm, minimum_av)
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
                required_av_over_s_mm2_per_mm=region.demand.av_over_s_mm2_per_mm,
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
        """Apply the confirmed left/middle/right lap rules within one span."""

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
        through_diameter = self._through_diameter(regions[0].bottom)
        through_template = next(bar.layers for bar in regions[0].bottom.bars if bar.diameter_mm == through_diameter)
        middle_required = max(
            regions[1].bottom.required_area_mm2,
            regions[0].bottom.provided_area_mm2,
            regions[2].bottom.provided_area_mm2,
        )
        for index, region in enumerate(regions):
            required = middle_required if index == 1 else region.bottom.required_area_mm2
            coordinated = self._fixed_diameter_design(
                section,
                required,
                region.stirrup.legs,
                region.stirrup.diameter_mm,
                through_diameter,
                max_layers,
            )
            if len(coordinated.bars) == 1 and sum(through_template) == coordinated.bars[0].count:
                coordinated_bar = replace(coordinated.bars[0], layers=through_template)
                coordinated = replace(
                    coordinated,
                    bars=(coordinated_bar,),
                    layers=len(through_template),
                )
            regions[index] = replace(region, bottom=coordinated)
        return regions

    def _coordinate_sb_bottom(
        self,
        section: BeamSection,
        regions: list[RegionDetailing],
        max_layers: int | None,
    ) -> list[RegionDetailing]:
        through_diameter = self._through_diameter(regions[0].bottom)
        middle_required = max(
            regions[1].bottom.required_area_mm2,
            regions[0].bottom.provided_area_mm2,
            regions[2].bottom.provided_area_mm2,
        )
        for index, region in enumerate(regions):
            required = middle_required if index == 1 else region.bottom.required_area_mm2
            allow_credit = region.as_flex_bottom_mm2 <= region.as_min_mm2 + 1e-9
            coordinated = self._base_diameter_design(
                section,
                required,
                region.stirrup.diameter_mm,
                through_diameter,
                max_layers,
                region.stirrup.legs,
                allow_credit,
            )
            regions[index] = replace(region, bottom=coordinated)
        return regions

    def _through_diameter(self, design: LongitudinalDesign) -> int:
        candidates = [
            bar.diameter_mm for bar in design.bars if bar.diameter_mm >= self.project.minimum_main_diameter_mm
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
        for supplement_diameter in self.project.ordered_longitudinal_diameters():
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
        required_av = max(demand.av_over_s_mm2_per_mm, minimum_av)
        combo = required_av + 2.0 * demand.at_over_s_mm2_per_mm
        for diameter_rank, diameter in enumerate(self.project.ordered_stirrup_diameters()):
            area = self.project.catalog.area(diameter)
            for spacing in sorted(self.project.allowed_spacings_mm, reverse=True):
                maximum, reason, limits, high_shear = self.code.maximum_stirrup_spacing(
                    section,
                    demand,
                    diameter,
                    max_main,
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
                            demand.av_over_s_mm2_per_mm,
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
                            (max(demand.av_over_s_mm2_per_mm, minimum_av) + 2.0 * demand.at_over_s_mm2_per_mm)
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
                    < max(demand.av_over_s_mm2_per_mm, minimum_av) + 2.0 * demand.at_over_s_mm2_per_mm
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
                            max(demand.av_over_s_mm2_per_mm for demand in demands),
                            minimum_av,
                            max(
                                max(demand.av_over_s_mm2_per_mm, minimum_av) + 2.0 * demand.at_over_s_mm2_per_mm
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
        diameters = self.project.ordered_longitudinal_diameters()
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
        diameters = main_diameters or self.project.ordered_longitudinal_diameters()
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
    """Coordinate top/bottom bars at a shared support without touching ETABS.

    Option 1 uses the larger selected layout on both sides.  Option 2 preserves
    one shared diameter baseline while allowing the higher-demand side to add
    bars.  The function is intentionally pure so it can be applied before an
    Excel export or a future model writeback transaction.
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
    count_adjustment = option is ContinuityOption.OPTION_2_COUNT_ADJUSTMENT and not first.detailing_profile.is_secondary
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

    if first.detailing_profile.is_secondary:
        # SB bottom bars keep independent counts, but use the preceding span's
        # through-bar diameter at the common support for lap compatibility.
        first_bottom, second_bottom = _common_support_layout(
            first.regions[2].bottom,
            second.regions[0].bottom,
            first.regions[2],
            second.regions[0],
            first,
            second,
            engine,
            exact_count=False,
        )
    else:
        first_bottom, second_bottom = _common_support_layout(
            first.regions[2].bottom,
            second.regions[0].bottom,
            first.regions[2],
            second.regions[0],
            first,
            second,
            engine,
            exact_count=not count_adjustment,
            allow_supplement=not count_adjustment,
        )
    first_regions[2] = replace(first_regions[2], bottom=first_bottom)
    second_regions[0] = replace(second_regions[0], bottom=second_bottom)
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
    anchor = engine._through_diameter(first_design)
    diameters = (anchor,) + tuple(value for value in engine.project.ordered_longitudinal_diameters() if value != anchor)
    max_layers = (
        None
        if first_result.mode is ReinforcementMode.FACE_TORSION and second_result.mode is ReinforcementMode.FACE_TORSION
        else engine.project.max_main_bar_layers
    )
    if not exact_count:
        if not allow_supplement:
            first_minimum = 2 if first_result.detailing_profile.is_secondary else first_region.stirrup.legs
            second_minimum = 2 if second_result.detailing_profile.is_secondary else second_region.stirrup.legs
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
        if first_result.detailing_profile.is_secondary
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
        if first_result.detailing_profile.is_secondary:
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
