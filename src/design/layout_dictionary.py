"""Offline-built, exact lookup tables for bounded physical-row candidates.

The reference beamset file is deliberately not loaded here.  Dictionaries are
compiled from the selected detailing engine's own candidate generator and are
accepted only for their complete recorded section/policy context.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import fields, is_dataclass, replace
from enum import Enum
import itertools
import json
import math
from pathlib import Path
import time
from typing import Any, Iterable

from src.design.detailing import DetailingError, LongitudinalDesign, PhysicalRow
from src.design.row_candidates import (
    _outer_row_preference,
    _resolve_with_geometry_cache,
    candidates as oracle_candidates,
    signature,
)
from src.design.offline_style import DRAWING_POLICY_REVISION_R6_ID, DRAWING_POLICY_REVISION_R7_ID, DRAWING_POLICY_REVISION_R8_ID


LAYOUT_DICTIONARY_FORMAT = "beam-physical-row-layout-dictionary@1"
LAYOUT_DICTIONARY_ORACLE = "R6_ROW_CANDIDATES_INTERVAL_ORACLE_V1"
LAYOUT_DICTIONARY_BANK_FORMAT = "beam-physical-row-layout-dictionary-bank@1"
LAYOUT_DICTIONARY_BANK_NORMALIZATION = "PHYSICAL_CONTEXT_EXCEPT_NAME_LENGTH_PROVENANCE_AND_RANKING_MODE_V1"
_TOL = 1e-9


class LayoutDictionaryError(ValueError):
    """The requested context is absent or invalid in a saved dictionary."""


class LayoutDictionaryMiss(LayoutDictionaryError):
    """The saved dictionary does not cover the requested context or demand."""


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, float) and not math.isfinite(value):
            raise LayoutDictionaryError("DICTIONARY_CONTEXT_NONFINITE_VALUE")
        return value
    return str(value)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _engine_context(engine: Any) -> dict[str, Any]:
    code = engine.code
    base_code = getattr(code, "base_policy", code)
    project = _json_value(engine.project)
    for name in (
        "pb_main_diameters_mm", "pb_preferred_main_diameters_mm",
        "sb_main_diameters_mm", "sb_preferred_main_diameters_mm",
        "allowed_stirrup_diameters_mm",
    ):
        if not getattr(engine.project, name, ()):
            project.pop(name, None)
    return {
        "detailing_policy_id": str(engine.detailing_policy_id),
        "detailing_policy_revision_id": str(DRAWING_POLICY_REVISION_R7_ID if engine.detailing_policy_revision_id == DRAWING_POLICY_REVISION_R8_ID else engine.detailing_policy_revision_id),
        "ranking_mode": str(engine.ranking_mode),
        "avoid_two_row_4_3": bool(engine.avoid_two_row_4_3),
        "uniform_stirrup_diameter_per_span": bool(engine.uniform_stirrup_diameter_per_span),
        "exact_candidate_state_dedup": bool(engine.exact_candidate_state_dedup),
        "project": project,
        "code": {
            "type": type(code).__name__,
            "base_type": type(base_code).__name__,
            "base_policy": _json_value(base_code),
            "spacing_rule_id": str(getattr(code, "spacing_rule_id", "")),
        },
    }


def _context_record(section: Any, hoops: Iterable[Any], engine: Any, *, first_diameter: int | None,
                    max_layers: int | None, face: str, preserve_support_alternatives: bool) -> dict[str, Any]:
    # In the oracle, candidate geometry reads hoop diameter and support-leg
    # count only. Spacing and the calculated Av/S fields do not enter this
    # function or physical-layout resolution, so normalize to those exact
    # effective inputs while retaining order (the first hoop supplies the
    # returned resolved geometry).
    effective_hoops: list[list[int]] = []
    for hoop in hoops:
        pair = [int(hoop.diameter_mm), int(hoop.legs)]
        if pair not in effective_hoops:
            effective_hoops.append(pair)
    if not effective_hoops:
        raise LayoutDictionaryError("DICTIONARY_CONTEXT_REQUIRES_HOOPS")
    return {
        "section": _json_value(section),
        "engine": _engine_context(engine),
        "candidate_controls": {
            "hoops_diameter_mm_legs_ordered": effective_hoops,
            "first_diameter_mm": first_diameter,
            "max_layers": max_layers,
            "face": str(face),
            "preserve_support_alternatives": bool(preserve_support_alternatives),
        },
    }


def _candidate_parameters(section: Any, hoops: tuple[Any, ...], engine: Any, *, first_diameter: int | None,
                          max_layers: int | None) -> tuple[tuple[int, ...], tuple[int, ...], int, int, int]:
    diameters = engine.project.ordered_longitudinal_diameters_for(section)
    first_diameters = (first_diameter,) if first_diameter is not None else diameters
    if not diameters:
        return (), (), 0, 0, 0
    minimum_count = max(2, *(int(hoop.legs) for hoop in hoops))
    smallest = min(diameters)
    gap = engine.code.minimum_clear_spacing(smallest, section.aggregate_mm)
    height_bound = max(1, int(section.depth_mm // (smallest + gap)))
    layer_limit = engine.project.max_main_bar_layers if max_layers is None else max_layers
    limit = min(layer_limit, height_bound)
    count_bound = int((section.width_mm + gap) // (smallest + gap))
    return tuple(first_diameters), tuple(diameters), minimum_count, count_bound, limit


def _possible_physical_rows(section: Any, hoops: tuple[Any, ...], engine: Any, *, first_diameter: int | None,
                            max_layers: int | None) -> Iterable[tuple[PhysicalRow, ...]]:
    """Enumerate the finite row signatures once to discover exact As events.

    This is builder-only. Runtime lookup never calls this function or the row
    candidate generator.
    """
    first_diameters, diameters, minimum_count, count_bound, limit = _candidate_parameters(
        section, hoops, engine, first_diameter=first_diameter, max_layers=max_layers
    )
    if not diameters or count_bound < minimum_count or limit <= 0:
        return
    additional = tuple(
        (PhysicalRow(count, diameter) for diameter in diameters for count in range(2, count_bound + 1))
    )
    additional = tuple(additional)
    seen: set[tuple[tuple[int, int], ...]] = set()
    for first in first_diameters:
        if first not in diameters:
            continue
        for count in range(minimum_count, count_bound + 1):
            prefix = (PhysicalRow(count, first),)
            prefix_key = tuple((row.count, row.diameter_mm) for row in prefix)
            if prefix_key not in seen:
                seen.add(prefix_key)
                yield prefix
            for extra_count in range(1, limit):
                for extra in itertools.product(additional, repeat=extra_count):
                    rows = prefix + extra
                    key = tuple((row.count, row.diameter_mm) for row in rows)
                    if key not in seen:
                        seen.add(key)
                        yield rows


def _last_layer_count_event_upper(existing_area: float, area_per_bar: float, count: int,
                                  minimum_count: int) -> float:
    """Largest float demand at which the R6 count-start still includes count."""
    def includes(required: float) -> bool:
        if existing_area + _TOL < required:
            first_count = max(
                minimum_count,
                math.ceil((required - existing_area - _TOL) / area_per_bar),
            )
        else:
            first_count = minimum_count
        return first_count <= count

    candidate = existing_area + _TOL + count * area_per_bar
    for _ in range(64):
        if includes(candidate):
            break
        candidate = math.nextafter(candidate, -math.inf)
    else:  # pragma: no cover - protects the compiler from unbounded adjustment
        raise LayoutDictionaryError("DICTIONARY_COUNT_EVENT_BOUNDARY_NOT_FOUND")
    for _ in range(64):
        next_value = math.nextafter(candidate, math.inf)
        if not includes(next_value):
            return candidate
        candidate = next_value
    raise LayoutDictionaryError("DICTIONARY_COUNT_EVENT_BOUNDARY_NOT_ISOLATED")


def _layout_record(design: LongitudinalDesign, as_min_mm2: float) -> dict[str, Any]:
    return {
        "rows": [
            {
                "count": row.count,
                "diameter_mm": row.diameter_mm,
                "role": row.role,
                "credited_area_mm2": row.credited_area_mm2,
            }
            for row in design.rows
        ],
        "provided_area_mm2": design.provided_area_mm2,
        "as_min_mm2": as_min_mm2,
        "clear_spacing_mm": design.clear_spacing_mm,
        "row_centers_mm": list(design.row_centers_mm),
        "row_x_mm": [list(row) for row in design.row_x_mm],
        "effective_depth_mm": design.effective_depth_mm,
        "constraint_sources": list(design.constraint_sources),
    }


def _record_key(record: dict[str, Any]) -> str:
    return _canonical(record)


def _candidate_sort_key(design: LongitudinalDesign, section: Any, engine: Any) -> tuple[Any, ...]:
    outer = _outer_row_preference(design)
    return (
        len(design.rows),
        sum(row.diameter_mm > engine.project.sb_top_preferred_max_diameter_mm for row in design.rows)
        if section.is_secondary else 0,
        outer[0],
        outer[1],
        design.provided_area_mm2 - design.required_area_mm2,
        outer[2],
        outer[3],
        len({row.diameter_mm for row in design.rows}),
        sum(row.count for row in design.rows),
        signature(design),
    )


def build_layout_dictionary(section: Any, hoops: Iterable[Any], engine: Any, *, first_diameter: int | None = None,
                            max_layers: int | None = None, face: str = "top",
                            preserve_support_alternatives: bool = True,
                            build_timeout_seconds: float = 180.0) -> dict[str, Any]:
    """Compile an exact threshold table using the R6/R7 physical-row kernel.

    The historical oracle label remains the same; the complete context key
    includes the policy revision, so dictionaries cannot cross revisions.
    """
    build_started = time.perf_counter()
    if build_timeout_seconds <= 0:
        raise LayoutDictionaryError("DICTIONARY_BUILD_TIMEOUT_INVALID")

    def check_timeout() -> None:
        if time.perf_counter() - build_started > build_timeout_seconds:
            raise LayoutDictionaryError(
                f"DICTIONARY_BUILD_TIMEOUT: max_seconds={build_timeout_seconds:g}"
            )

    if engine.detailing_policy_revision_id not in {DRAWING_POLICY_REVISION_R6_ID, DRAWING_POLICY_REVISION_R7_ID, DRAWING_POLICY_REVISION_R8_ID}:
        raise LayoutDictionaryError("DICTIONARY_BUILDER_REQUIRES_R6_ORACLE")
    hoops = tuple(hoops)
    if not hoops:
        raise LayoutDictionaryError("DICTIONARY_CONTEXT_REQUIRES_HOOPS")
    context = _context_record(
        section, hoops, engine, first_diameter=first_diameter, max_layers=max_layers,
        face=face, preserve_support_alternatives=preserve_support_alternatives,
    )
    context_key = _canonical(context)
    geometry_cache: dict[Any, Any] = {}
    geometry_resolution_cache: dict[Any, Any] = {}
    original_resolver = engine._resolve_physical_layout
    resolver_attribute = engine.__dict__.get("_resolve_physical_layout", None)
    resolver_was_instance_attribute = "_resolve_physical_layout" in engine.__dict__

    def exact_geometry_resolver(active_section: Any, hoop: Any, design: LongitudinalDesign, *, face: str):
        row_key = tuple(
            (row.count, row.diameter_mm, row.role, row.credited_area_mm2)
            for row in design.rows
        )
        key = (active_section, int(hoop.diameter_mm), face, row_key)
        cached = geometry_resolution_cache.get(key)
        if cached is not None:
            if cached[0]:
                return replace(cached[1], required_area_mm2=design.required_area_mm2)
            error_type, error_args = cached[1]
            raise error_type(*error_args)
        try:
            resolved = original_resolver(active_section, hoop, design, face=face)
        except DetailingError as error:
            geometry_resolution_cache[key] = (False, (type(error), error.args))
            raise
        geometry_resolution_cache[key] = (True, resolved)
        return resolved

    engine._resolve_physical_layout = exact_geometry_resolver
    threshold_values: set[float] = set()
    geometry_checks = 0
    geometry_errors = 0
    area_ineligible = 0
    ratio_ineligible = 0

    try:
        first_diameters, diameters, minimum_count, count_bound, layer_limit = _candidate_parameters(
            section, hoops, engine, first_diameter=first_diameter, max_layers=max_layers
        )
        for rows in _possible_physical_rows(
            section, hoops, engine, first_diameter=first_diameter, max_layers=max_layers
        ):
            if geometry_checks % 64 == 0:
                check_timeout()
            design = LongitudinalDesign(
                rows=rows,
                required_area_mm2=0.0,
                constraint_sources=("FINITE_PHYSICAL_ROWS", "FIRST_ROW_HOOP_SUPPORT"),
            )
            resolved = []
            for hoop in hoops:
                geometry_checks += 1
                try:
                    resolved.append(_resolve_with_geometry_cache(
                        section, hoop, design, engine, face=face,
                        geometry_cache=geometry_cache, search_stats=None,
                    ))
                except DetailingError:
                    geometry_errors += 1
                    resolved = []
                    break
            if not resolved:
                continue
            provided = design.provided_area_mm2
            as_min = max(
                engine.code.minimum_longitudinal_area(
                    section.width_mm, item.effective_depth_mm, section.fc_mpa, section.fy_mpa
                )
                for item in resolved
            )
            if provided + _TOL < as_min:
                area_ineligible += 1
                continue
            if section.is_smf and any(
                provided > engine.project.smf_max_longitudinal_ratio
                * section.width_mm * item.effective_depth_mm + _TOL
                for item in resolved
            ):
                ratio_ineligible += 1
                continue
            threshold_values.add(provided + _TOL)
            if len(rows) == layer_limit:
                if len(rows) == 1:
                    existing_area = 0.0
                    min_count_for_loop = minimum_count
                else:
                    existing_area = sum(float(row.credited_area_mm2 or 0.0) for row in rows[:-1])
                    min_count_for_loop = 2
                final_row = rows[-1]
                area_per_bar = math.pi * final_row.diameter_mm**2 / 4.0
                threshold_values.add(_last_layer_count_event_upper(
                    existing_area, area_per_bar, final_row.count, min_count_for_loop
                ))

        upper_bounds = sorted(threshold_values)
        if not upper_bounds:
            raise LayoutDictionaryError("DICTIONARY_CONTEXT_HAS_NO_GEOMETRIC_LAYOUTS")

        layouts: dict[str, dict[str, Any]] = {}
        layout_ids_by_key: dict[str, str] = {}
        states: list[dict[str, Any]] = []
        state_ids: dict[tuple[str, ...], str] = {}
        interval_state_ids: list[str] = []
        oracle_calls = 0
        for upper_bound in upper_bounds:
            check_timeout()
            oracle_calls += 1
            candidates = oracle_candidates(
                section, hoops, upper_bound, engine,
                first_diameter=first_diameter,
                max_layers=max_layers,
                face=face,
                preserve_support_alternatives=preserve_support_alternatives,
                geometry_cache=geometry_cache,
            )
            ids: list[str] = []
            for candidate in candidates:
                minimums = []
                for hoop in hoops:
                    item = _resolve_with_geometry_cache(
                        section, hoop,
                        LongitudinalDesign(rows=candidate.rows, required_area_mm2=upper_bound),
                        engine, face=face, geometry_cache=geometry_cache, search_stats=None,
                    )
                    minimums.append(engine.code.minimum_longitudinal_area(
                        section.width_mm, item.effective_depth_mm, section.fc_mpa, section.fy_mpa
                    ))
                record = _layout_record(candidate, max(minimums))
                key = _record_key(record)
                layout_id = layout_ids_by_key.get(key)
                if layout_id is None:
                    layout_id = f"L{len(layouts):05d}"
                    layout_ids_by_key[key] = layout_id
                    layouts[layout_id] = record
                ids.append(layout_id)
            state_key = tuple(sorted(set(ids)))
            state_id = state_ids.get(state_key)
            if state_id is None:
                state_id = f"S{len(states):05d}"
                state_ids[state_key] = state_id
                states.append({"state_id": state_id, "layout_ids": list(state_key)})
            interval_state_ids.append(state_id)
    finally:
        if resolver_was_instance_attribute:
            engine._resolve_physical_layout = resolver_attribute
        else:
            delattr(engine, "_resolve_physical_layout")

    return {
        "format": LAYOUT_DICTIONARY_FORMAT,
        "oracle": LAYOUT_DICTIONARY_ORACLE,
        "context": context,
        "context_key": context_key,
        "threshold_semantics": "first upper bound >= required area; bound = provided As + 1e-9 inclusive",
        "upper_bounds_mm2": upper_bounds,
        "interval_state_ids": interval_state_ids,
        "states": states,
        "layouts": layouts,
        "build_audit": {
            "syntactic_layout_geometry_checks": geometry_checks,
            "geometry_cache_unique_resolutions": sum(1 for result in geometry_resolution_cache.values() if result[0]),
            "geometry_ineligible_layouts": geometry_errors,
            "AsMin_ineligible_layouts": area_ineligible,
            "SMF_rho_ineligible_layouts": ratio_ineligible,
            "area_breakpoint_count": len(upper_bounds),
            "oracle_candidate_queries": oracle_calls,
            "interned_layout_count": len(layouts),
            "interned_membership_state_count": len(states),
            "runtime_candidate_generation_calls": 0,
            "runtime_geometry_resolution_calls": 0,
        },
    }


def write_layout_dictionary(path: str | Path, dictionary: dict[str, Any], *, overwrite: bool = False) -> Path:
    destination = Path(path)
    if destination.exists() and not overwrite:
        raise LayoutDictionaryError(f"DICTIONARY_OUTPUT_EXISTS: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(dictionary, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(destination)
    return destination


class LayoutDictionary:
    """Safe JSON loader with exact-context binary-search queries."""

    def __init__(self, data: dict[str, Any]):
        if data.get("format") != LAYOUT_DICTIONARY_FORMAT or data.get("oracle") != LAYOUT_DICTIONARY_ORACLE:
            raise LayoutDictionaryError("DICTIONARY_FORMAT_UNSUPPORTED")
        if _canonical(data.get("context")) != data.get("context_key"):
            raise LayoutDictionaryError("DICTIONARY_CONTEXT_KEY_INVALID")
        if len(data.get("upper_bounds_mm2", ())) != len(data.get("interval_state_ids", ())):
            raise LayoutDictionaryError("DICTIONARY_INTERVAL_INDEX_INVALID")
        self.data = data
        self.query_audit = {
            "dictionary_query_calls": 0,
            "interval_binary_searches": 0,
            "row_candidates_calls": 0,
            "physical_layout_resolver_calls": 0,
            "returned_layout_count": 0,
            "context_misses": 0,
            "area_misses": 0,
        }
        self._states = {item["state_id"]: tuple(item["layout_ids"]) for item in data["states"]}
        self._layouts = data["layouts"]

    @classmethod
    def load(cls, path: str | Path) -> "LayoutDictionary":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise LayoutDictionaryError("DICTIONARY_ROOT_MUST_BE_OBJECT")
        return cls(data)

    def query(self, required_area_mm2: float, section: Any, hoops: Iterable[Any], engine: Any, *,
              first_diameter: int | None = None, max_layers: int | None = None, face: str = "top",
              preserve_support_alternatives: bool = True) -> tuple[LongitudinalDesign, ...]:
        self.query_audit["dictionary_query_calls"] += 1
        required = float(required_area_mm2)
        if not math.isfinite(required) or required < 0:
            raise LayoutDictionaryError("DICTIONARY_REQUIRED_AREA_INVALID")
        context = _context_record(
            section, tuple(hoops), engine, first_diameter=first_diameter,
            max_layers=max_layers, face=face,
            preserve_support_alternatives=preserve_support_alternatives,
        )
        if _canonical(context) != self.data["context_key"]:
            self.query_audit["context_misses"] += 1
            raise LayoutDictionaryMiss("DICT_MISS: exact section/policy/candidate context is not compiled")
        return self._query_records(required, section, engine)

    def _query_records(self, required: float, section: Any, engine: Any) -> tuple[LongitudinalDesign, ...]:
        self.query_audit["interval_binary_searches"] += 1
        interval_index = bisect_left(self.data["upper_bounds_mm2"], required)
        if interval_index >= len(self.data["upper_bounds_mm2"]):
            self.query_audit["area_misses"] += 1
            return ()
        state_id = self.data["interval_state_ids"][interval_index]
        ids = self._states[state_id]
        layouts: list[LongitudinalDesign] = []
        for layout_id in ids:
            record = self._layouts[layout_id]
            rows = tuple(PhysicalRow(
                int(row["count"]), int(row["diameter_mm"]), str(row["role"]),
                float(row["credited_area_mm2"]),
            ) for row in record["rows"])
            layouts.append(LongitudinalDesign(
                rows=rows,
                required_area_mm2=max(required, float(record["as_min_mm2"])),
                clear_spacing_mm=float(record["clear_spacing_mm"]),
                row_centers_mm=tuple(float(value) for value in record["row_centers_mm"]),
                row_x_mm=tuple(tuple(float(value) for value in row) for row in record["row_x_mm"]),
                effective_depth_mm=float(record["effective_depth_mm"]),
                constraint_sources=tuple(str(value) for value in record["constraint_sources"]),
            ))
        layouts.sort(key=lambda item: _candidate_sort_key(item, section, engine))
        self.query_audit["returned_layout_count"] += len(layouts)
        return tuple(layouts)


_IGNORED_SECTION_CONTEXT_FIELDS = frozenset({
    "name",
    "length_mm",
    "etabs_longitudinal_cover_source",
    "clear_cover_source",
    "max_aggregate_size_source",
    "profile_source",
})


def normalized_bank_context(context: dict[str, Any]) -> dict[str, Any]:
    """Normalize only fields proven irrelevant to row generation/geometry."""
    normalized = json.loads(_canonical(context))
    section = normalized["section"]
    for field_name in _IGNORED_SECTION_CONTEXT_FIELDS:
        section.pop(field_name, None)
    normalized["engine"].pop("ranking_mode", None)
    return normalized


def normalized_bank_context_for(section: Any, hoops: Iterable[Any], engine: Any, *, first_diameter: int | None,
                                max_layers: int | None, face: str,
                                preserve_support_alternatives: bool) -> dict[str, Any]:
    strict = _context_record(
        section, hoops, engine, first_diameter=first_diameter, max_layers=max_layers,
        face=face, preserve_support_alternatives=preserve_support_alternatives,
    )
    return normalized_bank_context(strict)


def _bank_key(normalized_context: dict[str, Any]) -> str:
    return _canonical(normalized_context)


def build_layout_dictionary_bank(dictionaries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Combine strict context dictionaries, deduplicating proven-equivalent keys."""
    entries_by_key: dict[str, dict[str, Any]] = {}
    source_count = 0
    for data in dictionaries:
        source_count += 1
        LayoutDictionary(data)  # validate the embedded dictionary before banking it
        if data["context"]["candidate_controls"].get("max_layers") is None:
            raise LayoutDictionaryError("DICT_BANK_REQUIRES_EXPLICIT_MAX_LAYERS")
        normalized = normalized_bank_context(data["context"])
        key = _bank_key(normalized)
        existing = entries_by_key.get(key)
        if existing is not None:
            first = existing["dictionary"]
            relevant = ("upper_bounds_mm2", "interval_state_ids", "states", "layouts")
            if any(first.get(field) != data.get(field) for field in relevant):
                raise LayoutDictionaryError("DICT_BANK_NORMALIZED_CONTEXT_CONFLICT")
            existing["source_context_count"] += 1
            continue
        entries_by_key[key] = {
            "normalized_context": normalized,
            "normalized_context_key": key,
            "source_context_count": 1,
            "dictionary": data,
        }
    if not entries_by_key:
        raise LayoutDictionaryError("DICT_BANK_REQUIRES_AT_LEAST_ONE_CONTEXT")
    entries = [entries_by_key[key] for key in sorted(entries_by_key)]
    return {
        "format": LAYOUT_DICTIONARY_BANK_FORMAT,
        "normalization": LAYOUT_DICTIONARY_BANK_NORMALIZATION,
        "oracle": LAYOUT_DICTIONARY_ORACLE,
        "entries": entries,
        "bank_audit": {
            "source_dictionary_count": source_count,
            "deduplicated_context_count": len(entries),
            "normalized_duplicate_count": source_count - len(entries),
        },
    }


