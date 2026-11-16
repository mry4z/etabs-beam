"""Finite, geometrically verified physical-row catalogue (no mixed groups)."""
import math
from dataclasses import replace
from itertools import permutations
from .detailing import LongitudinalDesign, PhysicalRow, DetailingError


_LAYOUT_CACHE_MISS = object()
R6_GEOMETRY_CACHE_MAX_ENTRIES = 4096


def signature(design):
    return tuple((row.count, row.diameter_mm) for row in design.rows)


def _resolve_with_geometry_cache(section, hoop, design, engine, *, face,
                                 geometry_cache, search_stats):
    """Reuse only exact physical-layout resolutions in the active R6 span."""

    scope = getattr(engine, "_r7_span_caches", None)
    shared_r7_cache = bool(getattr(engine, "_r7_span_caches_enabled", False) and scope is not None)
    if shared_r7_cache:
        cache = scope["geometry"]
        if search_stats is not None:
            search_stats["physical_layout_resolver_calls"] += 1
            geometry_hits_before = engine._r7_cache_statistics["geometry_hits"]
        try:
            return engine._resolve_physical_layout(section, hoop, design, face=face)
        finally:
            if search_stats is not None:
                # The synchronous R7 resolver records each success/failure
                # hit before returning or raising; avoid hashing its key twice.
                if engine._r7_cache_statistics["geometry_hits"] > geometry_hits_before:
                    search_stats["physical_layout_resolver_cache_hits"] += 1
                else:
                    search_stats["physical_layout_resolver_unique_states"] += 1
                search_stats["physical_layout_resolver_cache_entries_peak"] = max(
                    search_stats["physical_layout_resolver_cache_entries_peak"], len(cache),
                )
    if geometry_cache is None:
        return engine._resolve_physical_layout(section, hoop, design, face=face)
    if search_stats is not None:
        search_stats["physical_layout_resolver_calls"] += 1
    # `_resolve_physical_layout` depends on the full section and layout, the
    # hoop diameter, and the selected face. Spacing and legs do not enter its
    # geometry calculations. The dataclasses are frozen and retained by this
    # per-span dictionary, so equality is exact and object identities stay live.
    key = (section, int(hoop.diameter_mm), face, design)
    cached = geometry_cache.get(key, _LAYOUT_CACHE_MISS)
    if cached is not _LAYOUT_CACHE_MISS:
        if search_stats is not None:
            search_stats["physical_layout_resolver_cache_hits"] += 1
        succeeded, value = cached
        if succeeded:
            return value
        error_type, error_args = value
        raise error_type(*error_args)

    if search_stats is not None:
        search_stats["physical_layout_resolver_unique_states"] += 1
    retain_result = len(geometry_cache) < R6_GEOMETRY_CACHE_MAX_ENTRIES
    if not retain_result and search_stats is not None:
        search_stats["physical_layout_resolver_cache_bypasses"] += 1
    try:
        result = engine._resolve_physical_layout(section, hoop, design, face=face)
    except DetailingError as error:
        if retain_result:
            geometry_cache[key] = (False, (type(error), error.args))
        raise
    if retain_result:
        geometry_cache[key] = (True, result)
        if search_stats is not None:
            search_stats["physical_layout_resolver_cache_entries_peak"] = max(
                search_stats["physical_layout_resolver_cache_entries_peak"],
                len(geometry_cache),
            )
    return result


def _is_two_row_four_three(rows, engine):
    main = tuple(row for row in rows if row.role != "hanger")
    return bool(
        getattr(engine, "avoid_two_row_4_3", False)
        and len(main) == 2
        and sorted(row.count for row in main) == [3, 4]
    )


def _outer_row_preference(design):
    """Rank an outer tension row that carries the primary bar pattern.

    Rows are stored from the relevant tension face toward the section core.
    This project preference is deliberately a soft ordering after all
    geometric/area hard filters: a preferred layout has an outer row whose
    diameter and count are each at least every inward main row.  Returning the
    negative outer values makes ties deterministic while still allowing
    oversupply to decide between layouts with the same preference class.
    """

    rows = tuple(row for row in design.rows if row.role != "hanger")
    if len(rows) <= 1:
        return 0, 0, 0, 0
    outer = rows[0]
    count_dominant = all(
        outer.diameter_mm >= row.diameter_mm and outer.count >= row.count
        for row in rows[1:]
    )
    balance = sum(abs(outer.count - row.count) for row in rows[1:])
    return (
        0 if count_dominant else 1,
        -outer.diameter_mm,
        balance,
        -outer.count,
    )


