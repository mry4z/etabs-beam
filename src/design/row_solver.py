"""Finite physical-row solving with final-geometry transverse feedback."""
import copy
from dataclasses import astuple, fields, replace
from contextlib import nullcontext
import heapq
import math
import time
import weakref
from collections import OrderedDict
from itertools import product
from .detailing import (
    BeamDetailingProfile, DetailingError, RegionDetailing, ReinforcementMode,
    StirrupDesign,
    LongitudinalDesign, PhysicalRow,
    R7_SPAN_CACHE_MAX_ENTRIES,
    _refresh_result_physical_layout,
)
from .row_candidates import (
    R6_GEOMETRY_CACHE_MAX_ENTRIES,
    approved_pattern_candidates,
    candidates,
    signature,
)


# A single beam line can have a finite but very large support-state search
# when no common physical-row layout exists.  Keep that failure auditable and
# bounded so one pathological line cannot hold a full-model export forever.
MAX_COORDINATION_SEARCH_STATES = 5_000
R6_CANDIDATE_SEARCH_STRATEGY = "R6_EXACT_CANDIDATE_STATE_DEDUP_V1"
R6_CANDIDATE_SEARCH_AUDIT_VERSION = "R6_CANDIDATE_SEARCH_AUDIT_V3"
COORDINATION_SOLVER_REVISION = "coordination_20261028_support_pool_v1"
INDEXED_COORDINATION_SOLVER_REVISION = "coordination_20261031_indexed_candidates_v1"


def _indexed_coordination(engine):
    return getattr(engine, "coordination_solver_revision_id", None) == INDEXED_COORDINATION_SOLVER_REVISION


_R9_ASTUPLE_ATOMIC_TYPES = (type(None), bool, int, float, complex, str, bytes)


def _is_dataclass_instance(value):
    return hasattr(type(value), "__dataclass_fields__")


def _r9_astuple_inner(value, tuple_factory):
    if type(value) in _R9_ASTUPLE_ATOMIC_TYPES:
        return value
    if _is_dataclass_instance(value):
        result = [_r9_astuple_inner(getattr(value, field.name), tuple_factory)
                  for field in fields(value)]
        return tuple_factory(result)
    if isinstance(value, tuple) and hasattr(value, "_fields"):
        return type(value)(*[_r9_astuple_inner(item, tuple_factory) for item in value])
    if isinstance(value, (list, tuple)):
        return type(value)(_r9_astuple_inner(item, tuple_factory) for item in value)
    if isinstance(value, dict):
        return type(value)((_r9_astuple_inner(key, tuple_factory),
                            _r9_astuple_inner(item, tuple_factory))
                           for key, item in value.items())
    return copy.deepcopy(value)


def _r9_fast_astuple(value, tuple_factory=tuple):
    if not _is_dataclass_instance(value):
        raise TypeError("astuple() should be called on dataclass instances")
    return _r9_astuple_inner(value, tuple_factory)


class _R9SignatureCache(dict):
    """Mark only R9 signature caches for the exact atomic fast path."""


def _engine_astuple(value, engine):
    return _r9_fast_astuple(value) if _indexed_coordination(engine) else astuple(value)


def _signature_astuple(value, signature_cache):
    return _r9_fast_astuple(value) if isinstance(signature_cache, _R9SignatureCache) else astuple(value)


def _revised_coordination(engine):
    return getattr(engine, "coordination_solver_revision_id", None) in (
        COORDINATION_SOLVER_REVISION, INDEXED_COORDINATION_SOLVER_REVISION
    )


class _CoordinationSearchLimit(DetailingError):
    """Internal fail-closed signal for an exhausted line-search budget."""


def _style_candidate_pool_context(engine):
    return (
        engine.detailing_policy_id,
        engine.detailing_policy_revision_id,
        engine.ranking_mode,
        str(engine.rules_path or ""),
        id(engine.code),
        id(engine.project),
        tuple(engine.project.enabled_diameters_mm),
        tuple(engine.project.allowed_spacings_mm),
        tuple(engine.project.smf_allowed_spacings_mm),
    )


def _store_style_candidate_pool(engine, selected, pool, *, complete=True):
    cache = getattr(engine, "_style_candidate_pool_cache", None)
    if cache is None or not pool:
        return
    cache_key = id(selected)
    context = _style_candidate_pool_context(engine)
    lightweight_pool = tuple(replace(candidate, input_data=None) for candidate in pool)

    def discard(reference):
        entry = cache.get(cache_key)
        if entry is not None and entry[0] is reference:
            cache.pop(cache_key, None)

    reference = weakref.ref(selected, discard)
    cache[cache_key] = (reference, context, lightweight_pool, bool(complete))


def _take_style_candidate_pool(engine, selected, *, allow_incomplete=False):
    cache = getattr(engine, "_style_candidate_pool_cache", None)
    if cache is None:
        return None
    entry = cache.pop(id(selected), None)
    if entry is None:
        return None
    reference, context, pool, complete = entry
    if reference() is not selected or context != _style_candidate_pool_context(engine):
        return None
    if not complete and not allow_incomplete:
        raise _CoordinationSearchLimit(
            f"ROW_SEGMENT_SEARCH_LIMIT: max_states={MAX_COORDINATION_SEARCH_STATES}; "
            "INITIAL_SPAN_CANDIDATE_POOL_INCOMPLETE"
        )
    if selected.input_data is None:
        return None
    return tuple(replace(candidate, input_data=selected.input_data) for candidate in pool)


def _checked_state_cache(engine):
    """Share fully checked physical states across initial and constrained searches."""
    if not _indexed_coordination(engine):
        return None
    cache = getattr(engine, "_indexed_checked_state_cache", None)
    if cache is None:
        cache = engine._indexed_checked_state_cache = OrderedDict()
    return cache


def _remember_checked_state(cache, key, result):
    if cache is not None:
        cache[key] = result
        cache.move_to_end(key)
        if len(cache) > 4096:
            cache.popitem(last=False)


def _consume_search_budget(budget):
    if budget is None:
        return
    budget[0] -= 1
    if budget[0] < 0:
        raise _CoordinationSearchLimit(
            f"ROW_SEGMENT_SEARCH_LIMIT: max_states={MAX_COORDINATION_SEARCH_STATES}"
        )


def _new_r6_candidate_search_stats():
    return {
        "phase": "SPAN_CANDIDATE_GENERATION",
        "budget_limit": MAX_COORDINATION_SEARCH_STATES,
        "budget_basis": "UNIQUE_CANDIDATE_VALIDATION_STATES",
        "raw_layout_combinations": 0,
        "exact_duplicate_states_skipped": 0,
        "unique_candidate_validation_states": 0,
        "base_candidate_validation_states": 0,
        "economic_alternative_states": 0,
        "economic_candidate_validation_states": 0,
        "candidate_row_result_build_calls": 0,
        "finalize_rows_calls": 0,
        "economic_hoop_trial_calls": 0,
        "economic_hoop_trial_unique_states": 0,
        "economic_hoop_trial_cache_hits": 0,
        "last_layer_area_count_options_pruned": 0,
        "last_layer_area_states_pruned": 0,
        "frozen_signature_calls": 0,
        "frozen_signature_computations": 0,
        "frozen_signature_cache_hits": 0,
        "physical_layout_resolver_calls": 0,
        "physical_layout_resolver_unique_states": 0,
        "physical_layout_resolver_cache_hits": 0,
        "physical_layout_resolver_cache_bypasses": 0,
        "physical_layout_resolver_cache_entries_peak": 0,
        "physical_layout_resolver_cache_limit": R6_GEOMETRY_CACHE_MAX_ENTRIES,
        "style_context_requests": 0,
        "style_context_cache_hits": 0,
        "style_context_compilations": 0,
        "style_compiled_pattern_entries": 0,
        "style_matched_field_calls": 0,
        "style_actual_signatures_computed": 0,
        "style_compiled_pattern_lookups": 0,
        "style_compiled_pattern_hits": 0,
        "search_complete": True,
    }


def _r6_frozen_signature(value, signature_cache, search_stats=None):
    """Reuse a dataclass signature while retaining its immutable source object."""

    if search_stats is not None:
        search_stats["frozen_signature_calls"] += 1
    if signature_cache is None:
        if search_stats is not None:
            search_stats["frozen_signature_computations"] += 1
        return _signature_astuple(value, signature_cache)
    identity = id(value)
    cached = signature_cache.get(identity)
    if cached is not None and cached[0] is value:
        if search_stats is not None:
            search_stats["frozen_signature_cache_hits"] += 1
        return cached[1]
    signature = _signature_astuple(value, signature_cache)
    # Keep a strong reference so the identity key cannot be reused during the
    # span search. All source records here are frozen dataclasses.
    signature_cache[identity] = (value, signature)
    if search_stats is not None:
        search_stats["frozen_signature_computations"] += 1
    return signature


def _r6_candidate_state_key(data_key, hoops, top_layouts, bottom_layouts, face_rebar,
                            *, signature_cache=None, search_stats=None):
    """Full physical/input key for exact duplicate suppression within one span."""

    return (
        data_key,
        tuple(_r6_frozen_signature(hoop, signature_cache, search_stats) for hoop in hoops),
        tuple(_r6_frozen_signature(layout, signature_cache, search_stats) for layout in top_layouts),
        tuple(_r6_frozen_signature(layout, signature_cache, search_stats) for layout in bottom_layouts),
        _r6_frozen_signature(face_rebar, signature_cache, search_stats),
    )


def _r6_result_state_key(data_key, regions, face_rebar, *, signature_cache=None,
                         search_stats=None):
    """Exact post-capacity state key for economic alternative candidates."""

    return (
        data_key,
        tuple(_r6_frozen_signature(region, signature_cache, search_stats) for region in regions),
        _r6_frozen_signature(face_rebar, signature_cache, search_stats),
    )


def _r6_candidate_search_audit(stats, *, complete, budget_remaining):
    return {
        "strategy": R6_CANDIDATE_SEARCH_STRATEGY,
        "audit_version": R6_CANDIDATE_SEARCH_AUDIT_VERSION,
        **stats,
        "search_complete": bool(complete),
        "budget_remaining": int(budget_remaining),
    }


def _record_r6_style_lookup_stats(stats, library, snapshot):
    if stats is None or snapshot is None:
        return {}
    delta = library.r6_lookup_counters_since(snapshot)
    prefixed = {f"style_{name}": value for name, value in delta.items()}
    stats.update(prefixed)
    return prefixed


def _combinations(groups, budget=None, *, skip_if=None, stop_if=None, expand_axes=None):
    if any(not group for group in groups):
        return
    start = (0,) * len(groups)
    queue, seen = [(0, start)], {start}
    while queue and (stop_if is None or not stop_if()):
        _, indices = heapq.heappop(queue)
        combination = tuple(group[index] for group, index in zip(groups, indices))
        # Style coordination pools retain only the first hard-valid candidate
        # for a normalized boundary support state.  Once complete, skip the
        # remaining count variants before charging the finite search budget.
        if skip_if is None or not skip_if(combination):
            _consume_search_budget(budget)
            yield combination
        # Ask after the consumer resumes: accepting the yielded candidate may
        # make its support class redundant before its neighbours are queued.
        axes = range(len(groups)) if expand_axes is None else expand_axes(combination)
        for axis in axes:
            group = groups[axis]
            if indices[axis] + 1 >= len(group):
                continue
            following = (*indices[:axis], indices[axis] + 1, *indices[axis+1:])
            if following not in seen:
                seen.add(following)
                heapq.heappush(queue, (sum(following), following))


def _hoop_signature(hoops):
    return tuple((h.diameter_mm, h.legs, h.spacing_mm) for h in hoops)


def _initial_hoops(data, engine):
    # In clear-cover mode a larger assumed bar reduces d. Its spacing
    # failure cannot rule out a smaller bar before actual rows are selected.
    failures = []
    for db in sorted(engine.project.ordered_longitudinal_diameters_for(data.section), reverse=True):
        try:
            if data.section.is_secondary:
                hoop = engine._select_secondary_span_stirrup(data.section, data.regions, (db,)*3)
                return (hoop,)*3
            if engine.uniform_stirrup_diameter_per_span:
                for stirrup_diameter in engine.project.ordered_stirrup_diameters():
                    try:
                        hoops = tuple(
                            engine._select_stirrup(
                                data.section,
                                demand,
                                db,
                                fixed_diameter_mm=stirrup_diameter,
                            )
                            for demand in data.regions
                        )
                        return tuple(
                            engine._with_legs(hoop, max(item.legs for item in hoops))
                            for hoop in hoops
                        )
                    except DetailingError as error:
                        if "NO_ALLOWED_SPACING" not in str(error):
                            raise
                        failures.append(error)
                continue
            hoops = tuple(engine._select_stirrup(data.section, demand, db) for demand in data.regions)
            return tuple(engine._with_legs(h, max(item.legs for item in hoops)) for h in hoops)
        except DetailingError as error:
            if "NO_ALLOWED_SPACING" not in str(error):
                raise
            failures.append(error)
    if failures:
        raise failures[0]
    raise DetailingError("EMPTY_LONGITUDINAL_CATALOGUE")


def _fit_hoops(result, engine, *, preserve_current=False):
    scope = getattr(engine, "_r7_span_caches", None)
    if (not _indexed_coordination(engine) or not engine.etabs_design_results_only
            or not getattr(engine, "_r7_span_caches_enabled", False) or scope is None):
        return _uncached_fit_hoops(result, engine, preserve_current=preserve_current)
    section = result.section
    key = (result.input_data, section, bool(preserve_current), tuple(
        (region.name, region.stirrup, engine._final_main_db(region, section),
         min(region.top.effective_depth_mm, region.bottom.effective_depth_mm))
        for region in result.regions))
    cache = scope.setdefault("indexed_fit_hoops", {})
    stats = engine._r7_cache_statistics
    stats["indexed_fit_hoops_requests"] = stats.get("indexed_fit_hoops_requests", 0) + 1
    cached = cache.get(key)
    if cached is not None:
        stats["indexed_fit_hoops_hits"] = stats.get("indexed_fit_hoops_hits", 0) + 1
        succeeded, value = cached
        if succeeded:
            return value
        error_type, error_args = value
        raise error_type(*error_args)
    stats["indexed_fit_hoops_computations"] = stats.get("indexed_fit_hoops_computations", 0) + 1
    try:
        value = _uncached_fit_hoops(result, engine, preserve_current=preserve_current)
    except DetailingError as error:
        if len(cache) < R7_SPAN_CACHE_MAX_ENTRIES:
            cache[key] = (False, (type(error), error.args))
        raise
    if len(cache) < R7_SPAN_CACHE_MAX_ENTRIES:
        cache[key] = (True, value)
    return value


def _fit_region_hoop_choices(section, demand, region, engine):
    scope = getattr(engine, "_r7_span_caches", None)
    if (not _indexed_coordination(engine) or not engine.etabs_design_results_only
            or not getattr(engine, "_r7_span_caches_enabled", False) or scope is None):
        return _compute_fit_region_hoop_choices(section, demand, region, engine)
    db = engine._final_main_db(region, section)
    depth = min(region.top.effective_depth_mm, region.bottom.effective_depth_mm)
    key = (section, demand, region.name, region.stirrup, db, depth)
    cache = scope.setdefault("indexed_fit_region_choices", {})
    stats = engine._r7_cache_statistics
    stats["indexed_fit_region_requests"] = stats.get("indexed_fit_region_requests", 0) + 1
    cached = cache.get(key)
    if cached is not None:
        stats["indexed_fit_region_hits"] = stats.get("indexed_fit_region_hits", 0) + 1
        return cached
    stats["indexed_fit_region_computations"] = stats.get("indexed_fit_region_computations", 0) + 1
    choices = _compute_fit_region_hoop_choices(section, demand, region, engine)
    if choices and len(cache) < R7_SPAN_CACHE_MAX_ENTRIES:
        choices = tuple(choices)
        cache[key] = choices
    return choices


def _compute_fit_region_hoop_choices(section, demand, region, engine):
    choices = []
    db = engine._final_main_db(region, section)
    depth = min(region.top.effective_depth_mm, region.bottom.effective_depth_mm)
    minimum = engine.code.minimum_transverse_area_per_spacing(section)
    combo = max(minimum, demand.transverse_required_av_over_s_mm2_per_mm) + 2*demand.at_over_s_mm2_per_mm
    for rank, diameter in enumerate(engine.project.ordered_stirrup_diameters()):
        if diameter < region.stirrup.diameter_mm:
            continue
        area = engine.project.catalog.area(diameter)
        maximum, reason, limits, high = engine.code.maximum_stirrup_spacing(
            section, demand, diameter, db, engine.project.smf_end_max_spacing_mm,
            effective_depth_mm=(section.depth_mm if section.is_smf else depth))
        for spacing in engine.project.allowed_spacings_for(section, region.name):
            if spacing > min(maximum, region.stirrup.spacing_mm) + 1e-9:
                continue
            legs = max(region.stirrup.legs, 2*math.ceil(combo*spacing/area/2-1e-9))
            if area/spacing + 1e-9 < demand.at_over_s_mm2_per_mm:
                continue
            hoop = replace(region.stirrup, diameter_mm=diameter, spacing_mm=spacing, legs=legs,
                provided_av_over_s_mm2_per_mm=legs*area/spacing,
                provided_at_over_s_mm2_per_mm=area/spacing, maximum_spacing_mm=maximum,
                governing_reason=reason, spacing_limits=limits, high_shear=high)
            choices.append((rank, spacing, hoop))
    return choices


