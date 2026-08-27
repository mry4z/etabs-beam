from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from src.core.etabs_tower import (
    EtabsTowerReader,
    TowerAssignmentApiError,
    _story_arrays,
    create_tower_review_groups,
)
from src.core.tower_assignment import TowerAssignmentReport, TowerGroupPlan, TowerObjectRecord


class _Tower:
    def __init__(self):
        self.active = "T1"

    def GetActiveTower(self, *args):
        return 0, self.active

    def GetNameList(self, *args):
        return 0, 2, ["T1", "T2"]

    def SetActiveTower(self, name):
        self.active = name
        return 0


class _Story:
    def GetStories_2(self, *args):
        return 0, 1, ["L1"], [0.0], [3000.0]


class _Tower23:
    """ETABS 23 public cTower shape: no GetNameList method."""

    def __init__(self):
        self.active = "T1"

    def GetActiveTower(self, *args):
        return 0, self.active

    def SetActiveTower(self, name):
        self.active = name
        return 0


class _EmptyStory(_Story):
    def GetStories_2(self, *args):
        return 0, 0.0, 1, [], [], [], [], [], [], [], []


class _IterableClrArray:
    """Small stand-in for pythonnet System.*[] values in unit tests."""

    def __init__(self, values):
        self._values = tuple(values)

    def __iter__(self):
        return iter(self._values)


def _table(fields, rows):
    flat = [value for row in rows for value in row]
    return (0, "", 1, tuple(fields), len(rows), flat)


class _DatabaseTables:
    tables = {
        "Story Definitions": (("Tower", "Story", "Height"), [("T1", "L1", "3000"), ("T2", "L1", "3000")]),
        "Point Object Connectivity": (
            ("UniqueName", "Tower", "Story", "X", "Y", "Z", "GUID"),
            [("P1", "T1", "L1", "0", "0", "0", "GP1"), ("P2", "T2", "L1", "5000", "0", "0", "GP2")],
        ),
        "Beam Object Connectivity": (
            ("UniqueName", "Story", "UniquePtI", "UniquePtJ", "GUID"),
            [("B1", "L1", "P1", "P2", "GB1")],
        ),
        "Column Object Connectivity": (
            ("UniqueName", "Story", "UniquePtI", "UniquePtJ", "GUID"),
            [("C1", "L1", "P1", "P1C", "GC1")],
        ),
    }

    def GetAvailableTables(self, *args):
        keys = list(self.tables)
        return 0, len(keys), keys, keys, [0] * len(keys)

    def GetTableForDisplayArray(self, key, *args):
        fields, rows = self.tables[key]
        return _table(fields, rows)


class _Sap:
    def __init__(self):
        self.Tower = _Tower()
        self.Story = _Story()
        self.DatabaseTables = _DatabaseTables()


def test_live_reader_restores_active_tower_and_reports_mixed_endpoints():
    sap = _Sap()
    report = EtabsTowerReader(sap).read_report(selection="all")
    assert report.status == "REVIEW_REQUIRED"
    assert any(record.status == "TOWER_MIXED_ENDPOINT_ASSIGNMENT" for record in report.records)
    assert report.active_tower_before == "T1"
    assert report.active_tower_after == "T1"
    assert sap.Tower.active == "T1"


def test_story_arrays_accept_pythonnet_style_iterable_arrays():
    values = (
        0,
        0.0,
        1,
        _IterableClrArray(("L1",)),
        _IterableClrArray((0.0,)),
        _IterableClrArray((3000.0,)),
        _IterableClrArray((True,)),
        _IterableClrArray(("",)),
        _IterableClrArray((False,)),
        _IterableClrArray((0.0,)),
        _IterableClrArray((0,)),
    )
    assert _story_arrays(values) == (("L1",), (3000.0,), (0.0,))


