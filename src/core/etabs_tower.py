"""ETABS public-OAPI adapter for Tower Assignment preflight.

Only this adapter knows about ``SapModel.Tower`` and ``DatabaseTables``.  The
decision logic lives in :mod:`src.core.tower_assignment`, which also makes the
same checks replayable from a SQLite snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
from collections.abc import Iterable as IterableABC
from time import perf_counter
from typing import Any, Callable, Iterable, Mapping

from src.core.etabs_connector import EtabsApiError, api_values, model_paths_match
from src.core.etabs_selection import EtabsSelectionSnapshot, SelectionRestoreResult
from src.core.tower_assignment import (
    BLOCKED,
    TowerAssignmentReport,
    TowerStoryCatalog,
    build_tower_assignment_report,
)
from src.snapshot.sqlite_snapshot import PublicOapiTableSource, SnapshotTableError


class TowerAssignmentApiError(RuntimeError):
    """A public OAPI Tower/Story call could not be completed safely."""


TowerProgress = Callable[[int, str], None]


def _emit_progress(progress: TowerProgress | None, percent: int, message: str) -> None:
    """Notify the host about a coarse Tower phase without exposing records."""

    if progress is not None:
        progress(max(0, min(100, int(percent))), message)


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
    # CSI documents NumberStories as the number of real stories while the
    # returned arrays also contain the synthetic Base row.  Some pythonnet
    # fakes/reporters expose the array length instead, so accept both forms.
    array_count = count
    if (
        names_array
        and _as_text(names_array[0]).casefold() == "base"
        and len(names_array) >= count + 1
    ):
        array_count = count + 1
    names = tuple(_as_text(value) for value in names_array[:array_count])
    numeric_arrays = [
        [float(value) if value not in (None, "") else None for value in array[:array_count]]
        for array in arrays
        if len(array) >= array_count and array is not names_array and all(
            value in (None, "") or isinstance(value, (int, float)) or _is_number(value)
            for value in array[:array_count]
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


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_stories(tower_api: Any, story_api: Any, tower: str) -> tuple[TowerStoryCatalog, ...]:
    """Read ``GetStories_2`` with a conservative ``GetStories`` fallback."""

    calls = (
        # ETABS 22/23 expose GetStories_2 as:
        # BaseElevation, NumberStories, StoryNames, StoryElevations,
        # StoryHeights, IsMasterStory, SimilarToStory, SpliceAbove,
        # SpliceHeight, Color.
        ("GetStories_2", (0.0, 0, None, None, None, None, None, None, None, None)),
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


def _normalized_field(value: Any) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def _table_has_fields(table: Any, *alternatives: tuple[str, ...]) -> bool:
    fields = {
        _normalized_field(field): index
        for index, field in enumerate(getattr(table, "fields", ()) or ())
    }
    rows = tuple(getattr(table, "rows", ()) or ())
    if not rows:
        return False
    indexes: list[int] = []
    for alternative in alternatives:
        index = next((fields[_normalized_field(candidate)] for candidate in alternative if _normalized_field(candidate) in fields), None)
        if index is None:
            return False
        indexes.append(index)
    return all(
        all(index < len(row) and _as_text(row[index]) for index in indexes)
        for row in rows
    )


def _table_has_numeric_fields(table: Any, *alternatives: tuple[str, ...]) -> bool:
    """Require a complete finite numeric column, not merely a named column."""

    fields = {
        _normalized_field(field): index
        for index, field in enumerate(getattr(table, "fields", ()) or ())
    }
    rows = tuple(getattr(table, "rows", ()) or ())
    if not rows:
        return False
    indexes: list[int] = []
    for alternative in alternatives:
        index = next(
            (
                fields[_normalized_field(candidate)]
                for candidate in alternative
                if _normalized_field(candidate) in fields
            ),
            None,
        )
        if index is None:
            return False
        indexes.append(index)
    return all(
        all(index < len(row) and _finite_number(row[index]) is not None for index in indexes)
        for row in rows
    )


def _story_table_has_reliable_elevations(table: Any) -> bool:
    """Check whether Story Definitions can replace a failed Story API read.

    ``Height`` is a relative quantity and cannot identify a Story when two
    Towers both contain (for example) ``L1``.  The table-only path therefore
    uses finite absolute elevations only when it needs a complete Story
    catalog as a fallback for the scoped Story API.  The normal table-only
    path uses same-level point topology, so equal elevations are not rejected
    by themselves.
    """

    if not _table_has_numeric_fields(
        table, ("Elevation", "Z", "TopElevation", "BottomElevation")
    ):
        return False
    fields = {
        _normalized_field(field): index
        for index, field in enumerate(getattr(table, "fields", ()) or ())
    }
    rows = tuple(getattr(table, "rows", ()) or ())
    tower_index = next(
        (
            fields[_normalized_field(candidate)]
            for candidate in ("Tower", "TowerName")
            if _normalized_field(candidate) in fields
        ),
        None,
    )
    story_index = next(
        (
            fields[_normalized_field(candidate)]
            for candidate in ("Story", "StoryName")
            if _normalized_field(candidate) in fields
        ),
        None,
    )
    elevation_index = next(
        (
            fields[_normalized_field(candidate)]
            for candidate in ("Elevation", "Z", "TopElevation", "BottomElevation")
            if _normalized_field(candidate) in fields
        ),
        None,
    )
    if tower_index is None or story_index is None or elevation_index is None:
        return False

    definitions: dict[tuple[str, str], float] = {}
    for row in rows:
        if max(tower_index, story_index, elevation_index) >= len(row):
            return False
        tower = _as_text(row[tower_index])
        story = _as_text(row[story_index])
        elevation = _finite_number(row[elevation_index])
        if not tower or not story or elevation is None:
            return False
        key = (tower, story)
        previous = definitions.get(key)
        if previous is not None and not math.isclose(previous, elevation, abs_tol=1.0):
            return False
        definitions[key] = elevation

    # Same-name Stories at an identical absolute elevation cannot be separated
    # by this Story catalog alone.  This helper is only used when deciding
    # whether the catalog can replace a failed scoped Story API read; the
    # normal table path uses same-level point topology instead.
    stories = {story for _, story in definitions}
    for story in stories:
        towers = {tower for tower, item_story in definitions if item_story == story}
        elevations = {
            definitions[(tower, story)]
            for tower in towers
        }
        if len(towers) > 1 and len(elevations) != len(towers):
            return False
    return True


def _public_tables_are_sufficient(tables: Mapping[str, Any]) -> bool:
    """Whether public tables contain enough topology for a no-switch check.

    Same-name Stories may share one absolute elevation across Towers.  That
    is not a reason to switch Towers; point Z plus same-level connectivity is
    the discriminator used by the domain checker.
    """

    story = tables.get("Story Definitions")
    point = tables.get("Point Object Connectivity")
    beam = tables.get("Beam Object Connectivity")
    return (
        _table_has_fields(story, ("Tower", "TowerName"), ("Story", "StoryName"))
        and _table_has_fields(
            point,
            ("UniqueName", "Name", "Point"),
            ("Tower", "TowerName"),
            ("Story", "StoryName"),
        )
        and _table_has_numeric_fields(point, ("Z", "GlobalZ"))
        and _table_has_fields(
            beam,
            ("UniqueName", "Name", "Frame"),
            ("UniquePtI", "PointI", "PointObjI", "IEndPoint"),
            ("UniquePtJ", "PointJ", "PointObjJ", "JEndPoint"),
        )
    )


def _table_only_rejection_diagnostics(tables: Mapping[str, Any]) -> tuple[str, ...]:
    story = tables.get("Story Definitions")
    if (
        _table_has_fields(story, ("Tower", "TowerName"), ("Story", "StoryName"))
        and not _public_tables_are_sufficient(tables)
    ):
        return (
            "TOWER_TABLE_ONLY_REJECTED: public tables lack a complete finite, "
            "same-level point elevation and connectivity catalog; "
            "using scoped Tower/Story API.",
        )
    return ()


def _point_elevation_is_incomplete_for_ambiguous_stories(
    point_table: Any,
    story_table: Any,
    catalog: Iterable[TowerStoryCatalog] = (),
) -> bool:
    """Reject topology-only guesses when a point's Story maps to many Towers."""

    catalog_items = tuple(catalog)
    ambiguous_stories = {
        item.story
        for item in catalog_items
        if len({other.tower for other in catalog_items if other.story == item.story}) > 1
    }
    story_fields = {
        _normalized_field(field): index
        for index, field in enumerate(getattr(story_table, "fields", ()) or ())
    }
    story_rows = tuple(getattr(story_table, "rows", ()) or ())
    tower_index = next(
        (
            story_fields[_normalized_field(candidate)]
            for candidate in ("Tower", "TowerName")
            if _normalized_field(candidate) in story_fields
        ),
        None,
    )
    story_index = next(
        (
            story_fields[_normalized_field(candidate)]
            for candidate in ("Story", "StoryName")
            if _normalized_field(candidate) in story_fields
        ),
        None,
    )
    if not ambiguous_stories and tower_index is not None and story_index is not None:
        towers_by_story: dict[str, set[str]] = {}
        for row in story_rows:
            if max(tower_index, story_index) < len(row):
                towers_by_story.setdefault(_as_text(row[story_index]), set()).add(_as_text(row[tower_index]))
        ambiguous_stories = {
            story for story, towers in towers_by_story.items()
            if story and len(towers) > 1
        }
    if not ambiguous_stories:
        return False

    point_fields = {
        _normalized_field(field): index
        for index, field in enumerate(getattr(point_table, "fields", ()) or ())
    }
    point_rows = tuple(getattr(point_table, "rows", ()) or ())
    point_story_index = next(
        (
            point_fields[_normalized_field(candidate)]
            for candidate in ("Story", "StoryName")
            if _normalized_field(candidate) in point_fields
        ),
        None,
    )
    point_z_index = next(
        (
            point_fields[_normalized_field(candidate)]
            for candidate in ("Z", "GlobalZ")
            if _normalized_field(candidate) in point_fields
        ),
        None,
    )
    if point_story_index is None or point_z_index is None:
        return any(
            point_story_index is None or point_story_index >= len(row)
            or _as_text(row[point_story_index]) in ambiguous_stories
            for row in point_rows
        )
    return any(
        point_story_index >= len(row)
        or _as_text(row[point_story_index]) in ambiguous_stories
        and (point_z_index >= len(row) or _finite_number(row[point_z_index]) is None)
        for row in point_rows
    )


