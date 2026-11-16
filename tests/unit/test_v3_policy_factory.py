import pytest

from src.design.detailing import (
    BeamRegionDemand,
    BeamDetailingProfile,
    BeamSection,
    DRAWING_POLICY_ID,
    DRAWING_V3_POLICY_ID,
    DRAWING_V3_REVISION_ID,
    DRAWING_V3_SOLVER_REVISION_ID,
    ACI318_14_DetailingPolicy,
    DetailingError,
    LongitudinalDesign,
    PhysicalRow,
    StirrupDesign,
    make_detailing_engine,
    make_fresh_detailing_engine,
)


def _section(width, depth, role, profile):
    return BeamSection(
        f"v3-{role}-{width}x{depth}",
        width,
        depth,
        40.0,
        25.0,
        420.0,
        420.0,
        25.0,
        6000.0,
        profile,
        beam_role=role,
    )


def test_fresh_v3_factory_uses_v3_revision_and_role_specific_diameter_order():
    engine = make_fresh_detailing_engine(DRAWING_V3_POLICY_ID, candidate_lookup="off")
    pb = _section(300.0, 700.0, "PB", BeamDetailingProfile.OMF)
    sb = _section(300.0, 700.0, "SB", BeamDetailingProfile.SB_ORDINARY_NO_CH18)

    assert engine.detailing_policy_id == DRAWING_V3_POLICY_ID
    assert engine.detailing_policy_revision_id == DRAWING_V3_REVISION_ID
    assert engine.coordination_solver_revision_id == DRAWING_V3_SOLVER_REVISION_ID
    assert engine.strict_symmetric_rows is True
    assert engine.project.ordered_longitudinal_diameters_for(pb) == (20, 25)
    assert engine.project.ordered_longitudinal_diameters_for(sb) == (16, 12, 20, 25)
    assert engine.project.ordered_stirrup_diameters() == (10, 12)
    assert engine.project.max_main_bar_layers == 3
    assert engine.detailing_policy_parameters["high_shear_spacing_limit"] == "EFFECTIVE_DEPTH_OVER_2"
    assert "V3_HIGH_SHEAR_D_OVER_4_NOT_PROJECT_CONSTRAINT" in engine.detailing_policy_exceptions
    with pytest.raises(DetailingError, match="DETAILING_POLICY_REVISION_UNKNOWN"):
        make_detailing_engine(
            DRAWING_V3_POLICY_ID,
            detailing_policy_revision_id="drawing_v3_20261104_top10_v1",
        )


def test_v3_width_rules_are_hard_minimums_and_leave_legacy_drawing_unchanged():
    v3 = make_fresh_detailing_engine(DRAWING_V3_POLICY_ID, candidate_lookup="off")
    legacy_drawing = make_detailing_engine(DRAWING_POLICY_ID, ranking_mode="legacy")
    widths = (399.0, 400.0, 500.0, 549.0, 550.0)

    assert tuple(v3.code.minimum_legs(_section(width, 700, "PB", BeamDetailingProfile.OMF), "middle")
                 for width in widths) == (2, 4, 4, 4, 6)
    assert tuple(legacy_drawing.code.minimum_legs(
        _section(width, 700, "PB", BeamDetailingProfile.OMF), "middle"
    ) for width in widths) == (2, 2, 2, 2, 2)


