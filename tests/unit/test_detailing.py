import sys
from types import SimpleNamespace

import pytest

from src.design.detailing import (
    BarSpec,
    BeamDetailingEngine,
    BeamDetailingInput,
    BeamDetailingProfile,
    BeamDetailingResult,
    BeamRegionDemand,
    BeamSection,
    ContinuityOption,
    DetailingError,
    FaceRebarDesign,
    FaceRebarMode,
    LongitudinalDesign,
    ProjectDetailingPolicy,
    RegionDetailing,
    ReinforcementMode,
    StirrupDesign,
    coordinate_adjacent_spans,
    coordinate_intraspan_result,
    resolve_detailing_profile,
)
from src.design.constructability import SeismicDetailingContext, SupportFaceContext
from src.design.etabs_result import EtabsResultProvider
from src.design.provider import DesignResultStatus


def _input(
    name: str, width: float, depth: float, demands, *, smf: bool = True, length: float = 6000
) -> BeamDetailingInput:
    profile = BeamDetailingProfile.PB_SMF if smf else BeamDetailingProfile.PB_OMF
    section = BeamSection(name, width, depth, 40, 35, 420, 420, 20, length, profile)
    return BeamDetailingInput(section, tuple(demands))


def _demand(name: str, top: float, bottom: float, av: float, at: float, al: float) -> BeamRegionDemand:
    return BeamRegionDemand(name, {"left": 0, "middle": 3000, "right": 6000}[name], top, bottom, av, at, al)


def _manual_longitudinal(*groups: tuple[int, int, tuple[int, ...] | None]) -> LongitudinalDesign:
    bars = []
    for diameter, count, layers in groups:
        area = 3.141592653589793 * diameter**2 / 4
        bars.append(BarSpec(diameter, count, layers or (count,), area))
    provided = sum(bar.total_area_mm2 for bar in bars)
    return LongitudinalDesign(tuple(bars), provided, provided, max(len(bar.layers) for bar in bars), 25)


def _manual_result(
    profile: BeamDetailingProfile,
    top: tuple[LongitudinalDesign, LongitudinalDesign, LongitudinalDesign],
    bottom: tuple[LongitudinalDesign, LongitudinalDesign, LongitudinalDesign],
    *,
    width: float = 500,
    legs: int = 4,
) -> BeamDetailingResult:
    section = BeamSection("MANUAL", width, 700, 40, 35, 420, 420, 20, 6000, profile)
    regions = []
    for index, name in enumerate(("left", "middle", "right")):
        demand = _demand(name, top[index].required_area_mm2, bottom[index].required_area_mm2, 0.2, 0, 0)
        stirrup = StirrupDesign(10, 150, legs, 1.0, 0.5, 150, "manual")
        regions.append(
            RegionDetailing(
                name,
                demand,
                500,
                top[index].required_area_mm2,
                bottom[index].required_area_mm2,
                stirrup,
                top[index],
                bottom[index],
            )
        )
    return BeamDetailingResult(
        "MANUAL",
        "ACI 318-14",
        "test",
        "test",
        profile,
        section,
        ReinforcementMode.NORMAL,
        tuple(regions),
        FaceRebarDesign(FaceRebarMode.NOT_APPLICABLE, None, 0, 0, "manual"),
    )


def test_project_policy_rejects_diameter_outside_full_catalog() -> None:
    with pytest.raises(DetailingError):
        ProjectDetailingPolicy(enabled_diameters_mm=(10, 13))


def test_main_bar_selection_never_uses_diameter_below_t16() -> None:
    policy = ProjectDetailingPolicy(
        preferred_longitudinal_diameters_mm=(16,),
    )
    assert policy.ordered_longitudinal_diameters() == (25, 20, 16)
    assert all(value >= 16 for value in policy.ordered_longitudinal_diameters())


def test_project_policy_matches_nasiriyah_smf_sb_measure() -> None:
    policy = ProjectDetailingPolicy()

    assert policy.version == "nasiriyah-smf-sb-2026-08-24-r1"
    assert policy.enabled_diameters_mm == (10, 12, 16, 20, 25)
    assert policy.allowed_spacings_mm == (100, 150, 200)
    assert policy.smf_end_max_spacing_mm == pytest.approx(100.0)
    assert policy.smf_max_longitudinal_ratio == pytest.approx(0.01613)
    assert policy.face_min_depth_mm == pytest.approx(800.0)
    assert policy.face_max_spacing_mm == pytest.approx(250.0)