def _uncached_fit_hoops(result, engine, *, preserve_current=False):
    review = engine._calculate_capacity_review(result.section, result.regions, result.seismic_context)
    demands = engine._apply_capacity_shear_demands(
        result.input_data.regions, result.input_data.regions, review, result.input_data)
    selected = []
    choices_by_region = []
    for region, demand in zip(result.regions, demands):
        choices = _fit_region_hoop_choices(result.section, demand, region, engine)
        if not choices:
            raise DetailingError("FINAL_ROWS_NO_FEASIBLE_HOOP")
        choices_by_region.append(choices)
        current = next(
            (
                item[2] for item in choices
                if (item[2].diameter_mm, item[2].spacing_mm, item[2].legs)
                == (region.stirrup.diameter_mm, region.stirrup.spacing_mm, region.stirrup.legs)
            ),
            None,
        )
        if preserve_current and current is not None:
            selected.append(current)
        else:
            selected.append(engine._preferred_stirrup(result.section, choices))
    if engine.uniform_stirrup_diameter_per_span and not result.section.is_secondary:
        for diameter in engine.project.ordered_stirrup_diameters():
            per_region = [
                tuple(item for item in choices if item[2].diameter_mm == diameter)
                for choices in choices_by_region
            ]
            if any(not choices for choices in per_region):
                continue
            uniform = []
            for region, choices in zip(result.regions, per_region):
                current = next(
                    (
                        item[2]
                        for item in choices
                        if (item[2].diameter_mm, item[2].spacing_mm, item[2].legs)
                        == (region.stirrup.diameter_mm, region.stirrup.spacing_mm, region.stirrup.legs)
                    ),
                    None,
                )
                uniform.append(
                    current
                    if preserve_current and current is not None
                    else engine._preferred_stirrup(result.section, choices)
                )
            common_legs = max(hoop.legs for hoop in uniform)
            selected = [engine._with_legs(hoop, common_legs) for hoop in uniform]
            return tuple(selected), demands
        raise DetailingError("FINAL_ROWS_NO_FEASIBLE_UNIFORM_DIAMETER_HOOP")
    if result.section.is_secondary:
        common = selected[0]
        for hoop in selected[1:]:
            common = engine._conservative_stirrup(common, hoop)
        selected = [common]*3
    else:
        selected = [engine._with_legs(h, max(item.legs for item in selected)) for h in selected]
    return tuple(selected), demands


def _support_compatible(candidate, required, *, exact):
    if required is None:
        return True
    if not candidate or not required:
        return False
    if candidate[0][1] != required[0][1]:
        return False
    if exact:
        # The common-support rule is physical-row based: keep the same row
        # count and the same diameter in each corresponding row, but do not
        # force the first-row bar count to be identical.  Each side still
        # independently passes FIRST_ROW_HOOP_SUPPORT during finalization.
        return len(candidate) == len(required) and all(
            left[1] == right[1] for left, right in zip(candidate, required)
        )
    return len(candidate) >= min(len(candidate), len(required)) and all(
        left[1] == right[1] for left, right in zip(candidate, required)
    )


def _legacy_span_candidates(data, engine, *, first_diameter=None, left_rows=None,
                            right_rows=None, support_exact=False, search_budget=None,
                            coordination_pool=False, fixed_face_rebar=None):
    if data.section.detailing_profile is BeamDetailingProfile.UNRESOLVED:
        raise DetailingError("DETAILING_PROFILE_REVIEW_REQUIRED")
    pending = [_initial_hoops(data, engine)]
    checked_cache = _checked_state_cache(engine)
    context_key = _engine_astuple(data, engine) if checked_cache is not None else None
    seen, reasons = set(), set()
    while pending:
        hoops = pending.pop(0)
        state = _hoop_signature(hoops)
        if state in seen:
            continue
        seen.add(state)
        # Total longitudinal demand is the only selectable/verified control.
        # The former NORMAL -> FACE_TORSION_MODE fallback intentionally stays
        # disabled: face/skin bars are independent detailing output and never
        # receive credit against top/bottom longitudinal demand.
        mode = ReinforcementMode.NORMAL
        limit = engine.project.max_main_bar_layers
        top_required = [r.as_top_total_mm2 for r in data.regions]
        bottom_required = max(r.as_bottom_total_mm2 for r in data.regions)
        lower = candidates(data.section, hoops, bottom_required, engine,
                           max_layers=limit, face="bottom",
                           preserve_support_alternatives=False)
        if not lower:
            continue
        diameters = (first_diameter,) if first_diameter is not None else engine.project.ordered_longitudinal_diameters_for(data.section)
        valid_mode = False
        accepted_supports = set()
        diameter_groups = [diameters]
        if data.section.is_secondary:
            preferred = tuple(d for d in diameters
                              if d <= engine.project.sb_top_preferred_max_diameter_mm)
            fallback = tuple(d for d in diameters if d > engine.project.sb_top_preferred_max_diameter_mm)
            diameter_groups = [group for group in (preferred, fallback) if group]
        for diameter_group in diameter_groups:
          group_found = False
          for diameter in diameter_group:
            upper = [candidates(data.section, (hoop,), required, engine,
                        first_diameter=diameter, max_layers=limit,
                        preserve_support_alternatives=True)
                     for hoop, required in zip(hoops, top_required)]
            for index, required_rows in ((0, left_rows), (2, right_rows)):
                if required_rows is not None:
                    upper[index] = tuple(item for item in upper[index]
                        if _support_compatible(signature(item), required_rows,
                                               exact=support_exact))
            def already_covered(combination):
                if not coordination_pool:
                    return False
                left, _middle, right, _bottom = combination
                return (
                    tuple(row[1] for row in signature(left)),
                    tuple(row[1] for row in signature(right)),
                ) in accepted_supports

            def expand_uncovered_supports(combination):
                # Endpoint-only paths still reach every uncovered class in
                # the original (sum(indices), indices) order. Middle/bottom
                # increments inside an accepted class cannot yield a result.
                return (0, 2) if already_covered(combination) else range(4)

            possible_supports = None
            if coordination_pool:
                possible_supports = {
                    (
                        tuple(row[1] for row in signature(left)),
                        tuple(row[1] for row in signature(right)),
                    )
                    for left in upper[0]
                    for right in upper[2]
                }

            for left, middle, right, bottom in _combinations(
                (*upper, lower),
                search_budget,
                skip_if=already_covered,
                stop_if=(
                    (lambda: possible_supports.issubset(accepted_supports))
                    if possible_supports is not None
                    else None
                ),
                expand_axes=(
                    expand_uncovered_supports
                    if coordination_pool and getattr(engine, "independent_skin_rebar", False)
                    else None
                ),
            ):
                support_key = (
                    tuple(row[1] for row in signature(left)),
                    tuple(row[1] for row in signature(right)),
                ) if coordination_pool else (signature(left), signature(right))
                if support_key in accepted_supports:
                    continue
                regions = tuple(RegionDetailing(d.name, d,
                    engine.code.minimum_longitudinal_area(data.section.width_mm,
                        min(top.effective_depth_mm, bottom.effective_depth_mm), data.section.fc_mpa, data.section.fy_mpa),
                    d.as_flex_top_mm2, d.as_flex_bottom_mm2, hoop, top, bottom)
                    for d, hoop, top in zip(data.regions, hoops, (left, middle, right)))
                checked_key = None
                result = None
                if checked_cache is not None:
                    face_key = _engine_astuple(fixed_face_rebar, engine) if fixed_face_rebar is not None else None
                    checked_key = ("legacy", context_key,
                        tuple(_engine_astuple(hoop, engine) for hoop in hoops),
                        tuple(_engine_astuple(layout, engine) for layout in (left, middle, right, bottom)), face_key)
                    result = checked_cache.get(checked_key)
                try:
                    if result is None:
                        result = engine._build_row_result(
                            data, mode, regions,
                            **({"face_rebar_override": fixed_face_rebar}
                               if getattr(engine, "independent_skin_rebar", False) else {}),
                        )
                        result = _refresh_result_physical_layout(result, engine)
                        following, demands = _fit_hoops(result, engine)
                        if _hoop_signature(following) != state:
                            if _hoop_signature(following) not in seen and all(
                                    _hoop_signature(h) != _hoop_signature(following) for h in pending):
                                pending.append(following)
                            continue
                        result = replace(result, regions=tuple(replace(r, demand=d)
                            for r, d in zip(result.regions, demands)))
                        result = finalize_rows(result, engine)
                        _remember_checked_state(checked_cache, checked_key, result)
                except DetailingError as error:
                    reasons.add(str(error))
                    continue
                valid_mode = True
                group_found = True
                accepted_supports.add(support_key)
                yield replace(result, coordination_status="STABLE",
                    coordination_audit=({"strategy": "FINITE_PHYSICAL_ROWS", "hoop_states": len(seen),
                        "first_row_rule": "COMMON_DIAMETER_INDEPENDENT_COUNT_WITH_LOCAL_HOOP_SUPPORT",
                        "rejected_reasons": sorted(reasons)},))
          if group_found:
            break
        if valid_mode and not (coordination_pool and _revised_coordination(engine)):
            break


def _style_hoop(section, demand, signature, engine):
    diameter = int(signature["diameter_mm"])
    spacing = int(signature["spacing_mm"])
    legs = int(signature["legs"])
    if diameter not in engine.project.ordered_stirrup_diameters():
        return None
    if spacing not in engine.project.allowed_spacings_for(section, demand.name):
        return None
    if diameter < engine.project.minimum_stirrup_diameter_for(section) or legs < 2 or legs % 2:
        return None
    area = engine.project.catalog.area(diameter)
    return StirrupDesign(
        diameter_mm=diameter,
        spacing_mm=spacing,
        legs=legs,
        provided_av_over_s_mm2_per_mm=legs * area / spacing,
        provided_at_over_s_mm2_per_mm=area / spacing,
        maximum_spacing_mm=float("inf"),
        governing_reason="APPROVED_STYLE_PATTERN_TRIAL",
        final_main_bar_diameter_mm=None,
    )


def _economic_hoop_spacing_limits(section, demand, engine, diameter, *, max_main,
                                  effective_depth):
    scope = getattr(engine, "_r7_span_caches", None)
    if not getattr(engine, "_r7_span_caches_enabled", False) or scope is None:
        return engine.code.maximum_stirrup_spacing(
            section, demand, diameter, max_main, engine.project.smf_end_max_spacing_mm,
            effective_depth_mm=effective_depth,
        )
    cache = scope["economic_spacing"]
    stats = engine._r7_cache_statistics
    stats["economic_spacing_requests"] += 1
    # Spacing itself is not an input to the code's maximum-spacing rule.
    key = (section, demand, diameter, max_main, effective_depth,
           engine.project.smf_end_max_spacing_mm)
    cached = cache.get(key)
    if cached is not None:
        stats["economic_spacing_hits"] += 1
        succeeded, value = cached
        if succeeded:
            return value
        error_type, error_args = value
        raise error_type(*error_args)
    retain = len(cache) < R7_SPAN_CACHE_MAX_ENTRIES
    if not retain:
        stats["economic_spacing_limit_bypasses"] += 1
    stats["economic_spacing_computations"] += 1
    try:
        limits = engine.code.maximum_stirrup_spacing(
            section, demand, diameter, max_main, engine.project.smf_end_max_spacing_mm,
            effective_depth_mm=effective_depth,
        )
    except DetailingError as error:
        stats["economic_spacing_failures"] += 1
        if retain:
            cache[key] = (False, (type(error), error.args))
            stats["economic_spacing_entries_stored"] += 1
        raise
    if retain:
        cache[key] = (True, limits)
        stats["economic_spacing_entries_stored"] += 1
    return limits


def _required_hoop_legs(section, demand, engine, diameter, spacing, *, max_main,
                        effective_depth):
    minimum_av = engine.code.minimum_transverse_area_per_spacing(section)
    required_av = max(demand.transverse_required_av_over_s_mm2_per_mm, minimum_av)
    combo = required_av + 2.0 * demand.at_over_s_mm2_per_mm
    area = engine.project.catalog.area(diameter)
    if area / spacing + 1e-9 < demand.at_over_s_mm2_per_mm:
        return None
    maximum, _reason, _limits, _high = _economic_hoop_spacing_limits(
        section, demand, engine, diameter, max_main=max_main, effective_depth=effective_depth,
    )
    if spacing > maximum + 1e-9:
        return None
    required_legs = max(
        2,
        engine.code.minimum_legs(section, demand.name),
        2 * math.ceil(combo * spacing / area / 2.0 - 1e-9),
    )
    return required_legs + required_legs % 2


def _economic_hoop_trial(section, demand, engine, diameter, spacing, legs, *,
                         max_main, effective_depth):
    if diameter < engine.project.minimum_stirrup_diameter_for(section):
        return None
    required_legs = _required_hoop_legs(
        section, demand, engine, diameter, spacing,
        max_main=max_main, effective_depth=effective_depth,
    )
    if required_legs is None or legs < required_legs:
        return None
    minimum_av = engine.code.minimum_transverse_area_per_spacing(section)
    required_av = max(demand.transverse_required_av_over_s_mm2_per_mm, minimum_av)
    area = engine.project.catalog.area(diameter)
    maximum, reason, limits, high = _economic_hoop_spacing_limits(
        section, demand, engine, diameter, max_main=max_main, effective_depth=effective_depth,
    )
    return StirrupDesign(
        diameter_mm=diameter,
        spacing_mm=spacing,
        legs=legs,
        provided_av_over_s_mm2_per_mm=legs * area / spacing,
        provided_at_over_s_mm2_per_mm=area / spacing,
        maximum_spacing_mm=maximum,
        governing_reason=reason,
        required_av_over_s_mm2_per_mm=demand.transverse_required_av_over_s_mm2_per_mm,
        minimum_av_over_s_mm2_per_mm=minimum_av,
        required_combo_over_s_mm2_per_mm=required_av + 2.0 * demand.at_over_s_mm2_per_mm,
        final_main_bar_diameter_mm=max_main,
        high_shear=high,
        spacing_limits=limits,
    )


def _cached_economic_hoop_trial(section, demand, engine, diameter, spacing, legs, *,
                                max_main, effective_depth, trial_cache,
                                search_stats):
    cache_key = (
        section,
        demand,
        id(engine),
        float(diameter),
        float(spacing),
        int(legs),
        float(max_main),
        None if effective_depth is None else float(effective_depth),
    )
    if search_stats is not None:
        search_stats["economic_hoop_trial_calls"] += 1
    if trial_cache is not None and cache_key in trial_cache:
        if search_stats is not None:
            search_stats["economic_hoop_trial_cache_hits"] += 1
        return trial_cache[cache_key]
    if search_stats is not None:
        search_stats["economic_hoop_trial_unique_states"] += 1
    hoop = _economic_hoop_trial(
        section,
        demand,
        engine,
        diameter,
        spacing,
        legs,
        max_main=max_main,
        effective_depth=effective_depth,
    )
    trial_cache[cache_key] = hoop
    return hoop


def _indexed_supply_hoop_trial(section, demand, engine, diameter, spacing, legs, *,
                               max_main, effective_depth, supply_table):
    """Filter independent supply by actual demand and the final-depth rule."""
    if diameter < engine.project.minimum_stirrup_diameter_for(section):
        return None
    supply = supply_table.get(diameter, spacing, legs)
    minimum_av = engine.code.minimum_transverse_area_per_spacing(section)
    required_av = max(demand.transverse_required_av_over_s_mm2_per_mm, minimum_av)
    combo = required_av + 2.0 * demand.at_over_s_mm2_per_mm
    if supply.provided_at_over_s + 1e-9 < demand.at_over_s_mm2_per_mm:
        return None
    maximum, reason, limits, high = _economic_hoop_spacing_limits(
        section, demand, engine, diameter, max_main=max_main, effective_depth=effective_depth)
    if spacing > maximum + 1e-9:
        return None
    required_legs = max(2, engine.code.minimum_legs(section, demand.name),
        2 * math.ceil(combo * spacing / supply.bar_area_mm2 / 2.0 - 1e-9))
    required_legs += required_legs % 2
    if legs < required_legs:
        return None
    return StirrupDesign(
        diameter_mm=diameter, spacing_mm=spacing, legs=legs,
        provided_av_over_s_mm2_per_mm=supply.provided_av_over_s,
        provided_at_over_s_mm2_per_mm=supply.provided_at_over_s,
        maximum_spacing_mm=maximum, governing_reason=reason,
        required_av_over_s_mm2_per_mm=demand.transverse_required_av_over_s_mm2_per_mm,
        minimum_av_over_s_mm2_per_mm=minimum_av,
        required_combo_over_s_mm2_per_mm=combo,
        final_main_bar_diameter_mm=max_main, high_shear=high, spacing_limits=limits)


