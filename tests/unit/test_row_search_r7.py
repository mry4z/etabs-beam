"""Small exact oracles for R7 boundary pruning and standalone selection."""
from dataclasses import dataclass
import heapq
from types import SimpleNamespace

import pytest

from src.design import row_solver
from src.design.detailing import (
    BeamDetailingInput,
    BeamDetailingProfile,
    BeamRegionDemand,
    BeamSection,
    DetailingError,
    FaceRebarDesign,
    FaceRebarMode,
    LongitudinalDesign,
    PhysicalRow,
    StirrupDesign,
)


def _old_combinations(groups, budget, *, skip_if, stop_if):
    """The exhaustive heap traversal before optional axis pruning."""
    start = (0,) * len(groups)
    queue, seen = [(0, start)], {start}
    while queue and not stop_if():
        _, indices = heapq.heappop(queue)
        combination = tuple(group[index] for group, index in zip(groups, indices))
        if not skip_if(combination):
            budget[0] -= 1
            if budget[0] < 0:
                raise row_solver._CoordinationSearchLimit("oracle budget exhausted")
            yield combination
        for axis, group in enumerate(groups):
            if indices[axis] + 1 < len(group):
                following = (*indices[:axis], indices[axis] + 1, *indices[axis + 1:])
                if following not in seen:
                    seen.add(following)
                    heapq.heappush(queue, (sum(following), following))


@pytest.mark.parametrize("budget_limit", (0, 7, 5000))
@pytest.mark.parametrize("impossible_support", (False, True))
def test_r7_pruning_preserves_exhaustive_sequence_budget_and_first_valid(
    budget_limit, impossible_support,
):
    groups = (tuple(range(5)), tuple(range(4)), tuple(range(4)), tuple(range(3)))
    left_classes, right_classes = ("A", "B", "A", "C", "B"), ("X", "Y", "X", "Z")

    def support(combination):
        return left_classes[combination[0]], right_classes[combination[2]]

    all_supports = {(left, right) for left in left_classes for right in right_classes}

    def run(pruned):
        accepted, chosen, yielded, expansions = set(), {}, [], []
        budget = [budget_limit]

        def covered(combination):
            return support(combination) in accepted

        def axes(combination):
            expansions.append((combination, covered(combination)))
            return (0, 2) if covered(combination) else range(4)

        kwargs = dict(skip_if=covered, stop_if=lambda: all_supports <= accepted)
        iterator = (
            row_solver._combinations(groups, budget, expand_axes=axes, **kwargs)
            if pruned else _old_combinations(groups, budget, **kwargs)
        )
        exhausted = False
        try:
            for combination in iterator:
                yielded.append(combination)
                left, middle, right, bottom = combination
                hard_valid = middle >= (left + right) % 3 and bottom >= (2 * left + right) % 2
                if impossible_support and support(combination) == ("C", "Z"):
                    hard_valid = False
                if hard_valid:
                    chosen[support(combination)] = combination
                    accepted.add(support(combination))
                # Exercise external coverage changes between generator resumes.
                if len(yielded) == 4:
                    accepted.add(("B", "Y"))
        except row_solver._CoordinationSearchLimit:
            exhausted = True
        return yielded, budget, chosen, accepted, exhausted, expansions

    old, pruned = run(False), run(True)
    assert pruned[:5] == old[:5]
    if budget_limit == 5000:
        assert any(covered for _combination, covered in pruned[5])
        assert (all_supports <= pruned[3]) is not impossible_support


def test_r7_covered_classes_do_not_expand_middle_or_bottom_after_acceptance():
    accepted = set()
    expanded = []
    groups = (range(3), range(20), range(3), range(20))

    def covered(combination):
        return combination[2] < 2 and (combination[0], combination[2]) in accepted

    def axes(combination):
        expanded.append(combination)
        return (0, 2) if covered(combination) else range(4)

    for combination in row_solver._combinations(groups, skip_if=covered, expand_axes=axes):
        if combination[2] < 2:
            accepted.add((combination[0], combination[2]))
    # The uncovered final-right class exhausts its full middle/bottom grid;
    # the six accepted endpoint pairs expand only at middle=bottom=0.
    covered_expansions = [value for value in expanded if value[2] < 2]
    assert len(covered_expansions) == 6
    assert all(value[1] == value[3] == 0 for value in covered_expansions)