def test_project_smf_limits_have_a_legal_catalogue_solution_and_do_not_conflict_with_asmin() -> None:
    policy = ProjectDetailingPolicy()
    minimum_smf_db = policy.smf_end_zone_min_flexural_diameter_mm
    end_limit = min(6.0 * minimum_smf_db, policy.smf_end_max_spacing_mm)
    minimum_ratio = max(0.25 * 25**0.5 / 420, 1.4 / 420)

    assert any(spacing <= end_limit for spacing in policy.allowed_spacings_mm)
    assert minimum_ratio < policy.smf_max_longitudinal_ratio < 0.025


def test_profile_resolver_preserves_explicit_smf_for_sb() -> None:
    profile, source = resolve_detailing_profile("SB", ("Sway Special",))
    assert profile is BeamDetailingProfile.SMF
    assert "model-defined" in source


@pytest.mark.parametrize(
    ("frame_type", "expected"),
    [
        ("Non-Sway", BeamDetailingProfile.NON_SWAY),
        ("Sway Ordinary", BeamDetailingProfile.OMF),
        ("Sway Intermediate", BeamDetailingProfile.IMF),
    ],
)
def test_profile_resolver_preserves_explicit_non_smf_type(frame_type: str, expected: BeamDetailingProfile) -> None:
    profile, _ = resolve_detailing_profile("PB", (frame_type,))
    assert profile is expected


def test_profile_resolver_maps_sway_special_pb_to_smf() -> None:
    profile, _ = resolve_detailing_profile("PB", ("Sway Special",))
    assert profile is BeamDetailingProfile.PB_SMF


def test_profile_resolver_uses_smf_project_default_for_program_determined() -> None:
    profile, _ = resolve_detailing_profile("PB", ("Program Determined",))
    assert profile is BeamDetailingProfile.SMF


def test_review_required_profile_is_fail_closed_in_engine() -> None:
    section = BeamSection(
        "PB-REVIEW",
        500,
        700,
        40,
        35,
        420,
        420,
        20,
        6000,
        BeamDetailingProfile.PB_REVIEW_REQUIRED,
    )
    demands = tuple(_demand(region, 1000, 1000, 0.2, 0, 0) for region in ("left", "middle", "right"))
    with pytest.raises(DetailingError, match="DETAILING_PROFILE_REVIEW_REQUIRED"):
        BeamDetailingEngine().design(BeamDetailingInput(section, demands))


def test_station_envelope_can_override_asflex_from_same_station_calculation() -> None:
    demand = BeamRegionDemand("left", 0, 2000, 1500, 0.5, 0.1, 800, as_flex_top_override_mm2=1750)
    assert demand.as_flex_top_mm2 == 1750
    assert demand.as_flex_bottom_mm2 == 1100


def test_case_one_selects_stirrups_first_and_keeps_shallow_face_na() -> None:
    data = _input(
        "CASE-1",
        500,
        700,
        (
            _demand("left", 2750, 1800, 1.20, 0.40, 800),
            _demand("middle", 1300, 2200, 0.75, 0.00, 0),
            _demand("right", 2850, 1800, 1.10, 0.35, 800),
        ),
    )
    result = BeamDetailingEngine().design(data)
    assert result.mode is ReinforcementMode.NORMAL
    assert result.face_rebar.mode is FaceRebarMode.NOT_APPLICABLE
    assert all(region.stirrup.legs == 4 for region in result.regions)
    assert result.regions[0].stirrup.notation == "T10@100"
    assert result.regions[1].stirrup.notation == "T10@200"
    assert result.regions[0].top.provided_area_mm2 >= 2750
    assert result.regions[1].bottom.provided_area_mm2 >= 2200


