"""Exact per-region hoop choices and cost comparisons for R9."""
from dataclasses import replace
import pytest
from src.design.detailing import DetailingError
from src.design import row_solver
from tests.unit.test_feedback1026_parent_solver import _data, _engine


def test_ordered_region_choices_match_and_cache_only_complete_exact_context(monkeypatch):
    engine = _engine()
    data = _data('A', 700, 0, 5000, parent=False)
    result = engine.design(data, prepare_coordination_pool=False)
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    engine.etabs_design_results_only = True
    engine._r7_span_caches_enabled = True
    section, region, demand = data.section, result.regions[0], data.regions[0]
    expected = tuple(row_solver._compute_fit_region_hoop_choices(section, demand, region, engine))
    assert expected
    calls = []
    compute = row_solver._compute_fit_region_hoop_choices
    def counted(*args, **kwargs):
        calls.append(1)
        return compute(*args, **kwargs)
    monkeypatch.setattr(row_solver, '_compute_fit_region_hoop_choices', counted)
    with engine._r7_span_cache_scope(data):
        assert row_solver._fit_region_hoop_choices(section, demand, region, engine) == expected
        assert row_solver._fit_region_hoop_choices(section, demand, region, engine) == expected
        assert len(calls) == 1
        changed_demand = replace(demand, av_over_s_mm2_per_mm=demand.av_over_s_mm2_per_mm + 0.01)
        row_solver._fit_region_hoop_choices(section, changed_demand, region, engine)
        changed_hoop = replace(region, stirrup=replace(region.stirrup, spacing_mm=region.stirrup.spacing_mm - 10))
        row_solver._fit_region_hoop_choices(section, demand, changed_hoop, engine)
        changed_depth = replace(region, top=replace(region.top, effective_depth_mm=region.top.effective_depth_mm - 10))
        row_solver._fit_region_hoop_choices(section, demand, changed_depth, engine)
        changed_section = replace(section, width_mm=section.width_mm + 10)
        row_solver._fit_region_hoop_choices(changed_section, demand, region, engine)
        changed_name = replace(region, name='right')
        row_solver._fit_region_hoop_choices(section, demand, changed_name, engine)
        def larger(face):
            return replace(face, rows=tuple(replace(row, diameter_mm=row.diameter_mm + 1) for row in face.rows))
        changed_db = replace(region, top=larger(region.top), bottom=larger(region.bottom))
        row_solver._fit_region_hoop_choices(section, demand, changed_db, engine)
        assert len(calls) == 7
    # Only complete nonempty enumerations are cached.
    with engine._r7_span_cache_scope(data):
        monkeypatch.setattr(row_solver, '_compute_fit_region_hoop_choices', lambda *_: [])
        assert row_solver._fit_region_hoop_choices(section, demand, region, engine) == []
        assert not engine._r7_span_caches['indexed_fit_region_choices']
        def fail(*_):
            raise DetailingError('TEST_INCOMPLETE')
        monkeypatch.setattr(row_solver, '_compute_fit_region_hoop_choices', fail)
        with pytest.raises(DetailingError, match='TEST_INCOMPLETE'):
            row_solver._fit_region_hoop_choices(section, demand, region, engine)
        assert not engine._r7_span_caches['indexed_fit_region_choices']


def test_leg_override_cost_exact_for_explicit_inferred_and_fallback_geometry():
    engine = _engine()
    data = _data('A', 700, 0, 5000, parent=False)
    result = engine.design(data, prepare_coordination_pool=False)
    region, section = result.regions[0], data.section
    inferred = replace(section, clear_cover_to_stirrup_outer_mm=None, etabs_longitudinal_cover_mm=40)
    narrow = replace(section, width_mm=50)
    for active_section, context in ((section, region), (inferred, region), (inferred, None), (narrow, region)):
        for zone in ('left', 'middle', 'right'):
            for legs in (0, 2, 4, 6, 8, 12):
                original = row_solver._economic_hoop_cost(active_section, zone,
                    replace(region.stirrup, legs=legs), region=context)
                optimized = row_solver._economic_hoop_cost(active_section, zone,
                    region.stirrup, region=context, legs_override=legs)
                assert optimized == original
