"""Constructability and seismic detailing checks for reinforced-concrete beams.

The detailing engine selects reinforcement from normalized ETABS demands.  This
module adds the second, spatial layer of the contract: support-face based
zones, actual hoop locations, and auditable rule checks.  It deliberately has
no CSI/ETABS imports so it can be tested independently and reused by snapshot
workflows.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, sqrt
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class SupportFaceContext:
    """One support face measured along the beam span axis."""

    name: str
    face_location_mm: float
    centerline_location_mm: float | None = None
    support_dimension_along_beam_mm: float | None = None
    source: str = ""

    def __post_init__(self) -> None:
        if not str(self.name).strip():
            raise ValueError("支承面名称不能为空。")
        if self.face_location_mm < 0:
            raise ValueError("支承面里程不能为负数。")
        if self.centerline_location_mm is not None and self.centerline_location_mm < 0:
            raise ValueError("支承中心线里程不能为负数。")
        if self.support_dimension_along_beam_mm is not None and self.support_dimension_along_beam_mm <= 0:
            raise ValueError("支承构件沿梁方向尺寸必须为正数。")

    @property
    def resolved(self) -> bool:
        return bool(str(self.source).strip()) and self.face_location_mm >= 0


@dataclass(frozen=True)
class SeismicDetailingContext:
    """Evidence required before a beam can be called a verified SMF beam."""

    is_smf: bool = False
    frame_type: str = ""
    design_code: str = "ACI 318-14"
    capacity_shear_source: str = ""
    capacity_shear_verified: bool = False
    axial_load_kN: float | None = None
    axial_load_source: str = ""
    likely_yield_locations_mm: tuple[float, ...] = ()
    source: str = ""
    provenance: "DesignResultProvenance | None" = None
    capacity_shear_demand_kN: float | None = None
    gravity_shear_kN: float | None = None
    left_mpr_kNm: float | None = None
    right_mpr_kNm: float | None = None
    mpr_strength_factor: float | None = None
    mpr_strength_source: str = ""
    capacity_shear_phi: float = 0.75
    capacity_shear_vc_zero: bool = True
    capacity_shear_final_kN: float | None = None
    capacity_shear_required_av_over_s_mm2_per_mm: float | None = None
    capacity_shear_control_source: str = ""

    def __post_init__(self) -> None:
        if self.capacity_shear_phi <= 0.0 or self.capacity_shear_phi > 1.0:
            raise ValueError("capacity_shear_phi 必须在 (0, 1] 范围内。")
        if self.mpr_strength_factor is not None and self.mpr_strength_factor <= 0.0:
            raise ValueError("mpr_strength_factor 必须为正数。")


@dataclass(frozen=True)
class DesignResultProvenance:
    """Traceability recorded with a demand/capacity decision.

    ETABS result values are inputs to detailing, not proof that every ACI
    construction rule has already been applied.  Keeping this compact record
    beside the SMF context lets the workbook show exactly what was (and was
    not) verified by the source application.
    """

    source: str = ""
    design_code: str = "ACI 318-14"
    frame_type: str = ""
    analysis_status: str = ""
    design_status: str = ""
    capacity_shear_included: bool | None = None
    units: str = "N_mm_C"
    model_hash: str = ""


@dataclass(frozen=True)
class CapacityShearReview:
    """Numeric SMF capacity-shear review used before transverse selection."""

    status: str
    left_mpr_positive_kNm: float | None = None
    left_mpr_negative_kNm: float | None = None
    right_mpr_positive_kNm: float | None = None
    right_mpr_negative_kNm: float | None = None
    actual_capacity_shear_kN: float | None = None
    etabs_capacity_shear_kN: float | None = None
    final_capacity_shear_kN: float | None = None
    required_av_over_s_mm2_per_mm: float | None = None
    control_source: str = ""
    source: str = ""
    vc_zero: bool = True
    gravity_shear_kN: float | None = None
    message: str = ""

    @property
    def left_mpr_kNm(self) -> float | None:
        values = (self.left_mpr_positive_kNm, self.left_mpr_negative_kNm)
        available = [value for value in values if value is not None]
        return max(available, default=None)

    @property
    def right_mpr_kNm(self) -> float | None:
        values = (self.right_mpr_positive_kNm, self.right_mpr_negative_kNm)
        available = [value for value in values if value is not None]
        return max(available, default=None)

    @property
    def vu_from_actual_rebar_kN(self) -> float | None:
        """Capacity shear attributed to the final selected reinforcement."""

        return self.actual_capacity_shear_kN

    def as_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "left_mpr_positive_kNm": self.left_mpr_positive_kNm,
            "left_mpr_negative_kNm": self.left_mpr_negative_kNm,
            "right_mpr_positive_kNm": self.right_mpr_positive_kNm,
            "right_mpr_negative_kNm": self.right_mpr_negative_kNm,
            "actual_capacity_shear_kN": self.actual_capacity_shear_kN,
            "etabs_capacity_shear_kN": self.etabs_capacity_shear_kN,
            "final_capacity_shear_kN": self.final_capacity_shear_kN,
            "vu_from_actual_rebar_kN": self.vu_from_actual_rebar_kN,
            "required_av_over_s_mm2_per_mm": self.required_av_over_s_mm2_per_mm,
            "control_source": self.control_source,
            "source": self.source,
            "vc_zero": self.vc_zero,
            "gravity_shear_kN": self.gravity_shear_kN,
            "message": self.message,
        }


@dataclass(frozen=True)
class HoopPosition:
    location_mm: float
    zone: str
    is_first_from_support: bool = False


@dataclass(frozen=True)
class DetailingZone:
    name: str
    kind: str
    start_mm: float
    end_mm: float
    maximum_spacing_mm: float
    selected_spacing_mm: float | None
    controlling_rule: str
    first_hoop_location_mm: float | None = None
    status: str = "OK"

    @property
    def length_mm(self) -> float:
        return max(0.0, self.end_mm - self.start_mm)


@dataclass(frozen=True)
class RuleCheck:
    rule_id: str
    clause: str
    status: str
    actual: float | str | None = None
    limit: float | str | None = None
    message: str = ""
    source: str = ""


@dataclass(frozen=True)
class RebarLayoutGeometry:
    """Coordinate-ready summary of the selected longitudinal layout.

    The current selector provides layer counts and effective depth.  Exact
    x/z bar coordinates are optional so later optimization can replace the
    conservative summary without changing workbook or manifest schemas.
    """

    width_mm: float
    depth_mm: float
    effective_depth_mm: float
    stirrup_diameter_mm: float
    clear_cover_mm: float
    top_layers: tuple[tuple[float, ...], ...] = ()
    bottom_layers: tuple[tuple[float, ...], ...] = ()
    coordinates_resolved: bool = False


@dataclass(frozen=True)
class ConstructabilityResult:
    status: str
    zones: tuple[DetailingZone, ...] = ()
    hoop_positions: tuple[HoopPosition, ...] = ()
    checks: tuple[RuleCheck, ...] = ()
    warnings: tuple[str, ...] = ()
    layout_geometry: RebarLayoutGeometry | None = None

    @property
    def blocking_statuses(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    check.status
                    for check in self.checks
                    if check.status not in {"PASS", "INFO", "WARN"}
                }
                | ({self.status} if self.status not in {"OK", "REVIEW"} else set())
            )
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "zones": [
                {
                    "name": zone.name,
                    "kind": zone.kind,
                    "start_mm": zone.start_mm,
                    "end_mm": zone.end_mm,
                    "length_mm": zone.length_mm,
                    "maximum_spacing_mm": zone.maximum_spacing_mm,
                    "selected_spacing_mm": zone.selected_spacing_mm,
                    "controlling_rule": zone.controlling_rule,
                    "first_hoop_location_mm": zone.first_hoop_location_mm,
                    "status": zone.status,
                }
                for zone in self.zones
            ],
            "hoop_positions": [
                {
                    "location_mm": hoop.location_mm,
                    "zone": hoop.zone,
                    "is_first_from_support": hoop.is_first_from_support,
                }
                for hoop in self.hoop_positions
            ],
            "checks": [
                {
                    "rule_id": check.rule_id,
                    "clause": check.clause,
                    "status": check.status,
                    "actual": check.actual,
                    "limit": check.limit,
                    "message": check.message,
                    "source": check.source,
                }
                for check in self.checks
            ],
            "warnings": list(self.warnings),
            "layout_geometry": (
                None
                if self.layout_geometry is None
                else {
                    "width_mm": self.layout_geometry.width_mm,
                    "depth_mm": self.layout_geometry.depth_mm,
                    "effective_depth_mm": self.layout_geometry.effective_depth_mm,
                    "stirrup_diameter_mm": self.layout_geometry.stirrup_diameter_mm,
                    "clear_cover_mm": self.layout_geometry.clear_cover_mm,
                    "top_layers": [list(layer) for layer in self.layout_geometry.top_layers],
                    "bottom_layers": [list(layer) for layer in self.layout_geometry.bottom_layers],
                    "coordinates_resolved": self.layout_geometry.coordinates_resolved,
                }
            ),
        }


def build_constructability(
    *,
    is_smf: bool,
    span_length_mm: float,
    beam_depth_mm: float,
    effective_depth_mm: float,
    smallest_primary_bar_diameter_mm: float,
    left_support: SupportFaceContext | None,
    right_support: SupportFaceContext | None,
    left_spacing_mm: float | None,
    middle_spacing_mm: float | None,
    right_spacing_mm: float | None,
    allowed_spacings_mm: Sequence[int] = (100, 110, 130, 150, 170, 200),
    smf_end_project_limit_mm: float = 100.0,
    yield_locations_mm: Iterable[float] = (),
    seismic_context: SeismicDetailingContext | None = None,
    beam_width_mm: float | None = None,
    concrete_strength_mpa: float | None = None,
    layout_geometry: RebarLayoutGeometry | None = None,
) -> ConstructabilityResult:
    """Build support-face zones and hoop coordinates.

    SMF output is deliberately review-gated when support faces are missing.
    Ordinary beams still receive a useful full-span zone because their basic
    detailing does not require an earthquake joint-face coordinate.
    """

    if span_length_mm <= 0 or beam_depth_mm <= 0 or effective_depth_mm <= 0:
        return ConstructabilityResult(
            "INVALID_SPAN_GEOMETRY",
            checks=(
                RuleCheck(
                    "DET-GEOMETRY",
                    "ACI 318-14 9/18",
                    "INVALID_SPAN_GEOMETRY",
                    message="梁长度、梁高和有效高度必须为正数。",
                ),
            ),
        )
    if not is_smf:
        return ConstructabilityResult(
            "OK",
            zones=(
                DetailingZone(
                    "ordinary_full_span",
                    "ORDINARY_BEAM",
                    0.0,
                    span_length_mm,
                    span_length_mm,
                    middle_spacing_mm,
                    "ACI 318-14 Ch. 9; project spacing policy",
                ),
            ),
            layout_geometry=layout_geometry,
        )

    checks: list[RuleCheck] = []
    # The following are eligibility screens, not a substitute for the
    # engineer's complete Chapter 18 classification review.
    if span_length_mm < 4.0 * effective_depth_mm - 1e-9:
        checks.append(
            RuleCheck(
                "SMF-DIMENSION-CLEAR-SPAN",
                "ACI 318-14 18.6.2",
                "SMF_DIMENSION_FAILED",
                actual=span_length_mm / effective_depth_mm,
                limit=4.0,
                message="SMF 梁净跨/有效高度小于 4，不能通过几何适用性筛查。",
                source="beam span and selected effective depth",
            )
        )
    if beam_width_mm is not None:
        width_limit = max(250.0, 0.3 * beam_depth_mm)
        if beam_width_mm + 1e-9 < width_limit:
            checks.append(
                RuleCheck(
                    "SMF-DIMENSION-WIDTH",
                    "ACI 318-14 18.6.2",
                    "SMF_DIMENSION_FAILED",
                    actual=beam_width_mm,
                    limit=width_limit,
                    message="梁宽未达到 SMF 梁宽度适用性筛查值。",
                    source="selected beam section",
                )
            )
    if (
        seismic_context is not None
        and seismic_context.axial_load_kN is not None
        and beam_width_mm is not None
        and concrete_strength_mpa is not None
        and beam_width_mm > 0
        and beam_depth_mm > 0
        and concrete_strength_mpa > 0
    ):
        axial_limit = 0.1 * beam_width_mm * beam_depth_mm * concrete_strength_mpa / 1000.0
        axial = abs(float(seismic_context.axial_load_kN))
        if axial > axial_limit + 1e-9:
            checks.append(
                RuleCheck(
                    "SMF-DIMENSION-AXIAL",
                    "ACI 318-14 18.6.2",
                    "SMF_DIMENSION_FAILED",
                    actual=axial,
                    limit=axial_limit,
                    message="梁轴力超过本项目 SMF 适用性筛查值，需人工确认构件类别。",
                    source=seismic_context.axial_load_source or seismic_context.source,
                )
            )
    if seismic_context is not None:
        code = str(seismic_context.design_code or "").strip()
        if code and code != "ACI 318-14":
            checks.append(
                RuleCheck(
                    "SMF-DESIGN-CODE",
                    "ACI 318-14 18.6",
                    "SMF_DESIGN_SOURCE_UNVERIFIED",
                    actual=code,
                    limit="ACI 318-14",
                    message="SMF 梁构造门禁固定按 ACI 318-14；来源规范不一致。",
                    source=seismic_context.source,
                )
            )
        frame_type = str(seismic_context.frame_type or "").strip().casefold()
        if not frame_type or "special" not in frame_type:
            checks.append(
                RuleCheck(
                    "SMF-FRAME-TYPE",
                    "ACI 318-14 18.6",
                    "SMF_DESIGN_SOURCE_UNVERIFIED",
                    actual=seismic_context.frame_type or None,
                    limit="Sway Special / project-confirmed SMF",
                    message="仅凭梁标签不能确认特殊抗弯体系，需人工确认 Frame Type 和体系归属。",
                    source=seismic_context.source,
                )
            )
        provenance = seismic_context.provenance
        capacity_verified = bool(
            seismic_context.capacity_shear_verified
            and (provenance is None or provenance.capacity_shear_included is not False)
        )
        has_mpr_envelope = (
            seismic_context.left_mpr_kNm is not None
            and seismic_context.right_mpr_kNm is not None
            and seismic_context.gravity_shear_kN is not None
        )
        if not capacity_verified and not has_mpr_envelope:
            checks.append(
                RuleCheck(
                    "SMF-CAPACITY-SHEAR-SOURCE",
                    "ACI 318-14 18.6.5",
                    "CAPACITY_SHEAR_SOURCE_UNVERIFIED",
                    actual=seismic_context.capacity_shear_source or None,
                    limit="verified ETABS capacity shear or engine Mpr calculation",
                    message="未确认 ETABS 已包含 SMF 容量设计剪力；应按 Mpr 重新计算并在加密区取 Vc=0。",
                    source=(provenance.source if provenance else seismic_context.source),
                )
            )
        checks.extend(
            evaluate_smf_capacity_shear(
                span_length_mm=span_length_mm,
                context=seismic_context,
            )
        )
    if left_support is None or right_support is None or not left_support.resolved or not right_support.resolved:
        checks.append(
            RuleCheck(
                "SMF-SUPPORT-FACE",
                "ACI 318-14 18.6.4",
                "SMF_SUPPORT_FACE_UNRESOLVED",
                message="SMF 梁必须从实际支承面量取 2h 和首箍位置；当前缺少支承面来源。",
            )
        )
        return ConstructabilityResult("SMF_SUPPORT_FACE_UNRESOLVED", checks=tuple(checks), layout_geometry=layout_geometry)

    left_face = left_support.face_location_mm
    right_face = right_support.face_location_mm
    if not (0.0 <= left_face < right_face <= span_length_mm):
        checks.append(
            RuleCheck(
                "SMF-SUPPORT-FACE-RANGE",
                "ACI 318-14 18.6.4",
                "SMF_SUPPORT_FACE_UNRESOLVED",
                actual=f"{left_face},{right_face}",
                limit=f"0 <= left < right <= {span_length_mm}",
                message="支承面里程不在梁跨范围内。",
            )
        )
        return ConstructabilityResult("SMF_SUPPORT_FACE_UNRESOLVED", checks=tuple(checks), layout_geometry=layout_geometry)

    if smallest_primary_bar_diameter_mm <= 0:
        return ConstructabilityResult("SMF_HOOP_ZONE_UNRESOLVED", layout_geometry=layout_geometry)

    end_limit = min(
        effective_depth_mm / 4.0,
        6.0 * smallest_primary_bar_diameter_mm,
        150.0,
        smf_end_project_limit_mm,
    )
    outside_limit = effective_depth_mm / 2.0
    two_h = 2.0 * beam_depth_mm
    left_end = min(left_face + two_h, right_face)
    right_start = max(right_face - two_h, left_face)
    left_spacing = _spacing(left_spacing_mm, end_limit, allowed_spacings_mm)
    right_spacing = _spacing(right_spacing_mm, end_limit, allowed_spacings_mm)
    middle_spacing = _spacing(middle_spacing_mm, outside_limit, allowed_spacings_mm)

    zones: list[DetailingZone] = []
    hoops: list[HoopPosition] = []
    if right_start <= left_end:
        spacing = _minimum_not_none(left_spacing, right_spacing)
        status = "OK" if spacing is not None else "SMF_HOOP_SPACING_FAILED"
        zone = DetailingZone(
            "smf_end_hoop_overlap",
            "SMF_2H_OVERLAP",
            left_face,
            right_face,
            end_limit,
            spacing,
            "ACI 318-14 18.6.4.1-.2; project SMF measure 3.1",
            min(left_face + 50.0, right_face),
            status,
        )
        zones.append(zone)
        hoops.extend(_place_hoops(zone, first_from_support=True))
    else:
        left_zone = DetailingZone(
            "smf_left_2h",
            "SMF_2H",
            left_face,
            left_end,
            end_limit,
            left_spacing,
            "ACI 318-14 18.6.4.1-.2; project SMF measure 3.1",
            min(left_face + 50.0, left_end),
            "OK" if left_spacing is not None else "SMF_HOOP_SPACING_FAILED",
        )
        right_zone = DetailingZone(
            "smf_right_2h",
            "SMF_2H",
            right_start,
            right_face,
            end_limit,
            right_spacing,
            "ACI 318-14 18.6.4.1-.2; project SMF measure 3.1",
            max(right_face - 50.0, right_start),
            "OK" if right_spacing is not None else "SMF_HOOP_SPACING_FAILED",
        )
        zones.extend((left_zone, right_zone))
        hoops.extend(_place_hoops(left_zone, first_from_support=True))
        hoops.extend(_place_hoops(right_zone, first_from_support=True, reverse=True))
        if right_start > left_end:
            middle_zone = DetailingZone(
                "smf_middle",
                "SMF_NON_HOOP",
                left_end,
                right_start,
                outside_limit,
                middle_spacing,
                "ACI 318-14 18.6.4.3",
                None,
                "OK" if middle_spacing is not None else "SMF_HOOP_SPACING_FAILED",
            )
            zones.insert(1, middle_zone)
            hoops.extend(_place_hoops(middle_zone))

    for index, location in enumerate(yield_locations_mm, start=1):
        location = float(location)
        if left_face <= location <= right_face:
            yield_zone = DetailingZone(
                f"smf_yield_{index}",
                "SMF_2H_YIELD",
                max(left_face, location - two_h),
                min(right_face, location + two_h),
                end_limit,
                _minimum_not_none(left_spacing, middle_spacing, right_spacing),
                "ACI 318-14 18.6.4.1(b); project SMF measure 3.1",
                None,
                "OK",
            )
            zones.append(yield_zone)
            hoops.extend(_place_hoops(yield_zone))
            checks.append(
                RuleCheck(
                    "SMF-YIELD-ZONE",
                    "ACI 318-14 18.6.4.1(b)",
                    "INFO",
                    actual=location,
                    limit=two_h,
                    message="已记录额外可能屈服位置，并在其两侧延伸 2h 加密区。",
                )
            )

    for zone in zones:
        if zone.selected_spacing_mm is None:
            checks.append(
                RuleCheck(
                    "SMF-HOOP-SPACING",
                    zone.controlling_rule,
                    "SMF_HOOP_SPACING_FAILED",
                    actual=None,
                    limit=zone.maximum_spacing_mm,
                    message=f"{zone.name} 没有可用的项目箍筋间距。",
                )
            )
        zone_hoops = sorted(
            hoop.location_mm
            for hoop in _dedupe_hoops(hoops)
            if zone.start_mm - 1e-9 <= hoop.location_mm <= zone.end_mm + 1e-9
        )
        if zone.selected_spacing_mm is not None and zone_hoops:
            gaps = [zone_hoops[0] - zone.start_mm, zone.end_mm - zone_hoops[-1]]
            gaps.extend(right - left for left, right in zip(zone_hoops, zone_hoops[1:]))
            actual_gap = max(gaps, default=0.0)
            checks.append(
                RuleCheck(
                    "SMF-ACTUAL-HOOP-SPACING",
                    zone.controlling_rule,
                    "PASS" if actual_gap <= zone.maximum_spacing_mm + 1e-9 else "SMF_ACTUAL_HOOP_SPACING_FAILED",
                    actual=actual_gap,
                    limit=zone.maximum_spacing_mm,
                    message=f"{zone.name} 按实际箍筋里程检查边界和相邻箍筋净间距。",
                )
            )
        else:
            checks.append(
                RuleCheck(
                    "SMF-HOOP-SPACING",
                    zone.controlling_rule,
                    "PASS",
                    actual=zone.selected_spacing_mm,
                    limit=zone.maximum_spacing_mm,
                    message=f"{zone.name} 箍筋间距满足上限。",
                )
            )
    checks.extend(
        (
            RuleCheck(
                "SMF-FIRST-HOOP-LEFT",
                "ACI 318-14 18.6.4.2",
                "PASS",
                actual=left_face + 50.0,
                limit=left_face + 50.0,
                message="左首箍距左支承面 50 mm。",
            ),
            RuleCheck(
                "SMF-FIRST-HOOP-RIGHT",
                "ACI 318-14 18.6.4.2",
                "PASS",
                actual=right_face - 50.0,
                limit=right_face - 50.0,
                message="右首箍距右支承面 50 mm。",
            ),
        )
    )
    status = "OK" if not any(
        check.status not in {"PASS", "INFO", "WARN"} for check in checks
    ) else "REVIEW"
    return ConstructabilityResult(
        status,
        zones=tuple(sorted(zones, key=lambda zone: (zone.start_mm, zone.end_mm, zone.name))),
        hoop_positions=tuple(sorted(_dedupe_hoops(hoops), key=lambda hoop: hoop.location_mm)),
        checks=tuple(checks),
        warnings=tuple(sorted({check.message for check in checks if check.status == "WARN"})),
        layout_geometry=layout_geometry,
    )


def evaluate_smf_capacity_shear(
    *,
    span_length_mm: float,
    context: SeismicDetailingContext,
) -> tuple[RuleCheck, ...]:
    """Check the provenance or a supplied Mpr-based capacity-shear envelope.

    A complete gravity-load extraction remains project-specific.  When the
    caller supplies both end ``Mpr`` values and gravity shear, this function
    calculates the simple end-moment envelope and explicitly sets the SMF
    confined-zone concrete contribution to ``Vc=0`` in the audit message.
    Otherwise it fails closed with a source-unverified status.
    """

    provenance = context.provenance
    if context.capacity_shear_verified and (
        provenance is None or provenance.capacity_shear_included is not False
    ):
        return (
            RuleCheck(
                "SMF-CAPACITY-SHEAR",
                "ACI 318-14 18.6.5",
                "PASS",
                actual=context.capacity_shear_source or "verified source",
                limit="ETABS capacity shear included",
                message="ETABS 容量设计剪力来源已确认；加密区按 Vc=0 复核。",
                source=provenance.source if provenance else context.source,
            ),
        )
    if (
        span_length_mm > 0
        and context.left_mpr_kNm is not None
        and context.right_mpr_kNm is not None
        and context.gravity_shear_kN is not None
    ):
        envelope = (
            abs(float(context.left_mpr_kNm)) + abs(float(context.right_mpr_kNm))
        ) * 1000.0 / span_length_mm + abs(float(context.gravity_shear_kN))
        demand = context.capacity_shear_demand_kN
        return (
            RuleCheck(
                "SMF-CAPACITY-SHEAR",
                "ACI 318-14 18.6.5",
                "PASS" if demand is None or envelope + 1e-9 >= abs(float(demand)) else "CAPACITY_SHEAR_SOURCE_UNVERIFIED",
                actual=envelope,
                limit=(abs(float(demand)) if demand is not None else "Mpr envelope"),
                message="已按两端 Mpr 与重力剪力形成容量剪力包络；SMF 加密区采用 Vc=0。",
                source="engine Mpr + gravity shear inputs",
            ),
        )
    return (
        RuleCheck(
            "SMF-CAPACITY-SHEAR",
            "ACI 318-14 18.6.5",
            "CAPACITY_SHEAR_SOURCE_UNVERIFIED",
            actual=context.capacity_shear_source or None,
            limit="verified ETABS capacity shear or Mpr inputs",
            message="缺少容量剪力来源或 Mpr/重力剪力输入；不能把 ETABS VMajorArea 当作已完成容量设计。",
            source=provenance.source if provenance else context.source,
        ),
    )


def calculate_smf_capacity_shear(
    *,
    span_length_mm: float,
    beam_width_mm: float,
    fc_mpa: float,
    fy_mpa: float,
    fyt_mpa: float,
    effective_depth_mm: float,
    left_region: Any,
    right_region: Any,
    context: SeismicDetailingContext,
) -> CapacityShearReview:
    """Calculate the SMF shear demand that must reach hoop selection.

    ``context.capacity_shear_demand_kN`` is an optional, already-normalized
    ETABS capacity-design ``Vu``.  ``VMajorArea`` remains an ``Av/s`` field;
    it is converted to force only in the explicit processed-table bridge
    below, together with ETABS Provided steel, a project Mpr factor and the
    confined-zone ``Vc=0`` assumption.  If end ``Mpr`` values are supplied
    explicitly, they are used.  Otherwise selected layouts are converted from
    the rectangular stress-block screening when a project-supplied factor is
    present.  Missing source evidence stays unverified instead of silently
    becoming a nominal-moment design.
    """

    if not context.is_smf:
        return CapacityShearReview("NOT_APPLICABLE", vc_zero=False)
    if span_length_mm <= 0 or effective_depth_mm <= 0 or fyt_mpa <= 0:
        return CapacityShearReview(
            "CAPACITY_SHEAR_SOURCE_UNVERIFIED",
            source="invalid section input",
            message="容量剪力复核缺少有效的净跨、有效高度或箍筋钢筋强度。",
        )

    left_positive, left_negative, right_positive, right_negative, mpr_source = _mpr_pairs(
        left_region=left_region,
        right_region=right_region,
        beam_width_mm=beam_width_mm,
        fc_mpa=fc_mpa,
        fy_mpa=fy_mpa,
        effective_depth_mm=effective_depth_mm,
        context=context,
    )
    etabs_demand = _nonnegative_or_none(context.capacity_shear_demand_kN)

    actual_capacity: float | None = None
    gravity_shear = _nonnegative_or_none(context.gravity_shear_kN)
    if all(value is not None for value in (left_positive, left_negative, right_positive, right_negative)):
        moment_envelopes = (
            abs(float(left_positive)) + abs(float(right_negative)),
            abs(float(left_negative)) + abs(float(right_positive)),
        )
        if gravity_shear is not None:
            actual_capacity = max(moment_envelopes) * 1000.0 / span_length_mm + gravity_shear
        elif etabs_demand is None:
            inferred = _infer_vu_from_processed_regions(
                left_region=left_region,
                right_region=right_region,
                beam_width_mm=beam_width_mm,
                fc_mpa=fc_mpa,
                fy_mpa=fy_mpa,
                fyt_mpa=fyt_mpa,
                effective_depth_mm=effective_depth_mm,
                span_length_mm=span_length_mm,
                factor=context.mpr_strength_factor,
                phi=context.capacity_shear_phi,
                vc_zero=context.capacity_shear_vc_zero,
            )
            if inferred is not None:
                etabs_demand, gravity_shear, etabs_source = inferred
                actual_capacity = max(moment_envelopes) * 1000.0 / span_length_mm + gravity_shear
                mpr_source = f"{mpr_source}; {etabs_source}"
            else:
                return CapacityShearReview(
                    "CAPACITY_SHEAR_SOURCE_UNVERIFIED",
                    left_positive,
                    left_negative,
                    right_positive,
                    right_negative,
                    etabs_capacity_shear_kN=etabs_demand,
                    source=mpr_source,
                    vc_zero=context.capacity_shear_vc_zero,
                    message="已取得自动配筋 Mpr，但处理后的梁表缺少可用于反算 ETABS Vu 的 Provided 配筋或 Av/s。",
                )

    if actual_capacity is None and not context.capacity_shear_verified:
        return CapacityShearReview(
            "CAPACITY_SHEAR_SOURCE_UNVERIFIED",
            left_positive,
            left_negative,
            right_positive,
            right_negative,
            etabs_capacity_shear_kN=etabs_demand,
            source=mpr_source or context.capacity_shear_source,
            vc_zero=context.capacity_shear_vc_zero,
            gravity_shear_kN=gravity_shear,
            message="缺少已确认 ETABS Vu 或最终实际配筋的 Mpr/重力剪力输入。",
        )

    final_demand = max(
        value for value in (actual_capacity, etabs_demand) if value is not None
    ) if (actual_capacity is not None or etabs_demand is not None) else None
    if final_demand is None:
        return CapacityShearReview(
            "PASS" if context.capacity_shear_verified else "CAPACITY_SHEAR_SOURCE_UNVERIFIED",
            left_positive,
            left_negative,
            right_positive,
            right_negative,
            source=mpr_source or context.capacity_shear_source,
            vc_zero=context.capacity_shear_vc_zero,
            gravity_shear_kN=gravity_shear,
            message=(
                "ETABS 容量剪力来源已确认，但未提供数值 Vu；保留来源审计，不能换算 Av/s。"
                if context.capacity_shear_verified
                else "容量剪力数值为空。"
            ),
        )

    control_source = "Vcap_actual" if actual_capacity is not None and (
        etabs_demand is None or actual_capacity >= etabs_demand - 1e-9
    ) else "ETABS_CAPACITY_INCLUDED"
    required_av = None
    if context.capacity_shear_vc_zero:
        required_av = final_demand * 1000.0 / (context.capacity_shear_phi * fyt_mpa * effective_depth_mm)
    review_status = (
        "PASS"
        if context.capacity_shear_verified
        else "ENGINE_MPR_FALLBACK"
        if actual_capacity is not None
        else "CAPACITY_SHEAR_SOURCE_UNVERIFIED"
    )
    return CapacityShearReview(
        review_status,
        left_positive,
        left_negative,
        right_positive,
        right_negative,
        actual_capacity_shear_kN=actual_capacity,
        etabs_capacity_shear_kN=etabs_demand,
        final_capacity_shear_kN=final_demand,
        required_av_over_s_mm2_per_mm=required_av,
        control_source=control_source,
        source=mpr_source or context.capacity_shear_source,
        vc_zero=context.capacity_shear_vc_zero,
        gravity_shear_kN=gravity_shear,
        message=(
            "已按处理后的梁跨和自动配筋计算实际 Vu，并与 ETABS Av/s 换算值复核；SMF 加密区采用 Vc=0。"
            if context.capacity_shear_verified and context.capacity_shear_vc_zero
            else "ETABS 容量剪力不可用，已按最终实际配筋 Mpr 和重力剪力回退计算；SMF 加密区采用 Vc=0。"
            if actual_capacity is not None and context.capacity_shear_vc_zero
            else "容量剪力已进入横筋需求。"
        ),
    )


def _mpr_pairs(
    *,
    left_region: Any,
    right_region: Any,
    beam_width_mm: float,
    fc_mpa: float,
    fy_mpa: float,
    effective_depth_mm: float,
    context: SeismicDetailingContext,
) -> tuple[float | None, float | None, float | None, float | None, str]:
    explicit = (
        context.left_mpr_kNm,
        context.left_mpr_kNm,
        context.right_mpr_kNm,
        context.right_mpr_kNm,
    )
    if all(value is not None for value in explicit):
        return (*[abs(float(value)) for value in explicit], context.mpr_strength_source or "context Mpr inputs")
    factor = context.mpr_strength_factor
    if factor is None:
        return None, None, None, None, ""
    left_positive = _layout_mpr_kNm(
        left_region,
        tension="bottom",
        beam_width_mm=beam_width_mm,
        fc_mpa=fc_mpa,
        fy_mpa=fy_mpa,
        effective_depth_mm=effective_depth_mm,
        factor=factor,
    )
    left_negative = _layout_mpr_kNm(
        left_region,
        tension="top",
        beam_width_mm=beam_width_mm,
        fc_mpa=fc_mpa,
        fy_mpa=fy_mpa,
        effective_depth_mm=effective_depth_mm,
        factor=factor,
    )
    right_positive = _layout_mpr_kNm(
        right_region,
        tension="bottom",
        beam_width_mm=beam_width_mm,
        fc_mpa=fc_mpa,
        fy_mpa=fy_mpa,
        effective_depth_mm=effective_depth_mm,
        factor=factor,
    )
    right_negative = _layout_mpr_kNm(
        right_region,
        tension="top",
        beam_width_mm=beam_width_mm,
        fc_mpa=fc_mpa,
        fy_mpa=fy_mpa,
        effective_depth_mm=effective_depth_mm,
        factor=factor,
    )
    return left_positive, left_negative, right_positive, right_negative, context.mpr_strength_source or "selected layout + project Mpr factor"


def _layout_mpr_kNm(
    region: Any,
    *,
    tension: str,
    beam_width_mm: float,
    fc_mpa: float,
    fy_mpa: float,
    effective_depth_mm: float,
    factor: float,
) -> float:
    top = _provided_area(getattr(region, "top", None))
    bottom = _provided_area(getattr(region, "bottom", None))
    tension_area, compression_area = (bottom, top) if tension == "bottom" else (top, bottom)
    return _nominal_moment(
        tension_area,
        compression_area,
        beam_width_mm,
        fc_mpa,
        fy_mpa,
        effective_depth_mm,
    ) * factor / 1_000_000.0


def _infer_vu_from_processed_regions(
    *,
    left_region: Any,
    right_region: Any,
    beam_width_mm: float,
    fc_mpa: float,
    fy_mpa: float,
    fyt_mpa: float,
    effective_depth_mm: float,
    span_length_mm: float,
    factor: float | None,
    phi: float,
    vc_zero: bool,
) -> tuple[float, float, str] | None:
    """Infer ETABS Vu from the processed span table under explicit assumptions.

    The ``_2`` table exposes ETABS ``VMajorArea`` (Av/s) and Provided
    longitudinal areas.  With an explicit project Mpr factor and ``Vc=0``
    declared for the confined zone, Av/s is converted to a force, the ETABS
    Mpr envelope is removed as a gravity residual, and the selected layout's
    Mpr envelope is put back.  This is a verification bridge, not a claim
    that VMajorArea itself is a force value.
    """

    if factor is None or not vc_zero or phi <= 0 or fyt_mpa <= 0 or effective_depth_mm <= 0:
        return None
    left_demand = getattr(left_region, "demand", left_region)
    right_demand = getattr(right_region, "demand", right_region)
    av_values = (
        _nonnegative_or_none(getattr(left_demand, "av_over_s_mm2_per_mm", None)),
        _nonnegative_or_none(getattr(right_demand, "av_over_s_mm2_per_mm", None)),
    )
    etabs_areas = (
        _etabs_mpr_pairs(left_demand, beam_width_mm, fc_mpa, fy_mpa, effective_depth_mm, factor),
        _etabs_mpr_pairs(right_demand, beam_width_mm, fc_mpa, fy_mpa, effective_depth_mm, factor),
    )
    if any(value is None for value in av_values) or any(pair is None for pair in etabs_areas):
        return None
    etabs_vu = max(float(value) for value in av_values) * phi * fyt_mpa * effective_depth_mm / 1000.0
    etabs_mpr_envelope = max(
        abs(etabs_areas[0][0]) + abs(etabs_areas[1][1]),
        abs(etabs_areas[0][1]) + abs(etabs_areas[1][0]),
    ) * 1000.0 / span_length_mm
    gravity = max(0.0, etabs_vu - etabs_mpr_envelope)
    return etabs_vu, gravity, "processed span + ETABS VMajorArea (Av/s), Provided and Vc=0 conversion"


def _etabs_mpr_pairs(
    demand: Any,
    beam_width_mm: float,
    fc_mpa: float,
    fy_mpa: float,
    effective_depth_mm: float,
    factor: float,
) -> tuple[float, float] | None:
    top = _nonnegative_or_none(getattr(demand, "as_top_etabs_mm2", None))
    bottom = _nonnegative_or_none(getattr(demand, "as_bottom_etabs_mm2", None))
    if top is None or bottom is None or top <= 0 or bottom <= 0:
        return None
    positive = _nominal_moment(bottom, top, beam_width_mm, fc_mpa, fy_mpa, effective_depth_mm)
    negative = _nominal_moment(top, bottom, beam_width_mm, fc_mpa, fy_mpa, effective_depth_mm)
    return positive * factor / 1_000_000.0, negative * factor / 1_000_000.0


def _nonnegative_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return abs(float(value))


def evaluate_smf_longitudinal(
    *,
    width_mm: float,
    fc_mpa: float,
    fy_mpa: float,
    effective_depth_mm: float,
    left_top: Any,
    left_bottom: Any,
    middle_top: Any,
    middle_bottom: Any,
    right_top: Any,
    right_bottom: Any,
    maximum_ratio: float = 0.01613,
) -> tuple[RuleCheck, ...]:
    """Evaluate the SMF longitudinal checks using the selected bar layouts.

    The moment calculation is an explicitly labelled rectangular stress-block
    screening calculation.  A project may replace it with a verified section
    analysis provider without changing the output contract.
    """

    designs = (
        ("left", left_top, left_bottom),
        ("middle", middle_top, middle_bottom),
        ("right", right_top, right_bottom),
    )
    checks: list[RuleCheck] = []
    face_strengths: dict[str, tuple[float, float]] = {}
    for name, top, bottom in designs:
        top_count = _count_primary(top)
        bottom_count = _count_primary(bottom)
        top_area = _provided_area(top)
        bottom_area = _provided_area(bottom)
        checks.append(
            RuleCheck(
                f"SMF-CONTINUOUS-BARS-{name.upper()}",
                "ACI 318-14 18.6.3.1",
                "PASS" if top_count >= 2 and bottom_count >= 2 else "SMF_LONGITUDINAL_COUNT_FAILED",
                actual=f"top={top_count},bottom={bottom_count}",
                limit="top>=2,bottom>=2",
                message=f"{name} 截面顶部和底部连续纵筋数量。",
            )
        )
        max_area = max(top_area, bottom_area)
        ratio = max_area / (width_mm * effective_depth_mm) if width_mm > 0 and effective_depth_mm > 0 else 0.0
        checks.append(
            RuleCheck(
                f"SMF-RHO-MAX-{name.upper()}",
                "Project SMF measure 2.2; ACI 318-14 18.6.3.1",
                "PASS" if ratio <= maximum_ratio + 1e-9 else "SMF_LONGITUDINAL_RATIO_FAILED",
                actual=ratio,
                limit=maximum_ratio,
                message=f"{name} 截面最大受拉纵筋率（项目延性限值）。",
                source="Nasiriyah SMF/SB measure rev.1 (2026-08-24); selected layout / b*d",
            )
        )
        face_strengths[name] = (
            _nominal_moment(bottom_area, top_area, width_mm, fc_mpa, fy_mpa, effective_depth_mm),
            _nominal_moment(top_area, bottom_area, width_mm, fc_mpa, fy_mpa, effective_depth_mm),
        )
    for name in ("left", "right"):
        positive, negative = face_strengths[name]
        checks.append(
            RuleCheck(
                f"SMF-POSITIVE-NEGATIVE-{name.upper()}",
                "ACI 318-14 18.6.3.2",
                "PASS" if positive + 1e-9 >= 0.5 * negative else "SMF_STRENGTH_RATIO_FAILED",
                actual=positive / negative if negative > 0 else 0.0,
                limit=0.5,
                message=f"{name} 支承面正负弯矩强度比。",
                source="rectangular stress-block screening",
            )
        )
    max_face_strength = max((value for pair in face_strengths.values() for value in pair), default=0.0)
    for name, strengths in face_strengths.items():
        for sign, value in zip(("positive", "negative"), strengths):
            checks.append(
                RuleCheck(
                    f"SMF-MIN-STRENGTH-{name.upper()}-{sign.upper()}",
                    "ACI 318-14 18.6.3.2",
                    "PASS" if value + 1e-9 >= 0.25 * max_face_strength else "SMF_STRENGTH_RATIO_FAILED",
                    actual=value / max_face_strength if max_face_strength > 0 else 0.0,
                    limit=0.25,
                    message=f"{name} 截面 {sign} 弯矩强度相对端部最大值。",
                    source="rectangular stress-block screening",
                )
            )
    return tuple(checks)


def evaluate_ordinary_constructability(
    *,
    depth_mm: float,
    mode: str,
    torsion_longitudinal_area_mm2: float = 0.0,
    crack_control_spacing_mm: float | None = None,
    crack_control_limit_mm: float | None = None,
    anchorage_verified: bool = False,
    splice_verified: bool = False,
) -> tuple[RuleCheck, ...]:
    """Return ordinary-beam checks that are independent of ETABS demand points.

    The existing selector does not yet carry bar coordinates, development
    lengths, or crack-control service stresses.  We therefore expose those
    gaps explicitly instead of presenting a three-station area match as a
    complete construction check.  Callers can promote the informational
    records to a blocking failure once the corresponding inputs are supplied.
    """

    checks: list[RuleCheck] = []
    if crack_control_spacing_mm is not None and crack_control_limit_mm is not None:
        checks.append(
            RuleCheck(
                "ORD-CRACK-CONTROL-SPACING",
                "ACI 318-14 24.3 / project serviceability basis",
                "PASS" if crack_control_spacing_mm <= crack_control_limit_mm + 1e-9 else "CRACK_CONTROL_SPACING_FAILED",
                actual=crack_control_spacing_mm,
                limit=crack_control_limit_mm,
                message="裂缝控制最大纵筋间距与普通净距检查分开记录。",
                source="service stress / project input",
            )
        )
    else:
        checks.append(
            RuleCheck(
                "ORD-CRACK-CONTROL-SPACING",
                "ACI 318-14 24.3",
                "INFO",
                message="尚未提供使用阶段钢筋应力和裂缝控制间距限值；当前仅完成几何净距检查。",
            )
        )
    if torsion_longitudinal_area_mm2 > 0.0 or mode == "FACE_TORSION":
        checks.append(
            RuleCheck(
                "ORD-TORSION-PERIMETER",
                "ACI 318-14 9.7.5",
                "TORSION_PERIMETER_LAYOUT_UNVERIFIED",
                actual=torsion_longitudinal_area_mm2,
                message="抗扭纵筋必须沿封闭箍周边布置并校核四角与周边间距；当前结果尚无实际坐标。",
                source="selected face/torsion area without bar coordinates",
            )
        )
    checks.append(
        RuleCheck(
            "ORD-ANCHORAGE",
            "ACI 318-14 9.7.3",
            "PASS" if anchorage_verified else "ANCHORAGE_UNVERIFIED",
            message=(
                "已提供支承几何、材料和锚固输入。"
                if anchorage_verified
                else "缺少直锚/弯钩/机械锚固长度与可用支承长度，不能标记锚固合格。"
            ),
        )
    )
    checks.append(
        RuleCheck(
            "ORD-SPLICE",
            "ACI 318-14 9.7.3 / 18.6.3",
            "PASS" if splice_verified else "SPLICE_UNVERIFIED",
            message=(
                "已核对搭接长度、横向约束和允许搭接区。"
                if splice_verified
                else "当前仅表示钢筋规格过渡，未完成搭接长度和 SMF 接头禁区校核。"
            ),
        )
    )
    return tuple(checks)


def _provided_area(design: Any) -> float:
    return float(getattr(design, "provided_area_mm2", 0.0) or 0.0)


def _count_primary(design: Any) -> int:
    return sum(int(getattr(bar, "count", 0)) for bar in getattr(design, "bars", ()) if getattr(bar, "role", "") != "hanger")


def _nominal_moment(
    tension_area: float,
    compression_area: float,
    width_mm: float,
    fc_mpa: float,
    fy_mpa: float,
    effective_depth_mm: float,
) -> float:
    if min(tension_area, width_mm, fc_mpa, fy_mpa, effective_depth_mm) <= 0:
        return 0.0
    a = min(tension_area * fy_mpa / (0.85 * fc_mpa * width_mm), effective_depth_mm)
    concrete = tension_area * fy_mpa * max(0.0, effective_depth_mm - a / 2.0)
    compression_steel = compression_area * fy_mpa * max(0.0, effective_depth_mm - 40.0)
    return concrete + compression_steel


def _spacing(value: float | None, limit: float, allowed: Sequence[int]) -> float | None:
    if value is None or value <= 0:
        return None
    if value > limit + 1e-9:
        return None
    if allowed and all(abs(float(candidate) - value) > 1e-9 for candidate in allowed):
        return None
    return float(value)


def _minimum_not_none(*values: float | None) -> float | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _place_hoops(zone: DetailingZone, *, first_from_support: bool = False, reverse: bool = False) -> list[HoopPosition]:
    spacing = zone.selected_spacing_mm
    if spacing is None or zone.end_mm <= zone.start_mm:
        return []
    if reverse:
        first = zone.first_hoop_location_mm if first_from_support else zone.end_mm
        location = first if first is not None else zone.end_mm
        positions: list[HoopPosition] = []
        while location >= zone.start_mm - 1e-9:
            positions.append(HoopPosition(location, zone.name, first_from_support and len(positions) == 0))
            location -= spacing
        return positions
    first = zone.first_hoop_location_mm if first_from_support else zone.start_mm
    location = first if first is not None else zone.start_mm
    positions = []
    while location <= zone.end_mm + 1e-9:
        positions.append(HoopPosition(location, zone.name, first_from_support and len(positions) == 0))
        location += spacing
    return positions


def _dedupe_hoops(values: Iterable[HoopPosition]) -> tuple[HoopPosition, ...]:
    result: dict[float, HoopPosition] = {}
    for value in values:
        key = round(value.location_mm, 6)
        previous = result.get(key)
        if previous is None or value.is_first_from_support:
            result[key] = value
    return tuple(result.values())
