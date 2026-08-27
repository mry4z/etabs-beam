from types import SimpleNamespace

import pytest

from src.design.constructability import (
    calculate_smf_capacity_shear,
    DesignResultProvenance,
    SeismicDetailingContext,
    SupportFaceContext,
    build_constructability,
    evaluate_smf_longitudinal,
)


def _support(name: str, face: float) -> SupportFaceContext:
    return SupportFaceContext(name, face, face + 150.0, 300.0, "fixture-column-section")


def test_smf_zones_are_measured_from_support_faces_and_place_first_hoops() -> None:
    result = build_constructability(
        is_smf=True,
        span_length_mm=10000,
        beam_depth_mm=600,
        effective_depth_mm=520,
        smallest_primary_bar_diameter_mm=20,
        left_support=_support("L", 300),
        right_support=_support("R", 9700),
        left_spacing_mm=100,
        middle_spacing_mm=200,
        right_spacing_mm=100,
    )

    assert result.status == "OK"
    assert [(z.name, z.start_mm, z.end_mm) for z in result.zones] == [
        ("smf_left_2h", 300, 1500),
        ("smf_middle", 1500, 8500),
        ("smf_right_2h", 8500, 9700),
    ]
    first = [h.location_mm for h in result.hoop_positions if h.is_first_from_support]
    assert first == pytest.approx([350, 9650])
    assert all(100 <= h.location_mm <= 9700 for h in result.hoop_positions)
    assert result.zones[0].maximum_spacing_mm == pytest.approx(100.0)
    assert result.zones[2].maximum_spacing_mm == pytest.approx(100.0)
    assert all(check.status in {"PASS", "INFO"} for check in result.checks)


def test_smf_overlapping_two_h_zones_use_one_controlling_zone() -> None:
    result = build_constructability(
        is_smf=True,
        span_length_mm=3000,
        beam_depth_mm=1000,
        effective_depth_mm=900,
        smallest_primary_bar_diameter_mm=20,
        left_support=_support("L", 250),
        right_support=_support("R", 2750),
        left_spacing_mm=100,
        middle_spacing_mm=200,
        right_spacing_mm=150,
    )

    assert len(result.zones) == 1
    assert result.zones[0].name == "smf_end_hoop_overlap"
    assert result.zones[0].selected_spacing_mm == 100
    assert result.zones[0].first_hoop_location_mm == 300


def test_smf_without_support_face_is_review_blocked() -> None:
    result = build_constructability(
        is_smf=True,
        span_length_mm=6000,
        beam_depth_mm=600,
        effective_depth_mm=520,
        smallest_primary_bar_diameter_mm=20,
        left_support=None,
        right_support=None,
        left_spacing_mm=100,
        middle_spacing_mm=200,
        right_spacing_mm=100,
    )

    assert result.status == "SMF_SUPPORT_FACE_UNRESOLVED"
    assert "SMF_SUPPORT_FACE_UNRESOLVED" in result.blocking_statuses


def test_smf_strength_ratio_screening_returns_auditable_checks() -> None:
    def design(area: float, count: int):
        bars = tuple(SimpleNamespace(count= count, role="longitudinal") for _ in range(1))
        return SimpleNamespace(provided_area_mm2=area, bars=bars)

    checks = evaluate_smf_longitudinal(
        width_mm=400,
        fc_mpa=30,
        fy_mpa=420,
        effective_depth_mm=600,
        left_top=design(1800, 4),
        left_bottom=design(1000, 2),
        middle_top=design(1200, 3),
        middle_bottom=design(1200, 3),
        right_top=design(1800, 4),
        right_bottom=design(1000, 2),
    )

    ids = {check.rule_id for check in checks}
    assert "SMF-RHO-MAX-LEFT" in ids
    assert "SMF-POSITIVE-NEGATIVE-LEFT" in ids
    assert all(check.source or check.clause for check in checks)