def test_smf_capacity_shear_enters_stirrup_selection_before_final_result() -> None:
    data = _input(
        "SMF-CAPACITY",
        400,
        600,
        (
            _demand("left", 1800, 1800, 0.05, 0, 0),
            _demand("middle", 1200, 1200, 0.05, 0, 0),
            _demand("right", 1800, 1800, 0.05, 0, 0),
        ),
    )
    context = SeismicDetailingContext(
        is_smf=True,
        frame_type="Sway Special",
        left_mpr_kNm=450.0,
        right_mpr_kNm=500.0,
        gravity_shear_kN=60.0,
        capacity_shear_verified=True,
        capacity_shear_source="fixture capacity Vu",
    )
    data = BeamDetailingInput(
        data.section,
        data.regions,
        left_support=SupportFaceContext("L", 300.0, source="fixture"),
        right_support=SupportFaceContext("R", 5700.0, source="fixture"),
        seismic_context=context,
    )

    result = BeamDetailingEngine().design(data)

    assert result.capacity_shear_review is not None
    assert result.capacity_shear_review.status == "PASS"
    required = result.capacity_shear_review.required_av_over_s_mm2_per_mm
    assert required is not None
    assert result.regions[0].demand.capacity_shear_av_over_s_mm2_per_mm == pytest.approx(required)
    assert result.regions[2].demand.capacity_shear_av_over_s_mm2_per_mm == pytest.approx(required)
    assert result.regions[1].demand.capacity_shear_av_over_s_mm2_per_mm is None
    assert result.regions[0].stirrup.required_av_over_s_mm2_per_mm >= required
    assert len({region.bottom.notation for region in result.regions}) == 1
    assert result.capacity_shear_review.left_mpr_kNm is not None
    assert result.capacity_shear_review.right_mpr_kNm is not None


def test_multilayer_longitudinal_layout_never_leaves_one_bar_in_last_layer() -> None:
    data = _input(
        "PRACTICAL-LAYERS",
        350,
        700,
        (
            _demand("left", 2700, 1000, 0.20, 0.00, 0),
            _demand("middle", 2700, 1000, 0.20, 0.00, 0),
            _demand("right", 2700, 1000, 0.20, 0.00, 0),
        ),
    )
    result = BeamDetailingEngine().design(data)
    assert result.regions[0].top.notation == "6T25 4/2"
    assert result.regions[0].top.bars[0].layers == (4, 2)
    assert all(value >= 2 for value in result.regions[0].top.bars[0].layers)


def test_case_two_triggers_span_wide_torsion_face_mode() -> None:
    data = _input(
        "CASE-2",
        450,
        1000,
        (
            _demand("left", 7500, 6000, 2.30, 1.00, 4000),
            _demand("middle", 4800, 6000, 1.00, 0.55, 2600),
            _demand("right", 7000, 5600, 2.10, 0.90, 3300),
        ),
    )
    result = BeamDetailingEngine().design(data)
    assert result.mode is ReinforcementMode.FACE_TORSION
    assert result.face_rebar.mode is FaceRebarMode.TORSION_AND_SKIN
    assert result.face_rebar.notation == "7T20"
    assert result.face_rebar.provided_each_side_mm2 >= 2000
    for region in result.regions:
        assert region.top.provided_area_mm2 >= region.as_flex_top_mm2
        assert region.bottom.provided_area_mm2 >= region.as_flex_bottom_mm2
        assert region.stirrup.legs == 4
        assert any(name == "TORSION_PH_OVER_8" for name, _ in region.stirrup.spacing_limits)
        assert any(name == "TORSION_ABSOLUTE" for name, _ in region.stirrup.spacing_limits)


def test_face_mode_that_cannot_place_asmin_fails_with_spec_status() -> None:
    data = _input(
        "MIN-FLEX-CONFLICT",
        90,
        1000,
        tuple(_demand(region, 1000, 1000, 0.2, 0.5, 3000) for region in ("left", "middle", "right")),
        smf=False,
    )

    with pytest.raises(DetailingError, match="MIN_FLEXURE_CONFLICT"):
        BeamDetailingEngine().design(data)


def test_deep_non_torsion_beam_gets_default_t10_skin_bars() -> None:
    data = _input(
        "DEEP-SKIN",
        500,
        1000,
        (
            _demand("left", 1000, 1000, 0.20, 0.00, 0),
            _demand("middle", 1000, 1000, 0.20, 0.00, 0),
            _demand("right", 1000, 1000, 0.20, 0.00, 0),
        ),
        smf=False,
    )
    result = BeamDetailingEngine().design(data)
    assert result.face_rebar.mode is FaceRebarMode.SKIN
    assert result.face_rebar.notation == "3T10"


