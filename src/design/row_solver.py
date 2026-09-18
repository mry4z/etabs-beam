"""Finite physical-row solving with final-geometry transverse feedback."""
from dataclasses import replace
import heapq
import math
from .detailing import (
    BeamDetailingProfile, DetailingError, RegionDetailing, ReinforcementMode,
    _refresh_result_physical_layout,
)
from .row_candidates import candidates, signature


# A single beam line can have a finite but very large support-state search
# when no common physical-row layout exists.  Keep that failure auditable and
# bounded so one pathological line cannot hold a full-model export forever.
MAX_COORDINATION_SEARCH_STATES = 5_000


class _CoordinationSearchLimit(DetailingError):
    """Internal fail-closed signal for an exhausted line-search budget."""


def _consume_search_budget(budget):
    if budget is None:
        return
    budget[0] -= 1
    if budget[0] < 0:
        raise _CoordinationSearchLimit(
            f"ROW_SEGMENT_SEARCH_LIMIT: max_states={MAX_COORDINATION_SEARCH_STATES}"
        )


def _combinations(groups, budget=None):
    if any(not group for group in groups):
        return
    start = (0,) * len(groups)
    queue, seen = [(0, start)], {start}
    while queue:
        _consume_search_budget(budget)
        _, indices = heapq.heappop(queue)
        yield tuple(group[index] for group, index in zip(groups, indices))
        for axis, group in enumerate(groups):
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
            hoops = tuple(engine._select_stirrup(data.section, demand, db) for demand in data.regions)
            return tuple(engine._with_legs(h, max(item.legs for item in hoops)) for h in hoops)
        except DetailingError as error:
            if "NO_ALLOWED_SPACING" not in str(error):
                raise
            failures.append(error)
    if failures:
        raise failures[0]
    raise DetailingError("EMPTY_LONGITUDINAL_CATALOGUE")


def _fit_hoops(result, engine):
    review = engine._calculate_capacity_review(result.section, result.regions, result.seismic_context)
    demands = engine._apply_capacity_shear_demands(
        result.input_data.regions, result.input_data.regions, review, result.input_data)
    selected = []
    for region, demand in zip(result.regions, demands):
        choices = []
        db = engine._final_main_db(region, result.section)
        depth = min(region.top.effective_depth_mm, region.bottom.effective_depth_mm)
        minimum = engine.code.minimum_transverse_area_per_spacing(result.section)
        combo = max(minimum, demand.transverse_required_av_over_s_mm2_per_mm) + 2*demand.at_over_s_mm2_per_mm
        for rank, diameter in enumerate(engine.project.ordered_stirrup_diameters()):
            if diameter < region.stirrup.diameter_mm:
                continue
            area = engine.project.catalog.area(diameter)
            maximum, reason, limits, high = engine.code.maximum_stirrup_spacing(
                result.section, demand, diameter, db, engine.project.smf_end_max_spacing_mm,
                effective_depth_mm=(result.section.depth_mm if result.section.is_smf else depth))
            for spacing in engine.project.allowed_spacings_for(result.section, region.name):
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
        if not choices:
            raise DetailingError("FINAL_ROWS_NO_FEASIBLE_HOOP")
        selected.append(engine._preferred_stirrup(result.section, choices))
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


def span_candidates(data, engine, *, first_diameter=None, left_rows=None,
                    right_rows=None, support_exact=False, search_budget=None):
    if data.section.detailing_profile is BeamDetailingProfile.UNRESOLVED:
        raise DetailingError("DETAILING_PROFILE_REVIEW_REQUIRED")
    pending = [_initial_hoops(data, engine)]
    seen, reasons = set(), set()
    while pending:
        hoops = pending.pop(0)
        state = _hoop_signature(hoops)
        if state in seen:
            continue
        seen.add(state)
        for mode in (ReinforcementMode.NORMAL, ReinforcementMode.FACE_TORSION):
            limit = engine.project.max_main_bar_layers if mode is ReinforcementMode.NORMAL else None
            top_required = [r.as_top_total_mm2 if mode is ReinforcementMode.NORMAL else r.as_flex_top_mm2 for r in data.regions]
            bottom_required = max(r.as_bottom_total_mm2 if mode is ReinforcementMode.NORMAL else r.as_flex_bottom_mm2 for r in data.regions)
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
                for left, middle, right, bottom in _combinations((*upper, lower), search_budget):
                    support_key = (signature(left), signature(right))
                    if support_key in accepted_supports:
                        continue
                    regions = tuple(RegionDetailing(d.name, d,
                        engine.code.minimum_longitudinal_area(data.section.width_mm,
                            min(top.effective_depth_mm, bottom.effective_depth_mm), data.section.fc_mpa, data.section.fy_mpa),
                        d.as_flex_top_mm2, d.as_flex_bottom_mm2, hoop, top, bottom)
                        for d, hoop, top in zip(data.regions, hoops, (left, middle, right)))
                    try:
                        result = engine._build_row_result(data, mode, regions)
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
            if valid_mode:
                break


def design_span(data, engine):
    result = next(span_candidates(data, engine), None)
    if result is None:
        raise DetailingError(f"{data.section.name}: ROW_CATALOG_NO_FEASIBLE_LAYOUT")
    return result


def finalize_rows(result, engine):
    result = _refresh_result_physical_layout(result, engine)
    for region in result.regions:
        for face in (region.top, region.bottom):
            if face.rows[0].count < region.stirrup.legs:
                raise DetailingError(f"{result.beam_name}/{region.name}: FIRST_ROW_HOOP_SUPPORT_FAILED")
            minimum = engine.code.minimum_longitudinal_area(
                result.section.width_mm, face.effective_depth_mm, result.section.fc_mpa, result.section.fy_mpa)
            if face.provided_area_mm2 + 1e-9 < max(minimum, face.required_area_mm2):
                raise DetailingError(f"{result.beam_name}/{region.name}: FINAL_ROW_AREA_UNDERSIZED")
    result = engine.refresh_capacity_shear_after_layout_change(result)
    regions = engine._finalize_stirrup_controls(result.section, result.regions)
    if result.input_data is None:
        raise DetailingError("FINAL_ROW_INPUT_CONTEXT_MISSING")
    rebuilt = engine._build_row_result(result.input_data, result.mode, regions)
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


def coordinate_segment(results, option, engine):
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

    def solve(index, previous, diameter):
        if index == len(results):
            return ()
        key = (index, previous, diameter)
        if key in failures:
            return None
        exact = index > 0 and (option is ContinuityOption.OPTION_1_STRICT_EQUAL
            or results[index-1].section.is_secondary or results[index].section.is_secondary)
        tried_right = set()
        for candidate in span_candidates(
                results[index].input_data,
                engine,
                first_diameter=diameter,
                left_rows=previous if previous is not None else None,
                support_exact=exact,
                search_budget=search_budget,
        ):
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
