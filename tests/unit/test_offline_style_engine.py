from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.design.bar_layout_capacity import single_layer_max_bars
from src.design.detailing import (
    ACI318_14_DetailingPolicy,
    BeamDetailingInput,
    BeamDetailingProfile,
    BeamRegionDemand,
    BeamSection,
    ContinuityOption,
    DetailingError,
    FaceRebarDesign,
    FaceRebarMode,
    LongitudinalDesign,
    PhysicalRow,
    StirrupDesign,
    make_detailing_engine,
)
from src.design.equivalent_steel import equivalent_steel_mm3
from src.design.offline_style import (
    DRAWING_POLICY_ID,
    DRAWING_POLICY_REVISION_ID,
    DRAWING_POLICY_REVISION_R4_ID,
    DRAWING_POLICY_REVISION_R5_ID,
    DRAWING_POLICY_REVISION_R6_ID,
    has_two_row_4_3_layout,
)
from src.design import row_solver as row_solver_module
from src.design.row_candidates import (
    _resolve_with_geometry_cache,
    approved_pattern_candidates,
    candidates as row_candidates,
)
from src.design.row_solver import (
    _cached_economic_hoop_trial,
    _combinations,
    _economic_hoop_states,
    _fit_hoops,
    _hoop_signature,
    _r6_candidate_search_audit,
    _new_r6_candidate_search_stats,
    _r6_candidate_state_key,
    _refresh_result_physical_layout,
    _style_hoop,
    _style_hoop_states,
    coordinate_segment,
    finalize_rows,
    span_candidates,
)


def _data(
    *,
    width=300,
    depth=600,
    frame_type="Non Sway",
    profile=BeamDetailingProfile.NON_SWAY,
    top=328.7298980517751,
    bottom=300.0,
    av=0.2,
):
    section = BeamSection(
        "PB7",
        width,
        depth,
        40,
        25,
        420,
        420,
        25,
        7800,
        profile,
        beam_role="PB",
        actual_frame_type=frame_type,
    )
    demands = tuple(
        BeamRegionDemand(name, index * 2600, top, bottom, av, 0.0, 0.0)
        for index, name in enumerate(("left", "middle", "right"))
    )
    return BeamDetailingInput(section, demands)


def test_drawing_policy_changes_only_clear_spacing_and_ph_over_8():
    legacy = ACI318_14_DetailingPolicy()
    drawing = make_detailing_engine(DRAWING_POLICY_ID, ranking_mode="legacy")
    section = BeamSection("NET", 300, 600, 40, 25, 420, 420, 25, 6000)
    assert section.aggregate_mm == 25
    assert legacy.minimum_clear_spacing(20, section.aggregate_mm) == pytest.approx(100 / 3)
    assert drawing.code.minimum_clear_spacing(20, section.aggregate_mm) == 25
    assert single_layer_max_bars(300, 40, 10, 25, 20) == 4
    assert drawing._capacity_per_layer(section, 10, 20) == 5

    torsion_section = replace(section, name="PH8", width_mm=300, depth_mm=1000)
    demand = BeamRegionDemand("middle", 3000, 0, 0, 0, 0.01, 0)
    legacy_limit = legacy.maximum_stirrup_spacing(torsion_section, demand, 10, 20)[2]
    new_limit = drawing.code.maximum_stirrup_spacing(torsion_section, demand, 10, 20)[2]
    assert "TORSION_PH_OVER_8" in dict(legacy_limit)
    assert "TORSION_PH_OVER_8" not in dict(new_limit)
    assert "TORSION_ABSOLUTE" in dict(new_limit)


def test_runtime_library_is_frozen_and_excludes_pb7_holdout_condition():
    engine = make_detailing_engine(DRAWING_POLICY_ID)
    rules = engine.offline_style_library.rules
    assert len(rules) == 412
    assert all(rule["development_family_count"] >= 5 for rule in rules)
    assert not any("T1-L1-PB7" in rule["support_family_ids"] for rule in rules)
    assert not engine.offline_style_library.applicable_fields(_data(), engine).get("top_rebar_middle")
    assert engine.detailing_policy_revision_id == DRAWING_POLICY_REVISION_ID
    assert engine.detailing_policy_parameters["runtime_rule_filter"] == {
        "frozen_rule_count": 412,
        "eligible_rule_count": 408,
        "filtered_rule_count": 4,
        "filtered_rule_ids": ["R-00595-005", "R-00602-005", "R-00621-004", "R-00628-004"],
        "filter_reason": "STIRRUP_SPACING_NOT_IN_REVISION_CATALOG",
    }


def test_revision_catalog_is_exact_for_drawing_engines_and_legacy_is_unchanged():
    expected = (100, 110, 120, 130, 150, 160, 180, 200)
    legacy = make_detailing_engine()
    drawing_b = make_detailing_engine(DRAWING_POLICY_ID, ranking_mode="legacy")
    drawing_c = make_detailing_engine(DRAWING_POLICY_ID, ranking_mode="style")
    assert legacy.project.allowed_spacings_mm == (100, 110, 130, 150, 170, 200)
    assert legacy.project.smf_allowed_spacings_mm == (100, 110, 130, 150, 170, 200)
    for engine in (drawing_b, drawing_c):
        assert engine.project.allowed_spacings_mm == expected
        assert engine.project.smf_allowed_spacings_mm == expected
        assert engine.detailing_policy_revision_id == DRAWING_POLICY_REVISION_ID
    with pytest.raises(DetailingError, match="DETAILING_POLICY_REVISION_UNKNOWN"):
        make_detailing_engine(
            DRAWING_POLICY_ID,
            detailing_policy_revision_id="drawing_20260924_v1",
        )