def test_sb_separates_asmin_credit_from_parenthesized_hangers() -> None:
    policy = ProjectDetailingPolicy(
        enabled_diameters_mm=(10, 12, 16),
        preferred_stirrup_diameters_mm=(10, 12),
        preferred_longitudinal_diameters_mm=(16,),
        preferred_torsion_face_diameters_mm=(16, 12, 10),
    )
    demands = tuple(_demand(region, 300, 300, 2.00, 0.00, 0) for region in ("left", "middle", "right"))
    section = BeamSection(
        "SB-HANGER",
        500,
        295,
        40,
        35,
        420,
        420,
        20,
        6000,
        BeamDetailingProfile.SB_ORDINARY_NO_CH18,
    )
    result = BeamDetailingEngine(project_policy=policy).design(BeamDetailingInput(section, demands))
    assert all(region.stirrup.legs == 4 for region in result.regions)
    assert result.regions[0].top.notation == "2T16+1T12+(1T12)"
    assert result.regions[0].top.provided_area_mm2 >= result.regions[0].as_min_mm2
    assert result.regions[0].top.bars[1].role == "minimum_credit_hanger"
    assert result.regions[0].top.hanger_bars is not None
    assert result.regions[0].top.hanger_bars.role == "hanger"


def test_sb_asmin_control_can_count_unparenthesized_t12_hanger_area() -> None:
    policy = ProjectDetailingPolicy(
        enabled_diameters_mm=(10, 12, 16, 20, 25),
        preferred_longitudinal_diameters_mm=(16,),
    )
    section = BeamSection(
        "SB-ASMIN-CREDIT",
        300,
        500,
        40,
        35,
        420,
        420,
        20,
        6000,
        BeamDetailingProfile.SB_ORDINARY_NO_CH18,
    )
    demands = tuple(
        BeamRegionDemand(
            region,
            {"left": 0, "middle": 3000, "right": 6000}[region],
            500,
            500,
            0.20,
            0,
            0,
            as_flex_top_override_mm2=300,
            as_flex_bottom_override_mm2=300,
        )
        for region in ("left", "middle", "right")
    )
    result = BeamDetailingEngine(project_policy=policy).design(BeamDetailingInput(section, demands))
    assert result.regions[1].top.notation == "2T16+1T12"
    assert result.regions[1].top.provided_area_mm2 == pytest.approx(515.2212)
    assert result.regions[1].top.bars[1].role == "minimum_credit_hanger"


def test_300_wide_beam_prefers_valid_two_leg_stirrup_candidate() -> None:
    section = BeamSection(
        "SB-300-TWO-LEG",
        300,
        700,
        40,
        35,
        420,
        420,
        20,
        6000,
        BeamDetailingProfile.SB_ORDINARY_NO_CH18,
    )
    demands = tuple(_demand(region, 1000, 1000, 1.0, 0, 0) for region in ("left", "middle", "right"))
    result = BeamDetailingEngine().design(BeamDetailingInput(section, demands))
    assert result.regions[0].stirrup.notation == "T12@200"
    assert result.regions[0].stirrup.legs == 2


def test_sb_uses_one_stirrup_specification_for_the_whole_span() -> None:
    section = BeamSection(
        "SB-SPAN-STIRRUP",
        300,
        700,
        40,
        35,
        420,
        420,
        20,
        6000,
        BeamDetailingProfile.SB_ORDINARY_NO_CH18,
    )
    demands = (
        _demand("left", 1000, 1000, 1.20, 0.10, 200),
        _demand("middle", 1000, 1000, 0.20, 0.00, 0),
        _demand("right", 1000, 1000, 0.80, 0.05, 100),
    )

    result = BeamDetailingEngine().design(BeamDetailingInput(section, demands))

    stirrups = tuple(region.stirrup for region in result.regions)
    assert len({stirrup.notation for stirrup in stirrups}) == 1
    assert len({stirrup.legs for stirrup in stirrups}) == 1
    for stirrup, demand in zip(stirrups, demands):
        assert stirrup.provided_at_over_s_mm2_per_mm >= demand.at_over_s_mm2_per_mm
        assert stirrup.provided_av_over_s_mm2_per_mm >= (
            demand.av_over_s_mm2_per_mm + 2.0 * demand.at_over_s_mm2_per_mm
        )


