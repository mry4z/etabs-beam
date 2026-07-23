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
        return section.depth_mm - section.cover_mm - stirrup_diameter_mm - main_diameter_mm / 2.0

    def hoop_perimeter(self, section: "BeamSection", stirrup_diameter_mm: float) -> float:
        width = section.width_mm - 2.0 * (section.cover_mm + stirrup_diameter_mm / 2.0)
        depth = section.depth_mm - 2.0 * (section.cover_mm + stirrup_diameter_mm / 2.0)
        return max(0.0, 2.0 * (width + depth))

    def maximum_stirrup_spacing(
        self,
        section: "BeamSection",
        region: str,
        stirrup_diameter_mm: float,
        main_diameter_mm: float,
    ) -> float:
        effective_depth = self.effective_depth(section, stirrup_diameter_mm, main_diameter_mm)
        if effective_depth <= 0:
            return 0.0
        ph = self.hoop_perimeter(section, stirrup_diameter_mm)
        ordinary = min(effective_depth / 2.0, 600.0, 300.0, ph / 8.0 if ph else 0.0)
        if section.is_smf and region in {"left", "right"}:
            # db is intentionally the smallest selected main-bar diameter;
            # skin bars must not control the SMF limit.
            smf = min(effective_depth / 4.0, 6.0 * main_diameter_mm, 150.0)
            return min(ordinary, smf)
        return ordinary

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
    cover_mm: float
    fc_mpa: float
    fy_mpa: float
    fyt_mpa: float
    aggregate_mm: float
    length_mm: float
    detailing_profile: BeamDetailingProfile = BeamDetailingProfile.PB_OMF

    def __post_init__(self) -> None:
        values = (
            self.width_mm,
            self.depth_mm,
            self.cover_mm,
            self.fc_mpa,
            self.fy_mpa,
            self.fyt_mpa,
            self.aggregate_mm,
            self.length_mm,
        )
        if any(value <= 0 for value in values):
            raise DetailingError("梁截面、材料、保护层和长度必须为正数。")

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
        max_main = max(self.project.enabled_diameters_mm)
        selected_stirrups = [self._select_stirrup(section, demand, max_main) for demand in input_data.regions]
        # The project梁表 has one ``Legs`` field.  Use the controlling region's
        # legs for the whole span, then recalculate provided Av/s so the
        # longitudinal-bar minimum count and the reported stirrup capacity are
        # consistent at all three positions.
        span_legs = max(stirrup.legs for stirrup in selected_stirrups)
        selected_stirrups = [self._with_legs(stirrup, span_legs) for stirrup in selected_stirrups]
        normal_regions: list[RegionDetailing] = []
        normal_fails = False
        minimum_main_count = 2 if section.is_secondary else None
        for demand, stirrup in zip(input_data.regions, selected_stirrups):
            effective_depth = self.code.effective_depth(section, stirrup.diameter_mm, max_main)
            as_min = self.code.minimum_longitudinal_area(
                section.width_mm, effective_depth, section.fc_mpa, section.fy_mpa
            )
            top = self._select_longitudinal(
                section,
                demand.as_top_total_mm2,
                minimum_main_count or stirrup.legs,
                stirrup.diameter_mm,
                max_layers=self.project.max_main_bar_layers,
                hanger_support_count=stirrup.legs if section.is_secondary else None,
                allow_minimum_credit_hanger=(section.is_secondary and demand.as_flex_top_mm2 <= as_min + 1e-9),
            )
            bottom = self._select_longitudinal(
                section,
                demand.as_bottom_total_mm2,
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
                self._redesign_face_region(section, demand, stirrup, max_main)
                for demand, stirrup in zip(input_data.regions, selected_stirrups)
            )

        regions = self._coordinate_intraspan_longitudinal(section, regions, mode)

        face = self._select_face_rebar(section, input_data.regions, mode)
        warnings = tuple(
            "FLEXURAL_DEMAND_BELOW_AS_MIN"
            for region in regions
            if region.as_flex_top_mm2 < region.as_min_mm2 or region.as_flex_bottom_mm2 < region.as_min_mm2
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
        )

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
            demand.as_flex_top_mm2,
            minimum_main_count,
            stirrup.diameter_mm,
            None,
            hanger_support_count,
            section.is_secondary and demand.as_flex_top_mm2 <= as_min + 1e-9,
        )
        bottom = self._select_longitudinal(
            section,
            demand.as_flex_bottom_mm2,
            minimum_main_count,
            stirrup.diameter_mm,
            None,
            hanger_support_count,
            section.is_secondary and demand.as_flex_bottom_mm2 <= as_min + 1e-9,
        )
        if top is None or bottom is None:
            raise DetailingError(f"{section.name}/{demand.name}: AsFlex 无法排布。")
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
        for diameter_rank, diameter in enumerate(self.project.ordered_stirrup_diameters()):
            area = self.project.catalog.area(diameter)
            for spacing in sorted(self.project.allowed_spacings_mm, reverse=True):
                maximum = self.code.maximum_stirrup_spacing(section, demand.name, diameter, max_main)
                if spacing > maximum + 1e-9:
                    continue
                combo = demand.av_over_s_mm2_per_mm + 2.0 * demand.at_over_s_mm2_per_mm
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
                            "剪扭组合+构造限值",
                        ),
                    )
                )
        if candidates:
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
        raise DetailingError(f"{section.name}/{demand.name}: NO_ALLOWED_SPACING")

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
        return single_layer_max_bars(
            section.width_mm,
            section.cover_mm,
            stirrup_diameter,
            section.aggregate_mm,
            main_diameter,
        )

    def _select_face_rebar(
        self, section: BeamSection, demands: Sequence[BeamRegionDemand], mode: ReinforcementMode
    ) -> FaceRebarDesign:
        max_required = max((demand.al_torsion_mm2 / 2.0 for demand in demands), default=0.0)
        face_length = section.depth_mm - 2.0 * section.cover_mm
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

    Option 1 uses the larger selected layout on both sides.  Option 2 currently
    shares that local kernel; its distinct Beam-Line-wide baseline/count
    adjustment remains a tracked TODO.  The function is intentionally pure so
    it can be applied before an Excel export or a future model writeback
    transaction.  For SB, only top reinforcement is coordinated.
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
    first_top, second_top = _common_support_layout(
        first.regions[2].top,
        second.regions[0].top,
        first.regions[2],
        second.regions[0],
        first,
        second,
        engine,
        exact_count=True,
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
            exact_count=True,
        )
    first_regions[2] = replace(first_regions[2], bottom=first_bottom)
    second_regions[0] = replace(second_regions[0], bottom=second_bottom)
    first_output = replace(first, regions=tuple(first_regions))
    second_output = replace(second, regions=tuple(second_regions))
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
) -> tuple[LongitudinalDesign, LongitudinalDesign]:
    anchor = engine._through_diameter(first_design)
    diameters = (anchor,) + tuple(value for value in engine.project.ordered_longitudinal_diameters() if value != anchor)
    max_layers = (
        None
        if first_result.mode is ReinforcementMode.FACE_TORSION and second_result.mode is ReinforcementMode.FACE_TORSION
        else engine.project.max_main_bar_layers
    )
    if not exact_count:
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