def write_layout_dictionary_bank(path: str | Path, bank: dict[str, Any], *, overwrite: bool = False) -> Path:
    destination = Path(path)
    if destination.exists() and not overwrite:
        raise LayoutDictionaryError(f"DICTIONARY_BANK_OUTPUT_EXISTS: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(bank, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(destination)
    return destination


class LayoutDictionaryBank:
    """Versioned multi-context bank with exact R6/R7 policy revision keys."""

    def __init__(self, data: dict[str, Any], *, source_path: str | Path | None = None):
        if (data.get("format") != LAYOUT_DICTIONARY_BANK_FORMAT
                or data.get("normalization") != LAYOUT_DICTIONARY_BANK_NORMALIZATION
                or data.get("oracle") != LAYOUT_DICTIONARY_ORACLE):
            raise LayoutDictionaryError("DICT_BANK_FORMAT_UNSUPPORTED")
        self.data = data
        self.source_path = str(Path(source_path).resolve()) if source_path is not None else None
        self._entries: dict[str, tuple[dict[str, Any], LayoutDictionary]] = {}
        for entry in data.get("entries", ()):
            normalized = entry.get("normalized_context")
            key = entry.get("normalized_context_key")
            if not isinstance(normalized, dict) or _bank_key(normalized) != key:
                raise LayoutDictionaryError("DICT_BANK_CONTEXT_KEY_INVALID")
            inner = LayoutDictionary(entry.get("dictionary", {}))
            if normalized_bank_context(inner.data["context"]) != normalized:
                raise LayoutDictionaryError("DICT_BANK_EMBEDDED_CONTEXT_MISMATCH")
            if key in self._entries:
                raise LayoutDictionaryError("DICT_BANK_DUPLICATE_NORMALIZED_CONTEXT")
            self._entries[key] = (normalized, inner)
        if not self._entries:
            raise LayoutDictionaryError("DICT_BANK_REQUIRES_AT_LEAST_ONE_CONTEXT")
        self.query_audit = {
            "bank_query_calls": 0,
            "context_hits": 0,
            "context_misses": 0,
            "area_misses": 0,
            "returned_layout_count": 0,
            "row_candidates_calls": 0,
            "physical_layout_resolver_calls": 0,
        }

    @classmethod
    def load(cls, path: str | Path) -> "LayoutDictionaryBank":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise LayoutDictionaryError("DICT_BANK_ROOT_MUST_BE_OBJECT")
        return cls(data, source_path=path)

    @property
    def context_count(self) -> int:
        return len(self._entries)

    def metadata(self) -> dict[str, Any]:
        return {
            "mode": "PRECOMPUTED_PHYSICAL_ROW_LOOKUP",
            "schema": LAYOUT_DICTIONARY_BANK_FORMAT,
            "normalization": LAYOUT_DICTIONARY_BANK_NORMALIZATION,
            "path": self.source_path,
            "context_count": self.context_count,
            "query_audit": dict(self.query_audit),
            "bank_audit": dict(self.data.get("bank_audit", {})),
        }

    def query(self, required_area_mm2: float, section: Any, hoops: Iterable[Any], engine: Any, *,
              first_diameter: int | None = None, max_layers: int | None = None, face: str = "top",
              preserve_support_alternatives: bool = True) -> tuple[LongitudinalDesign, ...]:
        self.query_audit["bank_query_calls"] += 1
        if (engine.detailing_policy_id != "drawing_20260924_v1"
                or engine.detailing_policy_revision_id not in {DRAWING_POLICY_REVISION_R6_ID, DRAWING_POLICY_REVISION_R7_ID, DRAWING_POLICY_REVISION_R8_ID}):
            raise LayoutDictionaryMiss("DICT_MISS: layout dictionary bank requires explicit R6/R7 engine")
        required = float(required_area_mm2)
        if not math.isfinite(required) or required < 0:
            raise LayoutDictionaryError("DICTIONARY_REQUIRED_AREA_INVALID")
        if max_layers is None:
            self.query_audit["context_misses"] += 1
            raise LayoutDictionaryMiss("DICT_MISS: bank requires an explicit max_layers control")
        normalized = normalized_bank_context_for(
            section, tuple(hoops), engine, first_diameter=first_diameter,
            max_layers=max_layers, face=face,
            preserve_support_alternatives=preserve_support_alternatives,
        )
        key = _bank_key(normalized)
        entry = self._entries.get(key)
        if entry is None:
            self.query_audit["context_misses"] += 1
            raise LayoutDictionaryMiss("DICT_MISS: normalized physical context is not compiled")
        self.query_audit["context_hits"] += 1
        _, dictionary = entry
        before_area_misses = dictionary.query_audit["area_misses"]
        result = dictionary._query_records(required, section, engine)
        if dictionary.query_audit["area_misses"] > before_area_misses:
            self.query_audit["area_misses"] += 1
        self.query_audit["returned_layout_count"] += len(result)
        return result