def test_sb_reselects_one_span_stirrup_for_final_main_bar_diameters() -> None:
    section = BeamSection(
        "SB-FINAL-DB",
        300,
        600,
        None,
        25,
        420,
        420,
        25,
        6900,
        BeamDetailingProfile.SB_ORDINARY_NO_CH18,
        50,
        "ETABS GetRebarBeam fixture",
        "",
        "user-confirmed aggregate",
    )
    values = (
        (388.473017261175, 533.0256214150195, 0.0, 0.41246856225950235, 595.7695913276252),
        (358.1751750787203, 758.6757682256809, 0.0, 0.42367383557419713, 611.9544881033703),
        (991.7306826276979, 310.7702828707926, 0.0, 0.42603080965283785, 615.3589014625591),
    )
    demands = tuple(
        BeamRegionDemand(name, index * 3450, *region_values)
        for index, (name, region_values) in enumerate(zip(("left", "middle", "right"), values))
    )

    result = BeamDetailingEngine().design(BeamDetailingInput(section, demands))

    assert result.iteration_status == "STABLE"
    assert result.iteration_count == 2
    assert {(region.stirrup.notation, region.stirrup.legs) for region in result.regions} == {("T12@150", 2)}
    assert all(region.stirrup.spacing_mm <= region.stirrup.maximum_spacing_mm + 1e-9 for region in result.regions)


def test_stirrup_records_minimum_quantity_and_final_db_control() -> None:
    data = _input(
        "SMF-CONTROL",
        500,
        700,
        tuple(_demand(region, 2600, 1800, 0.05, 0.0, 0.0) for region in ("left", "middle", "right")),
    )

    result = BeamDetailingEngine().design(data)
    left = result.regions[0]

    assert left.stirrup.minimum_av_over_s_mm2_per_mm > 0
    assert left.stirrup.required_combo_over_s_mm2_per_mm >= left.stirrup.minimum_av_over_s_mm2_per_mm
    assert left.stirrup.final_main_bar_diameter_mm == min(
        bar.diameter_mm for design in (left.top, left.bottom) for bar in design.bars if bar.diameter_mm >= 16
    )
    assert any(name == "SMF_6DB_FINAL" for name, _ in left.stirrup.spacing_limits)


def test_project_end_cap_prevents_stirrup_bar_preference_cycle() -> None:
    section = BeamSection(
        "PB-CYCLE",
        400,
        700,
        None,
        25,
        420,
        420,
        25,
        7800,
        BeamDetailingProfile.PB_SMF,
        50,
        "ETABS GetRebarBeam fixture",
        "",
        "user-confirmed aggregate",
    )
    demands = (
        _demand(
            "left",
            2487.1556285032907,
            1523.602230792785,
            1.08536587900272,
            0.5069755826264463,
            935.0657645962175,
        ),
        _demand(
            "middle",
            1143.8075562966337,
            1255.1958157205995,
            0.9384762627112714,
            0.4997431106945288,
            921.726193364989,
        ),
        _demand(
            "right",
            2119.9113118205337,
            1315.2999768621114,
            1.043169345666706,
            0.48970227696524976,
            903.2068796347068,
        ),
    )

    result = BeamDetailingEngine().design(BeamDetailingInput(section, demands))

    assert result.iteration_status == "STABLE"
    assert tuple(region.stirrup.notation for region in result.regions) == (
        "T10@100",
        "T10@150",
        "T10@100",
    )
    assert result.regions[0].stirrup.governing_reason == "SMF_PROJECT_ABSOLUTE"
    assert result.regions[2].stirrup.governing_reason == "SMF_PROJECT_ABSOLUTE"
    assert all(region.stirrup.spacing_mm <= region.stirrup.maximum_spacing_mm + 1e-9 for region in result.regions)


