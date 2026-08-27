"""Tower-assignment preflight and review-plan domain logic.

The module deliberately contains no ETABS/COM calls.  Adapters provide the
public DatabaseTables rows and this module turns those rows into a stable,
auditable report.  A missing or ambiguous tower is a review condition; the
algorithm never silently moves an object to another tower.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


TOWER_REPORT_SCHEMA = "etabs-beam-generator/tower-assignment@1"
TOWER_GROUP_PREFIX = "CODX_TOWER_CHECK_"

PASS = "PASS"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class TowerStoryCatalog:
    """One Tower/Story definition as returned by ETABS."""

    tower: str
    story: str
    elevation: float | None = None
    height: float | None = None
    source: str = "Story Definitions"


@dataclass(frozen=True)
class TowerObjectRecord:
    """Normalized assignment decision for one frame or point object."""

    object_type: str
    name: str
    guid: str = ""
    story: str = ""
    actual_tower: str = ""
    expected_tower: str = ""
    candidate_towers: tuple[str, ...] = ()
    status: str = PASS
    reason: str = ""
    point_i: str = ""
    point_j: str = ""
    group_name: str = ""


@dataclass(frozen=True)
class TowerGroupPlan:
    """Review Group contents, grouped by the expected Tower."""

    group_name: str
    expected_tower: str = ""
    frame_names: tuple[str, ...] = ()
    point_names: tuple[str, ...] = ()
    frame_guids: tuple[str, ...] = ()
    point_guids: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class TowerAssignmentReport:
    """Serializable Tower preflight result."""

    schema: str = TOWER_REPORT_SCHEMA
    status: str = PASS
    selection: str = "all"
    checked_beams: int = 0
    checked_points: int = 0
    issue_count: int = 0
    records: tuple[TowerObjectRecord, ...] = ()
    groups: tuple[TowerGroupPlan, ...] = ()
    catalog: tuple[TowerStoryCatalog, ...] = ()
    active_tower_before: str = ""
    active_tower_after: str = ""
    diagnostics: tuple[str, ...] = ()
    model_identity: Mapping[str, Any] | None = None

    @property
    def review_required(self) -> bool:
        return self.status == REVIEW_REQUIRED

    @property
    def blocked(self) -> bool:
        return self.status == BLOCKED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TowerAssignmentChecker:
    """Small façade used by OAPI and snapshot adapters.

    The checker deliberately accepts already-read table rows so callers cannot
    accidentally mix a Tower read with a different model snapshot.  Keeping
    this façade separate from the pure function also gives the plugin a stable
    named entry point while preserving deterministic, offline-testable logic.
    """

    def __init__(
        self,
        *,
        catalog: Iterable[TowerStoryCatalog] | None = None,
        model_identity: Mapping[str, Any] | None = None,
    ) -> None:
        self.catalog = tuple(catalog or ())
        self.model_identity = model_identity

    def check(
        self,
        *,
        story_rows: Any,
        point_rows: Any,
        beam_rows: Any,
        column_rows: Any = (),
        brace_rows: Any = (),
        selection: str = "all",
        selected_beams: Iterable[str] | None = None,
        active_tower_before: str = "",
        active_tower_after: str = "",
        diagnostics: Iterable[str] = (),
    ) -> TowerAssignmentReport:
        return build_tower_assignment_report(
            story_rows=story_rows,
            point_rows=point_rows,
            beam_rows=beam_rows,
            column_rows=column_rows,
            brace_rows=brace_rows,
            selection=selection,
            selected_beams=selected_beams,
            catalog=self.catalog or None,
            active_tower_before=active_tower_before,
            active_tower_after=active_tower_after,
            model_identity=self.model_identity,
            diagnostics=diagnostics,
        )


def _rows(table: Any) -> tuple[dict[str, str], ...]:
    """Accept an OapiTable, a mapping, or a sequence of mappings."""

    if table is None:
        return ()
    if isinstance(table, Mapping):
        return tuple({str(k): _text(v) for k, v in table.items()} for table in (table,))
    fields = getattr(table, "fields", None)
    values = getattr(table, "rows", None)
    if fields is not None and values is not None:
        names = tuple(str(field) for field in fields)
        return tuple(
            {name: _text(value) for name, value in zip(names, row)}
            for row in values
        )
    if isinstance(table, Sequence) and not isinstance(table, (str, bytes)):
        return tuple(
            {str(k): _text(v) for k, v in row.items()}
            for row in table
            if isinstance(row, Mapping)
        )
    raise TypeError(f"不支持的 ETABS 表类型：{type(table).__name__}")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _first(row: Mapping[str, str], *names: str) -> str:
    lowered = {str(key).casefold(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.casefold())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _number(row: Mapping[str, str], *names: str) -> float | None:
    raw = _first(row, *names)
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _story_token(value: str) -> str:
    """Normalize ETABS frame story labels such as ``T1-L3`` to ``L3``.

    Frame connectivity commonly prefixes a local story with the Tower name,
    while Point Object Connectivity stores only the local story.  Removing a
    single Tower prefix avoids treating that documented representation as a
    topology error; genuine endpoint disagreement still fails the check.
    """

    text = _text(value)
    return text.rsplit("-", 1)[-1] if "-" in text else text


def safe_tower_name(tower: str) -> str:
    """Make a deterministic native ETABS Group suffix."""

    text = re.sub(r"[^0-9A-Za-z_\-]+", "_", _text(tower)) or "UNRESOLVED"
    text = text.strip(" _.") or "UNRESOLVED"
    name = f"{TOWER_GROUP_PREFIX}EXPECTED_{text}"
    if len(name) <= 79:
        return name
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{name[:70]}_{digest}"[:79]


def unresolved_group_name() -> str:
    return f"{TOWER_GROUP_PREFIX}UNRESOLVED"


def _catalog_index(catalog: Iterable[TowerStoryCatalog]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for item in catalog:
        result.setdefault(item.story, set()).add(item.tower)
    return result


def _catalog_from_rows(rows: Iterable[Mapping[str, str]]) -> tuple[TowerStoryCatalog, ...]:
    result: list[TowerStoryCatalog] = []
    for row in rows:
        tower = _first(row, "Tower", "TowerName")
        story = _first(row, "Story", "StoryName")
        if not tower or not story:
            continue
        result.append(
            TowerStoryCatalog(
                tower=tower,
                story=story,
                elevation=_number(row, "Elevation", "Z", "TopElevation", "BottomElevation"),
                height=_number(row, "Height", "StoryHeight"),
            )
        )
    return tuple(dict.fromkeys(result))


def _endpoint_graph(
    tables: Iterable[Iterable[Mapping[str, str]]],
) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for rows in tables:
        for row in rows:
            i = _first(row, "UniquePtI", "PointI", "PointObjI", "IEndPoint")
            j = _first(row, "UniquePtJ", "PointJ", "PointObjJ", "JEndPoint")
            if not i or not j:
                continue
            graph.setdefault(i, set()).add(j)
            graph.setdefault(j, set()).add(i)
    return graph


def _infer_point_expectations(
    point_rows: tuple[dict[str, str], ...],
    catalog: tuple[TowerStoryCatalog, ...],
    graph: Mapping[str, set[str]],
) -> dict[str, tuple[str, tuple[str, ...], str]]:
    """Return point -> (expected, candidates, reason)."""

    by_story = _catalog_index(catalog)
    points = {
        _first(row, "UniqueName", "Name", "Point"):
        row
        for row in point_rows
        if _first(row, "UniqueName", "Name", "Point")
    }
    actual = {
        name: _first(row, "Tower", "TowerName")
        for name, row in points.items()
    }
    result: dict[str, tuple[str, tuple[str, ...], str]] = {}
    for name, row in points.items():
        story = _first(row, "Story", "StoryName")
        candidates = set(by_story.get(story, ()))
        z = _number(row, "Z", "GlobalZ")
        if z is not None:
            elevation_matches = {
                item.tower
                for item in catalog
                if item.story == story
                and item.elevation is not None
                and abs(item.elevation - z) <= 1.0
            }
            if elevation_matches:
                candidates &= elevation_matches
        votes: dict[str, int] = {tower: 0 for tower in candidates}
        for neighbour in graph.get(name, ()):
            neighbour_tower = actual.get(neighbour, "")
            if neighbour_tower in votes:
                votes[neighbour_tower] += 1
        if votes:
            best = max(votes.values())
            winners = tuple(sorted(tower for tower, count in votes.items() if count == best and best > 0))
        else:
            winners = ()
        if len(winners) == 1:
            expected = winners[0]
            reason = "由相连梁/柱/支撑端点的 Tower 多数推断。"
        elif len(candidates) == 1:
            expected = next(iter(candidates))
            reason = "Story/标高在 Tower 目录中唯一匹配。"
        elif not candidates:
            expected = ""
            reason = "Story 或标高无法匹配 Tower 目录。"
        else:
            expected = ""
            reason = "同名 Story 对应多个 Tower，拓扑多数不足以唯一判断。"
        result[name] = (expected, tuple(sorted(candidates)), reason)
    return result


def build_tower_assignment_report(
    *,
    story_rows: Any,
    point_rows: Any,
    beam_rows: Any,
    column_rows: Any = (),
    brace_rows: Any = (),
    selection: str = "all",
    selected_beams: Iterable[str] | None = None,
    catalog: Iterable[TowerStoryCatalog] | None = None,
    active_tower_before: str = "",
    active_tower_after: str = "",
    model_identity: Mapping[str, Any] | None = None,
    diagnostics: Iterable[str] = (),
) -> TowerAssignmentReport:
    """Build a fail-closed report from public OAPI table rows."""

    diagnostics_list = [str(item) for item in diagnostics if str(item).strip()]
    story = _rows(story_rows)
    points = _rows(point_rows)
    beams = _rows(beam_rows)
    columns = _rows(column_rows)
    braces = _rows(brace_rows)
    if not story:
        diagnostics_list.append("TOWER_TABLE_MISSING: Story Definitions")
    if not points:
        diagnostics_list.append("TOWER_TABLE_MISSING: Point Object Connectivity")
    if not beams:
        diagnostics_list.append("TOWER_TABLE_MISSING: Beam Object Connectivity")
    if diagnostics_list:
        return TowerAssignmentReport(
            status=BLOCKED,
            selection=selection,
            diagnostics=tuple(dict.fromkeys(diagnostics_list)),
            active_tower_before=active_tower_before,
            active_tower_after=active_tower_after,
            model_identity=model_identity,
        )

    catalog_tuple = tuple(catalog or _catalog_from_rows(story))
    graph = _endpoint_graph((beams, columns, braces))
    point_expectations = _infer_point_expectations(points, catalog_tuple, graph)
    point_by_name = {
        _first(row, "UniqueName", "Name", "Point"): row
        for row in points
        if _first(row, "UniqueName", "Name", "Point")
    }
    selected = {str(value).strip() for value in (selected_beams or ()) if str(value).strip()}
    beam_rows_checked = tuple(
        row for row in beams
        if selection != "selected" or _first(row, "UniqueName", "Name", "Frame") in selected
    )
    relevant_points: set[str] = set()
    records: list[TowerObjectRecord] = []
    for row in beam_rows_checked:
        name = _first(row, "UniqueName", "Name", "Frame")
        i = _first(row, "UniquePtI", "PointI", "PointObjI", "IEndPoint")
        j = _first(row, "UniquePtJ", "PointJ", "PointObjJ", "JEndPoint")
        relevant_points.update(value for value in (i, j) if value)
        i_row = point_by_name.get(i, {})
        j_row = point_by_name.get(j, {})
        i_actual = _first(i_row, "Tower", "TowerName")
        j_actual = _first(j_row, "Tower", "TowerName")
        actual_values = tuple(sorted({value for value in (i_actual, j_actual) if value}))
        i_expect = point_expectations.get(i, ("", (), ""))[0]
        j_expect = point_expectations.get(j, ("", (), ""))[0]
        expected_values = tuple(sorted({value for value in (i_expect, j_expect) if value}))
        story_name = _first(row, "Story", "StoryName")
        endpoint_stories = {
            _first(i_row, "Story", "StoryName"),
            _first(j_row, "Story", "StoryName"),
        } - {""}
        if (
            len(endpoint_stories) > 1
            or (story_name and endpoint_stories and _story_token(story_name) not in {_story_token(value) for value in endpoint_stories})
        ):
            status = "TOWER_STORY_MISMATCH"
            reason = "梁的 Story 与端点 Story 不一致。"
        elif len(actual_values) > 1:
            status = "TOWER_MIXED_ENDPOINT_ASSIGNMENT"
            reason = "梁两端点当前属于不同 Tower。"
        elif len(expected_values) != 1:
            status = "TOWER_EXPECTATION_UNRESOLVED"
            reason = "梁两端点的预期 Tower 无法唯一确定。"
        elif actual_values != expected_values:
            status = "TOWER_POINT_ASSIGNMENT_MISMATCH"
            reason = "梁端点当前 Tower 与按 Story/拓扑推断的预期 Tower 不一致。"
        else:
            status = PASS
            reason = "梁两端点 Tower 与预期一致。"
        records.append(
            TowerObjectRecord(
                object_type="Beam",
                name=name,
                guid=_first(row, "GUID", "Guid", "UniqueID"),
                story=story_name,
                actual_tower=(actual_values[0] if len(actual_values) == 1 else ";".join(actual_values)),
                expected_tower=(expected_values[0] if len(expected_values) == 1 else ""),
                candidate_towers=tuple(sorted(set(point_expectations.get(i, ("", (), ""))[1]) | set(point_expectations.get(j, ("", (), ""))[1]))),
                status=status,
                reason=reason,
                point_i=i,
                point_j=j,
            )
        )

    for name in sorted(relevant_points):
        row = point_by_name.get(name)
        if row is None:
            records.append(
                TowerObjectRecord(
                    object_type="Point",
                    name=name,
                    status="TOWER_TABLE_MISSING",
                    reason="梁端点在 Point Object Connectivity 中不存在。",
                )
            )
            continue
        expected, candidates, reason = point_expectations.get(name, ("", (), ""))
        actual = _first(row, "Tower", "TowerName")
        status = PASS if expected and actual == expected else (
            "TOWER_POINT_ASSIGNMENT_MISMATCH" if expected else "TOWER_EXPECTATION_UNRESOLVED"
        )
        if status != PASS:
            reason = (
                "点当前 Tower 与预期 Tower 不一致。"
                if expected
                else reason
            )
        records.append(
            TowerObjectRecord(
                object_type="Point",
                name=name,
                guid=_first(row, "GUID", "Guid", "UniqueID"),
                story=_first(row, "Story", "StoryName"),
                actual_tower=actual,
                expected_tower=expected,
                candidate_towers=candidates,
                status=status,
                reason=reason,
            )
        )

    issues = [record for record in records if record.status != PASS]
    grouped: dict[str, dict[str, list[str]]] = {}
    expected_by_group: dict[str, str] = {}
    reasons: dict[str, list[str]] = {}
    for record in issues:
        expected = record.expected_tower or ""
        group = safe_tower_name(expected) if expected else unresolved_group_name()
        expected_by_group.setdefault(group, expected)
        bucket = grouped.setdefault(group, {"frames": [], "points": [], "frame_guids": [], "point_guids": []})
        key = "frames" if record.object_type.casefold() == "beam" else "points"
        guid_key = "frame_guids" if record.object_type.casefold() == "beam" else "point_guids"
        bucket[key].append(record.name)
        if record.guid:
            bucket[guid_key].append(record.guid)
        reasons.setdefault(group, []).append(record.reason)
    groups = tuple(
        TowerGroupPlan(
            group_name=name,
            expected_tower=expected_by_group.get(name, ""),
            frame_names=tuple(sorted(set(values["frames"]))),
            point_names=tuple(sorted(set(values["points"]))),
            frame_guids=tuple(sorted(set(values["frame_guids"]))),
            point_guids=tuple(sorted(set(values["point_guids"]))),
            reason="；".join(dict.fromkeys(reasons.get(name, ())))
        )
        for name, values in sorted(grouped.items())
    )
    group_by_object = {
        object_name: plan.group_name
        for plan in groups
        for object_name in (*plan.frame_names, *plan.point_names)
    }
    records = [replace(record, group_name=group_by_object.get(record.name, "")) for record in records]
    return TowerAssignmentReport(
        status=REVIEW_REQUIRED if issues else PASS,
        selection=selection,
        checked_beams=len(beam_rows_checked),
        checked_points=len(relevant_points),
        issue_count=len(issues),
        records=tuple(records),
        groups=groups,
        catalog=catalog_tuple,
        active_tower_before=active_tower_before,
        active_tower_after=active_tower_after,
        diagnostics=tuple(dict.fromkeys(diagnostics_list)),
        model_identity=model_identity,
    )


def write_tower_assignment_report(
    report: TowerAssignmentReport,
    output_dir: str | Path,
    run_id: str,
) -> tuple[Path, Path]:
    """Write the JSON and human-readable CSV audit artifacts."""

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"tower_assignment_check_{run_id}"
    json_path = directory / f"{stem}.json"
    csv_path = directory / f"{stem}.csv"
    json_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "ObjectType", "Name", "GUID", "Story", "ActualTower", "ExpectedTower",
                "Candidates", "Status", "Reason", "PointI", "PointJ", "ReviewGroup",
            ]
        )
        for record in report.records:
            group = next((plan.group_name for plan in report.groups if record.name in plan.frame_names or record.name in plan.point_names), "")
            writer.writerow(
                [
                    record.object_type, record.name, record.guid, record.story,
                    record.actual_tower, record.expected_tower, ";".join(record.candidate_towers),
                    record.status, record.reason, record.point_i, record.point_j, group,
                ]
            )
    return json_path, csv_path


def report_group_summary(report: TowerAssignmentReport) -> list[dict[str, Any]]:
    """Small UI-friendly table without exposing internal implementation details."""

    rows: list[dict[str, Any]] = []
    for plan in report.groups:
        actual: set[str] = set()
        for record in report.records:
            if record.name in plan.frame_names or record.name in plan.point_names:
                if record.actual_tower:
                    actual.update(value for value in record.actual_tower.split(";") if value)
        rows.append(
            {
                "expected_tower": plan.expected_tower or "UNRESOLVED",
                "current_tower": ";".join(sorted(actual)) or "UNRESOLVED",
                "object_count": len(plan.frame_names) + len(plan.point_names),
                "group_name": plan.group_name,
            }
        )
    return rows


def compact_tower_assignment_summary(
    payload: TowerAssignmentReport | Mapping[str, Any],
    *,
    created_group_names: Iterable[str] = (),
    updated_group_names: Iterable[str] = (),
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return the small Tower summary intended for terminal/plugin output.

    The JSON/CSV report remains the authoritative per-object audit.  The
    process stdout is consumed by the WinForms plugin, however, so putting the
    complete ``records`` array there turns one check into hundreds of log
    lines.  Keep only beam counts, suspect beam names, and Group names in the
    display payload.
    """

    raw: Mapping[str, Any] = (
        payload.to_dict() if isinstance(payload, TowerAssignmentReport) else payload
    )
    record_rows = tuple(
        row for row in (raw.get("records") or ()) if isinstance(row, Mapping)
    )
    beam_rows = tuple(
        row
        for row in record_rows
        if str(row.get("object_type", "")).casefold() in {"beam", "frame"}
    )
    group_rows = tuple(
        row for row in (raw.get("groups") or ()) if isinstance(row, Mapping)
    )

    def unique_text(values: Iterable[Any]) -> list[str]:
        normalized = (
            "" if value is None else str(value).strip()
            for value in values
        )
        return list(dict.fromkeys(value for value in normalized if value))

    def integer(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    planned_groups = unique_text(row.get("group_name", "") for row in group_rows)
    if beam_rows:
        suspect_beams = unique_text(
            row.get("name", "") for row in beam_rows if row.get("status") != PASS
        )
        passed_beams = sum(row.get("status") == PASS for row in beam_rows)
        checked_beams = integer(raw.get("checked_beams"), len(beam_rows))
    else:
        # The final workflow summary already strips records, but its Group
        # plans still contain frame_names.  Use those names to retain the same
        # concise result for normal (non-preflight-only) runs.
        suspect_beams = unique_text(
            name
            for group in group_rows
            for name in (group.get("frame_names") or ())
        )
        checked_beams = integer(raw.get("checked_beams"), 0)
        passed_beams = max(0, checked_beams - len(suspect_beams))

    created = unique_text(created_group_names)
    updated = unique_text(updated_group_names)
    review_count = len(suspect_beams)
    checked_points = integer(raw.get("checked_points"), 0)
    issue_count = integer(raw.get("issue_count"), review_count)
    report_status = str(raw.get("status") or "UNKNOWN")
    review_text = ", ".join(suspect_beams) if suspect_beams else "无"
    planned_text = ", ".join(planned_groups) if planned_groups else "无"
    mutated_groups = unique_text((*created, *updated))
    mutated_text = ", ".join(mutated_groups) if mutated_groups else "无"
    result: dict[str, Any] = {
        "status": report_status,
        "checked_beams": checked_beams,
        "passed_beams": passed_beams,
        "review_beams": review_count,
        "review_beam_names": suspect_beams,
        "review_group_names": planned_groups,
        "created_group_names": created,
        "updated_group_names": updated,
        "checked_points": checked_points,
        "issue_count": issue_count,
        "message": (
            f"分塔检查：{passed_beams}/{checked_beams} 通过检查；"
            f"有 {review_count} 根梁存疑，编号：[{review_text}]；"
            f"审查 Group：[{planned_text}]；"
            f"已创建/更新 Group：[{mutated_text}]。"
        ),
    }
    if report_path is not None:
        result["report"] = str(report_path)
    diagnostics = unique_text(raw.get("diagnostics") or ())
    if diagnostics:
        result["diagnostics"] = diagnostics
    return result