def test_high_shear_d_over_2_exception_is_limited_to_fresh_v3_policy():
    ordinary = _section(500.0, 300.0, "PB", BeamDetailingProfile.OMF)
    smf = _section(500.0, 300.0, "PB", BeamDetailingProfile.SMF)
    high_middle = BeamRegionDemand("middle", 1500.0, 0.0, 0.0, 2.4, 0.0, 0.0)
    high_right = BeamRegionDemand("right", 2600.0, 0.0, 0.0, 2.4, 0.0, 0.0)
    low_middle = BeamRegionDemand("middle", 1500.0, 0.0, 0.0, 0.8, 0.0, 0.0)
    depth = 238.0

    v3 = make_fresh_detailing_engine(DRAWING_V3_POLICY_ID, candidate_lookup="off")
    maximum, reason, limits, high = v3.code.maximum_stirrup_spacing(
        ordinary, high_middle, 12.0, 20.0, effective_depth_mm=depth
    )
    assert high is True
    assert maximum == pytest.approx(119.0)
    assert reason == "SHEAR_HIGH_D_OVER_2"
    assert dict(limits)["SHEAR_HIGH_ABSOLUTE"] == pytest.approx(300.0)

    low_maximum, low_reason, _low_limits, low_high = v3.code.maximum_stirrup_spacing(
        ordinary, low_middle, 12.0, 20.0, effective_depth_mm=depth
    )
    assert low_high is False
    assert low_maximum == pytest.approx(119.0)
    assert low_reason == "SHEAR_LOW_D_OVER_2"

    smf_maximum, smf_reason, smf_limits, smf_high = v3.code.maximum_stirrup_spacing(
        smf, high_right, 12.0, 20.0, effective_depth_mm=depth
    )
    assert smf_high is True
    assert smf_maximum == pytest.approx(59.5)
    assert smf_reason == "SMF_D_OVER_4"
    assert dict(smf_limits)["SHEAR_HIGH_D_OVER_2"] == pytest.approx(119.0)

    legacy = ACI318_14_DetailingPolicy()
    old_drawing = make_detailing_engine(DRAWING_POLICY_ID)
    for code in (legacy, old_drawing.code):
        old_maximum, old_reason, _old_limits, old_high = code.maximum_stirrup_spacing(
            ordinary, high_middle, 12.0, 20.0, effective_depth_mm=depth
        )
        assert old_high is True
        assert old_maximum == pytest.approx(59.5)
        assert old_reason == "SHEAR_HIGH_D_OVER_4"


def test_v3_symmetric_geometry_uses_twelve_mm_main_bars_when_wider_bars_cannot_fit():
    engine = make_fresh_detailing_engine(DRAWING_V3_POLICY_ID, candidate_lookup="off")
    section = _section(
        150.0,
        300.0,
        "SB",
        BeamDetailingProfile.SB_ORDINARY_NO_CH18,
    )
    hoop = StirrupDesign(10, 100, 2, 0.0, 0.0, 300.0, "unit-test")

    resolved = engine._resolve_physical_layout_uncached(
        section,
        hoop,
        LongitudinalDesign(rows=(PhysicalRow(2, 12),), required_area_mm2=0.0),
        face="top",
    )

    x = resolved.row_x_mm[0]
    assert len(x) == 2
    assert x[0] + x[1] == pytest.approx(section.width_mm, abs=1e-9)
    minimum = engine.code.minimum_longitudinal_area(
        section.width_mm,
        resolved.effective_depth_mm,
        section.fc_mpa,
        section.fy_mpa,
    )
    assert resolved.provided_area_mm2 >= minimum

    for diameter in (16, 20, 25):
        with pytest.raises(DetailingError, match="ROW_HORIZONTAL_CLEARANCE_FAILED"):
            engine._resolve_physical_layout_uncached(
                section,
                hoop,
                LongitudinalDesign(
                    rows=(PhysicalRow(2, diameter),),
                    required_area_mm2=0.0,
                ),
                face="top",
            )


def test_v3_multilayer_geometry_keeps_three_nested_rows_symmetric():
    engine = make_fresh_detailing_engine(DRAWING_V3_POLICY_ID, candidate_lookup="off")
    section = _section(400.0, 800.0, "PB", BeamDetailingProfile.OMF)
    hoop = StirrupDesign(10, 100, 2, 0.0, 0.0, 300.0, "unit-test")
    design = LongitudinalDesign(
        rows=(PhysicalRow(5, 20), PhysicalRow(3, 16), PhysicalRow(2, 12)),
        required_area_mm2=0.0,
    )

    resolved = engine._resolve_physical_layout_uncached(section, hoop, design, face="top")

    assert tuple(len(row) for row in resolved.row_x_mm) == (5, 3, 2)
    for row in resolved.row_x_mm:
        assert all(left + right == pytest.approx(section.width_mm, abs=1e-9)
                   for left, right in zip(row, reversed(row)))
    for outer, inner in zip(resolved.row_x_mm, resolved.row_x_mm[1:]):
        assert set(inner) <= set(outer)