def _economic_hoop_cost(section, region_name, hoop, *, region=None, legs_override=None):
    length = float(section.length_mm)
    end_length = min(2.0 * section.depth_mm, length / 2.0)
    zone_length = {
        "left": end_length,
        "middle": max(0.0, length - 2.0 * end_length),
        "right": end_length,
    }[region_name]
    if zone_length <= 0:
        return 0.0
    diameter = float(hoop.diameter_mm)
    legs = hoop.legs if legs_override is None else legs_override
    if section.clear_cover_to_stirrup_outer_mm is not None:
        center_cover = section.clear_cover_to_stirrup_outer_mm + diameter / 2.0
        b_center = section.width_mm - 2.0 * center_cover
        h_center = section.depth_mm - 2.0 * center_cover
    elif region is not None:
        surfaces = []
        for face in (region.top, region.bottom):
            rows = face.rows
            centers = face.row_centers_mm
            x_rows = face.row_x_mm
            if not rows or not centers or not x_rows or not x_rows[0]:
                return engine_cost_fallback(hoop, zone_length, legs_override=legs_override)
            outer = rows[0]
            xs = tuple(float(value) for value in x_rows[0])
            outer_diameter = float(outer.diameter_mm)
            surfaces.append((
                min(xs) - outer_diameter / 2.0,
                section.width_mm - max(xs) - outer_diameter / 2.0,
                float(centers[0]) - outer_diameter / 2.0,
            ))
        b_center = section.width_mm - min(item[0] for item in surfaces) - min(item[1] for item in surfaces) - diameter
        h_center = section.depth_mm - surfaces[0][2] - surfaces[1][2] - diameter
    else:
        return engine_cost_fallback(hoop, zone_length, legs_override=legs_override)
    if b_center <= 0 or h_center <= 0:
        return engine_cost_fallback(hoop, zone_length, legs_override=legs_override)
    area = math.pi * diameter**2 / 4.0
    return zone_length / hoop.spacing_mm * area * (2.0 * b_center + legs * h_center)


def engine_cost_fallback(hoop, zone_length, *, legs_override=None):
    legs = hoop.legs if legs_override is None else legs_override
    return zone_length * hoop.diameter_mm**2 * max(1, legs) / hoop.spacing_mm


def _indexed_economic_hoop_key(data, engine, result):
    """Exact inputs read by the ETABS-only economic-hoop calculation.

    Unused interior bar rows do not affect demand, trial hoops, first-row
    support, effective depth, or the actual-coordinate hoop cost. Keep the
    outer coordinates when cover has to be inferred from them.
    """
    if result is None:
        return ("indexed_etabs_economic", data, None)
    section = data.section
    region_keys = []
    for region in result.regions:
        faces = (region.top, region.bottom)
        geometry = None
        if section.clear_cover_to_stirrup_outer_mm is None:
            if any(not face.rows or not face.row_centers_mm or not face.row_x_mm
                   or not face.row_x_mm[0] for face in faces):
                geometry = (False,)
            else:
                geometry = tuple((face.rows[0].diameter_mm, float(face.row_centers_mm[0]),
                                  tuple(float(x) for x in face.row_x_mm[0])) for face in faces)
        region_keys.append((engine._final_main_db(region, section),
            section.depth_mm if section.is_smf else min(
                region.top.effective_depth_mm, region.bottom.effective_depth_mm),
            min(region.top.rows[0].count, region.bottom.rows[0].count), geometry))
    return ("indexed_etabs_economic", data, result.section, tuple(region_keys))


def _economic_hoop_states(data, engine, result=None, *, trial_cache=None,
                          search_stats=None):
    scope = getattr(engine, "_r7_span_caches", None)
    if not getattr(engine, "_r7_span_caches_enabled", False) or scope is None:
        return _uncached_economic_hoop_states(
            data, engine, result, trial_cache=trial_cache, search_stats=search_stats,
        )
    cache = scope["economic_hoops"]
    stats = engine._r7_cache_statistics
    stats["economic_hoops_requests"] += 1
    # Full post-finalization regions preserve the physical coordinates used
    # by hoop volume, effective depths and first-row leg-support counts.
    if _indexed_coordination(engine) and engine.etabs_design_results_only:
        key = _indexed_economic_hoop_key(data, engine, result)
    else:
        key = (data, None if result is None else (
            result.section, result.regions, result.seismic_context,
        ))
    cached = cache.get(key)
    if cached is not None:
        stats["economic_hoops_hits"] += 1
        succeeded, value = cached
        if succeeded:
            return value
        error_type, error_args = value
        raise error_type(*error_args)
    retain = len(cache) < R7_SPAN_CACHE_MAX_ENTRIES
    if not retain:
        stats["economic_hoops_limit_bypasses"] += 1
    stats["economic_hoops_computations"] += 1
    try:
        states = _uncached_economic_hoop_states(
            data, engine, result, trial_cache=trial_cache, search_stats=search_stats,
        )
    except DetailingError as error:
        stats["economic_hoops_failures"] += 1
        if retain:
            cache[key] = (False, (type(error), error.args))
            stats["economic_hoops_entries_stored"] += 1
        raise
    if retain:
        cache[key] = (True, states)
        stats["economic_hoops_entries_stored"] += 1
    return states


def _ordinary_sb_supply_records(section, demand, common_spacings, engine, scope):
    """Build demand-side supply once, independently of actual d and main db."""
    own_spacings = tuple(engine.project.allowed_spacings_for(section, demand.name))
    spacings = tuple(dict.fromkeys((*own_spacings, *common_spacings)))
    key = (section, demand, spacings)
    cache = scope.setdefault("indexed_sb_demand_supply", {})
    cached = cache.get(key)
    if cached is not None:
        return cached
    minimum_av = engine.code.minimum_transverse_area_per_spacing(section)
    required_av = max(demand.transverse_required_av_over_s_mm2_per_mm, minimum_av)
    combo = required_av + 2.0 * demand.at_over_s_mm2_per_mm
    # Populate an entry only when the original ordered traversal reaches it.
    # Failed calculations are never retained as negative results.
    value = (minimum_av, combo, {}, {})
    if len(cache) < R7_SPAN_CACHE_MAX_ENTRIES:
        cache[key] = value
    return value


def _ordinary_sb_economic_hoop_states(data, engine, result, scope):
    """Select ordinary SB hoops in original order without building losers."""
    from .hoop_supply import HoopSupply
    section = data.section
    if result is None:
        demands = tuple(data.regions)
        contexts = (None, None, None)
        main = (max(engine.project.ordered_longitudinal_diameters_for(section)),) * 3
        depths = (None,) * len(demands)
    else:
        review = engine._calculate_capacity_review(result.section, result.regions, result.seismic_context)
        demands = engine._apply_capacity_shear_demands(data.regions, data.regions, review, data)
        contexts = tuple(result.regions)
        main = tuple(engine._final_main_db(region, section) for region in contexts)
        depths = tuple(min(region.top.effective_depth_mm, region.bottom.effective_depth_mm)
                       for region in contexts)
    diameters = engine.project.ordered_stirrup_diameters()
    common_spacings = tuple(engine.project.allowed_spacings_for(section, demands[0].name))
    prepared = [None] * len(demands)
    limits_by_region = [{} for _ in demands]
    minimum_diameter = None
    supply_cache = scope.setdefault("indexed_sb_cost_supplies", {})

    def demand_supply(index):
        if prepared[index] is None:
            prepared[index] = _ordinary_sb_supply_records(section, demands[index], common_spacings, engine, scope)
        return prepared[index]

    def min_diameter():
        nonlocal minimum_diameter
        if minimum_diameter is None:
            minimum_diameter = engine.project.minimum_stirrup_diameter_for(section)
        return minimum_diameter

    def admissible(index, diameter, spacing, *, apply_minimum_first):
        demand = demands[index]
        if apply_minimum_first and diameter < min_diameter():
            return None
        minimum_av, combo, records, leg_cache = demand_supply(index)
        key = (diameter, spacing)
        record = records.get(key)
        if record is None:
            area = engine.project.catalog.area(diameter)
            record = (area, area / spacing)
            records[key] = record
        area, at = record
        if at + 1e-9 < demand.at_over_s_mm2_per_mm:
            return None
        limits = limits_by_region[index].get(diameter)
        if limits is None:
            limits = _economic_hoop_spacing_limits(section, demand, engine, diameter,
                max_main=main[index], effective_depth=depths[index])
            limits_by_region[index][diameter] = limits
        maximum, _reason, _details, _high = limits
        if spacing > maximum + 1e-9:
            return None
        key = (diameter, spacing)
        required = leg_cache.get(key)
        if required is None:
            required = max(2, engine.code.minimum_legs(section, demand.name),
                           2 * math.ceil(combo * spacing / area / 2.0 - 1e-9))
            required += required % 2
            leg_cache[key] = required
        if diameter < min_diameter():
            return None
        return required, limits

    fallback_legs = []
    for index, demand in enumerate(demands):
        minimum = None
        preferred_region = None
        for diameter in diameters:
            for spacing in engine.project.allowed_spacings_for(section, demand.name):
                value = admissible(index, diameter, spacing, apply_minimum_first=False)
                if value is None:
                    continue
                if preferred_region is None:
                    preferred_region = max(2, engine.code.minimum_legs(section, demand.name),
                                           engine.project.preferred_stirrup_legs(section))
                    preferred_region += preferred_region % 2
                legs = max(preferred_region, value[0])
                if minimum is None or legs < minimum:
                    minimum = legs
        if minimum is None:
            return ()
        fallback_legs.append(minimum)

    preferred_legs = max(max(2, engine.code.minimum_legs(section, demand.name),
                             engine.project.preferred_stirrup_legs(section)) for demand in demands)
    preferred_legs += preferred_legs % 2
    targets = {preferred_legs}
    targets.update(value for value in (2, 4, 6) if value >= preferred_legs)
    fallback = max(fallback_legs)
    fallback += fallback % 2
    if fallback > 6:
        targets.add(fallback)

    states = []
    for target in sorted(targets):
        common_candidates = []
        for diameter in diameters:
            for spacing in common_spacings:
                choices = []
                for index in range(len(demands)):
                    value = admissible(index, diameter, spacing, apply_minimum_first=True)
                    if value is None or target < value[0]:
                        break
                    choices.append(value)
                if len(choices) != len(demands):
                    continue
                if result is not None and any(
                        region.top.rows[0].count < target or region.bottom.rows[0].count < target
                        for region in contexts):
                    continue
                supply_key = (diameter, spacing, target)
                supply = supply_cache.get(supply_key)
                if supply is None:
                    area, at = demand_supply(0)[2][(diameter, spacing)]
                    supply = HoopSupply(diameter, spacing, target, area, at, target * area / spacing)
                    if len(supply_cache) < R7_SPAN_CACHE_MAX_ENTRIES:
                        supply_cache[supply_key] = supply
                cost = sum(_economic_hoop_cost(section, demand.name, supply, region=context)
                           for demand, context in zip(demands, contexts))
                common_candidates.append((cost, diameter, -spacing, tuple(choices)))
        if common_candidates:
            best = min(common_candidates, key=lambda item: item[:3])
            states.append((best[0], target, best[1], -best[2], best[3]))
    if not states:
        return ()
    _cost, legs, diameter, spacing, chosen = min(states,
        key=lambda item: (item[0], item[1], tuple((item[2], -item[3]) for _ in demands)))
    supply = supply_cache[(diameter, spacing, legs)] if (diameter, spacing, legs) in supply_cache else None
    if supply is None:
        area, at = demand_supply(0)[2][(diameter, spacing)]
        supply = HoopSupply(diameter, spacing, legs, area, at, legs * area / spacing)
    hoops = []
    for index, (demand, (_required, limits)) in enumerate(zip(demands, chosen)):
        maximum, reason, details, high = limits
        minimum_av, combo, _records, _leg_cache = demand_supply(index)
        hoops.append(StirrupDesign(diameter_mm=diameter, spacing_mm=spacing, legs=legs,
            provided_av_over_s_mm2_per_mm=supply.provided_av_over_s,
            provided_at_over_s_mm2_per_mm=supply.provided_at_over_s,
            maximum_spacing_mm=maximum, governing_reason=reason,
            required_av_over_s_mm2_per_mm=demand.transverse_required_av_over_s_mm2_per_mm,
            minimum_av_over_s_mm2_per_mm=minimum_av,
            required_combo_over_s_mm2_per_mm=combo, final_main_bar_diameter_mm=main[index],
            high_shear=high, spacing_limits=details))
    return (tuple(hoops),)


def _uncached_economic_hoop_states(data, engine, result=None, *, trial_cache=None,
                                   search_stats=None):
    """Generate bounded, cost-dominant hoop alternatives on the active catalog."""
    section = data.section
    scope = getattr(engine, "_r7_span_caches", None)
    if (_indexed_coordination(engine) and engine.etabs_design_results_only
            and section.is_secondary
            and section.detailing_profile in (BeamDetailingProfile.SB_ORDINARY_NO_CH18, BeamDetailingProfile.NON_SWAY)
            and getattr(engine, "_r7_span_caches_enabled", False) and scope is not None):
        return _ordinary_sb_economic_hoop_states(data, engine, result, scope)
    supply_table = None
    supply_trials = None
    supply_missing = object()
    if (_indexed_coordination(engine) and engine.etabs_design_results_only
            and getattr(engine, "_r7_span_caches_enabled", False) and scope is not None):
        from .hoop_supply import HoopSupplyTable
        tables = scope.setdefault("indexed_hoop_supply_tables", {})
        table_key = (section, tuple(demand.name for demand in data.regions))
        supply_table = tables.get(table_key)
        if supply_table is None:
            spacings = tuple(dict.fromkeys(spacing for demand in data.regions
                for spacing in engine.project.allowed_spacings_for(section, demand.name)))
            supply_table = HoopSupplyTable(engine.project.ordered_stirrup_diameters(),
                                         spacings, engine.project.catalog)
            tables[table_key] = supply_table
        supply_trials = scope.setdefault("indexed_hoop_supply_trials", {})

    def trial(demand, diameter, spacing, legs, *, max_main, effective_depth):
        if supply_table is not None:
            if search_stats is not None and trial_cache is not None:
                search_stats["economic_hoop_trial_calls"] += 1
            key = (section, demand, diameter, spacing, legs, max_main, effective_depth)
            cached = supply_trials.get(key, supply_missing)
            if cached is not supply_missing:
                if search_stats is not None and trial_cache is not None:
                    search_stats["economic_hoop_trial_cache_hits"] += 1
                return cached
            if search_stats is not None and trial_cache is not None:
                search_stats["economic_hoop_trial_unique_states"] += 1
            hoop = _indexed_supply_hoop_trial(section, demand, engine, diameter, spacing, legs,
                max_main=max_main, effective_depth=effective_depth, supply_table=supply_table)
            if len(supply_trials) < R7_SPAN_CACHE_MAX_ENTRIES:
                supply_trials[key] = hoop
            return hoop
        if trial_cache is None:
            return _economic_hoop_trial(
                section,
                demand,
                engine,
                diameter,
                spacing,
                legs,
                max_main=max_main,
                effective_depth=effective_depth,
            )
        return _cached_economic_hoop_trial(
            section,
            demand,
            engine,
            diameter,
            spacing,
            legs,
            max_main=max_main,
            effective_depth=effective_depth,
            trial_cache=trial_cache,
            search_stats=search_stats,
        )

    if result is None:
        demands = tuple(data.regions)
        contexts = (None, None, None)
        max_main_by_region = (max(engine.project.ordered_longitudinal_diameters_for(section)),) * 3
        effective_depths = tuple(section.depth_mm if section.is_smf else None for _ in demands)
    else:
        review = engine._calculate_capacity_review(result.section, result.regions, result.seismic_context)
        demands = engine._apply_capacity_shear_demands(
            data.regions, data.regions, review, data
        )
        contexts = tuple(result.regions)
        max_main_by_region = tuple(
            engine._final_main_db(region, section) for region in result.regions
        )
        effective_depths = tuple(
            section.depth_mm
            if section.is_smf
            else min(region.top.effective_depth_mm, region.bottom.effective_depth_mm)
            for region in result.regions
        )

    region_options = []
    fallback_legs = []
    for index, (demand, context, max_main, effective_depth) in enumerate(
        zip(demands, contexts, max_main_by_region, effective_depths)
    ):
        options = _economic_region_options(
            section, demand, engine, max_main=max_main,
            effective_depth=effective_depth, trial=trial,
        )
        if not options:
            return ()
        region_options.append(tuple(options))
        fallback_legs.append(min(hoop.legs for hoop in options))

    preferred_legs = max(
        max(2, engine.code.minimum_legs(section, demand.name), engine.project.preferred_stirrup_legs(section))
        for demand in demands
    )
    preferred_legs += preferred_legs % 2
    leg_targets = {preferred_legs}
    leg_targets.update(value for value in (2, 4, 6) if value >= preferred_legs)
    fallback = max(fallback_legs)
    fallback += fallback % 2
    if fallback > 6:
        leg_targets.add(fallback)

    states = []
    if section.is_secondary:
        for target_legs in sorted(leg_targets):
            common_candidates = []
            for diameter in engine.project.ordered_stirrup_diameters():
                for spacing in engine.project.allowed_spacings_for(section, demands[0].name):
                    choices = []
                    for index, (demand, context, max_main, effective_depth) in enumerate(
                        zip(demands, contexts, max_main_by_region, effective_depths)
                    ):
                        hoop = trial(
                            demand,
                            diameter,
                            spacing,
                            target_legs,
                            max_main=max_main,
                            effective_depth=effective_depth,
                        )
                        if hoop is None:
                            break
                        choices.append(hoop)
                    if len(choices) != len(demands):
                        continue
                    if result is not None and any(
                        region.top.rows[0].count < target_legs
                        or region.bottom.rows[0].count < target_legs
                        for region in contexts
                    ):
                        continue
                    cost = sum(
                        _economic_hoop_cost(section, demand.name, hoop, region=context)
                        for demand, hoop, context in zip(demands, choices, contexts)
                    )
                    common_candidates.append((cost, diameter, -spacing, tuple(choices)))
            if common_candidates:
                best = min(common_candidates, key=lambda item: item[:3])
                states.append((best[0], target_legs, best[3]))
    elif engine.uniform_stirrup_diameter_per_span:
        for target_legs in sorted(leg_targets):
            for diameter in engine.project.ordered_stirrup_diameters():
                selected = []
                for demand, context, options in zip(demands, contexts, region_options):
                    if context is not None and (
                        context.top.rows[0].count < target_legs
                        or context.bottom.rows[0].count < target_legs
                    ):
                        selected = []
                        break
                    eligible = [
                        hoop for hoop in options
                        if hoop.diameter_mm == diameter and hoop.legs <= target_legs
                    ]
                    if not eligible:
                        selected = []
                        break
                    hoop = min(
                        eligible,
                        key=lambda item: (
                            (_economic_hoop_cost(section, demand.name, item,
                                 region=context, legs_override=target_legs)
                             if _indexed_coordination(engine) and engine.etabs_design_results_only
                             else _economic_hoop_cost(section, demand.name,
                                 replace(item, legs=target_legs), region=context)),
                            -item.spacing_mm,
                        ),
                    )
                    selected.append(replace(
                        hoop,
                        legs=target_legs,
                        provided_av_over_s_mm2_per_mm=(
                            target_legs * engine.project.catalog.area(diameter) / hoop.spacing_mm
                        ),
                    ))
                if len(selected) == len(demands):
                    cost = sum(
                        _economic_hoop_cost(section, demand.name, hoop, region=context)
                        for demand, hoop, context in zip(demands, selected, contexts)
                    )
                    states.append((cost, target_legs, tuple(selected)))
    else:
        for target_legs in sorted(leg_targets):
            selected = []
            for index, (demand, context, options) in enumerate(zip(demands, contexts, region_options)):
                if context is not None and (
                    context.top.rows[0].count < target_legs
                    or context.bottom.rows[0].count < target_legs
                ):
                    selected = []
                    break
                eligible = [hoop for hoop in options if hoop.legs <= target_legs]
                # Each physical region retains one minimum transverse-volume
                # option for this common leg state. All final checks are rerun.
                if not eligible:
                    selected = []
                    break
                hoop = min(
                    eligible,
                    key=lambda item: (
                        _economic_hoop_cost(section, demand.name, replace(item, legs=target_legs), region=context),
                        item.diameter_mm,
                        -item.spacing_mm,
                    ),
                )
                selected.append(replace(
                    hoop,
                    legs=target_legs,
                    provided_av_over_s_mm2_per_mm=(
                        target_legs * engine.project.catalog.area(hoop.diameter_mm) / hoop.spacing_mm
                    ),
                ))
            if len(selected) == len(demands):
                cost = sum(
                    _economic_hoop_cost(section, demand.name, hoop, region=context)
                    for demand, hoop, context in zip(demands, selected, contexts)
                )
                states.append((cost, target_legs, tuple(selected)))

    if not states:
        return ()
    _cost, _legs, state = min(
        states,
        key=lambda item: (
            item[0],
            item[1],
            tuple((hoop.diameter_mm, -hoop.spacing_mm) for hoop in item[2]),
        ),
    )
    return (state,)