def test_etabs23_without_tower_name_list_uses_story_definition_table():
    sap = _Sap()
    sap.Tower = _Tower23()
    report = EtabsTowerReader(sap).read_report(selection="all")
    assert report.status == "REVIEW_REQUIRED"
    assert {item.tower for item in report.catalog} == {"T1", "T2"}
    assert not any(item.startswith("TOWER_NAME_LIST_READ_FAILED") for item in report.diagnostics)


def test_empty_story_api_falls_back_to_public_story_table():
    sap = _Sap()
    sap.Tower = _Tower23()
    sap.Story = _EmptyStory()
    report = EtabsTowerReader(sap).read_report(selection="all")
    assert report.status == "REVIEW_REQUIRED"
    assert {item.tower for item in report.catalog} == {"T1", "T2"}
    assert any(item.startswith("TOWER_STORY_API_FALLBACK") for item in report.diagnostics)


def test_story_api_failure_uses_public_table_when_available():
    class BrokenStory(_Story):
        def GetStories_2(self, *args):
            raise RuntimeError("story api unavailable")

    sap = _Sap()
    sap.Story = BrokenStory()
    report = EtabsTowerReader(sap).read_report(selection="all")
    assert report.status == "REVIEW_REQUIRED"
    assert any(item.startswith("TOWER_STORY_API_FALLBACK") for item in report.diagnostics)


def test_group_transaction_uses_guid_and_writes_ownership(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "ETABSv1", SimpleNamespace(eItemType=SimpleNamespace(Objects=0)))
    calls = []

    class Groups:
        def GetNameList(self, *args):
            return 0, 0, []

        def SetGroup(self, *args):
            calls.append(("SetGroup", args[0]))
            return 0

        def Delete(self, *args):
            calls.append(("Delete", args[0]))
            return 0

    class Objects:
        def __init__(self, guids):
            self.guids = guids

        def GetGUID(self, name, *args):
            return 0, self.guids[name]

        def SetGroupAssign(self, name, group, remove, *args):
            calls.append(("Assign", name, group, bool(remove)))
            return 0

    sap = SimpleNamespace(
        GroupDef=Groups(),
        FrameObj=Objects({"B1": "GB1"}),
        PointObj=Objects({"P1": "GP1"}),
    )
    report = TowerAssignmentReport(
        status="REVIEW_REQUIRED",
        records=(
            TowerObjectRecord("Beam", "B1", guid="GB1", status="TOWER_POINT_ASSIGNMENT_MISMATCH"),
            TowerObjectRecord("Point", "P1", guid="GP1", status="TOWER_POINT_ASSIGNMENT_MISMATCH"),
        ),
        groups=(TowerGroupPlan("CODX_TOWER_CHECK_EXPECTED_T1", "T1", ("B1",), ("P1",), ("GB1",), ("GP1",)),),
    )
    ownership = tmp_path / "ownership.json"
    result = create_tower_review_groups(
        sap,
        report,
        allow_mutation=True,
        ownership_path=ownership,
        expected_model_sha256="abc",
    )
    assert result.created == ("CODX_TOWER_CHECK_EXPECTED_T1",)
    assert ownership.is_file()
    assert any(call[0] == "Assign" and call[3] is False for call in calls)


def test_group_name_conflict_is_rejected_before_assignment(monkeypatch):
    monkeypatch.setitem(sys.modules, "ETABSv1", SimpleNamespace(eItemType=SimpleNamespace(Objects=0)))

    class Groups:
        def GetNameList(self, *args):
            return 0, 1, ["CODX_TOWER_CHECK_EXPECTED_T1"]

    report = TowerAssignmentReport(
        status="REVIEW_REQUIRED",
        groups=(TowerGroupPlan("CODX_TOWER_CHECK_EXPECTED_T1", "T1", ("B1",)),),
    )
    sap = SimpleNamespace(GroupDef=Groups())
    with pytest.raises(TowerAssignmentApiError, match="TOWER_GROUP_NAME_CONFLICT"):
        create_tower_review_groups(sap, report, allow_mutation=True)
