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


class DiameterFirstEngine(BeamDetailingEngine):
    """Freeze the preceding ranking solely as a regression comparator."""

    def _preferred_stirrup(self, section, candidates):
        if section.width_mm <= self.project.prefer_two_leg_max_width_mm + 1e-9:
            return min(candidates, key=lambda item: (item[2].legs, -item[1], item[0]))[2]
        return min(candidates, key=lambda item: (item[0], -item[1], item[2].legs))[2]


def _section(*, width=400, role="PB", profile=BeamDetailingProfile.SMF):
    return BeamSection("PB-FOUR-LEG", width, 800, 40, 25, 420, 420, 25, 7700, profile, beam_role=role)


def _demand(name="middle", av=1.5, at=0.3):
    return BeamRegionDemand(name, 3850 if name == "middle" else 0, 1200, 1700, av, at, 0)


@pytest.mark.parametrize("invalid", [0, 1, 3, 5, -2, True, 4.0])
def test_pb_preferred_leg_limit_is_a_valid_even_integer(invalid):
    with pytest.raises(DetailingError, match="主梁优选箍筋肢数上限"):
        ProjectDetailingPolicy(pb_preferred_max_stirrup_legs=invalid)


@pytest.mark.parametrize(
    ("name", "av", "at", "notation"),
    [
        ("middle", 1.5, 0.3, "T12@200"),
        ("middle", 1.5, 0.45, "T16@200"),
        ("left", 4.0, 0, "T12@100"),
        ("right", 6.0, 0, "T16@100"),
        ("left", 10.0, 0, "T20@100"),
        ("right", 15.0, 0, "T25@100"),
    ],
)
def test_pb_increases_hoop_diameter_before_adding_longitudinal_support_legs(name, av, at, notation):
    engine = BeamDetailingEngine()
    section, demand = _section(), _demand(name, av, at)
    previous = DiameterFirstEngine()._select_stirrup(section, demand, 25)
    selected = engine._select_stirrup(section, demand, 25)

    assert previous.legs > 4
    assert selected.legs == 4
    assert selected.notation == notation
    assert selected.diameter_mm > previous.diameter_mm
    assert selected.spacing_mm <= selected.maximum_spacing_mm
    assert selected.provided_at_over_s_mm2_per_mm >= at
    assert selected.provided_av_over_s_mm2_per_mm >= selected.required_combo_over_s_mm2_per_mm


def test_pb_prefers_valid_four_leg_spacing_over_six_legs_at_larger_spacing():
    # High shear prevents 200 mm. Four legs at 150 mm must remain preferable
    # to a small-diameter six-leg choice at the same permitted spacing.
    selected = BeamDetailingEngine()._select_stirrup(_section(), _demand(av=2.5, at=0), 25)
    assert (selected.notation, selected.legs) == ("T12@150", 4)
    assert selected.high_shear
    assert selected.spacing_mm <= selected.maximum_spacing_mm


def test_pb_four_legs_is_not_a_hard_cap_when_the_enabled_diameters_are_insufficient():
    engine = BeamDetailingEngine()
    selected = engine._select_stirrup(_section(), _demand("left", av=21, at=0), 25)
    assert (selected.notation, selected.legs) == ("T25@100", 6)
    assert selected.provided_av_over_s_mm2_per_mm >= 21


def test_pb_diameter_escalation_cannot_use_disabled_bars():
    project = ProjectDetailingPolicy(
        enabled_diameters_mm=(10, 12, 16),
        preferred_longitudinal_diameters_mm=(16,),
        preferred_torsion_face_diameters_mm=(16, 12, 10),
    )
    engine = BeamDetailingEngine(project_policy=project)
    selected = engine._select_stirrup(_section(), _demand("left", av=10, at=0), 25)
    assert (selected.notation, selected.legs) == ("T16@100", 6)
    assert selected.diameter_mm in project.enabled_diameters_mm
    assert selected.provided_av_over_s_mm2_per_mm >= 10


def test_single_leg_torsion_requirement_cannot_be_replaced_by_more_legs():
    with pytest.raises(DetailingError, match="NO_ALLOWED_SPACING"):
        BeamDetailingEngine()._select_stirrup(_section(), _demand("left", av=0.1, at=6), 25)


def test_pb_four_leg_preference_does_not_add_an_unnecessary_four_leg_minimum():
    selected = BeamDetailingEngine()._select_stirrup(_section(), _demand(av=0.1, at=0), 25)
    assert (selected.notation, selected.legs) == ("T10@200", 2)


@pytest.mark.parametrize("role", ["PB", "SB"])
def test_narrow_beams_keep_the_existing_two_leg_preference(role):
    section = _section(width=300, role=role)
    demand = _demand(av=1, at=0)
    selected = BeamDetailingEngine()._select_stirrup(section, demand, 25)
    assert selected == DiameterFirstEngine()._select_stirrup(section, demand, 25)
    assert (selected.notation, selected.legs) == ("T12@200", 2)


@pytest.mark.parametrize("width", [300, 400, 500])
@pytest.mark.parametrize("profile", [BeamDetailingProfile.NON_SWAY, BeamDetailingProfile.SMF])
def test_sb_full_span_design_is_unchanged_by_pb_hoop_preference(width, profile):
    section = _section(width=width, role="SB", profile=profile)
    data = BeamDetailingInput(section, tuple(_demand(name) for name in ("left", "middle", "right")))
    assert BeamDetailingEngine().design(data) == DiameterFirstEngine().design(data)


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
    previous = DiameterFirstEngine().design(data)
    result = engine.design(data)
    review = result.capacity_shear_review

    assert review is not None
    assert review == engine._calculate_capacity_review(section, result.regions, data.seismic_context)
    assert review.left_mpr_positive_kNm != previous.capacity_shear_review.left_mpr_positive_kNm
    assert review.actual_capacity_shear_kN != previous.capacity_shear_review.actual_capacity_shear_kN
    assert len({region.bottom.notation for region in result.regions}) == 1
    assert result.regions[1].top.provided_area_mm2 < previous.regions[1].top.provided_area_mm2
    assert result.regions[1].bottom.notation != previous.regions[1].bottom.notation
    # Existing bar-diameter/layer preferences need not minimize each face's
    # area independently (e.g. 6T20 -> 4T25). Do not claim universal savings.
    assert sum(face.provided_area_mm2 for region in result.regions for face in (region.top, region.bottom)) < sum(
        face.provided_area_mm2 for region in previous.regions for face in (region.top, region.bottom)
    )
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
