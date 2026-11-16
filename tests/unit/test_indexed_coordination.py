from src.design import row_solver, row_candidates
import pytest
from dataclasses import astuple as dataclass_astuple, replace
from types import SimpleNamespace
from tests.unit.test_feedback1026_parent_solver import _data, _engine


@pytest.fixture
def dynamic_candidates(monkeypatch):
    # Isolate coordination caching from the separately tested lookup catalog.
    def query(section, hoops, required, engine, **kwargs):
        revision = engine.coordination_solver_revision_id
        engine.coordination_solver_revision_id = None
        try:
            return row_candidates.candidates(section, hoops, required, engine, **kwargs)
        finally:
            engine.coordination_solver_revision_id = revision
    monkeypatch.setattr(row_solver, "candidates", query)


def test_checked_state_cache_is_revision_gated_and_bounded():
    engine = _engine()
    assert row_solver._checked_state_cache(engine) is None
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    cache = row_solver._checked_state_cache(engine)
    for i in range(4097):
        row_solver._remember_checked_state(cache, i, i)
    assert len(cache) == 4096
    assert 0 not in cache and cache[4096] == 4096


def test_r9_astuple_and_signature_cache_preserve_r8_path_and_values(monkeypatch):
    data = _data("A", 700, 0, 5000)
    expected = dataclass_astuple(data)
    fast_result = row_solver._r9_fast_astuple(data)
    assert fast_result == expected
    assert hash(fast_result) == hash(expected)
    assert row_solver._r9_fast_astuple(data, tuple_factory=list) == dataclass_astuple(
        data, tuple_factory=list
    )

    for invalid in (None, 42, type(data)):
        with pytest.raises(TypeError) as stdlib_error:
            dataclass_astuple(invalid)
        with pytest.raises(TypeError) as fast_error:
            row_solver._r9_fast_astuple(invalid)
        assert type(fast_error.value) is type(stdlib_error.value)
        assert str(fast_error.value) == str(stdlib_error.value)

    calls = {"stdlib": 0, "fast": 0}
    stdlib_astuple = row_solver.astuple
    fast_astuple = row_solver._r9_fast_astuple

    def counted_stdlib(*args, **kwargs):
        calls["stdlib"] += 1
        return stdlib_astuple(*args, **kwargs)

    def counted_fast(*args, **kwargs):
        calls["fast"] += 1
        return fast_astuple(*args, **kwargs)

    monkeypatch.setattr(row_solver, "astuple", counted_stdlib)
    monkeypatch.setattr(row_solver, "_r9_fast_astuple", counted_fast)
    r8 = SimpleNamespace(coordination_solver_revision_id=row_solver.COORDINATION_SOLVER_REVISION)
    r9 = SimpleNamespace(coordination_solver_revision_id=row_solver.INDEXED_COORDINATION_SOLVER_REVISION)
    assert row_solver._engine_astuple(data, r8) == expected
    assert row_solver._engine_astuple(data, r9) == expected
    assert calls == {"stdlib": 1, "fast": 1}

    legacy_cache = {}
    r8_signature = row_solver._r6_frozen_signature(data, legacy_cache)
    r9_cache = row_solver._R9SignatureCache()
    r9_signature = row_solver._r6_frozen_signature(data, r9_cache)
    assert r8_signature == r9_signature == expected
    assert hash(r8_signature) == hash(r9_signature) == hash(expected)
    assert calls == {"stdlib": 2, "fast": 2}
    assert row_solver._r6_frozen_signature(data, r9_cache) is r9_signature
    assert calls == {"stdlib": 2, "fast": 2}
    assert r9_cache[id(data)] == (data, r9_signature)

    other = replace(data, section=replace(data.section, depth_mm=data.section.depth_mm + 10))
    collision_cache = row_solver._R9SignatureCache({id(data): (other, expected)})
    recovered = row_solver._r6_frozen_signature(data, collision_cache)
    changed = row_solver._r6_frozen_signature(other, collision_cache)
    assert recovered == expected and collision_cache[id(data)] == (data, recovered)
    assert changed == dataclass_astuple(other) and changed != recovered
    assert hash(changed) == hash(dataclass_astuple(other))



