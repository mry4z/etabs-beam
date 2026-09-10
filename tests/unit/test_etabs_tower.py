from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from src.core.etabs_tower import (
    EtabsTowerReader,
    TowerAssignmentApiError,
    _public_tables_are_sufficient,
    _read_stories,
    _story_arrays,
    create_tower_review_groups,
)
from src.core.tower_assignment import TowerAssignmentReport, TowerGroupPlan, TowerObjectRecord


class _Tower:
    def __init__(self):
        self.active = "T1"
        self.set_calls = []

    def GetActiveTower(self, *args):
        return 0, self.active

    def GetNameList(self, *args):
        return 0, 2, ["T1", "T2"]

    def SetActiveTower(self, name):
        self.set_calls.append(name)
        self.active = name
        return 0


class _Story:
    def GetStories_2(self, *args):
        return 0, 1, ["L1"], [0.0], [3000.0]


class _TowerScopedStory(_Story):
    def __init__(self, tower):
        self.tower = tower

    def GetStories_2(self, *args):
        elevation = 3000.0 if self.tower.active == "T1" else 6000.0
        return 0, 1, ["L1"], [elevation], [3000.0]


class _Tower23:
    """ETABS 23 public cTower shape: no GetNameList method."""

    def __init__(self):
        self.active = "T1"
        self.set_calls = []

    def GetActiveTower(self, *args):
        return 0, self.active

    def SetActiveTower(self, name):
        self.set_calls.append(name)
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
        "Story Definitions": (
            ("Tower", "Story", "Elevation", "Height"),
            [("T1", "L1", "0", "3000"), ("T2", "L1", "6000", "3000")],
        ),
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

    def __init__(self):
        self.tables = {
            key: (tuple(fields), [tuple(row) for row in rows])
            for key, (fields, rows) in type(self).tables.items()
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
        self.selected = {(1, "P1"), (2, "B1"), (5, "A1")}
        self.SelectObj = SimpleNamespace(
            GetSelected=lambda *args: (
                0,
                len(self.selected),
                [x[0] for x in sorted(self.selected)],
                [x[1] for x in sorted(self.selected)],
            ),
            ClearSelection=lambda: self.selected.clear() or 0,
        )
        for kind, api in ((1, "PointObj"), (2, "FrameObj"), (4, "TendonObj"), (5, "AreaObj"), (7, "LinkObj")):
            setattr(
                self,
                api,
                SimpleNamespace(
                    SetSelected=lambda name, selected, item_type, kind=kind: self.selected.add((kind, name)) or 0,
                ),
            )


def _same_story_different_elevations_sap(monkeypatch, *, table_elevations):
    sap = _Sap()
    sap.DatabaseTables.tables["Point Object Connectivity"] = (
        ("UniqueName", "Tower", "Story", "X", "Y", "Z", "GUID"),
        [
            ("P1", "T1", "L1", "0", "0", "6000", "GP1"),
            ("P2", "T1", "L1", "5000", "0", "6000", "GP2"),
        ],
    )
    if table_elevations:
        sap.DatabaseTables.tables["Story Definitions"] = (
            ("Tower", "Story", "Elevation", "Height"),
            [("T1", "L1", "3000", "3000"), ("T2", "L1", "6000", "3000")],
        )
    else:
        sap.DatabaseTables.tables["Story Definitions"] = (
            ("Tower", "Story", "Height"),
            [("T1", "L1", "3000"), ("T2", "L1", "3000")],
        )
    sap.Story = _TowerScopedStory(sap.Tower)
    return sap


def _beam_record(report):
    return next(record for record in report.records if record.name == "B1")


def test_story_elevation_and_topology_paths_use_available_discriminators(monkeypatch):
    table_sap = _same_story_different_elevations_sap(monkeypatch, table_elevations=True)
    assert _public_tables_are_sufficient(
        {
            "Story Definitions": SimpleNamespace(
                fields=("Tower", "Story", "Elevation", "Height"),
                rows=(("T1", "L1", "3000", "3000"), ("T2", "L1", "6000", "3000")),
            ),
            "Point Object Connectivity": SimpleNamespace(
                fields=("UniqueName", "Tower", "Story", "Z"), rows=(("P1", "T1", "L1", "3000"),)
            ),
            "Beam Object Connectivity": SimpleNamespace(
                fields=("UniqueName", "UniquePtI", "UniquePtJ"), rows=(("B1", "P1", "P1"),)
            ),
        }
    )
    table_report = EtabsTowerReader(table_sap).read_report(selection="all")

    api_sap = _same_story_different_elevations_sap(monkeypatch, table_elevations=False)
    api_report = EtabsTowerReader(api_sap).read_report(selection="all")

    assert table_report.status == "REVIEW_REQUIRED"
    assert api_report.status == "PASS"
    assert _beam_record(table_report).actual_tower == _beam_record(api_report).actual_tower == "T1"
    assert _beam_record(table_report).expected_tower == "T2"
    assert _beam_record(api_report).expected_tower == "T1"
    assert any(item.startswith("TOWER_TABLE_ONLY_PATH") for item in table_report.diagnostics)
    assert any(item.startswith("TOWER_TABLE_ONLY_PATH") for item in api_report.diagnostics)
    assert table_sap.Tower.set_calls == []
    assert api_sap.Tower.set_calls == []


@pytest.mark.parametrize(
    "story_fields, story_rows, expected_status, expected_tower",
    [
        (("Tower", "Story", "Height"), (("T1", "L1", "3000"), ("T2", "L1", "3000")), "PASS", "T1"),
        (("Tower", "Story", "Elevation"), (("T1", "L1", "3000"), ("T2", "L1", "")), "PASS", "T1"),
        (("Tower", "Story", "Elevation"), (("T1", "L1", ""), ("T2", "L1", "6000")), "REVIEW_REQUIRED", "T2"),
    ],
)
def test_missing_or_partial_story_elevation_uses_same_level_topology_path(
    monkeypatch, story_fields, story_rows, expected_status, expected_tower
):
    sap = _same_story_different_elevations_sap(monkeypatch, table_elevations=False)
    sap.DatabaseTables.tables["Story Definitions"] = (story_fields, list(story_rows))

    report = EtabsTowerReader(sap).read_report(selection="all")

    assert any(item.startswith("TOWER_TABLE_ONLY_PATH") for item in report.diagnostics)
    assert not any(item.startswith("TOWER_TABLE_ONLY_REJECTED") for item in report.diagnostics)
    assert report.status == expected_status
    assert _beam_record(report).actual_tower == "T1"
    assert _beam_record(report).expected_tower == expected_tower
    assert sap.Tower.set_calls == []


def test_live_reader_restores_active_tower_and_reports_mixed_endpoints():
    sap = _Sap()
    report = EtabsTowerReader(sap).read_report(selection="all")
    assert report.status == "REVIEW_REQUIRED"
    assert any(record.status == "TOWER_MIXED_ENDPOINT_ASSIGNMENT" for record in report.records)
    assert report.active_tower_before == "T1"
    assert report.active_tower_after == "T1"
    assert sap.Tower.active == "T1"
    assert sap.Tower.set_calls == []
    assert any(item.startswith("TOWER_TABLE_ONLY_PATH") for item in report.diagnostics)
    assert any(item.startswith("TOWER_READ_TIMING_MS") and "tower_switches=0" in item for item in report.diagnostics)


def test_tower_progress_reports_coarse_phases_instead_of_records():
    events = []
    report = EtabsTowerReader(_Sap()).read_report(
        progress=lambda percent, message: events.append((percent, message)),
    )

    assert report.status == "REVIEW_REQUIRED"
    assert events[0][0] == 0
    assert events[-1][0] == 100
    assert len(events) <= 8
    assert [percent for percent, _ in events] == sorted(percent for percent, _ in events)
    assert any("公共表" in message for _, message in events)


def test_same_story_same_elevation_can_use_topology_catalog():
    table = SimpleNamespace(
        fields=("Tower", "Story", "Elevation", "Height"),
        rows=(("T1", "L1", "6000", "3000"), ("T2", "L1", "6000", "3000")),
    )
    assert _public_tables_are_sufficient(
        {
            "Story Definitions": table,
            "Point Object Connectivity": SimpleNamespace(
                fields=("UniqueName", "Tower", "Story", "Z"), rows=(("P1", "T1", "L1", "3000"),)
            ),
            "Beam Object Connectivity": SimpleNamespace(
                fields=("UniqueName", "UniquePtI", "UniquePtJ"), rows=(("B1", "P1", "P1"),)
            ),
        }
    )


def test_missing_point_elevation_rejects_table_only_path(monkeypatch):
    sap = _same_story_different_elevations_sap(monkeypatch, table_elevations=True)
    sap.DatabaseTables.tables["Point Object Connectivity"] = (
        ("UniqueName", "Tower", "Story"),
        [("P1", "T1", "L1"), ("P2", "T1", "L1")],
    )

    report = EtabsTowerReader(sap).read_report()

    assert report.status == "BLOCKED"
    assert not any(item.startswith("TOWER_TABLE_ONLY_PATH") for item in report.diagnostics)
    assert any(item.startswith("TOWER_POINT_ELEVATION_INCOMPLETE") for item in report.diagnostics)
    assert sap.Tower.set_calls == ["T2", "T1"]


def test_end_to_end_timing_includes_slow_selection_restore(monkeypatch):
    import src.core.etabs_tower as etabs_tower

    sap = _api_catalog_sap(monkeypatch)
    original_previous = sap.SelectObj.PreviousSelection
    clock = [0.0]

    def virtual_perf_counter():
        return clock[0]

    def slow_previous_selection():
        clock[0] += 123.0
        return original_previous()

    sap.SelectObj.PreviousSelection = slow_previous_selection
    monkeypatch.setattr(etabs_tower, "perf_counter", virtual_perf_counter)

    report = EtabsTowerReader(sap).read_report()

    end_to_end = next(item for item in report.diagnostics if item.startswith("TOWER_TIMING_MS"))
    assert "selection_capture=0.0" in end_to_end
    assert "selection_restore=123000.0" in end_to_end
    assert "read_report=123000.0" in end_to_end
    assert any(item.startswith("TOWER_READ_TIMING_MS") for item in report.diagnostics)


def test_partial_story_api_catalog_uses_topology_and_stays_reviewable(monkeypatch):
    sap = _api_catalog_sap(monkeypatch)
    sap.Story.GetStories_2 = lambda *args: (0, 1, ["L1"], [None], [3000.0])

    report = EtabsTowerReader(sap).read_report()

    assert report.status == "REVIEW_REQUIRED"
    assert not any(item.startswith("TOWER_STORY_ELEVATION_INCOMPLETE") for item in report.diagnostics)
    assert report.selection_restore_verified is True
    assert sap.selected == {(1, "P1"), (2, "B1"), (5, "A1")}


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


def test_getstories2_probe_matches_etabs22_23_public_signature():
    calls = []

    class Story:
        def GetStories_2(self, *args):
            calls.append(args)
            assert len(args) == 10
            assert args[0] == 0.0
            return (
                0,
                0.0,
                1,
                ["Base", "L1"],
                [0.0, 3000.0],
                [0.0, 3000.0],
                [False, True],
                ["", ""],
                [False, False],
                [0.0, 0.0],
                [0, 0],
            )

    catalog = _read_stories(SimpleNamespace(), Story(), "T1")

    assert calls
    assert catalog[-1].story == "L1"
    assert catalog[-1].elevation == 3000.0


def test_etabs23_without_tower_name_list_uses_story_definition_table():
    sap = _Sap()
    sap.Tower = _Tower23()
    report = EtabsTowerReader(sap).read_report(selection="all")
    assert report.status == "REVIEW_REQUIRED"
    assert {item.tower for item in report.catalog} == {"T1", "T2"}
    assert not any(item.startswith("TOWER_NAME_LIST_READ_FAILED") for item in report.diagnostics)
    assert any(item.startswith("TOWER_TABLE_ONLY_PATH") for item in report.diagnostics)
    assert sap.Tower.set_calls == []


def test_empty_story_api_falls_back_to_public_story_table():
    sap = _Sap()
    sap.Tower = _Tower23()
    sap.Story = _EmptyStory()
    report = EtabsTowerReader(sap).read_report(selection="all")
    assert report.status == "REVIEW_REQUIRED"
    assert {item.tower for item in report.catalog} == {"T1", "T2"}
    assert any(item.startswith("TOWER_TABLE_ONLY_PATH") for item in report.diagnostics)
    assert sap.Tower.active == "T1"


def test_story_api_failure_uses_public_table_when_available():
    class BrokenStory(_Story):
        def GetStories_2(self, *args):
            raise RuntimeError("story api unavailable")

    sap = _Sap()
    sap.Story = BrokenStory()
    report = EtabsTowerReader(sap).read_report(selection="all")
    assert report.status == "REVIEW_REQUIRED"
    assert any(item.startswith("TOWER_TABLE_ONLY_PATH") for item in report.diagnostics)
    assert sap.Tower.active == "T1"


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


def _clearing_tower_sap(monkeypatch):
    monkeypatch.setitem(sys.modules, "ETABSv1", SimpleNamespace(eItemType=SimpleNamespace(Objects="Objects")))
    sap = _Sap()
    sap.switches = []
    sap.previous_selection_calls = 0
    sap.previous_selection = set()
    sap.has_previous_selection = False

    def switch(name):
        sap.switches.append(name)
        if not sap.has_previous_selection:
            sap.previous_selection = set(sap.selected)
            sap.has_previous_selection = True
        sap.Tower.active = name
        sap.selected.clear()  # Reproduce ETABS selection loss on Tower switches.
        return 0

    sap.Tower.SetActiveTower = switch
    sap.SelectObj.PreviousSelection = lambda: (
        setattr(sap, "previous_selection_calls", sap.previous_selection_calls + 1)
        or setattr(sap, "selected", set(sap.previous_selection))
        or 0
    )
    return sap


def _api_catalog_sap(monkeypatch):
    sap = _clearing_tower_sap(monkeypatch)
    # Force the Tower/Story API path so selection recovery is exercised after
    # multiple Tower switches; the public table-only path is covered above.
    fields, rows = sap.DatabaseTables.tables["Story Definitions"]
    sap.DatabaseTables.tables["Story Definitions"] = (("Story", "Height"), [(row[1], row[2]) for row in rows])
    sap.Story = _TowerScopedStory(sap.Tower)
    return sap


@pytest.mark.parametrize("selected", [set(), {(2, "B1")}, {(1, "P1"), (2, "B1"), (4, "T1"), (5, "A1"), (7, "K1")}])
def test_tower_reader_restores_exact_selection_after_last_tower_switch(monkeypatch, selected):
    sap = _api_catalog_sap(monkeypatch)
    sap.selected = set(selected)
    report = EtabsTowerReader(sap).read_report()
    assert report.status == "REVIEW_REQUIRED"
    assert report.selection_restore_verified is True
    assert report.selected_objects_before == report.selected_objects_after == tuple(sorted(selected))
    assert sap.selected == selected
    assert sap.Tower.active == "T1" and sap.switches == ["T2", "T1"]
    assert sap.previous_selection_calls == (0 if not selected else 1)


@pytest.mark.parametrize("failure", ["story", "switch", "table"])
def test_selection_recovers_on_read_failure(monkeypatch, failure):
    sap = _api_catalog_sap(monkeypatch)
    selected = set(sap.selected)
    if failure == "story":
        sap.Story.GetStories_2 = lambda *args: (_ for _ in ()).throw(RuntimeError("story failed"))
    elif failure == "switch":
        original = sap.Tower.SetActiveTower
        sap.Tower.SetActiveTower = lambda name: original(name) or (1 if name == "T2" else 0)
    else:

        def fail(*args):
            sap.selected.clear()
            raise RuntimeError("table failed")

        sap.DatabaseTables.GetAvailableTables = fail
    report = EtabsTowerReader(sap).read_report()
    assert sap.selected == selected
    assert report.selection_restore_verified is True
    assert sap.Tower.active == "T1"
    assert report.diagnostics


@pytest.mark.parametrize(
    "result",
    [
        (9, 0, [], []),
        (0, 2, [2], ["B1"]),
        (0, -1, [], []),
        (0, 1, [99], ["unknown"]),
        (0, 1, [3], ["Cable"]),
        (0, 1, [2], [""]),
    ],
)
def test_unreadable_or_unrestorable_selection_blocks_before_tower_change(monkeypatch, result):
    sap = _clearing_tower_sap(monkeypatch)
    sap.SelectObj.GetSelected = lambda *args: result
    report = EtabsTowerReader(sap).read_report()
    assert report.blocked and report.selection_restore_verified is False
    assert "TOWER_SELECTION_CAPTURE_FAILED" in report.diagnostics[0]
    assert sap.switches == []


@pytest.mark.parametrize("failure", ["set_error", "set_silent", "clear_error", "active_restore"])
def test_restore_failure_blocks_and_does_not_report_pass(monkeypatch, failure):
    sap = _api_catalog_sap(monkeypatch)
    if failure.startswith("set_") or failure == "clear_error":
        sap.previous_selection = {(2, "wrong")}
        sap.has_previous_selection = True
    if failure.startswith("set_"):
        sap.FrameObj.SetSelected = lambda *args: 1 if failure == "set_error" else 0
    elif failure == "clear_error":
        sap.SelectObj.ClearSelection = lambda: 1
    else:
        original = sap.Tower.SetActiveTower
        sap.Tower.SetActiveTower = lambda name: 1 if name == "T1" and len(sap.switches) >= 1 else original(name)
    report = EtabsTowerReader(sap).read_report()
    assert report.blocked
    if failure != "active_restore":
        assert report.selection_restore_verified is False
        assert any("TOWER_SELECTION_RESTORE_FAILED" in item for item in report.diagnostics)
    else:
        # A failed Tower restore must not prevent best-effort selection recovery.
        assert sap.selected == {(1, "P1"), (2, "B1"), (5, "A1")}
        assert any("TOWER_ACTIVE_RESTORE_FAILED" in item for item in report.diagnostics)


def test_consecutive_selected_preflight_runs_do_not_lose_beams(monkeypatch):
    sap = _api_catalog_sap(monkeypatch)
    for _ in range(2):
        selected_names = [name for kind, name in sap.selected if kind == 2]
        report = EtabsTowerReader(sap).read_report(selection="selected", selected_beams=selected_names)
        assert report.checked_beams == 1 and report.selection_restore_verified


def test_model_change_refuses_selection_restore_into_other_model(monkeypatch):
    sap = _api_catalog_sap(monkeypatch)
    path = ["model.EDB"]
    sap.GetModelFilename = lambda *args: path[0]

    def change_model(*args):
        path[0] = "other.EDB"
        sap.selected = {(2, "other-beam")}
        raise RuntimeError("model replaced")

    sap.DatabaseTables.GetAvailableTables = change_model
    report = EtabsTowerReader(sap).read_report()
    assert report.blocked and report.selection_restore_verified is False
    assert sap.selected == {(2, "other-beam")}
    assert any("SELECTION_RESTORE_MODEL_CHANGED" in item for item in report.diagnostics)