def _economic_region_options(section, demand, engine, *, max_main, effective_depth,
                              trial):
    scope = getattr(engine, "_r7_span_caches", None)
    if not getattr(engine, "_r7_span_caches_enabled", False) or scope is None:
        return _uncached_economic_region_options(
            section, demand, engine, max_main=max_main,
            effective_depth=effective_depth, trial=trial,
        )
    cache = scope["economic_region_options"]
    stats = engine._r7_cache_statistics
    stats["economic_region_options_requests"] += 1
    # These ordered feasible catalogue entries do not read physical rows.
    # First-row support and actual-coordinate costs remain downstream.
    key = (section, demand, max_main, effective_depth)
    cached = cache.get(key)
    if cached is not None:
        stats["economic_region_options_hits"] += 1
        succeeded, value = cached
        if succeeded:
            return value
        error_type, error_args = value
        raise error_type(*error_args)
    retain = len(cache) < R7_SPAN_CACHE_MAX_ENTRIES
    if not retain:
        stats["economic_region_options_limit_bypasses"] += 1
    stats["economic_region_options_computations"] += 1
    try:
        options = _uncached_economic_region_options(
            section, demand, engine, max_main=max_main,
            effective_depth=effective_depth, trial=trial,
        )
    except DetailingError as error:
        stats["economic_region_options_failures"] += 1
        if retain:
            cache[key] = (False, (type(error), error.args))
            stats["economic_region_options_entries_stored"] += 1
        raise
    if retain:
        cache[key] = (True, options)
        stats["economic_region_options_entries_stored"] += 1
    return options


def _uncached_economic_region_options(section, demand, engine, *, max_main,
                                       effective_depth, trial):
    options = []
    for diameter in engine.project.ordered_stirrup_diameters():
        for spacing in engine.project.allowed_spacings_for(section, demand.name):
            required = _required_hoop_legs(
                section, demand, engine, diameter, spacing,
                max_main=max_main, effective_depth=effective_depth,
            )
            if required is None:
                continue
            preferred = max(
                2,
                engine.code.minimum_legs(section, demand.name),
                engine.project.preferred_stirrup_legs(section),
            )
            preferred += preferred % 2
            legs = max(preferred, required)
            hoop = trial(
                demand, diameter, spacing, legs,
                max_main=max_main, effective_depth=effective_depth,
            )
            if hoop is not None:
                options.append(hoop)
    return tuple(options)


def _style_hoop_states(data, engine, applicable, *, trial_cache=None,
                       search_stats=None):
    """Keep approved hoop patterns in the search before legacy hoop pruning."""
    try:
        base = _initial_hoops(data, engine)
    except DetailingError:
        base = None
    section = data.section
    near_rules = applicable.get("stirrup_near_support", ())
    continuous_rules = applicable.get("stirrup_continuous", ())
    states = []

    if base is not None:
        states.append(base)
    states.extend(_economic_hoop_states(
        data,
        engine,
        trial_cache=trial_cache,
        search_stats=search_stats,
    ))
    near_signatures = list(dict.fromkeys(
        tuple(sorted(rule["pattern_signature"].items())) for rule in near_rules
    )) if near_rules else []
    continuous_signatures = list(dict.fromkeys(
        tuple(sorted(rule["pattern_signature"].items())) for rule in continuous_rules
    )) if continuous_rules else []
    near_by_key = {
        tuple(sorted(rule["pattern_signature"].items())): rule["pattern_signature"]
        for rule in near_rules
    }
    continuous_by_key = {
        tuple(sorted(rule["pattern_signature"].items())): rule["pattern_signature"]
        for rule in continuous_rules
    }

    if section.is_secondary:
        signatures = [*near_by_key.values(), *continuous_by_key.values()]
        for signature in signatures:
            hoop = _style_hoop(section, data.regions[0], signature, engine)
            if hoop is not None:
                states.append((hoop, hoop, hoop))
    elif engine.uniform_stirrup_diameter_per_span:
        near_options = [None, *near_by_key.values()]
        continuous_options = [None, *continuous_by_key.values()]
        for near_signature in near_options:
            for middle_signature in continuous_options:
                if near_signature is None and middle_signature is None:
                    continue
                if near_signature is None:
                    middle = _style_hoop(
                        section, data.regions[1], middle_signature, engine
                    )
                    if middle is None:
                        continue
                    left = _style_hoop(section, data.regions[0], middle_signature, engine)
                    right = _style_hoop(section, data.regions[2], middle_signature, engine)
                else:
                    near = _style_hoop(section, data.regions[0], near_signature, engine)
                    if near is None:
                        continue
                    left = right = near
                    middle = (
                        near
                        if middle_signature is None
                        else _style_hoop(section, data.regions[1], middle_signature, engine)
                    )
                if (
                    left is None
                    or middle is None
                    or right is None
                    or len({left.diameter_mm, middle.diameter_mm, right.diameter_mm}) != 1
                ):
                    continue
                common_legs = max(left.legs, middle.legs, right.legs)
                states.append(tuple(
                    engine._with_legs(hoop, common_legs)
                    for hoop in (left, middle, right)
                ))
    else:
        near_options = [near_by_key[key] for key in near_signatures] or [None]
        continuous_options = [continuous_by_key[key] for key in continuous_signatures] or [None]
        for near_signature in near_options:
            for middle_signature in continuous_options:
                if near_signature is None and middle_signature is None:
                    continue
                if near_signature is None:
                    if middle_signature is not None:
                        left = right = _style_hoop(section, data.regions[0], middle_signature, engine)
                    elif base is not None:
                        left, right = base[0], base[2]
                    else:
                        left = right = None
                else:
                    left = _style_hoop(section, data.regions[0], near_signature, engine)
                    right = _style_hoop(section, data.regions[2], near_signature, engine)
                middle = (
                    _style_hoop(section, data.regions[1], middle_signature, engine)
                    if middle_signature is not None
                    else (base[1] if base is not None else None)
                )
                if base is None and (left is None or middle is None or right is None):
                    continue
                if left is not None and middle is not None and right is not None:
                    common_legs = max(left.legs, middle.legs, right.legs)
                    states.append(tuple(
                        engine._with_legs(hoop, common_legs)
                        for hoop in (left, middle, right)
                    ))

    unique = {}
    for state in states:
        if engine.uniform_stirrup_diameter_per_span and len(
            {hoop.diameter_mm for hoop in state}
        ) != 1:
            continue
        key = _hoop_signature(state)
        unique.setdefault(key, state)
    return tuple(unique.values())


def _fixed_parent_bottom(data, engine, hoops, rows, required):
    """Resolve an externally selected physical layout in this actual section."""
    if not rows or len(rows) > engine.project.max_main_bar_layers:
        return ()
    if any(role != "longitudinal" or diameter not in engine.project.ordered_longitudinal_diameters_for(data.section)
           for count, diameter, role in rows):
        return ()
    if rows[0][0] < max(hoop.legs for hoop in hoops):
        return ()
    if (getattr(engine, "avoid_two_row_4_3", False) and len(rows) == 2
            and sorted(row[0] for row in rows) == [3, 4]):
        return ()
    try:
        layout = LongitudinalDesign(rows=tuple(PhysicalRow(count, diameter, role) for count, diameter, role in rows),
                                    required_area_mm2=required,
                                    constraint_sources=("PARENT_COMMON_BOTTOM",))
        resolved = tuple(engine._resolve_physical_layout(data.section, hoop, layout, face="bottom")
                         for hoop in hoops)
        minimum = max(engine.code.minimum_longitudinal_area(data.section.width_mm, item.effective_depth_mm,
                        data.section.fc_mpa, data.section.fy_mpa) for item in resolved)
        if layout.provided_area_mm2 + 1e-9 < max(required, minimum):
            return ()
        if data.section.is_smf and any(layout.provided_area_mm2 >
                engine.project.smf_max_longitudinal_ratio * data.section.width_mm * item.effective_depth_mm + 1e-9
                for item in resolved):
            return ()
        return (replace(resolved[0], required_area_mm2=max(required, minimum)),)
    except (DetailingError, ValueError):
        return ()


def _parent_cached_candidates(section, hoops, required, engine, **kwargs):
    """Memoize R7 physical proposals across a single mixed-parent line."""
    cache = getattr(engine, "_parent_candidate_cache", None)
    if cache is None:
        return candidates(section, hoops, required, engine, **kwargs)
    # This matches the layout dictionary's _context_record: only diameter and
    # legs affect candidates; spacing and hoop strengths do not affect rows.
    effective_hoops = tuple(dict.fromkeys((int(h.diameter_mm), int(h.legs)) for h in hoops))
    key = (section, effective_hoops, float(required), kwargs.get("first_diameter"),
           kwargs.get("max_layers"), kwargs.get("face", "top"),
           kwargs.get("preserve_support_alternatives", True), id(engine.project),
           id(engine.code), id(getattr(engine, "layout_dictionary_bank", None)))
    if key in cache:
        success, value = cache[key]
        cache.move_to_end(key)
        if success:
            return value
        error_type, error_args = value
        raise error_type(*error_args)
    try:
        value = candidates(section, hoops, required, engine, **kwargs)
    except DetailingError as error:
        if len(cache) >= R7_SPAN_CACHE_MAX_ENTRIES:
            cache.popitem(last=False)
        cache[key] = (False, (type(error), error.args))
        raise
    if len(cache) >= R7_SPAN_CACHE_MAX_ENTRIES:
        cache.popitem(last=False)
    cache[key] = (True, value)
    return value


