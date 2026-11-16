"""Bounded, interface-first recommendation search for drawing_v3."""

from __future__ import annotations

import heapq
import time
from dataclasses import dataclass, replace
from itertools import count
from typing import Any, Mapping, Sequence

from src.design.detailing import (
    BeamDetailingEngine,
    BeamDetailingInput,
    BeamDetailingProfile,
    BeamDetailingResult,
    ContinuityOption,
    DetailingError,
    FaceRebarDesign,
    LongitudinalDesign,
    RegionDetailing,
    ReinforcementMode,
)
from src.design.offline_style import (
    DRAWING_V3_POLICY_ID,
    DRAWING_V3_REVISION_ID,
    DRAWING_V3_SUPPORTED_REVISION_IDS,
    DRAWING_V3_SOLVER_REVISION_ID,
)


V3_LINE_SEARCH_SECONDS = 300.0
V3_LOCAL_STATE_LIMIT = 25_000
V3_COVERAGE_SOLVER_REVISION_ID = "coordination_v3_top10_coverage_v1"


@dataclass(frozen=True)
class V3LineRecommendationSet:
    status: str
    selected: tuple[BeamDetailingResult | None, ...]
    alternatives: tuple[tuple[BeamDetailingResult | None, ...], ...]
    errors: tuple[str | None, ...]
    audit: dict[str, Any]


@dataclass(frozen=True)
class _SpanRecord:
    index: int
    input_data: BeamDetailingInput
    regions: tuple[RegionDetailing, ...]
    face_rebar: FaceRebarDesign
    left_support: tuple[tuple[int, int], ...]
    right_support: tuple[tuple[int, int], ...]
    pb_row_diameters: tuple[int | None, ...]
    bottom_signature: tuple[tuple[int, int, str], ...]
    hoop_signature: tuple[tuple[int, int, int], ...]
    seed_hoop_signature: tuple[tuple[int, int, int], ...]
    preference_tier: tuple[int, int, int, int]
    heuristic: tuple[Any, ...]


@dataclass(frozen=True)
class _UnitOption:
    indices: tuple[int, ...]
    records: tuple[_SpanRecord, ...]
    left_support: tuple[tuple[int, int], ...]
    right_support: tuple[tuple[int, int], ...]
    pb_row_diameters: tuple[int | None, ...]
    preference_tier: tuple[int, int, int, int]
    heuristic: tuple[Any, ...]


def _merge_preference_stage_audit(result, prior_audit, *, candidate_limit, summary):
    audit = result.audit
    for key in (
        "complete_materializations",
        "partial_materialization_attempts",
        "complete_validation_calls",
        "complete_validation_passes",
        "partial_validation_calls",
        "partial_validation_passes",
        "span_result_builds",
        "local_combo_checks",
        "local_records_seen",
        "interface_state_count",
        "local_candidate_tuple_combinations",
        "local_interface_blocks_skipped_after_coverage",
        "local_interface_representatives",
        "parent_shared_state_count",
        "parent_interface_candidates_skipped_after_coverage",
        "parent_join_combinations",
        "parent_longitudinal_checks",
        "duplicate_physical_schemes_discarded",
    ):
        audit[key] = int(audit.get(key, 0)) + int(prior_audit.get(key, 0))
    earlier_rejections = prior_audit.get("materialization_rejections", ())
    current_rejections = audit.get("materialization_rejections", ())
    if earlier_rejections or current_rejections:
        audit["materialization_rejections"] = [*earlier_rejections, *current_rejections]
    audit["candidate_lookup_before"] = dict(prior_audit.get("candidate_lookup_before", {}))
    audit["candidate_limit"] = candidate_limit
    audit["materialization_budget"] = candidate_limit
    audit["truncated"] = bool(audit.get("truncated") or prior_audit.get("truncated"))
    audit["preference_tier_search"] = summary


def _attach_hard_check_cache_delta(result, engine, before):
    from .v3_hard_checks import v3_hard_check_cache_statistics

    after = v3_hard_check_cache_statistics(engine)
    result.audit["hard_check_region_cache"] = {
        name: (value if name in {"capacity", "entries"}
               else int(value) - int(before.get(name, 0)))
        for name, value in after.items()
    }
    return result


def _rows_signature(design: LongitudinalDesign) -> tuple[tuple[int, int], ...]:
    return tuple((int(row.count), int(row.diameter_mm)) for row in design.rows)


def _support_diameter_pattern(support):
    return tuple(int(diameter) for _count, diameter in support)


def _common_hoop_legs(actual_required_legs, seed_hoop_signature, *, research_seed_lower_bound):
    """Return common legs, optionally preserving the research seed as a floor."""
    required = tuple(int(value) for value in actual_required_legs)
    if not required:
        return 0
    seed_floor = (
        max((int(item[1]) for item in seed_hoop_signature), default=0)
        if research_seed_lower_bound else 0
    )
    return max((*required, seed_floor))


def _parent_join_hoop_signature(seed_signature, regions, *, use_actual_signature):
    if not use_actual_signature:
        return seed_signature
    return tuple(
        (
            int(region.stirrup.diameter_mm),
            int(region.stirrup.legs),
            int(region.stirrup.spacing_mm),
        )
        for region in regions
    )


def _terminal_parent_signature_evidence(candidate_index, aligned, records, parent_groups):
    records_by_index = {record.index: record for record in records}
    groups = []
    for group in parent_groups:
        if len(group) <= 1:
            continue
        span_items = []
        all_bottom = []
        all_hoops = []
        for index in group:
            result = aligned[index]
            record = records_by_index.get(index)
            if result is None:
                span_items.append({"input_index": index, "result_present": False})
                continue
            bottom_by_region = tuple(
                (region.name, _bar_signature(region.bottom))
                for region in result.regions
            )
            hoop_by_region = tuple(
                (
                    region.name,
                    int(region.stirrup.diameter_mm),
                    int(region.stirrup.legs),
                    int(region.stirrup.spacing_mm),
                )
                for region in result.regions
            )
            all_bottom.extend(signature for _name, signature in bottom_by_region)
            all_hoops.extend((diameter, legs, spacing) for _name, diameter, legs, spacing in hoop_by_region)
            span_items.append({
                "input_index": index,
                "result_present": True,
                "record_seed_hoop_signature": (
                    record.seed_hoop_signature if record is not None else None
                ),
                "record_parent_join_hoop_signature": (
                    record.hoop_signature if record is not None else None
                ),
                "actual_bottom_signature_by_region": bottom_by_region,
                "actual_hoop_signature_by_region": hoop_by_region,
            })

        def unique(values):
            output = []
            for value in values:
                if value not in output:
                    output.append(value)
            return tuple(output)

        groups.append({
            "input_indices": tuple(group),
            "spans": tuple(span_items),
            "distinct_actual_bottom_signatures": unique(all_bottom),
            "distinct_actual_hoop_signatures": unique(all_hoops),
        })
    return {"candidate_index": candidate_index, "parent_groups": tuple(groups)}


def _diameter_signature(design: LongitudinalDesign) -> tuple[tuple[int, str], ...]:
    return tuple((int(row.diameter_mm), str(row.role)) for row in design.rows)


def _bar_signature(design: LongitudinalDesign) -> tuple[tuple[int, int, str], ...]:
    return tuple((int(row.count), int(row.diameter_mm), str(row.role)) for row in design.rows)


def _span_record_storage_key(record):
    return (
        record.left_support,
        record.right_support,
        record.pb_row_diameters,
        tuple(_bar_signature(region.top) for region in record.regions),
        record.bottom_signature,
        record.hoop_signature,
    )


def _raw_local_tuple_key(left, middle, right, bottom, pb_vector, hoop_key):
    return (
        _rows_signature(left),
        _rows_signature(right),
        tuple(pb_vector),
        tuple(_bar_signature(design) for design in (left, middle, right)),
        _bar_signature(bottom),
        hoop_key,
    )


def _physical_line_fingerprint(results: Sequence[BeamDetailingResult | None]) -> tuple[Any, ...]:
    """Identify a returned line by its final physical detailing, not search seeds."""

    def longitudinal(design: LongitudinalDesign) -> tuple[Any, ...]:
        return (
            tuple(
                (int(row.count), int(row.diameter_mm), str(row.role))
                for row in design.rows
            ),
            tuple(float(value) for value in design.row_centers_mm),
            tuple(tuple(float(value) for value in row) for row in design.row_x_mm),
        )

    def side_reinforcement(face_rebar: FaceRebarDesign) -> tuple[Any, ...]:
        bars = face_rebar.bars_each_side
        bar_signature = None if bars is None else (
            int(bars.count),
            int(bars.diameter_mm),
            tuple(int(value) for value in (bars.layers or ())),
            str(bars.role),
        )
        return (str(face_rebar.mode.value), bar_signature)

    fingerprint = []
    for result in results:
        if result is None:
            fingerprint.append(None)
            continue
        section = result.section
        fingerprint.append((
            str(result.beam_name),
            str(section.name),
            float(section.width_mm),
            float(section.depth_mm),
            tuple((
                str(region.name),
                longitudinal(region.top),
                longitudinal(region.bottom),
                (
                    int(region.stirrup.diameter_mm),
                    int(region.stirrup.spacing_mm),
                    int(region.stirrup.legs),
                ),
            ) for region in result.regions),
            side_reinforcement(result.face_rebar),
        ))
    return tuple(fingerprint)


def _deduplicate_ranked_materialized(materialized, audit=None):
    """Keep the highest-ranked physical plan; never create replacement attempts."""
    materialized.sort(key=lambda item: item[0])
    unique = []
    seen = set()
    duplicate_count = 0
    for item in materialized:
        fingerprint = _physical_line_fingerprint(item[1])
        if fingerprint in seen:
            duplicate_count += 1
            continue
        seen.add(fingerprint)
        unique.append(item)
    if audit is not None:
        audit["duplicate_physical_schemes_discarded"] = (
            int(audit.get("duplicate_physical_schemes_discarded", 0)) + duplicate_count
        )
        audit["candidate_count"] = len(unique)
    return unique, duplicate_count


def _compress_axis(values: Sequence[LongitudinalDesign]) -> tuple[LongitudinalDesign, ...]:
    unique: dict[tuple[tuple[int, int, str], ...], LongitudinalDesign] = {}
    for design in values:
        unique.setdefault(_bar_signature(design), design)
    return tuple(unique.values())


def _candidate_axis(data, hoops, hoop, required, engine, *, face, rules,
                    support_alternatives, require_sb_mid_t12=False,
                    allowed_diameters=None):
    from .row_candidates import approved_pattern_candidates, candidates, signature

    base = candidates(
        data.section,
        tuple(hoops),
        required,
        engine,
        max_layers=engine.project.max_main_bar_layers,
        face=face,
        preserve_support_alternatives=support_alternatives,
    )
    patterned = approved_pattern_candidates(
        data.section,
        tuple(hoops),
        required,
        engine,
        rules,
        face=face,
    )
    seen = {signature(item) for item in base}
    output = list(base)
    output.extend(item for item in patterned if signature(item) not in seen)

    allowed = set(engine.project.ordered_longitudinal_diameters_for(data.section))
    if allowed_diameters is not None:
        allowed.intersection_update(allowed_diameters)
    supported = []
    for design in output:
        if any(row.role != "hanger" and row.diameter_mm not in allowed for row in design.rows):
            continue
        if data.section.is_secondary:
            design = engine._with_sb_hangers(design, data.section, hoop.diameter_mm, hoop.legs)
            if design is None:
                continue
            if face == "top" and require_sb_mid_t12:
                main_middle = sum(row.count for row in design.rows if row.role != "hanger")
                if main_middle < 2 or not any(
                    row.count >= 2 and row.diameter_mm >= 12 and row.role != "hanger"
                    for row in design.rows
                ):
                    continue
        effective_depth = design.effective_depth_mm
        if effective_depth is None:
            effective_depth = engine.code.effective_depth(
                data.section,
                hoop.diameter_mm,
                design.maximum_main_diameter_mm,
            )
        minimum = engine.code.minimum_longitudinal_area(
            data.section.width_mm,
            effective_depth,
            data.section.fc_mpa,
            data.section.fy_mpa,
        )
        if design.provided_area_mm2 + 1e-9 < max(float(required), minimum):
            continue
        supported.append(design)
    pattern_signatures = {
        tuple((int(row.count), int(row.diameter_mm)) for row in item.rows if row.role != "hanger")
        for item in patterned
    }
    preference_order = (16, 12, 20, 25) if data.section.is_secondary else (20, 25)

    def candidate_order(design):
        main_rows = tuple(row for row in design.rows if row.role != "hanger")
        preference_cost = sum(
            preference_order.index(row.diameter_mm)
            for row in main_rows
            if row.diameter_mm in preference_order
        )
        main_signature = tuple((int(row.count), int(row.diameter_mm)) for row in main_rows)
        return preference_cost, 0 if main_signature in pattern_signatures else 1

    supported.sort(key=lambda design: (
        *candidate_order(design),
    ))
    return _compress_axis(supported)


def _candidate_axes_for_hoop(
    data,
    hoops,
    bottom_required,
    engine,
    applicable,
    *,
    allowed_main_diameters=None,
):
    """Build the existing local axes once so a coverage query can reuse them."""
    tops = []
    for region_index, (demand, hoop) in enumerate(zip(data.regions, hoops)):
        field_name = ("top_rebar_end_1", "top_rebar_middle", "top_rebar_end_2")[region_index]
        tops.append(_candidate_axis(
            data,
            (hoop,),
            hoop,
            demand.as_top_total_mm2,
            engine,
            face="top",
            rules=applicable.get(field_name, ()),
            support_alternatives=region_index in (0, 2),
            require_sb_mid_t12=region_index == 1,
            allowed_diameters=allowed_main_diameters,
        ))
    bottom = _candidate_axis(
        data,
        hoops,
        hoops[0],
        bottom_required,
        engine,
        face="bottom",
        rules=applicable.get("bottom_rebar", ()),
        support_alternatives=True,
        allowed_diameters=allowed_main_diameters,
    )
    return tuple(tops), bottom


