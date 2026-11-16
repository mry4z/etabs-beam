from dataclasses import replace

import pytest
from src.design import row_solver
from src.design.detailing import BeamDetailingProfile, DetailingError
from src.design.hoop_supply import HoopSupplyTable
from tests.unit.test_feedback1026_parent_solver import _data, _engine


@pytest.mark.parametrize('profile,role', [
    (BeamDetailingProfile.SB_ORDINARY_NO_CH18, 'SB'),
    (BeamDetailingProfile.SMF, 'PB'),
])
def test_supply_trials_match_original_for_strength_spacing_and_depth(profile, role):
    engine = _engine()
    data = _data('A', 700, 0, 5000, parent=False)
    section = replace(data.section, detailing_profile=profile, beam_role=role)
    diameters = engine.project.ordered_stirrup_diameters()
    spacings = tuple(dict.fromkeys(s for r in data.regions for s in engine.project.allowed_spacings_for(section, r.name)))
    table = HoopSupplyTable(diameters, spacings, engine.project.catalog)
    for demand in data.regions:
        for diameter in diameters:
            for spacing in spacings:
                at = engine.project.catalog.area(diameter) / spacing
                for torsion in (0.0, max(0.0, at - 0.5e-9), at + 1.5e-9):
                    active = replace(demand, at_over_s_mm2_per_mm=torsion)
                    for legs in (2, 4, 6, 8, 10):
                        for depth in (350.0, 620.0):
                            kwargs = dict(max_main=20, effective_depth=depth)
                            old = row_solver._economic_hoop_trial(section, active, engine, diameter, spacing, legs, **kwargs)
                            new = row_solver._indexed_supply_hoop_trial(section, active, engine, diameter, spacing, legs,
                                supply_table=table, **kwargs)
                            assert new == old


def test_supply_spacing_and_shear_exact_boundaries():
    engine = _engine()
    data = _data('A', 700, 0, 5000, parent=False)
    section, demand = data.section, data.regions[0]
    diameter = engine.project.ordered_stirrup_diameters()[0]
    maximum = row_solver._economic_hoop_spacing_limits(section, demand, engine, diameter,
        max_main=20, effective_depth=400)[0]
    spacings = (maximum - 0.5e-9, maximum + 0.5e-9, maximum + 1.5e-9)
    table = HoopSupplyTable((diameter,), spacings, engine.project.catalog)
    for spacing in spacings:
        area = engine.project.catalog.area(diameter)
        for delta in (-1.5e-9, -0.5e-9, 0, 0.5e-9, 1.5e-9):
            # Exercise the original ceil boundary and max-spacing comparison.
            active = replace(demand, av_over_s_mm2_per_mm=max(0, 8 * area / spacing + delta))
            for legs in (6, 8, 10):
                kwargs = dict(max_main=20, effective_depth=400)
                assert row_solver._indexed_supply_hoop_trial(section, active, engine, diameter, spacing, legs,
                    supply_table=table, **kwargs) == row_solver._economic_hoop_trial(
                        section, active, engine, diameter, spacing, legs, **kwargs)


def test_supply_table_does_not_truncate_high_leg_count():
    engine = _engine()
    diameter = engine.project.ordered_stirrup_diameters()[0]
    spacing = engine.project.allowed_spacings_for(_data('A', 700, 0, 5000, parent=False).section, 'left')[0]
    table = HoopSupplyTable((diameter,), (spacing,), engine.project.catalog)
    area = engine.project.catalog.area(diameter)
    assert table.get(diameter, spacing, 12).provided_av_over_s == 12 * area / spacing


