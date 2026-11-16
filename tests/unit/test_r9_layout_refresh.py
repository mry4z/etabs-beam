"""R9 layout refresh reuses values only after all normal checks."""
from dataclasses import replace
import pytest

from src.design.detailing import DetailingError, _refresh_result_physical_layout
from src.design.row_solver import COORDINATION_SOLVER_REVISION, INDEXED_COORDINATION_SOLVER_REVISION
from tests.unit.test_feedback1026_parent_solver import _data, _engine


def _result():
    engine = _engine()
    return engine, engine.design(_data('A', 700, 0, 5000, parent=False), prepare_coordination_pool=False)


def test_r9_equal_resolved_layouts_reuse_regions_and_result(monkeypatch):
    engine, result = _result()
    engine.coordination_solver_revision_id = INDEXED_COORDINATION_SOLVER_REVISION
    calls = []
    def resolve(_section, _hoop, design, *, face):
        calls.append(face)
        return replace(design)  # equal value, different instance
    monkeypatch.setattr(engine, '_resolve_physical_layout', resolve)
    checked = _refresh_result_physical_layout(result, engine)
    assert calls == ['top', 'bottom'] * 3
    assert checked is result


def test_r9_changed_layout_replaces_only_affected_region(monkeypatch):
    engine, result = _result()
    engine.coordination_solver_revision_id = INDEXED_COORDINATION_SOLVER_REVISION
    target = result.regions[0].top
    calls = []
    def resolve(_section, _hoop, design, *, face):
        calls.append(face)
        return replace(design, effective_depth_mm=design.effective_depth_mm - 1) if len(calls) == 1 else design
    monkeypatch.setattr(engine, '_resolve_physical_layout', resolve)
    checked = _refresh_result_physical_layout(result, engine)
    assert checked is not result and checked.regions[0] is not result.regions[0]
    assert checked.regions[0].top.effective_depth_mm == target.effective_depth_mm - 1
    assert checked.regions[1] is result.regions[1] and checked.regions[2] is result.regions[2]


def test_r9_noop_still_checks_collisions_and_r8_keeps_copy(monkeypatch):
    engine, result = _result()
    engine.coordination_solver_revision_id = INDEXED_COORDINATION_SOLVER_REVISION
    calls = []
    def resolve(_section, _hoop, design, *, face):
        calls.append(face)
        return design
    monkeypatch.setattr(engine, '_resolve_physical_layout', resolve)
    monkeypatch.setattr(engine.code, 'minimum_clear_spacing', lambda *_: 10000)
    with pytest.raises(DetailingError, match='TOP_BOTTOM_ROWS_COLLIDE'):
        _refresh_result_physical_layout(result, engine)
    assert calls == ['top', 'bottom'] * 3
    monkeypatch.undo()
    engine.coordination_solver_revision_id = COORDINATION_SOLVER_REVISION
    monkeypatch.setattr(engine, '_resolve_physical_layout', lambda _s, _h, design, *, face: design)
    monkeypatch.setattr(engine.code, 'minimum_clear_spacing', lambda *_: 25)
    copied = _refresh_result_physical_layout(result, engine)
    assert copied == result and copied is not result
    assert all(a is not b for a, b in zip(copied.regions, result.regions))
