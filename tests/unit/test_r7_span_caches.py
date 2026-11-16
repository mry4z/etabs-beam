"""Exact R7 cache isolation, lifecycle and cold-path selection oracles."""
from dataclasses import asdict, replace

import pytest

from src.design import detailing, row_candidates, row_solver
from src.design.detailing import (
    BeamDetailingInput, BeamDetailingProfile, BeamRegionDemand, BeamSection,
    DetailingError, LongitudinalDesign, PhysicalRow, RegionDetailing,
    ReinforcementMode, make_detailing_engine,
)
from src.design.offline_style import (
    DRAWING_POLICY_ID, DRAWING_POLICY_REVISION_R6_ID, DRAWING_POLICY_REVISION_R7_ID,
)


PERFORMANCE_AUDIT_FIELDS = {
    "economic_hoop_trial_calls", "economic_hoop_trial_unique_states",
    "economic_hoop_trial_cache_hits", "physical_layout_resolver_unique_states",
    "physical_layout_resolver_cache_hits", "physical_layout_resolver_cache_bypasses",
    "physical_layout_resolver_cache_entries_peak",
}


def _engine(revision=DRAWING_POLICY_REVISION_R7_ID):
    return make_detailing_engine(DRAWING_POLICY_ID, detailing_policy_revision_id=revision)


def _input(kind="PB"):
    secondary = kind == "SB"
    section = BeamSection(
        f"CACHE-{kind}", 300 if secondary else 400, 600 if secondary else 700,
        40, 25, 420, 420, 25, 6000,
        BeamDetailingProfile.NON_SWAY if secondary else BeamDetailingProfile.SMF,
        beam_role=kind, actual_frame_type="Non Sway" if secondary else "Sway Special",
    )
    area = 900 if secondary else 1500
    return BeamDetailingInput(section, tuple(
        BeamRegionDemand(name, index * 3000, area, area, .2, 0, 0)
        for index, name in enumerate(("left", "middle", "right"))
    ))


def _manual_result(engine, data):
    layout = LongitudinalDesign(rows=(PhysicalRow(4, 25),), required_area_mm2=1500)
    regions = tuple(
        RegionDetailing(d.name, d, 1000, d.as_flex_top_mm2, d.as_flex_bottom_mm2,
                        engine._select_stirrup(data.section, d, 25), layout, layout)
        for d in data.regions
    )
    return engine._build_row_result(data, ReinforcementMode.NORMAL, regions)


def _without_performance(value):
    if isinstance(value, dict):
        return {key: _without_performance(item) for key, item in value.items()
                if key not in PERFORMANCE_AUDIT_FIELDS}
    if isinstance(value, (list, tuple)):
        return tuple(_without_performance(item) for item in value)
    return value


def test_geometry_cache_is_shared_by_enumeration_build_refresh_and_finalize():
    engine, data = _engine(), _input()
    original = _manual_result(engine, data)
    region = original.regions[0]
    with engine._r7_span_cache_scope(data):
        first = row_candidates._resolve_with_geometry_cache(
            data.section, region.stirrup, region.top, engine, face="top",
            geometry_cache={}, search_stats=None,
        )
        assert engine._resolve_physical_layout(
            data.section, region.stirrup, region.top, face="top",
        ) is first
        built = engine._build_row_result(data, original.mode, original.regions)
        refreshed = detailing._refresh_result_physical_layout(built, engine)
        finalized = row_solver.finalize_rows(refreshed, engine)
        assert finalized.regions == row_solver.finalize_rows(original, _engine()).regions
        stats = engine._r7_cache_statistics_snapshot()
        assert stats["geometry_hits"] > stats["geometry_computations"]
    assert engine._r7_span_caches is None
    assert stats["geometry_requests"] == stats["geometry_hits"] + stats["geometry_computations"]


