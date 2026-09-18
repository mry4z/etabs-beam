"""PB four-leg preference is an optimization, not a relaxed design check."""

from dataclasses import replace

import pytest

from src.design.constructability import SeismicDetailingContext
from src.design.detailing import (
    BeamDetailingEngine,
    BeamDetailingInput,
    BeamDetailingProfile,
    BeamRegionDemand,
    BeamSection,
    DetailingError,
    ProjectDetailingPolicy,
)


def _section(*, width=400, role="PB", profile=BeamDetailingProfile.SMF):
    return BeamSection("PB-FOUR-LEG", width, 800, 40, 25, 420, 420, 25, 7700, profile, beam_role=role)


def _demand(name="middle", av=1.5, at=0.3):
    return BeamRegionDemand(name, 3850 if name == "middle" else 0, 1200, 1700, av, at, 0)


@pytest.mark.parametrize("invalid", [0, 1, 3, 5, -2, True, 4.0])
def test_pb_preferred_leg_limit_is_a_valid_even_integer(invalid):
    with pytest.raises(DetailingError, match="主梁优选箍筋肢数上限"):
        ProjectDetailingPolicy(pb_preferred_max_stirrup_legs=invalid)


@pytest.mark.parametrize(
    ("name", "av", "at"),
    [
        ("middle", 1.5, 0.3),
        ("middle", 1.5, 0.45),
        ("left", 4.0, 0),
        ("right", 6.0, 0),
        ("left", 10.0, 0),
        ("right", 15.0, 0),
    ],
)
def test_pb_uses_spacing_before_diameter_or_extra_legs(name, av, at):
    engine = BeamDetailingEngine()
    section, demand = _section(), _demand(name, av, at)
    selected = engine._select_stirrup(section, demand, 25)

    assert selected.legs == 4
    assert selected.spacing_mm in (100, 110, 130, 150, 170, 200)
    assert selected.spacing_mm <= selected.maximum_spacing_mm
    assert selected.provided_at_over_s_mm2_per_mm >= at
    assert selected.provided_av_over_s_mm2_per_mm >= selected.required_combo_over_s_mm2_per_mm


def test_pb_prefers_valid_four_leg_spacing_over_six_legs_at_larger_spacing():
    # High shear prevents 200 mm. Four legs at 150 mm must remain preferable
    # to a small-diameter six-leg choice at the same permitted spacing.
    selected = BeamDetailingEngine()._select_stirrup(_section(), _demand(av=2.5, at=0), 25)
    assert selected.diameter_mm == 10
    assert selected.legs == 4
    assert selected.spacing_mm in (100, 110, 130, 150, 170, 200)
    assert selected.high_shear
    assert selected.spacing_mm <= selected.maximum_spacing_mm


def test_pb_four_legs_is_not_a_hard_cap_when_the_enabled_diameters_are_insufficient():
    engine = BeamDetailingEngine()
    selected = engine._select_stirrup(_section(), _demand("left", av=50, at=0), 25)
    assert selected.diameter_mm == 25
    assert selected.legs > 4
    assert selected.spacing_mm in (100, 110, 130, 150, 170, 200)
    assert selected.provided_av_over_s_mm2_per_mm >= selected.required_combo_over_s_mm2_per_mm


def test_shallow_smf_end_zone_fails_closed_when_catalogue_has_no_legal_spacing():
    section = BeamSection(
        "PB-SHALLOW-SMF",
        500,
        300,
        40,
        25,
        420,
        420,
        25,
        2965,
        BeamDetailingProfile.SMF,
        beam_role="PB",
    )
    with pytest.raises(DetailingError, match="NO_ALLOWED_SPACING"):
        BeamDetailingEngine()._select_stirrup(
            section,
            _demand("left", av=1.23, at=0.42),
            25,
        )


def test_pb_diameter_escalation_cannot_use_disabled_bars():
    project = ProjectDetailingPolicy(
        enabled_diameters_mm=(10, 12, 16),
        preferred_longitudinal_diameters_mm=(16,),
        preferred_torsion_face_diameters_mm=(16, 12, 10),
    )
    engine = BeamDetailingEngine(project_policy=project)
    selected = engine._select_stirrup(_section(), _demand("left", av=20, at=0), 25)
    assert selected.diameter_mm == 16
    assert selected.legs > 4
    assert selected.spacing_mm in (100, 110, 130, 150, 170, 200)
    assert selected.diameter_mm in project.enabled_diameters_mm
    assert selected.provided_av_over_s_mm2_per_mm >= 10


