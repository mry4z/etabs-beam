from datetime import datetime
from pathlib import Path

import pytest

from src.core.beam_line_span_analyzer import BeamLineSpanResult, BeamSpan
from src.core.geometry_extractor import BeamGeometry
from src.core.model_writeback import (
    AuditComparison,
    EtabsWriteback,
    FrameAuditState,
    FrameState,
    OrientationAction,
    RenameAction,
    WritebackExecutionError,
    WritebackPlanError,
    build_writeback_plan,
    compare_design_envelopes,
    compare_frame_audits,
    create_working_copy,
    exclude_writeback_objects,
    plan_orientation,
    plan_rename,
)


def _beam(name: str, start: tuple[float, float, float], end: tuple[float, float, float]) -> BeamGeometry:
    return BeamGeometry(name, name, "F1", f"{name}-I", f"{name}-J", start, end, "B 300X600", 600)


def _state(beam: BeamGeometry, guid: str, reverse: bool = False) -> FrameState:
    return FrameState(
        beam.name,
        guid,
        beam.point_j if reverse else beam.point_i,
        beam.point_i if reverse else beam.point_j,
        beam.label,
        beam.story,
        beam.section,
    )


def test_plan_orientation_uses_direction_not_coordinate_sign() -> None:
    beam = _beam("B1", (100.0, 100.0, 0.0), (0.0, 100.0, 0.0))
    state = _state(beam, "g1")
    action = plan_orientation([beam], {beam.name: state})[0]
    assert action.reverse is True
    assert (action.new_point_i, action.new_point_j) == (beam.point_j, beam.point_i)


def test_plan_orientation_follows_logical_path_for_a_reversed_vertical_segment() -> None:
    beam = _beam("B1", (0.0, 1000.0, 0.0), (0.0, 0.0, 0.0))
    state = _state(beam, "g1")
    action = plan_orientation(
        [beam],
        {beam.name: state},
        desired_paths={beam.name: ((0.0, 0.0, 0.0), (0.0, 1000.0, 0.0))},
    )[0]
    assert action.reverse is True


def test_plan_rename_blocks_object_used_by_two_spans() -> None:
    beam = _beam("B1", (0.0, 0.0, 0.0), (1000.0, 0.0, 0.0))
    result = BeamLineSpanResult(
        (),
        (
            BeamSpan("F1", "PB", 1, 1, ("B1",), (beam.start, beam.end), False, False),
            BeamSpan("F1", "PB", 1, 2, ("B1",), (beam.start, beam.end), False, False),
        ),
        (),
    )
    actions = plan_rename(result, {beam.name: _state(beam, "g1")})
    assert all(action.status == "REQUIRES_SPLIT" for action in actions)


def test_plan_rename_uses_no_s_prefix() -> None:
    beam = _beam("B1", (0.0, 0.0, 0.0), (1000.0, 0.0, 0.0))
    result = BeamLineSpanResult(
        (),
        (BeamSpan("T1-F2", "SB", 10, 3, ("B1",), (beam.start, beam.end), False, False),),
        (),
    )
    action = plan_rename(result, {beam.name: _state(beam, "g1")})[0]
    assert action.final_name == "T1-F2-SB10-3.1"


def test_mixed_section_span_requires_confirmation_before_any_writeback() -> None:
    beam1 = _beam("B1", (0.0, 0.0, 0.0), (1000.0, 0.0, 0.0))
    beam2 = BeamGeometry(
        "B2",
        "B2",
        "F1",
        "B2-I",
        "B2-J",
        (1000.0, 0.0, 0.0),
        (2000.0, 0.0, 0.0),
        "B 500X600",
        600,
    )
    result = BeamLineSpanResult(
        (),
        (BeamSpan("F1", "PB", 1, 1, ("B1", "B2"), (beam1.start, beam1.end, beam2.end), False, False),),
        (),
    )
    states = {"B1": _state(beam1, "g1"), "B2": _state(beam2, "g2")}

    plan = build_writeback_plan(result, [beam1, beam2], states)

    assert {action.status for action in plan.renames} == {"REQUIRES_SECTION_REVIEW"}
    assert {action.status for action in plan.orientations} == {"REQUIRES_SECTION_REVIEW"}