def test_r4_filters_only_known_row_four_three_rules_and_reports_effective_library():
    data = _data(
        width=400,
        depth=800,
        frame_type="Program Determined",
        profile=BeamDetailingProfile.SMF,
        top=1500,
        bottom=1500,
    )
    old_engine = make_detailing_engine(DRAWING_POLICY_ID, ranking_mode="legacy")
    engine = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R4_ID,
        ranking_mode="legacy",
    )
    old_applicable = old_engine.offline_style_library.applicable_fields(data, old_engine)
    applicable = engine.offline_style_library.applicable_fields(data, engine)
    assert any(
        rule["rule_id"] == "R-00312-005"
        for rule in old_applicable["top_rebar_end_1"]
    )
    assert all(
        rule["rule_id"] != "R-00312-005"
        for rule in applicable["top_rebar_end_1"]
    )
    assert any(
        rule["mode_type"] == "longitudinal_core"
        for rule in applicable["top_rebar_end_1"]
    )
    filter_audit = engine.detailing_policy_parameters["runtime_rule_filter"]
    assert filter_audit["frozen_rule_count"] == 412
    assert filter_audit["eligible_rule_count"] == 396
    assert filter_audit["filtered_4_3_rule_count"] == 12
    assert filter_audit["filtered_spacing_rule_count"] == 4
    assert filter_audit["filtered_rule_reasons"]["R-00312-005"] == (
        "TWO_ROW_4_3_LAYOUT_PROHIBITED"
    )
    assert filter_audit["filtered_rule_reasons"]["R-00595-005"] == (
        "STIRRUP_SPACING_NOT_IN_REVISION_CATALOG"
    )
    assert engine.avoid_two_row_4_3
    assert engine.uniform_stirrup_diameter_per_span


def test_r4_replaces_physical_mixed_diameter_four_three_and_keeps_legal_alternatives():
    engine = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R4_ID,
        ranking_mode="legacy",
    )
    section = BeamSection(
        "PB400x800",
        400,
        800,
        40,
        25,
        420,
        420,
        25,
        6000,
        BeamDetailingProfile.SMF,
        beam_role="PB",
        actual_frame_type="Program Determined",
    )
    hoop = StirrupDesign(10, 130, 4, 1.0, 0.5, 150, "test")
    layouts = row_candidates(section, (hoop,), 1900, engine, max_layers=2)
    assert layouts
    assert not any(has_two_row_4_3_layout(layout.rows) for layout in layouts)
    assert any(
        tuple((row.count, row.diameter_mm) for row in layout.rows)
        == ((5, 20), (2, 25))
        for layout in layouts
    )

    # At width 280, five T20 bars cannot fit in a layer.  The seven-bar core
    # pattern therefore cannot materialize as 5/2; the general bounded
    # catalogue still finds another legal, hard-checked arrangement.
    narrow = replace(section, name="PB280x800", width_mm=280)
    seven_bar_rule = {
        "rule_id": "TEST-SEVEN-T20-CORE",
        "pattern_signature": {
            "main_components_by_diameter": [{"diameter_mm": 20, "count": 7}],
            "primary_bar_count": 7,
            "hanger_state": "EXPLICIT_NONE",
            "hanger_physical_components_by_diameter": [],
        },
    }
    assert engine._capacity_per_layer(narrow, 10, 20) == 4
    assert not approved_pattern_candidates(
        narrow, (hoop,), 2200, engine, (seven_bar_rule,)
    )
    narrow_layouts = row_candidates(narrow, (hoop,), 2200, engine, max_layers=2)
    assert any(
        tuple(sorted(row.count for row in layout.rows)) == (4, 4)
        for layout in narrow_layouts
    )
    assert not any(has_two_row_4_3_layout(layout.rows) for layout in narrow_layouts)


def test_r4_design_and_finalization_enforce_span_uniform_stirrup_diameter():
    data = _data(top=900, bottom=900, av=0.2)
    engine = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R4_ID,
        ranking_mode="legacy",
    )
    result = engine.design(data)
    assert len({region.stirrup.diameter_mm for region in result.regions}) == 1
    assert not any(
        has_two_row_4_3_layout(face.rows)
        for region in result.regions
        for face in (region.top, region.bottom)
    )
    bad_regions = (
        result.regions[0],
        replace(
            result.regions[1],
            stirrup=replace(result.regions[1].stirrup, diameter_mm=12),
        ),
        result.regions[2],
    )
    with pytest.raises(
        DetailingError,
        match="POLICY_SPAN_STIRRUP_DIAMETER_MUST_BE_UNIFORM",
    ):
        finalize_rows(replace(result, regions=bad_regions), engine)

    # The historical/default strategy remains on the R2 catalog and does not
    # activate either R4 physical-layout restriction.
    default_engine = make_detailing_engine()
    assert default_engine.detailing_policy_revision_id == "legacy"
    assert not default_engine.avoid_two_row_4_3
    assert not default_engine.uniform_stirrup_diameter_per_span