def test_smf_project_ratio_rejects_layout_above_1_613_percent_even_below_aci_2_5_percent() -> None:
    def design(area: float):
        return SimpleNamespace(
            provided_area_mm2=area,
            bars=(SimpleNamespace(count=4, role="longitudinal"),),
        )

    checks = evaluate_smf_longitudinal(
        width_mm=400,
        fc_mpa=25,
        fy_mpa=420,
        effective_depth_mm=600,
        left_top=design(4000),
        left_bottom=design(2000),
        middle_top=design(2000),
        middle_bottom=design(2000),
        right_top=design(4000),
        right_bottom=design(2000),
    )

    ratio_checks = [check for check in checks if check.rule_id.startswith("SMF-RHO-MAX")]
    assert any(check.status == "SMF_LONGITUDINAL_RATIO_FAILED" for check in ratio_checks)
    assert all(check.limit == pytest.approx(0.01613) for check in ratio_checks)


def test_smf_capacity_shear_uses_verified_source_or_mpr_envelope() -> None:
    context = SeismicDetailingContext(
        is_smf=True,
        frame_type="Sway Special",
        capacity_shear_source="engine Mpr",
        capacity_shear_verified=False,
        left_mpr_kNm=420.0,
        right_mpr_kNm=380.0,
        gravity_shear_kN=120.0,
        capacity_shear_demand_kN=180.0,
        provenance=DesignResultProvenance(source="fixture", capacity_shear_included=False),
    )
    result = build_constructability(
        is_smf=True,
        span_length_mm=10000,
        beam_depth_mm=600,
        effective_depth_mm=520,
        smallest_primary_bar_diameter_mm=20,
        left_support=_support("L", 300),
        right_support=_support("R", 9700),
        left_spacing_mm=100,
        middle_spacing_mm=200,
        right_spacing_mm=100,
        beam_width_mm=400,
        concrete_strength_mpa=30,
        seismic_context=context,
    )
    capacity = [check for check in result.checks if check.rule_id == "SMF-CAPACITY-SHEAR"]
    assert capacity
    assert any(check.status == "PASS" and "Vc=0" in check.message for check in capacity)


def test_capacity_shear_calculation_returns_vcap_and_required_av_over_s() -> None:
    context = SeismicDetailingContext(
        is_smf=True,
        left_mpr_kNm=450.0,
        right_mpr_kNm=500.0,
        gravity_shear_kN=60.0,
        capacity_shear_demand_kN=180.0,
        capacity_shear_source="fixture ETABS capacity Vu",
        capacity_shear_verified=True,
    )
    region = SimpleNamespace()
    review = calculate_smf_capacity_shear(
        span_length_mm=6000.0,
        beam_width_mm=400.0,
        fc_mpa=30.0,
        fy_mpa=420.0,
        fyt_mpa=420.0,
        effective_depth_mm=550.0,
        left_region=region,
        right_region=region,
        context=context,
    )

    expected_vcap = (450.0 + 500.0) * 1000.0 / 6000.0 + 60.0
    expected_av = expected_vcap * 1000.0 / (0.75 * 420.0 * 550.0)
    assert review.status == "PASS"
    assert review.actual_capacity_shear_kN == pytest.approx(expected_vcap)
    assert review.final_capacity_shear_kN == pytest.approx(expected_vcap)
    assert review.required_av_over_s_mm2_per_mm == pytest.approx(expected_av)
    assert review.control_source == "Vcap_actual"


def test_capacity_shear_keeps_higher_verified_etabs_vu_as_governing_demand() -> None:
    context = SeismicDetailingContext(
        is_smf=True,
        left_mpr_kNm=450.0,
        right_mpr_kNm=500.0,
        gravity_shear_kN=60.0,
        capacity_shear_demand_kN=260.0,
        capacity_shear_verified=True,
        capacity_shear_source="fixture ETABS capacity Vu",
    )
    review = calculate_smf_capacity_shear(
        span_length_mm=6000.0,
        beam_width_mm=400.0,
        fc_mpa=30.0,
        fy_mpa=420.0,
        fyt_mpa=420.0,
        effective_depth_mm=550.0,
        left_region=SimpleNamespace(),
        right_region=SimpleNamespace(),
        context=context,
    )

    assert review.actual_capacity_shear_kN == pytest.approx(218.3333333333)
    assert review.final_capacity_shear_kN == pytest.approx(260.0)
    assert review.control_source == "ETABS_CAPACITY_INCLUDED"