def test_high_shear_branch_uses_reduced_spacing_limit() -> None:
    data = _input(
        "HIGH-SHEAR",
        500,
        700,
        tuple(_demand(region, 1200, 1200, 2.5, 0.0, 0.0) for region in ("left", "middle", "right")),
        smf=False,
    )

    result = BeamDetailingEngine().design(data)

    assert all(region.stirrup.high_shear for region in result.regions)
    assert all(
        any(name == "SHEAR_HIGH_D_OVER_4" for name, _ in region.stirrup.spacing_limits) for region in result.regions
    )
    assert all(region.stirrup.spacing_mm <= 150 for region in result.regions)


def test_no_project_spacing_within_code_limit_fails_closed() -> None:
    policy = ProjectDetailingPolicy(allowed_spacings_mm=(200,))
    data = _input(
        "NO-SPACING",
        300,
        400,
        tuple(_demand(region, 800, 800, 0.2, 0.0, 0.0) for region in ("left", "middle", "right")),
    )

    with pytest.raises(DetailingError, match="NO_ALLOWED_SPACING"):
        BeamDetailingEngine(project_policy=policy).design(data)


@pytest.mark.parametrize(
    ("top", "expected_middle"),
    [
        (
            (
                _manual_longitudinal((20, 4, None)),
                _manual_longitudinal((16, 4, None)),
                _manual_longitudinal((25, 5, None)),
            ),
            "2T20+2T25",
        ),
        (
            (
                _manual_longitudinal((25, 6, (4, 2))),
                _manual_longitudinal((16, 6, None)),
                _manual_longitudinal((25, 6, (4, 2))),
            ),
            "4T25",
        ),
        (
            (
                _manual_longitudinal((25, 9, (5, 4))),
                _manual_longitudinal((20, 6, None)),
                _manual_longitudinal((25, 9, (5, 4))),
            ),
            "4T25",
        ),
    ],
)
def test_pb_top_allows_only_one_diameter_transition(top, expected_middle) -> None:
    bottom = tuple(_manual_longitudinal((16, 4, None)) for _ in range(3))
    result = _manual_result(BeamDetailingProfile.PB_OMF, top, bottom)
    coordinated = coordinate_intraspan_result(result)
    assert coordinated.regions[1].top.notation == expected_middle


def test_pb_bottom_middle_does_not_reduce_from_support_layout() -> None:
    top = tuple(_manual_longitudinal((20, 4, None)) for _ in range(3))
    bottom = (
        _manual_longitudinal((20, 8, (5, 3))),
        _manual_longitudinal((16, 8, (5, 3))),
        _manual_longitudinal((20, 8, (5, 3))),
    )
    result = _manual_result(BeamDetailingProfile.PB_OMF, top, bottom)
    coordinated = coordinate_intraspan_result(result)
    bottom_notations = tuple(region.bottom.notation for region in coordinated.regions)
    assert len(set(bottom_notations)) == 1
    assert coordinated.regions[0].bottom.provided_area_mm2 >= 8 * 314.1592653589793


def test_sb_bottom_uses_one_full_span_layout_from_all_three_demands() -> None:
    top = (
        _manual_longitudinal((16, 4, None)),
        _manual_longitudinal((16, 2, None), (12, 1, None)),
        _manual_longitudinal((25, 4, (2, 2))),
    )
    bottom = (
        _manual_longitudinal((20, 2, None)),
        _manual_longitudinal((25, 2, None)),
        _manual_longitudinal((16, 2, None)),
    )
    result = _manual_result(
        BeamDetailingProfile.SB_ORDINARY_NO_CH18,
        top,
        bottom,
        width=300,
        legs=2,
    )
    coordinated = coordinate_intraspan_result(result)
    assert tuple(region.bottom.notation for region in coordinated.regions) == ("2T25", "2T25", "2T25")
    assert tuple(region.top.notation for region in coordinated.regions) == (
        "4T16",
        "2T16+1T12",
        "4T25 2/2",
    )


