import pytest
from types import SimpleNamespace

from src.design import row_solver
from src.design.detailing import PhysicalRow
from dataclasses import replace
from tests.unit.test_feedback1026_parent_solver import _data, _engine


def test_parent_line_keeps_complete_path_when_later_search_exhausts(monkeypatch):
    engine = _engine()
    engine.coordination_solver_revision_id = row_solver.COORDINATION_SOLVER_REVISION
    inputs = (_data("A", 700, 0, 5000, parent=False),
              _data("B", 700, 0, 5000, parent=False))
    initial = tuple(engine.design(data, prepare_coordination_pool=False) for data in inputs)
    calls = []

    def candidates(data, _engine, **kwargs):
        calls.append((data.section.name, kwargs.get("left_rows")))
        yield initial[0 if data.section.name == "A" else 1]
        raise row_solver._CoordinationSearchLimit("ROW_SEGMENT_SEARCH_LIMIT: test")

    monkeypatch.setattr(row_solver, "span_candidates", candidates)
    monkeypatch.setattr(row_solver, "_parent_candidate_blockers", lambda _candidate: set())
    result = row_solver.coordinate_parent_line(initial, ((0,), (1,)),
                                               engine.project.default_continuity_option, engine)
    assert len(result) == 2
    assert calls[1][1] is not None
    assert all(item.coordination_status == "FEASIBLE_OPTIMIZATION_INCOMPLETE" for item in result)
    assert all(item.coordination_audit[-1]["search_complete"] is False for item in result)


def test_parent_line_without_complete_path_propagates_budget(monkeypatch):
    engine = _engine()
    engine.coordination_solver_revision_id = row_solver.COORDINATION_SOLVER_REVISION
    data = _data("A", 700, 0, 5000, parent=False)
    initial = (engine.design(data, prepare_coordination_pool=False),)

    def candidates(*_args, **_kwargs):
        raise row_solver._CoordinationSearchLimit("ROW_SEGMENT_SEARCH_LIMIT: test")
        yield

    monkeypatch.setattr(row_solver, "span_candidates", candidates)
    with pytest.raises(row_solver._CoordinationSearchLimit):
        row_solver.coordinate_parent_line(initial, ((0,),), engine.project.default_continuity_option, engine)


@pytest.mark.parametrize("matched", [True, False])
def test_style_segment_keeps_complete_path_and_requests_constrained_pool(monkeypatch, matched):
    engine = _engine()
    engine.coordination_solver_revision_id = row_solver.COORDINATION_SOLVER_REVISION
    inputs = (_data("A", 700, 0, 5000, parent=False),
              _data("B", 700, 0, 5000, parent=False))
    initial = tuple(engine.design(data, prepare_coordination_pool=False) for data in inputs)
    calls = []

    def candidates(data, _engine, **kwargs):
        calls.append(kwargs)
        yield initial[0 if data.section.name == "A" else 1]
        raise row_solver._CoordinationSearchLimit("ROW_SEGMENT_SEARCH_LIMIT: test")

    library = engine.offline_style_library
    audit_reasons = []
    monkeypatch.setattr(library, "applicable_fields", lambda *_: {"top": ({"rule_id": "R"},)})
    monkeypatch.setattr(library, "matched_fields", lambda *_: (SimpleNamespace(rule_id="R"),) if matched else ())
    def audit(*_args, **kwargs):
        audit_reasons.append(kwargs["fallback_reason"])
        return {}
    monkeypatch.setattr(library, "audit", audit)
    monkeypatch.setattr(row_solver, "_style_path_rank", lambda *_: (0, 0))
    monkeypatch.setattr(row_solver, "finalize_rows", lambda result, _engine: result)
    monkeypatch.setattr(row_solver, "span_candidates", candidates)
    monkeypatch.setattr(row_solver, "_legacy_coordinate_segment", lambda *_args, **_kwargs: pytest.fail("must not restart search"))
    results = row_solver.coordinate_segment(initial, engine.project.default_continuity_option, engine)
    assert calls[1]["left_rows"] is not None and calls[1]["coordination_pool"]
    assert all(result.coordination_status == "FEASIBLE_OPTIMIZATION_INCOMPLETE" for result in results)
    assert all(result.coordination_audit[-1]["search_complete"] is False for result in results)
    assert audit_reasons == ([None, None] if matched else ["NO_FEASIBLE_SUPPORTED_PATTERN"] * 2)


