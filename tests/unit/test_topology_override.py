import pytest

from src.core.topology_override import (
    ManualGroupKind,
    ManualOverrideError,
    ManualTopologyOverrides,
    OverrideObject,
    create_manual_group,
    read_manual_overrides,
    validate_group_against_current_model,
    validate_override_collection,
    write_manual_overrides,
)


def _object(guid, name, start, end, story="F2"):
    return OverrideObject(guid, name, story, start, end)


def test_curved_short_segments_form_one_ordered_manual_span() -> None:
    selected = [
        _object("g2", "B2", (10, 0, 0), (15, 5, 0)),
        _object("g1", "B1", (0, 0, 0), (10, 0, 0)),
        _object("g3", "B3", (15, 5, 0), (15, 15, 0)),
    ]
    group = create_manual_group("MG-1", ManualGroupKind.SPAN, selected)
    assert group.object_guids_in_i_to_j_order == ("g1", "g2", "g3")
    assert group.object_names_at_creation == ("B1", "B2", "B3")
    assert group.i_end_coordinate_mm == (0, 0, 0)


@pytest.mark.parametrize(
    "selected,message",
    [
        (
            [
                _object("g1", "B1", (0, 0, 0), (10, 0, 0)),
                _object("g2", "B2", (20, 0, 0), (30, 0, 0)),
            ],
            "断开",
        ),
        (
            [
                _object("g1", "B1", (0, 0, 0), (10, 0, 0)),
                _object("g2", "B2", (10, 0, 0), (20, 0, 0)),
                _object("g3", "B3", (10, 0, 0), (10, 10, 0)),
            ],
            "分叉",
        ),
    ],
)
def test_disconnected_or_branched_selection_is_rejected(selected, message) -> None:
    with pytest.raises(ManualOverrideError, match=message):
        create_manual_group("MG", ManualGroupKind.SPAN, selected)


def test_internal_support_requires_force_and_reason() -> None:
    selected = [_object("g1", "B1", (0, 0, 0), (20, 0, 0))]
    with pytest.raises(ManualOverrideError, match="MANUAL_SPAN_SUPPORT_CONFLICT"):
        create_manual_group(
            "MG",
            ManualGroupKind.SPAN,
            selected,
            support_points_mm=[(10, 0, 0)],
        )
    with pytest.raises(ManualOverrideError, match="工程原因"):
        create_manual_group(
            "MG",
            ManualGroupKind.SPAN,
            selected,
            support_points_mm=[(10, 0, 0)],
            force_support_override=True,
        )
    group = create_manual_group(
        "MG",
        ManualGroupKind.SPAN,
        selected,
        support_points_mm=[(10, 0, 0)],
        force_support_override=True,
        reason="工程师确认该点不是结构分跨支承",
    )
    assert group.support_conflicts_mm == ((10.0, 0.0, 0.0),)


def test_object_rename_does_not_invalidate_guid_group() -> None:
    group = create_manual_group(
        "MG",
        ManualGroupKind.LINE,
        [
            _object("g1", "4412", (0, 0, 0), (10, 0, 0)),
            _object("g2", "4413", (10, 0, 0), (20, 0, 0)),
        ],
    )
    current = {
        "g1": _object("g1", "F2-PB1-1.1", (0, 0, 0), (10, 0, 0)),
        "g2": _object("g2", "F2-PB1-1.2", (10, 0, 0), (20, 0, 0)),
    }
    assert validate_group_against_current_model(group, current).ok


def test_duplicate_span_membership_is_rejected() -> None:
    first = create_manual_group(
        "MG-1",
        ManualGroupKind.SPAN,
        [_object("g1", "B1", (0, 0, 0), (10, 0, 0))],
    )
    second = create_manual_group(
        "MG-2",
        ManualGroupKind.SPAN,
        [_object("g1", "B1", (0, 0, 0), (10, 0, 0))],
    )
    with pytest.raises(ManualOverrideError, match="同时属于"):
        validate_override_collection((first, second))


def test_manual_override_json_round_trip(tmp_path) -> None:
    group = create_manual_group(
        "MG-1",
        ManualGroupKind.SPAN,
        [_object("g1", "B1", (0, 0, 0), (10, 0, 0))],
        automatic_span_ids_before_override=("F2-PB1-1",),
        base_run_id="AUTO-1",
    )
    source = ManualTopologyOverrides("model-sha", (group,))
    path = write_manual_overrides(source, tmp_path / "manual.json")
    loaded = read_manual_overrides(path)
    assert loaded == source