def test_r5_fresh_policy_adds_only_three_layers_and_two_percent_smf_rho():
    legacy = make_detailing_engine()
    r4 = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R4_ID,
    )
    fresh = make_detailing_engine(DRAWING_POLICY_ID, detailing_policy_revision_id=DRAWING_POLICY_REVISION_R5_ID)
    assert fresh.detailing_policy_revision_id == DRAWING_POLICY_REVISION_R5_ID
    assert legacy.project.max_main_bar_layers == r4.project.max_main_bar_layers == 2
    assert legacy.project.smf_max_longitudinal_ratio == r4.project.smf_max_longitudinal_ratio == 0.01613
    assert fresh.project.max_main_bar_layers == 3
    assert fresh.project.smf_max_longitudinal_ratio == 0.02
    assert fresh.avoid_two_row_4_3
    assert fresh.uniform_stirrup_diameter_per_span
    assert fresh.project.allowed_spacings_mm == (100, 110, 120, 130, 150, 160, 180, 200)
    assert fresh.project.smf_allowed_spacings_mm == fresh.project.allowed_spacings_mm
    assert fresh.detailing_policy_parameters["max_main_bar_layers"] == 3
    assert fresh.detailing_policy_parameters["smf_max_longitudinal_ratio"] == 0.02
    assert fresh.detailing_policy_parameters["runtime_rule_filter"]["eligible_rule_count"] == 396


def test_r5_generates_and_finalizes_three_rows_without_weakening_rho_or_r4_checks():
    data = _data(
        width=400,
        depth=900,
        frame_type="Program Determined",
        profile=BeamDetailingProfile.SMF,
        top=6000,
        bottom=3000,
    )
    r4 = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R4_ID,
        ranking_mode="style",
    )
    with pytest.raises(DetailingError, match="NO_FEASIBLE_TOTAL_LONGITUDINAL_LAYOUT"):
        r4.design(data)

    engine = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R5_ID,
        ranking_mode="style",
    )
    result = engine.design(data)
    assert any(len(region.top.rows) == 3 for region in result.regions)
    assert all(len(region.top.rows) <= 3 for region in result.regions)
    assert len({region.stirrup.diameter_mm for region in result.regions}) == 1
    generated = row_candidates(
        data.section,
        (result.regions[0].stirrup,),
        data.regions[0].as_top_total_mm2,
        engine,
    )
    assert any(len(candidate.rows) == 3 for candidate in generated)
    rho_checks = [check for check in result.smf_checks if check.rule_id.startswith("SMF-RHO-MAX-")]
    assert len(rho_checks) == 3
    assert all(check.status == "PASS" and check.actual <= 0.02 + 1e-9 for check in rho_checks)

    first = result.regions[0]
    over_rho = LongitudinalDesign(
        rows=(*first.top.rows[:-1], PhysicalRow(4, 20)),
        required_area_mm2=first.top.required_area_mm2,
        constraint_sources=first.top.constraint_sources,
    )
    with pytest.raises(DetailingError, match="SMF_LONGITUDINAL_RATIO_FAILED"):
        finalize_rows(
            replace(result, regions=(replace(first, top=over_rho), *result.regions[1:])),
            engine,
        )

    four_layers = LongitudinalDesign(
        rows=(*first.top.rows, PhysicalRow(2, 16)),
        required_area_mm2=first.top.required_area_mm2,
        constraint_sources=first.top.constraint_sources,
    )
    with pytest.raises(DetailingError, match="POLICY_MAX_MAIN_BAR_LAYERS_EXCEEDED"):
        finalize_rows(
            replace(result, regions=(replace(first, top=four_layers), *result.regions[1:])),
            engine,
        )

    two_row_four_three = LongitudinalDesign(
        rows=(PhysicalRow(4, 25), PhysicalRow(3, 25)),
        required_area_mm2=first.top.required_area_mm2,
        constraint_sources=first.top.constraint_sources,
    )
    with pytest.raises(DetailingError, match="POLICY_TWO_ROW_4_3_LAYOUT_PROHIBITED"):
        finalize_rows(
            replace(
                result,
                regions=(replace(first, top=two_row_four_three), *result.regions[1:]),
            ),
            engine,
        )


def test_pb7_middle_without_pattern_preserves_new_policy_legacy_fallback(monkeypatch):
    data = _data()
    old_order = make_detailing_engine(DRAWING_POLICY_ID, ranking_mode="legacy").design(data)
    styled_engine = make_detailing_engine(DRAWING_POLICY_ID, ranking_mode="style")
    styled = styled_engine.design(data)
    old_rows = tuple(
        (tuple((row.count, row.diameter_mm) for row in region.top.rows),
         tuple((row.count, row.diameter_mm) for row in region.bottom.rows),
         region.stirrup.notation)
        for region in old_order.regions
    )
    styled_rows = tuple(
        (tuple((row.count, row.diameter_mm) for row in region.top.rows),
         tuple((row.count, row.diameter_mm) for row in region.bottom.rows),
         region.stirrup.notation)
        for region in styled.regions
    )
    assert styled_rows == old_rows
    audits = [item for item in styled.coordination_audit if item.get("strategy") == "DRAWING_STYLE_SUPPORTED_FIELDS_THEN_EQUIVALENT_STEEL"]
    assert audits and audits[-1]["fallback_reason"] == "NO_APPLICABLE_PATTERN"
    assert styled.input_data.regions[1].as_top_total_mm2 == pytest.approx(328.7298980517751)
    assert id(styled) in styled_engine._style_candidate_pool_cache
    cached = styled_engine._style_candidate_pool_cache[id(styled)]
    assert cached[3] is True
    cached_supports = {
        (
            tuple(row.diameter_mm for row in candidate.regions[0].top.rows),
            tuple(row.diameter_mm for row in candidate.regions[-1].top.rows),
        )
        for candidate in cached[2]
    }
    assert len(cached_supports) == len(cached[2])

    def no_candidate_regeneration(*_args, **_kwargs):
        raise AssertionError("coordinator regenerated the already-proven span pool")

    monkeypatch.setattr(row_solver_module, "span_candidates", no_candidate_regeneration)
    coordinated = coordinate_segment(
        (styled,), ContinuityOption.OPTION_1_STRICT_EQUAL, styled_engine
    )[0]
    assert id(styled) not in styled_engine._style_candidate_pool_cache
    assert tuple(region.top.notation for region in coordinated.regions) == tuple(
        region.top.notation for region in styled.regions
    )