def test_span_keeps_checked_candidate_when_later_optimization_exhausts(monkeypatch):
    engine = _engine()
    data = _data("A", 700, 0, 5000, parent=False)
    checked = engine.design(data, prepare_coordination_pool=False)
    engine.coordination_solver_revision_id = row_solver.COORDINATION_SOLVER_REVISION
    library = engine.offline_style_library
    monkeypatch.setattr(library, "applicable_fields", lambda *_: {"top": ({"rule_id": "R"},)})
    monkeypatch.setattr(library, "matched_fields", lambda *_: (SimpleNamespace(rule_id="R"),))
    monkeypatch.setattr(library, "non_hoop_matches", lambda *_: (SimpleNamespace(rule_id="R"),))
    monkeypatch.setattr(library, "rank_key", lambda *_: (0,))

    def candidates(*_args, **_kwargs):
        yield checked
        raise row_solver._CoordinationSearchLimit("ROW_SEGMENT_SEARCH_LIMIT: test")

    monkeypatch.setattr(row_solver, "_style_span_candidates", candidates)
    result = row_solver.design_span(data, engine, prepare_coordination_pool=False)
    assert result.coordination_status == "FEASIBLE_OPTIMIZATION_INCOMPLETE"
    assert result.coordination_audit[-1]["termination_stage"] == "SPAN_CANDIDATE_GENERATION"


def test_parent_requests_distinguish_support_states_and_do_not_cache_incomplete(monkeypatch):
    engine = _engine()
    inputs = (_data("A", 700, 0, 5000),
              _data("B", 700, 5000, 2000))
    initial = tuple(engine.design(data, prepare_coordination_pool=False) for data in inputs)
    engine.coordination_solver_revision_id = row_solver.COORDINATION_SOLVER_REVISION
    monkeypatch.setattr(type(engine.project), "ordered_longitudinal_diameters_for", lambda *_: (20,))
    monkeypatch.setattr(type(engine.project), "ordered_stirrup_diameters", lambda *_: (10,))
    monkeypatch.setattr(type(engine.project), "allowed_spacings_for", lambda *_: (150,))
    monkeypatch.setattr(type(engine.project), "preferred_stirrup_legs", lambda *_: 2)
    monkeypatch.setattr(engine.code, "minimum_legs", lambda *_: 2)
    monkeypatch.setattr(engine, "_capacity_per_layer", lambda *_: 2)
    monkeypatch.setattr(row_solver, "_parent_candidate_blockers", lambda _result: set())
    monkeypatch.setattr(row_solver, "_style_hoop_states", lambda *_: ())
    hoops = tuple(region.stirrup for region in initial[0].regions)
    monkeypatch.setattr(row_solver, "_parent_hoop_options", lambda *_args, **_kwargs: (hoops,))
    monkeypatch.setattr(row_solver, "_parent_bottom_proposals", lambda *_: (((2, 20, "main"),),))

    def with_right(rows):
        result = initial[0]
        end = replace(result.regions[-1], top=replace(result.regions[-1].top, rows=rows))
        return replace(result, regions=(*result.regions[:-1], end))

    a = (PhysicalRow(2, 20), PhysicalRow(2, 16))
    b = (PhysicalRow(2, 20), PhysicalRow(2, 20))
    calls = []
    interrupted = [False]

    def candidates(data, _engine, **kwargs):
        if data.section.name == "A":
            yield with_right(a)
            yield with_right(b)
            yield with_right(a)
        else:
            calls.append((tuple(d for _, d in kwargs["left_rows"]), kwargs["support_exact"]))
            if interrupted[0]:
                raise row_solver._CoordinationSearchLimit("ROW_SEGMENT_SEARCH_LIMIT: test")

    monkeypatch.setattr(row_solver, "span_candidates", candidates)
    groups = ((0, 1),)
    option = engine.project.default_continuity_option
    with pytest.raises(row_solver.DetailingError, match="PARENT_PROPOSAL_SEARCH_INCOMPLETE"):
        row_solver.coordinate_parent_line(initial, groups, option, engine)
    assert calls == [((20, 16), True), ((20, 20), True)]

    calls.clear()
    interrupted[0] = True
    with pytest.raises(row_solver._CoordinationSearchLimit, match="ROW_SEGMENT_SEARCH_LIMIT"):
        row_solver.coordinate_parent_line(initial, groups, option, engine)
    assert calls == [((20, 16), True)]