@dataclass(frozen=True)
class _Candidate:
    cost: int
    input_data: object
    coordination_audit: tuple = ()
    coordination_status: str = "STABLE"
    regions: tuple = ()


def _data():
    section = BeamSection(
        "R7-SEARCH", 300, 600, 40, 25, 420, 420, 25, 6000,
        BeamDetailingProfile.NON_SWAY,
    )
    regions = tuple(
        BeamRegionDemand(name, index * 3000, 500, 300, 0.2, 0, 0)
        for index, name in enumerate(("left", "middle", "right"))
    )
    return BeamDetailingInput(section, regions)


def _fake_engine(*, independent, applicable):
    library = SimpleNamespace(
        applicable_fields=lambda *_args: applicable,
        audit=lambda *_args, **kwargs: kwargs,
        matched_fields=lambda *_args: (),
        non_hoop_matches=lambda *_args: (),
    )
    return SimpleNamespace(
        ranking_mode="style", independent_skin_rebar=independent,
        exact_candidate_state_dedup=False, offline_style_library=library,
        _construction_face_rebar=lambda _section: object(),
        _style_candidate_pool_cache={}, detailing_policy_id="test",
        detailing_policy_revision_id="test", rules_path=None, code=object(),
        project=SimpleNamespace(enabled_diameters_mm=(25,), allowed_spacings_mm=(100,),
                                smf_allowed_spacings_mm=(100,)),
    )


@pytest.mark.parametrize("independent,prepare,expected", (
    (True, False, 1), (True, True, 2), (False, False, 2),
))
def test_standalone_fallback_is_r7_only_and_never_caches_truncated_pool(
    monkeypatch, independent, prepare, expected,
):
    data = _data()
    engine = _fake_engine(independent=independent, applicable={})
    visited, faces, modes = [], [], []
    monkeypatch.setattr(engine, "_construction_face_rebar", lambda section: faces.append(section) or "fixed")

    def fallback(_data, _engine, **kwargs):
        modes.append(kwargs["coordination_pool"])
        for cost in (1, 2):
            visited.append(cost)
            yield _Candidate(cost, data)

    monkeypatch.setattr(row_solver, "_legacy_span_candidates", fallback)
    result = row_solver.design_span(data, engine, prepare_coordination_pool=prepare)
    assert result.cost == 1
    assert len(visited) == expected
    assert modes == [expected == 2]
    assert len(faces) == int(independent)
    assert bool(engine._style_candidate_pool_cache) is (expected == 2)
    if expected == 1:
        assert row_solver._take_style_candidate_pool(engine, result) is None
    else:
        assert len(row_solver._take_style_candidate_pool(engine, result)) == 2


def test_standalone_completed_style_failure_uses_only_first_valid_fallback(monkeypatch):
    data = _data()
    engine = _fake_engine(independent=True, applicable={"top_rebar_middle": ({"rule_id": "R1"},)})
    style_completed, fallback_visits = [], []

    def style(*_args, **_kwargs):
        style_completed.append(True)
        return iter(())

    def fallback(*_args, **_kwargs):
        for cost in (1, 2):
            fallback_visits.append(cost)
            yield _Candidate(cost, data)

    monkeypatch.setattr(row_solver, "_style_span_candidates", style)
    monkeypatch.setattr(row_solver, "_legacy_span_candidates", fallback)
    result = row_solver.design_span(data, engine, prepare_coordination_pool=False)
    assert style_completed == [True]
    assert fallback_visits == [1]
    assert result.coordination_audit[-1]["fallback_reason"] == "NO_FEASIBLE_SUPPORTED_PATTERN"
    assert not engine._style_candidate_pool_cache