def candidates(section, hoops, required, engine, *, first_diameter=None,
               max_layers=None, face="top", preserve_support_alternatives=True,
               search_stats=None, geometry_cache=None):
    solver_revision = getattr(engine, "coordination_solver_revision_id", None)
    if solver_revision in {
        "coordination_20261031_indexed_candidates_v1",
        "coordination_v3_top10_v1",
    }:
        from .layout_dictionary import LayoutDictionaryMiss
        provider = getattr(engine, "candidate_lookup_provider", None)
        if provider is None:
            raise DetailingError("DICT_MISS: indexed candidate lookup provider is required")
        if search_stats is not None:
            search_stats["layout_dictionary_queries"] = search_stats.get("layout_dictionary_queries", 0) + 1
        try:
            if solver_revision == "coordination_v3_top10_v1":
                provider.prepare(
                    section,
                    hoops,
                    engine,
                    first_diameter=first_diameter,
                    max_layers=max_layers,
                    face=face,
                    preserve_support_alternatives=preserve_support_alternatives,
                    compile_membership=False,
                )
            return provider.query(required, section, hoops, engine,
                first_diameter=first_diameter, max_layers=max_layers, face=face,
                preserve_support_alternatives=preserve_support_alternatives)
        except LayoutDictionaryMiss as exc:
            raise DetailingError(str(exc)) from exc
    dictionary_bank = getattr(engine, "layout_dictionary_bank", None)
    if dictionary_bank is not None:
        from .layout_dictionary import LayoutDictionaryMiss

        if search_stats is not None:
            search_stats["layout_dictionary_queries"] = search_stats.get("layout_dictionary_queries", 0) + 1
        try:
            return dictionary_bank.query(
                required,
                section,
                hoops,
                engine,
                first_diameter=first_diameter,
                max_layers=max_layers,
                face=face,
                preserve_support_alternatives=preserve_support_alternatives,
            )
        except LayoutDictionaryMiss as exc:
            raise DetailingError(str(exc)) from exc
    use_geometry_cache = bool(getattr(engine, "exact_candidate_state_dedup", False))
    if use_geometry_cache and geometry_cache is None:
        geometry_cache = {}
    else:
        geometry_cache = geometry_cache if use_geometry_cache else None
    diameters = engine.project.ordered_longitudinal_diameters_for(section)
    first_diameters = (first_diameter,) if first_diameter is not None else diameters
    minimum_count = max(2, *(hoop.legs for hoop in hoops))
    smallest = min(diameters)
    gap = engine.code.minimum_clear_spacing(smallest, section.aggregate_mm)
    height_bound = max(1, int(section.depth_mm // (smallest + gap)))
    layer_limit = engine.project.max_main_bar_layers if max_layers is None else max_layers
    limit = height_bound if layer_limit is None else min(layer_limit, height_bound)
    count_bound = int((section.width_mm + gap) // (smallest + gap))
    output = []
    replaced_four_three = set()

    def visit(rows, extension=False):
        if _is_two_row_four_three(rows, engine):
            first_four = next(row for row in rows if row.count == 4)
            second_three = next(row for row in rows if row.count == 3)
            replacement = (
                PhysicalRow(5, first_four.diameter_mm),
                PhysicalRow(2, second_three.diameter_mm),
            )
            replacement_key = tuple((row.count, row.diameter_mm) for row in replacement)
            if (
                replacement[0].count >= minimum_count
                and replacement_key not in replaced_four_three
            ):
                replaced_four_three.add(replacement_key)
                visit(replacement, extension=True)
            return
        if (
            getattr(engine, "exact_candidate_state_dedup", False)
            and len(rows) >= limit
            and sum(float(row.credited_area_mm2 or 0.0) for row in rows) + 1e-9 < required
        ):
            if search_stats is not None:
                search_stats["last_layer_area_states_pruned"] += 1
            return
        design = LongitudinalDesign(rows=rows, required_area_mm2=required,
            constraint_sources=("FINITE_PHYSICAL_ROWS", "FIRST_ROW_HOOP_SUPPORT"))
        resolved = []
        for hoop in hoops:
            try:
                resolved.append(_resolve_with_geometry_cache(
                    section,
                    hoop,
                    design,
                    engine,
                    face=face,
                    geometry_cache=geometry_cache,
                    search_stats=search_stats if use_geometry_cache else None,
                ))
            except DetailingError:
                return
        minimum = max(engine.code.minimum_longitudinal_area(
            section.width_mm, item.effective_depth_mm, section.fc_mpa, section.fy_mpa)
            for item in resolved)
        if section.is_smf and any(design.provided_area_mm2 >
                engine.project.smf_max_longitudinal_ratio * section.width_mm * item.effective_depth_mm + 1e-9
                for item in resolved):
            return
        if design.provided_area_mm2 + 1e-9 >= max(required, minimum):
            output.append(replace(resolved[0], required_area_mm2=max(required, minimum)))
            # Keep one bounded superseding layer so a low-demand span can
            # adopt the higher-demand span's support state. Do not recurse
            # beyond that one extension: support alternatives are finite and
            # this avoids an exponential superset catalogue.
            if (preserve_support_alternatives and not extension
                    and len(rows) < limit and rows[0].count == minimum_count):
                for diameter in diameters:
                    for count in range(2, count_bound + 1):
                        visit((*rows, PhysicalRow(count, diameter)), True)
            return
        if len(rows) >= limit:
            return
        existing_area = sum(float(row.credited_area_mm2 or 0.0) for row in rows)
        for diameter in diameters:
            # Additional rows are independently sized.  The first-row count
            # is only the hoop-support requirement; it is not a cap on a
            # higher-demand inward row.
            first_count = 2
            if (
                getattr(engine, "exact_candidate_state_dedup", False)
                and len(rows) + 1 == limit
                and existing_area + 1e-9 < required
            ):
                area_per_bar = math.pi * diameter**2 / 4.0
                first_count = max(
                    first_count,
                    math.ceil((required - existing_area - 1e-9) / area_per_bar),
                )
                if search_stats is not None:
                    search_stats["last_layer_area_count_options_pruned"] += max(
                        0, min(first_count, count_bound + 1) - 2
                    )
            for count in range(first_count, count_bound + 1):
                visit((*rows, PhysicalRow(count, diameter)), extension)

    for diameter in first_diameters:
        if diameter not in diameters:
            continue
        first_count = minimum_count
        if (
            getattr(engine, "exact_candidate_state_dedup", False)
            and limit == 1
            and required > 1e-9
        ):
            area_per_bar = math.pi * diameter**2 / 4.0
            first_count = max(
                first_count,
                math.ceil((required - 1e-9) / area_per_bar),
            )
            if search_stats is not None:
                search_stats["last_layer_area_count_options_pruned"] += max(
                    0, min(first_count, count_bound + 1) - minimum_count
                )
        for count in range(first_count, count_bound + 1):
            visit((PhysicalRow(count, diameter),))
    ordered = sorted(output, key=lambda design: (
        len(design.rows),
        sum(row.diameter_mm > engine.project.sb_top_preferred_max_diameter_mm
            for row in design.rows) if section.is_secondary else 0,
        _outer_row_preference(design)[0],
        _outer_row_preference(design)[1],
        design.provided_area_mm2 - design.required_area_mm2,
        _outer_row_preference(design)[2],
        _outer_row_preference(design)[3],
        len({row.diameter_mm for row in design.rows}),
        sum(row.count for row in design.rows), signature(design)))
    if getattr(engine, "avoid_two_row_4_3", False):
        seen = set()
        unique = []
        for design in ordered:
            key = signature(design)
            if key in seen:
                continue
            seen.add(key)
            unique.append(design)
        ordered = unique
    return tuple(ordered)


def approved_pattern_candidates(section, hoops, required, engine, rules, *, face="top",
                                 geometry_cache=None, search_stats=None):
    """Materialize only the approved rule patterns and recheck physical rows.

    This keeps the offline search bounded while making every eligible style
    pattern available before the legacy candidate ordering can prune it.
    """
    output = {}
    maximum_layers = engine.project.max_main_bar_layers
    minimum_first_count = max(2, *(hoop.legs for hoop in hoops))
    enabled_diameters = set(engine.project.ordered_longitudinal_diameters_for(section))

    def row_options(pattern):
        if pattern.get("hanger_state") != "EXPLICIT_NONE" or pattern.get("hanger_physical_components_by_diameter"):
            return ()
        row_pattern = pattern.get("row_pattern")
        if isinstance(row_pattern, dict):
            components = row_pattern.get("unordered_main_row_components")
            if not isinstance(components, list) or len(components) != row_pattern.get("row_count"):
                return ()
            if len(components) > maximum_layers:
                return ()
            if any(
                int(item.get("count", 0)) < 2
                or int(item.get("diameter_mm", 0)) not in enabled_diameters
                for item in components
            ):
                return ()
            base = tuple(PhysicalRow(int(item["count"]), int(item["diameter_mm"])) for item in components)
            return tuple(dict.fromkeys(permutations(base)))

        components = pattern.get("main_components_by_diameter")
        if not isinstance(components, list) or not components:
            return ()
        if sum(int(item["count"]) for item in components) != int(pattern.get("primary_bar_count", -1)):
            return ()
        if len(components) > maximum_layers:
            return ()
        if any(
            int(item.get("count", 0)) < 2
            or int(item.get("diameter_mm", 0)) not in enabled_diameters
            for item in components
        ):
            return ()
        base = tuple(PhysicalRow(int(item["count"]), int(item["diameter_mm"])) for item in components)
        if len(base) > 1:
            return tuple(dict.fromkeys(permutations(base)))
        row = base[0]
        cap = min(
            engine._capacity_per_layer(section, hoop.diameter_mm, row.diameter_mm)
            for hoop in hoops
        )
        if cap <= 0:
            return ()
        if row.count <= cap:
            return (base,)
        if getattr(engine, "avoid_two_row_4_3", False) and row.count == 7:
            if maximum_layers < 2 or minimum_first_count > 5 or cap < 5:
                return ()
            return ((PhysicalRow(5, row.diameter_mm), PhysicalRow(2, row.diameter_mm)),)
        if maximum_layers < 2 or row.count < minimum_first_count + 2 or row.count > 2 * cap:
            return ()
        first_count = min(cap, max(minimum_first_count, row.count // 2))
        second_count = row.count - first_count
        if second_count < 2 or second_count > cap:
            first_count = row.count - min(cap, max(2, row.count // 2))
            second_count = row.count - first_count
        if first_count < minimum_first_count or second_count < 2 or max(first_count, second_count) > cap:
            return ()
        return ((PhysicalRow(first_count, row.diameter_mm), PhysicalRow(second_count, row.diameter_mm)),)

    for rule in rules:
        rule_id = str(rule.get("rule_id", ""))
        pattern = rule.get("pattern_signature") or {}
        for rows in row_options(pattern):
            if not rows or rows[0].count < minimum_first_count:
                continue
            if _is_two_row_four_three(rows, engine):
                continue
            design = LongitudinalDesign(
                rows=rows,
                required_area_mm2=required,
                constraint_sources=("APPROVED_STYLE_PATTERN", rule_id),
            )
            resolved = []
            for hoop in hoops:
                try:
                    item = _resolve_with_geometry_cache(
                        section,
                        hoop,
                        design,
                        engine,
                        face=face,
                        geometry_cache=(
                            geometry_cache
                            if getattr(engine, "exact_candidate_state_dedup", False)
                            else None
                        ),
                        search_stats=(
                            search_stats
                            if getattr(engine, "exact_candidate_state_dedup", False)
                            else None
                        ),
                    )
                except DetailingError:
                    resolved = []
                    break
                minimum = engine.code.minimum_longitudinal_area(
                    section.width_mm, item.effective_depth_mm, section.fc_mpa, section.fy_mpa
                )
                if item.provided_area_mm2 + 1e-9 < max(required, minimum):
                    resolved = []
                    break
                if section.is_smf and item.provided_area_mm2 > (
                    engine.project.smf_max_longitudinal_ratio
                    * section.width_mm
                    * item.effective_depth_mm
                    + 1e-9
                ):
                    resolved = []
                    break
                resolved.append(replace(item, required_area_mm2=max(required, minimum)))
            if resolved:
                output.setdefault(signature(resolved[0]), resolved[0])
    return tuple(output.values())
