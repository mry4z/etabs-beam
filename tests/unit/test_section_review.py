import json
import sys
from types import SimpleNamespace

import pytest

from src.core.beam_line_span_analyzer import BeamLine, BeamLineSpanResult, BeamSpan
from src.core.geometry_extractor import BeamGeometry
from src.core.section_review import (
    build_multi_section_span_records,
    create_etabs_section_review_groups,
    select_etabs_review_group,
    write_multi_section_span_audit,
)


def _fixture():
    beams = (
        BeamGeometry("A", "A", "F1", "P1", "P2", (0, 0, 0), (10, 0, 0), "B200X600", 600),
        BeamGeometry("B", "B", "F1", "P2", "P3", (10, 0, 0), (20, 0, 0), "B300X600", 600),
        BeamGeometry("C", "C", "F1", "P3", "P4", (20, 0, 0), (30, 0, 0), "B300X600", 600),
    )
    line = BeamLine("F1", "SB", 15, "VARIES", ("A", "B", "C"), ((0, 0, 0), (30, 0, 0)), False)
    spans = (
        BeamSpan("F1", "SB", 15, 1, ("A", "B"), ((0, 0, 0), (10, 0, 0), (20, 0, 0)), False, False),
        BeamSpan("F1", "SB", 15, 2, ("C",), ((20, 0, 0), (30, 0, 0)), False, False),
    )
    return beams, BeamLineSpanResult((line,), spans, ())


def test_build_and_write_multi_section_review_records(tmp_path) -> None:
    beams, result = _fixture()

    records = build_multi_section_span_records(result, beams)

    assert len(records) == 1
    assert records[0].span_id == "F1-SB15-1"
    assert records[0].sections == ("B200X600", "B300X600")
    assert records[0].source_objects == ("A", "B")
    json_path, csv_path = write_multi_section_span_audit(records, tmp_path)
    assert json.loads(json_path.read_text(encoding="utf-8"))[0]["status"] == "REQUIRES_SECTION_REVIEW"
    assert "CODX_MS_F1-SB15-1" in csv_path.read_text(encoding="utf-8-sig")


def test_confirmed_step_beam_stays_in_audit_but_needs_no_review_group(monkeypatch) -> None:
    beams, result = _fixture()
    records = build_multi_section_span_records(
        result,
        beams,
        confirmed_span_ids={"F1-SB15-1"},
    )
    calls = []
    sap = SimpleNamespace(
        GroupDef=SimpleNamespace(SetGroup=lambda *args: calls.append(args) or 0),
        FrameObj=SimpleNamespace(SetGroupAssign=lambda *args: calls.append(args) or 0),
    )
    monkeypatch.setitem(sys.modules, "ETABSv1", SimpleNamespace(eItemType=SimpleNamespace(Objects=0)))

    assert records[0].status == "CONFIRMED_STEP_BEAM"
    assert create_etabs_section_review_groups(sap, records, allow_mutation=True) == ()
    assert calls == []


def test_etabs_review_group_requires_opt_in_and_can_be_selected(monkeypatch) -> None:
    beams, result = _fixture()
    record = build_multi_section_span_records(result, beams)[0]
    calls = []

    class GroupDef:
        def SetGroup(self, *args):
            calls.append(("SetGroup", args))
            return 0

    class FrameObj:
        def SetGroupAssign(self, *args):
            calls.append(("SetGroupAssign", args))
            return 0

    class SelectObj:
        def ClearSelection(self):
            calls.append(("ClearSelection", ()))
            return 0

        def Group(self, *args):
            calls.append(("Group", args))
            return 0

    sap = SimpleNamespace(GroupDef=GroupDef(), FrameObj=FrameObj(), SelectObj=SelectObj())
    monkeypatch.setitem(sys.modules, "ETABSv1", SimpleNamespace(eItemType=SimpleNamespace(Objects=0)))

    with pytest.raises(PermissionError):
        create_etabs_section_review_groups(sap, [record])

    assert create_etabs_section_review_groups(sap, [record], allow_mutation=True) == ("CODX_MS_F1-SB15-1",)
    select_etabs_review_group(sap, record.etabs_group_name)

    assert [call[0] for call in calls] == ["SetGroup", "SetGroupAssign", "SetGroupAssign", "ClearSelection", "Group"]
