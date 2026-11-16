from dataclasses import replace
import pytest
from src.design.detailing import (
    DetailingError,
    BeamDetailingEngine,
    BeamDetailingInput,
    BeamDetailingProfile,
    BeamRegionDemand,
    BeamSection,
    PhysicalRow,
    LongitudinalDesign,
    ContinuityOption,
    coordinate_adjacent_spans,
)
from src.design.row_candidates import candidates, signature


def test_coordination_combination_budget_fails_closed() -> None:
    from src.design.row_solver import _combinations

    with pytest.raises(DetailingError, match="ROW_SEGMENT_SEARCH_LIMIT"):
        next(_combinations(((1, 2), (3, 4)), [0]))


def test_bottom_effective_depth_uses_opposite_compression_face():
    engine = BeamDetailingEngine()
    data = _input()
    hoop = engine._select_stirrup(data.section, data.regions[0], 25)
    hoop = replace(hoop, diameter_mm=10)
    layout = LongitudinalDesign(rows=(PhysicalRow(4, 25),))
    actual = engine._resolve_physical_layout(data.section, hoop, layout, face="bottom")
    assert actual.row_centers_mm == (62.5,)
    assert actual.effective_depth_mm == 737.5


def test_initial_hoops_try_smaller_main_bar_before_rejecting_spacing():
    engine = BeamDetailingEngine()
    section = BeamSection(
        "STARTER", 500, 460, 40, 25, 420, 420, 25, 6000,
        BeamDetailingProfile.SMF,
    )
    demands = tuple(BeamRegionDemand(name, index * 3000, 800, 800, 0.3, 0, 0)
                    for index, name in enumerate(("left", "middle", "right")))
    selected = engine._select_stirrup(section, demands[0], 25)
    assert selected.notation == "T10@100"
    assert selected.spacing_mm <= selected.maximum_spacing_mm
    result = engine.design(BeamDetailingInput(section, demands))
    for region in result.regions:
        assert region.top.rows[0].diameter_mm == 25
        assert region.stirrup.spacing_mm <= region.stirrup.maximum_spacing_mm
        assert region.top.provided_area_mm2 >= region.top.required_area_mm2
    assert result.regions[0].stirrup.spacing_mm == 100
    assert result.regions[0].top.effective_depth_mm == 397.5


def test_centroid_cover_is_total_row_centroid():
    engine = BeamDetailingEngine()
    data = _input()
    section = replace(data.section, clear_cover_to_stirrup_outer_mm=None,
                      etabs_longitudinal_cover_mm=60)
    hoop = replace(engine._select_stirrup(data.section, data.regions[0], 25), diameter_mm=10)
    layout = LongitudinalDesign(rows=(PhysicalRow(4, 25), PhysicalRow(2, 20)))
    actual = engine._resolve_physical_layout(section, hoop, layout, face="bottom")
    centroid = sum(z*r.credited_area_mm2 for z,r in zip(actual.row_centers_mm,actual.rows))/actual.provided_area_mm2
    assert centroid == pytest.approx(60)
    assert actual.effective_depth_mm == pytest.approx(740)
    with pytest.raises(DetailingError, match="CENTROID_COVER_GEOMETRY_INFEASIBLE"):
        engine._resolve_physical_layout(replace(section,etabs_longitudinal_cover_mm=15), hoop, layout, face="top")


def _input() -> BeamDetailingInput:
    section = BeamSection(
        "ROW-V042", 500, 800, 40, 35, 420, 420, 20, 6000,
        BeamDetailingProfile.NON_SWAY,
    )
    demands = tuple(
        BeamRegionDemand(name, i * 3000, 1800, 1200, 0.2, 0.0, 0.0)
        for i, name in enumerate(("left", "middle", "right"))
    )
    return BeamDetailingInput(section, demands)


def test_v042_publishes_physical_rows_for_every_face() -> None:
    result = BeamDetailingEngine().design(_input())
    assert len(result.regions) == 3
    for region in result.regions:
        for design in (region.top, region.bottom):
            assert design.rows
            assert design.row_centers_mm
            assert design.effective_depth_mm is not None
            assert all(row.count >= 2 for row in design.rows)
            assert design.provided_area_mm2 >= design.required_area_mm2


def test_mixed_rows_are_lossless_and_not_flattened() -> None:
    design = LongitudinalDesign(
        rows=(PhysicalRow(4, 25), PhysicalRow(2, 20)),
        required_area_mm2=1000,
        clear_spacing_mm=25,
    )
    assert [(row.count, row.diameter_mm) for row in design.rows] == [(4, 25), (2, 20)]
    assert design.notation == "4T25/2T20"
    assert design.provided_area_mm2 > 1000