def test_approved_rows_known_pattern_is_materialized_before_legacy_pruning():
    data = _data(
        width=400,
        depth=700,
        frame_type="Program Determined",
        profile=BeamDetailingProfile.SMF,
        top=1500,
        bottom=1500,
    )
    engine = make_detailing_engine(DRAWING_POLICY_ID)
    applicable = engine.offline_style_library.applicable_fields(data, engine)
    bottom_rules = applicable["bottom_rebar"]
    assert {rule["mode_type"] for rule in bottom_rules} == {
        "longitudinal_core",
        "longitudinal_rows_known",
    }
    rule = next(
        rule for rule in bottom_rules
        if rule["mode_type"] == "longitudinal_rows_known"
        and rule["pattern_signature"].get("row_pattern", {}).get("unordered_main_row_components")
        == [{"diameter_mm": 20, "count": 2}, {"diameter_mm": 20, "count": 5}]
    )
    hoop = StirrupDesign(10, 150, 4, 1.0, 0.5, 150, "test")
    pool = approved_pattern_candidates(
        data.section,
        (hoop,),
        1500,
        engine,
        (rule,),
        face="bottom",
    )
    assert any(
        tuple((row.count, row.diameter_mm) for row in layout.rows) in {
            ((2, 20), (5, 20)),
            ((5, 20), (2, 20)),
        }
        for layout in pool
    )


def test_style_pattern_search_budget_fails_closed():
    data = _data(
        width=400,
        depth=700,
        frame_type="Program Determined",
        profile=BeamDetailingProfile.SMF,
        top=1500,
        bottom=1500,
    )
    engine = make_detailing_engine(DRAWING_POLICY_ID)
    with pytest.raises(DetailingError, match="ROW_SEGMENT_SEARCH_LIMIT"):
        next(span_candidates(data, engine, search_budget=[0]))


def test_stirrup_continuous_rule_matches_middle_even_when_ends_differ():
    engine = make_detailing_engine(DRAWING_POLICY_ID)
    source_rule = next(
        rule for rule in engine.offline_style_library.rules
        if rule["kind"] == "PB" and rule["field"] == "stirrup_continuous"
    )
    av = 0.2 if "<= 4" in source_rule["required_demand_level"] else 5.0
    data = _data(
        width=int(source_rule["section_width_mm"]),
        depth=int(source_rule["section_depth_mm"]),
        frame_type=source_rule["actual_frame_type"],
        profile=BeamDetailingProfile(source_rule["resolved_profile"]),
        av=av,
    )
    engine.offline_style_library.rules = (source_rule,)
    assert "stirrup_continuous" in engine.offline_style_library.applicable_fields(data, engine)
    target = source_rule["pattern_signature"]
    other_spacing = 100 if target["spacing_mm"] != 100 else 150
    left = StirrupDesign(target["diameter_mm"], other_spacing, target["legs"], 1, 0.5, 150, "test")
    middle = StirrupDesign(target["diameter_mm"], target["spacing_mm"], target["legs"], 1, 0.5, 150, "test")
    right = left
    rows = LongitudinalDesign(rows=(PhysicalRow(4, 20),))
    regions = tuple(
        SimpleNamespace(name=name, top=rows, bottom=rows, stirrup=stirrup)
        for name, stirrup in zip(("left", "middle", "right"), (left, middle, right))
    )
    result = SimpleNamespace(
        regions=regions,
        face_rebar=FaceRebarDesign(FaceRebarMode.NOT_APPLICABLE, None, 0, 0, "test"),
    )
    matched = engine.offline_style_library.matched_fields(data, result, engine)
    continuous = [item for item in matched if item.field == "stirrup_continuous"]
    assert len(continuous) == 1
    assert continuous[0].rule_id == source_rule["rule_id"]


@pytest.mark.parametrize(("av", "spacing"), [(0.8, 180), (0.9, 160), (1.25, 120)])
def test_economic_hoop_candidates_include_new_catalog_spacings(av, spacing):
    data = _data(top=900, bottom=900, av=av)
    engine = make_detailing_engine(DRAWING_POLICY_ID)
    states = _economic_hoop_states(data, engine)
    assert states
    assert tuple(hoop.spacing_mm for hoop in states[0]) == (spacing, spacing, spacing)
    assert all(hoop.diameter_mm in engine.project.ordered_stirrup_diameters() for hoop in states[0])


