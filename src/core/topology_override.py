"""GUID-based manual Beam Line/Span topology overrides.

The ETABS plug-in is only an interaction layer.  This module owns the stable
data contract and validates a user's selected Frame Objects as one connected,
ordered I-to-J chain before any numbering or model write-back is planned.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from src.utils.geometry import Point3D, distance


class ManualGroupKind(str, Enum):
    LINE = "MANUAL_LINE"
    SPAN = "MANUAL_SPAN"


class ManualOverrideError(ValueError):
    """Raised when a selected manual group is not a safe ordered chain."""


@dataclass(frozen=True)
class OverrideObject:
    guid: str
    name: str
    story: str
    start: Point3D
    end: Point3D


@dataclass(frozen=True)
class ManualTopologyGroup:
    group_id: str
    kind: ManualGroupKind
    story: str
    object_guids_in_i_to_j_order: tuple[str, ...]
    object_reversed_in_i_to_j_order: tuple[bool, ...]
    object_names_at_creation: tuple[str, ...]
    i_end_coordinate_mm: Point3D
    automatic_span_ids_before_override: tuple[str, ...] = ()
    reason: str = ""
    force_support_override: bool = False
    support_conflicts_mm: tuple[Point3D, ...] = ()
    base_run_id: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "kind": self.kind.value,
            "base_run_id": self.base_run_id,
            "story": self.story,
            "object_guids_in_i_to_j_order": list(self.object_guids_in_i_to_j_order),
            "object_reversed_in_i_to_j_order": list(self.object_reversed_in_i_to_j_order),
            "object_names_at_creation": list(self.object_names_at_creation),
            "automatic_span_ids_before_override": list(self.automatic_span_ids_before_override),
            "i_end_coordinate_mm": list(self.i_end_coordinate_mm),
            "reason": self.reason,
            "force_support_override": self.force_support_override,
            "support_conflicts_mm": [list(point) for point in self.support_conflicts_mm],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ManualTopologyGroup":
        guids = tuple(str(item) for item in value["object_guids_in_i_to_j_order"])
        reverses = tuple(bool(item) for item in value["object_reversed_in_i_to_j_order"])
        if len(guids) != len(reverses):
            raise ManualOverrideError("人工组 GUID 数量与方向标记数量不一致。")
        return cls(
            group_id=str(value["group_id"]),
            kind=ManualGroupKind(str(value["kind"])),
            story=str(value["story"]),
            object_guids_in_i_to_j_order=guids,
            object_reversed_in_i_to_j_order=reverses,
            object_names_at_creation=tuple(str(item) for item in value.get("object_names_at_creation", ())),
            i_end_coordinate_mm=_point(value["i_end_coordinate_mm"]),
            automatic_span_ids_before_override=tuple(
                str(item) for item in value.get("automatic_span_ids_before_override", ())
            ),
            reason=str(value.get("reason", "")),
            force_support_override=bool(value.get("force_support_override", False)),
            support_conflicts_mm=tuple(_point(item) for item in value.get("support_conflicts_mm", ())),
            base_run_id=str(value.get("base_run_id", "")),
        )


@dataclass(frozen=True)
class ManualTopologyOverrides:
    model_fingerprint: str
    groups: tuple[ManualTopologyGroup, ...]
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "model_fingerprint": self.model_fingerprint,
            "groups": [group.to_dict() for group in self.groups],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ManualTopologyOverrides":
        schema = str(value.get("schema_version", ""))
        if schema != "1.0":
            raise ManualOverrideError(f"不支持的人工拓扑 schema：{schema}")
        result = cls(
            model_fingerprint=str(value.get("model_fingerprint", "")),
            groups=tuple(ManualTopologyGroup.from_dict(item) for item in value.get("groups", ())),
            schema_version=schema,
        )
        validate_override_collection(result.groups)
        return result


@dataclass(frozen=True)
class OverrideValidation:
    ok: bool
    status: str
    issues: tuple[str, ...] = ()


def create_manual_group(
    group_id: str,
    kind: ManualGroupKind | str,
    selected_objects: Sequence[OverrideObject],
    *,
    i_end_coordinate_mm: Point3D | None = None,
    support_points_mm: Iterable[Point3D] = (),
    coordinate_tolerance_mm: float = 1.0,
    automatic_span_ids_before_override: Sequence[str] = (),
    reason: str = "",
    force_support_override: bool = False,
    base_run_id: str = "",
) -> ManualTopologyGroup:
    """Validate and order one ETABS selection as an I-to-J manual group."""

    if not group_id.strip():
        raise ManualOverrideError("人工组必须具有 group_id。")
    try:
        group_kind = ManualGroupKind(kind)
    except ValueError as exc:
        raise ManualOverrideError(f"未知人工组类型：{kind}") from exc
    if not selected_objects:
        raise ManualOverrideError("没有选中的 Beam Object。")
    guids = [item.guid for item in selected_objects]
    if any(not guid for guid in guids) or len(guids) != len(set(guids)):
        raise ManualOverrideError("选中对象缺少 GUID 或 GUID 重复。")
    stories = {item.story for item in selected_objects}
    if len(stories) != 1:
        raise ManualOverrideError("人工组中的 Beam Object 必须位于同一楼层。")

    ordered, reverses, path = _order_object_chain(
        selected_objects,
        i_end_coordinate_mm,
        coordinate_tolerance_mm,
    )
    conflicts = _internal_supports(path, support_points_mm, coordinate_tolerance_mm)
    if group_kind is ManualGroupKind.SPAN and conflicts and not force_support_override:
        raise ManualOverrideError(f"MANUAL_SPAN_SUPPORT_CONFLICT：组内存在 {len(conflicts)} 个结构支承。")
    if force_support_override and not reason.strip():
        raise ManualOverrideError("强制跨越内部支承时必须填写工程原因。")
    return ManualTopologyGroup(
        group_id=group_id.strip(),
        kind=group_kind,
        story=next(iter(stories)),
        object_guids_in_i_to_j_order=tuple(item.guid for item in ordered),
        object_reversed_in_i_to_j_order=tuple(reverses),
        object_names_at_creation=tuple(item.name for item in ordered),
        i_end_coordinate_mm=path[0],
        automatic_span_ids_before_override=tuple(automatic_span_ids_before_override),
        reason=reason.strip(),
        force_support_override=force_support_override,
        support_conflicts_mm=conflicts,
        base_run_id=base_run_id,
    )


def validate_override_collection(groups: Sequence[ManualTopologyGroup]) -> None:
    """Reject duplicate IDs and overlapping groups of the same kind."""

    ids = [group.group_id for group in groups]
    if len(ids) != len(set(ids)):
        raise ManualOverrideError("人工拓扑 override 中存在重复 group_id。")
    by_kind: dict[ManualGroupKind, dict[str, str]] = {
        ManualGroupKind.LINE: {},
        ManualGroupKind.SPAN: {},
    }
    for group in groups:
        for guid in group.object_guids_in_i_to_j_order:
            previous = by_kind[group.kind].get(guid)
            if previous is not None:
                raise ManualOverrideError(f"Object GUID {guid} 同时属于 {previous} 和 {group.group_id}。")
            by_kind[group.kind][guid] = group.group_id


def validate_group_against_current_model(
    group: ManualTopologyGroup,
    objects_by_guid: Mapping[str, OverrideObject],
    *,
    support_points_mm: Iterable[Point3D] = (),
    coordinate_tolerance_mm: float = 1.0,
) -> OverrideValidation:
    """Revalidate a stored group after names or model geometry have changed."""

    missing = [guid for guid in group.object_guids_in_i_to_j_order if guid not in objects_by_guid]
    if missing:
        return OverrideValidation(
            False,
            "STALE",
            tuple(f"GUID 不存在：{guid}" for guid in missing),
        )
    selected = [objects_by_guid[guid] for guid in group.object_guids_in_i_to_j_order]
    try:
        current = create_manual_group(
            group.group_id,
            group.kind,
            selected,
            i_end_coordinate_mm=group.i_end_coordinate_mm,
            support_points_mm=support_points_mm,
            coordinate_tolerance_mm=coordinate_tolerance_mm,
            automatic_span_ids_before_override=group.automatic_span_ids_before_override,
            reason=group.reason,
            force_support_override=group.force_support_override,
            base_run_id=group.base_run_id,
        )
    except ManualOverrideError as exc:
        return OverrideValidation(False, "BLOCKED", (str(exc),))
    issues: list[str] = []
    if current.story != group.story:
        issues.append(f"楼层由 {group.story} 变为 {current.story}。")
    if current.object_guids_in_i_to_j_order != group.object_guids_in_i_to_j_order:
        issues.append("对象的 I→J 连通顺序发生变化。")
    if current.object_reversed_in_i_to_j_order != group.object_reversed_in_i_to_j_order:
        issues.append("对象的 I→J 方向发生变化。")
    if current.support_conflicts_mm != group.support_conflicts_mm:
        issues.append("人工 Span 的内部支承清单发生变化。")
    return OverrideValidation(not issues, "VALID" if not issues else "STALE", tuple(issues))


def read_manual_overrides(path: str | Path) -> ManualTopologyOverrides:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ManualOverrideError("人工拓扑 override 根节点必须是 JSON object。")
    return ManualTopologyOverrides.from_dict(value)


def write_manual_overrides(overrides: ManualTopologyOverrides, path: str | Path) -> Path:
    validate_override_collection(overrides.groups)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(overrides.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output


def _order_object_chain(
    objects: Sequence[OverrideObject],
    requested_i_end: Point3D | None,
    tolerance: float,
) -> tuple[tuple[OverrideObject, ...], tuple[bool, ...], tuple[Point3D, ...]]:
    nodes: list[Point3D] = []

    def node_for(point: Point3D) -> int:
        for index, existing in enumerate(nodes):
            if distance(point, existing) <= tolerance:
                return index
        nodes.append(point)
        return len(nodes) - 1

    edges: list[tuple[OverrideObject, int, int]] = []
    adjacency: dict[int, list[int]] = {}
    for item in objects:
        left, right = node_for(item.start), node_for(item.end)
        if left == right:
            raise ManualOverrideError(f"Object {item.name} 为零长度或闭合边。")
        edge_index = len(edges)
        edges.append((item, left, right))
        adjacency.setdefault(left, []).append(edge_index)
        adjacency.setdefault(right, []).append(edge_index)
    if any(len(edge_indices) > 2 for edge_indices in adjacency.values()):
        raise ManualOverrideError("选中对象形成分叉链，不能指定唯一 I→J。")
    first_node = next(iter(adjacency))
    reachable_nodes = {first_node}
    stack = [first_node]
    while stack:
        node = stack.pop()
        for edge_index in adjacency[node]:
            _, left, right = edges[edge_index]
            other = right if node == left else left
            if other not in reachable_nodes:
                reachable_nodes.add(other)
                stack.append(other)
    if len(reachable_nodes) != len(adjacency):
        raise ManualOverrideError("选中对象包含断开的梁段。")
    end_nodes = [node for node, edge_indices in adjacency.items() if len(edge_indices) == 1]
    if len(end_nodes) != 2:
        raise ManualOverrideError("选中对象必须形成具有两个端点的开链；闭合环不支持。")

    visited_edges: set[int] = set()
    start_node = _select_start_node(nodes, end_nodes, requested_i_end, tolerance)
    current_node = start_node
    ordered: list[OverrideObject] = []
    reverses: list[bool] = []
    path: list[Point3D] = [nodes[start_node]]
    while len(visited_edges) < len(edges):
        choices = [edge for edge in adjacency[current_node] if edge not in visited_edges]
        if len(choices) != 1:
            raise ManualOverrideError("选中对象无法形成唯一连续路径。")
        edge_index = choices[0]
        item, left, right = edges[edge_index]
        reverse = current_node == right
        next_node = left if reverse else right
        ordered.append(item)
        reverses.append(reverse)
        path.append(nodes[next_node])
        visited_edges.add(edge_index)
        current_node = next_node
    return tuple(ordered), tuple(reverses), tuple(path)


def _select_start_node(
    nodes: Sequence[Point3D],
    end_nodes: Sequence[int],
    requested_i_end: Point3D | None,
    tolerance: float,
) -> int:
    if requested_i_end is not None:
        ranked = sorted((distance(nodes[node], requested_i_end), node) for node in end_nodes)
        if ranked[0][0] > tolerance:
            raise ManualOverrideError("指定 I 端不在人工组的两个端点上。")
        return ranked[0][1]
    left, right = end_nodes
    start, end = nodes[left], nodes[right]
    horizontal = abs(end[0] - start[0]) >= abs(end[1] - start[1])
    if horizontal:
        return left if (start[0], start[1], start[2]) <= (end[0], end[1], end[2]) else right
    return left if (start[1], start[0], start[2]) <= (end[1], end[0], end[2]) else right


def _internal_supports(
    path: Sequence[Point3D],
    support_points: Iterable[Point3D],
    tolerance: float,
) -> tuple[Point3D, ...]:
    conflicts: list[Point3D] = []
    for raw_point in support_points:
        point = _point(raw_point)
        if distance(point, path[0]) <= tolerance or distance(point, path[-1]) <= tolerance:
            continue
        if any(_point_on_segment(point, left, right, tolerance) for left, right in zip(path, path[1:])):
            if not any(distance(point, existing) <= tolerance for existing in conflicts):
                conflicts.append(point)
    return tuple(conflicts)


def _point_on_segment(
    point: Point3D,
    start: Point3D,
    end: Point3D,
    tolerance: float,
) -> bool:
    vector = tuple(end[index] - start[index] for index in range(3))
    length_sq = sum(value * value for value in vector)
    if length_sq <= tolerance * tolerance:
        return distance(point, start) <= tolerance
    projection = sum((point[index] - start[index]) * vector[index] for index in range(3)) / length_sq
    if projection < 0.0 or projection > 1.0:
        return False
    projected = tuple(start[index] + projection * vector[index] for index in range(3))
    return distance(point, projected) <= tolerance


def _point(value: object) -> Point3D:
    try:
        items = tuple(float(item) for item in value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ManualOverrideError(f"无效坐标：{value!r}") from exc
    if len(items) != 3:
        raise ManualOverrideError(f"坐标必须包含三个分量：{value!r}")
    return items[0], items[1], items[2]