def test_common_support_does_not_drop_second_row_area() -> None:
    engine = BeamDetailingEngine()
    section = BeamSection(
        "SUPPORT-AREA", 300, 800, 40, 35, 420, 420, 20, 6000,
        BeamDetailingProfile.NON_SWAY,
    )
    demands = tuple(
        BeamRegionDemand(name, index * 3000, 3000, 1200, 0.2, 0.0, 0.0)
        for index, name in enumerate(("left", "middle", "right"))
    )
    result = engine.design(BeamDetailingInput(section, demands))
    first, second = coordinate_adjacent_spans(
        result, result, ContinuityOption.OPTION_1_STRICT_EQUAL, engine
    )
    for coordinated in (first, second):
        for region in coordinated.regions:
            assert region.top.provided_area_mm2 >= region.top.required_area_mm2
    assert first.regions[2].top.rows == second.regions[0].top.rows
    # Both physical rows must survive coordination; the catalogue may select
    # a lower-excess mixed-diameter second row instead of the old all-T25 one.
    assert len(first.regions[2].top.rows) == 2
    assert first.regions[2].top.rows[0].count < sum(r.count for r in first.regions[2].top.rows)


def test_320mm_section_keeps_two_leg_stirrups_when_two_legs_are_adequate() -> None:
    section = BeamSection(
        "TWO-LEG-320MM", 320, 800, 40, 35, 420, 420, 20, 6000,
        BeamDetailingProfile.NON_SWAY,
    )
    demands = tuple(
        BeamRegionDemand(name, index * 3000, required, 500, 1.0, 0.0, 0.0)
        for index, (name, required) in enumerate(
            zip(("left", "middle", "right"), (2580, 1100, 3300))
        )
    )
    result = BeamDetailingEngine().design(BeamDetailingInput(section, demands))
    # DET-R024/VAL-20260827: four legs are a preference band, not a minimum;
    # a 320 mm beam retains two legs when the shear/torsion checks pass.
    assert [region.stirrup.legs for region in result.regions] == [2, 2, 2]
    assert [region.top.notation for region in result.regions] == [
        "4T25/2T20", "3T25", "7T25 4/3",
    ]


def test_engine_does_not_use_legacy_mixed_group_selector(monkeypatch):
    engine = BeamDetailingEngine()
    def forbidden(*args, **kwargs):
        raise AssertionError("legacy mixed representation must not enter new design")
    monkeypatch.setattr(engine, "_legacy_design", forbidden)
    monkeypatch.setattr(engine, "_mixed_candidate", forbidden)
    result = engine.design(_input())
    assert all(r.top.rows for r in result.regions)


def test_first_row_counts_can_differ_while_diameter_and_hoop_support_hold():
    data = _input()
    demands = tuple(replace(d, as_top_total_mm2=required)
                    for d, required in zip(data.regions, (2200, 1100, 2600)))
    result = BeamDetailingEngine().design(replace(data, regions=demands))
    rows = [r.top.rows[0] for r in result.regions]
    assert len({r.diameter_mm for r in rows}) == 1
    assert len({r.count for r in rows}) > 1
    assert all(r.top.rows[0].count >= r.stirrup.legs for r in result.regions)


def test_inward_rows_use_actual_outer_slots_and_reject_more_bars():
    engine = BeamDetailingEngine()
    data = _input()
    hoop = engine._select_stirrup(data.section, data.regions[0], 25)
    layout = LongitudinalDesign(rows=(PhysicalRow(4,25), PhysicalRow(3,20)))
    resolved = engine._resolve_physical_layout(data.section, hoop, layout, face="top")
    assert set(resolved.row_x_mm[1]).issubset(resolved.row_x_mm[0])
    assert len(resolved.row_x_mm[1]) == 3
    with pytest.raises(DetailingError, match="ROW_ALIGNMENT_INFEASIBLE"):
        engine._resolve_physical_layout(data.section, hoop,
            LongitudinalDesign(rows=(PhysicalRow(2,25),PhysicalRow(4,20))), face="top")


def test_final_hoop_limits_use_actual_multilayer_depth():
    engine = BeamDetailingEngine()
    data = _input()
    result = engine.design(data)
    region = result.regions[0]
    top = engine._resolve_physical_layout(data.section, region.stirrup,
        LongitudinalDesign(rows=(PhysicalRow(4,25),PhysicalRow(4,25))), face="top")
    changed = replace(region, top=top)
    final = engine._finalize_stirrup_controls(data.section, (changed,))[0]
    limits = dict(final.stirrup.spacing_limits)
    assert limits["SHEAR_LOW_D_OVER_2"] == pytest.approx(top.effective_depth_mm/2)


def test_segment_is_idempotent_and_preserves_mixed_profiles():
    from src.design.detailing import coordinate_span_segment
    engine = BeamDetailingEngine()
    original = _input()
    smf = replace(original, section=replace(original.section, name="SMF", detailing_profile=BeamDetailingProfile.SMF))
    results = (engine.design(original), engine.design(smf))
    once = coordinate_span_segment(results, ContinuityOption.OPTION_1_STRICT_EQUAL, engine)
    twice = coordinate_span_segment(once, ContinuityOption.OPTION_1_STRICT_EQUAL, engine)
    assert [r.detailing_profile for r in once] == [BeamDetailingProfile.NON_SWAY, BeamDetailingProfile.SMF]
    assert [[(r.top.rows,r.bottom.rows) for r in item.regions] for item in once] == [
        [(r.top.rows,r.bottom.rows) for r in item.regions] for item in twice]
    assert once[0].regions[-1].top.rows == once[1].regions[0].top.rows
    assert all(c.status in {"PASS", "INFO", "WARN"} for c in once[1].smf_checks)


