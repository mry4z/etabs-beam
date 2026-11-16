"""Lightweight hard checks for V3 candidates before result construction."""

from __future__ import annotations

import math
from collections import OrderedDict
from typing import Sequence

from .constructability import evaluate_smf_longitudinal
from .detailing import DetailingError, RegionDetailing


V3_HARD_CHECK_CACHE_MAX_ENTRIES = 4096
_CACHE_MISS = object()


def prepare_light_regions(data, regions: Sequence[RegionDetailing], engine) -> tuple[RegionDetailing, ...]:
    """Validate selected V3 physical rows and the existing selected hoops.

    This deliberately does not construct a BeamDetailingResult or invoke the
    row search/finalization pipeline.  It keeps only candidate-local hard
    checks needed before a complete recommendation path is materialized.
    """

    section = data.section
    selected = tuple(regions)
    if len(selected) != 3:
        raise DetailingError(f"{section.name}: V3_HARD_CHECK_REQUIRES_THREE_REGIONS")

    # Preserve whole-span checks (notably uniform stirrup diameter) before
    # memoizing any independent region result.
    engine._validate_revision_layout(section, selected)
    allowed_main = set(engine.project.ordered_longitudinal_diameters_for(section))
    allowed_hoops = set(engine.project.ordered_stirrup_diameters())
    main_layer_limit = int(engine.project.max_main_bar_layers)
    cache, stats = _cache_state(engine)
    finalized = tuple(
        _cached_region_hard_checks(
            data,
            region,
            engine,
            allowed_main=allowed_main,
            allowed_hoops=allowed_hoops,
            main_layer_limit=main_layer_limit,
            cache=cache,
            stats=stats,
        )
        for region in selected
    )

    if section.is_smf:
        effective_depth = min(
            design.effective_depth_mm or engine.code.effective_depth(
                section,
                region.stirrup.diameter_mm,
                engine._final_main_db(region, section),
            )
            for region in finalized
            for design in (region.top, region.bottom)
        )
        checks = evaluate_smf_longitudinal(
            width_mm=section.width_mm,
            fc_mpa=section.fc_mpa,
            fy_mpa=section.fy_mpa,
            effective_depth_mm=effective_depth,
            left_top=finalized[0].top,
            left_bottom=finalized[0].bottom,
            middle_top=finalized[1].top,
            middle_bottom=finalized[1].bottom,
            right_top=finalized[2].top,
            right_bottom=finalized[2].bottom,
            maximum_ratio=engine.project.smf_max_longitudinal_ratio,
        )
        failures = sorted({
            check.status for check in checks
            if check.status not in {"PASS", "INFO", "WARN"}
        })
        if failures:
            raise DetailingError("V3_SMF_LONGITUDINAL_HARD_CHECK_FAILED:" + ",".join(failures))

    return finalized


def v3_hard_check_cache_statistics(engine) -> dict[str, int]:
    """Return a copy of this engine's bounded V3 region-check cache counters."""

    cache = getattr(engine, "_v3_hard_check_region_cache", None)
    stats = getattr(engine, "_v3_hard_check_cache_stats", None) or {}
    return {
        "capacity": V3_HARD_CHECK_CACHE_MAX_ENTRIES,
        "entries": len(cache) if isinstance(cache, OrderedDict) else 0,
        **{name: int(stats.get(name, 0)) for name in (
            "lookups", "hits", "success_hits", "failure_hits", "misses",
            "success_stores", "failure_stores", "evictions",
        )},
    }


def _cache_state(engine):
    cache = getattr(engine, "_v3_hard_check_region_cache", None)
    if not isinstance(cache, OrderedDict):
        cache = OrderedDict()
        engine._v3_hard_check_region_cache = cache
    stats = getattr(engine, "_v3_hard_check_cache_stats", None)
    if not isinstance(stats, dict):
        stats = {
            "lookups": 0,
            "hits": 0,
            "success_hits": 0,
            "failure_hits": 0,
            "misses": 0,
            "success_stores": 0,
            "failure_stores": 0,
            "evictions": 0,
        }
        engine._v3_hard_check_cache_stats = stats
    return cache, stats