def test_non_hoop_support_then_steel_beats_feasible_t12_at_150_style_hit():
    section = BeamSection(
        "SB300x600",
        300,
        600,
        40,
        25,
        420,
        420,
        25,
        6000,
        BeamDetailingProfile.NON_SWAY,
        beam_role="SB",
        actual_frame_type="Non Sway",
    )
    data = BeamDetailingInput(
        section,
        tuple(
            BeamRegionDemand(name, index * 3000, 900, 900, 1.15, 0.0, 0.0)
            for index, name in enumerate(("left", "middle", "right"))
        ),
    )
    engine = make_detailing_engine(DRAWING_POLICY_ID)
    applicable = engine.offline_style_library.applicable_fields(data, engine)
    t12_rule = next(
        rule for rule in applicable["stirrup_continuous"]
        if rule["pattern_signature"] == {"diameter_mm": 12, "spacing_mm": 150, "legs": 2}
    )
    assert any(
        all((hoop.diameter_mm, hoop.spacing_mm, hoop.legs) == (12, 150, 2) for hoop in state)
        for state in _style_hoop_states(data, engine, applicable)
    )

    selected = engine.design(data)
    assert tuple(region.stirrup.notation for region in selected.regions) == (
        "T10@130", "T10@130", "T10@130"
    )

    t12_hoops = tuple(
        _style_hoop(section, demand, t12_rule["pattern_signature"], engine)
        for demand in data.regions
    )
    t12_regions = tuple(
        replace(region, stirrup=hoop)
        for region, hoop in zip(selected.regions, t12_hoops)
    )
    t12 = engine._build_row_result(
        data, selected.mode, t12_regions, face_rebar_override=selected.face_rebar
    )
    t12 = _refresh_result_physical_layout(t12, engine)
    fitted, demands = _fit_hoops(t12, engine, preserve_current=True)
    assert _hoop_signature(fitted) == _hoop_signature(t12_hoops)
    t12 = replace(
        t12,
        regions=tuple(
            replace(region, demand=demand)
            for region, demand in zip(t12.regions, demands)
        ),
    )
    t12 = finalize_rows(t12, engine)
    matches = engine.offline_style_library.matched_fields(data, t12, engine)
    assert any(item.field == "stirrup_continuous" and item.rule_id == t12_rule["rule_id"]
               for item in matches)
    assert len(engine.offline_style_library.non_hoop_matches(data, selected, engine)) == len(
        engine.offline_style_library.non_hoop_matches(data, t12, engine)
    )
    assert equivalent_steel_mm3(selected).total_mm3 < equivalent_steel_mm3(t12).total_mm3
    assert engine.offline_style_library.rank_key(data, selected, engine) < engine.offline_style_library.rank_key(
        data, t12, engine
    )

    stale = replace(
        selected,
        coordination_audit=({
            "strategy": "DRAWING_STYLE_SUPPORTED_FIELDS_THEN_EQUIVALENT_STEEL",
            "matched_fields": [{"field": "stirrup_continuous", "rule_id": t12_rule["rule_id"]}],
        },),
    )
    coordinated = coordinate_segment(
        (stale,),
        ContinuityOption.OPTION_1_STRICT_EQUAL,
        engine,
    )[0]
    style_audits = [
        item for item in coordinated.coordination_audit
        if item.get("strategy") == "DRAWING_STYLE_SUPPORTED_FIELDS_THEN_EQUIVALENT_STEEL"
    ]
    actual_matches = engine.offline_style_library.matched_fields(data, coordinated, engine)
    assert len(style_audits) == 1
    assert [
        (item["field"], item["rule_id"])
        for item in style_audits[0]["matched_fields"]
    ] == [(item.field, item.rule_id) for item in actual_matches]


def test_sb_style_design_selects_lowest_equivalent_steel_with_maximum_support():
    section = BeamSection(
        "SB300x600",
        300,
        600,
        40,
        25,
        420,
        420,
        25,
        6000,
        BeamDetailingProfile.NON_SWAY,
        beam_role="SB",
        actual_frame_type="Non Sway",
    )
    data = BeamDetailingInput(
        section,
        tuple(
            BeamRegionDemand(name, index * 3000, 900, 900, 0.2, 0.0, 0.0)
            for index, name in enumerate(("left", "middle", "right"))
        ),
    )
    engine = make_detailing_engine(DRAWING_POLICY_ID)
    candidates = tuple(span_candidates(data, engine, search_budget=[5000]))
    support_scores = [
        (len(engine.offline_style_library.non_hoop_matches(data, candidate, engine)),
         equivalent_steel_mm3(candidate).total_mm3)
        for candidate in candidates
    ]
    best_support = max(support for support, _volume in support_scores)
    best_supported_volumes = [
        volume for support, volume in support_scores if support == best_support
    ]
    assert best_support > 0
    assert len(best_supported_volumes) > 1
    assert min(best_supported_volumes) < max(best_supported_volumes)

    result = engine.design(data)
    selected_metric = equivalent_steel_mm3(result)
    assert selected_metric.available
    assert len(engine.offline_style_library.non_hoop_matches(data, result, engine)) == best_support
    assert selected_metric.total_mm3 == pytest.approx(min(best_supported_volumes))

    # Every returned style candidate is finalized under the production
    # constructability, AsMin, spacing, and row-support checks.
    for candidate in candidates:
        assert candidate.constructability.status in {"OK", "REVIEW"}
        assert all(
            check.status in {"PASS", "INFO", "WARN", "ANCHORAGE_UNVERIFIED", "SPLICE_UNVERIFIED"}
            for check in candidate.constructability.checks
        )
        for region in candidate.regions:
            for face in (region.top, region.bottom):
                assert face.rows[0].count >= region.stirrup.legs
                assert face.provided_area_mm2 + 1e-9 >= max(
                    face.required_area_mm2,
                    region.as_min_mm2,
                    region.demand.as_top_total_mm2
                    if face is region.top
                    else region.demand.as_bottom_total_mm2,
                )