def _coverage_parent_state_target(
    indices,
    normalized_inputs,
    engine,
    *,
    bottom_required,
    allowed_main_by_index,
    allowed_stirrup_diameters,
    deadline,
):
    """Find one catalog-backed common parent state to try before ordinary coverage.

    This pre-query only ranks existing hoop and row-candidate dictionaries.  It
    does not accept a design: the selected state still goes through
    ``_local_records`` and the normal parent and terminal checks.
    """
    if len(indices) < 2 or time.monotonic() >= deadline:
        return None

    from .row_solver import _style_hoop, _hoop_signature, _parent_hoop_potential

    state_maps = {}
    applicable_by_index = {}
    for index in indices:
        data = normalized_inputs[index]
        if data is None or time.monotonic() >= deadline:
            return None
        section = data.section
        applicable = engine.offline_style_library.applicable_fields(data, engine)
        applicable_by_index[index] = applicable
        states = {
            _hoop_signature(state): state
            for state in _v3_hoop_states(
                data,
                engine,
                applicable,
                allowed_stirrup_diameters=allowed_stirrup_diameters,
            )
        }
        spacings = tuple(sorted(set.intersection(*(
            set(engine.project.allowed_spacings_for(section, demand.name))
            for demand in data.regions
        ))))
        if section.is_secondary:
            profiles = ((spacing, spacing, spacing) for spacing in spacings)
        else:
            profiles = (
                (end_spacing, middle_spacing, end_spacing)
                for end_spacing in spacings
                for middle_spacing in spacings
            )
        profiles = tuple(profiles)
        minimum_legs = max(
            2,
            engine.project.preferred_stirrup_legs(section),
            *(engine.code.minimum_legs(section, demand.name) for demand in data.regions),
        )
        minimum_legs += minimum_legs % 2
        stirrup_catalog = engine.project.ordered_stirrup_diameters()
        if allowed_stirrup_diameters is not None:
            allowed = set(allowed_stirrup_diameters)
            stirrup_catalog = tuple(diameter for diameter in stirrup_catalog if diameter in allowed)
        for diameter in stirrup_catalog:
            if time.monotonic() >= deadline:
                return None
            if diameter < engine.project.minimum_stirrup_diameter_for(section):
                continue
            maximum_legs = max(
                engine._capacity_per_layer(section, diameter, db)
                for db in engine.project.ordered_longitudinal_diameters_for(section)
            )
            for profile in profiles:
                for legs in range(minimum_legs, maximum_legs + 1, 2):
                    if time.monotonic() >= deadline:
                        return None
                    hoops = tuple(
                        _style_hoop(
                            section,
                            demand,
                            {"diameter_mm": diameter, "spacing_mm": spacing, "legs": legs},
                            engine,
                        )
                        for demand, spacing in zip(data.regions, profile)
                    )
                    if not all(hoops):
                        continue
                    if any(
                        hoop.provided_at_over_s_mm2_per_mm + 1e-9
                        < demand.at_over_s_mm2_per_mm
                        for demand, hoop in zip(data.regions, hoops)
                    ):
                        continue
                    if not all(
                        _parent_hoop_potential(data, engine, demand, hoop)
                        for demand, hoop in zip(data.regions, hoops)
                    ):
                        continue
                    states.setdefault(_hoop_signature(hoops), hoops)
        state_maps[index] = states

    common_hoop_keys = set.intersection(*(set(state_maps[index]) for index in indices))
    if not common_hoop_keys:
        return None

    def hoop_rank(key):
        return (
            sum(int(item[0]) != 10 for item in key),
            -max(int(item[1]) for item in key),
            sum(int(item[2]) for item in key),
            key,
        )

    common_hoop_keys = sorted(common_hoop_keys, key=hoop_rank)
    checked_states = 0
    for hoop_key in common_hoop_keys:
        if time.monotonic() >= deadline:
            return None
        checked_states += 1
        axes_by_index = {}
        bottom_sets = []
        top_vectors_by_index = {}
        for index in indices:
            data = normalized_inputs[index]
            hoops = state_maps[index][hoop_key]
            axes = _candidate_axes_for_hoop(
                data,
                hoops,
                bottom_required,
                engine,
                applicable_by_index[index],
                allowed_main_diameters=allowed_main_by_index[index],
            )
            axes_by_index[index] = axes
            tops, bottom = axes
            bottom_sets.append({_bar_signature(item) for item in bottom})
            legs = max(int(item[1]) for item in hoop_key)
            filtered_top_patterns = [
                {
                    tuple(int(row.diameter_mm) for row in design.rows if row.role != "hanger")
                    for design in axis
                    if design.rows and design.rows[0].count >= legs
                }
                for axis in tops
            ]
            vectors = set()
            if data.section.is_secondary:
                if all(filtered_top_patterns):
                    vectors.add(())
            elif all(filtered_top_patterns):
                for left_pattern in filtered_top_patterns[0]:
                    for middle_pattern in filtered_top_patterns[1]:
                        for right_pattern in filtered_top_patterns[2]:
                            vector = _pb_vector_from_diameter_patterns(
                                (left_pattern, middle_pattern, right_pattern),
                                is_secondary=False,
                            )
                            if vector is not None:
                                vectors.add(vector)
            top_vectors_by_index[index] = vectors

        common_bottom = set.intersection(*bottom_sets) if bottom_sets else set()
        if not common_bottom or any(not top_vectors_by_index[index] for index in indices):
            continue
        compatible_vectors = set(top_vectors_by_index[indices[0]])
        for index in indices[1:]:
            merged = set()
            for left in compatible_vectors:
                for right in top_vectors_by_index[index]:
                    value = _merge_pb_vector(left, right)
                    if value is not None:
                        merged.add(tuple(value))
            compatible_vectors = merged
            if not compatible_vectors:
                break
        if not compatible_vectors:
            continue

        def bottom_rank(signature):
            return (
                int(any(int(row[1]) != 20 for row in signature)),
                len(signature),
                sum(int(row[0]) for row in signature),
                signature,
            )

        bottom_signature = min(common_bottom, key=bottom_rank)
        return {
            "hoop_key": hoop_key,
            "bottom_signature": bottom_signature,
            "hoop_states": {
                index: state_maps[index][hoop_key] for index in indices
            },
            "axis_candidates": axes_by_index,
            "audit": {
                "input_indices": list(indices),
                "common_hoop_state_count": len(common_hoop_keys),
                "common_hoop_states_checked": checked_states,
                "selected_hoop_signature": [list(item) for item in hoop_key],
                "selected_bottom_signature": [list(item) for item in bottom_signature],
                "common_bottom_signature_count": len(common_bottom),
                "compatible_pb_vectors": [list(item) for item in sorted(compatible_vectors)],
                "candidate_axis_counts": {
                    str(index): {
                        "top": [len(axis) for axis in axes_by_index[index][0]],
                        "bottom": len(axes_by_index[index][1]),
                    }
                    for index in indices
                },
            },
        }
    return None


def _merge_pb_vector(left, right):
    if not left:
        return tuple(right)
    if not right:
        return tuple(left)
    size = max(len(left), len(right))
    output = []
    for index in range(size):
        a = left[index] if index < len(left) else None
        b = right[index] if index < len(right) else None
        if a is not None and b is not None and a != b:
            return None
        output.append(a if a is not None else b)
    return tuple(output)


def _pb_vector(tops: Sequence[LongitudinalDesign], *, is_secondary: bool):
    if is_secondary:
        return ()
    values: list[int | None] = [None, None, None]
    for design in tops:
        for row_index, row in enumerate(design.rows):
            if row_index >= len(values):
                return None
            if values[row_index] is not None and values[row_index] != row.diameter_mm:
                return None
            values[row_index] = row.diameter_mm
    while values and values[-1] is None:
        values.pop()
    return tuple(values)


def _pb_vector_from_diameter_patterns(patterns, *, is_secondary):
    if is_secondary:
        return ()
    values: list[int | None] = [None, None, None]
    for pattern in patterns:
        for row_index, diameter in enumerate(pattern):
            if row_index >= len(values):
                return None
            if values[row_index] is not None and values[row_index] != diameter:
                return None
            values[row_index] = diameter
    while values and values[-1] is None:
        values.pop()
    return tuple(values)


def _preference_tier(tops, bottom, section, hoop_signature):
    main_rows = [
        row
        for design in (*tops, bottom)
        for row in design.rows
        if row.role != "hanger"
    ]
    used_diameters = {int(row.diameter_mm) for row in main_rows}
    # Flags are ordered worst-to-best so tuple comparison preserves the
    # requested longitudinal diameter sequence before hoop or style ranking.
    longitudinal_fallbacks = (
        int(25 in used_diameters),
        int(section.is_secondary and 20 in used_diameters),
        int(section.is_secondary and 12 in used_diameters),
    )
    nonpreferred_hoop_regions = sum(item[0] != 10 for item in hoop_signature)
    return (*longitudinal_fallbacks, nonpreferred_hoop_regions)


def _combine_preference_tiers(tiers):
    """Combine path tiers: longitudinal fallback presence, then hoop count."""
    longitudinal_fallbacks = [0, 0, 0]
    nonpreferred_hoop_regions = 0
    for tier in tiers:
        for index in range(3):
            longitudinal_fallbacks[index] = max(
                longitudinal_fallbacks[index], int(tier[index])
            )
        nonpreferred_hoop_regions += int(tier[3])
    return (*longitudinal_fallbacks, nonpreferred_hoop_regions)


def _pb_vector_from_signatures(record_rows):
    values = [None, None, None]
    for rows in record_rows:
        for index, row in enumerate(rows):
            if index < len(values):
                values[index] = row[1]
    while values and values[-1] is None:
        values.pop()
    return tuple(values)


def _heuristic_record(regions, hoop_signature, tier, index):
    area = sum(
        region.top.provided_area_mm2 - region.top.required_area_mm2
        + region.bottom.provided_area_mm2 - region.bottom.required_area_mm2
        for region in regions
    )
    bar_rows = sum(len(region.top.rows) + len(region.bottom.rows) for region in regions)
    hoop_penalty = sum(1.0 / max(1, item[1]) for item in hoop_signature)
    return (tier, area, hoop_penalty, bar_rows, index)


def _v3_hoop_states(data, engine, applicable, *, allowed_stirrup_diameters=None):
    from .row_solver import _parent_hoop_potential, _style_hoop, _style_hoop_states

    states = list(_style_hoop_states(data, engine, applicable))
    states = [
        state for state in states
        if all(
            hoop.provided_at_over_s_mm2_per_mm + 1e-9
            >= demand.at_over_s_mm2_per_mm
            for demand, hoop in zip(data.regions, state)
        )
    ]
    if allowed_stirrup_diameters is not None:
        allowed = set(allowed_stirrup_diameters)
        states = [state for state in states if all(item.diameter_mm in allowed for item in state)]
    section = data.section
    spacings = tuple(sorted(set.intersection(*(
        set(engine.project.allowed_spacings_for(section, demand.name))
        for demand in data.regions
    ))))
    if section.is_secondary:
        profiles = ((spacing, spacing, spacing) for spacing in spacings)
    else:
        profiles = (
            (end_spacing, middle_spacing, end_spacing)
            for end_spacing in spacings
            for middle_spacing in spacings
        )
    profiles = tuple(profiles)
    minimum_legs = max(
        2,
        engine.project.preferred_stirrup_legs(section),
        *(engine.code.minimum_legs(section, demand.name) for demand in data.regions),
    )
    minimum_legs += minimum_legs % 2
    main_db = max(engine.project.ordered_longitudinal_diameters_for(section))
    stirrup_catalog = engine.project.ordered_stirrup_diameters()
    if allowed_stirrup_diameters is not None:
        stirrup_catalog = tuple(
            diameter for diameter in stirrup_catalog
            if diameter in set(allowed_stirrup_diameters)
        )
    for diameter in stirrup_catalog:
        if diameter < engine.project.minimum_stirrup_diameter_for(section):
            continue
        maximum_legs = max(
            engine._capacity_per_layer(section, diameter, db)
            for db in engine.project.ordered_longitudinal_diameters_for(section)
        )
        for profile in profiles:
            for legs in range(minimum_legs, maximum_legs + 1, 2):
                state = tuple(
                    _style_hoop(
                        section,
                        demand,
                        {"diameter_mm": diameter, "spacing_mm": spacing, "legs": legs},
                        engine,
                    )
                    for demand, spacing in zip(data.regions, profile)
                )
                if not all(state):
                    continue
                if any(
                    hoop.provided_at_over_s_mm2_per_mm + 1e-9
                    < demand.at_over_s_mm2_per_mm
                    for demand, hoop in zip(data.regions, state)
                ):
                    continue
                if not all(
                    _parent_hoop_potential(data, engine, demand, hoop)
                    for demand, hoop in zip(data.regions, state)
                ):
                    continue
                states.append(state)
                break
    unique = {}
    for state in states:
        unique.setdefault(tuple((item.diameter_mm, item.legs, item.spacing_mm) for item in state), state)
    return tuple(unique.values())


def _preferred_t10_static_blocker(data, engine):
    if 10 not in engine.project.ordered_stirrup_diameters():
        return "PREFERRED_T10_NOT_IN_STIRRUP_CATALOGUE"
    if 10 < engine.project.minimum_stirrup_diameter_for(data.section):
        return "PREFERRED_T10_BELOW_CODE_MINIMUM"
    area = engine.project.catalog.area(10)
    for demand in data.regions:
        spacings = engine.project.allowed_spacings_for(data.section, demand.name)
        if not spacings:
            return "NO_ALLOWED_STIRRUP_SPACING"
        maximum_at_over_s = area / min(spacings)
        if maximum_at_over_s + 1e-9 < demand.at_over_s_mm2_per_mm:
            return "PREFERRED_T10_AT_OVER_S_UNSATISFIABLE"
    return None