def test_capacity_shear_does_not_replace_explicit_etabs_vu_with_table_inference() -> None:
    def region():
        demand = SimpleNamespace(
            av_over_s_mm2_per_mm=0.80,
            as_top_etabs_mm2=1800.0,
            as_bottom_etabs_mm2=1500.0,
        )
        return SimpleNamespace(
            demand=demand,
            top=SimpleNamespace(provided_area_mm2=2400.0),
            bottom=SimpleNamespace(provided_area_mm2=1800.0),
        )

    context = SeismicDetailingContext(
        is_smf=True,
        capacity_shear_demand_kN=210.0,
        capacity_shear_verified=True,
        capacity_shear_source="verified ETABS capacity Vu",
        mpr_strength_factor=1.25,
        capacity_shear_vc_zero=True,
    )
    review = calculate_smf_capacity_shear(
        span_length_mm=6000.0,
        beam_width_mm=400.0,
        fc_mpa=30.0,
        fy_mpa=420.0,
        fyt_mpa=420.0,
        effective_depth_mm=550.0,
        left_region=region(),
        right_region=region(),
        context=context,
    )

    assert review.status == "PASS"
    assert review.etabs_capacity_shear_kN == pytest.approx(210.0)
    assert review.actual_capacity_shear_kN is None
    assert review.final_capacity_shear_kN == pytest.approx(210.0)


def test_capacity_shear_uses_processed_span_and_table_provided_areas_without_extra_vu_input() -> None:
    def region(selected_top: float, selected_bottom: float, etabs_top: float, etabs_bottom: float):
        demand = SimpleNamespace(
            av_over_s_mm2_per_mm=0.80,
            as_top_etabs_mm2=etabs_top,
            as_bottom_etabs_mm2=etabs_bottom,
        )
        return SimpleNamespace(
            demand=demand,
            top=SimpleNamespace(provided_area_mm2=selected_top),
            bottom=SimpleNamespace(provided_area_mm2=selected_bottom),
        )

    context = SeismicDetailingContext(
        is_smf=True,
        mpr_strength_factor=1.25,
        mpr_strength_source="fixture selected automatic bars",
        capacity_shear_verified=False,
        capacity_shear_vc_zero=True,
    )
    review = calculate_smf_capacity_shear(
        span_length_mm=6000.0,
        beam_width_mm=400.0,
        fc_mpa=30.0,
        fy_mpa=420.0,
        fyt_mpa=420.0,
        effective_depth_mm=550.0,
        left_region=region(2400, 1800, 1800, 1500),
        right_region=region(2400, 1800, 1800, 1500),
        context=context,
    )

    assert review.status == "ENGINE_MPR_FALLBACK"
    assert review.source.startswith("fixture selected automatic bars; processed span")
    assert review.etabs_capacity_shear_kN == pytest.approx(0.80 * 0.75 * 420.0 * 550.0 / 1000.0)
    assert review.gravity_shear_kN == pytest.approx(0.0)

    def nominal_moment(tension_area: float, compression_area: float) -> float:
        a = tension_area * 420.0 / (0.85 * 30.0 * 400.0)
        return (
            tension_area * 420.0 * (550.0 - a / 2.0)
            + compression_area * 420.0 * (550.0 - 40.0)
        ) / 1_000_000.0

    expected_actual_vu = (
        1.25
        * (nominal_moment(1800.0, 2400.0) + nominal_moment(2400.0, 1800.0))
        * 1000.0
        / 6000.0
    )
    assert review.vu_from_actual_rebar_kN == pytest.approx(expected_actual_vu)
    assert review.final_capacity_shear_kN == pytest.approx(expected_actual_vu)
    assert review.required_av_over_s_mm2_per_mm == pytest.approx(
        expected_actual_vu * 1000.0 / (0.75 * 420.0 * 550.0)
    )