def _with_timing(
    report: TowerAssignmentReport,
    *,
    started: float,
    table_finished: float,
    catalog_finished: float,
    switch_count: int,
) -> TowerAssignmentReport:
    now = perf_counter()
    timing = (
        "TOWER_READ_TIMING_MS: "
        f"table_read={((table_finished - started) * 1000):.1f}; "
        f"tower_catalog={((catalog_finished - table_finished) * 1000):.1f}; "
        f"report_build={((now - catalog_finished) * 1000):.1f}; "
        f"tower_read={((now - started) * 1000):.1f}; tower_switches={switch_count}"
    )
    return replace(report, diagnostics=(*report.diagnostics, timing))


def _with_end_to_end_timing(
    report: TowerAssignmentReport,
    *,
    started: float,
    capture_finished: float,
    read_started: float,
    read_finished: float,
    restore_started: float,
    finished: float,
    restore_strategy: str,
) -> TowerAssignmentReport:
    """Append phase timings that include the potentially slow recovery stage."""

    timing = (
        "TOWER_TIMING_MS: "
        f"selection_capture={((capture_finished - started) * 1000):.1f}; "
        f"tower_read={((read_finished - read_started) * 1000):.1f}; "
        f"selection_restore={((finished - restore_started) * 1000):.1f}; "
        f"read_report={((finished - started) * 1000):.1f}; "
        f"selection_restore_strategy={restore_strategy}"
    )
    return replace(report, diagnostics=(*report.diagnostics, timing))