def _no_allowed_spacing_blocker(
    data,
    engine,
    *,
    allowed_main_diameters=None,
    allowed_stirrup_diameters=None,
):
    """Return a proof that no catalog spacing can meet the code maximum.

    This check is deliberately an upper bound over all allowed longitudinal
    and stirrup diameters.  It can identify a spacing-only hard blocker without
    constructing longitudinal layouts or weakening the project spacing list.
    """
    section = data.section
    main_diameters = tuple(
        allowed_main_diameters
        if allowed_main_diameters is not None
        else engine.project.ordered_longitudinal_diameters_for(section)
    )
    stirrup_diameters = tuple(
        allowed_stirrup_diameters
        if allowed_stirrup_diameters is not None
        else engine.project.ordered_stirrup_diameters()
    )
    minimum_stirrup_diameter = engine.project.minimum_stirrup_diameter_for(section)
    stirrup_diameters = tuple(
        diameter for diameter in stirrup_diameters
        if diameter >= minimum_stirrup_diameter
    )
    if not main_diameters or not stirrup_diameters:
        return None

    for demand in data.regions:
        spacings = tuple(engine.project.allowed_spacings_for(section, demand.name))
        if not spacings:
            return {
                "reason": "NO_ALLOWED_SPACING",
                "region": demand.name,
                "minimum_allowed_spacing_mm": None,
                "maximum_possible_spacing_mm": None,
                "allowed_spacings_mm": [],
                "governing_limit": "NO_SPACING_IN_PROJECT_CATALOGUE",
            }
        best = None
        for stirrup_diameter in stirrup_diameters:
            for main_diameter in main_diameters:
                effective_depth = (
                    section.depth_mm if section.is_smf else
                    engine.code.effective_depth(section, stirrup_diameter, main_diameter)
                )
                maximum, governing_limit, _limits, _high_shear = (
                    engine.code.maximum_stirrup_spacing(
                        section,
                        demand,
                        stirrup_diameter,
                        main_diameter,
                        engine.project.smf_end_max_spacing_mm,
                        effective_depth_mm=effective_depth,
                    )
                )
                if best is None or maximum > best["maximum_possible_spacing_mm"]:
                    best = {
                        "maximum_possible_spacing_mm": float(maximum),
                        "main_diameter_mm": int(main_diameter),
                        "stirrup_diameter_mm": int(stirrup_diameter),
                        "governing_limit": str(governing_limit),
                    }
        minimum_allowed = min(spacings)
        if best is None or best["maximum_possible_spacing_mm"] + 1e-9 < minimum_allowed:
            return {
                "reason": "NO_ALLOWED_SPACING",
                "region": demand.name,
                "minimum_allowed_spacing_mm": int(minimum_allowed),
                "maximum_possible_spacing_mm": (
                    None if best is None else best["maximum_possible_spacing_mm"]
                ),
                "allowed_spacings_mm": list(spacings),
                **({} if best is None else {
                    "main_diameter_mm": best["main_diameter_mm"],
                    "stirrup_diameter_mm": best["stirrup_diameter_mm"],
                    "governing_limit": best["governing_limit"],
                }),
            }
    return None


def _best_first_pairs(left, right, limit=None):
    if not left or not right:
        return
    heap = [(0, 0, 0)]
    seen = {(0, 0)}
    produced = 0
    while heap and (limit is None or produced < limit):
        _score, i, j = heapq.heappop(heap)
        yield left[i], right[j]
        produced += 1
        for ni, nj in ((i + 1, j), (i, j + 1)):
            if ni < len(left) and nj < len(right) and (ni, nj) not in seen:
                seen.add((ni, nj))
                heapq.heappush(heap, (ni + nj, ni, nj))


def _local_records(index, data, engine, *, bottom_required, deadline, budget, audit,
                   allowed_main_diameters=None, allowed_stirrup_diameters=None,
                   representatives_per_interface=None, precomputed_hoop_states=None,
                   precomputed_axis_candidates=None, target_parent_state=None,
                   coverage_schedule=False, coverage_parent_state_scope=False,
                   coverage_seen_tuples=None, coverage_existing_records=(),
                   research_actual_parent_hoop_signatures=False):
    from .row_solver import _required_hoop_legs, _style_hoop_states, _hoop_signature
    from .row_candidates import signature

    rejection_counts: dict[str, int] = {}
    rejection_examples: dict[str, Any] = {}
    state_budget_cutoffs = 0
    tuple_combinations = 0
    local_start = audit.get("local_combo_checks", 0)
    search_exhausted = False

    def local_used():
        return audit.get("local_combo_checks", 0) - local_start

    def reject(reason, example=None):
        code = str(reason).strip().split()[0] if str(reason).strip() else "UNSPECIFIED_LOCAL_REJECTION"
        rejection_counts[code] = rejection_counts.get(code, 0) + 1
        if code not in rejection_examples:
            rejection_examples[code] = example

    def finish(values):
        span_key = str(index)
        histogram = audit.setdefault("local_rejection_histogram", {}).setdefault(span_key, {})
        for code, amount in rejection_counts.items():
            entry = histogram.setdefault(code, {"count": 0, "first_example": rejection_examples.get(code)})
            entry["count"] += amount
            if entry.get("first_example") is None:
                entry["first_example"] = rejection_examples.get(code)
        truncated = bool(search_exhausted or state_budget_cutoffs)
        audit.setdefault("local_search_by_input", {})[span_key] = {
            "local_combo_checks": local_used(),
            "candidate_tuple_combinations": tuple_combinations,
            "hard_valid_records": len(values),
            "truncated": truncated,
            "stop_reason": (
                "LOCAL_TIME_OR_BUDGET_EXHAUSTED" if search_exhausted else
                "PER_HOOP_STATE_BUDGET_EXHAUSTED" if state_budget_cutoffs else
                "LOCAL_REPRESENTATIVES_COLLECTED_OR_SEARCH_EXHAUSTED"
            ),
        }
        return tuple(values)

    library = engine.offline_style_library
    applicable = library.applicable_fields(data, engine)
    hoop_states = (
        tuple(precomputed_hoop_states)
        if precomputed_hoop_states is not None else
        _v3_hoop_states(
            data, engine, applicable,
            allowed_stirrup_diameters=allowed_stirrup_diameters,
        )
    )
    if not hoop_states:
        spacing_blocker = _no_allowed_spacing_blocker(
            data,
            engine,
            allowed_main_diameters=allowed_main_diameters,
            allowed_stirrup_diameters=allowed_stirrup_diameters,
        )
        reason = (
            spacing_blocker["reason"] if spacing_blocker is not None
            else "NO_ALLOWED_HOOP_STATE"
        )
        reject(reason, spacing_blocker)
        values = finish(())
        if spacing_blocker is not None:
            audit["local_search_by_input"][str(index)]["terminal_blocker"] = spacing_blocker
        return values
    hoop_states = tuple(sorted(
        dict.fromkeys(hoop_states),
        key=lambda state: (
            sum(item.diameter_mm != 10 for item in state),
            sum(item.spacing_mm for item in state),
            _hoop_signature(state),
        ),
    ))
    records: dict[tuple[Any, ...], _SpanRecord] = {
        _span_record_storage_key(record): record
        for record in coverage_existing_records
    }
    representative_counts: dict[tuple[Any, ...], int] = {}
    for record in records.values():
        future_interface = (
            tuple(row[1] for row in record.left_support),
            tuple(row[1] for row in record.right_support),
            record.pb_row_diameters,
        )
        representative_key = (future_interface, record.seed_hoop_signature)
        if coverage_parent_state_scope:
            representative_key = (*representative_key, record.bottom_signature)
        representative_counts[representative_key] = (
            representative_counts.get(representative_key, 0) + 1
        )
    initial_record_count = len(records)
    initial_representative_count = sum(representative_counts.values())
    for hoop_index, hoops in enumerate(hoop_states):
        if time.monotonic() >= deadline or local_used() >= budget:
            audit["truncated"] = True
            search_exhausted = True
            break
        hoop_key = _hoop_signature(hoops)
        if target_parent_state is not None and hoop_key != target_parent_state[0]:
            continue
        remaining_states = len(hoop_states) - hoop_index
        remaining_budget = budget - local_used()
        state_ceiling = local_used() + max(
            1, remaining_budget // max(1, remaining_states)
        )
        state_done = False
        if len({item.diameter_mm for item in hoops}) != 1:
            continue
        cached_axes = (
            precomputed_axis_candidates.get(hoop_key)
            if precomputed_axis_candidates is not None else None
        )
        if cached_axes is None:
            tops, bottom = _candidate_axes_for_hoop(
                data,
                hoops,
                bottom_required,
                engine,
                applicable,
                allowed_main_diameters=allowed_main_diameters,
            )
        else:
            tops, bottom = cached_axes
        for region_index, choices in enumerate(tops):
            demand = data.regions[region_index]
            if not choices:
                reject("NO_TOP_AXIS_CANDIDATE", {
                    "region": demand.name,
                    "hoop": _hoop_signature((hoops[region_index],)),
                })
        if any(not choices for choices in tops):
            continue
        if target_parent_state is not None:
            required_bottom_signature = target_parent_state[1]
            bottom = tuple(
                design for design in bottom
                if _bar_signature(design) == required_bottom_signature
            )
        if not bottom:
            reject(
                "NO_BOTTOM_AXIS_CANDIDATE",
                {"hoop": hoop_key, "target_parent_state": target_parent_state},
            )
            continue

        left_groups: dict[tuple[int, ...], tuple[LongitudinalDesign, ...]] = {}
        right_groups: dict[tuple[int, ...], tuple[LongitudinalDesign, ...]] = {}
        for values, target in ((tops[0], left_groups), (tops[2], right_groups)):
            grouped: dict[tuple[int, ...], list[LongitudinalDesign]] = {}
            for design in values:
                key = tuple(row.diameter_mm for row in design.rows)
                grouped.setdefault(key, []).append(design)
            target.update({key: tuple(items) for key, items in grouped.items()})
        middle_groups: dict[tuple[int, ...], tuple[LongitudinalDesign, ...]] = {}
        for design in tops[1]:
            key = tuple(row.diameter_mm for row in design.rows)
            middle_groups.setdefault(key, tuple())
            middle_groups[key] = (*middle_groups[key], design)

        for left_pattern, left_values in left_groups.items():
            for right_pattern, right_values in right_groups.items():
                for middle_pattern, middle_values in middle_groups.items():
                    pb_vector = _pb_vector_from_diameter_patterns(
                        (left_pattern, middle_pattern, right_pattern),
                        is_secondary=data.section.is_secondary,
                    )
                    if pb_vector is None:
                        reject("PB_TOP_ROW_DIAMETER_PATTERN_CONFLICT", {
                            "left": left_pattern,
                            "middle": middle_pattern,
                            "right": right_pattern,
                            "hoop": hoop_key,
                        })
                        continue
                    future_interface = (left_pattern, right_pattern, pb_vector)
                    representative_key = (future_interface, hoop_key)
                    if (not coverage_parent_state_scope
                            and representatives_per_interface is not None
                            and representative_counts.get(representative_key, 0)
                            >= representatives_per_interface):
                        audit["local_interface_blocks_skipped_after_coverage"] = (
                            audit.get("local_interface_blocks_skipped_after_coverage", 0) + 1
                        )
                        continue
                    for left, right in _best_first_pairs(left_values, right_values):
                        if state_done or (
                            not coverage_parent_state_scope
                            and representatives_per_interface is not None
                            and representative_counts.get(representative_key, 0)
                            >= representatives_per_interface
                        ):
                            break
                        for middle, bottom_design in _best_first_pairs(middle_values, bottom):
                            active_representative_key = (
                                (*representative_key, _bar_signature(bottom_design))
                                if coverage_schedule and coverage_parent_state_scope
                                else representative_key
                            )
                            if (representatives_per_interface is not None
                                    and representative_counts.get(active_representative_key, 0)
                                    >= representatives_per_interface):
                                if coverage_schedule and coverage_parent_state_scope:
                                    continue
                                break
                            if time.monotonic() >= deadline:
                                audit["truncated"] = True
                                state_done = True
                                search_exhausted = True
                                break
                            if local_used() >= budget:
                                audit["truncated"] = True
                                state_done = True
                                search_exhausted = True
                                break
                            if local_used() >= state_ceiling:
                                state_budget_cutoffs += 1
                                audit["truncated"] = True
                                state_done = True
                                break
                            actual_tops = (left, middle, right)
                            actual_pb_vector = _pb_vector(
                                actual_tops,
                                is_secondary=data.section.is_secondary,
                            )
                            raw_tuple_key = _raw_local_tuple_key(
                                left,
                                middle,
                                right,
                                bottom_design,
                                actual_pb_vector,
                                hoop_key,
                            )
                            if coverage_seen_tuples is not None:
                                if raw_tuple_key in coverage_seen_tuples:
                                    continue
                                coverage_seen_tuples.add(raw_tuple_key)
                            tuple_combinations += 1
                            audit["local_candidate_tuple_combinations"] += 1
                            if actual_pb_vector != pb_vector:
                                reject("PB_TOP_ROW_DIAMETER_PATTERN_CONFLICT", {
                                    "actual": actual_pb_vector,
                                    "expected": pb_vector,
                                    "hoop": hoop_key,
                                })
                                continue
                            audit["local_combo_checks"] += 1
                            regions = tuple(
                                RegionDetailing(
                                    demand.name,
                                    demand,
                                    engine.code.minimum_longitudinal_area(
                                        data.section.width_mm,
                                        min(top.effective_depth_mm, bottom_design.effective_depth_mm),
                                        data.section.fc_mpa,
                                        data.section.fy_mpa,
                                    ),
                                    demand.as_flex_top_mm2,
                                    demand.as_flex_bottom_mm2,
                                    hoop,
                                    top,
                                    bottom_design,
                                )
                                for demand, hoop, top in zip(data.regions, hoops, actual_tops)
                            )
                            actual_hoop_legs = []
                            actual_hoop_legs_available = True
                            for region in regions:
                                depth = min(
                                    region.top.effective_depth_mm,
                                    region.bottom.effective_depth_mm,
                                )
                                if data.section.is_smf:
                                    depth = data.section.depth_mm
                                final_db = engine._final_main_db(region, data.section)
                                required_legs = _required_hoop_legs(
                                    data.section,
                                    region.demand,
                                    engine,
                                    region.stirrup.diameter_mm,
                                    region.stirrup.spacing_mm,
                                    max_main=final_db,
                                    effective_depth=depth,
                                )
                                if required_legs is None:
                                    reject("REQUIRED_HOOP_LEGS_UNAVAILABLE", {
                                        "region": region.name,
                                        "hoop": _hoop_signature((region.stirrup,)),
                                        "top": _bar_signature(region.top),
                                        "bottom": _bar_signature(region.bottom),
                                    })
                                    actual_hoop_legs = []
                                    actual_hoop_legs_available = False
                                    break
                                actual_hoop_legs.append(required_legs)
                            common_legs = (
                                _common_hoop_legs(
                                    actual_hoop_legs,
                                    hoop_key,
                                    research_seed_lower_bound=research_actual_parent_hoop_signatures,
                                )
                                if actual_hoop_legs_available else 0
                            )
                            if common_legs < 2 or any(
                                min(region.top.rows[0].count, region.bottom.rows[0].count) < common_legs
                                for region in regions
                            ):
                                reject("FIRST_ROW_HOOP_SUPPORT_FAILED", {
                                    "required_legs": common_legs,
                                    "hoop": hoop_key,
                                    "top": tuple(_bar_signature(item) for item in actual_tops),
                                    "bottom": _bar_signature(bottom_design),
                                })
                                continue
                            regions = tuple(
                                replace(
                                    region,
                                    stirrup=replace(
                                        region.stirrup,
                                        legs=common_legs,
                                        provided_av_over_s_mm2_per_mm=(
                                            common_legs
                                            * engine.project.catalog.area(region.stirrup.diameter_mm)
                                            / region.stirrup.spacing_mm
                                        ),
                                    ),
                                )
                                for region in regions
                            )
                            from .v3_hard_checks import prepare_light_regions

                            try:
                                regions = prepare_light_regions(data, regions, engine)
                            except DetailingError as exc:
                                reject(str(exc).rsplit(": ", 1)[-1], {
                                    "hoop": hoop_key,
                                    "top": tuple(_bar_signature(item) for item in actual_tops),
                                    "bottom": _bar_signature(bottom_design),
                                })
                                continue
                            face = engine._construction_face_rebar(data.section)
                            bottom_sig = _bar_signature(bottom_design)
                            parent_hoop_key = _parent_join_hoop_signature(
                                hoop_key,
                                regions,
                                use_actual_signature=research_actual_parent_hoop_signatures,
                            )
                            tier = _preference_tier(
                                actual_tops,
                                bottom_design,
                                data.section,
                                parent_hoop_key,
                            )
                            heuristic = _heuristic_record(regions, parent_hoop_key, tier, index)
                            record = _SpanRecord(
                                index=index,
                                input_data=data,
                                regions=regions,
                                face_rebar=face,
                                left_support=_rows_signature(left),
                                right_support=_rows_signature(right),
                                pb_row_diameters=pb_vector,
                                bottom_signature=bottom_sig,
                                hoop_signature=parent_hoop_key,
                                seed_hoop_signature=hoop_key,
                                preference_tier=tier,
                                heuristic=heuristic,
                            )
                            key = _span_record_storage_key(record)
                            prior = records.get(key)
                            if prior is None or record.heuristic < prior.heuristic:
                                records[key] = record
                                if prior is None and representatives_per_interface is not None:
                                        representative_counts[active_representative_key] = (
                                            representative_counts.get(active_representative_key, 0) + 1
                                    )
                            audit["local_records_seen"] += 1
                        if state_done:
                            break
                    if state_done:
                        break
                if state_done:
                    break
            if state_done:
                break
        if search_exhausted:
            break
    result = tuple(sorted(records.values(), key=lambda item: item.heuristic))
    audit["local_interface_representatives"] = (
        audit.get("local_interface_representatives", 0)
        + max(0, sum(representative_counts.values()) - initial_representative_count)
    )
    audit["interface_state_count"] += max(0, len(result) - initial_record_count)
    return finish(result)