def test_shared_support_option1_aligns_rows_without_forcing_count_difference():
    engine = BeamDetailingEngine()
    left = engine.design(_input())
    right_data = replace(_input(), regions=tuple(
        replace(d, as_top_total_mm2=value)
        for d, value in zip(_input().regions, (900, 900, 3200))))
    right = engine.design(right_data)
    coordinated = coordinate_adjacent_spans(
        left, right, ContinuityOption.OPTION_1_STRICT_EQUAL, engine)
    first = coordinated[0].regions[-1].top
    second = coordinated[1].regions[0].top
    assert len(first.rows) == len(second.rows)
    assert first.rows[0].diameter_mm == second.rows[0].diameter_mm
    # Shared support coordination aligns row count/diameter, while each side
    # may independently choose its first-row count. Equal counts are valid
    # when both sides select the same feasible support state.
    assert first.rows[0].count >= coordinated[0].regions[-1].stirrup.legs
    assert second.rows[0].count >= coordinated[1].regions[0].stirrup.legs


def _support_fixture(name: str, top_required: float) -> BeamDetailingInput:
    section = BeamSection(
        name, 320, 800, 40, 35, 420, 420, 20, 6000,
        BeamDetailingProfile.NON_SWAY,
    )
    demands = tuple(
        BeamRegionDemand(region, index * 3000, top_required, 500, 1.0, 0.0, 0.0)
        for index, region in enumerate(("left", "middle", "right"))
    )
    return BeamDetailingInput(section, demands)


def test_support_state_preserves_rows_for_low_high_and_reverse_segments():
    from src.design.detailing import coordinate_span_segment
    from src.design.row_solver import _initial_hoops

    engine = BeamDetailingEngine()
    low_data = _support_fixture("LOW-REQ", 900)
    high_data = _support_fixture("HIGH-REQ", 3300)
    low = engine.design(low_data)
    high = engine.design(high_data)
    low_candidates = candidates(
        low_data.section, _initial_hoops(low_data, engine), 900, engine,
        first_diameter=25, max_layers=2,
    )
    # The low-demand side may use the two-leg minimum; four bars are not a
    # universal first-row floor for a 320 mm section.
    assert signature(low_candidates[0]) == ((2, 25),)
    # The low-demand catalogue retains a two-row alternative for support
    # coordination; its local first row need not be the high-demand 4T25 row.
    assert any(signature(item) == ((2, 25), (2, 25)) for item in low_candidates)
    assert signature(high.regions[0].top) == ((4, 25), (3, 25))

    for ordered in ((low, high), (high, low)):
        coordinated = coordinate_span_segment(
            ordered, ContinuityOption.OPTION_1_STRICT_EQUAL, engine
        )
        shared_left = coordinated[0].regions[-1].top
        shared_right = coordinated[1].regions[0].top
        assert len(shared_left.rows) == len(shared_right.rows)
        assert [row.diameter_mm for row in shared_left.rows] == [
            row.diameter_mm for row in shared_right.rows
        ]
        assert shared_left.rows[0].count >= coordinated[0].regions[-1].stirrup.legs
        assert shared_right.rows[0].count >= coordinated[1].regions[0].stirrup.legs
        assert len(shared_left.rows) == 2
        for result in coordinated:
            for region in result.regions:
                for face in (region.top, region.bottom):
                    assert face.provided_area_mm2 >= face.required_area_mm2
                    assert len(face.row_centers_mm) == len(face.rows)
                    assert len(face.row_x_mm) == len(face.rows)


def test_real_input_option2_allows_count_adjustment_and_is_idempotent():
    from src.design.detailing import coordinate_span_segment

    engine = BeamDetailingEngine()
    inputs = tuple(_support_fixture(name, top)
                   for name, top in (("OPT2-A", 2000), ("OPT2-B", 3300), ("OPT2-C", 2400)))
    initial = tuple(engine.design(data) for data in inputs)
    coordinated = coordinate_span_segment(
        initial, ContinuityOption.OPTION_2_COUNT_ADJUSTMENT, engine
    )
    assert any(len(region.top.rows) > 1
               for result in coordinated for region in result.regions)
    for left, right in zip(coordinated, coordinated[1:]):
        previous = left.regions[-1].top.rows
        current = right.regions[0].top.rows
        assert all(a.diameter_mm == b.diameter_mm for a, b in zip(previous, current))
    for result in coordinated:
        for region in result.regions:
            for face in (region.top, region.bottom):
                assert face.provided_area_mm2 >= face.required_area_mm2
                assert face.row_centers_mm and face.row_x_mm
    repeated = coordinate_span_segment(
        coordinated, ContinuityOption.OPTION_2_COUNT_ADJUSTMENT, engine
    )
    assert [[(r.top.rows, r.bottom.rows) for r in result.regions]
            for result in coordinated] == [[(r.top.rows, r.bottom.rows) for r in result.regions]
                                            for result in repeated]