def test_checked_result_reused_across_constrained_search(monkeypatch, dynamic_candidates):
    engine = _engine()
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    engine.independent_skin_rebar = True
    data = _data("A", 700, 0, 5000, parent=False)
    face = engine._construction_face_rebar(data.section)
    first = next(row_solver._style_span_candidates(data, engine, fixed_face_rebar=face))
    calls = []
    original = engine._build_row_result
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(engine, "_build_row_result", counted)
    left = row_solver.signature(first.regions[0].top)
    again = next(row_solver._style_span_candidates(
        data, engine, first_diameter=left[0][1], left_rows=left,
        support_exact=True, fixed_face_rebar=face))
    assert row_solver.signature(again.regions[0].top) == left
    assert calls == []
from src.design.detailing import DetailingError


def _alternate(result):
    region = result.regions[0]
    row = region.top.rows[0]
    top = replace(region.top, rows=(replace(row, count=row.count + 1), *region.top.rows[1:]))
    return replace(result, regions=(replace(region, top=top), *result.regions[1:]))


def test_final_failure_uses_explored_alternative_without_regeneration(monkeypatch):
    engine = _engine()
    data = _data("A", 700, 0, 5000, parent=False)
    first = engine.design(data, prepare_coordination_pool=False)
    second = _alternate(first)
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    library = engine.offline_style_library
    first_signature = library.stable_signature(first)
    generated = []
    finalized = []
    monkeypatch.setattr(library, "applicable_fields", lambda *_: {"top": ({"rule_id": "R"},)})
    monkeypatch.setattr(library, "matched_fields", lambda *_: (SimpleNamespace(rule_id="R"),))
    monkeypatch.setattr(library, "audit", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(row_solver, "_style_path_rank", lambda path, _engine: (
        sum(library.stable_signature(item) != first_signature for item in path), 0))
    def generate(*_args, **_kwargs):
        generated.append(1)
        yield first
        yield second
    monkeypatch.setattr(row_solver, "span_candidates", generate)
    def check(result, _engine):
        finalized.append((library.stable_signature(result), len(generated)))
        if library.stable_signature(result) == first_signature:
            raise DetailingError("FINAL_TEST_FAILURE")
        return result
    monkeypatch.setattr(row_solver, "finalize_rows", check)
    result = row_solver.coordinate_segment((first,), engine.project.default_continuity_option, engine)
    assert library.stable_signature(result[0]) == library.stable_signature(second)
    assert len(finalized) == 2 and finalized[0][1] == finalized[1][1] == len(generated)


def test_all_explored_final_candidates_fail_with_concrete_reason(monkeypatch):
    engine = _engine()
    data = _data("A", 700, 0, 5000, parent=False)
    first = engine.design(data, prepare_coordination_pool=False)
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    monkeypatch.setattr(row_solver, "_style_path_rank", lambda *_: (0,))
    def fail(*_args):
        raise DetailingError("FIRST_ROW_HOOP_SUPPORT_FAILED")
    monkeypatch.setattr(row_solver, "finalize_rows", fail)
    with pytest.raises(DetailingError, match="FINAL_ROW_ALL_EXPLORED_PATHS_FAILED.*FIRST_ROW_HOOP_SUPPORT_FAILED"):
        row_solver._checked_graph_path({(0,): [((first,), (1,))]}, {(1,)}, [(0,)], engine)


def test_legacy_checked_result_reused_across_constrained_search(monkeypatch, dynamic_candidates):
    engine = _engine()
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    engine.independent_skin_rebar = True
    data = _data("A", 700, 0, 5000, parent=False)
    face = engine._construction_face_rebar(data.section)
    first = next(row_solver._legacy_span_candidates(data, engine, fixed_face_rebar=face))
    calls = []
    original = engine._build_row_result
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(engine, "_build_row_result", counted)
    left = row_solver.signature(first.regions[0].top)
    again = next(row_solver._legacy_span_candidates(data, engine, first_diameter=left[0][1],
        left_rows=left, support_exact=True, fixed_face_rebar=face))
    assert row_solver.signature(again.regions[0].top) == left
    assert calls == []


def test_parent_final_failure_uses_already_explored_alternative(monkeypatch):
    engine = _engine()
    data = _data("A", 700, 0, 5000, parent=False)
    first = engine.design(data, prepare_coordination_pool=False)
    second = _alternate(first)
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    library = engine.offline_style_library
    first_signature = library.stable_signature(first)
    generated = []
    finalized = []
    monkeypatch.setattr(library, "applicable_fields", lambda *_: {})
    monkeypatch.setattr(row_solver, "_parent_candidate_blockers", lambda *_: set())
    monkeypatch.setattr(row_solver, "_style_path_rank", lambda path, _engine: (
        sum(library.stable_signature(item) != first_signature for item in path), 0))
    def generate(*_args, **_kwargs):
        generated.append(1)
        yield first
        yield second
    monkeypatch.setattr(row_solver, "span_candidates", generate)
    def check(result, _engine):
        finalized.append((library.stable_signature(result), len(generated)))
        if library.stable_signature(result) == first_signature:
            raise DetailingError("FINAL_TEST_FAILURE")
        return result
    monkeypatch.setattr(row_solver, "finalize_rows", check)
    result = row_solver.coordinate_parent_line((first,), ((0,),), engine.project.default_continuity_option, engine)
    assert library.stable_signature(result[0]) == library.stable_signature(second)
    assert len(finalized) == 2 and finalized[0][1] == finalized[1][1] == len(generated)


def test_future_dominance_keeps_tradeoffs_and_distinct_parent_geometry():
    # Ranking volume is summed one physical section at a time. A lower sum
    # alone does not dominate a different section-wise distribution.
    dominate = row_solver._dominates_future
    preferred = ((-2, 100.0, -2, -20, 4, "A"), (40.0, 60.0))
    weaker = ((-2, 120.0, -1, -19, 5, "B"), (50.0, 70.0))
    assert dominate(preferred, weaker)
    assert not dominate(preferred, ((-2, 120.0, -1, -19, 5, "B"), (30.0, 90.0)))
    assert not dominate(preferred, ((-2, 120.0, -3, -19, 5, "B"), (50.0, 70.0)))
    assert dominate(((-3, 500, 0, 0, 9, "Z"), (500,)), ((-2, 1, -99, -99, 1, "A"), (1,)))


def test_future_frontier_prunes_only_same_interface(monkeypatch):
    frontier = {}
    ranks = {"best": (0, 100.0, -2, -10, 2, "A"),
             "worse": (0, 110.0, -1, -9, 3, "B"),
             "tradeoff": (0, 90.0, -1, -9, 3, "C")}
    monkeypatch.setattr(row_solver, "_style_path_rank", lambda path, _engine: ranks[path[0]])
    assert row_solver._retain_future_candidate(frontier, (20,), ("best",), None)
    assert not row_solver._retain_future_candidate(frontier, (20,), ("worse",), None)
    assert row_solver._retain_future_candidate(frontier, (20,), ("tradeoff",), None)
    assert row_solver._retain_future_candidate(frontier, (25,), ("worse",), None)


def test_local_rank_cache_matches_original_six_term_path_rank(monkeypatch):
    engine = _engine()
    inputs = [_data("A", 700, 0, 5000, parent=False), _data("B", 700, 0, 5000, parent=False)]
    results = tuple(engine.design(data, prepare_coordination_pool=False) for data in inputs)
    expected = row_solver._style_path_rank_legacy(results, engine)
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    library = engine.offline_style_library
    original = library.matched_fields
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(library, "matched_fields", counted)
    assert row_solver._style_path_rank(results, engine) == expected
    assert len(calls) == 2
    assert row_solver._style_path_rank(results, engine) == expected
    assert len(calls) == 2


def test_segment_dp_skips_dominated_count_but_visits_distinct_future(monkeypatch):
    engine = _engine()
    a, b = (_data("A", 700, 0, 5000, parent=False), _data("B", 700, 0, 5000, parent=False))
    first, second = (engine.design(data, prepare_coordination_pool=False) for data in (a, b))
    worse = _alternate(first)
    right = first.regions[-1]
    other_right_row = replace(right.top.rows[0], diameter_mm=right.top.rows[0].diameter_mm + 1)
    other = replace(first, regions=(*first.regions[:-1], replace(right, top=replace(
        right.top, rows=(other_right_row, *right.top.rows[1:])))))
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    library = engine.offline_style_library
    signatures = {library.stable_signature(first): 0, library.stable_signature(worse): 1,
                  library.stable_signature(other): 0}
    monkeypatch.setattr(library, "applicable_fields", lambda *_: {"top": ({"rule_id": "R"},)})
    monkeypatch.setattr(library, "matched_fields", lambda *_: (SimpleNamespace(rule_id="R"),))
    monkeypatch.setattr(library, "audit", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(row_solver, "_style_path_rank", lambda path, _engine: (
        0, sum(signatures.get(library.stable_signature(item), 0) for item in path), 0, 0, 0, ""))
    first_diameter = row_solver.signature(first.regions[0].top)[0][1]
    calls = []
    def generate(data, _engine, **kwargs):
        if kwargs["first_diameter"] != first_diameter:
            return
        if data.section.name == "A":
            yield first
            yield worse
            yield other
        else:
            previous = kwargs["left_rows"]
            calls.append(previous)
            left = second.regions[0]
            new_row = replace(left.top.rows[0], diameter_mm=previous[0][1])
            yield replace(second, regions=(replace(left, top=replace(left.top,
                rows=(new_row, *left.top.rows[1:]))), *second.regions[1:]))
    monkeypatch.setattr(row_solver, "span_candidates", generate)
    monkeypatch.setattr(row_solver, "finalize_rows", lambda result, _engine: result)
    result = row_solver.coordinate_segment((first, second), engine.project.default_continuity_option, engine)
    assert len(result) == 2
    assert len(calls) == 2
    assert calls[0][0][1] != calls[1][0][1]


def test_no_applicable_pattern_preserves_first_feasible_not_lowest_steel(monkeypatch):
    engine = _engine()
    data = _data("A", 700, 0, 5000, parent=False)
    cheap = engine.design(data, prepare_coordination_pool=False)
    expensive = _alternate(cheap)  # one more physical bar
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    library = engine.offline_style_library
    monkeypatch.setattr(library, "applicable_fields", lambda *_: {})
    monkeypatch.setattr(library, "matched_fields", lambda *_: ())
    monkeypatch.setattr(library, "audit", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(row_solver, "_style_path_rank", lambda *_: pytest.fail("fallback must not rank by steel"))
    first_diameter = row_solver.signature(expensive.regions[0].top)[0][1]
    calls = []
    def generate(*_args, **kwargs):
        calls.append(kwargs)
        if kwargs["first_diameter"] == first_diameter:
            yield expensive
            yield cheap
    monkeypatch.setattr(row_solver, "span_candidates", generate)
    monkeypatch.setattr(row_solver, "finalize_rows", lambda result, _engine: result)
    selected = row_solver.coordinate_segment((cheap,), engine.project.default_continuity_option, engine)
    assert library.stable_signature(selected[0]) == library.stable_signature(expensive)
    assert len(calls) == 1


def test_no_applicable_final_failure_continues_same_candidate_iterator(monkeypatch):
    engine = _engine()
    data = _data("A", 700, 0, 5000, parent=False)
    first = engine.design(data, prepare_coordination_pool=False)
    second = _alternate(first)
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    library = engine.offline_style_library
    first_signature = library.stable_signature(first)
    monkeypatch.setattr(library, "applicable_fields", lambda *_: {})
    monkeypatch.setattr(library, "matched_fields", lambda *_: ())
    monkeypatch.setattr(library, "audit", lambda *_args, **_kwargs: {})
    first_diameter = row_solver.signature(first.regions[0].top)[0][1]
    calls = []
    def generate(*_args, **kwargs):
        calls.append(kwargs)
        if kwargs["first_diameter"] == first_diameter:
            yield first
            yield second
    monkeypatch.setattr(row_solver, "span_candidates", generate)
    def final(result, _engine):
        if library.stable_signature(result) == first_signature:
            raise DetailingError("SELECTED_FINAL_CHECK_FAILED")
        return result
    monkeypatch.setattr(row_solver, "finalize_rows", final)
    selected = row_solver.coordinate_segment((first,), engine.project.default_continuity_option, engine)
    assert library.stable_signature(selected[0]) == library.stable_signature(second)
    assert len(calls) == 1


def test_economic_cache_reuses_only_complete_effective_inputs(monkeypatch):
    engine = _engine()
    data = _data("A", 700, 0, 5000, parent=False)
    result = engine.design(data, prepare_coordination_pool=False)
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    engine.etabs_design_results_only = True
    engine._r7_span_caches_enabled = True
    region = result.regions[0]
    extra_row = replace(region.top.rows[0], count=region.top.rows[0].count + 1)
    changed_inner = replace(result, regions=(replace(region, top=replace(region.top,
        rows=(*region.top.rows, extra_row))), *result.regions[1:]))
    assert row_solver._indexed_economic_hoop_key(data, engine, result) == row_solver._indexed_economic_hoop_key(data, engine, changed_inner)
    changed_count = _alternate(result)
    assert row_solver._indexed_economic_hoop_key(data, engine, result) != row_solver._indexed_economic_hoop_key(data, engine, changed_count)
    expected_a = row_solver._uncached_economic_hoop_states(data, engine, result)
    expected_b = row_solver._uncached_economic_hoop_states(data, engine, changed_inner)
    assert expected_a == expected_b
    original = row_solver._uncached_economic_hoop_states
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(row_solver, "_uncached_economic_hoop_states", counted)
    with engine._r7_span_cache_scope(data):
        assert row_solver._economic_hoop_states(data, engine, result) == expected_a
        assert row_solver._economic_hoop_states(data, engine, changed_inner) == expected_b
    assert len(calls) == 1


def test_economic_cache_includes_inferred_cover_coordinates():
    engine = _engine()
    data = _data("A", 700, 0, 5000, parent=False)
    result = engine.design(data, prepare_coordination_pool=False)
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    section = replace(result.section, clear_cover_to_stirrup_outer_mm=None, etabs_longitudinal_cover_mm=40)
    data = replace(data, section=section)
    result = replace(result, section=section, input_data=data)
    region = result.regions[0]
    xs = region.top.row_x_mm
    moved = replace(region.top, row_x_mm=((xs[0][0] + 1, *xs[0][1:]), *xs[1:]))
    changed = replace(result, regions=(replace(region, top=moved), *result.regions[1:]))
    assert row_solver._indexed_economic_hoop_key(data, engine, result) != row_solver._indexed_economic_hoop_key(data, engine, changed)


def test_fit_hoops_cache_reuses_exact_inputs_and_separates_controls(monkeypatch):
    engine = _engine()
    data = _data("A", 700, 0, 5000, parent=False)
    result = engine.design(data, prepare_coordination_pool=False)
    alternate = _alternate(result)
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    engine.etabs_design_results_only = True
    engine._r7_span_caches_enabled = True
    expected = row_solver._uncached_fit_hoops(result, engine, preserve_current=True)
    assert row_solver._uncached_fit_hoops(alternate, engine, preserve_current=True) == expected
    original = row_solver._uncached_fit_hoops
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(row_solver, "_uncached_fit_hoops", counted)
    with engine._r7_span_cache_scope(data):
        assert row_solver._fit_hoops(result, engine, preserve_current=True) == expected
        assert row_solver._fit_hoops(alternate, engine, preserve_current=True) == expected
        assert len(calls) == 1
        row_solver._fit_hoops(result, engine, preserve_current=False)
        assert len(calls) == 2
        region = result.regions[0]
        top = replace(region.top, effective_depth_mm=region.top.effective_depth_mm - 10)
        changed = replace(result, regions=(replace(region, top=top), *result.regions[1:]))
        row_solver._fit_hoops(changed, engine, preserve_current=True)
        assert len(calls) == 3


@pytest.mark.parametrize('parent', [False, True])
def test_initial_pool_never_reorders_constrained_candidates_at_budget_limit(monkeypatch, parent):
    engine = _engine()
    data = _data('A', 700, 0, 5000, parent=False)
    first = engine.design(data, prepare_coordination_pool=False)
    later = _alternate(first)
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    row_solver._store_style_candidate_pool(engine, first, (later,), complete=False)
    library = engine.offline_style_library
    first_signature = library.stable_signature(first)
    monkeypatch.setattr(library, 'applicable_fields', lambda *_: {'top': ({'rule_id': 'R'},)})
    monkeypatch.setattr(library, 'matched_fields', lambda *_: (SimpleNamespace(rule_id='R'),))
    monkeypatch.setattr(library, 'audit', lambda *_args, **_kwargs: {})
    monkeypatch.setattr(row_solver, '_parent_candidate_blockers', lambda *_: set())
    # If the later initial candidate is incorrectly yielded before the real
    # constrained source, its nominal local rank would otherwise win.
    monkeypatch.setattr(row_solver, '_style_path_rank', lambda path, _engine: (
        sum(library.stable_signature(item) == first_signature for item in path), 0, 0, 0, 0, ''))
    calls = []
    def constrained(*_args, **kwargs):
        calls.append(kwargs)
        yield first
        raise row_solver._CoordinationSearchLimit('ROW_SEGMENT_SEARCH_LIMIT: test')
    monkeypatch.setattr(row_solver, 'span_candidates', constrained)
    monkeypatch.setattr(row_solver, 'finalize_rows', lambda result, _engine: result)
    if parent:
        output = row_solver.coordinate_parent_line((first,), ((0,),), engine.project.default_continuity_option, engine)
    else:
        output = row_solver.coordinate_segment((first,), engine.project.default_continuity_option, engine)
    assert len(calls) == 1
    assert library.stable_signature(output[0]) == first_signature
    assert output[0].coordination_status == 'FEASIBLE_OPTIMIZATION_INCOMPLETE'