def test_adjacent_span_option_coordinates_shared_support_only() -> None:
    first = BeamDetailingEngine().design(
        _input(
            "SPAN-1",
            500,
            700,
            (
                _demand("left", 1000, 1000, 0.2, 0, 0),
                _demand("middle", 1000, 1000, 0.2, 0, 0),
                _demand("right", 2800, 1000, 0.2, 0, 0),
            ),
        )
    )
    second = BeamDetailingEngine().design(
        _input(
            "SPAN-2",
            500,
            700,
            (
                _demand("left", 1400, 1000, 0.2, 0, 0),
                _demand("middle", 1000, 1000, 0.2, 0, 0),
                _demand("right", 1000, 1000, 0.2, 0, 0),
            ),
        )
    )
    first_out, second_out = coordinate_adjacent_spans(first, second, ContinuityOption.OPTION_1_STRICT_EQUAL)
    assert first_out.regions[2].top.bars[0].count == second_out.regions[0].top.bars[0].count
    assert first_out.regions[1].top.bars[0].count == first.regions[1].top.bars[0].count


def test_pb_option_2_keeps_common_diameter_but_allows_count_adjustment() -> None:
    bottom = tuple(_manual_longitudinal((20, 4, None)) for _ in range(3))
    first = _manual_result(
        BeamDetailingProfile.PB_OMF,
        (
            _manual_longitudinal((20, 4, None)),
            _manual_longitudinal((20, 4, None)),
            _manual_longitudinal((20, 4, None)),
        ),
        bottom,
    )
    second = _manual_result(
        BeamDetailingProfile.PB_OMF,
        (
            _manual_longitudinal((20, 8, (5, 3))),
            _manual_longitudinal((20, 4, None)),
            _manual_longitudinal((20, 4, None)),
        ),
        bottom,
    )

    first_out, second_out = coordinate_adjacent_spans(
        first,
        second,
        ContinuityOption.OPTION_2_COUNT_ADJUSTMENT,
    )
    first_support = first_out.regions[2].top
    second_support = second_out.regions[0].top

    assert first_support.bars[0].diameter_mm == second_support.bars[0].diameter_mm
    assert first_support.bars[0].count < second_support.bars[0].count
    assert "OPTION_2_COUNT_ADJUSTMENT_APPLIED" in first_out.warnings


def test_pb_adjacent_spans_keep_independent_uniform_bottom_layouts() -> None:
    top = tuple(_manual_longitudinal((20, 4, None)) for _ in range(3))
    first = _manual_result(
        BeamDetailingProfile.PB_OMF,
        top,
        tuple(_manual_longitudinal((20, 4, None)) for _ in range(3)),
        width=300,
        legs=4,
    )
    second = _manual_result(
        BeamDetailingProfile.PB_OMF,
        top,
        (
            _manual_longitudinal((16, 8, (5, 3))),
            _manual_longitudinal((16, 8, (5, 3))),
            _manual_longitudinal((16, 4, None)),
        ),
        width=300,
        legs=4,
    )

    first_out, second_out = coordinate_adjacent_spans(
        first,
        second,
        ContinuityOption.OPTION_1_STRICT_EQUAL,
    )

    assert len({region.bottom.notation for region in first_out.regions}) == 1
    assert len({region.bottom.notation for region in second_out.regions}) == 1
    assert first_out.regions[2].bottom.notation == first_out.regions[0].bottom.notation
    assert second_out.regions[0].bottom.notation == second_out.regions[2].bottom.notation


def test_sb_adjacent_spans_equalize_top_but_keep_bottom_independent() -> None:
    engine = BeamDetailingEngine()

    def sb_result(name: str, right_top: float, right_bottom: float, left_top: float, left_bottom: float):
        section = BeamSection(
            name,
            500,
            700,
            40,
            35,
            420,
            420,
            20,
            6000,
            BeamDetailingProfile.SB_ORDINARY_NO_CH18,
        )
        return engine.design(
            BeamDetailingInput(
                section,
                (
                    _demand("left", left_top, left_bottom, 1.20, 0, 0),
                    _demand("middle", 1000, 1000, 1.20, 0, 0),
                    _demand("right", right_top, right_bottom, 1.20, 0, 0),
                ),
            )
        )

    first = sb_result("SB-1", 1800, 1000, 1000, 1000)
    second = sb_result("SB-2", 1000, 1000, 1000, 1800)
    first_out, second_out = coordinate_adjacent_spans(first, second, ContinuityOption.OPTION_1_STRICT_EQUAL)
    assert first_out.regions[2].top.notation == second_out.regions[0].top.notation
    assert len({region.bottom.notation for region in first_out.regions}) == 1
    assert len({region.bottom.notation for region in second_out.regions}) == 1
    assert first_out.regions[2].bottom.bars[0].diameter_mm != second_out.regions[0].bottom.bars[0].diameter_mm
    assert first_out.regions[2].bottom.provided_area_mm2 >= first_out.regions[2].bottom.required_area_mm2
    assert second_out.regions[0].bottom.provided_area_mm2 >= second_out.regions[0].bottom.required_area_mm2