def test_standalone_style_search_limit_propagates_without_fallback(monkeypatch):
    data = _data()
    engine = _fake_engine(independent=True, applicable={"top_rebar_middle": ({"rule_id": "R1"},)})

    def limited(*_args, **_kwargs):
        raise row_solver._CoordinationSearchLimit("ROW_SEGMENT_SEARCH_LIMIT")

    def fallback(*_args, **_kwargs):
        pytest.fail("an incomplete style search must not enter fallback")

    monkeypatch.setattr(row_solver, "_style_span_candidates", limited)
    monkeypatch.setattr(row_solver, "_legacy_span_candidates", fallback)
    with pytest.raises(DetailingError, match="ROW_SEGMENT_SEARCH_LIMIT"):
        row_solver.design_span(data, engine, prepare_coordination_pool=False)


def test_standalone_supported_style_finishes_economy_selection(monkeypatch):
    data = _data()
    engine = _fake_engine(independent=True, applicable={"top_rebar_middle": ({"rule_id": "R1"},)})
    match = SimpleNamespace(rule_id="R1")
    engine.offline_style_library.matched_fields = lambda *_args: (match,)
    engine.offline_style_library.non_hoop_matches = lambda *_args: (match,)
    engine.offline_style_library.rank_key = lambda _data, candidate, _engine: (candidate.cost,)
    layout = LongitudinalDesign(rows=(PhysicalRow(2, 25),))
    regions = (SimpleNamespace(top=layout),) * 3
    visited = []

    def style(*_args, **_kwargs):
        for cost in (2, 1):
            visited.append(cost)
            yield _Candidate(cost, data, regions=regions)

    def fallback(*_args, **_kwargs):
        pytest.fail("supported style candidates must retain style selection")

    monkeypatch.setattr(row_solver, "_style_span_candidates", style)
    monkeypatch.setattr(row_solver, "_legacy_span_candidates", fallback)
    result = row_solver.design_span(data, engine, prepare_coordination_pool=False)
    assert visited == [2, 1]
    assert result.cost == 1
    assert not engine._style_candidate_pool_cache


@pytest.mark.parametrize("independent,axes,builds", ((True, 4, 1), (False, 5, 2)))
def test_r7_fixed_skin_has_no_cartesian_axis(monkeypatch, independent, axes, builds):
    data = _data()
    engine = _fake_engine(independent=independent, applicable={})
    engine.project.max_main_bar_layers = 3
    engine.project.ordered_longitudinal_diameters_for = lambda _section: (25,)
    engine.code = SimpleNamespace(minimum_longitudinal_area=lambda *_args: 100)
    hoop = StirrupDesign(10, 130, 2, 0.5, 0.25, 150, "test")
    layout = LongitudinalDesign(rows=(PhysicalRow(2, 25),), effective_depth_mm=540)
    face = FaceRebarDesign(FaceRebarMode.NOT_APPLICABLE, None, 0, 0, "fixed")
    face_options, widths, overrides = [], [], []

    def approved_faces(*_args):
        face_options.append(True)
        return (face, face)

    def reject_build(*_args, **kwargs):
        overrides.append(kwargs["face_rebar_override"])
        raise DetailingError("test hard rejection")

    engine._approved_face_rebar_candidates = approved_faces
    engine._build_row_result = reject_build
    monkeypatch.setattr(row_solver, "_style_hoop_states", lambda *_args, **_kwargs: ((hoop,) * 3,))
    monkeypatch.setattr(row_solver, "candidates", lambda *_args, **_kwargs: (layout,))
    monkeypatch.setattr(row_solver, "approved_pattern_candidates", lambda *_args, **_kwargs: ())
    combinations = row_solver._combinations

    def record_groups(groups, *args, **kwargs):
        widths.append(len(groups))
        yield from combinations(groups, *args, **kwargs)

    monkeypatch.setattr(row_solver, "_combinations", record_groups)
    assert not tuple(row_solver._style_span_candidates(data, engine, fixed_face_rebar=face))
    assert widths == [axes]
    assert len(overrides) == builds
    assert all(value is face for value in overrides)
    assert bool(face_options) is not independent