def _style_span_candidates(data, engine, *, first_diameter=None, left_rows=None,
                           right_rows=None, support_exact=False, search_budget=None,
                           search_stats=None, fixed_face_rebar=None,
                           parent_bottom=None, parent_hoops=None):
    if data.section.detailing_profile is BeamDetailingProfile.UNRESOLVED:
        raise DetailingError("DETAILING_PROFILE_REVIEW_REQUIRED")
    library = engine.offline_style_library
    applicable = library.applicable_fields(data, engine)
    budget = search_budget if search_budget is not None else [MAX_COORDINATION_SEARCH_STATES]
    r6_dedup = bool(getattr(engine, "exact_candidate_state_dedup", False))
    independent_skin = bool(getattr(engine, "independent_skin_rebar", False))
    stats = search_stats if search_stats is not None else (
        _new_r6_candidate_search_stats() if r6_dedup else None
    )
    if stats is not None and _indexed_coordination(engine):
        stats["budget_basis"] = "CANDIDATE_SEARCH_STATES"
        stats.setdefault("checked_state_cache_hits", 0)
    trial_cache = {} if r6_dedup else None
    signature_cache = (
        _R9SignatureCache() if _indexed_coordination(engine)
        else {} if r6_dedup else None
    )
    geometry_cache = {} if r6_dedup else None
    context_key = _engine_astuple(data, engine) if r6_dedup or _indexed_coordination(engine) else None
    hoop_states = (parent_hoops,) if parent_hoops is not None else _style_hoop_states(
        data,
        engine,
        applicable,
        trial_cache=trial_cache,
        search_stats=stats if r6_dedup else None,
    )
    if not hoop_states:
        return
    top_required = [region.as_top_total_mm2 for region in data.regions]
    bottom_required = max(region.as_bottom_total_mm2 for region in data.regions)
    mode = ReinforcementMode.NORMAL
    seen_results = set()
    seen_candidate_states = set()
    seen_economic_states = set()
    checked_cache = _checked_state_cache(engine)

    def bounded_layouts(legacy_layouts, supported_layouts, *, mode="first_by_diameter"):
        selected = {}
        for item in legacy_layouts:
            if mode == "first":
                selected.setdefault((0,), item)
            elif mode == "first_by_diameter":
                selected.setdefault((item.rows[0].diameter_mm,), item)
            else:
                key = (item.rows[0].diameter_mm, len(item.rows))
                selected.setdefault(key, item)
        output = list(selected.values())
        known = {signature(item) for item in output}
        for item in supported_layouts:
            if signature(item) not in known:
                output.append(item)
                known.add(signature(item))
        return tuple(output)

    for hoops in hoop_states:
        if not r6_dedup:
            _consume_search_budget(budget)
        if parent_bottom is not None:
            lower = _fixed_parent_bottom(data, engine, hoops, parent_bottom, bottom_required)
        else:
            bottom_legacy = candidates(
                data.section, hoops, bottom_required, engine,
                max_layers=engine.project.max_main_bar_layers,
                face="bottom", preserve_support_alternatives=False,
                search_stats=stats if r6_dedup else None,
                geometry_cache=geometry_cache,
            )
            bottom_supported = approved_pattern_candidates(
                data.section, hoops, bottom_required, engine,
                applicable.get("bottom_rebar", ()), face="bottom",
                geometry_cache=geometry_cache,
                search_stats=stats if r6_dedup else None,
            )
            lower = bounded_layouts(bottom_legacy, bottom_supported, mode="first")
        if not lower:
            continue
        upper = []
        for index, (hoop, required) in enumerate(zip(hoops, top_required)):
            field = ("top_rebar_end_1", "top_rebar_middle", "top_rebar_end_2")[index]
            proposal_fn = _parent_cached_candidates if parent_bottom is not None else candidates
            legacy_layouts = proposal_fn(
                data.section, (hoop,), required, engine,
                max_layers=engine.project.max_main_bar_layers,
                face="top", preserve_support_alternatives=True,
                search_stats=stats if r6_dedup else None,
                geometry_cache=geometry_cache,
            )
            supported_layouts = approved_pattern_candidates(
                data.section, (hoop,), required, engine,
                applicable.get(field, ()), face="top",
                geometry_cache=geometry_cache,
                search_stats=stats if r6_dedup else None,
            )
            if parent_bottom is not None:
                upper.append(bounded_layouts(
                    legacy_layouts, supported_layouts,
                    mode=("support_alternatives" if index in (0, 2) else "first_by_diameter")))
            else:
                upper.append(bounded_layouts(
                    legacy_layouts, supported_layouts,
                    mode=("support_alternatives" if index in (0, 2) else "first_by_diameter"),
                ))
        face_options = () if independent_skin else engine._approved_face_rebar_candidates(
            data, applicable.get("face_rebar", ())
        )
        first_diameter_set = {
            item.rows[0].diameter_mm
            for group in upper
            for item in group
        }
        first_diameters = tuple(
            diameter
            for diameter in engine.project.ordered_longitudinal_diameters_for(data.section)
            if diameter in first_diameter_set
        )
        if data.section.is_secondary:
            first_diameters = tuple(sorted(
                first_diameters,
                key=lambda value: (value > engine.project.sb_top_preferred_max_diameter_mm, -value),
            ))
        if first_diameter is not None:
            first_diameters = [diameter for diameter in first_diameters if diameter == first_diameter]
        for diameter in first_diameters:
            diameter_upper = [tuple(
                item for item in group if item.rows[0].diameter_mm == diameter
            ) for group in upper]
            diameter_lower = tuple(item for item in lower if item.rows[0].diameter_mm in
                                   engine.project.ordered_longitudinal_diameters_for(data.section))
            if any(not group for group in diameter_upper) or not diameter_lower:
                continue
            for index, required_rows in ((0, left_rows), (2, right_rows)):
                if required_rows is not None:
                    diameter_upper[index] = tuple(
                        item for item in diameter_upper[index]
                        if _support_compatible(signature(item), required_rows, exact=support_exact)
                    )
            if any(not group for group in diameter_upper):
                continue

            def skip_exact_duplicate(combination):
                if stats is not None:
                    stats["raw_layout_combinations"] += 1
                left, middle, right, bottom = combination[:4]
                face_rebar = fixed_face_rebar if independent_skin else combination[4]
                key = _r6_candidate_state_key(
                    context_key,
                    hoops,
                    (left, middle, right),
                    (bottom, bottom, bottom),
                    face_rebar,
                    signature_cache=signature_cache,
                    search_stats=stats,
                )
                if key in seen_candidate_states:
                    if stats is not None:
                        stats["exact_duplicate_states_skipped"] += 1
                    return True
                seen_candidate_states.add(key)
                return False

            groups = (*diameter_upper, diameter_lower)
            if not independent_skin:
                groups = (*groups, face_options)
            parent_support_pool = parent_bottom is not None and not applicable and not _indexed_coordination(engine)
            accepted_supports = set()
            possible_supports = ({
                (tuple(row.diameter_mm for row in left.rows), tuple(row.diameter_mm for row in right.rows))
                for left in diameter_upper[0] for right in diameter_upper[2]
            } if parent_support_pool else None)

            def skip_parent_duplicate(combination):
                state = (tuple(row.diameter_mm for row in combination[0].rows),
                         tuple(row.diameter_mm for row in combination[2].rows))
                if parent_support_pool and state in accepted_supports:
                    return True
                return skip_exact_duplicate(combination) if r6_dedup else False

            for combination in _combinations(
                groups,
                budget,
                skip_if=skip_parent_duplicate if (r6_dedup or parent_support_pool) else None,
                stop_if=(lambda: possible_supports.issubset(accepted_supports)) if possible_supports is not None else None,
                expand_axes=(lambda combination: (0, 2) if
                             (tuple(row.diameter_mm for row in combination[0].rows),
                              tuple(row.diameter_mm for row in combination[2].rows)) in accepted_supports
                             else range(len(groups))) if parent_support_pool else None,
            ):
                left, middle, right, bottom = combination[:4]
                face_rebar = fixed_face_rebar if independent_skin else combination[4]
                if stats is not None:
                    stats["unique_candidate_validation_states"] += 1
                    stats["base_candidate_validation_states"] += 1
                regions = tuple(
                    RegionDetailing(
                        demand.name,
                        demand,
                        engine.code.minimum_longitudinal_area(
                            data.section.width_mm,
                            min(top.effective_depth_mm, bottom.effective_depth_mm),
                            data.section.fc_mpa,
                            data.section.fy_mpa,
                        ),
                        demand.as_flex_top_mm2,
                        demand.as_flex_bottom_mm2,
                        hoop,
                        top,
                        bottom,
                    )
                    for demand, hoop, top in zip(data.regions, hoops, (left, middle, right))
                )
                checked_key = None
                result = None
                if checked_cache is not None:
                    checked_key = ("style", _r6_candidate_state_key(
                        context_key, hoops, (left, middle, right), (bottom, bottom, bottom),
                        face_rebar, signature_cache=signature_cache))
                    result = checked_cache.get(checked_key)
                    if result is not None and stats is not None:
                        stats["checked_state_cache_hits"] += 1
                        stats["unique_candidate_validation_states"] -= 1
                        stats["base_candidate_validation_states"] -= 1
                try:
                    if result is None:
                        if stats is not None:
                            stats["candidate_row_result_build_calls"] += 1
                        result = engine._build_row_result(
                            data, mode, regions, face_rebar_override=face_rebar
                        )
                        result = _refresh_result_physical_layout(result, engine)
                        following, demands = _fit_hoops(result, engine, preserve_current=True)
                        if _hoop_signature(following) != _hoop_signature(hoops):
                            continue
                        result = replace(
                            result,
                            regions=tuple(replace(region, demand=demand)
                                          for region, demand in zip(result.regions, demands)),
                        )
                        result = finalize_rows(
                            result,
                            engine,
                            search_stats=stats if r6_dedup else None,
                        )
                        _remember_checked_state(checked_cache, checked_key, result)
                except DetailingError:
                    continue
                variants = [result]
                if _revised_coordination(engine):
                    stable = library.stable_signature(result)
                    if stable not in seen_results:
                        seen_results.add(stable)
                        if parent_support_pool:
                            accepted_supports.add((tuple(row.diameter_mm for row in result.regions[0].top.rows),
                                                   tuple(row.diameter_mm for row in result.regions[-1].top.rows)))
                        yield replace(result, coordination_status="STABLE",
                                      coordination_audit=(library.audit(data, result, engine),))
                economic_hoop_states = () if parent_hoops is not None else _economic_hoop_states(
                    data,
                    engine,
                    result,
                    trial_cache=trial_cache,
                    search_stats=stats if r6_dedup else None,
                )
                if stats is not None:
                    stats["economic_alternative_states"] += len(economic_hoop_states)
                for economic_hoops in economic_hoop_states:
                    if _hoop_signature(economic_hoops) == _hoop_signature(hoops):
                        continue
                    economic_regions = tuple(
                        replace(region, stirrup=hoop)
                        for region, hoop in zip(result.regions, economic_hoops)
                    )
                    if r6_dedup:
                        economic_key = _r6_result_state_key(
                            context_key,
                            economic_regions,
                            result.face_rebar,
                            signature_cache=signature_cache,
                            search_stats=stats,
                        )
                        if economic_key in seen_economic_states:
                            stats["exact_duplicate_states_skipped"] += 1
                            continue
                        seen_economic_states.add(economic_key)
                    _consume_search_budget(budget)
                    if stats is not None:
                        stats["unique_candidate_validation_states"] += 1
                        stats["economic_candidate_validation_states"] += 1
                    cache_key = None
                    economic = None
                    if checked_cache is not None:
                        cache_key = ("economic", _r6_result_state_key(
                            context_key, economic_regions, result.face_rebar,
                            signature_cache=signature_cache))
                        economic = checked_cache.get(cache_key)
                        if economic is not None and stats is not None:
                            stats["checked_state_cache_hits"] += 1
                            stats["unique_candidate_validation_states"] -= 1
                            stats["economic_candidate_validation_states"] -= 1
                    try:
                        if economic is None:
                            if stats is not None:
                                stats["candidate_row_result_build_calls"] += 1
                            economic = engine._build_row_result(
                                data,
                                mode,
                                economic_regions,
                                face_rebar_override=result.face_rebar,
                            )
                            economic = _refresh_result_physical_layout(economic, engine)
                            following, demands = _fit_hoops(
                                economic, engine, preserve_current=True
                            )
                            if _hoop_signature(following) != _hoop_signature(economic_hoops):
                                continue
                            economic = replace(
                                economic,
                                regions=tuple(
                                    replace(region, demand=demand)
                                    for region, demand in zip(economic.regions, demands)
                                ),
                            )
                            economic = finalize_rows(
                                economic,
                                engine,
                                search_stats=stats if r6_dedup else None,
                            )
                            _remember_checked_state(checked_cache, cache_key, economic)
                        economic = replace(
                            economic,
                            coordination_status="STABLE",
                            coordination_audit=result.coordination_audit,
                        )
                    except DetailingError:
                        continue
                    if _revised_coordination(engine):
                        stable = library.stable_signature(economic)
                        if stable not in seen_results:
                            seen_results.add(stable)
                            yield replace(economic, coordination_status="STABLE",
                                          coordination_audit=(library.audit(data, economic, engine),))
                    variants.append(economic)

                if _revised_coordination(engine):
                    continue
                for candidate in variants:
                    stable = library.stable_signature(candidate)
                    if stable in seen_results:
                        continue
                    seen_results.add(stable)
                    audit = library.audit(data, candidate, engine)
                    if parent_support_pool:
                        accepted_supports.add((tuple(row.diameter_mm for row in candidate.regions[0].top.rows),
                                               tuple(row.diameter_mm for row in candidate.regions[-1].top.rows)))
                    yield replace(
                        candidate,
                        coordination_status="STABLE",
                        coordination_audit=(audit,),
                    )


def span_candidates(data, engine, *, first_diameter=None, left_rows=None,
                    right_rows=None, support_exact=False, search_budget=None,
                    coordination_pool=False, search_stats=None,
                    first_fallback_only=False, parent_bottom=None, parent_hoops=None):
    scope = (
        engine._r7_span_cache_scope(data)
        if getattr(engine, "_r7_span_caches_enabled", False) else nullcontext()
    )
    with scope:
        if parent_bottom is not None:
            if engine.ranking_mode != "style" or parent_hoops is None:
                raise DetailingError("PARENT_COMMON_LAYOUT_REQUIRES_DRAWING_STYLE")
            yield from _style_span_candidates(
                data, engine, first_diameter=first_diameter, left_rows=left_rows,
                right_rows=right_rows, support_exact=support_exact, search_budget=search_budget,
                fixed_face_rebar=(engine._construction_face_rebar(data.section)
                                  if getattr(engine, "independent_skin_rebar", False) else None),
                parent_bottom=parent_bottom, parent_hoops=parent_hoops,
            )
            return
        yield from _span_candidates(
            data, engine, first_diameter=first_diameter, left_rows=left_rows,
            right_rows=right_rows, support_exact=support_exact,
            search_budget=search_budget, coordination_pool=coordination_pool,
            search_stats=search_stats, first_fallback_only=first_fallback_only,
        )


def _span_candidates(data, engine, *, first_diameter=None, left_rows=None,
                     right_rows=None, support_exact=False, search_budget=None,
                     coordination_pool=False, search_stats=None,
                     first_fallback_only=False):
    fixed_face_rebar = (
        engine._construction_face_rebar(data.section)
        if getattr(engine, "independent_skin_rebar", False) else None
    )
    if engine.ranking_mode != "style":
        yield from _legacy_span_candidates(
            data,
            engine,
            first_diameter=first_diameter,
            left_rows=left_rows,
            right_rows=right_rows,
            support_exact=support_exact,
            search_budget=search_budget,
            coordination_pool=coordination_pool,
            fixed_face_rebar=fixed_face_rebar,
        )
        return
    library = engine.offline_style_library
    applicable = library.applicable_fields(data, engine)
    if not applicable:
        reason = "NO_APPLICABLE_PATTERN"
        for result in _legacy_span_candidates(
            data, engine, first_diameter=first_diameter, left_rows=left_rows,
            right_rows=right_rows, support_exact=support_exact, search_budget=search_budget,
            coordination_pool=coordination_pool,
            fixed_face_rebar=fixed_face_rebar,
        ):
            audit = library.audit(data, result, engine, fallback_reason=reason)
            yield replace(result, coordination_audit=(*result.coordination_audit, audit))
            if first_fallback_only:
                return
        return
    if _revised_coordination(engine):
        matched_any = False
        for result in _style_span_candidates(
            data, engine, first_diameter=first_diameter, left_rows=left_rows,
            right_rows=right_rows, support_exact=support_exact, search_budget=search_budget,
            search_stats=search_stats, fixed_face_rebar=fixed_face_rebar,
        ):
            matched_any |= bool(library.matched_fields(data, result, engine))
            yield result
        if not matched_any:
            for result in _legacy_span_candidates(
                data, engine, first_diameter=first_diameter, left_rows=left_rows,
                right_rows=right_rows, support_exact=support_exact, search_budget=search_budget,
                coordination_pool=True, fixed_face_rebar=fixed_face_rebar,
            ):
                audit = library.audit(data, result, engine, fallback_reason="NO_FEASIBLE_SUPPORTED_PATTERN")
                yield replace(result, coordination_audit=(*result.coordination_audit, audit))
        return
    candidates_found = tuple(_style_span_candidates(
        data,
        engine,
        first_diameter=first_diameter,
        left_rows=left_rows,
        right_rows=right_rows,
        support_exact=support_exact,
        search_budget=search_budget,
        search_stats=search_stats,
        fixed_face_rebar=fixed_face_rebar,
    ))
    if not candidates_found or not any(library.matched_fields(data, result, engine) for result in candidates_found):
        reason = "NO_FEASIBLE_SUPPORTED_PATTERN"
        applicable_rule_ids = {
            rule["rule_id"] for rules in applicable.values() for rule in rules
        }
        matched_rule_ids = {
            match.rule_id
            for result in candidates_found
            for match in library.matched_fields(data, result, engine)
        }
        unmatched_rule_ids = applicable_rule_ids - matched_rule_ids
        for result in _legacy_span_candidates(
            data, engine, first_diameter=first_diameter, left_rows=left_rows,
            right_rows=right_rows, support_exact=support_exact, search_budget=search_budget,
            coordination_pool=coordination_pool,
            fixed_face_rebar=fixed_face_rebar,
        ):
            audit = library.audit(
                data,
                result,
                engine,
                fallback_reason=reason,
                unmatched_applicable_pattern_rule_ids=unmatched_rule_ids,
            )
            yield replace(result, coordination_audit=(*result.coordination_audit, audit))
            if first_fallback_only:
                return
        return
    by_right_state = {}
    for result in candidates_found:
        left_state = tuple(row[1] for row in signature(result.regions[0].top))
        right_state = tuple(row[1] for row in signature(result.regions[-1].top))
        by_right_state.setdefault((left_state, right_state), []).append(result)
    selected = []
    for state_candidates in by_right_state.values():
        support_count = max(
            len(library.non_hoop_matches(data, result, engine))
            for result in state_candidates
        )
        supported = [
            result for result in state_candidates
            if len(library.non_hoop_matches(data, result, engine)) == support_count
        ]
        selected.append(min(supported, key=lambda result: library.rank_key(data, result, engine)))
    yield from selected


def design_span(data, engine, *, prepare_coordination_pool=True):
    if engine.ranking_mode == "style":
        library = engine.offline_style_library
        lookup_snapshot = (
            library.r6_lookup_counters_snapshot()
            if getattr(engine, "exact_candidate_state_dedup", False)
            else None
        )
        applicable = library.applicable_fields(data, engine)
        budget = [MAX_COORDINATION_SEARCH_STATES]
        stats = (
            _new_r6_candidate_search_stats()
            if getattr(engine, "exact_candidate_state_dedup", False) and applicable
            else None
        )
        candidate_pool = []
        complete = True
        prepare_pool = prepare_coordination_pool or not getattr(engine, "independent_skin_rebar", False)
        try:
            candidate_pool.extend(span_candidates(
                data,
                engine,
                search_budget=budget,
                coordination_pool=prepare_pool,
                search_stats=stats,
                first_fallback_only=not prepare_pool,
            ))
        except _CoordinationSearchLimit as exc:
            if stats is not None:
                _record_r6_style_lookup_stats(stats, library, lookup_snapshot)
                exc.candidate_search_audit = _r6_candidate_search_audit(
                    stats,
                    complete=False,
                    budget_remaining=budget[0],
                )
            if applicable and (not _revised_coordination(engine) or not candidate_pool):
                raise
            complete = False
        search_audit = None
        if candidate_pool:
            if stats is not None:
                search_audit = _r6_candidate_search_audit(
                    stats,
                    complete=complete,
                    budget_remaining=budget[0],
                )
                candidate_pool = [
                    replace(
                        candidate,
                        coordination_audit=(*candidate.coordination_audit, search_audit),
                    )
                    for candidate in candidate_pool
                ]
            if not applicable:
                result = candidate_pool[0]
            else:
                matched = [
                    (library.matched_fields(data, candidate, engine), candidate)
                    for candidate in candidate_pool
                ]
                best_support = max(
                    len(library.non_hoop_matches(data, candidate, engine))
                    for _fields, candidate in matched
                )
                if best_support:
                    eligible = [
                        candidate for _fields, candidate in matched
                        if len(library.non_hoop_matches(data, candidate, engine)) == best_support
                    ]
                    result = min(eligible, key=lambda candidate: library.rank_key(data, candidate, engine))
                elif any(fields for fields, _candidate in matched):
                    result = min(
                        (candidate for fields, candidate in matched if fields),
                        key=lambda candidate: library.rank_key(data, candidate, engine),
                    )
                else:
                    # A condition with no matched physical pattern keeps the
                    # legacy-ranked feasible candidate under the active checks.
                    result = candidate_pool[0]
            if stats is not None:
                search_audit.update(
                    _record_r6_style_lookup_stats(stats, library, lookup_snapshot)
                )
            if prepare_pool and not _indexed_coordination(engine):
                _store_style_candidate_pool(engine, result, candidate_pool, complete=complete)
            if not complete and _revised_coordination(engine):
                result = replace(result, coordination_status="FEASIBLE_OPTIMIZATION_INCOMPLETE",
                    coordination_audit=(*result.coordination_audit,
                        {"search_complete": False, "termination_stage": "SPAN_CANDIDATE_GENERATION",
                         "termination_reason": "ROW_SEGMENT_SEARCH_LIMIT", "budget_limit": MAX_COORDINATION_SEARCH_STATES,
                         "states_used": MAX_COORDINATION_SEARCH_STATES, "attempted_over_budget": True}))
            return result
        result = None
    else:
        result = next(span_candidates(data, engine), None)
    if result is None:
        error = DetailingError(
            f"{data.section.name}: NO_FEASIBLE_TOTAL_LONGITUDINAL_LAYOUT"
        )
        if engine.ranking_mode == "style" and stats is not None:
            _record_r6_style_lookup_stats(stats, library, lookup_snapshot)
            error.candidate_search_audit = _r6_candidate_search_audit(
                stats,
                complete=complete,
                budget_remaining=budget[0],
            )
        raise error
    return result