def test_etabs23_total_result_adapter_preserves_total_min_and_provided(monkeypatch) -> None:
    fake_module = SimpleNamespace(eItemType=SimpleNamespace(Objects="Objects"))
    monkeypatch.setitem(sys.modules, "ETABSv1", fake_module)

    arrays = (
        0,
        1,
        ["B1"],
        [100.0],
        ["TOP"],
        [1200.0],
        [1100.0],
        [900.0],
        [-1.0],
        ["BOT"],
        [800.0],
        [700.0],
        [600.0],
        [-1.0],
        ["V"],
        [1.2],
        [1.0],
        [0.8],
        [-1.0],
        ["TL"],
        [200.0],
        ["TT"],
        [0.4],
        [""],
        [""],
    )

    class FakeDesign:
        def GetSummaryResultsBeam_2(self, *args):
            return arrays

    result = EtabsResultProvider(SimpleNamespace(DesignConcrete=FakeDesign())).get_beam_design_total("B1")
    station = result.stations[0]
    assert station.as_total_top_mm2 == 1200
    assert station.as_total_bottom_mm2 == 800
    assert station.as_flex_top_mm2 == 1100
    assert station.top_min_area == 900
    assert station.top_provided_area is None
    assert station.raw_top_provided_area == -1
    assert station.source_object == "B1"
    assert station.units == "N_mm_C"


def test_etabs23_total_result_adapter_preserves_valid_zero_provided(
    monkeypatch,
) -> None:
    fake_module = SimpleNamespace(eItemType=SimpleNamespace(Objects="Objects"))
    monkeypatch.setitem(sys.modules, "ETABSv1", fake_module)
    arrays = (
        0,
        1,
        ["B1"],
        [0.0],
        ["TOP"],
        [1200.0],
        [1100.0],
        [900.0],
        [0.0],
        ["BOT"],
        [800.0],
        [700.0],
        [600.0],
        [0.0],
        ["V"],
        [1.2],
        [1.0],
        [0.8],
        [-1.0],
        ["TL"],
        [200.0],
        ["TT"],
        [0.4],
        [""],
        [""],
    )

    class FakeDesign:
        def GetSummaryResultsBeam_2(self, *args):
            return arrays

    result = EtabsResultProvider(SimpleNamespace(DesignConcrete=FakeDesign())).get_beam_design_total("B1")

    assert result.stations[0].top_provided_area == 0.0
    assert result.stations[0].bottom_provided_area == 0.0


def test_etabs23_design_error_marks_result_partial_without_losing_station(
    monkeypatch,
) -> None:
    fake_module = SimpleNamespace(eItemType=SimpleNamespace(Objects="Objects"))
    monkeypatch.setitem(sys.modules, "ETABSv1", fake_module)
    arrays = (
        0,
        1,
        ["B1"],
        [0.0],
        ["TOP"],
        [1200.0],
        [1100.0],
        [900.0],
        [-1.0],
        ["BOT"],
        [800.0],
        [700.0],
        [600.0],
        [-1.0],
        ["V"],
        [1.2],
        [1.0],
        [0.8],
        [-1.0],
        ["TL"],
        [200.0],
        ["TT"],
        [0.4],
        ["reinforcing design incomplete"],
        ["review warning"],
    )

    class FakeDesign:
        def GetSummaryResultsBeam_2(self, *args):
            return arrays

    result = EtabsResultProvider(SimpleNamespace(DesignConcrete=FakeDesign())).get_beam_design_total("B1")

    assert result.status is DesignResultStatus.PARTIAL
    assert len(result.stations) == 1
    assert "reinforcing design incomplete" in result.status_message
    assert "review warning" in result.status_message