@pytest.mark.parametrize('profile,role', [
    (BeamDetailingProfile.SB_ORDINARY_NO_CH18, 'SB'),
    (BeamDetailingProfile.SMF, 'PB'),
])
def test_full_economic_states_equal_original_and_use_table(profile, role, monkeypatch):
    engine = _engine()
    original_data = _data('A', 700, 0, 5000, parent=False)
    original_result = engine.design(original_data, prepare_coordination_pool=False)
    section = replace(original_data.section, detailing_profile=profile, beam_role=role)
    data = replace(original_data, section=section)
    result = replace(original_result, section=section, input_data=data)
    engine.etabs_design_results_only = True
    engine._r7_span_caches_enabled = True
    calls = []
    source = row_solver._indexed_supply_hoop_trial
    def counted(*args, **kwargs):
        calls.append(1)
        return source(*args, **kwargs)
    monkeypatch.setattr(row_solver, '_indexed_supply_hoop_trial', counted)
    engine.coordination_solver_revision_id = row_solver.COORDINATION_SOLVER_REVISION
    with engine._r7_span_cache_scope(data):
        expected = row_solver._uncached_economic_hoop_states(data, engine, result, trial_cache={})
    assert not calls
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    with engine._r7_span_cache_scope(data):
        actual = row_solver._uncached_economic_hoop_states(data, engine, result, trial_cache={})
    assert actual == expected
    if profile is BeamDetailingProfile.SMF:
        assert calls
    else:
        assert not calls  # ordinary-SB demand table avoids per-trial construction


def test_supply_trial_reuses_successes_and_none_failures(monkeypatch):
    engine = _engine()
    data = _data('A', 700, 0, 5000, parent=False)
    result = engine.design(data, prepare_coordination_pool=False)
    section = replace(data.section, detailing_profile=BeamDetailingProfile.SMF, beam_role='SB')
    data = replace(data, section=section, regions=tuple(replace(demand, at_over_s_mm2_per_mm=0.5) for demand in data.regions))
    result = replace(result, section=section, input_data=data)
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    engine.etabs_design_results_only = True
    engine._r7_span_caches_enabled = True
    original = row_solver._indexed_supply_hoop_trial
    values = []
    def counted(*args, **kwargs):
        value = original(*args, **kwargs)
        values.append(value)
        return value
    monkeypatch.setattr(row_solver, '_indexed_supply_hoop_trial', counted)
    with engine._r7_span_cache_scope(data):
        first = row_solver._uncached_economic_hoop_states(data, engine, result, trial_cache={})
        count = len(values)
        assert count and any(value is None for value in values) and any(value is not None for value in values)
        second = row_solver._uncached_economic_hoop_states(data, engine, result, trial_cache={})
        assert len(values) == count
    assert first == second


def _run_economic_with_revision(engine, data, result, revision):
    engine.coordination_solver_revision_id = revision
    try:
        with engine._r7_span_cache_scope(data):
            value = row_solver._uncached_economic_hoop_states(data, engine, result, trial_cache={})
        return ('ok', value)
    except Exception as exc:
        return ('error', type(exc), str(exc))


@pytest.mark.parametrize('profile', [BeamDetailingProfile.SB_ORDINARY_NO_CH18, BeamDetailingProfile.NON_SWAY])
def test_ordinary_sb_complete_tuple_matches_original_across_actual_demands(profile):
    engine = _engine()
    original_data = _data('A', 700, 0, 5000, parent=False)
    original_result = engine.design(original_data, prepare_coordination_pool=False)
    section = replace(original_data.section, detailing_profile=profile, beam_role='SB')
    engine.etabs_design_results_only = True
    engine._r7_span_caches_enabled = True
    observed_over_six = False
    for at in (0.0, 0.5, 2.0):
        for av in (0.18, 40.0):
            demands = tuple(replace(demand, at_over_s_mm2_per_mm=at, av_over_s_mm2_per_mm=av)
                            for demand in original_data.regions)
            data = replace(original_data, section=section, regions=demands)
            for depth in (350.0, 620.0):
                for count in (4, 12):
                    def face(face):
                        rows = (replace(face.rows[0], count=count, credited_area_mm2=None), *face.rows[1:])
                        return replace(face, rows=rows, effective_depth_mm=depth)
                    regions = tuple(replace(region,
                        top=replace(face(region.top), effective_depth_mm=depth + index * 10),
                        bottom=replace(face(region.bottom), effective_depth_mm=depth + index * 10))
                        for index, region in enumerate(original_result.regions))
                    result = replace(original_result, section=section, input_data=data, regions=regions)
                    old = _run_economic_with_revision(engine, data, result, row_solver.COORDINATION_SOLVER_REVISION)
                    new = _run_economic_with_revision(engine, data, result, row_solver.INDEXED_COORDINATION_SOLVER_REVISION)
                    assert new == old
                    if new[0] == 'ok' and new[1] and new[1][0][0].legs > 6:
                        observed_over_six = True
    assert observed_over_six