def finalize_rows(result, engine, *, search_stats=None):
    scope = (
        engine._r7_span_cache_scope(result.input_data)
        if getattr(engine, "_r7_span_caches_enabled", False) else nullcontext()
    )
    with scope:
        return _finalize_rows(result, engine, search_stats=search_stats)


def _finalize_rows(result, engine, *, search_stats=None):
    result = _refresh_result_physical_layout(result, engine)
    engine._validate_revision_layout(result.section, result.regions)
    for region in result.regions:
        for face in (region.top, region.bottom):
            if face.rows[0].count < region.stirrup.legs:
                raise DetailingError(f"{result.beam_name}/{region.name}: FIRST_ROW_HOOP_SUPPORT_FAILED")
            minimum = engine.code.minimum_longitudinal_area(
                result.section.width_mm, face.effective_depth_mm, result.section.fc_mpa, result.section.fy_mpa)
            total_required = (
                region.demand.as_top_total_mm2
                if face is region.top
                else region.demand.as_bottom_total_mm2
            )
            if face.provided_area_mm2 + 1e-9 < max(minimum, face.required_area_mm2, total_required):
                raise DetailingError(
                    f"{result.beam_name}/{region.name}: "
                    "NO_FEASIBLE_TOTAL_LONGITUDINAL_LAYOUT"
                )
    result = engine.refresh_capacity_shear_after_layout_change(result)
    regions = engine._finalize_stirrup_controls(result.section, result.regions)
    if result.input_data is None:
        raise DetailingError("FINAL_ROW_INPUT_CONTEXT_MISSING")
    if search_stats is not None:
        search_stats["finalize_rows_calls"] += 1
        search_stats["candidate_row_result_build_calls"] += 1
    rebuilt = engine._build_row_result(
        result.input_data,
        result.mode,
        regions,
        face_rebar_override=(result.face_rebar if engine.ranking_mode == "style"
                             or getattr(engine, "independent_skin_rebar", False) else None),
    )
    failures = [check.status for check in rebuilt.smf_checks if check.status not in {"PASS", "INFO", "WARN"}]
    if failures:
        raise DetailingError("FINAL_ROW_SMF_LONGITUDINAL_FAILED:" + ",".join(sorted(set(failures))))
    return replace(rebuilt, continuity_option=result.continuity_option,
        coordination_status=result.coordination_status, coordination_audit=result.coordination_audit)


def _enforce_through_top_row(result, engine, *, base_diameter=None, base_count=None):
    if result.input_data is None:
        raise DetailingError("ROW_INPUT_CONTEXT_MISSING")
    candidate = next(span_candidates(result.input_data, engine, first_diameter=base_diameter), None)
    if candidate is None:
        raise DetailingError("COMMON_THROUGH_ROW_DOES_NOT_FIT")
    return candidate


def _legacy_coordinate_segment(results, option, engine, *, precomputed_pools=None, constrained_pool=False):
    """Backtrack over verified support states, memoizing failed suffixes.

    Counts inside a span are independent. Only shared supports impose the
    selected equality rule. Each candidate retains its own section/profile.
    """
    from .detailing import ContinuityOption
    if not results:
        return ()
    if any(result.input_data is None for result in results):
        raise DetailingError("ROW_INPUT_CONTEXT_MISSING")
    common = set(engine.project.ordered_longitudinal_diameters_for(results[0].section))
    for result in results[1:]:
        common.intersection_update(engine.project.ordered_longitudinal_diameters_for(result.section))
    failures = set()
    search_budget = [MAX_COORDINATION_SEARCH_STATES]
    precomputed_by_key = {}

    def solve(index, previous, diameter):
        if index == len(results):
            return ()
        key = (index, previous, diameter)
        if key in failures:
            return None
        exact = index > 0 and (option is ContinuityOption.OPTION_1_STRICT_EQUAL
            or results[index-1].section.is_secondary or results[index].section.is_secondary)
        tried_right = set()
        if precomputed_pools is None or index not in precomputed_pools:
            candidate_iter = span_candidates(
                results[index].input_data,
                engine,
                first_diameter=diameter,
                left_rows=previous if previous is not None else None,
                support_exact=exact,
                search_budget=search_budget,
                coordination_pool=constrained_pool,
            )
        else:
            pool_key = (index, diameter)
            if pool_key not in precomputed_by_key:
                precomputed_by_key[pool_key] = tuple(
                    candidate for candidate in precomputed_pools[index]
                    if signature(candidate.regions[0].top)[0][1] == diameter
                )
                for _candidate in precomputed_by_key[pool_key]:
                    _consume_search_budget(search_budget)
            candidate_iter = precomputed_by_key[pool_key]
        for candidate in candidate_iter:
            left = signature(candidate.regions[0].top)
            if previous is not None and not _support_compatible(left, previous, exact=exact):
                continue
            right = signature(candidate.regions[-1].top)
            if right in tried_right:
                continue
            tried_right.add(right)
            suffix = solve(index+1, right, diameter)
            if suffix is not None:
                return (replace(candidate, continuity_option=option,
                    coordination_audit=(*candidate.coordination_audit, {
                        "shared_support": index, "incoming_rows": previous,
                        "exact": exact, "first_row_count_independent_within_span": True,
                        "profile": candidate.detailing_profile.value})), *suffix)
        failures.add(key)
        return None

    diameters = engine.project.ordered_longitudinal_diameters_for(results[0].section)
    if all(result.section.is_secondary for result in results):
        diameters = tuple(sorted(diameters,
            key=lambda value: (value > engine.project.sb_top_preferred_max_diameter_mm, -value)))
    for diameter in diameters:
        if diameter not in common:
            continue
        chosen = solve(0, None, diameter)
        if chosen is not None:
            return chosen
    names = ",".join(f"{r.beam_name}[{r.detailing_profile.value},{r.section.width_mm}x{r.section.depth_mm}]" for r in results)
    raise DetailingError(f"ROW_SEGMENT_NO_COMMON_FEASIBLE_LAYOUT: {names}; rejected_support_states={len(failures)}")


def _indexed_legacy_coordinate_segment(results, option, engine):
    """Keep legacy first-feasible ordering, retrying final failures in one DFS."""
    from .detailing import ContinuityOption
    if not results:
        return ()
    if any(result.input_data is None for result in results):
        raise DetailingError("ROW_INPUT_CONTEXT_MISSING")
    common = set(engine.project.ordered_longitudinal_diameters_for(results[0].section))
    for result in results[1:]:
        common.intersection_update(engine.project.ordered_longitudinal_diameters_for(result.section))
    diameters = engine.project.ordered_longitudinal_diameters_for(results[0].section)
    if all(result.section.is_secondary for result in results):
        diameters = tuple(sorted(diameters,
            key=lambda value: (value > engine.project.sb_top_preferred_max_diameter_mm, -value)))
    budget = [MAX_COORDINATION_SEARCH_STATES]
    library = engine.offline_style_library
    rejected = set()
    checked = {}
    final_errors = []

    def identity(result):
        return (id(result.input_data), library.stable_signature(result))

    def solve(index, previous, diameter, selected):
        if index == len(results):
            started = time.perf_counter()
            try:
                output = []
                for result in selected:
                    key = identity(result)
                    try:
                        if key not in checked:
                            checked[key] = finalize_rows(result, engine)
                        final = checked[key]
                        output.append(replace(final, continuity_option=result.continuity_option,
                            coordination_status=result.coordination_status,
                            coordination_audit=result.coordination_audit))
                    except DetailingError as exc:
                        rejected.add(key)
                        final_errors.append(str(exc))
                        return None
                return tuple(output)
            finally:
                timings = engine.__dict__.setdefault("_indexed_coordination_phase_seconds", {})
                timings["final_check"] = timings.get("final_check", 0.0) + time.perf_counter() - started
        exact = index > 0 and (option is ContinuityOption.OPTION_1_STRICT_EQUAL
            or results[index - 1].section.is_secondary or results[index].section.is_secondary)
        for candidate in span_candidates(results[index].input_data, engine,
                first_diameter=diameter, left_rows=previous, support_exact=exact,
                search_budget=budget, coordination_pool=True):
            if identity(candidate) in rejected:
                continue
            left = signature(candidate.regions[0].top)
            if previous is not None and not _support_compatible(left, previous, exact=exact):
                continue
            right = signature(candidate.regions[-1].top)
            coordinated = replace(candidate, continuity_option=option,
                coordination_audit=(*candidate.coordination_audit, {
                    "shared_support": index, "incoming_rows": previous,
                    "exact": exact, "first_row_count_independent_within_span": True,
                    "profile": candidate.detailing_profile.value}))
            path = solve(index + 1, right, diameter, (*selected, coordinated))
            if path is not None:
                return path
        return None

    for diameter in diameters:
        if diameter not in common:
            continue
        selected = solve(0, None, diameter, ())
        if selected is not None:
            return selected
    if final_errors:
        raise DetailingError("FINAL_ROW_ALL_EXPLORED_PATHS_FAILED: " + "; ".join(final_errors))
    names = ",".join(result.beam_name for result in results)
    raise DetailingError(f"ROW_SEGMENT_NO_COMMON_FEASIBLE_LAYOUT: {names}")


def _style_path_rank(results, engine):
    if not _indexed_coordination(engine):
        return _style_path_rank_legacy(results, engine)
    from src.design.equivalent_steel import equivalent_steel_mm3

    library = engine.offline_style_library
    indexed = _indexed_coordination(engine)
    cache = None
    if indexed:
        cache = engine.__dict__.setdefault("_indexed_local_rank_cache", OrderedDict())
    volume = 0.0
    non_hoop_count = 0
    hoop_count = 0
    confidence = 0
    row_count = 0
    signatures = []
    for result in results:
        stable = library.stable_signature(result)
        key = (id(result.input_data), stable)
        entry = cache.get(key) if cache is not None else None
        if entry is not None and entry[0] is result.input_data:
            nonhoop, value, hoop, family, rows = entry[1]
            cache.move_to_end(key)
        else:
            fields = library.matched_fields(result.input_data, result, engine)
            metric = equivalent_steel_mm3(result)
            if not metric.available or metric.total_mm3 is None:
                raise DetailingError(f"STYLE_EQUIVALENT_STEEL_UNAVAILABLE: {metric.reason}")
            nonhoop = sum(not field.field.startswith("stirrup_") for field in fields)
            value = float(metric.total_mm3)
            hoop = sum(field.field.startswith("stirrup_") for field in fields)
            family = sum(field.family_count for field in fields)
            rows = sum(len(region.top.rows) + len(region.bottom.rows) for region in result.regions)
            if cache is not None:
                cache[key] = (result.input_data, (nonhoop, value, hoop, family, rows))
                cache.move_to_end(key)
                if len(cache) > 4096:
                    cache.popitem(last=False)
        volume += value
        non_hoop_count += nonhoop
        hoop_count += hoop
        confidence += family
        row_count += rows
        signatures.append(stable)
    return (-non_hoop_count, volume, -hoop_count, -confidence, row_count, "|".join(signatures))


def _style_path_rank_legacy(results, engine):
    from src.design.equivalent_steel import equivalent_steel_mm3

    library = engine.offline_style_library
    matched = [
        library.matched_fields(result.input_data, result, engine)
        for result in results
    ]
    volume = 0.0
    non_hoop_count = 0
    hoop_count = 0
    confidence = 0
    row_count = 0
    for result, fields in zip(results, matched):
        metric = equivalent_steel_mm3(result)
        if not metric.available or metric.total_mm3 is None:
            raise DetailingError(f"STYLE_EQUIVALENT_STEEL_UNAVAILABLE: {metric.reason}")
        volume += float(metric.total_mm3)
        non_hoop_count += sum(not field.field.startswith("stirrup_") for field in fields)
        hoop_count += sum(field.field.startswith("stirrup_") for field in fields)
        confidence += sum(field.family_count for field in fields)
        row_count += sum(len(region.top.rows) + len(region.bottom.rows) for region in result.regions)
    stable = "|".join(library.stable_signature(result) for result in results)
    return (-non_hoop_count, volume, -hoop_count, -confidence, row_count, stable)


def _dominates_future(left, right):
    """Conservative dominance for paths with the same future support state.

    Volume is added span by span in the production rank. Componentwise
    comparison preserves the ordering even when floating-point sums round to
    equality; in that case the trailing rank must also be no worse.
    """
    left_rank, left_volumes = left
    right_rank, right_volumes = right
    if left_rank[0] != right_rank[0]:
        return left_rank[0] < right_rank[0]
    return (len(left_volumes) == len(right_volumes)
            and all(a <= b for a, b in zip(left_volumes, right_volumes))
            and left_rank[2:] <= right_rank[2:])


def _retain_future_candidate(frontiers, future, choice, engine):
    rank = _style_path_rank(choice, engine)
    volumes = tuple(_style_path_rank((item,), engine)[1] for item in choice)
    candidate = (rank, volumes)
    frontier = frontiers.setdefault(future, [])
    if any(_dominates_future(item, candidate) for item in frontier):
        return False
    frontier[:] = [item for item in frontier if not _dominates_future(candidate, item)]
    frontier.append(candidate)
    return True


def _parent_bottom_proposals(inputs, engine, hoop_sets):
    """Union the existing R7 physical-row proposals under the parent demand."""
    required = max(region.as_bottom_total_mm2 for data in inputs for region in data.regions)
    proposals = {}
    approved = set()
    for data, states in zip(inputs, hoop_sets):
        hoops = states[0]
        normal = _parent_cached_candidates(data.section, hoops, required, engine,
                            max_layers=engine.project.max_main_bar_layers,
                            face="bottom", preserve_support_alternatives=False)
        by_shape = {}
        for candidate in normal:
            by_shape.setdefault((candidate.rows[0].diameter_mm, len(candidate.rows)), candidate)
        rules = engine.offline_style_library.applicable_fields(data, engine).get("bottom_rebar", ())
        patterned = approved_pattern_candidates(data.section, hoops, required, engine, rules, face="bottom")
        for candidate in (*by_shape.values(), *patterned):
            rows = tuple((row.count, row.diameter_mm, row.role) for row in candidate.rows)
            proposals[rows] = candidate.provided_area_mm2
        for candidate in patterned:
            approved.add(tuple((row.count, row.diameter_mm, row.role) for row in candidate.rows))
    return tuple(sorted(proposals, key=lambda rows: (rows not in approved, proposals[rows], len(rows), rows)))


def _parent_hoop_options(data, engine, diameter, *, secondary_target=None, primary_target=None):
    """Materialize catalogue options, checked again against final rows."""
    section = data.section
    if secondary_target is not None:
        spacing, legs = secondary_target
        target = {"diameter_mm": diameter, "spacing_mm": spacing, "legs": legs}
        state = tuple(_style_hoop(section, demand, target, engine) for demand in data.regions)
        if all(state) and all(_parent_hoop_potential(data, engine, demand, hoop)
                              for demand, hoop in zip(data.regions, state)):
            yield state
        return
    if primary_target is not None:
        state = tuple(_style_hoop(section, demand,
                         {"diameter_mm": diameter, "spacing_mm": spacing, "legs": legs}, engine)
                      for demand, (spacing, legs) in zip(data.regions, primary_target))
        if all(state) and all(_parent_hoop_potential(data, engine, demand, hoop)
                              for demand, hoop in zip(data.regions, state)):
            yield state
        return
    minimum_legs = max(2, *(engine.code.minimum_legs(section, demand.name) for demand in data.regions),
                       engine.project.preferred_stirrup_legs(section))
    minimum_legs += minimum_legs % 2
    max_legs = max(engine._capacity_per_layer(section, diameter, db)
                   for db in engine.project.ordered_longitudinal_diameters_for(section))
    for legs in range(minimum_legs, max_legs + 1, 2):
        choices = [tuple(sorted(engine.project.allowed_spacings_for(section, demand.name), reverse=True))
                   for demand in data.regions]
        for spacings in product(*choices):
            state = tuple(_style_hoop(section, demand,
                                      {"diameter_mm": diameter, "spacing_mm": spacing, "legs": legs}, engine)
                          for demand, spacing in zip(data.regions, spacings))
            if all(state):
                yield state