def test_torsion_requirement_fails_closed_when_no_catalogue_spacing_meets_at_over_s():
    with pytest.raises(DetailingError, match="NO_ALLOWED_SPACING"):
        BeamDetailingEngine()._select_stirrup(_section(), _demand("left", av=0.1, at=6), 25)


def test_pb_four_leg_preference_is_applied_even_when_two_legs_would_pass():
    selected = BeamDetailingEngine()._select_stirrup(_section(), _demand(av=0.1, at=0), 25)
    assert (selected.notation, selected.legs) == ("T10@200", 4)


@pytest.mark.parametrize("role", ["PB", "SB"])
def test_narrow_beams_keep_the_existing_two_leg_preference(role):
    section = _section(width=300, role=role)
    demand = _demand(av=1, at=0)
    selected = BeamDetailingEngine()._select_stirrup(section, demand, 25)
    assert (selected.notation, selected.legs) == ("T10@150", 2)


@pytest.mark.parametrize("width", [300, 400, 500])
@pytest.mark.parametrize("profile", [BeamDetailingProfile.NON_SWAY, BeamDetailingProfile.SMF])
def test_sb_full_span_design_is_unchanged_by_pb_hoop_preference(width, profile):
    section = _section(width=width, role="SB", profile=profile)
    data = BeamDetailingInput(section, tuple(_demand(name) for name in ("left", "middle", "right")))
    result = BeamDetailingEngine().design(data)
    assert len({region.stirrup.notation for region in result.regions}) == 1
    expected_legs = 2 if width < 400 else 4 if width < 550 else 6
    assert result.regions[0].stirrup.legs == expected_legs


def test_final_layout_drives_capacity_shear_and_hoop_checks_after_leg_reduction():
    section = _section()
    data = BeamDetailingInput(
        section,
        (
            replace(_demand("left"), as_top_total_mm2=2885),
            _demand(),
            replace(_demand("right"), as_top_total_mm2=2616),
        ),
        seismic_context=SeismicDetailingContext(
            is_smf=True,
            frame_type="Sway Special",
            mpr_strength_factor=1.25,
            mpr_strength_source="offline test fixture, not live model verification",
            gravity_shear_kN=40,
        ),
    )
    engine = BeamDetailingEngine()
    result = engine.design(data)
    review = result.capacity_shear_review

    assert review is not None
    assert review == engine._calculate_capacity_review(section, result.regions, data.seismic_context)
    assert len({region.bottom.notation for region in result.regions}) == 1
    assert result.regions[1].top.provided_area_mm2 >= result.regions[1].top.required_area_mm2
    assert all(region.top.rows[0].count >= region.stirrup.legs for region in result.regions)
    assert len({region.top.rows[0].diameter_mm for region in result.regions}) == 1
    # The new solver is judged by the final physical rows and feedback checks,
    # not by comparison with the retired local-selector output.
    assert all(face.provided_area_mm2 >= face.required_area_mm2
               for region in result.regions for face in (region.top, region.bottom))
    assert result.smf_checks
    for region in result.regions:
        hoop = region.stirrup
        assert hoop.legs == 4
        assert hoop.spacing_mm <= hoop.maximum_spacing_mm
        assert hoop.provided_av_over_s_mm2_per_mm >= hoop.required_combo_over_s_mm2_per_mm
        assert hoop.provided_at_over_s_mm2_per_mm >= region.demand.at_over_s_mm2_per_mm
        if region.name != "middle":
            assert region.demand.capacity_shear_av_over_s_mm2_per_mm == pytest.approx(
                review.required_av_over_s_mm2_per_mm
            )
        for face in (region.top, region.bottom):
            assert face.provided_area_mm2 >= face.required_area_mm2
            assert all(
                max(bar.layers) <= engine._capacity_per_layer(section, hoop.diameter_mm, bar.diameter_mm)
                for bar in face.bars
            )