def _unit_options(
    indices,
    records_by_index,
    *,
    option,
    engine,
    limit,
    audit,
    representatives_per_interface=10,
    coverage_schedule=False,
    coverage_audit=None,
):
    if not indices or any(not records_by_index.get(index) for index in indices):
        return ()
    if len(indices) == 1:
        return tuple(
            _UnitOption(
                indices=indices,
                records=(record,),
                left_support=record.left_support,
                right_support=record.right_support,
                pb_row_diameters=record.pb_row_diameters,
                preference_tier=record.preference_tier,
                heuristic=record.heuristic,
            )
            for record in records_by_index[indices[0]]
        )
    records_by_shared_state = {}
    is_smf_parent = bool(
        coverage_schedule and records_by_index[indices[0]][0].input_data.section.is_smf
    )
    shared_state_keys = None
    for index in indices:
        grouped = {}
        for record in records_by_index[index]:
            open_state = (record.bottom_signature, record.hoop_signature)
            grouped.setdefault(open_state, []).append(record)
        records_by_shared_state[index] = grouped
        keys = set(grouped)
        shared_state_keys = keys if shared_state_keys is None else shared_state_keys & keys
    shared_state_keys = tuple(sorted(shared_state_keys or ()))
    audit["parent_shared_state_count"] = (
        audit.get("parent_shared_state_count", 0) + len(shared_state_keys)
    )

    output: dict[tuple[Any, ...], list[_UnitOption]] = {}
    stop_search = False
    state_join_budget = max(1, limit // max(1, len(shared_state_keys)))
    state_join_checks = 0
    state_budget_exhausted = False
    record_strength_cache = {}
    best_future_min_cache = {}

    def strength_vector(record):
        cached = record_strength_cache.get(id(record))
        if cached is not None:
            return cached
        from .constructability import _nominal_moment

        section = record.input_data.section
        values = []
        for region in record.regions:
            values.append(_nominal_moment(
                region.bottom.provided_area_mm2,
                region.top.provided_area_mm2,
                section.width_mm,
                section.fc_mpa,
                section.fy_mpa,
                region.bottom.effective_depth_mm,
            ))
            values.append(_nominal_moment(
                region.top.provided_area_mm2,
                region.bottom.provided_area_mm2,
                section.width_mm,
                section.fc_mpa,
                section.fy_mpa,
                region.top.effective_depth_mm,
            ))
        result = tuple(float(value) for value in values)
        record_strength_cache[id(record)] = result
        return result

    def strength_bounds(records):
        flat = tuple(value for record in records for value in strength_vector(record))
        return (min(flat), max(flat)) if flat else (0.0, 0.0)

    def endpoint_controls_pass(record, *, first):
        vector = strength_vector(record)
        if len(vector) < 6:
            return False
        positive, negative = (vector[0], vector[1]) if first else (vector[-2], vector[-1])
        return negative > 0.0 and positive / negative + 1e-9 >= 0.5

    def partial_strength_can_pass(selected, next_position, open_state):
        current_min, current_max = strength_bounds(selected)
        if next_position == 1 and not endpoint_controls_pass(selected[0], first=True):
            return False
        if next_position == len(indices) and not endpoint_controls_pass(selected[-1], first=False):
            return False
        if current_max <= 0.0:
            return False
        optimistic_min = current_min
        for future_position in range(next_position, len(indices)):
            future_index = indices[future_position]
            cache_key = (open_state, future_index)
            if cache_key not in best_future_min_cache:
                future_records = records_by_shared_state[future_index].get(open_state, ())
                best_future_min_cache[cache_key] = max(
                    (min(strength_vector(record)) for record in future_records),
                    default=0.0,
                )
            best_future_min = best_future_min_cache[cache_key]
            if best_future_min <= 0.0:
                return False
            optimistic_min = min(optimistic_min, best_future_min)
        return optimistic_min / current_max + 1e-9 >= 0.25

    def extend(position, selected, vector, open_state):
        nonlocal stop_search, state_join_checks, state_budget_exhausted
        if stop_search or state_budget_exhausted:
            return
        if len(output) >= limit:
            audit["truncated"] = True
            stop_search = True
            return
        if position == len(indices):
            first, last = selected[0], selected[-1]
            bottom, hoops = open_state
            state_key = (
                _support_diameter_pattern(first.left_support),
                _support_diameter_pattern(last.right_support),
                vector,
                bottom,
                hoops,
            )
            representatives = output.get(state_key, ())
            if len(representatives) >= representatives_per_interface:
                audit["parent_interface_candidates_skipped_after_coverage"] = (
                    audit.get("parent_interface_candidates_skipped_after_coverage", 0) + 1
                )
                return
            if first.input_data.section.is_smf:
                from .constructability import ParentLongitudinalRun, parent_longitudinal_checks

                runs = tuple(ParentLongitudinalRun(
                    record.input_data.section.name,
                    record.input_data.section.width_mm,
                    record.input_data.section.fc_mpa,
                    record.input_data.section.fy_mpa,
                    record.regions[0].top,
                    record.regions[0].bottom,
                    record.regions[1].top,
                    record.regions[1].bottom,
                    record.regions[2].top,
                    record.regions[2].bottom,
                ) for record in selected)
                checks = parent_longitudinal_checks(runs=runs)
                audit["parent_longitudinal_checks"] = (
                    audit.get("parent_longitudinal_checks", 0) + 1
                )
                failures = tuple(
                    check for check in checks
                    if check.status not in {"PASS", "INFO", "WARN"}
                )
                if coverage_schedule and coverage_audit is not None:
                    coverage_audit["parent_longitudinal_check_calls"] = (
                        coverage_audit.get("parent_longitudinal_check_calls", 0) + 1
                    )
                    if failures:
                        histogram = coverage_audit.setdefault(
                            "parent_longitudinal_failure_histogram", {}
                        )
                        examples = coverage_audit.setdefault(
                            "parent_longitudinal_failure_examples", {}
                        )
                        for check in failures:
                            status = str(check.status)
                            histogram[status] = histogram.get(status, 0) + 1
                            examples.setdefault(status, {
                                "rule_id": getattr(check, "rule_id", None),
                                "actual": getattr(check, "actual", None),
                                "limit": getattr(check, "limit", None),
                                "source": getattr(check, "source", None),
                                "message": getattr(check, "message", None),
                            })
                    else:
                        coverage_audit["parent_longitudinal_check_passes"] = (
                            coverage_audit.get("parent_longitudinal_check_passes", 0) + 1
                        )
                if failures:
                    return
            option_value = _UnitOption(
                indices=indices,
                records=tuple(selected),
                left_support=first.left_support,
                right_support=last.right_support,
                pb_row_diameters=vector,
                preference_tier=_combine_preference_tiers(
                    item.preference_tier for item in selected
                ),
                heuristic=tuple(value for item in selected for value in item.heuristic),
            )
            representatives = output.setdefault(state_key, [])
            if option_value not in representatives:
                representatives.append(option_value)
                representatives.sort(key=lambda item: item.heuristic)
                del representatives[representatives_per_interface:]
                if coverage_schedule and coverage_audit is not None:
                    coverage_audit["parent_unit_options"] = (
                        coverage_audit.get("parent_unit_options", 0) + 1
                    )
            return
        index = indices[position]
        candidates = records_by_shared_state[index].get(open_state, ())
        if coverage_schedule:
            def candidate_strength_rank(record):
                low, high = strength_bounds((*selected, record))
                ratio = low / high if high > 0.0 else 0.0
                endpoint_penalty = 0
                if position == 0 and not endpoint_controls_pass(record, first=True):
                    endpoint_penalty += 1
                if position == len(indices) - 1 and not endpoint_controls_pass(record, first=False):
                    endpoint_penalty += 1
                return (endpoint_penalty, -ratio, -low, high, record.heuristic)

            candidates = tuple(sorted(candidates, key=candidate_strength_rank))
        for record in candidates:
            merged = _merge_pb_vector(vector, record.pb_row_diameters)
            if merged is None:
                continue
            if position == len(indices) - 1:
                first = selected[0]
                possible_key = (
                    _support_diameter_pattern(first.left_support),
                    _support_diameter_pattern(record.right_support),
                    merged,
                    open_state[0],
                    open_state[1],
                )
                if len(output.get(possible_key, ())) >= representatives_per_interface:
                    audit["parent_interface_candidates_skipped_after_coverage"] = (
                        audit.get("parent_interface_candidates_skipped_after_coverage", 0) + 1
                    )
                    continue
            if state_join_checks >= state_join_budget:
                state_budget_exhausted = True
                audit["truncated"] = True
                return
            state_join_checks += 1
            audit["parent_join_combinations"] = audit.get("parent_join_combinations", 0) + 1
            next_selected = (*selected, record)
            if (coverage_schedule and is_smf_parent
                    and not partial_strength_can_pass(
                        next_selected,
                        position + 1,
                        open_state,
                    )):
                if coverage_audit is not None:
                    coverage_audit["parent_strength_bound_prunes"] = (
                        coverage_audit.get("parent_strength_bound_prunes", 0) + 1
                    )
                continue
            extend(position + 1, next_selected, merged, open_state)
            if stop_search:
                return

    for open_state in shared_state_keys:
        if stop_search:
            break
        state_join_checks = 0
        state_budget_exhausted = False
        extend(0, (), (), open_state)
    return tuple(sorted(
        (option for variants in output.values() for option in variants),
        key=lambda item: item.heuristic,
    ))


def _support_compatible(candidate, required, *, exact):
    from .row_solver import _support_compatible as existing

    return existing(candidate, required, exact=exact)


def _support_patterns_compatible(candidate_pattern, required_pattern, *, exact):
    """Apply the physical support rule to diameter patterns only.

    V3 support continuity deliberately ignores bar counts.  Its exact and
    non-exact branches compare only the number of rows and corresponding
    diameters, so records with different counts share this compatibility
    state without losing any reachable path.
    """
    if required_pattern is None:
        return True
    if candidate_pattern is None:
        return False
    candidate = tuple((0, diameter) for diameter in candidate_pattern)
    required = tuple((0, diameter) for diameter in required_pattern)
    return _support_compatible(candidate, required, exact=exact)


def _support_pattern_indices(options, side):
    indexed = {}
    for index, option in enumerate(options):
        pattern = _support_diameter_pattern(getattr(option, side))
        indexed.setdefault(pattern, []).append(index)
    return indexed


def _path_key(path):
    return (
        _combine_preference_tiers(unit.preference_tier for unit in path),
        tuple(record.heuristic for unit in path for record in unit.records),
        tuple((unit.left_support, unit.right_support) for unit in path),
    )


def _coordinate_units(units, option, engine, *, k, audit=None):
    if not units or any(not unit_options for _indices, unit_options, _data_group in units):
        return ()
    original_count = sum(len(unit_options) for _indices, unit_options, _data_group in units)
    reachable: list[set[int]] = [set() for _ in units]
    reachable[-1] = set(range(len(units[-1][1])))
    backward_left_patterns_by_unit = {
        index: _support_pattern_indices(unit[1], "left_support")
        for index, unit in enumerate(units)
    }
    compatibility_cache = {}
    compatibility_checks = 0

    def compatible_patterns(candidate_pattern, required_pattern, *, exact):
        nonlocal compatibility_checks
        key = (bool(exact), candidate_pattern, required_pattern)
        if key not in compatibility_cache:
            compatibility_cache[key] = _support_patterns_compatible(
                candidate_pattern,
                required_pattern,
                exact=exact,
            )
            compatibility_checks += 1
        return compatibility_cache[key]

    def record_compatibility_checks():
        if audit is not None:
            audit["coordinate_support_pattern_pair_checks"] = (
                audit.get("coordinate_support_pattern_pair_checks", 0)
                + compatibility_checks
            )

    for index in range(len(units) - 2, -1, -1):
        previous_sections = units[index][2]
        current_sections = units[index + 1][2]
        exact = (
            option is ContinuityOption.OPTION_1_STRICT_EQUAL
            or previous_sections[-1].section.is_secondary
            or current_sections[0].section.is_secondary
        )
        next_reachable = reachable[index + 1]
        next_left_patterns = tuple(
            pattern
            for pattern, candidate_indices in backward_left_patterns_by_unit[index + 1].items()
            if any(candidate_index in next_reachable for candidate_index in candidate_indices)
        )
        for candidate_index, candidate in enumerate(units[index][1]):
            if any(
                compatible_patterns(
                    next_pattern,
                    _support_diameter_pattern(candidate.right_support),
                    exact=exact,
                )
                for next_pattern in next_left_patterns
            ):
                reachable[index].add(candidate_index)
    filtered_units = []
    for index, (indices, unit_options, data_group) in enumerate(units):
        filtered_units.append((
            indices,
            tuple(candidate for candidate_index, candidate in enumerate(unit_options)
                  if candidate_index in reachable[index]),
            data_group,
        ))
    pruned = original_count - sum(len(unit_options) for _indices, unit_options, _data_group in filtered_units)
    if audit is not None:
        audit["backward_pruned"] = audit.get("backward_pruned", 0) + pruned
        audit["interface_class_count"] = audit.get("interface_class_count", 0) + sum(
            len({(
                _support_diameter_pattern(candidate.left_support),
                _support_diameter_pattern(candidate.right_support),
            ) for candidate in unit_options})
            for _indices, unit_options, _data_group in filtered_units
        )
    units = tuple(filtered_units)
    if any(not unit_options for _indices, unit_options, _data_group in units):
        record_compatibility_checks()
        return ()
    # Backward reachability changes each unit's option positions. Rebuild the
    # forward index against the filtered order so stored indices always refer
    # to the candidate tuple they will retrieve.
    left_patterns_by_unit = {
        index: _support_pattern_indices(unit[1], "left_support")
        for index, unit in enumerate(units)
    }
    states: dict[tuple[Any, ...], list[tuple[_UnitOption, ...]]] = {}
    first_options = units[0][1]
    for candidate in first_options:
        key = (_support_diameter_pattern(candidate.right_support), candidate.pb_row_diameters)
        target = states.setdefault(key, [])
        target.append((candidate,))
        target.sort(key=_path_key)
        del target[k:]
    for index in range(1, len(units)):
        options = units[index][1]
        next_states: dict[tuple[Any, ...], list[tuple[_UnitOption, ...]]] = {}
        previous_sections = units[index - 1][2]
        current_sections = units[index][2]
        exact = (
            option is ContinuityOption.OPTION_1_STRICT_EQUAL
            or previous_sections[-1].section.is_secondary
            or current_sections[0].section.is_secondary
        )
        next_indices_by_left_pattern = left_patterns_by_unit[index]
        for (_support_pattern, vector), paths in states.items():
            matching_indices = sorted(
                candidate_index
                for left_pattern, candidate_indices in next_indices_by_left_pattern.items()
                if compatible_patterns(
                    left_pattern,
                    _support_pattern,
                    exact=exact,
                )
                for candidate_index in candidate_indices
            )
            for candidate_index in matching_indices:
                candidate = options[candidate_index]
                merged = _merge_pb_vector(vector, candidate.pb_row_diameters)
                if merged is None:
                    continue
                key = (_support_diameter_pattern(candidate.right_support), merged)
                target = next_states.setdefault(key, [])
                target.extend((*path, candidate) for path in paths)
                target.sort(key=_path_key)
                del target[k:]
        states = next_states
        if not states:
            record_compatibility_checks()
            return ()
    paths = [path for candidates in states.values() for path in candidates]
    paths.sort(key=_path_key)
    record_compatibility_checks()
    return tuple(paths[:max(k * 4, k)])


def _materialize_record(record, option, engine, *, search_index, parent_unknown=False):
    from .row_solver import finalize_rows

    data = record.input_data
    # Build only after a complete line path has survived interface reachability.
    parent_unknown_status = (
        str(parent_unknown)
        if isinstance(parent_unknown, str) and parent_unknown
        else "V3_PARENT_COORDINATION_UNKNOWN_INPUT_BLOCKED" if parent_unknown
        else None
    )
    provisional = BeamDetailingResult(
        beam_name=data.section.name,
        code_edition=engine.code.edition,
        code_policy_version=engine.code.version,
        project_policy_version=engine.project.version,
        detailing_profile=data.section.detailing_profile,
        section=data.section,
        mode=ReinforcementMode.NORMAL,
        regions=record.regions,
        face_rebar=record.face_rebar,
        input_data=data,
        continuity_option=option,
        coordination_status=(parent_unknown_status or "V3_RECOMMENDATION_SET_ONLY"),
        coordination_audit=({
            "policy_id": DRAWING_V3_POLICY_ID,
            "policy_revision_id": engine.detailing_policy_revision_id,
            "solver_revision_id": DRAWING_V3_SOLVER_REVISION_ID,
            "candidate_scope": "BOUNDED_INTERFACE_RECOMMENDATION_SET",
            "materialization_index": search_index,
        },),
    )
    result = finalize_rows(provisional, engine)
    if engine.independent_skin_rebar is False:
        raise DetailingError("V3_INDEPENDENT_SKIN_POLICY_REQUIRED")
    if result.section.is_secondary:
        middle = result.regions[1].top
        if not any(
            row.role != "hanger" and row.count >= 2 and row.diameter_mm >= 12
            for row in middle.rows
        ):
            raise DetailingError("V3_SB_MIDSPAN_MINIMUM_T12_FAILED")
    for region in result.regions:
        for design in (region.top, region.bottom):
            if any(row.role != "hanger" and
                   row.diameter_mm not in engine.project.ordered_longitudinal_diameters_for(result.section)
                   for row in design.rows):
                raise DetailingError("V3_MAIN_BAR_DIAMETER_OUTSIDE_ROLE_CATALOGUE")
    library = engine.offline_style_library
    applicable = library.applicable_fields(data, engine)
    matched = library.matched_fields(data, result, engine)
    applicable_ids = {
        rule["rule_id"] for rules in applicable.values() for rule in rules
    }
    matched_ids = {item.rule_id for item in matched}
    audit = library.audit(
        data,
        result,
        engine,
        fallback_reason=("NO_APPLICABLE_PATTERN" if not applicable else
                         "NO_FEASIBLE_SUPPORTED_PATTERN" if not matched else None),
        unmatched_applicable_pattern_rule_ids=applicable_ids - matched_ids,
    )
    prior = tuple(item for item in result.coordination_audit
                  if not (isinstance(item, dict) and item.get("policy_id") == DRAWING_V3_POLICY_ID))
    return replace(
        result,
        coordination_status=(parent_unknown_status or "V3_RECOMMENDATION_SET_ONLY"),
        coordination_audit=(*prior, audit, {
            "policy_id": DRAWING_V3_POLICY_ID,
            "solver_revision_id": DRAWING_V3_SOLVER_REVISION_ID,
            "recommendation_scope": "RECOMMENDATION_SET_ONLY",
            "global_optimality_claimed": False,
        }),
    )


def _validate_full_path(path, option, parent_groups, engine):
    flattened = [
        record
        for component_path in path
        for unit in component_path
        for record in unit.records
    ]
    by_index = {record.index: record for record in flattened}
    ordered = [by_index[index] for index in sorted(by_index)]
    for left_record, right_record in zip(ordered, ordered[1:]):
        if right_record.index != left_record.index + 1:
            continue
        if any(left_record.index in group and right_record.index in group for group in parent_groups):
            continue
        exact = (
            option is ContinuityOption.OPTION_1_STRICT_EQUAL
            or left_record.input_data.section.is_secondary
            or right_record.input_data.section.is_secondary
        )
        if not _support_compatible(right_record.left_support, left_record.right_support, exact=exact):
            raise DetailingError("V3_SUPPORT_INTERFACE_REACHABILITY_FAILED")
    return ordered


def _parent_context_coverage_audit(results, tolerance_mm=1.0):
    contexts = []
    for result in results:
        input_data = getattr(result, "input_data", None)
        context = getattr(input_data, "parent_context", None)
        if context is None:
            return {
                "status": "UNVERIFIED_PARENT_CONTEXT_MISSING",
                "intervals_mm": [],
            }
        try:
            parent_id = str(context.parent_span_id).strip()
            parent_length = float(context.parent_length_mm)
            start = float(context.segment_start_mm)
            segment_length = float(result.section.length_mm)
        except (AttributeError, TypeError, ValueError):
            return {
                "status": "UNVERIFIED_PARENT_CONTEXT_INVALID",
                "intervals_mm": [],
            }
        if not parent_id or not all(
            value >= 0.0 for value in (parent_length, start, segment_length)
        ) or parent_length <= 0.0 or segment_length <= 0.0:
            return {
                "status": "UNVERIFIED_PARENT_CONTEXT_INVALID",
                "intervals_mm": [],
            }
        contexts.append((parent_id, parent_length, start, start + segment_length))

    parent_ids = {item[0] for item in contexts}
    parent_lengths = tuple(item[1] for item in contexts)
    intervals = tuple((item[2], item[3]) for item in contexts)
    valid = (
        len(parent_ids) == 1
        and max(parent_lengths) - min(parent_lengths) <= tolerance_mm
        and abs(intervals[0][0]) <= tolerance_mm
        and all(
            abs(left[1] - right[0]) <= tolerance_mm
            for left, right in zip(intervals, intervals[1:])
        )
        and abs(intervals[-1][1] - parent_lengths[0]) <= tolerance_mm
    )
    return {
        "status": "PASS" if valid else "UNVERIFIED_PARENT_CONTEXT_COVERAGE",
        "parent_span_id": contexts[0][0],
        "parent_length_mm": parent_lengths[0],
        "intervals_mm": intervals,
        "tolerance_mm": tolerance_mm,
    }


def _validate_final_line(
    results,
    record_path,
    option,
    parent_groups,
    *,
    external_support_faces_confirmed=False,
    audit=None,
):
    from .constructability import ParentLongitudinalRun, parent_longitudinal_checks
    from .row_candidates import signature
    from .row_solver import _parent_candidate_blockers, _parent_interface_hoops_valid, _support_compatible

    by_index = {record.index: record for record in record_path}
    ordered_indices = sorted(by_index)
    previous_index = None
    previous_result = None
    for index in ordered_indices:
        result = results[index]
        if result is None:
            previous_index = None
            previous_result = None
            continue
        if previous_result is not None and index == previous_index + 1:
            if not any(previous_index in group and index in group for group in parent_groups):
                exact = (
                    option is ContinuityOption.OPTION_1_STRICT_EQUAL
                    or previous_result.section.is_secondary
                    or result.section.is_secondary
                )
                if not _support_compatible(
                    signature(result.regions[0].top),
                    signature(previous_result.regions[-1].top),
                    exact=exact,
                ):
                    raise DetailingError("V3_FINAL_SUPPORT_INTERFACE_CHECK_FAILED")
        previous_index, previous_result = index, result

    pending_parent_proofs = []
    parent_validation_not_verified = []
    for group in parent_groups:
        group_results = tuple(results[index] for index in group)
        if len(group_results) <= 1:
            continue
        if any(result is None for result in group_results):
            parent_validation_not_verified.append({
                "status": "NOT_VERIFIED_INCOMPLETE_PARENT_GROUP",
                "input_indices": list(group),
                "members_present": sum(result is not None for result in group_results),
                "members_expected": len(group),
            })
            continue
        external_scope_checks = []
        if not _parent_interface_hoops_valid(
            group_results,
            external_support_faces_confirmed=external_support_faces_confirmed,
            external_scope_audit=external_scope_checks,
        ):
            raise DetailingError("PARENT_INTERFACE_HOOP_SPACING_FAILED")
        if audit is not None and external_scope_checks:
            audit.setdefault("external_support_scope_checks", []).extend(
                {"parent_group_indices": list(group), **check}
                for check in external_scope_checks
            )
        bottom_signatures = {
            _bar_signature(region.bottom)
            for result in group_results
            for region in result.regions
        }
        hoop_signatures = {
            (region.stirrup.diameter_mm, region.stirrup.legs, region.stirrup.spacing_mm)
            for result in group_results
            for region in result.regions
        }
        if len(bottom_signatures) != 1 or len(hoop_signatures) != 1:
            raise DetailingError("V3_PARENT_COMMON_BOTTOM_OR_HOOP_CHECK_FAILED")
        blockers_by_result = []
        for result in group_results:
            blockers_for_result = set(_parent_candidate_blockers(result))
            missing_face = (
                external_support_faces_confirmed
                and result.section.is_smf
                and result.input_data is not None
                and (result.input_data.left_support is None or result.input_data.right_support is None)
            )
            if missing_face:
                blockers_for_result.discard("SMF_SUPPORT_FACE_UNRESOLVED")
            blockers_by_result.append(blockers_for_result)
        blockers = sorted(set().union(*blockers_by_result))
        if blockers:
            raise DetailingError("PARENT_LONGITUDINAL_OR_CONSTRUCTABILITY_FAILED:" + ",".join(blockers))
        strength_checks = ()
        if group_results[0].section.is_smf:
            runs = tuple(ParentLongitudinalRun(
                result.beam_name,
                result.section.width_mm,
                result.section.fc_mpa,
                result.section.fy_mpa,
                result.regions[0].top,
                result.regions[0].bottom,
                result.regions[1].top,
                result.regions[1].bottom,
                result.regions[2].top,
                result.regions[2].bottom,
            ) for result in group_results)
            checks = parent_longitudinal_checks(runs=runs)
            failures = sorted({
                check.status for check in checks
                if check.status not in {"PASS", "INFO", "WARN"}
            })
            if failures:
                raise DetailingError("V3_PARENT_LONGITUDINAL_STRENGTH_FAILED:" + ",".join(failures))
            strength_checks = tuple(checks)

        context_coverage = _parent_context_coverage_audit(group_results)
        if context_coverage["status"] != "PASS":
            parent_validation_not_verified.append({
                "status": context_coverage["status"],
                "input_indices": list(group),
                "parent_context_coverage": context_coverage,
            })
            continue
        if group_results[0].section.is_smf and not strength_checks:
            parent_validation_not_verified.append({
                "status": "NOT_VERIFIED_PARENT_STRENGTH_CHECKS_MISSING",
                "input_indices": list(group),
            })
            continue

        interface_grid_status = (
            {
                "status": "EXTERNAL_SCOPE",
                "workflow_status": "EXTERNALLY_CONFIRMED",
                "support_face_coordinates_synthesized": False,
                "checks": external_scope_checks,
            }
            if external_scope_checks
            else {
                "status": "PASS" if group_results[0].section.is_smf else "NOT_APPLICABLE",
                "workflow_status": "ENGINE_VALIDATED" if group_results[0].section.is_smf else "ORDINARY_PARENT_NO_SEISMIC_HOOP_GRID",
                "support_face_coordinates_synthesized": False,
                "checks": [],
            }
        )
        bottom_by_member = [
            {
                "input_index": index,
                "beam_name": result.beam_name,
                "regions": tuple((region.name, _bar_signature(region.bottom)) for region in result.regions),
            }
            for index, result in zip(group, group_results)
        ]
        hoops_by_member = [
            {
                "input_index": index,
                "beam_name": result.beam_name,
                "regions": tuple(
                    (
                        region.name,
                        int(region.stirrup.diameter_mm),
                        int(region.stirrup.legs),
                        int(region.stirrup.spacing_mm),
                    )
                    for region in result.regions
                ),
            }
            for index, result in zip(group, group_results)
        ]
        independent_reviews = [
            {
                "beam_name": result.beam_name,
                "items": sorted(set(result.errors) - blockers_by_result[position]),
            }
            for position, result in enumerate(group_results)
        ]
        proof = {
            "proof_id": "V3_PARENT_FINAL_VALIDATION",
            "status": "PASS_WITH_EXTERNAL_SCOPE" if external_scope_checks else "PASS",
            "input_indices": list(group),
            "members_expected": len(group),
            "members_present": len(group_results),
            "parent_context_coverage": context_coverage,
            "bottom_signature_by_member": bottom_by_member,
            "hoop_signature_by_member": hoops_by_member,
            "checks": {
                "parent_bottom_uniformity": "PASS",
                "parent_hoop_uniformity": "PASS",
                "parent_interface_hoop_grid": interface_grid_status,
                "blocking_constructability": {
                    "status": "PASS",
                    "blockers_by_member": [
                        {"beam_name": result.beam_name, "blockers": []}
                        for result in group_results
                    ],
                },
                "independent_review_items": independent_reviews,
                "parent_longitudinal_strength": (
                    {
                        "status": "PASS",
                        "checks": [
                            {
                                "rule_id": check.rule_id,
                                "clause": check.clause,
                                "status": check.status,
                                "actual": check.actual,
                                "limit": check.limit,
                                "message": check.message,
                                "source": check.source,
                            }
                            for check in strength_checks
                        ],
                    }
                    if group_results[0].section.is_smf
                    else {"status": "NOT_APPLICABLE", "reason": "NON_SMF_PARENT"}
                ),
                "line_level_pb_top_row_diameter_vector": (
                    "PASS"
                    if any(not result.section.is_secondary for result in group_results)
                    else "NOT_APPLICABLE"
                ),
            },
        }
        checks_by_member = {
            index: tuple(check for check in strength_checks if check.source == result.beam_name)
            for index, result in zip(group, group_results)
        }
        pending_parent_proofs.append((tuple(group), proof, checks_by_member))

    # PB row diameter is an all-position, all-span physical invariant. Verify
    # the final built rows after geometry resolution and finalization.
    pb_values: list[int | None] = [None, None, None]
    for index in ordered_indices:
        result = results[index]
        if result is None or result.section.is_secondary:
            continue
        for region in result.regions:
            for row_index, row in enumerate(region.top.rows):
                if row.role == "hanger":
                    continue
                if row_index >= len(pb_values):
                    raise DetailingError("V3_PB_TOP_ROW_LIMIT_EXCEEDED")
                prior = pb_values[row_index]
                if prior is not None and prior != row.diameter_mm:
                    raise DetailingError("V3_PB_TOP_ROW_DIAMETER_VECTOR_CHANGED")
                pb_values[row_index] = row.diameter_mm

    # Publish parent proofs only after every parent check and the line-wide PB
    # row-diameter invariant have passed for this materialized candidate.
    for group, proof, checks_by_member in pending_parent_proofs:
        if audit is not None:
            audit.setdefault("parent_final_validation", []).append(proof)
        for index in group:
            result = results[index]
            results[index] = replace(
                result,
                coordination_status="PARENT_COORDINATION_VERIFIED",
                coordination_audit=(*result.coordination_audit, proof),
                smf_checks=(*result.smf_checks, *checks_by_member[index]),
            )
    if audit is not None and parent_validation_not_verified:
        audit.setdefault("parent_final_validation_not_verified", []).extend(
            parent_validation_not_verified
        )


def _normalize_parent_groups(parent_groups, count_inputs):
    if parent_groups is None:
        return ()
    source = parent_groups.values() if isinstance(parent_groups, Mapping) else parent_groups
    groups = []
    used = set()
    for raw in source:
        values = tuple(int(index) for index in raw)
        if not values:
            continue
        if any(index < 0 or index >= count_inputs or index in used for index in values):
            raise ValueError("PARENT_GROUP_INDEX_INVALID")
        if tuple(sorted(values)) != values:
            raise ValueError("PARENT_GROUP_ORDER_INVALID")
        used.update(values)
        groups.append(values)
    return tuple(sorted(groups, key=lambda item: item[0]))


def design_line_v3(
    inputs: Sequence[BeamDetailingInput | None],
    parent_groups,
    option: ContinuityOption,
    engine: BeamDetailingEngine,
    *,
    k: int = 10,
    _catalog_tier: str = "preferred",
    _deadline: float | None = None,
    _local_budget_remaining: int | None = None,
    _materialization_budget_remaining: int | None = None,
    research_actual_parent_hoop_signatures: bool = False,
    external_support_faces_confirmed: bool = False,
    coverage_schedule: bool = False,
) -> V3LineRecommendationSet:
    """Find and rank up to ``k`` complete beam-line recommendations.

    Local search is heuristic and bounded. Failure to populate the returned
    set is explicitly not a proof of hard infeasibility.
    """
    if engine.detailing_policy_id != DRAWING_V3_POLICY_ID or \
            engine.detailing_policy_revision_id not in DRAWING_V3_SUPPORTED_REVISION_IDS or \
            engine.coordination_solver_revision_id != DRAWING_V3_SOLVER_REVISION_ID:
        raise DetailingError("V3_ENGINE_REVISION_REQUIRED")
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= 10:
        raise ValueError("V3 recommendation limit k must be between 1 and 10")
    from .v3_hard_checks import v3_hard_check_cache_statistics

    hard_check_cache_before = v3_hard_check_cache_statistics(engine)
    materialization_budget = (
        k if _materialization_budget_remaining is None
        else max(0, min(k, int(_materialization_budget_remaining)))
    )
    parent_groups = _normalize_parent_groups(parent_groups, len(inputs))
    audit: dict[str, Any] = {
        "policy_id": DRAWING_V3_POLICY_ID,
        "policy_revision_id": engine.detailing_policy_revision_id,
        "solver_revision_id": DRAWING_V3_SOLVER_REVISION_ID,
        "research_actual_parent_hoop_signatures": bool(research_actual_parent_hoop_signatures),
        "external_support_faces_confirmed": bool(external_support_faces_confirmed),
        "support_face_coordinate_scope": (
            "EXTERNAL_SCOPE_COORDINATES_RETAINED_NONE"
            if external_support_faces_confirmed else "ENGINE_INPUT"
        ),
        "support_face_coordinates_synthesized": False,
        "external_support_scope_checks": [],
        "recommendation_scope": "RECOMMENDATION_SET_ONLY",
        "global_optimality_claimed": False,
        "full_specification_compliance_claimed": False,
        "hard_infeasible": False,
        "candidate_limit": k,
        "candidate_count": 0,
        "materialization_budget": materialization_budget,
        "complete_materializations": 0,
        "partial_materialization_attempts": 0,
        "complete_validation_calls": 0,
        "complete_validation_passes": 0,
        "partial_validation_calls": 0,
        "partial_validation_passes": 0,
        "duplicate_physical_schemes_discarded": 0,
        "span_result_builds": 0,
        "local_combo_checks": 0,
        "local_candidate_tuple_combinations": 0,
        "local_interface_representatives": 0,
        "local_interface_blocks_skipped_after_coverage": 0,
        "local_records_seen": 0,
        "interface_state_count": 0,
        "parent_shared_state_count": 0,
        "parent_interface_candidates_skipped_after_coverage": 0,
        "parent_join_combinations": 0,
        "parent_longitudinal_checks": 0,
        "backward_pruned": 0,
        "interface_class_count": 0,
        "candidate_lookup_mode": getattr(engine, "candidate_lookup_mode", "auto"),
        "candidate_lookup_before": dict(getattr(engine.candidate_lookup_provider, "query_audit", {})),
        "truncated": False,
        "stop_reason": None,
        "time_budget_seconds": V3_LINE_SEARCH_SECONDS,
    }
    if coverage_schedule:
        audit["solver_revision_id"] = V3_COVERAGE_SOLVER_REVISION_ID
        audit["coverage_schedule"] = {
            "enabled": True,
            "experimental_revision_id": V3_COVERAGE_SOLVER_REVISION_ID,
            "baseline_solver_revision_id": engine.coordination_solver_revision_id,
            "mode": "PARENT_ACTUAL_MN_GUIDED_JOIN",
            "parent_longitudinal_check_calls": 0,
            "parent_longitudinal_check_passes": 0,
            "parent_longitudinal_failure_histogram": {},
            "parent_longitudinal_failure_examples": {},
            "parent_strength_bound_prunes": 0,
            "parent_unit_options": 0,
        }
    errors: list[str | None] = [None] * len(inputs)
    normalized_inputs: list[BeamDetailingInput | None] = []
    for index, data in enumerate(inputs):
        if data is None:
            errors[index] = "INPUT_MISSING_CONTINUITY_BREAK"
            normalized_inputs.append(None)
        elif data.section.detailing_profile is BeamDetailingProfile.UNRESOLVED:
            errors[index] = "DETAILING_PROFILE_REVIEW_REQUIRED"
            normalized_inputs.append(None)
        else:
            normalized_inputs.append(engine._etabs_only_input(data))
    incomplete_parent_groups = tuple(
        group for group in parent_groups
        if any(normalized_inputs[index] is None for index in group)
    )
    input_gap_indices = {
        index for index, data in enumerate(normalized_inputs) if data is None
    }
    incomplete_parent_indices = {index for group in incomplete_parent_groups for index in group}
    complete_parent_groups = tuple(group for group in parent_groups if group not in incomplete_parent_groups)
    group_for_index = {index: group for group in complete_parent_groups for index in group}
    for index in incomplete_parent_indices:
        if errors[index] is None:
            errors[index] = "PARENT_COORDINATION_INCOMPLETE_MISSING_SUBSPAN"

    deadline = _deadline if _deadline is not None else time.monotonic() + V3_LINE_SEARCH_SECONDS
    local_budget = (
        V3_LOCAL_STATE_LIMIT if _local_budget_remaining is None
        else max(0, int(_local_budget_remaining))
    )
    records_by_index: dict[int, tuple[_SpanRecord, ...]] = {}
    active_input_count = max(1, sum(data is not None for data in normalized_inputs))
    local_budget_per_input = max(0, local_budget // active_input_count)
    preferred_main = tuple(
        (16 if data.section.is_secondary else 20)
        for data in normalized_inputs if data is not None
    )
    preferred_main_catalog = tuple(dict.fromkeys(preferred_main))
    preferred_stirrups = (10,)
    if _catalog_tier not in {"preferred", "full"}:
        raise ValueError("V3 catalog tier must be preferred or full")
    main_catalog = preferred_main_catalog if _catalog_tier == "preferred" else None
    stirrup_catalog = preferred_stirrups if _catalog_tier == "preferred" else None

    def expand_preferred_stage(reason, *, details=None):
        summary = {
            "preferred_main_diameters": list(preferred_main_catalog),
            "preferred_stirrup_diameters": list(preferred_stirrups),
            "preferred_search_truncated": bool(audit["truncated"]),
            "preferred_recommendations": 0,
            "expanded_to_full_catalog": True,
            "preferred_feasibility_status": reason,
            "global_infeasibility_proven": False,
            "preferred_local_combo_checks": audit["local_combo_checks"],
            "preferred_local_search_by_input": audit.get("local_search_by_input", {}),
            "preferred_local_rejection_histogram": audit.get("local_rejection_histogram", {}),
            "preferred_materialization_attempts": (
                audit["complete_materializations"]
                + audit["partial_materialization_attempts"]
            ),
            "preferred_materialization_rejections": len(
                audit.get("materialization_rejections", ())
            ),
        }
        if details is not None:
            summary.update(details)
        fallback = design_line_v3(
            inputs, parent_groups, option, engine, k=k,
            research_actual_parent_hoop_signatures=research_actual_parent_hoop_signatures,
            external_support_faces_confirmed=external_support_faces_confirmed,
            coverage_schedule=coverage_schedule,
            _catalog_tier="full", _deadline=deadline,
            _local_budget_remaining=max(0, local_budget - audit["local_combo_checks"]),
            _materialization_budget_remaining=max(
                0, materialization_budget
                - audit["complete_materializations"]
                - audit["partial_materialization_attempts"]
            ),
        )
        _merge_preference_stage_audit(
            fallback, audit, candidate_limit=k, summary=summary
        )
        return _attach_hard_check_cache_delta(
            fallback, engine, hard_check_cache_before
        )

    precomputed_preferred_hoops: dict[int, tuple] = {}
    if _catalog_tier == "preferred":
        preflight_blockers = []
        for index, data in enumerate(normalized_inputs):
            if data is None:
                continue
            blocker = _preferred_t10_static_blocker(data, engine)
            if blocker is not None:
                preflight_blockers.append({
                    "input_index": index,
                    "reason": blocker,
                    "scope": "STATIC_STIRRUP_SUPPLY",
                })
                continue
            try:
                applicable = engine.offline_style_library.applicable_fields(data, engine)
                hoop_states = _v3_hoop_states(
                    data, engine, applicable, allowed_stirrup_diameters=(10,)
                )
            except (DetailingError, ValueError) as exc:
                audit.setdefault("preferred_preflight_unknown", []).append({
                    "input_index": index,
                    "reason": str(exc),
                })
                continue
            if not hoop_states:
                preflight_blockers.append({
                    "input_index": index,
                    "reason": "PREFERRED_T10_NO_HARD_VALID_HOOP_STATE",
                    "scope": "EXHAUSTIVE_LOCAL_HOOP_DIRECTORY",
                })
            else:
                precomputed_preferred_hoops[index] = hoop_states
        audit["preferred_directory_preflight"] = {
            "checked_input_indices": sorted(precomputed_preferred_hoops),
            "blockers": preflight_blockers,
            "global_infeasibility_proven": False,
        }
        if preflight_blockers:
            return expand_preferred_stage(
                "PREFERRED_LOCAL_DIRECTORY_EMPTY_FOR_REQUIRED_INPUT",
                details={
                    "preferred_directory_preflight": audit["preferred_directory_preflight"],
                    "preferred_blocked_input_indices": [
                        item["input_index"] for item in preflight_blockers
                    ],
                },
            )

    group_bottom_required = {
        index: max(
            region.as_bottom_total_mm2
            for member in group
            for region in (normalized_inputs[member].regions if normalized_inputs[member] else ())
        )
        for group in parent_groups
        if group in complete_parent_groups
        for index in group
    }
    coverage_parent_targets = {}
    if coverage_schedule:
        seen_parent_geometries = set()
        target_queries = audit.setdefault("coverage_schedule", {}).setdefault(
            "parent_state_target_queries", []
        )
        for group in complete_parent_groups:
            if len(group) < 2:
                continue
            geometry_key = tuple(sorted((
                float(normalized_inputs[index].section.width_mm),
                float(normalized_inputs[index].section.depth_mm),
                float(normalized_inputs[index].section.fc_mpa),
                float(normalized_inputs[index].section.fy_mpa),
                float(normalized_inputs[index].section.etabs_longitudinal_cover_mm),
                float(normalized_inputs[index].section.clear_cover_to_stirrup_outer_mm),
                str(normalized_inputs[index].section.detailing_profile.value),
                str(normalized_inputs[index].section.beam_role),
            ) for index in group))
            if geometry_key not in seen_parent_geometries:
                seen_parent_geometries.add(geometry_key)
                continue
            allowed_main_by_index = {
                index: (
                    tuple(engine.project.ordered_longitudinal_diameters_for(
                        normalized_inputs[index].section
                    ))
                    if main_catalog is None else
                    (16 if normalized_inputs[index].section.is_secondary else 20,)
                )
                for index in group
            }
            target = _coverage_parent_state_target(
                group,
                normalized_inputs,
                engine,
                bottom_required=group_bottom_required[group[0]],
                allowed_main_by_index=allowed_main_by_index,
                allowed_stirrup_diameters=stirrup_catalog,
                deadline=deadline,
            )
            if target is None:
                target_queries.append({
                    "input_indices": list(group),
                    "status": "NO_TARGET_FOUND_OR_DEADLINE",
                    "hard_infeasible": False,
                })
                continue
            target_queries.append({"status": "TARGET_FOUND", **target["audit"]})
            for index in group:
                coverage_parent_targets[index] = target

    for index, data in enumerate(normalized_inputs):
        if data is None:
            continue
        parent_scope = len(group_for_index.get(index, (index,))) > 1
        bottom_required = group_bottom_required.get(
            index,
            max(region.as_bottom_total_mm2 for region in data.regions),
        )
        allowed_main = (
            tuple(engine.project.ordered_longitudinal_diameters_for(data.section))
            if main_catalog is None else (16 if data.section.is_secondary else 20,)
        )
        hoop_states = (
            precomputed_preferred_hoops.get(index)
            if _catalog_tier == "preferred" else None
        )
        try:
            if not coverage_schedule:
                records = _local_records(
                    index,
                    data,
                    engine,
                    bottom_required=bottom_required,
                    deadline=deadline,
                    budget=local_budget_per_input,
                    audit=audit,
                    allowed_main_diameters=allowed_main,
                    allowed_stirrup_diameters=stirrup_catalog,
                    representatives_per_interface=(None if parent_scope else k),
                    precomputed_hoop_states=hoop_states,
                    research_actual_parent_hoop_signatures=research_actual_parent_hoop_signatures,
                )
            else:
                input_start = audit["local_combo_checks"]
                seen_tuples = set()
                target = coverage_parent_targets.get(index)
                target_records = ()
                target_runs = []
                target_axis_cache = None
                if target is not None:
                    target_state = (target["hoop_key"], target["bottom_signature"])
                    target_hoops = target["hoop_states"][index]
                    target_axis_cache = {
                        target["hoop_key"]: target["axis_candidates"][index],
                    }
                    target_records = _local_records(
                        index,
                        data,
                        engine,
                        bottom_required=bottom_required,
                        deadline=deadline,
                        budget=min(local_budget_per_input, 500),
                        audit=audit,
                        allowed_main_diameters=allowed_main,
                        allowed_stirrup_diameters=stirrup_catalog,
                        representatives_per_interface=1,
                        precomputed_hoop_states=(target_hoops,),
                        precomputed_axis_candidates=target_axis_cache,
                        target_parent_state=target_state,
                        coverage_schedule=True,
                        coverage_parent_state_scope=parent_scope,
                        coverage_seen_tuples=seen_tuples,
                        research_actual_parent_hoop_signatures=research_actual_parent_hoop_signatures,
                    )
                    target_runs.append(dict(
                        audit.get("local_search_by_input", {}).get(str(index), {})
                    ))
                used = audit["local_combo_checks"] - input_start
                remaining = max(0, local_budget_per_input - used)
                search_hoop_states = hoop_states
                if target is not None:
                    search_hoop_states = tuple(dict.fromkeys((
                        *(hoop_states or ()),
                        target["hoop_states"][index],
                    )))
                if remaining:
                    first_records = _local_records(
                        index,
                        data,
                        engine,
                        bottom_required=bottom_required,
                        deadline=deadline,
                        budget=remaining,
                        audit=audit,
                        allowed_main_diameters=allowed_main,
                        allowed_stirrup_diameters=stirrup_catalog,
                        representatives_per_interface=1,
                        precomputed_hoop_states=search_hoop_states,
                        precomputed_axis_candidates=target_axis_cache,
                        coverage_schedule=True,
                        coverage_parent_state_scope=parent_scope,
                        coverage_seen_tuples=seen_tuples,
                        coverage_existing_records=target_records,
                        research_actual_parent_hoop_signatures=research_actual_parent_hoop_signatures,
                    )
                    first_run = dict(audit.get("local_search_by_input", {}).get(str(index), {}))
                else:
                    first_records = target_records
                    first_run = None
                used = audit["local_combo_checks"] - input_start
                remaining = max(0, local_budget_per_input - used)
                if remaining:
                    records = _local_records(
                        index,
                        data,
                        engine,
                        bottom_required=bottom_required,
                        deadline=deadline,
                        budget=remaining,
                        audit=audit,
                        allowed_main_diameters=allowed_main,
                        allowed_stirrup_diameters=stirrup_catalog,
                        representatives_per_interface=(None if parent_scope else k),
                        precomputed_hoop_states=search_hoop_states,
                        precomputed_axis_candidates=target_axis_cache,
                        coverage_schedule=True,
                        coverage_parent_state_scope=parent_scope,
                        coverage_seen_tuples=seen_tuples,
                        coverage_existing_records=first_records,
                        research_actual_parent_hoop_signatures=research_actual_parent_hoop_signatures,
                    )
                    second_run = dict(audit.get("local_search_by_input", {}).get(str(index), {}))
                else:
                    records = first_records
                    second_run = None
                runs = [*target_runs, *([first_run] if first_run is not None else []),
                        *([second_run] if second_run is not None else [])]
                input_checks = audit["local_combo_checks"] - input_start
                input_tuples = sum(int(run.get("candidate_tuple_combinations", 0)) for run in runs)
                input_truncated = any(bool(run.get("truncated")) for run in runs)
                terminal_blocker = next((
                    run.get("terminal_blocker") for run in runs
                    if isinstance(run.get("terminal_blocker"), Mapping)
                ), None)
                summary = {
                    "local_combo_checks": input_checks,
                    "candidate_tuple_combinations": input_tuples,
                    "hard_valid_records": len(records),
                    "truncated": input_truncated,
                    "stop_reason": (
                        "LOCAL_TIME_OR_BUDGET_EXHAUSTED" if input_truncated else
                        "COVERAGE_AND_FILL_COMPLETE"
                    ),
                    **({"terminal_blocker": terminal_blocker} if terminal_blocker else {}),
                }
                audit.setdefault("coverage_schedule", {}).setdefault(
                    "local_search_by_input", {}
                )[str(index)] = {
                    "budget_limit": local_budget_per_input,
                    "first_pass_records": len(first_records),
                    "added_records": max(0, len(records) - len(first_records)),
                    "target_scope": (
                        "JOINT_PARENT_STATE_THEN_PREFERRED_POOL"
                        if target is not None else
                        "PARENT_STATE_PLUS_FUTURE_INTERFACE"
                        if parent_scope else "FUTURE_INTERFACE"
                    ),
                    **({
                        "selected_parent_target": {
                            "hoop_signature": [list(item) for item in target["hoop_key"]],
                            "bottom_signature": [list(item) for item in target["bottom_signature"]],
                        }
                    } if target is not None else {}),
                    "runs": runs,
                }
                audit.setdefault("local_search_by_input", {})[str(index)] = summary
        except (DetailingError, ValueError) as exc:
            records = ()
            errors[index] = str(exc)
        records_by_index[index] = records
        if not records and errors[index] is None:
            local_search = audit.get("local_search_by_input", {}).get(str(index), {})
            terminal_blocker = local_search.get("terminal_blocker")
            errors[index] = (
                str(terminal_blocker.get("reason"))
                if isinstance(terminal_blocker, Mapping) and terminal_blocker.get("reason")
                else "NO_LOCAL_INTERFACE_RECORD_WITHIN_BUDGET"
                if local_search.get("truncated")
                else "NO_LOCAL_INTERFACE_RECORD_AFTER_EXHAUSTIVE_SEARCH"
            )
            if (
                _catalog_tier == "preferred"
                and not local_search.get("truncated")
                and not any(data is None for data in normalized_inputs)
                and not incomplete_parent_groups
            ):
                return expand_preferred_stage(
                    "PREFERRED_LOCAL_DIRECTORY_EMPTY_FOR_REQUIRED_INPUT",
                    details={
                        "preferred_empty_input_index": index,
                        "preferred_empty_input_reason": errors[index],
                        "preferred_empty_input_local_search": local_search,
                    },
                )

    # A source-valid span with an empty local directory is a known design gap.
    # It breaks line coordination without being relabelled as missing input.
    record_gap_indices = {
        index for index, data in enumerate(normalized_inputs)
        if data is not None and not records_by_index.get(index)
    }
    audit["unsolved_span_indices"] = sorted(record_gap_indices)
    audit["parent_groups_with_unresolved_local_child"] = []
    failed_parent_groups = tuple(
        group for group in complete_parent_groups
        if len(group) > 1 and any(index in record_gap_indices for index in group)
    )
    if failed_parent_groups:
        audit["parent_groups_with_unresolved_local_child"] = [
            list(group) for group in failed_parent_groups
        ]
        incomplete_parent_groups = tuple(sorted(
            {*incomplete_parent_groups, *failed_parent_groups}, key=lambda group: group[0]
        ))
        incomplete_parent_indices = {
            index for group in incomplete_parent_groups for index in group
        }
        complete_parent_groups = tuple(
            group for group in complete_parent_groups if group not in failed_parent_groups
        )
        group_for_index = {
            index: group for group in complete_parent_groups for index in group
        }
        for group in failed_parent_groups:
            for index in group:
                if index not in record_gap_indices and errors[index] is None:
                    errors[index] = "PARENT_COORDINATION_INCOMPLETE_FAILED_SUBSPAN"
    audit["parent_coordination_unknown_groups"] = [
        list(group) for group in incomplete_parent_groups if len(group) > 1
    ]

    # Missing inputs, unsolved spans, and incomplete parents break topology.
    # Valid children of an incomplete parent remain available as independent
    # local selections and keep an explicit unknown-parent status.
    components: list[tuple[int, ...]] = []
    current: list[int] = []
    for index, data in enumerate(normalized_inputs):
        if data is None:
            if current:
                components.append(tuple(current))
                current = []
            continue
        if index in record_gap_indices:
            if current:
                components.append(tuple(current))
                current = []
            continue
        if index in incomplete_parent_indices:
            if current:
                components.append(tuple(current))
                current = []
            components.append((index,))
            continue
        if current and index != current[-1] + 1:
            components.append(tuple(current))
            current = []
        current.append(index)
    if current:
        components.append(tuple(current))

    successful_component_paths = []
    failed_components = []
    for component in components:
        component_units = []
        cursor = 0
        while cursor < len(component):
            index = component[cursor]
            group = group_for_index.get(index)
            unit_indices = group if group is not None else (index,)
            if any(member not in component for member in unit_indices):
                errors[index] = "PARENT_GROUP_NOT_CONTIGUOUS_WITH_INPUT_SEGMENT"
                component_units = []
                break
            unit_options = _unit_options(
                unit_indices,
                records_by_index,
                option=option,
                engine=engine,
                limit=max(100, local_budget),
                audit=audit,
                representatives_per_interface=k,
                **({
                    "coverage_schedule": True,
                    "coverage_audit": audit["coverage_schedule"],
                } if coverage_schedule else {}),
            )
            if not unit_options:
                for member in unit_indices:
                    if errors[member] is None:
                        errors[member] = "NO_PARENT_OR_SPAN_INTERFACE_OPTION_WITHIN_BUDGET"
                component_units = []
                break
            data_group = tuple(normalized_inputs[member] for member in unit_indices)
            component_units.append((unit_indices, unit_options, data_group))
            cursor += len(unit_indices)
        if not component_units:
            failed_components.append(component)
            continue
        paths = _coordinate_units(component_units, option, engine, k=k, audit=audit)
        if not paths:
            for index in component:
                if errors[index] is None:
                    errors[index] = "NO_REACHABLE_COMPLETE_INTERFACE_PATH_WITHIN_BUDGET"
            failed_components.append(component)
            continue
        successful_component_paths.append(paths)

    incomplete = (
        bool(input_gap_indices)
        or bool(record_gap_indices)
        or bool(incomplete_parent_groups)
        or bool(failed_components)
    )
    audit["partial_components"] = [list(component) for component in failed_components]
    audit["solved_components"] = [
        list(component) for component in components if component not in failed_components
    ]
    audit["continuity_unknown_due_to_missing_input"] = bool(input_gap_indices)
    audit["continuity_unknown_due_to_unsolved_span"] = bool(record_gap_indices)
    if not successful_component_paths:
        audit["stop_reason"] = "NO_RECOMMENDATION_FOUND_WITHIN_BUDGET"
        audit["candidate_lookup_after"] = dict(getattr(engine.candidate_lookup_provider, "query_audit", {}))
        if (
            _catalog_tier == "preferred"
            and not any(data is None for data in normalized_inputs)
            and not incomplete_parent_groups
            and not audit["truncated"]
        ):
            fallback = design_line_v3(
                inputs, parent_groups, option, engine, k=k,
                research_actual_parent_hoop_signatures=research_actual_parent_hoop_signatures,
                external_support_faces_confirmed=external_support_faces_confirmed,
                coverage_schedule=coverage_schedule,
                _catalog_tier="full", _deadline=deadline,
                _local_budget_remaining=max(0, local_budget - audit["local_combo_checks"]),
                _materialization_budget_remaining=max(
                    0, materialization_budget
                    - audit["complete_materializations"]
                    - audit["partial_materialization_attempts"]
                ),
            )
            _merge_preference_stage_audit(fallback, audit, candidate_limit=k, summary={
                "preferred_main_diameters": list(preferred_main_catalog),
                "preferred_stirrup_diameters": list(preferred_stirrups),
                "preferred_search_truncated": False,
                "preferred_recommendations": 0,
                "expanded_to_full_catalog": True,
                "preferred_feasibility_status": "NO_PATH_FOUND_WITHIN_EXHAUSTIVE_BOUNDED_SEARCH",
                "global_infeasibility_proven": False,
                "preferred_local_combo_checks": audit["local_combo_checks"],
                "preferred_local_search_by_input": audit.get("local_search_by_input", {}),
                "preferred_local_rejection_histogram": audit.get("local_rejection_histogram", {}),
                "preferred_materialization_attempts": 0,
                "preferred_materialization_rejections": 0,
            })
            return _attach_hard_check_cache_delta(fallback, engine, hard_check_cache_before)
        empty = tuple(None for _ in inputs)
        return _attach_hard_check_cache_delta(V3LineRecommendationSet(
            status=("CONTINUITY_UNKNOWN_PARTIAL_SEGMENTS" if incomplete
                    else "NO_RECOMMENDATION_FOUND_WITHIN_BUDGET"),
            selected=empty,
            alternatives=(),
            errors=tuple(errors),
            audit=audit,
        ), engine, hard_check_cache_before)

    # Components separated by missing input are independently searched but
    # remain explicit None breaks in every returned aligned sequence.
    candidate_combinations: list[tuple[tuple[tuple[_UnitOption, ...], ...], tuple[int | None, ...]]] = [((), ())]
    cross_component_pb_vector_conflicts = 0
    for paths in successful_component_paths:
        next_combinations = []
        for prefix, prefix_vector in candidate_combinations:
            for path in paths:
                path_vector: tuple[int | None, ...] = ()
                for unit in path:
                    path_vector = _merge_pb_vector(path_vector, unit.pb_row_diameters)
                    if path_vector is None:
                        break
                if path_vector is None:
                    continue
                merged_vector = _merge_pb_vector(prefix_vector, path_vector)
                if merged_vector is None:
                    cross_component_pb_vector_conflicts += 1
                    continue
                next_combinations.append(((*prefix, path), merged_vector))
        next_combinations.sort(key=lambda item: tuple(_path_key(path) for path in item[0]))
        candidate_combinations = next_combinations[:max(k * 4, k)]

    audit["cross_component_pb_vector_conflicts"] = cross_component_pb_vector_conflicts
    if successful_component_paths and not candidate_combinations and cross_component_pb_vector_conflicts:
        for component in components:
            for index in component:
                if errors[index] is None:
                    errors[index] = "PB_ROW_DIAMETER_CONFLICT_ACROSS_SOLVED_COMPONENTS"

    materialized: list[tuple[tuple[Any, ...], tuple[BeamDetailingResult | None, ...]]] = []
    candidate_combinations = [item[0] for item in candidate_combinations]
    candidate_paths_available = len(candidate_combinations)
    audit["candidate_path_pool_size"] = candidate_paths_available
    for candidate_index, component_paths in enumerate(
        candidate_combinations[:materialization_budget], start=1
    ):
        if time.monotonic() >= deadline:
            audit["truncated"] = True
            audit["stop_reason"] = "TIME_BUDGET_EXHAUSTED"
            break
        if incomplete:
            audit["partial_materialization_attempts"] += 1
        else:
            audit["complete_materializations"] += 1
        aligned: list[BeamDetailingResult | None] = [None] * len(inputs)
        failed = False
        terminal_signature_evidence = None
        try:
            records = _validate_full_path(
                component_paths,
                option,
                complete_parent_groups,
                engine,
            )
            for record in records:
                audit["span_result_builds"] += 1
                aligned[record.index] = _materialize_record(
                    record,
                    option,
                    engine,
                    search_index=candidate_index,
                    parent_unknown=(
                        "V3_PARENT_COORDINATION_UNKNOWN_FAILED_SUBSPAN"
                        if record.index in incomplete_parent_indices
                        and any(record.index in group for group in failed_parent_groups)
                        else record.index in incomplete_parent_indices
                    ),
                )
            if incomplete:
                audit["partial_validation_calls"] += 1
            else:
                audit["complete_validation_calls"] += 1
            if research_actual_parent_hoop_signatures:
                terminal_signature_evidence = _terminal_parent_signature_evidence(
                    candidate_index,
                    aligned,
                    records,
                    complete_parent_groups,
                )
            _validate_final_line(
                aligned,
                records,
                option,
                complete_parent_groups,
                external_support_faces_confirmed=external_support_faces_confirmed,
                audit=audit,
            )
            if terminal_signature_evidence is not None:
                terminal_signature_evidence["terminal_validation"] = "PASS"
                audit.setdefault("research_terminal_actual_parent_signatures", []).append(
                    terminal_signature_evidence
                )
            if incomplete:
                audit["partial_validation_passes"] += 1
            else:
                audit["complete_validation_passes"] += 1
            from .row_solver import _style_path_rank

            full_results = tuple(result for result in aligned if result is not None)
            rank = (
                _combine_preference_tiers(record.preference_tier for record in records),
                _style_path_rank(full_results, engine),
            )
            materialized.append((rank, tuple(aligned)))
        except (DetailingError, ValueError) as exc:
            audit.setdefault("materialization_rejections", []).append(str(exc))
            if terminal_signature_evidence is not None:
                terminal_signature_evidence["terminal_validation"] = "REJECTED"
                terminal_signature_evidence["rejection"] = str(exc)
                audit.setdefault("research_terminal_actual_parent_signatures", []).append(
                    terminal_signature_evidence
                )
            failed = True
        if failed:
            continue
    materialized, _duplicate_physical_schemes_discarded = _deduplicate_ranked_materialized(
        materialized, audit
    )
    alternatives = tuple(item[1] for item in materialized[:k])
    if len(candidate_combinations) > materialization_budget and not alternatives:
        audit["truncated"] = True
        audit["stop_reason"] = "COMPLETE_PATH_MATERIALIZATION_LIMIT_REACHED"
    audit["candidate_lookup_after"] = dict(getattr(engine.candidate_lookup_provider, "query_audit", {}))
    audit["complete_recommendations"] = len(alternatives) if not incomplete else 0
    audit["materialized_aligned_selections"] = len(alternatives)
    audit["candidate_count"] = len(alternatives)
    audit["reachable_path_labels"] = sum(len(paths) for paths in successful_component_paths)
    if audit["truncated"]:
        audit["stop_reason"] = audit.get("stop_reason") or "LOCAL_INTERFACE_BUDGET_EXHAUSTED"
    else:
        audit["stop_reason"] = "RECOMMENDATION_SET_LIMIT_OR_CATALOG_EXHAUSTED"
    status = "CONTINUITY_UNKNOWN_PARTIAL_SEGMENTS" if incomplete else (
        "RECOMMENDATION_SET_ONLY" if alternatives else "NO_RECOMMENDATION_FOUND_WITHIN_BUDGET"
    )
    if not alternatives:
        selected = tuple(None for _ in inputs)
    else:
        selected = alternatives[0]
    if _catalog_tier == "preferred":
        audit["preference_tier_search"] = {
            "preferred_main_diameters": list(preferred_main_catalog),
            "preferred_stirrup_diameters": list(preferred_stirrups),
            "preferred_search_truncated": bool(audit["truncated"]),
            "preferred_recommendations": len(alternatives),
            "expanded_to_full_catalog": False,
            "preferred_feasibility_status": (
                "PREFERRED_PATHS_FOUND" if alternatives else
                "SEARCH_TRUNCATED" if audit["truncated"] else
                "PATHS_FOUND_BUT_NO_VALID_MATERIALIZED_RECOMMENDATION"
                if candidate_paths_available else
                "NO_PATH_FOUND_WITHIN_EXHAUSTIVE_BOUNDED_SEARCH"
            ),
            "global_infeasibility_proven": False,
        }
        if (
            not alternatives
            and not incomplete
            and not audit["truncated"]
            and candidate_paths_available == 0
        ):
            fallback = design_line_v3(
                inputs, parent_groups, option, engine, k=k,
                research_actual_parent_hoop_signatures=research_actual_parent_hoop_signatures,
                external_support_faces_confirmed=external_support_faces_confirmed,
                coverage_schedule=coverage_schedule,
                _catalog_tier="full", _deadline=deadline,
                _local_budget_remaining=max(0, local_budget - audit["local_combo_checks"]),
                _materialization_budget_remaining=max(
                    0, materialization_budget
                    - audit["complete_materializations"]
                    - audit["partial_materialization_attempts"]
                ),
            )
            _merge_preference_stage_audit(fallback, audit, candidate_limit=k, summary={
                **audit["preference_tier_search"],
                "expanded_to_full_catalog": True,
                "preferred_local_combo_checks": audit["local_combo_checks"],
                "preferred_materialization_attempts": (
                    audit["complete_materializations"]
                    + audit["partial_materialization_attempts"]
                ),
                "preferred_materialization_rejections": len(
                    audit.get("materialization_rejections", ())
                ),
                "preferred_local_search_by_input": audit.get("local_search_by_input", {}),
                "preferred_local_rejection_histogram": audit.get("local_rejection_histogram", {}),
            })
            return _attach_hard_check_cache_delta(fallback, engine, hard_check_cache_before)
    return _attach_hard_check_cache_delta(V3LineRecommendationSet(
        status=status,
        selected=selected,
        alternatives=alternatives,
        errors=tuple(errors),
        audit=audit,
    ), engine, hard_check_cache_before)