class EtabsTowerReader:
    """Read Tower/Story tables while preserving the active Tower and full selection."""

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
        progress: TowerProgress | None = None,
    ) -> TowerAssignmentReport:
        started = perf_counter()
        _emit_progress(progress, 0, "捕获原选择集")
        try:
            snapshot = EtabsSelectionSnapshot.capture(self.sap)
        except Exception as exc:
            capture_finished = perf_counter()
            finished = perf_counter()
            report = TowerAssignmentReport(
                status=BLOCKED, selection=selection, model_identity=model_identity,
                diagnostics=(f"TOWER_SELECTION_CAPTURE_FAILED: {type(exc).__name__}: {exc}",),
                selection_restore_verified=False,
            )
            report = _with_end_to_end_timing(
                report,
                started=started,
                capture_finished=capture_finished,
                read_started=capture_finished,
                read_finished=capture_finished,
                restore_started=finished,
                finished=finished,
                restore_strategy="capture_failed",
            )
            _emit_progress(progress, 100, "选择集捕获失败，Tower 检查已阻断")
            return report
        restored = ()
        selection_restore_verified = False
        selection_restore_diagnostics: tuple[str, ...] = ()
        restore_error = ""
        restore_strategy = "not_attempted"
        capture_finished = perf_counter()
        read_started = capture_finished
        read_finished = read_started
        restore_started = read_started
        finished = read_started
        _emit_progress(progress, 10, "选择集已捕获，读取公共表")
        try:
            report = self._read_report(
                selection=selection, selected_beams=selected_beams, model_identity=model_identity,
                expected_model_path=snapshot.model_path,
                progress=progress,
            )
        except Exception as exc:
            report = TowerAssignmentReport(
                status=BLOCKED, selection=selection, model_identity=model_identity,
                diagnostics=(f"TOWER_READ_FAILED: {type(exc).__name__}: {exc}",),
            )
        finally:
            read_finished = perf_counter()
            restore_started = read_finished
            _emit_progress(progress, 96, "校验并恢复原选择集")
            # _read_report restores the active Tower first: another tower switch
            # after this point could clear the selection again.
            try:
                restore_result: SelectionRestoreResult = snapshot.restore_with_audit(self.sap)
                restored = restore_result.actual
                restore_strategy = restore_result.strategy
                selection_restore_verified = restore_result.verified
                selection_restore_diagnostics = (*restore_result.diagnostics, restore_result.summary())
                if not restore_result.verified:
                    restore_error = (
                        "TOWER_SELECTION_RESTORE_FAILED: "
                        + "; ".join(restore_result.diagnostics or ("selection verification failed",))
                    )
            except Exception as exc:
                restore_strategy = "failed"
                restore_error = f"TOWER_SELECTION_RESTORE_FAILED: {type(exc).__name__}: {exc}"
            finally:
                finished = perf_counter()
                _emit_progress(
                    progress,
                    100,
                    "Tower 检查完成" if not restore_error else "选择集恢复未验证，Tower 检查阻断",
                )
        report = replace(
            report,
            status=BLOCKED if restore_error else report.status,
            diagnostics=tuple(
                (*report.diagnostics, *selection_restore_diagnostics, restore_error)
                if restore_error
                else (*report.diagnostics, *selection_restore_diagnostics)
            ),
            selected_objects_before=snapshot.objects,
            selected_objects_after=restored,
            selection_restore_verified=selection_restore_verified,
        )
        return _with_end_to_end_timing(
            report,
            started=started,
            capture_finished=capture_finished,
            read_started=read_started,
            read_finished=read_finished,
            restore_started=restore_started,
            finished=finished,
            restore_strategy=restore_strategy,
        )

    def _read_report(
        self,
        *,
        selection: str = "all",
        selected_beams: Iterable[str] | None = None,
        model_identity: Mapping[str, Any] | None = None,
        expected_model_path: str | None = None,
        progress: TowerProgress | None = None,
    ) -> TowerAssignmentReport:
        started = perf_counter()
        tower_api = getattr(self.sap, "Tower", None)
        story_api = getattr(self.sap, "Story", None)
        diagnostics: list[str] = []
        switch_count = 0

        # Read the public tables before enumerating Towers.  ETABS 23 removed
        # ``cTower.GetNameList`` from the public interface, while the Story
        # Definitions table still carries the authoritative Tower names.
        try:
            table_source = PublicOapiTableSource(self.sap)
            available_keys = {table.key for table in table_source.available_tables()}
            tables: dict[str, Any] = {}
            for key in self.TABLE_KEYS:
                if key not in available_keys:
                    diagnostics.append(f"TOWER_TABLE_MISSING: {key}")
                    continue
                try:
                    tables[key] = table_source.read_table(key)
                except SnapshotTableError as exc:
                    diagnostics.append(f"TOWER_TABLE_READ_FAILED: {key}: {exc}")
            if "Brace Object Connectivity" in available_keys:
                try:
                    tables["Brace Object Connectivity"] = table_source.read_table("Brace Object Connectivity")
                except SnapshotTableError as exc:
                    diagnostics.append(f"TOWER_OPTIONAL_TABLE_READ_FAILED: Brace Object Connectivity: {exc}")
        except SnapshotTableError as exc:
            diagnostics.append(f"TOWER_TABLE_MISSING: {exc}")
            tables = {}
        table_finished = perf_counter()
        _emit_progress(progress, 35, "公共表读取完成，构建同标高拓扑")
        table_only_rejection_diagnostics = _table_only_rejection_diagnostics(tables)

        def verify_model() -> None:
            if expected_model_path is not None:
                filename = getattr(self.sap, "GetModelFilename", None)
                if not callable(filename):
                    raise TowerAssignmentApiError("TOWER_MODEL_IDENTITY_UNAVAILABLE: GetModelFilename")
                if not model_paths_match(expected_model_path, str(filename(True))):
                    raise TowerAssignmentApiError("TOWER_MODEL_CHANGED: refusing to switch Tower in another model")

        try:
            verify_model()
        except TowerAssignmentApiError as exc:
            diagnostics.append(str(exc))

        # Story Definitions includes the authoritative Tower/Story mapping in
        # current ETABS releases.  When the table has the identity and
        # endpoint columns needed by the pure checker, avoid changing the
        # active Tower entirely.  This also avoids any selection-history churn.
        if _public_tables_are_sufficient(tables):
            active_before = ""
            active_after = ""
            active_read_failed = False
            if tower_api is not None and callable(getattr(tower_api, "GetActiveTower", None)):
                try:
                    active_before = _active_tower(tower_api)
                    verify_model()
                    active_after = active_before
                except TowerAssignmentApiError as exc:
                    diagnostics.append(str(exc))
                    active_read_failed = True
            else:
                diagnostics.append("TOWER_ACTIVE_UNAVAILABLE_TABLE_ONLY: no active Tower was changed")
            diagnostics.append(
                "TOWER_TABLE_ONLY_PATH: public Story Definitions and connectivity tables are sufficient; "
                "active Tower was not switched"
            )
            _emit_progress(progress, 60, "按同标高拓扑划分 Region")
            catalog_finished = perf_counter()
            report = build_tower_assignment_report(
                story_rows=tables.get("Story Definitions"),
                point_rows=tables.get("Point Object Connectivity"),
                beam_rows=tables.get("Beam Object Connectivity"),
                column_rows=tables.get("Column Object Connectivity"),
                brace_rows=tables.get("Brace Object Connectivity"),
                selection=selection,
                selected_beams=selected_beams,
                active_tower_before=active_before,
                active_tower_after=active_after,
                model_identity=model_identity,
                # Informational table-only diagnostics are appended after
                # the pure builder; that builder treats any diagnostic as a
                # blocking input by design.
                diagnostics=tuple(
                    item for item in diagnostics
                    if not item.startswith((
                        "TOWER_TABLE_ONLY_PATH",
                        "TOWER_ACTIVE_UNAVAILABLE_TABLE_ONLY",
                        "TOWER_OPTIONAL_TABLE_READ_FAILED",
                    ))
                ),
            )
            report = replace(report, diagnostics=tuple(dict.fromkeys((*report.diagnostics, *diagnostics))))
            if (
                active_read_failed
                or any(item.startswith("TOWER_MODEL_") for item in diagnostics)
                or any(item.startswith("TOWER_OPTIONAL_TABLE_READ_FAILED") for item in diagnostics)
            ):
                report = replace(report, status=BLOCKED)
            _emit_progress(progress, 90, "Region 归属检查完成")
            return _with_timing(
                report,
                started=started,
                table_finished=table_finished,
                catalog_finished=catalog_finished,
                switch_count=switch_count,
            )

        if tower_api is None or story_api is None:
            diagnostics.extend(table_only_rejection_diagnostics)
            diagnostics.append("TOWER_TABLE_PATH_NOT_SUFFICIENT: public tables lack required identity/topology fields")
            diagnostics.append("TOWER_TABLE_MISSING: SapModel.Tower/Story")
            catalog_finished = perf_counter()
            report = build_tower_assignment_report(
                story_rows=tables.get("Story Definitions"),
                point_rows=tables.get("Point Object Connectivity"),
                beam_rows=tables.get("Beam Object Connectivity"),
                column_rows=tables.get("Column Object Connectivity"),
                brace_rows=tables.get("Brace Object Connectivity"),
                selection=selection,
                selected_beams=selected_beams,
                model_identity=model_identity,
                diagnostics=diagnostics,
            )
            _emit_progress(progress, 90, "公共表不足，检查结果已阻断")
            return _with_timing(
                report,
                started=started,
                table_finished=table_finished,
                catalog_finished=catalog_finished,
                switch_count=switch_count,
            )
        try:
            active_before = _active_tower(tower_api)
            verify_model()
            names = _tower_names(tower_api, tables.get("Story Definitions"))
        except TowerAssignmentApiError as exc:
            catalog_finished = perf_counter()
            report = build_tower_assignment_report(
                story_rows=tables.get("Story Definitions"),
                point_rows=tables.get("Point Object Connectivity"),
                beam_rows=tables.get("Beam Object Connectivity"),
                column_rows=tables.get("Column Object Connectivity"),
                brace_rows=tables.get("Brace Object Connectivity"),
                selection=selection,
                model_identity=model_identity,
                diagnostics=tuple(
                    item for item in (*diagnostics, str(exc))
                    if item not in table_only_rejection_diagnostics
                ),
            )
            if table_only_rejection_diagnostics:
                report = replace(
                    report,
                    diagnostics=tuple(
                        dict.fromkeys((*report.diagnostics, *table_only_rejection_diagnostics))
                    ),
                )
            return _with_timing(
                report,
                started=started,
                table_finished=table_finished,
                catalog_finished=catalog_finished,
                switch_count=switch_count,
            )
        catalog: list[TowerStoryCatalog] = []
        active_after = active_before
        active_restore_needed = False
        story_api_failed = False
        story_fallback_diagnostics: list[str] = []

        try:
            _emit_progress(progress, 50, "读取 Tower/Story 目录")
            for tower in names:
                verify_model()
                if tower != active_after:
                    active_restore_needed = True
                    switch_count += 1
                    api_values(tower_api.SetActiveTower(tower), f"Tower.SetActiveTower({tower})")
                    verify_model()
                    active_after = tower
                catalog.extend(_read_stories(tower_api, story_api, tower))
                verify_model()
        except Exception as exc:
            story_api_failed = True
            if isinstance(exc, TowerAssignmentApiError) and str(exc).startswith("TOWER_MODEL_"):
                diagnostics.append(str(exc))
            if tables.get("Story Definitions") is not None and getattr(tables["Story Definitions"], "rows", ()):
                if _story_table_has_reliable_elevations(tables["Story Definitions"]):
                    # Public Story Definitions remains sufficient to build the
                    # assignment catalog.  Keep the API failure visible without
                    # blocking a valid ETABS 23 table-based preflight.
                    story_fallback_diagnostics.append(
                        f"TOWER_STORY_API_FALLBACK: {type(exc).__name__}: {exc}; "
                        "using public Story Definitions table."
                    )
                else:
                    diagnostics.append(
                        f"TOWER_STORY_READ_FAILED: {type(exc).__name__}: {exc}; "
                        "public Story Definitions lacks a complete discriminating elevation catalog."
                    )
            else:
                diagnostics.append(f"TOWER_STORY_READ_FAILED: {type(exc).__name__}: {exc}")
        finally:
            try:
                verify_model()
                if active_restore_needed or active_after != active_before:
                    switch_count += 1
                    api_values(tower_api.SetActiveTower(active_before), "Tower.SetActiveTower(restore)")
                    verify_model()
                active_after = _active_tower(tower_api)
                verify_model()
                if active_after != active_before:
                    diagnostics.append("TOWER_ACTIVE_RESTORE_FAILED: active Tower verification mismatch")
            except Exception as exc:
                diagnostics.append(f"TOWER_ACTIVE_RESTORE_FAILED: {type(exc).__name__}: {exc}")

        catalog_finished = perf_counter()
        _emit_progress(progress, 70, "Tower/Story 目录读取完成，检查拓扑 Region")
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
                and item not in table_only_rejection_diagnostics
            ),
        )
        if table_only_rejection_diagnostics:
            report = replace(
                report,
                diagnostics=tuple(
                    dict.fromkeys((*report.diagnostics, *table_only_rejection_diagnostics))
                ),
            )
        if story_fallback_diagnostics:
            report = replace(
                report,
                diagnostics=tuple(dict.fromkeys((*report.diagnostics, *story_fallback_diagnostics))),
            )
        if _point_elevation_is_incomplete_for_ambiguous_stories(
            tables.get("Point Object Connectivity"),
            tables.get("Story Definitions"),
            catalog if not story_api_failed else (),
        ):
            diagnostics.append(
                "TOWER_POINT_ELEVATION_INCOMPLETE: points in same-name multi-Tower Stories "
                "lack finite absolute Z; classification blocked instead of using topology votes."
            )
            report = replace(
                report,
                status=BLOCKED,
                diagnostics=tuple(dict.fromkeys((*report.diagnostics, diagnostics[-1]))),
            )
        _emit_progress(progress, 90, "Region 归属检查完成")
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
            report = replace(report, status=BLOCKED)
        return _with_timing(
            report,
            started=started,
            table_finished=table_finished,
            catalog_finished=catalog_finished,
            switch_count=switch_count,
        )
