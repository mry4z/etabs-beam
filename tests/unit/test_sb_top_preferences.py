"""User-confirmed SB top-bar preferences, without changing PB/bottom rules."""

from dataclasses import replace

import pytest

from src.design.detailing import (
    BeamDetailingEngine,
    BeamDetailingInput,
    BeamDetailingProfile,
    BeamRegionDemand,
    BeamSection,
    ContinuityOption,
    ProjectDetailingPolicy,
    ReinforcementMode,
    coordinate_adjacent_spans,
)


def _section(profile=BeamDetailingProfile.NON_SWAY):
    return BeamSection("SB-TOP", 300, 730, 40, 25, 420, 420, 25, 6000, profile, beam_role="SB")


def _input(top=1200, bottom=1500, profile=BeamDetailingProfile.NON_SWAY):
    return BeamDetailingInput(
        _section(profile),
        tuple(
            BeamRegionDemand(name, station, top, bottom, 0.3, 0, 0)
            for name, station in (("left", 0), ("middle", 3000), ("right", 6000))
        ),
    )


@pytest.mark.parametrize("profile", [BeamDetailingProfile.NON_SWAY, BeamDetailingProfile.SMF])
def test_sb_top_prefers_at_most_t20_but_bottom_keeps_its_own_selection(profile):
    result = BeamDetailingEngine().design(_input(profile=profile))
    for region in result.regions:
        assert max(bar.diameter_mm for bar in region.top.bars) <= 20
        assert region.top.provided_area_mm2 >= region.top.required_area_mm2
        if profile.is_smf:
            assert region.stirrup.final_main_bar_diameter_mm == min(
                bar.diameter_mm for face in (region.top, region.bottom) for bar in face.bars if bar.diameter_mm >= 20
            )
    assert any(bar.diameter_mm == 25 for bar in result.regions[1].bottom.bars)
    assert len({region.bottom.notation for region in result.regions}) == 1


def test_sb_minimum_area_midspan_does_not_use_two_different_corner_bars():
    # Before the fix, AsMin controls and the area-only tie breaker selects
    # 1T25+1T16, so the two physical top bars cannot form a symmetric pair.
    result = BeamDetailingEngine().design(_input(top=0))
    middle = result.regions[1].top
    assert any(bar.count >= 2 for bar in middle.bars)
    if sum(bar.count for bar in middle.bars) == 2:
        assert len(middle.bars) == 1
    assert middle.provided_area_mm2 >= result.regions[1].as_min_mm2


def test_matching_pair_is_applied_while_searching_mixed_candidates():
    engine = BeamDetailingEngine()
    candidate = engine._mixed_candidate(_section(), 670, 2, 10, 25, 16, 2, matching_pair=True)
    assert candidate is not None
    assert any(bar.count >= 2 for bar in candidate.bars)
    assert candidate.provided_area_mm2 >= 670


def test_sb_top_t25_remains_available_when_smaller_bars_do_not_fit():
    engine = BeamDetailingEngine()
    section = _section()
    limit = max(
        engine._capacity_per_layer(section, 10, diameter) * 2 * engine.project.catalog.area(diameter)
        for diameter in (16, 20)
    )
    result = engine._select_longitudinal(section, limit + 10, 2, 10, 2, top_region="middle")
    assert result is not None
    assert max(bar.diameter_mm for bar in result.bars) == 25
    assert result.provided_area_mm2 >= limit + 10
    assert result.layers <= 2


def test_sb_top_preference_cannot_invent_an_unavailable_diameter_or_a_solution():
    engine = BeamDetailingEngine()
    assert engine._select_longitudinal(_section(), 1e6, 2, 10, 2, top_region="middle") is None
    assert engine.project.enabled_diameters_mm == (10, 12, 16, 20, 25)


def test_sb_shared_support_does_not_reintroduce_avoidable_t25():
    engine = BeamDetailingEngine()
    first = engine.design(_input())
    second = replace(engine.design(_input()), beam_name="SB-TOP-2")
    old_support = engine._fixed_diameter_design(first.section, 1200, 2, 10, 25, 2)
    first = replace(first, regions=(*first.regions[:2], replace(first.regions[2], top=old_support)))
    first, second = coordinate_adjacent_spans(first, second, ContinuityOption.OPTION_1_STRICT_EQUAL, engine)
    assert first.regions[2].top.notation == second.regions[0].top.notation
    assert max(bar.diameter_mm for bar in first.regions[2].top.bars) <= 20


def test_sb_face_mode_keeps_the_same_top_preference_and_matching_pair():
    demands = tuple(
        BeamRegionDemand(name, station, 9000, 9000, 0.3, 0.2, 16000)
        for name, station in (("left", 0), ("middle", 3000), ("right", 6000))
    )
    result = BeamDetailingEngine().design(BeamDetailingInput(_section(), demands))
    assert result.mode is ReinforcementMode.FACE_TORSION
    for region in result.regions:
        assert max(bar.diameter_mm for bar in region.top.bars) <= 20
        assert region.top.provided_area_mm2 >= region.as_flex_top_mm2
    assert any(bar.count >= 2 for bar in result.regions[1].top.bars)


@pytest.mark.parametrize("clear_cover", [None, 40])
def test_pb38_midspan_reduces_with_four_leg_preference_without_changing_support_diameter(clear_cover):
    section = BeamSection(
        "T2-Story2-PB38-1",
        400,
        800,
        clear_cover,
        25,
        420,
        420,
        25,
        7700.332117999814,
        BeamDetailingProfile.SMF,
        60,
    )
    demands = (
        BeamRegionDemand(
            "left", 400, 2885.2166410871027, 1671.0812553979208, 1.85846201798997, 0.4438876275669137, 907.4838657977984
        ),
        BeamRegionDemand(
            "middle",
            3000,
            1129.5091989569646,
            1616.0162600914352,
            1.5296043112888322,
            0.44849047034711703,
            916.8939175776461,
        ),
        BeamRegionDemand(
            "right",
            7300,
            2616.305922849691,
            1531.0999014853915,
            1.605723330010188,
            0.44907488230420234,
            918.0886893827114,
        ),
    )
    engine = BeamDetailingEngine()
    result = engine.design(BeamDetailingInput(section, demands))
    assert all(region.stirrup.legs == 4 for region in result.regions)
    assert tuple(region.stirrup.notation for region in result.regions) == ("T10@100", "T16@200", "T10@100")
    first_rows = [region.top.rows[0] for region in result.regions]
    assert len({row.diameter_mm for row in first_rows}) == 1
    assert all(row.count >= region.stirrup.legs for row, region in zip(first_rows, result.regions))
    assert all(region.top.provided_area_mm2 >= region.top.required_area_mm2 for region in result.regions)
    assert result.regions[1].top.required_area_mm2 == pytest.approx(1129.5091989569646)
    # The newly authorized hoop preference reduces the controlling leg count;
    # the existing PB top count floor and support-diameter/lap rule remain.
    assert all(sum(bar.count for bar in region.top.bars) >= region.stirrup.legs for region in result.regions)
    assert len({region.bottom.notation for region in result.regions}) == 1
    assert ProjectDetailingPolicy().minimum_main_diameter_for(section) == 20
