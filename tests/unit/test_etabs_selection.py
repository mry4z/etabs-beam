from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from src.core.etabs_selection import EtabsSelectionSnapshot


class _SelectionSap:
    def __init__(self, selected=(), *, previous_selection=(), previous_result=0, model_path=None):
        self.selected = set(selected)
        self.previous_selection = set(previous_selection)
        self.previous_result = previous_result
        self.previous_calls = 0
        self.clear_calls = 0
        self.set_selected_calls = 0
        self.model_path = model_path
        self.SelectObj = SimpleNamespace(
            GetSelected=lambda *args: (
                0,
                len(self.selected),
                [kind for kind, _ in sorted(self.selected)],
                [name for _, name in sorted(self.selected)],
            ),
            PreviousSelection=self._previous_selection,
            ClearSelection=self._clear_selection,
        )
        for kind, api_name in {
            1: "PointObj",
            2: "FrameObj",
            3: "CableObj",
            4: "TendonObj",
            5: "AreaObj",
            6: "SolidObj",
            7: "LinkObj",
        }.items():
            setattr(self, api_name, SimpleNamespace(SetSelected=self._set_selected(kind)))
        if model_path is not None:
            self.GetModelFilename = lambda *args: self.model_path

    def _previous_selection(self):
        self.previous_calls += 1
        if self.previous_result == 0:
            self.selected = set(self.previous_selection)
        return self.previous_result

    def _clear_selection(self):
        self.clear_calls += 1
        self.selected.clear()
        return 0

    def _set_selected(self, kind):
        def set_selected(name, selected, item_type):
            self.set_selected_calls += 1
            if selected:
                self.selected.add((kind, name))
            return 0

        return set_selected


def _install_etabs(monkeypatch):
    monkeypatch.setitem(sys.modules, "ETABSv1", SimpleNamespace(eItemType=SimpleNamespace(Objects="Objects")))


def test_previous_selection_is_one_checked_batch_call_for_mixed_selection(monkeypatch):
    _install_etabs(monkeypatch)
    expected = {(1, "P1"), (2, "B1"), (4, "T1"), (5, "A1"), (7, "L1")}
    sap = _SelectionSap(expected, previous_selection=expected)
    snapshot = EtabsSelectionSnapshot.capture(sap)
    sap.selected.clear()

    result = snapshot.restore_with_audit(sap)

    assert result.verified and result.strategy == "previous_selection"
    assert result.previous_selection_return_code == 0
    assert result.previous_selection_verified is True
    assert sap.previous_calls == 1
    assert sap.clear_calls == 0 and sap.set_selected_calls == 0
    assert result.actual == tuple(sorted(expected))


def test_no_restore_needed_does_not_touch_selection_history():
    expected = {(2, "B1")}
    sap = _SelectionSap(expected, previous_selection={(2, "other")})
    snapshot = EtabsSelectionSnapshot.capture(sap)

    result = snapshot.restore_with_audit(sap)

    assert result.verified and result.strategy == "noop"
    assert sap.previous_calls == sap.clear_calls == sap.set_selected_calls == 0


@pytest.mark.parametrize("previous_result", [7, -1])
def test_previous_selection_nonzero_falls_back_to_exact_snapshot(monkeypatch, previous_result):
    _install_etabs(monkeypatch)
    expected = {(1, "P1"), (2, "B1"), (5, "A1")}
    sap = _SelectionSap(set(), previous_selection=expected, previous_result=previous_result)
    snapshot = EtabsSelectionSnapshot.capture(_SelectionSap(expected))

    result = snapshot.restore_with_audit(sap)

    assert result.verified and result.strategy == "object_fallback"
    assert result.previous_selection_return_code == previous_result
    assert result.previous_selection_verified is None
    assert result.set_selected_calls == len(expected)
    assert sap.selected == expected
    assert any("PreviousSelection" in item and str(previous_result) in item for item in result.diagnostics)


def test_previous_selection_success_but_wrong_complete_set_falls_back(monkeypatch):
    _install_etabs(monkeypatch)
    expected = {(1, "P1"), (2, "B1"), (5, "A1")}
    sap = _SelectionSap(
        set(),
        previous_selection={(2, "wrong"), (5, "A1")},
        previous_result=0,
    )
    snapshot = EtabsSelectionSnapshot.capture(_SelectionSap(expected))

    result = snapshot.restore_with_audit(sap)

    assert result.verified and result.strategy == "object_fallback"
    assert result.previous_selection_return_code == 0
    assert result.previous_selection_verified is False
    assert result.set_selected_calls == len(expected)
    assert sap.selected == expected
    assert any(item.startswith("SELECTION_RESTORE_PREVIOUS_MISMATCH") for item in result.diagnostics)


def test_empty_snapshot_is_verified_after_wrong_previous_selection(monkeypatch):
    _install_etabs(monkeypatch)
    sap = _SelectionSap({(2, "wrong")}, previous_selection={(2, "wrong")})
    snapshot = EtabsSelectionSnapshot.capture(_SelectionSap(set()))

    result = snapshot.restore_with_audit(sap)

    assert result.verified and result.strategy == "object_fallback"
    assert result.previous_selection_verified is False
    assert result.set_selected_calls == 0
    assert result.actual == () and sap.selected == set()