def test_sb_different_region_spacing_sets_and_none_result_match_original(monkeypatch):
    engine = _engine()
    data = _data('A', 700, 0, 5000, parent=False)
    result = engine.design(data, prepare_coordination_pool=False)
    engine.etabs_design_results_only = True
    engine._r7_span_caches_enabled = True
    policy = type(engine.project)
    original = policy.allowed_spacings_for
    def differing(self, section, name):
        values = original(self, section, name)
        return values if name == 'left' else values[::2] if name == 'middle' else values[1::2]
    monkeypatch.setattr(policy, 'allowed_spacings_for', differing)
    for active in (None, result):
        old = _run_economic_with_revision(engine, data, active, row_solver.COORDINATION_SOLVER_REVISION)
        new = _run_economic_with_revision(engine, data, active, row_solver.INDEXED_COORDINATION_SOLVER_REVISION)
        assert new == old
        assert old[0] == 'ok' and old[1]


def test_sb_spacing_exception_same_as_original_and_not_cached(monkeypatch):
    engine = _engine()
    data = _data('A', 700, 0, 5000, parent=False)
    result = engine.design(data, prepare_coordination_pool=False)
    engine.etabs_design_results_only = True
    engine._r7_span_caches_enabled = True
    def fail(*_args, **_kwargs):
        raise DetailingError('TEST_ACTUAL_DEPTH_EXCEPTION')
    monkeypatch.setattr(engine.code, 'maximum_stirrup_spacing', fail)
    old = _run_economic_with_revision(engine, data, result, row_solver.COORDINATION_SOLVER_REVISION)
    new = _run_economic_with_revision(engine, data, result, row_solver.INDEXED_COORDINATION_SOLVER_REVISION)
    assert old == new == ('error', DetailingError, 'TEST_ACTUAL_DEPTH_EXCEPTION')


def test_sb_min_first_row_support_key_is_exact():
    engine = _engine()
    data = _data('A', 700, 0, 5000, parent=False)
    result = engine.design(data, prepare_coordination_pool=False)
    region = result.regions[0]
    def first_count(face, count):
        return replace(face, rows=(replace(face.rows[0], count=count, credited_area_mm2=None), *face.rows[1:]))
    first = replace(result, regions=(replace(region, top=first_count(region.top, 4), bottom=first_count(region.bottom, 8)), *result.regions[1:]))
    second = replace(result, regions=(replace(region, top=first_count(region.top, 8), bottom=first_count(region.bottom, 4)), *result.regions[1:]))
    third = replace(result, regions=(replace(region, top=first_count(region.top, 8), bottom=first_count(region.bottom, 6)), *result.regions[1:]))
    assert row_solver._indexed_economic_hoop_key(data, engine, first) == row_solver._indexed_economic_hoop_key(data, engine, second)
    assert row_solver._indexed_economic_hoop_key(data, engine, first) != row_solver._indexed_economic_hoop_key(data, engine, third)
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    engine.etabs_design_results_only = True
    engine._r7_span_caches_enabled = True
    with engine._r7_span_cache_scope(data):
        one = row_solver._economic_hoop_states(data, engine, first, trial_cache={})
        assert row_solver._economic_hoop_states(data, engine, second, trial_cache={}) == one
        assert engine._r7_cache_statistics['economic_hoops_hits'] >= 1
        assert row_solver._uncached_economic_hoop_states(data, engine, second, trial_cache={}) == one