def test_r6_is_explicit_and_preserves_r4_r5_and_fresh_policy_versions():
    from src.design.detailing import make_fresh_detailing_engine
    from src.design.offline_style import DRAWING_POLICY_REVISION_R7_ID

    legacy = make_detailing_engine()
    r4 = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R4_ID,
    )
    r5 = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R5_ID,
    )
    r6 = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
    )

    assert not getattr(legacy, "exact_candidate_state_dedup", False)
    assert not getattr(r4, "exact_candidate_state_dedup", False)
    assert not getattr(r5, "exact_candidate_state_dedup", False)
    assert r4.project.max_main_bar_layers == 2
    assert r4.project.smf_max_longitudinal_ratio == pytest.approx(0.01613)
    assert r5.project.max_main_bar_layers == 3
    assert r5.project.smf_max_longitudinal_ratio == pytest.approx(0.02)
    assert r6.exact_candidate_state_dedup
    assert r6.project.max_main_bar_layers == 3
    assert r6.project.smf_max_longitudinal_ratio == pytest.approx(0.02)
    assert r6.detailing_policy_parameters["candidate_search_budget_limit"] == 5000
    assert r6.detailing_policy_parameters["candidate_search_budget_basis"] == (
        "UNIQUE_CANDIDATE_VALIDATION_STATES"
    )
    assert r6.detailing_policy_parameters["last_layer_area_bound"] == (
        "FIXED_REQUIRED_AREA_ONLY_ON_LAST_ALLOWED_LAYER"
    )
    assert r6.detailing_policy_parameters["candidate_search_audit_version"] == (
        "R6_CANDIDATE_SEARCH_AUDIT_V3"
    )
    assert "PER_SPAN_FROZEN_SIGNATURE_MEMO_V1" in r6.detailing_policy_parameters[
        "candidate_search_optimization_components"
    ]
    assert "PER_SPAN_PHYSICAL_LAYOUT_CACHE_V1" in r6.detailing_policy_parameters[
        "candidate_search_optimization_components"
    ]
    audit = _r6_candidate_search_audit(_new_r6_candidate_search_stats(), complete=True, budget_remaining=5000)
    assert audit["audit_version"] == "R6_CANDIDATE_SEARCH_AUDIT_V3"
    assert "COMPILED_STYLE_LOOKUP_V1" in r6.detailing_policy_parameters[
        "candidate_search_optimization_components"
    ]
    assert make_fresh_detailing_engine(DRAWING_POLICY_ID).detailing_policy_revision_id == (
        DRAWING_POLICY_REVISION_R7_ID
    )


def test_r6_exact_candidate_key_keeps_every_physical_state_dimension_distinct():
    from dataclasses import astuple

    data = _data(width=400, depth=700)
    hoop = StirrupDesign(10, 130, 4, 1.0, 0.5, 150, "test")
    layout = LongitudinalDesign(
        rows=(PhysicalRow(4, 25), PhysicalRow(2, 20, "hanger", 500.0)),
        required_area_mm2=1800,
        clear_spacing_mm=25,
        row_centers_mm=(60.0, 110.0),
        row_x_mm=((60.0, 100.0, 140.0, 180.0), (80.0, 120.0)),
        effective_depth_mm=640.0,
        constraint_sources=("test",),
    )
    face = FaceRebarDesign(FaceRebarMode.NOT_APPLICABLE, None, 0, 0, "test")

    def key(*, context=data, active_hoop=hoop, active_layout=layout, active_face=face,
            signature_cache=None, search_stats=None):
        return _r6_candidate_state_key(
            astuple(context),
            (active_hoop,),
            (active_layout,),
            (active_layout,),
            active_face,
            signature_cache=signature_cache,
            search_stats=search_stats,
        )

    baseline = key()
    assert isinstance(hash(baseline), int)
    variants = (
        key(active_hoop=replace(hoop, spacing_mm=160)),
        key(active_layout=replace(layout, rows=(PhysicalRow(5, 25), layout.rows[1]))),
        key(active_layout=replace(layout, rows=(PhysicalRow(4, 20), layout.rows[1]))),
        key(active_layout=replace(layout, rows=(layout.rows[0], PhysicalRow(2, 20, "longitudinal", 500.0)))),
        key(active_layout=replace(layout, row_centers_mm=(60.0, 111.0))),
        key(active_layout=replace(layout, effective_depth_mm=639.0)),
        key(active_layout=replace(layout, required_area_mm2=1801.0)),
        key(active_face=FaceRebarDesign(FaceRebarMode.SKIN, None, 0, 0, "test")),
        key(context=replace(
            data,
            regions=(replace(data.regions[0], as_top_total_mm2=900.0), *data.regions[1:]),
        )),
    )
    assert all(candidate != baseline for candidate in variants)
    memo = {}
    memo_stats = _new_r6_candidate_search_stats()
    assert key(signature_cache=memo, search_stats=memo_stats) == key(
        signature_cache=memo,
        search_stats=memo_stats,
    )
    assert memo_stats["frozen_signature_computations"] == 3
    assert memo_stats["frozen_signature_cache_hits"] == 5
    assert all(entry[0] is not None for entry in memo.values())


