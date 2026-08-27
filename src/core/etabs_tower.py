"""ETABS public-OAPI adapter for Tower Assignment preflight.

Only this adapter knows about ``SapModel.Tower`` and ``DatabaseTables``.  The
decision logic lives in :mod:`src.core.tower_assignment`, which also makes the
same checks replayable from a SQLite snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from collections.abc import Iterable as IterableABC
from typing import Any, Iterable, Mapping

from src.core.etabs_connector import EtabsApiError, api_values
from src.core.tower_assignment import (
    BLOCKED,
    TowerAssignmentReport,
    TowerStoryCatalog,
    build_tower_assignment_report,
)
from src.snapshot.sqlite_snapshot import PublicOapiTableSource, SnapshotTableError


class TowerAssignmentApiError(RuntimeError):
    """A public OAPI Tower/Story call could not be completed safely."""


@dataclass(frozen=True)
class TowerGroupMutationResult:
    """Outcome of one explicit, non-saving Group transaction."""

    status: str
    created: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    audit: tuple[str, ...] = ()
    ownership_path: str = ""


def _group_names(group_api: Any) -> set[str]:
    method = getattr(group_api, "GetNameList", None)
    if method is None:
        return set()
    for args in ((0, None), (None,)):
        try:
            values = api_values(method(*args), "GroupDef.GetNameList")
            if len(values) >= 2:
                return {_as_text(name) for name in (values[1] or ()) if _as_text(name)}
        except (TypeError, EtabsApiError):
            continue
    raise TowerAssignmentApiError("TOWER_GROUP_LIST_READ_FAILED: GroupDef.GetNameList")


def _object_guid(sap: Any, object_type: str, name: str) -> str:
    api = getattr(sap, "FrameObj" if object_type.casefold() == "beam" else "PointObj", None)
    method = getattr(api, "GetGUID", None) if api is not None else None
    if method is None:
        return ""
    for args in ((name,), (name, None)):
        try:
            values = api_values(method(*args), f"{object_type}Obj.GetGUID({name})")
            if values:
                return _as_text(values[0])
        except (TypeError, EtabsApiError):
            continue
    return ""


def create_tower_review_groups(
    sap_model: Any,
    report: TowerAssignmentReport,
    *,
    allow_mutation: bool = False,
    ownership_path: str | Path | None = None,
    expected_model_sha256: str | None = None,
) -> TowerGroupMutationResult:
    """Create/update review Groups without changing Tower assignments.

    Group membership is the only mutation.  The operation never selects
    objects and never calls ``File.Save``.  A small ownership sidecar is used
    to distinguish plugin-owned groups from a user's pre-existing group with
    the same reserved name.
    """

    if not allow_mutation:
        raise PermissionError("创建 Tower 审查 Group 必须显式确认 allow_mutation=True。")
    if report.blocked:
        raise TowerAssignmentApiError("TOWER_PREFLIGHT_BLOCKED: 不能从阻断报告创建 Group。")
    group_api = getattr(sap_model, "GroupDef", None)
    if group_api is None:
        raise TowerAssignmentApiError("TOWER_GROUP_API_MISSING: SapModel.GroupDef")
    from ETABSv1 import eItemType

    path = Path(ownership_path).resolve() if ownership_path else None
    ownership: dict[str, Any] = {"schema": "etabs-beam-generator/tower-group-ownership@1", "groups": {}}
    if path and path.is_file():
        ownership = json.loads(path.read_text(encoding="utf-8"))
        if ownership.get("model_sha256") and expected_model_sha256 and ownership["model_sha256"].casefold() != expected_model_sha256.casefold():
            raise TowerAssignmentApiError("TOWER_GROUP_OWNERSHIP_MODEL_MISMATCH")
    owned = ownership.setdefault("groups", {})
    existing = _group_names(group_api)
    created: list[str] = []
    updated: list[str] = []
    added: list[tuple[str, str, str]] = []
    removed: list[tuple[str, str, str]] = []
    report_guid_by_object = {
        record.name: record.guid
        for record in report.records
        if record.name and record.guid
    }

    def set_group(name: str) -> None:
        api_values(
            group_api.SetGroup(
                name, 42495, True, False, False, False, False, False,
                False, False, False, False, False,
            ),
            f"GroupDef.SetGroup({name})",
        )

    def assign(object_type: str, object_name: str, group: str, remove: bool = False) -> None:
        api = getattr(sap_model, "FrameObj" if object_type.casefold() == "beam" else "PointObj")
        api_values(
            api.SetGroupAssign(object_name, group, bool(remove), eItemType.Objects),
            f"{object_type}Obj.SetGroupAssign({object_name},{group},{remove})",
        )

    try:
        for plan in report.groups:
            name = plan.group_name
            if name in existing and name not in owned:
                raise TowerAssignmentApiError(f"TOWER_GROUP_NAME_CONFLICT: {name}")
            if name not in existing:
                set_group(name)
                existing.add(name)
                created.append(name)
            else:
                updated.append(name)
            previous = owned.get(name, {"frames": [], "points": [], "frame_guids": [], "point_guids": []})
            for object_type, names, old_names in (
                ("Beam", tuple(previous.get("frames", ())), ()),
                ("Point", tuple(previous.get("points", ())), ()),
            ):
                for object_name in names:
                    assign(object_type, str(object_name), name, remove=True)
                    removed.append((object_type, str(object_name), name))
            for object_type, names in (
                ("Beam", plan.frame_names),
                ("Point", plan.point_names),
            ):
                for object_name in names:
                    expected_guid = report_guid_by_object.get(object_name, "")
                    actual_guid = _object_guid(sap_model, object_type, object_name)
                    if expected_guid and actual_guid and expected_guid.casefold() != actual_guid.casefold():
                        raise TowerAssignmentApiError(f"TOWER_GROUP_GUID_MISMATCH: {object_type} {object_name}")
                    assign(object_type, object_name, name)
                    added.append((object_type, object_name, name))
            owned[name] = {
                "expected_tower": plan.expected_tower,
                "frames": list(plan.frame_names),
                "points": list(plan.point_names),
                "frame_guids": list(plan.frame_guids),
                "point_guids": list(plan.point_guids),
            }
    except Exception:
        for object_type, object_name, group in reversed(added):
            try:
                assign(object_type, object_name, group, remove=True)
            except Exception:
                pass
        for object_type, object_name, group in reversed(removed):
            try:
                assign(object_type, object_name, group)
            except Exception:
                pass
        delete_group = getattr(group_api, "Delete", None)
        if delete_group is not None:
            for group in reversed(created):
                try:
                    api_values(delete_group(group), f"GroupDef.Delete({group})")
                except Exception:
                    pass
        raise
    if path:
        ownership["model_sha256"] = expected_model_sha256 or ownership.get("model_sha256", "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ownership, ensure_ascii=False, indent=2), encoding="utf-8")
    return TowerGroupMutationResult(
        status="OK",
        created=tuple(created),
        updated=tuple(updated),
        audit=tuple(f"{record[0]}:{record[1]}->{record[2]}" for record in added),
        ownership_path=str(path) if path else "",
    )


def _as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _active_tower(tower_api: Any) -> str:
    for args in ((), (None,)):
        try:
            values = api_values(tower_api.GetActiveTower(*args), "Tower.GetActiveTower")
            return _as_text(values[0] if values else "")
        except (TypeError, EtabsApiError):
            continue
    raise TowerAssignmentApiError("TOWER_ACTIVE_READ_FAILED: Tower.GetActiveTower")


def _tower_names(
    tower_api: Any,
    story_rows: Any = None,
) -> tuple[str, ...]:
    """Return Tower names, using the public Story table when needed.

    ETABS 22 exposes ``cTower.GetNameList``.  ETABS 23's public ``cTower``
    interface does not, even though ``Story Definitions`` still contains the
    Tower column.  The table fallback is therefore a version-compatible
    public-API path, not an inferred Tower name.
    """

    method = getattr(tower_api, "GetNameList", None)
    if method is not None:
        for args in ((0, None), (None,)):
            try:
                values = api_values(method(*args), "Tower.GetNameList")
                if len(values) >= 2:
                    names = tuple(_as_text(value) for value in (values[1] or ()))
                    if names:
                        return tuple(dict.fromkeys(names))
            except (TypeError, EtabsApiError):
                continue

    fields = tuple(str(field) for field in (getattr(story_rows, "fields", ()) or ()))
    rows = tuple(getattr(story_rows, "rows", ()) or ())
    tower_index = next(
        (
            index
            for index, field in enumerate(fields)
            if "".join(character for character in field.casefold() if character.isalnum())
            in {"tower", "towername"}
        ),
        None,
    )
    if tower_index is not None:
        names = tuple(
            _as_text(row[tower_index])
            for row in rows
            if len(row) > tower_index and _as_text(row[tower_index])
        )
        if names:
            return tuple(dict.fromkeys(names))
    raise TowerAssignmentApiError("TOWER_NAME_LIST_READ_FAILED: Tower.GetNameList/Story Definitions")


def _sequence_values(value: Any) -> list[Any] | None:
    """Convert Python and pythonnet arrays to a plain list.

    pythonnet exposes ``System.String[]``/``System.Double[]`` as iterable CLR
    arrays rather than Python ``list``/``tuple`` instances.  Treating only
    native sequences made a valid ETABS 23 Story result look empty.
    """

    if value is None or isinstance(value, (str, bytes)):
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, IterableABC):
        try:
            return list(value)
        except TypeError:
            return None
    return None


def _story_arrays(values: tuple[Any, ...]) -> tuple[tuple[str, ...], tuple[float | None, ...], tuple[float | None, ...]]:
    """Extract story names/heights/elevations from both ETABS overloads.

    CSI changed the number of output arrays between releases.  We use the
    first string array as names and preserve numeric arrays as height/elevation
    candidates; no array is treated as an elevation unless its length matches
    the story count.
    """

    payload = list(values[1:])  # omit OAPI status
    count = next((int(item) for item in payload if isinstance(item, int) and item >= 0), None)
    arrays = []
    for item in payload:
        values = _sequence_values(item)
        if values is not None:
            arrays.append(values)
    names_array = next((array for array in arrays if array and all(isinstance(value, str) for value in array)), [])
    if count is None:
        count = len(names_array)
    names = tuple(_as_text(value) for value in names_array[:count])
    numeric_arrays = [
        [float(value) if value not in (None, "") else None for value in array[:count]]
        for array in arrays
        if len(array) >= count and array is not names_array and all(
            value in (None, "") or isinstance(value, (int, float)) or _is_number(value)
            for value in array[:count]
        )
    ]
    # Both GetStories and GetStories_2 expose StoryElevations before
    # StoryHeights.  Keep the return order (names, heights, elevations)
    # stable for the caller while mapping the OAPI order correctly.
    elevations = tuple(numeric_arrays[0]) if numeric_arrays else tuple(None for _ in names)
    heights = tuple(numeric_arrays[1]) if len(numeric_arrays) > 1 else tuple(None for _ in names)
    return names, heights, elevations


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _read_stories(tower_api: Any, story_api: Any, tower: str) -> tuple[TowerStoryCatalog, ...]:
    """Read ``GetStories_2`` with a conservative ``GetStories`` fallback."""

    calls = (
        ("GetStories_2", (0, None, None, None, None, None, None, None, None, None, None)),
        ("GetStories_2", (0, None, None, None, None, None, None, None, None, None)),
        ("GetStories", (0, None, None, None, None, None, None, None)),
    )
    last_error: Exception | None = None
    for method_name, args in calls:
        method = getattr(story_api, method_name, None)
        if method is None:
            continue
        try:
            values = api_values(method(*args), f"Story.{method_name}")
            names, heights, elevations = _story_arrays(values)
            if names:
                return tuple(
                    TowerStoryCatalog(
                        tower=tower,
                        story=story,
                        elevation=elevations[index] if index < len(elevations) else None,
                        height=heights[index] if index < len(heights) else None,
                        source=f"Story.{method_name}",
                    )
                    for index, story in enumerate(names)
                )
            last_error = TowerAssignmentApiError(f"TOWER_STORY_EMPTY: {tower}")
        except (TypeError, EtabsApiError, ValueError) as exc:
            last_error = exc
            continue
    raise TowerAssignmentApiError(f"TOWER_STORY_READ_FAILED: {tower}: {last_error}")


class EtabsTowerReader:
    """Read a Tower/Story snapshot while restoring the active Tower."""

    TABLE_KEYS = (
        "Story Definitions",
        "Point Object Connectivity",
        "Beam Object Connectivity",
        "Column Object Connectivity",
    )
    OPTIONAL_TABLE_KEYS = ("Brace Object Connectivity",)

    def __init__(self, sap_model: Any) -> None:
        self.sap = sap_model

    def read_report(
        self,
        *,
        selection: str = "all",
        selected_beams: Iterable[str] | None = None,
        model_identity: Mapping[str, Any] | None = None,
    ) -> TowerAssignmentReport:
        tower_api = getattr(self.sap, "Tower", None)
        story_api = getattr(self.sap, "Story", None)
        diagnostics: list[str] = []

        # Read the public tables before enumerating Towers.  ETABS 23 removed
        # ``cTower.GetNameList`` from the public interface, while the Story
        # Definitions table still carries the authoritative Tower names.
        try:
            table_source = PublicOapiTableSource(self.sap)
            available_keys = {table.key for table in table_source.available_tables()}
            tables = {table.key: table for table in table_source.read_tables(self.TABLE_KEYS)}
            if "Brace Object Connectivity" in available_keys:
                tables["Brace Object Connectivity"] = table_source.read_table("Brace Object Connectivity")
        except SnapshotTableError as exc:
            diagnostics.append(f"TOWER_TABLE_MISSING: {exc}")
            tables = {}

        if tower_api is None or story_api is None:
            diagnostics.append("TOWER_TABLE_MISSING: SapModel.Tower/Story")
            return build_tower_assignment_report(
                story_rows=tables.get("Story Definitions"),
                point_rows=tables.get("Point Object Connectivity"),
                beam_rows=tables.get("Beam Object Connectivity"),
                column_rows=tables.get("Column Object Connectivity"),
                brace_rows=tables.get("Brace Object Connectivity"),
                selection=selection,
                model_identity=model_identity, diagnostics=diagnostics,
            )
        try:
            active_before = _active_tower(tower_api)
            names = _tower_names(tower_api, tables.get("Story Definitions"))
        except TowerAssignmentApiError as exc:
            return build_tower_assignment_report(
                story_rows=tables.get("Story Definitions"),
                point_rows=tables.get("Point Object Connectivity"),
                beam_rows=tables.get("Beam Object Connectivity"),
                column_rows=tables.get("Column Object Connectivity"),
                brace_rows=tables.get("Brace Object Connectivity"),
                selection=selection,
                model_identity=model_identity, diagnostics=(str(exc),),
            )
        catalog: list[TowerStoryCatalog] = []
        active_after = active_before
        story_api_failed = False
        story_fallback_diagnostics: list[str] = []
        try:
            for tower in names:
                api_values(tower_api.SetActiveTower(tower), f"Tower.SetActiveTower({tower})")
                catalog.extend(_read_stories(tower_api, story_api, tower))
        except Exception as exc:
            story_api_failed = True
            if tables.get("Story Definitions") is not None and getattr(tables["Story Definitions"], "rows", ()):
                # Public Story Definitions remains sufficient to build the
                # assignment catalog.  Keep the API failure visible without
                # blocking a valid ETABS 23 table-based preflight.
                story_fallback_diagnostics.append(
                    f"TOWER_STORY_API_FALLBACK: {type(exc).__name__}: {exc}; "
                    "using public Story Definitions table."
                )
            else:
                diagnostics.append(f"TOWER_STORY_READ_FAILED: {type(exc).__name__}: {exc}")
        finally:
            try:
                api_values(tower_api.SetActiveTower(active_before), "Tower.SetActiveTower(restore)")
                active_after = _active_tower(tower_api)
                if active_after != active_before:
                    diagnostics.append("TOWER_ACTIVE_RESTORE_FAILED: active Tower verification mismatch")
            except Exception as exc:
                diagnostics.append(f"TOWER_ACTIVE_RESTORE_FAILED: {type(exc).__name__}: {exc}")

        report = build_tower_assignment_report(
            story_rows=tables.get("Story Definitions"),
            point_rows=tables.get("Point Object Connectivity"),
            beam_rows=tables.get("Beam Object Connectivity"),
            column_rows=tables.get("Column Object Connectivity"),
            brace_rows=tables.get("Brace Object Connectivity"),
            selection=selection,
            selected_beams=selected_beams,
            catalog=(catalog if not story_api_failed else None) or None,
            active_tower_before=active_before,
            active_tower_after=active_after,
            model_identity=model_identity,
            diagnostics=tuple(
                item for item in diagnostics
                if not item.startswith("TOWER_STORY_API_FALLBACK")
            ),
        )
        if story_fallback_diagnostics:
            report = replace(
                report,
                diagnostics=tuple(dict.fromkeys((*report.diagnostics, *story_fallback_diagnostics))),
            )
        # Any failed Tower/Story API read is a fail-closed condition.  A
        # partial catalog is not safe enough to classify a model as PASS or
        # REVIEW_REQUIRED because an omitted Tower can make a wrong
        # assignment look valid.
        blocking_prefixes = (
            "TOWER_STORY_READ_FAILED",
            "TOWER_ACTIVE_RESTORE_FAILED",
            "TOWER_TABLE_MISSING",
        )
        if diagnostics and any(item.startswith(blocking_prefixes) for item in diagnostics):
            return replace(report, status=BLOCKED)
        return report