def test_confirmed_step_beam_can_be_renamed_by_segment() -> None:
    beam1 = _beam("B1", (0.0, 0.0, 0.0), (1000.0, 0.0, 0.0))
    beam2 = BeamGeometry(
        "B2",
        "B2",
        "F1",
        "B2-I",
        "B2-J",
        (1000.0, 0.0, 0.0),
        (2000.0, 0.0, 0.0),
        "B 500X600",
        600,
    )
    result = BeamLineSpanResult(
        (),
        (BeamSpan("F1", "PB", 1, 1, ("B1", "B2"), (beam1.start, beam1.end, beam2.end), False, False),),
        (),
    )
    states = {"B1": _state(beam1, "g1"), "B2": _state(beam2, "g2")}

    actions = plan_rename(
        result,
        states,
        confirmed_multi_section_span_ids={"F1-PB1-1"},
    )

    assert [action.final_name for action in actions] == ["F1-PB1-1.1", "F1-PB1-1.2"]
    assert all(action.status == "READY" for action in actions)


def test_excluded_object_blocks_both_orientation_and_rename() -> None:
    beam = _beam("B1", (1000.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    result = BeamLineSpanResult(
        (),
        (BeamSpan("F1", "PB", 1, 1, ("B1",), (beam.end, beam.start), False, False),),
        (),
    )
    plan = build_writeback_plan(result, [beam], {"B1": _state(beam, "g1")})

    excluded = exclude_writeback_objects(plan, {"B1"})

    assert excluded.orientations[0].status == "REQUIRES_DEMAND_REVIEW"
    assert excluded.renames[0].status == "REQUIRES_DEMAND_REVIEW"


def test_mutation_requires_explicit_flag() -> None:
    with pytest.raises(WritebackPlanError):
        EtabsWriteback(object()).apply_orientation((), allow_mutation=False)


def test_snapshot_from_geometry_only_reads_guid() -> None:
    beam = _beam("B1", (0.0, 0.0, 0.0), (1000.0, 0.0, 0.0))

    class FrameObj:
        calls: list[str] = []

        def GetGUID(self, name, *_):
            self.calls.append(name)
            return 0, "guid-B1"

    frame_obj = FrameObj()
    sap = type("Sap", (), {"FrameObj": frame_obj})()
    states = EtabsWriteback(sap).snapshot_from_geometry([beam])

    assert frame_obj.calls == ["B1"]
    assert states["B1"] == _state(beam, "guid-B1")


def test_verify_orientation_can_resolve_names_after_rename() -> None:
    class FrameObj:
        def GetPoints(self, name, *_):
            assert name == "F1-PB1-1.1"
            return (0, "42-J", "42-I")

    action = plan_orientation(
        [_beam("42", (100.0, 0.0, 0.0), (0.0, 0.0, 0.0))],
        {"42": _state(_beam("42", (100.0, 0.0, 0.0), (0.0, 0.0, 0.0)), "g1")},
    )[0]
    result = EtabsWriteback(type("Sap", (), {"FrameObj": FrameObj()})()).verify_orientation(
        [action], name_resolver={"42": "F1-PB1-1.1"}
    )
    assert result.ok is True


def test_create_working_copy_is_non_overwriting(tmp_path: Path) -> None:
    source = tmp_path / "model.EDB"
    source.write_bytes(b"edb")
    stamp = datetime(2026, 1, 2, 3, 4, 5)
    copy = create_working_copy(source, tmp_path / "out", timestamp=stamp)
    assert copy.read_bytes() == b"edb"
    with pytest.raises(FileExistsError):
        create_working_copy(source, tmp_path / "out", timestamp=stamp)


def test_compare_frame_audits_allows_only_expected_name_and_axis_reversal() -> None:
    attributes = {
        "section": ("B 300X600", ""),
        "groups": (1, ("BEAMS",)),
        "loads": (),
        "end_offsets": (True, 100.0, 200.0, 0.5),
        "releases": ((True, False), (False, True), (1.0, 2.0), (3.0, 4.0)),
    }
    before = {"g1": FrameAuditState("B1", "g1", "I", "J", (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0), attributes)}
    after = {
        "g1": FrameAuditState(
            "F1-PB1-1.1",
            "g1",
            "J",
            "I",
            (-1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0),
            {
                **attributes,
                "end_offsets": (True, 200.0, 100.0, 0.5),
                "releases": (
                    (False, True),
                    (True, False),
                    (3.0, 4.0),
                    (1.0, 2.0),
                ),
            },
        )
    }

    result = compare_frame_audits(
        before,
        after,
        renamed={"B1": "F1-PB1-1.1"},
        reversed_guids={"g1"},
    )

    assert result == AuditComparison(True, 1, ())


def test_compare_frame_audits_fails_on_assignment_change() -> None:
    matrix = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    before = {"g1": FrameAuditState("B1", "g1", "I", "J", matrix, {"groups": ("A",)})}
    after = {"g1": FrameAuditState("B1", "g1", "I", "J", matrix, {"groups": ("B",)})}

    result = compare_frame_audits(before, after)

    assert result.ok is False
    assert "groups" in result.failures[0]


def test_idempotent_rename_does_not_call_change_name() -> None:
    class FrameObj:
        def GetGUID(self, name, *_):
            return 0, "g1"

        def ChangeName(self, old, new):
            raise AssertionError("idempotent rename must not mutate")

    action = RenameAction("F1-PB1-1.1", "g1", "F1-PB1-1.1", "F1-PB1-1", 1)
    result = EtabsWriteback(type("Sap", (), {"FrameObj": FrameObj()})()).apply_rename([action], allow_mutation=True)
    assert result.ok is True


def test_orientation_failure_rolls_back_completed_actions() -> None:
    calls = []

    class EditFrame:
        def ChangeConnectivity(self, name, point_i, point_j):
            calls.append((name, point_i, point_j))
            return 1 if name == "B2" else 0

    class FrameObj:
        def GetPoints(self, name, *_):
            return 0, "J", "I"

    sap = type("Sap", (), {"EditFrame": EditFrame(), "FrameObj": FrameObj()})()
    actions = [
        OrientationAction("B1", "g1", "I", "J", "J", "I", True),
        OrientationAction("B2", "g2", "I", "J", "J", "I", True),
    ]
    with pytest.raises(WritebackExecutionError, match="已回滚"):
        EtabsWriteback(sap).apply_orientation(actions, allow_mutation=True)
    assert calls == [("B1", "J", "I"), ("B2", "J", "I"), ("B1", "I", "J")]


def test_completed_transaction_rollback_restores_names_and_orientation() -> None:
    names = {"F1-PB1-1.1": "g1"}
    connectivity = {"F1-PB1-1.1": ("J", "I")}

    class FrameObj:
        def ChangeName(self, old, new):
            names[new] = names.pop(old)
            connectivity[new] = connectivity.pop(old)
            return 0

    class EditFrame:
        def ChangeConnectivity(self, name, point_i, point_j):
            connectivity[name] = (point_i, point_j)
            return 0

    sap = type("Sap", (), {"FrameObj": FrameObj(), "EditFrame": EditFrame()})()
    result = EtabsWriteback(sap).rollback_completed(
        [OrientationAction("B1", "g1", "I", "J", "J", "I", True)],
        [RenameAction("B1", "g1", "F1-PB1-1.1", "F1-PB1-1", 1)],
        allow_mutation=True,
    )
    assert result.ok is True
    assert names == {"B1": "g1"}
    assert connectivity == {"B1": ("I", "J")}


def test_temporary_rename_failure_rolls_back_only_staged_names() -> None:
    names = {"B1": "g1", "B2": "g2"}

    class FrameObj:
        def GetNameList(self, *_):
            return 0, len(names), list(names)

        def ChangeName(self, old, new):
            if old == "B2":
                return 1
            names[new] = names.pop(old)
            return 0

    actions = [
        RenameAction("B1", "g1", "F1-PB1-1.1", "F1-PB1-1", 1),
        RenameAction("B2", "g2", "F1-PB1-1.2", "F1-PB1-1", 2),
    ]
    with pytest.raises(WritebackExecutionError, match="已回滚"):
        EtabsWriteback(type("Sap", (), {"FrameObj": FrameObj()})()).apply_rename(actions, allow_mutation=True)
    assert names == {"B1": "g1", "B2": "g2"}


def test_final_rename_failure_rolls_back_both_stages() -> None:
    names = {"B1": "g1", "B2": "g2"}

    class FrameObj:
        def GetNameList(self, *_):
            return 0, len(names), list(names)

        def ChangeName(self, old, new):
            if old.startswith("__codex_wb_00001") and new == "F1-PB1-1.2":
                return 1
            names[new] = names.pop(old)
            return 0

    actions = [
        RenameAction("B1", "g1", "F1-PB1-1.1", "F1-PB1-1", 1),
        RenameAction("B2", "g2", "F1-PB1-1.2", "F1-PB1-1", 2),
    ]
    with pytest.raises(WritebackExecutionError, match="已回滚"):
        EtabsWriteback(type("Sap", (), {"FrameObj": FrameObj()})()).apply_rename(actions, allow_mutation=True)
    assert names == {"B1": "g1", "B2": "g2"}


def test_rename_rollback_failure_marks_copy_unusable() -> None:
    names = {"B1": "g1", "B2": "g2"}

    class FrameObj:
        def GetNameList(self, *_):
            return 0, len(names), list(names)

        def ChangeName(self, old, new):
            if old == "B2" or new == "B1":
                return 1
            names[new] = names.pop(old)
            return 0

    actions = [
        RenameAction("B1", "g1", "F1-PB1-1.1", "F1-PB1-1", 1),
        RenameAction("B2", "g2", "F1-PB1-1.2", "F1-PB1-1", 2),
    ]
    with pytest.raises(WritebackExecutionError, match="放弃当前工作副本"):
        EtabsWriteback(type("Sap", (), {"FrameObj": FrameObj()})()).apply_rename(actions, allow_mutation=True)


def test_compare_design_envelopes_ignores_station_order() -> None:
    from src.design.provider import BeamDesignResult, DesignStation

    def station(location, top):
        return DesignStation(location, "C", top, "C", 2.0, "C", 3.0, "C", 4.0, "C", 5.0)

    before = {"B1": BeamDesignResult("B1", (station(0.0, 1.0), station(1.0, 6.0)))}
    after = {"F1-PB1-1.1": BeamDesignResult("F1-PB1-1.1", (station(0.0, 6.0), station(1.0, 1.0)))}

    result = compare_design_envelopes(before, after, renamed={"B1": "F1-PB1-1.1"})
    assert result.ok is True


def test_compare_design_envelopes_fails_large_shear_change() -> None:
    from src.design.provider import BeamDesignResult, DesignStation

    def result(name, shear):
        station = DesignStation(0.0, "C", 1000.0, "C", 1000.0, "C", shear, "C", 100.0, "C", 0.5)
        return BeamDesignResult(name, (station,))

    comparison = compare_design_envelopes(
        {"B1": result("B1", 0.5)},
        {"N1": result("N1", 2.5)},
        renamed={"B1": "N1"},
    )
    assert comparison.ok is False