def _cache_key(data, region, engine):
    code = engine.code
    base_code = getattr(code, "base_policy", code)
    code_context = (
        type(code).__module__,
        type(code).__qualname__,
        id(code),
        id(base_code),
        getattr(code, "edition", None),
        getattr(code, "version", None),
        getattr(code, "policy_id", None),
        getattr(code, "spacing_rule_id", None),
        repr(base_code),
    )
    engine_context = (
        _cacheable(engine.project),
        code_context,
        getattr(engine, "detailing_policy_id", None),
        getattr(engine, "detailing_policy_revision_id", None),
        getattr(engine, "coordination_solver_revision_id", None),
        bool(getattr(engine, "strict_symmetric_rows", False)),
        bool(getattr(engine, "avoid_two_row_4_3", False)),
        bool(getattr(engine, "uniform_stirrup_diameter_per_span", False)),
    )
    return (_cacheable(data.section), _cacheable(region), engine_context)


def _cacheable(value):
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _cached_region_hard_checks(
    data,
    region,
    engine,
    *,
    allowed_main,
    allowed_hoops,
    main_layer_limit,
    cache,
    stats,
):
    section = data.section
    key = _cache_key(data, region, engine)
    stats["lookups"] += 1
    entry = cache.get(key, _CACHE_MISS)
    if entry is not _CACHE_MISS:
        cache.move_to_end(key)
        stats["hits"] += 1
        successful, value, arguments = entry
        if successful:
            stats["success_hits"] += 1
            return value
        stats["failure_hits"] += 1
        raise value(*arguments)

    stats["misses"] += 1
    try:
        finalized = _prepare_region_hard_checks(
            data,
            region,
            engine,
            allowed_main=allowed_main,
            allowed_hoops=allowed_hoops,
            main_layer_limit=main_layer_limit,
        )
    except (DetailingError, ValueError) as exc:
        _store_cache_entry(cache, stats, key, (False, type(exc), exc.args))
        stats["failure_stores"] += 1
        raise
    _store_cache_entry(cache, stats, key, (True, finalized, ()))
    stats["success_stores"] += 1
    return finalized


def _store_cache_entry(cache, stats, key, entry) -> None:
    cache[key] = entry
    cache.move_to_end(key)
    while len(cache) > V3_HARD_CHECK_CACHE_MAX_ENTRIES:
        cache.popitem(last=False)
        stats["evictions"] += 1