@pytest.mark.parametrize("mode", ("r7", "no_stats", "disabled", "r6"))
def test_enumeration_geometry_physical_counters_keep_success_and_failure_hits(mode):
    engine = _engine(DRAWING_POLICY_REVISION_R6_ID if mode == "r6" else DRAWING_POLICY_REVISION_R7_ID)
    data = _input()
    region = _manual_result(engine, data).regions[0]
    if mode == "disabled":
        engine._r7_span_caches_enabled = False
    stats = None if mode == "no_stats" else row_solver._new_r6_candidate_search_stats()
    enumeration_cache = {}
    invalid = LongitudinalDesign(rows=(PhysicalRow(30, 32),))

    def resolve(design):
        return row_candidates._resolve_with_geometry_cache(
            data.section, region.stirrup, design, engine, face="top",
            geometry_cache=enumeration_cache, search_stats=stats,
        )

    with engine._r7_span_cache_scope(data):
        first = resolve(region.top)
        assert resolve(region.top) is first
        errors = []
        for _ in range(2):
            with pytest.raises(DetailingError) as caught:
                resolve(invalid)
            errors.append((type(caught.value), caught.value.args))
        assert errors[0] == errors[1]
        shared = mode in {"r7", "no_stats"}
        physical = engine._r7_cache_statistics_snapshot()
        assert physical["geometry_requests"] == (4 if shared else 0)
        assert physical["geometry_hits"] == (2 if shared else 0)
        assert physical["geometry_computations"] == (2 if shared else 0)
        if stats is not None:
            assert stats["physical_layout_resolver_calls"] == 4
            assert stats["physical_layout_resolver_unique_states"] == 2
            assert stats["physical_layout_resolver_cache_hits"] == 2
            assert stats["physical_layout_resolver_cache_bypasses"] == 0
            # The original R6 branch records its peak only after success.
            assert stats["physical_layout_resolver_cache_entries_peak"] == (2 if shared else 1)
            assert stats["physical_layout_resolver_cache_limit"] == 4096
    assert engine._r7_span_caches is None


def test_geometry_full_keys_and_deterministic_failures_keep_current_error_context():
    engine, data = _engine(), _input()
    base = _manual_result(engine, data).regions[0]
    with engine._r7_span_cache_scope(data):
        variants = (
            (data.section, base.stirrup, base.top, "top"),
            (replace(data.section, name="OTHER-BEAM"), base.stirrup, base.top, "top"),
            (data.section, replace(base.stirrup, governing_reason="different"), base.top, "top"),
            (data.section, base.stirrup, replace(base.top, effective_depth_mm=123), "top"),
            (data.section, base.stirrup, replace(base.top, row_x_mm=((1, 2, 3, 4),)), "top"),
            (data.section, base.stirrup, base.top, "bottom"),
        )
        for section, hoop, design, face in variants:
            engine._resolve_physical_layout(section, hoop, design, face=face)
        assert engine._r7_cache_statistics_snapshot()["geometry_computations"] == len(variants)
        invalid = LongitudinalDesign(rows=(PhysicalRow(30, 32),))
        errors = []
        for section in (data.section, data.section, replace(data.section, name="OTHER-BEAM")):
            with pytest.raises(DetailingError) as caught:
                engine._resolve_physical_layout(section, base.stirrup, invalid, face="top")
            errors.append((type(caught.value), caught.value.args))
        assert errors[0] == errors[1]
        assert data.section.name in errors[0][1][0]
        assert "OTHER-BEAM" in errors[2][1][0]
        assert engine._r7_cache_statistics_snapshot()["geometry_failures"] == 2