@pytest.mark.parametrize('offset', [-1.5e-9, -0.5e-9, 0, 0.5e-9, 1.5e-9])
def test_ordinary_sb_complete_state_spacing_and_torsion_boundary(offset, monkeypatch):
    engine = _engine()
    original = _data('A', 700, 0, 5000, parent=False)
    result = engine.design(original, prepare_coordination_pool=False)
    engine.etabs_design_results_only = True
    engine._r7_span_caches_enabled = True
    diameter = engine.project.ordered_stirrup_diameters()[0]
    area = engine.project.catalog.area(diameter)
    demands = tuple(replace(demand, at_over_s_mm2_per_mm=area / 100 + offset)
                    for demand in original.regions)
    data = replace(original, regions=demands)
    result = replace(result, input_data=data)
    maximum = 100 + offset
    monkeypatch.setattr(engine.code, 'maximum_stirrup_spacing',
        lambda *_args, **_kwargs: (maximum, 'BOUNDARY', (('RULE', maximum),), False))
    old = _run_economic_with_revision(engine, data, result, row_solver.COORDINATION_SOLVER_REVISION)
    new = _run_economic_with_revision(engine, data, result, row_solver.INDEXED_COORDINATION_SOLVER_REVISION)
    assert new == old


def test_sb_empty_spacing_catalog_matches_original(monkeypatch):
    engine = _engine()
    data = _data('A', 700, 0, 5000, parent=False)
    result = engine.design(data, prepare_coordination_pool=False)
    engine.etabs_design_results_only = True
    engine._r7_span_caches_enabled = True
    monkeypatch.setattr(type(engine.project), 'allowed_spacings_for', lambda *_: ())
    old = _run_economic_with_revision(engine, data, result, row_solver.COORDINATION_SOLVER_REVISION)
    new = _run_economic_with_revision(engine, data, result, row_solver.INDEXED_COORDINATION_SOLVER_REVISION)
    assert old == new == ('ok', ())


def test_sb_empty_first_region_does_not_evaluate_later_regions(monkeypatch):
    engine = _engine()
    original = _data('A', 700, 0, 5000, parent=False)
    result = engine.design(original, prepare_coordination_pool=False)
    demands = (replace(original.regions[0], at_over_s_mm2_per_mm=100.0), *original.regions[1:])
    data = replace(original, regions=demands)
    result = replace(result, input_data=data)
    engine.etabs_design_results_only = True
    engine._r7_span_caches_enabled = True
    def fail(*_args, **_kwargs):
        raise DetailingError('MUST_NOT_EVALUATE_DEPTH')
    monkeypatch.setattr(engine.code, 'maximum_stirrup_spacing', fail)
    old = _run_economic_with_revision(engine, data, result, row_solver.COORDINATION_SOLVER_REVISION)
    new = _run_economic_with_revision(engine, data, result, row_solver.INDEXED_COORDINATION_SOLVER_REVISION)
    assert old == new == ('ok', ())


def test_sb_actual_spacing_rule_evaluated_once_per_region_and_diameter(monkeypatch):
    engine = _engine()
    data = _data('A', 700, 0, 5000, parent=False)
    result = engine.design(data, prepare_coordination_pool=False)
    engine.etabs_design_results_only = True
    engine._r7_span_caches_enabled = True
    engine.coordination_solver_revision_id = row_solver.INDEXED_COORDINATION_SOLVER_REVISION
    original = row_solver._economic_hoop_spacing_limits
    calls = []
    def counted(section, demand, active_engine, diameter, **kwargs):
        calls.append((demand.name, diameter, kwargs['max_main'], kwargs['effective_depth']))
        return original(section, demand, active_engine, diameter, **kwargs)
    monkeypatch.setattr(row_solver, '_economic_hoop_spacing_limits', counted)
    with engine._r7_span_cache_scope(data):
        result = row_solver._uncached_economic_hoop_states(data, engine, result, trial_cache={})
    assert result and len(calls) == len(set(calls))