def test_r6_duplicate_combinations_are_skipped_before_budget_charge():
    budget = [2]
    visited = set()
    duplicate_count = 0

    def skip_duplicate(combination):
        nonlocal duplicate_count
        if combination in visited:
            duplicate_count += 1
            return True
        visited.add(combination)
        return False

    combinations = tuple(_combinations(((1, 1, 2),), budget, skip_if=skip_duplicate))
    assert combinations == ((1,), (2,))
    assert duplicate_count == 1
    assert budget == [0]


def test_r6_last_layer_fixed_demand_bound_and_exact_economic_trial_cache(monkeypatch):
    data = _data(width=300, depth=600)
    r6 = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
    )
    hoop = StirrupDesign(10, 130, 2, 0.5, 0.25, 150, "test")
    stats = _new_r6_candidate_search_stats()
    required = 1800.0
    one_layer = row_candidates(
        data.section,
        (hoop,),
        required,
        r6,
        max_layers=1,
        search_stats=stats,
    )
    assert one_layer
    assert all(layout.provided_area_mm2 + 1e-9 >= required for layout in one_layer)
    assert any(
        tuple((row.count, row.diameter_mm) for row in layout.rows) == ((4, 25),)
        for layout in one_layer
    )
    assert stats["last_layer_area_count_options_pruned"] > 0

    # With another layer still available, an insufficient prefix must reach
    # the physical resolver; the demand bound only applies once the final
    # allowed row has been formed.
    three_layer_stats = _new_r6_candidate_search_stats()
    two_row_under_required_resolved = False
    original_resolve = r6._resolve_physical_layout

    def track_under_required_prefix(section, active_hoop, design, *, face):
        nonlocal two_row_under_required_resolved
        if len(design.rows) == 2 and design.provided_area_mm2 < 5000.0:
            two_row_under_required_resolved = True
        return original_resolve(section, active_hoop, design, face=face)

    monkeypatch.setattr(r6, "_resolve_physical_layout", track_under_required_prefix)
    row_candidates(
        data.section,
        (hoop,),
        5000.0,
        r6,
        max_layers=3,
        search_stats=three_layer_stats,
    )
    assert two_row_under_required_resolved

    calls = []
    monkeypatch.setattr(
        row_solver_module,
        "_economic_hoop_trial",
        lambda *args, **kwargs: calls.append((args, kwargs)) or object(),
    )
    cache = {}
    cache_stats = _new_r6_candidate_search_stats()
    section = data.section
    demand = data.regions[0]
    first = _cached_economic_hoop_trial(
        section, demand, r6, 10, 130, 4,
        max_main=25, effective_depth=540, trial_cache=cache,
        search_stats=cache_stats,
    )
    second = _cached_economic_hoop_trial(
        section, demand, r6, 10, 130, 4,
        max_main=25, effective_depth=540, trial_cache=cache,
        search_stats=cache_stats,
    )
    _cached_economic_hoop_trial(
        section, demand, r6, 10, 160, 4,
        max_main=25, effective_depth=540, trial_cache=cache,
        search_stats=cache_stats,
    )
    assert first is second
    assert len(calls) == 2
    assert cache_stats["economic_hoop_trial_calls"] == 3
    assert cache_stats["economic_hoop_trial_unique_states"] == 2
    assert cache_stats["economic_hoop_trial_cache_hits"] == 1