def _parent_hoop_potential(data, engine, demand, hoop):
    """Reject an impossible hoop using optimistic section depth before row search."""
    try:
        required = _required_hoop_legs(data.section, demand, engine, hoop.diameter_mm,
            hoop.spacing_mm, max_main=max(engine.project.ordered_longitudinal_diameters_for(data.section)),
            effective_depth=data.section.depth_mm)
        return required is not None and hoop.legs >= required
    except DetailingError:
        return False


def _parent_candidate_blockers(result):
    # These pre-existing project review items are unrelated to selection of
    # physical rows. Keep them on the result; they are never marked PASS.
    independent_reviews = {"ANCHORAGE_UNVERIFIED", "SPLICE_UNVERIFIED",
                           "TORSION_PERIMETER_LAYOUT_UNVERIFIED"}
    blockers = set(result.errors) - independent_reviews
    if "SMF_DESIGN_SOURCE_UNVERIFIED" in blockers and result.section.is_smf:
        checks = tuple(check for check in result.constructability.checks
                       if check.status == "SMF_DESIGN_SOURCE_UNVERIFIED")
        if checks and all(check.rule_id == "SMF-FRAME-TYPE" and
                          " ".join(str(check.actual or "").casefold().split()) == "program determined"
                          for check in checks):
            blockers.remove("SMF_DESIGN_SOURCE_UNVERIFIED")
    return blockers


def _parent_candidate_eligible(result):
    return not _parent_candidate_blockers(result)


def _parent_interface_hoops_valid(
    results,
    *,
    external_support_faces_confirmed=False,
    external_scope_audit=None,
):
    """Check actual parent-coordinate hoop gaps across section interfaces."""
    if not results or not results[0].section.is_smf:
        # Ordinary parent runs use one continuous SB specification or a
        # shared PB regional profile; their ordinary zone has no hoop grid.
        return True
    positions = []
    for result in results:
        context = result.input_data.parent_context
        if context is None or result.constructability is None:
            return False
        for hoop in result.constructability.hoop_positions:
            positions.append(context.segment_start_mm + hoop.location_mm)
    positions.sort()
    for left, right in zip(results, results[1:]):
        boundary = right.input_data.parent_context.segment_start_mm
        support_i, support_j = left.input_data.left_support, left.input_data.right_support
        missing_face_coordinates = any(
            support is None
            for result in (left, right)
            for support in (result.input_data.left_support, result.input_data.right_support)
        )
        if external_support_faces_confirmed and missing_face_coordinates:
            if external_scope_audit is not None:
                external_scope_audit.append({
                    "left_beam_name": left.beam_name,
                    "right_beam_name": right.beam_name,
                    "boundary_mm": boundary,
                    "check": "PARENT_INTERFACE_HOOP_GRID",
                    "status": "EXTERNAL_SCOPE",
                    "workflow_status": "EXTERNALLY_CONFIRMED",
                    "support_face_coordinates_synthesized": False,
                    "note": "The external support-face workflow is confirmed; this engine has no coordinates to check this interface hoop grid.",
                })
            continue
        if support_i is None or support_j is None:
            return False
        if not support_i.face_location_mm < boundary < support_j.face_location_mm:
            continue
        before = max((p for p in positions if p < boundary - 1e-9), default=None)
        after = min((p for p in positions if p >= boundary - 1e-9), default=None)
        if before is None or after is None:
            return False
        limits = []
        for result, local in ((left, left.section.length_mm), (right, 0.0)):
            zones = result.constructability.zones
            limits.extend(zone.selected_spacing_mm for zone in zones
                          if zone.selected_spacing_mm is not None
                          and zone.start_mm - 1e-9 <= local <= zone.end_mm + 1e-9)
        if not limits or after - before > min(limits) + 1e-9:
            return False
    return True


def coordinate_parent_line(results, parent_groups, option, engine):
    previous = getattr(engine, "_parent_candidate_cache", None)
    started = time.perf_counter() if _indexed_coordination(engine) else None
    engine._parent_candidate_cache = OrderedDict()
    try:
        return _coordinate_parent_line(results, parent_groups, option, engine)
    finally:
        if started is not None:
            timings = engine.__dict__.setdefault("_indexed_coordination_phase_seconds", {})
            timings["parent_coordination"] = timings.get("parent_coordination", 0.0) + time.perf_counter() - started
        if previous is None:
            del engine._parent_candidate_cache
        else:
            engine._parent_candidate_cache = previous


def _coordinate_parent_line(results, parent_groups, option, engine):
    """Coordinate a physical beam line, keeping every multi-section parent atomic."""
    if engine.ranking_mode != "style":
        raise DetailingError("PARENT_COORDINATION_REQUIRES_DRAWING_STYLE")
    budget = [MAX_COORDINATION_SEARCH_STATES]
    inputs = [result.input_data for result in results]
    if any(data is None for data in inputs):
        raise DetailingError("PARENT_COORDINATION_INPUT_MISSING")
    diameters = set(engine.project.ordered_longitudinal_diameters_for(inputs[0].section))
    for data in inputs[1:]:
        diameters.intersection_update(engine.project.ordered_longitudinal_diameters_for(data.section))
    memo = {}
    graph, terminals, roots = {}, set(), []
    global_best_rank = None
    incumbent = [None, None]
    revised = _revised_coordination(engine)
    request_cache = OrderedDict()
    max_nonhoop = sum(sum(not field.startswith("stirrup_") for field in
                           engine.offline_style_library.applicable_fields(data, engine)) for data in inputs)
    catalog_states = [0]
    parent_rejections = set()
    def minimum_area_bound(data):
        section = data.section
        if section.clear_cover_to_stirrup_outer_mm is None:
            depth = section.depth_mm - section.etabs_longitudinal_cover_mm
        else:
            db = max(engine.project.ordered_longitudinal_diameters_for(section))
            hoop = max(engine.project.ordered_stirrup_diameters())
            layers = engine.project.max_main_bar_layers
            gap = engine.code.minimum_clear_spacing(db, section.aggregate_mm)
            depth = section.depth_mm - section.clear_cover_to_stirrup_outer_mm - hoop - (layers - .5) * db - (layers - 1) * gap
        return engine.code.minimum_longitudinal_area(section.width_mm, max(0.0, depth), section.fc_mpa, section.fy_mpa)
    area_minima = tuple(minimum_area_bound(data) for data in inputs)
    def consume_catalog():
        catalog_states[0] += 1
        if catalog_states[0] > 50000:
            raise _CoordinationSearchLimit("PARENT_CATALOG_SEARCH_LIMIT: max_states=50000")

    def solve_parent(group, first_diameter, previous, left_exact):
        data_group = [inputs[i] for i in group]
        secondary = all(data.section.is_secondary for data in data_group)
        if any(data.section.is_secondary != secondary for data in data_group):
            return
        hoop_diameters = engine.project.ordered_stirrup_diameters()
        for hoop_diameter in hoop_diameters:
            if any(hoop_diameter < engine.project.minimum_stirrup_diameter_for(data.section) for data in data_group):
                continue
            max_count = min(max(engine._capacity_per_layer(data.section, hoop_diameter, db)
                                for db in diameters) for data in data_group)
            minimum_legs = max(2, *(engine.code.minimum_legs(data.section, demand.name)
                                    for data in data_group for demand in data.regions))
            minimum_legs += minimum_legs % 2
            preferred = max(engine.project.preferred_stirrup_legs(data.section) for data in data_group)
            leg_values = tuple(sorted(range(minimum_legs, max_count + 1, 2),
                                      key=lambda value: (value != preferred, value)))
            spacings = [set(engine.project.allowed_spacings_for(data.section, data.regions[region].name))
                        for region in range(3) for data in data_group]
            common_spacing = set.intersection(*spacings)
            if secondary:
                secondary_targets = set()
                for legs in leg_values:
                    for spacing in sorted(common_spacing, reverse=True):
                        if all(tuple(_parent_hoop_options(data, engine, hoop_diameter,
                                                         secondary_target=(spacing, legs))) for data in data_group):
                            secondary_targets.add((spacing, legs))
                            break
                for data in data_group:
                    applicable = engine.offline_style_library.applicable_fields(data, engine)
                    for state in _style_hoop_states(data, engine, applicable):
                        specs = {(hoop.diameter_mm, hoop.spacing_mm, hoop.legs) for hoop in state}
                        if len(specs) == 1:
                            diameter, spacing, legs = next(iter(specs))
                            if diameter == hoop_diameter:
                                secondary_targets.add((spacing, legs))
                targets = ((target, None) for target in sorted(secondary_targets, key=lambda item: (item[1], -item[0])))
            else:
                profiles = set()
                for data in data_group:
                    applicable = engine.offline_style_library.applicable_fields(data, engine)
                    for state in _style_hoop_states(data, engine, applicable):
                        if all(hoop.diameter_mm == hoop_diameter for hoop in state):
                            profiles.add(tuple((hoop.spacing_mm, hoop.legs) for hoop in state))
                for legs in leg_values:
                    profile = []
                    for region in range(3):
                        allowed = set.intersection(*(set(engine.project.allowed_spacings_for(data.section, data.regions[region].name))
                                                     for data in data_group))
                        valid_spacings = []
                        for spacing in sorted(allowed, reverse=True):
                            if all(_parent_hoop_potential(data, engine, data.regions[region],
                                    _style_hoop(data.section, data.regions[region],
                                                {"diameter_mm": hoop_diameter, "spacing_mm": spacing, "legs": legs}, engine))
                                   for data in data_group):
                                valid_spacings.append(spacing)
                        if not valid_spacings:
                            break
                        profile.append((valid_spacings[0], legs))
                    if len(profile) == 3:
                        profiles.add(tuple(profile))
                targets = ((None, profile) for profile in sorted(profiles))
            for target, primary_target in targets:
                consume_catalog()
                hoop_sets = [tuple(_parent_hoop_options(data, engine, hoop_diameter,
                              secondary_target=target, primary_target=primary_target)) for data in data_group]
                if any(not states for states in hoop_sets):
                    continue
                proposed = _parent_bottom_proposals(data_group, engine, hoop_sets)
                from src.design.equivalent_steel import _parent_zone_lengths
                hoop_lower_bound = 0.0
                for data, states in zip(data_group, hoop_sets):
                    cover = data.section.clear_cover_to_stirrup_outer_mm
                    if cover is None:
                        continue
                    lengths = _parent_zone_lengths(data.parent_context, data.section.length_mm)
                    if lengths is None:
                        continue
                    for name, hoop in zip(("left", "middle", "right"), states[0]):
                        b = data.section.width_mm - 2 * cover - hoop.diameter_mm
                        h = data.section.depth_mm - 2 * cover - hoop.diameter_mm
                        if b > 0 and h > 0:
                            hoop_lower_bound += (lengths[name] / hoop.spacing_mm *
                                math.pi * hoop.diameter_mm ** 2 / 4 * (2 * b + hoop.legs * h))
                for bottom in proposed:
                        consume_catalog()
                        bottom_area = sum(count * math.pi * diameter * diameter / 4 for count, diameter, _ in bottom)
                        group_set = set(group)
                        demand_lower_bound = sum(data.section.length_mm * (
                            (max(data.regions[0].as_top_total_mm2, area_minima[i]) +
                             2 * max(data.regions[1].as_top_total_mm2, area_minima[i]) +
                             max(data.regions[2].as_top_total_mm2, area_minima[i])) / 4 +
                            (0 if i in group_set else max(area_minima[i], *(region.as_bottom_total_mm2 for region in data.regions))))
                            for i, data in enumerate(inputs))
                        if (global_best_rank is not None and global_best_rank[0] == -max_nonhoop
                                and demand_lower_bound + hoop_lower_bound + bottom_area * sum(data.section.length_mm for data in data_group) > global_best_rank[1]):
                            continue
                        _consume_search_budget(budget)
                        pool_cache = {}
                        def run(index, incoming, selected):
                            if index == len(group):
                                if not _parent_interface_hoops_valid(selected):
                                    parent_rejections.add("PARENT_INTERFACE_HOOP_SPACING_FAILED")
                                    return
                                if selected and selected[0].section.is_smf:
                                    from .constructability import ParentLongitudinalRun, parent_longitudinal_checks
                                    check_inputs = tuple(ParentLongitudinalRun(
                                        result.beam_name, result.section.width_mm, result.section.fc_mpa,
                                        result.section.fy_mpa,
                                        result.regions[0].top, result.regions[0].bottom,
                                        result.regions[1].top, result.regions[1].bottom,
                                        result.regions[2].top, result.regions[2].bottom,
                                    ) for result in selected)
                                    checks = parent_longitudinal_checks(runs=check_inputs)
                                    failed = {check.status for check in checks if check.status not in {"PASS", "INFO", "WARN"}}
                                    if failed:
                                        parent_rejections.update(failed)
                                        return
                                    selected = tuple(replace(result, smf_checks=(*result.smf_checks,
                                        *(check for check in checks if check.source == result.beam_name)))
                                        for result in selected)
                                yield tuple(selected)
                                return
                            data = data_group[index]
                            exact = left_exact if index == 0 else True
                            key = (index, tuple(row[1] for row in incoming) if incoming else None)
                            if revised:
                                for hoops in hoop_sets[index]:
                                    request_key = (group[index], first_diameter, key[1], exact,
                                                   tuple(bottom), _hoop_signature(hoops))
                                    cached = request_cache.get(request_key)
                                    pending = []
                                    source = cached if cached is not None else span_candidates(data, engine, first_diameter=first_diameter,
                                            left_rows=incoming, support_exact=exact, search_budget=budget,
                                            parent_bottom=bottom, parent_hoops=hoops)
                                    for candidate in source:
                                        if cached is None:
                                            pending.append(candidate)
                                        blockers = _parent_candidate_blockers(candidate)
                                        if blockers:
                                            parent_rejections.update(blockers)
                                            continue
                                        following = signature(candidate.regions[-1].top)
                                        yield from run(index + 1, following, (*selected, candidate))
                                    if cached is None:
                                        request_cache[request_key] = tuple(pending)
                                        if len(request_cache) > 4096:
                                            request_cache.popitem(last=False)
                                return
                            if key not in pool_cache:
                                raw = tuple(candidate for hoops in hoop_sets[index]
                                    for candidate in span_candidates(data, engine, first_diameter=first_diameter,
                                        left_rows=incoming, support_exact=exact, search_budget=budget,
                                        parent_bottom=bottom, parent_hoops=hoops))
                                grouped = {}
                                for candidate in raw:
                                    if not _parent_candidate_eligible(candidate):
                                        parent_rejections.update(_parent_candidate_blockers(candidate))
                                        continue
                                    grid = tuple((h.location_mm, h.zone) for h in candidate.constructability.hoop_positions)
                                    key2 = (tuple(row.diameter_mm for row in candidate.regions[0].top.rows),
                                            tuple(row.diameter_mm for row in candidate.regions[-1].top.rows), grid)
                                    rank = engine.offline_style_library.rank_key(data, candidate, engine)
                                    if key2 not in grouped or rank < grouped[key2][0]:
                                        grouped[key2] = (rank, candidate)
                                pool_cache[key] = tuple(item[1] for item in grouped.values())
                            for candidate in pool_cache[key]:
                                    if not _parent_candidate_eligible(candidate):
                                        continue
                                    following = signature(candidate.regions[-1].top)
                                    yield from run(index + 1, following, (*selected, candidate))
                        best_by_endpoint = {}
                        for path in run(0, previous, ()):
                            endpoint = ((tuple(row.diameter_mm for row in path[0].regions[0].top.rows),
                                         tuple(row.diameter_mm for row in path[-1].regions[-1].top.rows))
                                        if revised else tuple(row.diameter_mm for row in path[-1].regions[-1].top.rows))
                            rank = _style_path_rank(path, engine)
                            if _indexed_coordination(engine):
                                yield path
                                continue
                            if endpoint not in best_by_endpoint or rank < best_by_endpoint[endpoint][0]:
                                best_by_endpoint[endpoint] = (rank, path)
                                if revised:
                                    yield path
                        if best_by_endpoint and not revised:
                            for _rank, path in best_by_endpoint.values():
                                yield path

    def solve(index, previous, first_diameter, prefix=()):
        nonlocal global_best_rank
        if index == len(parent_groups):
            if _indexed_coordination(engine):
                terminals.add((index, tuple(row[1] for row in previous) if previous else None, first_diameter))
            if revised:
                rank = _style_path_rank(prefix, engine)
                if incumbent[1] is None or rank < incumbent[1]:
                    incumbent[:] = [prefix, rank]
                    global_best_rank = rank
            return ()
        state = (index, tuple(row[1] for row in previous) if previous else None, first_diameter)
        if state in memo:
            if revised and memo[state] is not None:
                path = (*prefix, *memo[state])
                rank = _style_path_rank(path, engine)
                if incumbent[1] is None or rank < incumbent[1]:
                    incumbent[:] = [path, rank]
                    global_best_rank = rank
            return memo[state]
        group = parent_groups[index]
        prior_group = parent_groups[index - 1] if index else None
        from .detailing import ContinuityOption
        exact = bool(prior_group) and (option is ContinuityOption.OPTION_1_STRICT_EQUAL or
                 inputs[prior_group[-1]].section.is_secondary or inputs[group[0]].section.is_secondary)
        if len(group) > 1:
            choices = solve_parent(group, first_diameter, previous, exact)
        else:
            idx = group[0]
            def normal_choices():
                request_key = (idx, first_diameter, tuple(row[1] for row in previous) if previous else None, exact)
                cached = request_cache.get(request_key) if revised else None
                pending = []
                seen = set()
                source = cached if cached is not None else span_candidates(inputs[idx], engine,
                    first_diameter=first_diameter, left_rows=previous, support_exact=exact,
                    search_budget=budget, coordination_pool=True)
                for candidate in source:
                    if _indexed_coordination(engine):
                        stable = engine.offline_style_library.stable_signature(candidate)
                        if stable in seen:
                            continue
                        seen.add(stable)
                    if cached is None:
                        pending.append(candidate)
                    blockers = _parent_candidate_blockers(candidate)
                    if blockers:
                        parent_rejections.update(blockers)
                    else:
                        yield (candidate,)
                if revised and cached is None:
                    request_cache[request_key] = tuple(pending)
                    if len(request_cache) > 4096:
                        request_cache.popitem(last=False)
            choices = normal_choices()
        best = None
        best_rank = None
        best_by_future = {}
        for choice in choices:
            following = signature(choice[-1].regions[-1].top)
            if _indexed_coordination(engine):
                future = tuple(row[1] for row in following)
                next_state = (index + 1, future, first_diameter)
                graph.setdefault(state, []).append((choice, next_state))
                if not _retain_future_candidate(best_by_future, future, choice, engine):
                    continue
            tail = solve(index + 1, following, first_diameter, (*prefix, *choice))
            if tail is None:
                continue
            path = (*choice, *tail)
            rank = _style_path_rank(path, engine)
            if best_rank is None or rank < best_rank:
                best, best_rank = path, rank
                if index == 0 and (global_best_rank is None or rank < global_best_rank):
                    global_best_rank = rank
        memo[state] = best
        return best

    best_path, best_rank = None, None
    diameter_order = engine.project.ordered_longitudinal_diameters_for(inputs[0].section)
    if all(data.section.is_secondary for data in inputs):
        diameter_order = tuple(sorted(diameter_order,
                               key=lambda value: (value > engine.project.sb_top_preferred_max_diameter_mm, -value)))
    termination = None
    try:
        for diameter in diameter_order:
            if diameter not in diameters:
                continue
            if _indexed_coordination(engine):
                roots.append((0, None, diameter))
            path = solve(0, None, diameter)
            if path is not None:
                rank = _style_path_rank(path, engine)
                if best_rank is None or rank < best_rank:
                    best_path, best_rank = path, rank
    except _CoordinationSearchLimit as exc:
        if not revised or incumbent[0] is None:
            raise
        best_path, best_rank = incumbent
        termination = str(exc).split(":", 1)[0]
    if revised and incumbent[0] is not None and (best_rank is None or incumbent[1] < best_rank):
        best_path, best_rank = incumbent
    if best_path is not None and _indexed_coordination(engine):
        best_path, best_rank = _checked_graph_path(graph, terminals, roots, engine)
        if best_path is None:
            raise DetailingError("FINAL_ROW_NO_EXPLORED_COMPLETE_PATH")
    if best_path is not None:
        return tuple(replace(result, continuity_option=option,
                             coordination_status=("FEASIBLE_OPTIMIZATION_INCOMPLETE" if termination else "PARENT_COORDINATION_VERIFIED"),
                             coordination_audit=(*result.coordination_audit,
                                 {"strategy": "PARENT_R7_STRUCTURED_PROPOSALS", "search_complete": termination is None,
                                  **({"termination_stage": "PARENT_CATALOG" if termination == "PARENT_CATALOG_SEARCH_LIMIT" else "LINE_COORDINATION",
                                      "termination_reason": termination, "budget_limit": MAX_COORDINATION_SEARCH_STATES,
                                      "attempted_over_budget": budget[0] < 0} if termination else {}),
                                  "states_used": (min(MAX_COORDINATION_SEARCH_STATES, MAX_COORDINATION_SEARCH_STATES - budget[0])
                                                  if revised else MAX_COORDINATION_SEARCH_STATES - budget[0]),
                                  "catalog_states_examined": catalog_states[0],
                                  "search_scope": "R7_STRUCTURED_PROPOSAL_POOL",
                                  "catalog_exhaustive": False,
                                  "line_equivalent_steel_mm3": best_rank[1]})) for result in best_path)
    reason = "; ".join(sorted(parent_rejections)) or "NO_FEASIBLE_STRUCTURED_PROPOSAL"
    raise DetailingError(f"PARENT_PROPOSAL_SEARCH_INCOMPLETE: {reason}")