def test_economic_cache_full_regions_isolate_real_geometry_depth_and_support_counts():
    engine, data = _engine(), _input()
    base = _manual_result(engine, data)
    with engine._r7_span_cache_scope(data):
        first = row_solver._economic_hoop_states(data, engine, base)
        assert row_solver._economic_hoop_states(data, engine, replace(base, coordination_status="changed")) is first
        top = base.regions[0].top
        alternatives = (
            replace(top, row_centers_mm=(top.row_centers_mm[0] + 1,)),
            replace(top, row_x_mm=(tuple(x + 1 for x in top.row_x_mm[0]),)),
            replace(top, effective_depth_mm=top.effective_depth_mm - 1),
            replace(top, rows=(PhysicalRow(5, 25),)),
        )
        for changed in alternatives:
            result = replace(base, regions=(replace(base.regions[0], top=changed), *base.regions[1:]))
            row_solver._economic_hoop_states(data, engine, result)
        stats = engine._r7_cache_statistics_snapshot()
        assert stats["economic_hoops_hits"] == 1
        assert stats["economic_hoops_computations"] == 1 + len(alternatives)
        assert stats["economic_region_options_hits"] > 0


@pytest.mark.parametrize("category", (
    "geometry", "economic_hoops", "economic_region_options", "economic_spacing",
))
def test_span_cache_limit_keeps_computing_and_failure_type_and_arguments(monkeypatch, category):
    engine, data = _engine(), _input()
    base = _manual_result(engine, data)
    monkeypatch.setattr(detailing, "R7_SPAN_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(row_solver, "R7_SPAN_CACHE_MAX_ENTRIES", 2)
    calls = []

    class DeterministicFailure(DetailingError):
        pass

    def fail(*args, **kwargs):
        calls.append(True)
        raise DeterministicFailure("fixed failure", 7)

    if category == "geometry":
        monkeypatch.setattr(engine, "_resolve_physical_layout_uncached", fail)
        def run(index):
            engine._resolve_physical_layout(
                data.section, base.regions[0].stirrup,
                replace(base.regions[0].top, required_area_mm2=index), face="top",
            )
    elif category == "economic_hoops":
        monkeypatch.setattr(row_solver, "_uncached_economic_hoop_states", fail)
        def run(index):
            regions = (replace(base.regions[0], as_min_mm2=index), *base.regions[1:])
            row_solver._economic_hoop_states(data, engine, replace(base, regions=regions))
    elif category == "economic_region_options":
        monkeypatch.setattr(row_solver, "_uncached_economic_region_options", fail)
        def run(index):
            row_solver._economic_region_options(
                data.section, data.regions[0], engine, max_main=index + 20,
                effective_depth=600, trial=lambda *args, **kwargs: None,
            )
    else:
        monkeypatch.setattr(engine.code, "maximum_stirrup_spacing", fail)
        def run(index):
            row_solver._economic_hoop_spacing_limits(
                data.section, data.regions[0], engine, 10,
                max_main=index + 20, effective_depth=600,
            )

    with engine._r7_span_cache_scope(data):
        for index in (0, 0, 1, 2, 2):
            with pytest.raises(DeterministicFailure) as caught:
                run(index)
            assert caught.value.args == ("fixed failure", 7)
        assert len(engine._r7_span_caches[category]) == 2
    assert len(calls) == (3 if category == "geometry" else 4)
    stats = engine._r7_cache_statistics_snapshot()
    assert stats[f"{category}_hits"] == (2 if category == "geometry" else 1)
    assert stats[f"{category}_limit_bypasses"] == (0 if category == "geometry" else 2)
    if category == "geometry":
        assert stats["geometry_evictions"] == 1


def test_geometry_lru_refreshes_hits_and_evicts_successes_and_failures(monkeypatch):
    engine, data = _engine(), _input()
    base = _manual_result(engine, data).regions[0]
    monkeypatch.setattr(detailing, "R7_SPAN_CACHE_MAX_ENTRIES", 2)
    good_a = replace(base.top, required_area_mm2=1)
    good_c = replace(base.top, required_area_mm2=3)
    invalid_b = LongitudinalDesign(rows=(PhysicalRow(30, 32),), required_area_mm2=2)

    def key(design):
        return data.section, base.stirrup, design, "top"

    def resolve(design):
        return engine._resolve_physical_layout(data.section, base.stirrup, design, face="top")

    with engine._r7_span_cache_scope(data):
        cache = engine._r7_span_caches["geometry"]
        first = resolve(good_a)
        with pytest.raises(DetailingError) as original_error:
            resolve(invalid_b)
        assert list(cache) == [key(good_a), key(invalid_b)]
        assert resolve(good_a) is first
        assert list(cache) == [key(invalid_b), key(good_a)]
        resolve(good_c)
        assert list(cache) == [key(good_a), key(good_c)]
        with pytest.raises(DetailingError) as repeated_error:
            resolve(invalid_b)
        assert (type(repeated_error.value), repeated_error.value.args) == (
            type(original_error.value), original_error.value.args,
        )
        assert list(cache) == [key(good_c), key(invalid_b)]
        resolve(good_c)
        assert list(cache) == [key(invalid_b), key(good_c)]
        with pytest.raises(DetailingError):
            resolve(invalid_b)
        assert list(cache) == [key(good_c), key(invalid_b)]
        resolve(good_a)
        assert list(cache) == [key(invalid_b), key(good_a)]
        stats = engine._r7_cache_statistics_snapshot()
        assert stats["geometry_hits"] == 3
        assert stats["geometry_computations"] == 5
        assert stats["geometry_evictions"] == 3
        assert len(cache) == 2
    assert engine._r7_span_caches is None


def test_geometry_lru_retains_at_most_4096_entries(monkeypatch):
    engine, data = _engine(), _input()
    base = _manual_result(engine, data).regions[0]
    monkeypatch.setattr(engine, "_resolve_physical_layout_uncached", lambda _section, _hoop, design, **_: design)
    limit = detailing.R7_SPAN_CACHE_MAX_ENTRIES
    assert limit == 4096
    designs = [replace(base.top, required_area_mm2=index) for index in range(limit + 2)]
    with engine._r7_span_cache_scope(data):
        cache = engine._r7_span_caches["geometry"]
        for design in designs[:limit + 1]:
            assert engine._resolve_physical_layout(data.section, base.stirrup, design, face="top") is design
            assert len(cache) <= limit
        assert (data.section, base.stirrup, designs[0], "top") not in cache
        engine._resolve_physical_layout(data.section, base.stirrup, designs[1], face="top")
        engine._resolve_physical_layout(data.section, base.stirrup, designs[-1], face="top")
        assert (data.section, base.stirrup, designs[1], "top") in cache
        assert (data.section, base.stirrup, designs[2], "top") not in cache
        assert len(cache) == limit
        assert engine._r7_cache_statistics_snapshot()["geometry_evictions"] == 2


def test_economic_spacing_and_region_options_keep_every_rule_input_and_original_order():
    engine, data = _engine(), _input()
    demand = data.regions[0]
    variants = (
        (data.section, demand, 10, 25, 600),
        (replace(data.section, width_mm=350), demand, 10, 25, 600),
        (data.section, replace(demand, av_over_s_mm2_per_mm=1.5), 10, 25, 600),
        (data.section, demand, 12, 25, 600),
        (data.section, demand, 10, 20, 600),
        (data.section, demand, 10, 25, 550),
    )
    with engine._r7_span_cache_scope(data):
        for section, region_demand, diameter, main, depth in variants:
            expected = engine.code.maximum_stirrup_spacing(
                section, region_demand, diameter, main, engine.project.smf_end_max_spacing_mm,
                effective_depth_mm=depth,
            )
            assert row_solver._economic_hoop_spacing_limits(
                section, region_demand, engine, diameter, max_main=main, effective_depth=depth,
            ) == expected
            assert row_solver._economic_hoop_spacing_limits(
                section, region_demand, engine, diameter, max_main=main, effective_depth=depth,
            ) == expected
        stats = engine._r7_cache_statistics_snapshot()
        assert stats["economic_spacing_computations"] == stats["economic_spacing_hits"] == len(variants)

        def trial(region_demand, diameter, spacing, legs, *, max_main, effective_depth):
            return row_solver._economic_hoop_trial(
                data.section, region_demand, engine, diameter, spacing, legs,
                max_main=max_main, effective_depth=effective_depth,
            )

        expected = row_solver._uncached_economic_region_options(
            data.section, demand, engine, max_main=25, effective_depth=600, trial=trial,
        )
        actual = row_solver._economic_region_options(
            data.section, demand, engine, max_main=25, effective_depth=600, trial=trial,
        )
        assert actual == expected
        assert row_solver._economic_region_options(
            data.section, demand, engine, max_main=25, effective_depth=600, trial=trial,
        ) is actual


def test_span_scopes_release_on_completion_exception_and_generator_close(monkeypatch):
    engine, data = _engine(), _input()
    with engine._r7_span_cache_scope(data):
        outer = engine._r7_span_caches
        with engine._r7_span_cache_scope(data):
            assert engine._r7_span_caches is outer
        with engine._r7_span_cache_scope(replace(data, section=replace(data.section, name="OTHER"))):
            assert engine._r7_span_caches is not outer
        assert engine._r7_span_caches is outer
    assert engine._r7_span_caches is None
    with pytest.raises(DetailingError), engine._r7_span_cache_scope(data):
        raise DetailingError("failed span")
    assert engine._r7_span_caches is None

    def candidates(*args, **kwargs):
        assert engine._r7_span_caches is not None
        yield "candidate"

    monkeypatch.setattr(row_solver, "_span_candidates", candidates)
    iterator = row_solver.span_candidates(data, engine)
    assert next(iterator) == "candidate"
    iterator.close()
    assert engine._r7_span_caches is None
    stats = engine._r7_cache_statistics_snapshot()
    assert stats["span_scopes_started"] == stats["span_scopes_released"] == 4


def test_rule_summary_copy_context_isolation_off_switch_and_r6_scope():
    engine = _engine()
    library = engine.offline_style_library
    summary = library.effective_rule_summary(engine)
    summary["filtered_rule_ids"].clear()
    summary["filtered_rule_reasons"].clear()
    assert library.effective_rule_summary(engine) != summary
    assert engine._r7_cache_statistics_snapshot()["rule_summary_hits"] == 2
    engine.avoid_two_row_4_3 = False
    assert "filtered_rule_reasons" not in library.effective_rule_summary(engine)
    engine.avoid_two_row_4_3 = True
    engine._r7_span_caches_enabled = False
    assert not engine.legacy_ranking_engine()._r7_span_caches_enabled
    library.effective_rule_summary(engine)
    assert engine._r7_cache_statistics_snapshot()["rule_summary_computations"] == 3
    with engine._r7_span_cache_scope(_input()):
        assert engine._r7_span_caches is None
    old = _engine(DRAWING_POLICY_REVISION_R6_ID)
    with old._r7_span_cache_scope(_input()):
        assert old._r7_span_caches is None
    assert not old._r7_span_caches_enabled


@pytest.mark.parametrize("kind", ("PB", "SB"))
def test_cold_cache_on_off_keeps_exact_selected_result_and_budget(kind):
    data, uncached, cached = _input(kind), _engine(), _engine()
    uncached._r7_span_caches_enabled = False
    expected = uncached.design(data)
    actual = cached.design(data)
    assert _without_performance(asdict(actual)) == _without_performance(asdict(expected))
    assert cached._r7_span_caches is uncached._r7_span_caches is None


def test_cache_on_off_keeps_budget_failure_and_logical_counters():
    data = _input()
    outcomes = []
    for enabled in (False, True):
        engine = _engine()
        engine._r7_span_caches_enabled = enabled
        stats, budget = row_solver._new_r6_candidate_search_stats(), [0]
        with pytest.raises(row_solver._CoordinationSearchLimit) as caught:
            tuple(row_solver.span_candidates(data, engine, search_stats=stats, search_budget=budget))
        outcomes.append((type(caught.value), caught.value.args, budget, _without_performance(stats)))
        assert engine._r7_span_caches is None
    assert outcomes[0] == outcomes[1]
