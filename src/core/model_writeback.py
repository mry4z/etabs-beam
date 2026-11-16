"""Plan, apply, and verify safe ETABS frame write-back operations.

The module intentionally keeps planning independent from execution.  A plan is
serializable and reviewable before any ETABS mutation is attempted.  Execution
is opt-in and is expected to run against a saved working copy of the model.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
import hashlib
import json
import shutil
from typing import Any, Iterable, Mapping, Sequence

from src.core.beam_line_span_analyzer import BeamLineSpanResult, BeamSpan
from src.core.etabs_connector import api_values
from src.core.geometry_extractor import BeamGeometry
from src.core.numbering import canonical_object_part_id, canonical_span_id
from src.utils.geometry import distance


class WritebackPlanError(RuntimeError):
    """Raised when a write-back plan is internally inconsistent."""


class WritebackExecutionError(RuntimeError):
    """Raised when an ETABS write-back operation fails."""


@dataclass(frozen=True)
class FrameState:
    """Small identity/connectivity snapshot used for planning and verification."""

    name: str
    guid: str
    point_i: str
    point_j: str
    label: str = ""
    story: str = ""
    section: str = ""


@dataclass(frozen=True)
class OrientationAction:
    """One potential I/J reversal."""

    name: str
    guid: str
    old_point_i: str
    old_point_j: str
    new_point_i: str
    new_point_j: str
    reverse: bool
    status: str = "READY"
    reason: str = ""


@dataclass(frozen=True)
class RenameAction:
    """One source Object to final Unique Name mapping."""

    name: str
    guid: str
    final_name: str
    span_id: str
    segment_index: int
    status: str = "READY"
    reason: str = ""
    temporary_name: str = ""


@dataclass(frozen=True)
class WritebackPlan:
    """Reviewable write-back plan containing independent transactions."""

    orientations: tuple[OrientationAction, ...] = ()
    renames: tuple[RenameAction, ...] = ()
    records: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return any(action.status != "READY" for action in (*self.orientations, *self.renames))


@dataclass(frozen=True)
class VerificationResult:
    """Result of a post-write verification pass."""

    ok: bool
    checked: int
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrameAuditState:
    """Write-sensitive Frame state keyed by immutable ETABS GUID."""

    name: str
    guid: str
    point_i: str
    point_j: str
    transformation_matrix: tuple[float, ...]
    attributes: Mapping[str, Any]


@dataclass(frozen=True)
class AuditComparison:
    """Full pre/post audit result for a writeback transaction."""

    ok: bool
    checked: int
    failures: tuple[str, ...] = ()


def model_file_hash(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """Return a SHA-256 hash for a model copy/backup audit record."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def create_working_copy(
    source_path: str | Path,
    output_dir: str | Path,
    *,
    timestamp: datetime | None = None,
) -> Path:
    """Copy an EDB and available sidecars to a new, non-overwriting path.

    The source is never opened for writing.  The returned path is suitable for
    ``SapModel.File.OpenFile`` after the copy hash has been recorded.
    """

    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"找不到 ETABS 模型：{source}")
    if source.suffix.casefold() != ".edb":
        raise ValueError(f"工作副本必须来自 .EDB 文件：{source}")
    destination_dir = Path(output_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = (timestamp or datetime.now()).strftime("%Y%m%d_%H%M%S")
    destination = destination_dir / f"{source.stem}_writeback_{stamp}.edb"
    if destination.exists():
        raise FileExistsError(f"拒绝覆盖既有工作副本：{destination}")
    shutil.copy2(source, destination)
    # ETABS may use the .$et text backup when reopening a model.  Copy it if
    # present, but do not require it because EDB-only models are valid.
    for suffix in (".$et", ".ebk"):
        sidecar = source.with_suffix(suffix)
        if sidecar.is_file():
            shutil.copy2(sidecar, destination.with_suffix(suffix))
    return destination


def plan_orientation(
    beams: Iterable[BeamGeometry],
    states: Mapping[str, FrameState],
    *,
    preferred_direction: tuple[float, float] | None = None,
    desired_paths: Mapping[str, Sequence[tuple[float, float, float]]] | None = None,
    tolerance: float = 1e-9,
) -> tuple[OrientationAction, ...]:
    """Plan I/J reversals using geometry, never by coordinate sign alone.

    ``preferred_direction`` is a project/drawing positive vector.  When it is
    omitted, near-horizontal members use +X, near-vertical members use +Y, and
    diagonal members use their dominant global component.  The function only
    plans; it never calls ETABS.
    """

    actions: list[OrientationAction] = []
    for beam in beams:
        state = states.get(beam.name)
        if state is None:
            actions.append(
                OrientationAction(
                    beam.name,
                    "",
                    beam.point_i,
                    beam.point_j,
                    beam.point_i,
                    beam.point_j,
                    False,
                    "BLOCKED",
                    "缺少 Frame GUID/连接快照。",
                )
            )
            continue
        current_i = beam.start if state.point_i == beam.point_i else beam.end
        current_j = beam.end if state.point_i == beam.point_i else beam.start
        path = desired_paths.get(beam.name) if desired_paths else None
        if path and len(path) >= 2:
            reverse = _path_position(path, current_i) > _path_position(path, current_j) + tolerance
        else:
            dx = current_j[0] - current_i[0]
            dy = current_j[1] - current_i[1]
            reverse = _should_reverse(dx, dy, preferred_direction, tolerance)
        actions.append(
            OrientationAction(
                name=beam.name,
                guid=state.guid,
                old_point_i=state.point_i,
                old_point_j=state.point_j,
                new_point_i=state.point_j if reverse else state.point_i,
                new_point_j=state.point_i if reverse else state.point_j,
                reverse=reverse,
                reason="按项目正方向整理" if reverse else "已符合项目正方向",
            )
        )
    return tuple(actions)


def plan_rename(
    result: BeamLineSpanResult,
    states: Mapping[str, FrameState],
    *,
    confirmed_multi_section_span_ids: set[str] | None = None,
) -> tuple[RenameAction, ...]:
    """Plan names while blocking multi-Span and unconfirmed mixed sections."""

    confirmed_multi_section_span_ids = confirmed_multi_section_span_ids or set()
    occurrences: dict[str, list[str]] = defaultdict(list)
    sections_by_span: dict[str, set[str]] = defaultdict(set)
    candidates: list[tuple[str, str, int, int, str]] = []
    for span in result.spans:
        span_id = _canonical_span_id(span)
        for index, source_name in enumerate(span.source_objects, start=1):
            occurrences[source_name].append(span_id)
            candidates.append((source_name, span_id, index, span.line_number, span.beam_type))
            state = states.get(source_name)
            if state is not None:
                sections_by_span[span_id].add(state.section.strip() or "<NONE>")

    actions: list[RenameAction] = []
    for source_name, span_id, segment_index, _, _ in candidates:
        state = states.get(source_name)
        if state is None:
            actions.append(
                RenameAction(
                    source_name,
                    "",
                    "",
                    span_id,
                    segment_index,
                    "BLOCKED",
                    "缺少 Frame GUID/连接快照。",
                )
            )
            continue
        if len(occurrences[source_name]) > 1:
            actions.append(
                RenameAction(
                    source_name,
                    state.guid,
                    "",
                    span_id,
                    segment_index,
                    "REQUIRES_SPLIT",
                    "同一 ETABS Object 贡献多个结构 Span，不能静默改名。",
                )
            )
            continue
        if len(sections_by_span[span_id]) > 1 and span_id not in confirmed_multi_section_span_ids:
            actions.append(
                RenameAction(
                    source_name,
                    state.guid,
                    "",
                    span_id,
                    segment_index,
                    "REQUIRES_SECTION_REVIEW",
                    "同一结构跨包含多个截面："
                    + ", ".join(sorted(sections_by_span[span_id]))
                    + "；须人工确认 step beam 后才能写回。",
                )
            )
            continue
        final_name = canonical_object_part_id(span_id, segment_index)
        actions.append(RenameAction(source_name, state.guid, final_name, span_id, segment_index))
    return tuple(actions)


def build_writeback_plan(
    result: BeamLineSpanResult,
    beams: Iterable[BeamGeometry],
    states: Mapping[str, FrameState],
    *,
    preferred_direction: tuple[float, float] | None = None,
    confirmed_multi_section_span_ids: set[str] | None = None,
) -> WritebackPlan:
    """Build both independent transactions and explain blocked actions."""

    beam_list = tuple(beams)
    beam_by_name = {beam.name: beam for beam in beam_list}
    desired_paths: dict[str, Sequence[tuple[float, float, float]]] = {}
    for line in result.lines:
        path_parts = tuple(getattr(line, "path_parts", ()) or (line.path_points,))
        for source_name in line.source_objects:
            source = beam_by_name.get(source_name)
            selected_path = line.path_points
            if len(path_parts) > 1 and source is not None:
                selected_path = next(
                    (
                        path
                        for path in path_parts
                        if any(distance(point, source.start) <= 1.0 for point in path)
                        and any(distance(point, source.end) <= 1.0 for point in path)
                    ),
                    source.path_points or (source.start, source.end),
                )
            desired_paths.setdefault(source_name, selected_path)
    orientations = plan_orientation(
        beam_list,
        states,
        preferred_direction=preferred_direction,
        desired_paths=desired_paths,
    )
    renames = plan_rename(
        result,
        states,
        confirmed_multi_section_span_ids=confirmed_multi_section_span_ids,
    )
    section_review_names = {action.name for action in renames if action.status == "REQUIRES_SECTION_REVIEW"}
    orientations = tuple(
        (
            replace(
                action,
                status="REQUIRES_SECTION_REVIEW",
                reason="所属结构跨包含多个截面，须人工确认后才能整理 I/J。",
            )
            if action.name in section_review_names and action.status == "READY"
            else action
        )
        for action in orientations
    )
    records = tuple(
        f"{action.status}: {action.name} -> {action.span_id} ({action.reason})"
        for action in renames
        if action.status != "READY"
    )
    return WritebackPlan(orientations, renames, records)


def exclude_writeback_objects(
    plan: WritebackPlan,
    names: set[str],
    *,
    status: str = "REQUIRES_DEMAND_REVIEW",
    reason: str = "方向反转后设计需求超出等价容差，须人工复核。",
) -> WritebackPlan:
    """Fail closed for model-specific objects discovered by verification."""

    orientations = tuple(
        replace(action, status=status, reason=reason) if action.name in names and action.status == "READY" else action
        for action in plan.orientations
    )
    renames = tuple(
        replace(action, status=status, reason=reason) if action.name in names and action.status == "READY" else action
        for action in plan.renames
    )
    records = plan.records + tuple(f"{status}: {name} ({reason})" for name in sorted(names))
    return WritebackPlan(orientations, renames, records)


class EtabsWriteback:
    """Execute an already reviewed plan against a supplied SapModel."""

    def __init__(self, sap_model: Any) -> None:
        self.sap = sap_model

    def snapshot(self, names: Iterable[str]) -> dict[str, FrameState]:
        """Read identity/connectivity state without mutating ETABS."""

        states: dict[str, FrameState] = {}
        for name in names:
            point_i, point_j = api_values(self.sap.FrameObj.GetPoints(name, None, None), f"GetPoints({name})")
            guid = api_values(self.sap.FrameObj.GetGUID(name, None), f"GetGUID({name})")[0]
            label, story = api_values(self.sap.FrameObj.GetLabelFromName(name, None, None), f"GetLabelFromName({name})")
            section, _ = api_values(self.sap.FrameObj.GetSection(name, None, None), f"GetSection({name})")
            states[str(name)] = FrameState(
                str(name),
                str(guid),
                str(point_i),
                str(point_j),
                str(label),
                str(story),
                str(section),
            )
        return states

    def snapshot_from_geometry(self, beams: Iterable[BeamGeometry]) -> dict[str, FrameState]:
        """Build writeback state from an existing bulk geometry snapshot.

        Geometry extraction already owns the authoritative connectivity,
        label/story and section values for this run.  Re-reading those fields
        object-by-object multiplies COM round trips on large models; only GUID
        is unavailable from ``FrameObj.GetAllFrames`` and must be queried here.
        """

        states: dict[str, FrameState] = {}
        for beam in beams:
            guid = api_values(
                self.sap.FrameObj.GetGUID(beam.name, None),
                f"GetGUID({beam.name})",
            )[0]
            states[beam.name] = FrameState(
                beam.name,
                str(guid),
                beam.point_i,
                beam.point_j,
                beam.label,
                beam.story,
                beam.section,
            )
        return states

    def snapshot_audit(
        self,
        names: Iterable[str],
        *,
        concrete_overwrite_items: Sequence[int] = (*range(1, 14), 21),
    ) -> dict[str, FrameAuditState]:
        """Capture all properties that direction/name writeback must preserve.

        Optional ETABS assignments return code 1 when absent; those are stored
        as ``None``.  Unsupported or failed mandatory reads are retained as an
        explicit ``__error__`` value so verification fails closed.
        """

        from ETABSv1 import eFramePropType, eItemType

        audits: dict[str, FrameAuditState] = {}
        for name_value in names:
            name = str(name_value)
            guid = str(_required_values(self.sap.FrameObj.GetGUID(name, ""), "GUID")[0])
            point_i, point_j = _required_values(self.sap.FrameObj.GetPoints(name, "", ""), "points")
            matrix = tuple(
                float(value)
                for value in _required_values(
                    self.sap.FrameObj.GetTransformationMatrix(name, [], False),
                    "transformation_matrix",
                )[0]
            )
            section_values = _required_values(self.sap.FrameObj.GetSection(name, "", ""), "section")
            section = str(section_values[0])
            attributes = {
                "section": _normalise(section_values),
                "section_material": _audit_result(
                    lambda: self.sap.PropFrame.GetNameInPropFile(section, "", "", "", eFramePropType.Rectangular)
                ),
                "material_overwrite": _audit_result(
                    lambda: self.sap.FrameObj.GetMaterialOverwrite(name, ""),
                    optional_codes={1},
                ),
                "releases": _audit_result(lambda: self.sap.FrameObj.GetReleases(name, [], [], [], [])),
                "end_offsets": _audit_result(lambda: self.sap.FrameObj.GetEndLengthOffset(name, False, 0.0, 0.0, 0.0)),
                "groups": _sorted_assignment(_audit_result(lambda: self.sap.FrameObj.GetGroupAssign(name, 0, []))),
                "hinges": _audit_result(
                    lambda: self.sap.FrameObj.GetHingeAssigns_1(name, 0, [], [], [], [], [], [], [], []),
                    optional_codes={1},
                ),
                "spring": _audit_result(
                    lambda: self.sap.FrameObj.GetSpringAssignment(name, ""),
                    optional_codes={1},
                ),
                "load_distributed": _frame_load_result(
                    lambda: self.sap.FrameObj.GetLoadDistributed(
                        name,
                        0,
                        [],
                        [],
                        [],
                        [],
                        [],
                        [],
                        [],
                        [],
                        [],
                        [],
                        [],
                        eItemType.Objects,
                    )
                ),
                "load_point": _frame_load_result(
                    lambda: self.sap.FrameObj.GetLoadPoint(name, 0, [], [], [], [], [], [], [], [], eItemType.Objects)
                ),
                "load_temperature": _frame_load_result(
                    lambda: self.sap.FrameObj.GetLoadTemperature(name, 0, [], [], [], [], [], eItemType.Objects)
                ),
                "concrete_design_overwrites": _concrete_overwrites(self.sap, name, concrete_overwrite_items),
            }
            audits[guid] = FrameAuditState(
                name,
                guid,
                str(point_i),
                str(point_j),
                matrix,
                attributes,
            )
        return audits

    def rollback_completed(
        self,
        orientations: Sequence[OrientationAction],
        renames: Sequence[RenameAction],
        *,
        allow_mutation: bool = False,
    ) -> VerificationResult:
        """Roll back a fully applied rename/orientation transaction."""

        _require_mutation_flag(allow_mutation)
        changed_names = [action for action in renames if action.status == "READY" and action.name != action.final_name]
        failures: list[str] = []
        temporary: list[tuple[RenameAction, str]] = []
        for index, action in enumerate(changed_names):
            temp = f"__codex_rb_{index:05d}_{action.guid[:12]}"
            try:
                _check_code(
                    self.sap.FrameObj.ChangeName(action.final_name, temp),
                    f"rollback stage1 {action.final_name}->{temp}",
                )
                temporary.append((action, temp))
            except Exception as exc:
                failures.append(f"rename stage1 {action.final_name}: {exc}")
        for action, temp in temporary:
            try:
                _check_code(
                    self.sap.FrameObj.ChangeName(temp, action.name),
                    f"rollback stage2 {temp}->{action.name}",
                )
            except Exception as exc:
                failures.append(f"rename stage2 {temp}->{action.name}: {exc}")
        for action in reversed(orientations):
            if action.status != "READY" or not action.reverse:
                continue
            try:
                _check_code(
                    self.sap.EditFrame.ChangeConnectivity(action.name, action.old_point_i, action.old_point_j),
                    f"rollback ChangeConnectivity({action.name})",
                )
            except Exception as exc:
                failures.append(f"orientation {action.name}: {exc}")
        return VerificationResult(not failures, len(orientations) + len(renames), tuple(failures))

    def apply_orientation(
        self,
        actions: Sequence[OrientationAction],
        *,
        allow_mutation: bool = False,
    ) -> VerificationResult:
        """Apply reversals and roll back already-applied actions on failure."""

        _require_mutation_flag(allow_mutation)
        applied: list[OrientationAction] = []
        try:
            for action in actions:
                if action.status != "READY" or not action.reverse:
                    continue
                _check_code(
                    self.sap.EditFrame.ChangeConnectivity(action.name, action.new_point_i, action.new_point_j),
                    f"ChangeConnectivity({action.name})",
                )
                applied.append(action)
        except Exception as exc:
            for action in reversed(applied):
                try:
                    _check_code(
                        self.sap.EditFrame.ChangeConnectivity(action.name, action.old_point_i, action.old_point_j),
                        f"rollback ChangeConnectivity({action.name})",
                    )
                except Exception as rollback_exc:
                    raise WritebackExecutionError(f"方向修改失败且回滚失败：{action.name}: {rollback_exc}") from exc
            raise WritebackExecutionError("方向修改失败，已回滚已执行动作。") from exc
        return self.verify_orientation(actions)

    def apply_rename(
        self,
        actions: Sequence[RenameAction],
        *,
        allow_mutation: bool = False,
    ) -> VerificationResult:
        """Rename through unique temporary names, with rollback on failure."""

        _require_mutation_flag(allow_mutation)
        all_ready = [action for action in actions if action.status == "READY"]
        if len(all_ready) != len(actions):
            raise WritebackPlanError("改名计划包含阻塞项，必须先处理 REQUIRES_SPLIT/BLOCKED。")
        ready = [action for action in all_ready if action.name != action.final_name]
        if not ready:
            return self.verify_rename(all_ready)
        _check_unique_names(ready)
        existing = set(self._frame_names())
        old_names = {action.name for action in ready}
        collisions = {action.final_name for action in ready} & (existing - old_names)
        if collisions:
            raise WritebackPlanError(f"最终名称已被未参与改名的对象占用：{sorted(collisions)}")

        temporary: list[tuple[RenameAction, str]] = []
        for index, action in enumerate(ready):
            temporary_name = f"__codex_wb_{index:05d}_{action.guid[:12]}"
            if temporary_name in existing:
                raise WritebackPlanError(f"临时名称冲突：{temporary_name}")
            temporary.append((action, temporary_name))

        staged: list[tuple[RenameAction, str]] = []
        finalised: list[tuple[RenameAction, str]] = []
        try:
            for action, temporary_name in temporary:
                _check_code(
                    self.sap.FrameObj.ChangeName(action.name, temporary_name),
                    f"ChangeName({action.name}->{temporary_name})",
                )
                staged.append((action, temporary_name))
            for action, temporary_name in temporary:
                _check_code(
                    self.sap.FrameObj.ChangeName(temporary_name, action.final_name),
                    f"ChangeName({temporary_name}->{action.final_name})",
                )
                finalised.append((action, temporary_name))
        except Exception as exc:
            rollback_failures = self._rollback_names(staged, finalised)
            if rollback_failures:
                raise WritebackExecutionError(
                    "改名失败且回滚失败；必须放弃当前工作副本：" + ", ".join(rollback_failures)
                ) from exc
            raise WritebackExecutionError("改名失败，已回滚两阶段改名。") from exc
        return self.verify_rename(ready)

    def verify_orientation(
        self,
        actions: Sequence[OrientationAction],
        *,
        name_resolver: Mapping[str, str] | None = None,
    ) -> VerificationResult:
        """Verify I/J connectivity.

        ``name_resolver`` is used when verification runs after a rename
        transaction.  The action keeps the original Object name for audit
        purposes, while ETABS must be queried with its current name.  Without
        the resolver, a successful rename is incorrectly reported as a
        ``GetPoints(old_name)`` failure after reopening the model.
        """
        failures: list[str] = []
        checked = 0
        for action in actions:
            if action.status != "READY":
                continue
            checked += 1
            api_name = (name_resolver or {}).get(action.name, action.name)
            try:
                point_i, point_j = api_values(
                    self.sap.FrameObj.GetPoints(api_name, None, None),
                    f"GetPoints({api_name})",
                )
                if (str(point_i), str(point_j)) != (
                    action.new_point_i,
                    action.new_point_j,
                ):
                    failures.append(
                        f"{action.name}（当前名称 {api_name}）: "
                        f"连接为 {point_i}->{point_j}，期望 "
                        f"{action.new_point_i}->{action.new_point_j}"
                    )
            except Exception as exc:
                failures.append(f"{action.name}: 读取连接失败：{exc}")
        return VerificationResult(not failures, checked, tuple(failures))

    def verify_rename(self, actions: Sequence[RenameAction]) -> VerificationResult:
        failures: list[str] = []
        checked = 0
        for action in actions:
            if action.status != "READY":
                continue
            checked += 1
            try:
                guid = str(
                    api_values(
                        self.sap.FrameObj.GetGUID(action.final_name, None),
                        f"GetGUID({action.final_name})",
                    )[0]
                )
                if guid != action.guid:
                    failures.append(f"{action.final_name}: GUID 变为 {guid}，期望 {action.guid}")
            except Exception as exc:
                failures.append(f"{action.name}->{action.final_name}: 改名后读取失败：{exc}")
        return VerificationResult(not failures, checked, tuple(failures))

    def _frame_names(self) -> list[str]:
        values = api_values(self.sap.FrameObj.GetNameList(0, None), "FrameObj.GetNameList")
        return [str(name) for name in list(values[1])]

    def _rollback_names(
        self,
        temporary: Sequence[tuple[RenameAction, str]],
        finalised: Sequence[tuple[RenameAction, str]],
    ) -> tuple[str, ...]:
        finalised_by_temp = {temporary_name: action for action, temporary_name in finalised}
        failures: list[str] = []
        for action, temporary_name in reversed(temporary):
            current = action.final_name if temporary_name in finalised_by_temp else temporary_name
            try:
                _check_code(
                    self.sap.FrameObj.ChangeName(current, action.name),
                    f"rollback ChangeName({current}->{action.name})",
                )
            except Exception as exc:
                failures.append(f"{current}->{action.name}: {exc}")
        return tuple(failures)


def _check_code(result: Any, context: str) -> None:
    api_values(result, context)


def compare_frame_audits(
    before: Mapping[str, FrameAuditState],
    after: Mapping[str, FrameAuditState],
    *,
    renamed: Mapping[str, str] | None = None,
    reversed_guids: set[str] | None = None,
    tolerance: float = 1e-7,
) -> AuditComparison:
    """Compare immutable identity and every write-sensitive Frame attribute."""

    renamed = renamed or {}
    reversed_guids = reversed_guids or set()
    failures: list[str] = []
    if set(before) != set(after):
        missing = sorted(set(before) - set(after))
        added = sorted(set(after) - set(before))
        failures.append(f"GUID 集合变化：missing={missing}, added={added}")
    for guid in sorted(set(before) & set(after)):
        old = before[guid]
        new = after[guid]
        expected_name = renamed.get(old.name, old.name)
        if new.name != expected_name:
            failures.append(f"{guid}: 名称 {new.name}，期望 {expected_name}")
        if guid in reversed_guids:
            if (new.point_i, new.point_j) != (old.point_j, old.point_i):
                failures.append(f"{guid}: I/J 未按预期交换")
            if not _matrix_has_reversed_axis(old.transformation_matrix, new.transformation_matrix, tolerance):
                failures.append(f"{guid}: 变换矩阵未体现局部 1 轴反向")
        else:
            if (new.point_i, new.point_j) != (old.point_i, old.point_j):
                failures.append(f"{guid}: 非目标对象 I/J 发生变化")
            if not _equivalent(old.transformation_matrix, new.transformation_matrix, tolerance):
                failures.append(f"{guid}: 非目标对象变换矩阵发生变化")
        for key in sorted(set(old.attributes) | set(new.attributes)):
            old_value = old.attributes.get(key, "<MISSING>")
            new_value = new.attributes.get(key, "<MISSING>")
            if _contains_error(old_value) or _contains_error(new_value):
                failures.append(f"{guid}: {key} 存在不可读取值")
            elif guid in reversed_guids and key == "end_offsets":
                if not _end_offsets_reversed(old_value, new_value, tolerance):
                    failures.append(f"{guid}: end_offsets 未按 I/J 交换")
            elif guid in reversed_guids and key == "releases":
                if not _releases_reversed(old_value, new_value, tolerance):
                    failures.append(f"{guid}: releases 未按 I/J 交换")
            elif not _equivalent(old_value, new_value, tolerance):
                failures.append(f"{guid}: {key} 写前/写后不一致")
    return AuditComparison(not failures, len(set(before) & set(after)), tuple(failures))


def compare_design_envelopes(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    renamed: Mapping[str, str] | None = None,
    relative_tolerances: Sequence[float] = (0.02, 0.02, 0.02, 0.02, 0.02, 0.002, 0.002),
    absolute_tolerances: Sequence[float] = (10.0, 10.0, 10.0, 10.0, 0.05, 1.0, 0.001),
) -> AuditComparison:
    """Verify that I/J cleanup only reverses station order, not demand envelopes."""

    renamed = renamed or {}
    failures: list[str] = []
    checked = 0
    for old_name, old_result in before.items():
        new_name = renamed.get(old_name, old_name)
        new_result = after.get(new_name)
        if new_result is None:
            failures.append(f"{old_name}: 写后缺少 {new_name} 设计结果")
            continue
        checked += 1
        if str(old_result.status) != str(new_result.status):
            failures.append(f"{old_name}: 设计状态 {old_result.status} -> {new_result.status}")
            continue
        old_envelope = _design_envelope(old_result)
        new_envelope = _design_envelope(new_result)
        differences = tuple(
            _demand_difference_exceeds(
                old,
                new,
                relative_tolerances[index],
                absolute_tolerances[index],
            )
            for index, (old, new) in enumerate(zip(old_envelope, new_envelope))
        )
        if any(differences):
            failures.append(f"{old_name}->{new_name}: 需求包络变化 " f"before={old_envelope}, after={new_envelope}")
    extra = sorted(set(after) - {renamed.get(name, name) for name in before})
    if extra:
        failures.append(f"写后出现未映射设计对象：{extra[:20]}")
    return AuditComparison(not failures, checked, tuple(failures))


def _demand_difference_exceeds(
    before: float,
    after: float,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> bool:
    difference = abs(before - after)
    relative = difference / max(1.0, abs(before), abs(after))
    return difference > absolute_tolerance and relative > relative_tolerance


def _design_envelope(result: Any) -> tuple[float, ...]:
    stations = tuple(getattr(result, "stations", ()))
    selectors = (
        lambda station: station.top_flexural_area,
        lambda station: station.bottom_flexural_area,
        lambda station: station.as_total_top_mm2,
        lambda station: station.as_total_bottom_mm2,
        lambda station: station.shear_area,
        lambda station: station.torsion_longitudinal_area,
        lambda station: station.torsion_trans_area,
    )
    return tuple(max((float(selector(station)) for station in stations), default=0.0) for selector in selectors)


def write_frame_audit(
    path: str | Path,
    before: Mapping[str, FrameAuditState],
    after: Mapping[str, FrameAuditState],
    comparison: AuditComparison,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "before": {guid: asdict(value) for guid, value in before.items()},
                "after": {guid: asdict(value) for guid, value in after.items()},
                "comparison": asdict(comparison),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def _required_values(result: Any, context: str) -> tuple[Any, ...]:
    values = tuple(result) if isinstance(result, tuple) else (result,)
    if not values or int(values[0]) != 0:
        raise WritebackExecutionError(f"{context}: ETABS 返回 {values[:1]}")
    return values[1:]


def _audit_result(call: Any, optional_codes: set[int] | None = None) -> Any:
    try:
        result = call()
        values = tuple(result) if isinstance(result, tuple) else (result,)
        code = int(values[0]) if values else -1
        if code == 0:
            return _normalise(values[1:])
        if code in (optional_codes or set()):
            return None
        return {"__error__": f"ETABS return code {code}"}
    except Exception as exc:
        return {"__error__": f"{type(exc).__name__}: {exc}"}


def _frame_load_result(call: Any) -> Any:
    result = _audit_result(call)
    if isinstance(result, dict) or result is None:
        return result
    values = list(result)
    if len(values) >= 2:
        # Drop NumberItems and FrameName; the latter changes during a valid rename.
        values = values[2:]
    return _normalise(values)


def _concrete_overwrites(sap: Any, name: str, items: Sequence[int]) -> Any:
    try:
        interface = sap.DesignConcrete.ACI318_14
    except Exception as exc:
        return {"__error__": f"ACI318_14 unavailable: {exc}"}
    values: dict[str, Any] = {}
    for item in items:
        result = _audit_result(
            lambda item=item: interface.GetOverwrite(name, item, 0.0, False),
            optional_codes={1},
        )
        if result is not None and not _contains_error(result):
            if isinstance(result, (list, tuple)) and len(result) >= 2:
                value, program_determined = result[0], bool(result[1])
                values[str(item)] = (
                    {"program_determined": True}
                    if program_determined
                    else {"program_determined": False, "value": value}
                )
            else:
                values[str(item)] = result
    return values or {"__error__": "no readable ACI318_14 overwrite items"}


def _sorted_assignment(value: Any) -> Any:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return (value[0], tuple(sorted(value[1])))
    return value


def _normalise(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return tuple(_normalise(item) for item in value)
    except TypeError:
        return str(value)


def _contains_error(value: Any) -> bool:
    if isinstance(value, Mapping):
        return "__error__" in value or any(_contains_error(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_error(item) for item in value)
    return False


def _equivalent(left: Any, right: Any, tolerance: float) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= tolerance * max(1.0, abs(float(left)), abs(float(right)))
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(_equivalent(left[key], right[key], tolerance) for key in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(_equivalent(a, b, tolerance) for a, b in zip(left, right))
    return left == right


def _matrix_has_reversed_axis(before: Sequence[float], after: Sequence[float], tolerance: float) -> bool:
    if len(before) != 9 or len(after) != 9:
        return False
    candidates = ((0, 1, 2), (0, 3, 6))
    return any(all(abs(after[index] + before[index]) <= tolerance for index in indexes) for indexes in candidates)


def _end_offsets_reversed(before: Any, after: Any, tolerance: float) -> bool:
    if not isinstance(before, (list, tuple)) or not isinstance(after, (list, tuple)):
        return False
    if len(before) != 4 or len(after) != 4:
        return False
    expected = (before[0], before[2], before[1], before[3])
    return _equivalent(expected, after, tolerance)


def _releases_reversed(before: Any, after: Any, tolerance: float) -> bool:
    if not isinstance(before, (list, tuple)) or not isinstance(after, (list, tuple)):
        return False
    if len(before) != 4 or len(after) != 4:
        return False
    expected = (before[1], before[0], before[3], before[2])
    return _equivalent(expected, after, tolerance)


def _require_mutation_flag(allow_mutation: bool) -> None:
    if not allow_mutation:
        raise WritebackPlanError("模型写回默认关闭；必须显式传入 allow_mutation=True。")


def _check_unique_names(actions: Sequence[RenameAction]) -> None:
    final_names = [action.final_name for action in actions]
    if len(final_names) != len(set(final_names)):
        raise WritebackPlanError("改名计划中存在重复最终名称。")


def _should_reverse(
    dx: float,
    dy: float,
    preferred_direction: tuple[float, float] | None,
    tolerance: float,
) -> bool:
    if preferred_direction is not None:
        px, py = preferred_direction
        dot = dx * px + dy * py
        return dot < -tolerance
    if abs(dx) >= abs(dy):
        return dx < -tolerance
    return dy < -tolerance


def _path_position(path: Sequence[tuple[float, float, float]], point: tuple[float, float, float]) -> float:
    """Return the approximate along-path position of a point."""

    best_position = 0.0
    best_distance = float("inf")
    cumulative = 0.0
    for start, end in zip(path, path[1:]):
        vx, vy, vz = end[0] - start[0], end[1] - start[1], end[2] - start[2]
        length_sq = vx * vx + vy * vy + vz * vz
        if length_sq <= 1e-18:
            continue
        wx, wy, wz = point[0] - start[0], point[1] - start[1], point[2] - start[2]
        fraction = max(0.0, min(1.0, (wx * vx + wy * vy + wz * vz) / length_sq))
        projection = (start[0] + fraction * vx, start[1] + fraction * vy, start[2] + fraction * vz)
        distance_sq = sum((point[index] - projection[index]) ** 2 for index in range(3))
        segment_length = length_sq**0.5
        if distance_sq < best_distance:
            best_distance = distance_sq
            best_position = cumulative + fraction * segment_length
        cumulative += segment_length
    return best_position


def _canonical_span_id(span: BeamSpan) -> str:
    return canonical_span_id(span.story, span.beam_type, span.line_number, span.span_index)