def _legacy_fallback_audit(results, engine, reason):
    library = engine.offline_style_library
    output = []
    applicable_rule_ids = {
        rule["rule_id"]
        for result in results
        if result.input_data is not None
        for rules in library.applicable_fields(result.input_data, engine).values()
        for rule in rules
    }
    matched_rule_ids = {
        match.rule_id
        for result in results
        if result.input_data is not None
        for match in library.matched_fields(result.input_data, result, engine)
    }
    for result in results:
        audit = library.audit(
            result.input_data,
            result,
            engine,
            fallback_reason=reason,
            unmatched_applicable_pattern_rule_ids=applicable_rule_ids - matched_rule_ids,
        )
        output.append(
            replace(
                result,
                coordination_audit=(*result.coordination_audit, audit),
            )
        )
    return tuple(output)


def _checked_graph_path(graph, terminals, roots, engine):
    started = time.perf_counter()
    try:
        return _checked_graph_path_inner(graph, terminals, roots, engine)
    finally:
        timings = engine.__dict__.setdefault("_indexed_coordination_phase_seconds", {})
        timings["final_check"] = timings.get("final_check", 0.0) + time.perf_counter() - started


def _checked_graph_path_inner(graph, terminals, roots, engine):
    """Retry only paths represented by already explored DP edges.

    Each edge contains a checked span or a fully checked atomic parent. A
    failed final layout removes that physical candidate; the graph is then
    re-ranked without generating candidates or spending another search budget.
    """
    rejected = set()
    finalized = {}
    failures = []
    library = engine.offline_style_library

    def identity(result):
        return (id(result.input_data), library.stable_signature(result))

    while True:
        memo = {}
        def best(state):
            if state in terminals:
                return ()
            if state in memo:
                return memo[state]
            winner = None
            winner_rank = None
            for choice, following in graph.get(state, ()):
                if any(identity(item) in rejected for item in choice):
                    continue
                tail = best(following)
                if tail is None:
                    continue
                path = (*choice, *tail)
                rank = _style_path_rank(path, engine)
                if winner_rank is None or rank < winner_rank:
                    winner, winner_rank = path, rank
            memo[state] = winner
            return winner

        path = None
        rank = None
        for root in roots:
            candidate = best(root)
            if candidate is None:
                continue
            candidate_rank = _style_path_rank(candidate, engine)
            if rank is None or candidate_rank < rank:
                path, rank = candidate, candidate_rank
        if path is None:
            if failures:
                raise DetailingError("FINAL_ROW_ALL_EXPLORED_PATHS_FAILED: " + "; ".join(failures))
            return None, None
        output = []
        for result in path:
            key = identity(result)
            try:
                if key not in finalized:
                    finalized[key] = finalize_rows(result, engine)
                # Keep path-specific continuity and parent checks/audit.
                checked = finalized[key]
                checked = replace(checked, continuity_option=result.continuity_option,
                    coordination_status=result.coordination_status,
                    coordination_audit=result.coordination_audit,
                    smf_checks=(*checked.smf_checks,
                        *(check for check in result.smf_checks if check not in checked.smf_checks)))
                output.append(checked)
            except DetailingError as exc:
                rejected.add(key)
                failures.append(str(exc))
                break
        else:
            return tuple(output), rank


def _style_coordinate_segment(results, option, engine):
    from .detailing import ContinuityOption

    if not results:
        return ()
    if any(result.input_data is None for result in results):
        return _legacy_coordinate_segment(results, option, engine.legacy_ranking_engine())
    library = engine.offline_style_library
    revised = _revised_coordination(engine)
    precomputed_pools = {
        index: pool
        for index, result in enumerate(results)
        if not revised and (pool := _take_style_candidate_pool(engine, result)) is not None
    }
    has_applicable = any(library.applicable_fields(result.input_data, engine) for result in results)
    if not has_applicable and _indexed_coordination(engine):
        fallback = _indexed_legacy_coordinate_segment(results, option, engine)
        return _legacy_fallback_audit(fallback, engine, "NO_APPLICABLE_PATTERN")
    if not has_applicable:
        fallback = _legacy_coordinate_segment(
            results,
            option,
            engine if revised else engine.legacy_ranking_engine(),
            precomputed_pools=None if revised else precomputed_pools,
            constrained_pool=revised,
        )
        return _legacy_fallback_audit(fallback, engine, "NO_APPLICABLE_PATTERN")
    common = set(engine.project.ordered_longitudinal_diameters_for(results[0].section))
    for result in results[1:]:
        common.intersection_update(engine.project.ordered_longitudinal_diameters_for(result.section))
    diameters = engine.project.ordered_longitudinal_diameters_for(results[0].section)
    if all(result.section.is_secondary for result in results):
        diameters = tuple(sorted(
            diameters,
            key=lambda value: (value > engine.project.sb_top_preferred_max_diameter_mm, -value),
        ))
    search_budget = [MAX_COORDINATION_SEARCH_STATES]
    candidate_pools = {}
    memo = {}
    graph, terminals, roots = {}, set(), []
    incumbent = [None, None]

    def support_state(rows):
        if rows is None:
            return None
        return tuple(row[1] for row in rows)

    def get_candidate_pool(index, diameter, previous=None, exact=False):
        pool_key = (index, diameter, support_state(previous), exact) if revised else (index, diameter)
        if pool_key in candidate_pools:
            yield from candidate_pools[pool_key]
            return
        source = results[index].input_data
        if revised:
            pending = []
            seen = set()
            for candidate in span_candidates(source, engine, first_diameter=diameter,
                    left_rows=previous, support_exact=exact, search_budget=search_budget,
                    coordination_pool=True):
                if _indexed_coordination(engine):
                    stable = library.stable_signature(candidate)
                    if stable in seen:
                        continue
                    seen.add(stable)
                pending.append(candidate)
                yield candidate
            candidate_pools[pool_key] = tuple(pending)
            if len(candidate_pools) > 4096:
                del candidate_pools[next(iter(candidate_pools))]
            return
        if index in precomputed_pools:
            raw = tuple(
                candidate for candidate in precomputed_pools[index]
                if signature(candidate.regions[0].top)[0][1] == diameter
            )
            for _candidate in raw:
                _consume_search_budget(search_budget)
        else:
            raw = tuple(span_candidates(
                source,
                engine,
                first_diameter=diameter,
                search_budget=search_budget,
            ))
        grouped = {}
        for candidate in raw:
            left_state = tuple(row[1] for row in signature(candidate.regions[0].top))
            right_state = tuple(row[1] for row in signature(candidate.regions[-1].top))
            grouped.setdefault((left_state, right_state), candidate)
        candidate_pools[pool_key] = tuple(grouped.values())
        yield from candidate_pools[pool_key]

    def solve(index, previous, diameter, prefix=()):
        _consume_search_budget(search_budget)
        if index == len(results):
            if _indexed_coordination(engine):
                terminals.add((index, support_state(previous), diameter))
            if revised:
                rank = _style_path_rank(prefix, engine)
                if incumbent[1] is None or rank < incumbent[1]:
                    incumbent[:] = [prefix, rank]
            return ()
        key = (index, support_state(previous), diameter)
        if key in memo:
            if revised and memo[key] is not None:
                path = (*prefix, *memo[key])
                rank = _style_path_rank(path, engine)
                if incumbent[1] is None or rank < incumbent[1]:
                    incumbent[:] = [path, rank]
            return memo[key]
        exact = index > 0 and (
            option is ContinuityOption.OPTION_1_STRICT_EQUAL
            or results[index - 1].section.is_secondary
            or results[index].section.is_secondary
        )
        best_suffix = None
        best_rank = None
        best_by_future = {}
        for candidate in get_candidate_pool(index, diameter, previous, exact):
            left = signature(candidate.regions[0].top)
            if previous is not None and not _support_compatible(left, previous, exact=exact):
                continue
            right = signature(candidate.regions[-1].top)
            coordinated = replace(
                candidate,
                continuity_option=option,
                coordination_audit=(
                    *candidate.coordination_audit,
                    {
                        "shared_support": index,
                        "incoming_row_diameters": list(support_state(previous) or ()),
                        "exact": exact,
                        "first_row_count_independent_within_span": True,
                        "profile": candidate.detailing_profile.value,
                    },
                ),
            )
            if _indexed_coordination(engine):
                future = support_state(right)
                graph.setdefault(key, []).append(((coordinated,), (index + 1, future, diameter)))
                if not _retain_future_candidate(best_by_future, future, (coordinated,), engine):
                    continue
            suffix = solve(index + 1, right, diameter, (*prefix, coordinated))
            if suffix is None:
                continue
            path = (coordinated, *suffix)
            rank = _style_path_rank(path, engine)
            if best_rank is None or rank < best_rank:
                best_suffix, best_rank = path, rank
        memo[key] = best_suffix
        return best_suffix

    best_path = None
    best_rank = None
    termination = None
    try:
        for diameter in diameters:
            if diameter not in common:
                continue
            if _indexed_coordination(engine):
                roots.append((0, None, diameter))
            candidate = solve(0, None, diameter)
            if candidate is None:
                continue
            rank = _style_path_rank(candidate, engine)
            if best_rank is None or rank < best_rank:
                best_path, best_rank = candidate, rank
    except _CoordinationSearchLimit as exc:
        if not revised or incumbent[0] is None:
            raise
        best_path, best_rank = incumbent
        termination = str(exc).split(":", 1)[0]
    if revised and incumbent[0] is not None and (best_rank is None or incumbent[1] < best_rank):
        best_path, best_rank = incumbent

    if best_path is None or best_rank is None or (not revised and not any(
        library.matched_fields(result.input_data, result, engine)
        for result in best_path
    )):
        reason = "NO_FEASIBLE_SUPPORTED_PATTERN" if has_applicable else "NO_APPLICABLE_PATTERN"
        if _indexed_coordination(engine):
            raise DetailingError("ROW_SEGMENT_NO_EXPLORED_COMPLETE_PATH: " + reason)
        fallback = _legacy_coordinate_segment(results, option, engine.legacy_ranking_engine())
        return _legacy_fallback_audit(fallback, engine, reason)

    if _indexed_coordination(engine):
        best_path, best_rank = _checked_graph_path(graph, terminals, roots, engine)
        if best_path is None:
            raise DetailingError("FINAL_ROW_NO_EXPLORED_COMPLETE_PATH")
    audited = []
    for result in best_path:
        if not _indexed_coordination(engine):
            result = finalize_rows(result, engine)
        applicable = library.applicable_fields(result.input_data, engine)
        matched = library.matched_fields(result.input_data, result, engine)
        applicable_rule_ids = {
            rule["rule_id"]
            for field_rules in applicable.values()
            for rule in field_rules
        }
        matched_rule_ids = {item.rule_id for item in matched}
        fallback_reason = (
            "NO_APPLICABLE_PATTERN"
            if not applicable
            else "NO_FEASIBLE_SUPPORTED_PATTERN"
            if not matched
            else None
        )
        audit = library.audit(
            result.input_data,
            result,
            engine,
            fallback_reason=fallback_reason,
            unmatched_applicable_pattern_rule_ids=applicable_rule_ids - matched_rule_ids,
        )
        prior_audits = tuple(
            item
            for item in result.coordination_audit
            if not (
                isinstance(item, dict)
                and item.get("strategy") == "DRAWING_STYLE_SUPPORTED_FIELDS_THEN_EQUIVALENT_STEEL"
            )
        )
        audited.append(replace(
            result,
            coordination_status=("FEASIBLE_OPTIMIZATION_INCOMPLETE" if termination else result.coordination_status),
            coordination_audit=(*prior_audits, audit, *(
                ({"search_complete": termination is None, "termination_stage": "LINE_COORDINATION" if termination else None,
                  "termination_reason": termination, "attempted_over_budget": search_budget[0] < 0,
                  "budget_limit": MAX_COORDINATION_SEARCH_STATES,
                  "states_used": min(MAX_COORDINATION_SEARCH_STATES, MAX_COORDINATION_SEARCH_STATES - search_budget[0])},) if revised else ())),
        ))
    return tuple(audited)


def coordinate_segment(results, option, engine):
    started = time.perf_counter() if _indexed_coordination(engine) else None
    try:
        if engine.ranking_mode == "style":
            return _style_coordinate_segment(results, option, engine)
        return _legacy_coordinate_segment(results, option, engine)
    finally:
        if started is not None:
            timings = engine.__dict__.setdefault("_indexed_coordination_phase_seconds", {})
            timings["segment_coordination"] = timings.get("segment_coordination", 0.0) + time.perf_counter() - started