def test_r6_geometry_cache_hits_equal_geometry_and_preserves_failure_context():
    data = _data(width=400, depth=700)
    r6 = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
    )
    layout = LongitudinalDesign(
        rows=(PhysicalRow(4, 25), PhysicalRow(2, 20, "hanger", 500.0)),
        required_area_mm2=1800,
        constraint_sources=("GEOMETRY_CACHE_TEST",),
    )
    hoop130 = StirrupDesign(10, 130, 4, 1.0, 0.5, 150, "test")
    hoop160 = replace(hoop130, spacing_mm=160)
    cache = {}
    stats = _new_r6_candidate_search_stats()

    first = _resolve_with_geometry_cache(
        data.section, hoop130, layout, r6, face="top",
        geometry_cache=cache, search_stats=stats,
    )
    same_geometry = _resolve_with_geometry_cache(
        data.section, hoop160, layout, r6, face="top",
        geometry_cache=cache, search_stats=stats,
    )
    assert same_geometry == first
    assert stats["physical_layout_resolver_calls"] == 2
    assert stats["physical_layout_resolver_unique_states"] == 1
    assert stats["physical_layout_resolver_cache_hits"] == 1

    distinct_inputs = (
        (data.section, replace(hoop130, diameter_mm=12), layout, "top"),
        (replace(data.section, clear_cover_to_stirrup_outer_mm=45.0), hoop130, layout, "top"),
        (data.section, hoop130, replace(layout, required_area_mm2=1801.0), "top"),
        (data.section, hoop130, replace(layout, constraint_sources=("OTHER",)), "top"),
        (data.section, hoop130, replace(layout, rows=(PhysicalRow(5, 25), layout.rows[1])), "top"),
        (data.section, hoop130, replace(layout, rows=(layout.rows[0], PhysicalRow(2, 20, "hanger", 400.0))), "top"),
        (data.section, hoop130, layout, "bottom"),
    )
    for section, hoop, design, face in distinct_inputs:
        _resolve_with_geometry_cache(
            section, hoop, design, r6, face=face,
            geometry_cache=cache, search_stats=stats,
        )
    assert stats["physical_layout_resolver_unique_states"] == 8
    assert stats["physical_layout_resolver_cache_hits"] == 1

    impossible = LongitudinalDesign(rows=(PhysicalRow(20, 25),), required_area_mm2=1000)
    with pytest.raises(DetailingError, match="ROW_HORIZONTAL_CLEARANCE_FAILED") as first_failure:
        _resolve_with_geometry_cache(
            data.section, hoop130, impossible, r6, face="top",
            geometry_cache=cache, search_stats=stats,
        )
    with pytest.raises(DetailingError, match="ROW_HORIZONTAL_CLEARANCE_FAILED") as cached_failure:
        _resolve_with_geometry_cache(
            data.section, hoop160, impossible, r6, face="top",
            geometry_cache=cache, search_stats=stats,
        )
    assert cached_failure.value.args == first_failure.value.args
    assert stats["physical_layout_resolver_unique_states"] == 9
    assert stats["physical_layout_resolver_cache_hits"] == 2

    candidate_cache = {}
    candidate_stats = _new_r6_candidate_search_stats()
    row_candidates(
        data.section,
        (hoop130, hoop160),
        1800.0,
        r6,
        max_layers=1,
        search_stats=candidate_stats,
        geometry_cache=candidate_cache,
    )
    assert candidate_stats["physical_layout_resolver_cache_hits"] > 0

    r5 = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R5_ID,
    )
    legacy_cache = {}
    legacy_stats = _new_r6_candidate_search_stats()
    row_candidates(
        data.section,
        (hoop130, hoop160),
        1800.0,
        r5,
        max_layers=1,
        search_stats=legacy_stats,
        geometry_cache=legacy_cache,
    )
    assert legacy_cache == {}
    assert legacy_stats["physical_layout_resolver_calls"] == 0


def test_r6_geometry_cache_bypass_at_cap_preserves_success_and_failure(monkeypatch):
    from src.design.row_candidates import R6_GEOMETRY_CACHE_MAX_ENTRIES

    data = _data(width=400, depth=700)
    r6 = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
    )
    layout = LongitudinalDesign(rows=(PhysicalRow(4, 25),), required_area_mm2=1800)
    hoop = StirrupDesign(10, 130, 4, 1.0, 0.5, 150, "test")
    cache = {("occupied", index): None for index in range(R6_GEOMETRY_CACHE_MAX_ENTRIES)}
    stats = _new_r6_candidate_search_stats()

    original_resolver = r6._resolve_physical_layout
    resolver_calls = 0

    def counted_resolver(*args, **kwargs):
        nonlocal resolver_calls
        resolver_calls += 1
        return original_resolver(*args, **kwargs)

    monkeypatch.setattr(r6, "_resolve_physical_layout", counted_resolver)
    actual = _resolve_with_geometry_cache(
        data.section, hoop, layout, r6, face="top",
        geometry_cache=cache, search_stats=stats,
    )
    expected = original_resolver(data.section, hoop, layout, face="top")
    assert actual == expected
    assert len(cache) == R6_GEOMETRY_CACHE_MAX_ENTRIES
    assert resolver_calls == 1
    assert stats["physical_layout_resolver_cache_bypasses"] == 1
    assert stats["physical_layout_resolver_cache_entries_peak"] == 0

    impossible = LongitudinalDesign(rows=(PhysicalRow(20, 25),), required_area_mm2=1000)
    with pytest.raises(DetailingError, match="ROW_HORIZONTAL_CLEARANCE_FAILED") as bypassed:
        _resolve_with_geometry_cache(
            data.section, hoop, impossible, r6, face="top",
            geometry_cache=cache, search_stats=stats,
        )
    with pytest.raises(DetailingError, match="ROW_HORIZONTAL_CLEARANCE_FAILED") as direct:
        original_resolver(data.section, hoop, impossible, face="top")
    assert bypassed.value.args == direct.value.args
    assert len(cache) == R6_GEOMETRY_CACHE_MAX_ENTRIES
    assert resolver_calls == 2
    assert stats["physical_layout_resolver_cache_bypasses"] == 2


def test_r6_physical_layout_uses_exact_slot_selector_without_changing_r5(monkeypatch):
    import src.design.detailing as detailing_module

    data = _data(width=400, depth=700)
    r5 = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R5_ID,
    )
    r6 = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
    )
    design = LongitudinalDesign(
        rows=(PhysicalRow(4, 25), PhysicalRow(2, 20, "hanger", 500.0)),
        required_area_mm2=1800,
    )
    hoop = StirrupDesign(10, 130, 4, 1.0, 0.5, 150, "test")
    calls = []
    original_selector = detailing_module.select_physical_row_slots

    def counted_selector(*args, **kwargs):
        calls.append((args, kwargs))
        return original_selector(*args, **kwargs)

    monkeypatch.setattr(detailing_module, "select_physical_row_slots", counted_selector)
    r6_result = r6._resolve_physical_layout(data.section, hoop, design, face="top")
    assert len(calls) == len(design.rows)
    calls.clear()
    r5_result = r5._resolve_physical_layout(data.section, hoop, design, face="top")
    assert calls == []
    assert r5_result == r6_result