def _prepare_region_hard_checks(
    data,
    region,
    engine,
    *,
    allowed_main,
    allowed_hoops,
    main_layer_limit,
):
    section = data.section
    hoop = region.stirrup
    if hoop.diameter_mm not in allowed_hoops:
        raise DetailingError(f"{section.name}/{region.name}: V3_HOOP_DIAMETER_OUTSIDE_CATALOGUE")
    allowed_spacings = set(engine.project.allowed_spacings_for(section, region.name))
    if hoop.spacing_mm not in allowed_spacings:
        raise DetailingError(f"{section.name}/{region.name}: V3_HOOP_SPACING_OUTSIDE_CATALOGUE")
    if not isinstance(hoop.legs, int) or hoop.legs < 2 or hoop.legs % 2:
        raise DetailingError(f"{section.name}/{region.name}: V3_HOOP_LEG_COUNT_INVALID")
    minimum_legs = max(2, int(engine.code.minimum_legs(section, region.name)))
    if hoop.legs < minimum_legs:
        raise DetailingError(
            f"{section.name}/{region.name}: V3_MINIMUM_HOOP_LEG_COUNT_FAILED"
        )

    for face_name, design in (("top", region.top), ("bottom", region.bottom)):
        rows = tuple(design.rows)
        if not rows:
            raise DetailingError(f"{section.name}/{region.name}/{face_name}: V3_PHYSICAL_ROWS_REQUIRED")
        if not any(row.role != "hanger" for row in rows):
            raise DetailingError(f"{section.name}/{region.name}/{face_name}: V3_MAIN_PHYSICAL_ROW_REQUIRED")
        if sum(row.role != "hanger" for row in rows) > main_layer_limit:
            raise DetailingError(f"{section.name}/{region.name}/{face_name}: V3_MAX_MAIN_ROW_COUNT_EXCEEDED")
        if rows[0].count < hoop.legs:
            raise DetailingError(f"{section.name}/{region.name}/{face_name}: FIRST_ROW_HOOP_SUPPORT_FAILED")

        for row in rows:
            if row.count < 2 or row.diameter_mm not in engine.project.catalog.diameters_mm:
                raise DetailingError(f"{section.name}/{region.name}/{face_name}: V3_PHYSICAL_ROW_INVALID")
            if row.role == "longitudinal":
                if row.diameter_mm not in allowed_main:
                    raise DetailingError(f"{section.name}/{region.name}/{face_name}: V3_MAIN_BAR_DIAMETER_OUTSIDE_ROLE_CATALOGUE")
            elif row.role == "hanger":
                if not section.is_secondary or row.diameter_mm != engine.project.sb_hanger_diameter_mm:
                    raise DetailingError(f"{section.name}/{region.name}/{face_name}: V3_HANGER_ROW_INVALID")
            elif row.role == "minimum_credit_hanger":
                if (
                    not section.is_secondary
                    or row.diameter_mm != engine.project.minimum_credit_hanger_diameter_mm
                ):
                    raise DetailingError(f"{section.name}/{region.name}/{face_name}: V3_MINIMUM_CREDIT_HANGER_INVALID")
            else:
                raise DetailingError(f"{section.name}/{region.name}/{face_name}: V3_PHYSICAL_ROW_ROLE_INVALID")

        if getattr(engine, "strict_symmetric_rows", False):
            _validate_symmetric_row_slots(section, region.name, face_name, design)

    # Recheck only this chosen hoop.  Revision and cross-region restrictions
    # were checked for the original three-region tuple before this cache lookup.
    finalized = tuple(engine._finalize_stirrup_controls(section, (region,)))
    if len(finalized) != 1:
        raise DetailingError(f"{section.name}/{region.name}: V3_STIRRUP_FINALIZATION_ALIGNMENT_ERROR")
    finalized_region = finalized[0]
    for face_name, design, total_demand in (
        ("top", finalized_region.top, finalized_region.demand.as_top_total_mm2),
        ("bottom", finalized_region.bottom, finalized_region.demand.as_bottom_total_mm2),
    ):
        effective_depth = design.effective_depth_mm or engine.code.effective_depth(
            section,
            finalized_region.stirrup.diameter_mm,
            engine._final_main_db(finalized_region, section),
        )
        if not math.isfinite(float(effective_depth)) or effective_depth <= 0:
            raise DetailingError(f"{section.name}/{region.name}/{face_name}: V3_EFFECTIVE_DEPTH_INVALID")
        as_min = engine.code.minimum_longitudinal_area(
            section.width_mm,
            effective_depth,
            section.fc_mpa,
            section.fy_mpa,
        )
        required = max(float(total_demand), float(finalized_region.as_min_mm2), float(as_min))
        if not math.isfinite(float(design.provided_area_mm2)) or design.provided_area_mm2 + 1e-9 < required:
            raise DetailingError(
                f"{section.name}/{region.name}/{face_name}: V3_TOTAL_LONGITUDINAL_AREA_FAILED"
            )
    return finalized_region


def _validate_symmetric_row_slots(section, region_name: str, face_name: str, design) -> None:
    rows = tuple(design.rows)
    row_x = tuple(tuple(float(x) for x in row) for row in design.row_x_mm)
    prefix = f"{section.name}/{region_name}/{face_name}"
    if len(row_x) != len(rows):
        raise DetailingError(f"{prefix}: V3_SYMMETRIC_ROW_GEOMETRY_MISSING")
    previous: tuple[float, ...] | None = None
    for index, (physical_row, slots) in enumerate(zip(rows, row_x)):
        if (
            len(slots) != physical_row.count
            or not slots
            or any(not math.isfinite(value) for value in slots)
            or tuple(sorted(slots)) != slots
        ):
            raise DetailingError(f"{prefix}: V3_SYMMETRIC_ROW_GEOMETRY_INVALID")
        if any(
            abs(left + right - section.width_mm) > 1e-5
            for left, right in zip(slots, reversed(slots))
        ):
            raise DetailingError(f"{prefix}: V3_ROW_MIRROR_SYMMETRY_FAILED")
        if index and previous is not None and any(
            not any(abs(value - prior) <= 1e-5 for prior in previous)
            for value in slots
        ):
            raise DetailingError(f"{prefix}: V3_INNER_ROW_MIRROR_SUBSET_FAILED")
        previous = slots
