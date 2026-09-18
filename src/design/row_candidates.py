"""Finite, geometrically verified physical-row catalogue (no mixed groups)."""
from dataclasses import replace
from .detailing import LongitudinalDesign, PhysicalRow, DetailingError


def signature(design):
    return tuple((row.count, row.diameter_mm) for row in design.rows)


def candidates(section, hoops, required, engine, *, first_diameter=None,
               max_layers=2, face="top", preserve_support_alternatives=True):
    diameters = engine.project.ordered_longitudinal_diameters_for(section)
    first_diameters = (first_diameter,) if first_diameter is not None else diameters
    minimum_count = max(2, *(hoop.legs for hoop in hoops))
    smallest = min(diameters)
    gap = engine.code.minimum_clear_spacing(smallest, section.aggregate_mm)
    height_bound = max(1, int(section.depth_mm // (smallest + gap)))
    limit = height_bound if max_layers is None else min(max_layers, height_bound)
    count_bound = int((section.width_mm + gap) // (smallest + gap))
    output = []

    def visit(rows, extension=False):
        design = LongitudinalDesign(rows=rows, required_area_mm2=required,
            constraint_sources=("FINITE_PHYSICAL_ROWS", "FIRST_ROW_HOOP_SUPPORT"))
        resolved = []
        for hoop in hoops:
            try:
                resolved.append(engine._resolve_physical_layout(section, hoop, design, face=face))
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
        for diameter in diameters:
            # Additional rows are independently sized.  The first-row count
            # is only the hoop-support requirement; it is not a cap on a
            # higher-demand inward row.
            for count in range(2, count_bound + 1):
                visit((*rows, PhysicalRow(count, diameter)), extension)

    for diameter in first_diameters:
        if diameter not in diameters:
            continue
        for count in range(minimum_count, count_bound + 1):
            visit((PhysicalRow(count, diameter),))
    return tuple(sorted(output, key=lambda design: (
        len(design.rows),
        sum(row.diameter_mm > engine.project.sb_top_preferred_max_diameter_mm
            for row in design.rows) if section.is_secondary else 0,
        design.provided_area_mm2 - design.required_area_mm2,
        len({row.diameter_mm for row in design.rows}),
        sum(row.count for row in design.rows), signature(design))))