def test_fallback_reports_failure_when_a_set_selected_call_is_not_verified(monkeypatch):
    _install_etabs(monkeypatch)
    expected = {(2, "B1"), (5, "A1")}
    sap = _SelectionSap(set(), previous_selection={(2, "wrong")})
    sap.FrameObj.SetSelected = lambda *args: 9
    snapshot = EtabsSelectionSnapshot.capture(_SelectionSap(expected))

    result = snapshot.restore_with_audit(sap)

    assert not result.verified
    assert result.strategy == "object_fallback"
    assert any(item.startswith("SELECTION_RESTORE_OBJECT_FAILED") for item in result.diagnostics)
    assert any(item.startswith("SELECTION_RESTORE_VERIFY_FAILED") for item in result.diagnostics)


def test_post_fallback_get_selected_read_failure_is_not_treated_as_empty_success(monkeypatch):
    _install_etabs(monkeypatch)
    sap = _SelectionSap({(2, "wrong")}, previous_selection={(2, "wrong")})
    snapshot = EtabsSelectionSnapshot.capture(_SelectionSap(set()))
    original_get_selected = sap.SelectObj.GetSelected
    reads = 0

    def get_selected(*args):
        nonlocal reads
        reads += 1
        if reads >= 3:
            raise RuntimeError("GetSelected unavailable")
        return original_get_selected(*args)

    sap.SelectObj.GetSelected = get_selected
    result = snapshot.restore_with_audit(sap)

    assert not result.verified
    assert any(item.startswith("SELECTION_RESTORE_VERIFY_READ_FAILED") for item in result.diagnostics)


def test_model_identity_change_fails_closed_before_selection_mutation():
    expected = {(2, "B1")}
    sap = _SelectionSap(expected, model_path="other.EDB")
    snapshot = EtabsSelectionSnapshot.capture(_SelectionSap(expected, model_path="model.EDB"))

    with pytest.raises(Exception, match="SELECTION_RESTORE_MODEL_CHANGED"):
        snapshot.restore_with_audit(sap)

    assert sap.previous_calls == sap.clear_calls == sap.set_selected_calls == 0


def test_model_identity_read_failure_fails_closed_before_selection_mutation():
    expected = {(2, "B1")}
    sap = _SelectionSap(expected, model_path="model.EDB")
    snapshot = EtabsSelectionSnapshot.capture(
        _SelectionSap(expected, model_path="model.EDB")
    )
    sap.GetModelFilename = lambda *args: (_ for _ in ()).throw(RuntimeError("identity unavailable"))

    with pytest.raises(Exception, match="SELECTION_RESTORE_MODEL_IDENTITY_READ_FAILED"):
        snapshot.restore_with_audit(sap)

    assert sap.previous_calls == sap.clear_calls == sap.set_selected_calls == 0


def test_model_change_during_previous_selection_aborts_without_object_fallback(monkeypatch):
    _install_etabs(monkeypatch)
    expected = {(1, "P1"), (2, "B1"), (5, "A1")}
    sap = _SelectionSap(
        set(), previous_selection={(2, "wrong")}, model_path="model.EDB"
    )
    snapshot = EtabsSelectionSnapshot.capture(
        _SelectionSap(expected, model_path="model.EDB")
    )

    def previous_selection_changes_model():
        sap.previous_calls += 1
        sap.model_path = "other.EDB"
        sap.selected = {(2, "OTHER")}
        return 0

    sap.SelectObj.PreviousSelection = previous_selection_changes_model

    with pytest.raises(Exception, match="SELECTION_RESTORE_MODEL_CHANGED"):
        snapshot.restore_with_audit(sap)

    assert sap.previous_calls == 1
    assert sap.clear_calls == sap.set_selected_calls == 0
    assert sap.selected == {(2, "OTHER")}


def test_model_change_after_first_object_restore_aborts_before_next_object(monkeypatch):
    _install_etabs(monkeypatch)
    expected = {(1, "P1"), (2, "B1")}
    sap = _SelectionSap(
        set(), previous_selection={(2, "wrong")}, model_path="model.EDB"
    )
    snapshot = EtabsSelectionSnapshot.capture(
        _SelectionSap(expected, model_path="model.EDB")
    )

    def first_object_changes_model(name, selected, item_type):
        sap.set_selected_calls += 1
        sap.selected.add((1, name))
        sap.model_path = "other.EDB"
        return 0

    sap.PointObj.SetSelected = first_object_changes_model

    with pytest.raises(Exception, match="SELECTION_RESTORE_MODEL_CHANGED"):
        snapshot.restore_with_audit(sap)

    assert sap.clear_calls == 1
    assert sap.set_selected_calls == 1
    assert sap.selected == {(1, "P1")}


def test_large_selection_uses_constant_size_normal_batch_path(monkeypatch):
    _install_etabs(monkeypatch)
    expected = {(2, f"B{index}") for index in range(3919)}
    sap = _SelectionSap(set(), previous_selection=expected)
    snapshot = EtabsSelectionSnapshot.capture(_SelectionSap(expected))

    result = snapshot.restore_with_audit(sap)

    assert result.verified and result.strategy == "previous_selection"
    assert sap.previous_calls == 1
    assert sap.clear_calls == sap.set_selected_calls == 0
