"""Equivalent steel volume indicator for comparing finalized layouts.

This geometric indicator is not a steel mass or tonnage calculation. It uses
the same clipped 2h left/middle/right partition as region demand grouping.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping


REGION_ZONE_ASSUMPTION = "left=min(2h,L/2); middle=L-2*left; right=min(2h,L/2), nonoverlapping from span station 0"


@dataclass(frozen=True)
class EquivalentSteelResult:
    available: bool
    total_mm3: float | None
    longitudinal_mm3: float | None
    transverse_mm3: float | None
    reason: str = ""
    zone_assumption: str = REGION_ZONE_ASSUMPTION


def equivalent_steel_mm3(result: Any) -> EquivalentSteelResult:
    """Calculate the requested indicator from a final result or serialized row.

    The mapping form requires lossless ``longitudinal_rows`` and exact
    per-region ``region_stirrups`` from the final coordinated design.
    """

    if isinstance(result, Mapping):
        return _from_payload_row(result)
    return _from_detailing_result(result)


def _from_detailing_result(result: Any) -> EquivalentSteelResult:
    section = getattr(result, "section", None)
    regions = {str(region.name): region for region in getattr(result, "regions", ())}
    if section is None or set(regions) != {"left", "middle", "right"}:
        return _unavailable("missing finalized section or left/middle/right regions")
    dimensions = (
        _positive(getattr(section, "length_mm", None)),
        _positive(getattr(section, "width_mm", None)),
        _positive(getattr(section, "depth_mm", None)),
    )
    if any(value is None for value in dimensions):
        return _unavailable("missing positive span or section dimensions")
    length, width, depth = (float(value) for value in dimensions)

    areas: dict[tuple[str, str], float] = {}
    for region_name, region in regions.items():
        for face in ("top", "bottom"):
            area = _physical_rows_area(getattr(getattr(region, face, None), "rows", None))
            if area is None:
                return _unavailable(f"{region_name} {face} physical bars are unavailable")
            areas[(region_name, face)] = area
    bottom_values = [areas[(name, "bottom")] for name in ("left", "middle", "right")]
    if not _same(bottom_values):
        return _unavailable("bottom reinforcement is not uniform over the full span")

    side_area = _face_each_side_area(getattr(result, "face_rebar", None))
    if side_area is None:
        return _unavailable("side-face reinforcement is unresolved")
    longitudinal = length * (
        bottom_values[0]
        + (areas[("left", "top")] + 2.0 * areas[("middle", "top")] + areas[("right", "top")]) / 4.0
        + 2.0 * side_area
    )

    stirrups = {
        name: {
            "diameter_mm": getattr(region.stirrup, "diameter_mm", None),
            "spacing_mm": getattr(region.stirrup, "spacing_mm", None),
            "legs": getattr(region.stirrup, "legs", None),
        }
        for name, region in regions.items()
    }
    centroid_geometry = {
        name: _centroid_cover_geometry(region, width)
        for name, region in regions.items()
    }
    transverse = _region_transverse_volume(
        length,
        width,
        depth,
        stirrups,
        clear_cover_to_stirrup_outer_mm=getattr(section, "clear_cover_to_stirrup_outer_mm", None),
        etabs_longitudinal_cover_mm=getattr(section, "etabs_longitudinal_cover_mm", None),
        centroid_geometry=centroid_geometry,
        zone_lengths=_parent_zone_lengths(getattr(getattr(result, "input_data", None), "parent_context", None), length),
    )
    if isinstance(transverse, str):
        return _unavailable(transverse, longitudinal)
    return EquivalentSteelResult(True, longitudinal + transverse, longitudinal, transverse)


def _from_payload_row(row: Mapping[str, Any]) -> EquivalentSteelResult:
    dimensions = (_positive(row.get("length_mm")), _positive(row.get("width_mm")), _positive(row.get("depth_mm")))
    if any(value is None for value in dimensions):
        return _unavailable("serialized row lacks positive span or section dimensions")
    length, width, depth = (float(value) for value in dimensions)

    layout = row.get("longitudinal_rows")
    if not isinstance(layout, Mapping):
        return _unavailable("serialized row lacks final physical longitudinal rows")
    areas: dict[tuple[str, str], float] = {}
    audit_geometry = _centroid_geometry_from_audit(row.get("row_coordination_audit"), width)
    for region_name in ("left", "middle", "right"):
        region = layout.get(region_name)
        if not isinstance(region, Mapping):
            return _unavailable(f"serialized row lacks {region_name} longitudinal rows")
        for face in ("top", "bottom"):
            area = _physical_rows_area(region.get(face))
            if area is None:
                return _unavailable(f"serialized row lacks {region_name} {face} physical bars")
            areas[(region_name, face)] = area
    bottom_values = [areas[(name, "bottom")] for name in ("left", "middle", "right")]
    if not _same(bottom_values):
        return _unavailable("serialized bottom reinforcement is not uniform over the full span")

    side_area = _face_area_from_text(row.get("face"))
    if side_area is None:
        return _unavailable("serialized row lacks resolvable side-face reinforcement")
    longitudinal = length * (
        bottom_values[0]
        + (areas[("left", "top")] + 2.0 * areas[("middle", "top")] + areas[("right", "top")]) / 4.0
        + 2.0 * side_area
    )

    stirrups = row.get("region_stirrups")
    if not isinstance(stirrups, Mapping):
        return _unavailable("serialized row lacks per-region physical stirrup details", longitudinal)
    transverse = _region_transverse_volume(
        length,
        width,
        depth,
        stirrups,
        clear_cover_to_stirrup_outer_mm=row.get("clear_cover_to_stirrup_outer_mm"),
        etabs_longitudinal_cover_mm=row.get("etabs_longitudinal_cover_mm"),
        centroid_geometry=audit_geometry,
        zone_lengths=_parent_zone_lengths_from_row(row, length),
    )
    if isinstance(transverse, str):
        return _unavailable(transverse, longitudinal)
    return EquivalentSteelResult(True, longitudinal + transverse, longitudinal, transverse)


def _region_transverse_volume(
    length: float,
    width: float,
    depth: float,
    stirrups: Mapping[str, Any],
    *,
    clear_cover_to_stirrup_outer_mm: Any,
    etabs_longitudinal_cover_mm: Any,
    centroid_geometry: Mapping[str, tuple[float, float, float, float] | None],
    zone_lengths: Mapping[str, float] | None = None,
) -> float | str:
    clear_cover = _positive(clear_cover_to_stirrup_outer_mm)
    etabs_cover = _positive(etabs_longitudinal_cover_mm)
    if clear_cover is None and etabs_cover is None:
        return "cover semantics are unavailable for stirrup centerline geometry"

    end_zone = min(2.0 * depth, length / 2.0)
    zone_lengths = zone_lengths or {"left": end_zone, "middle": length - 2.0 * end_zone, "right": end_zone}
    total = 0.0
    for region_name in ("left", "middle", "right"):
        zone_length = zone_lengths[region_name]
        if zone_length <= 0:
            continue
        spec = stirrups.get(region_name)
        if not isinstance(spec, Mapping):
            return f"{region_name} physical stirrup details are unavailable"
        diameter = _positive(spec.get("diameter_mm"))
        spacing = _positive(spec.get("spacing_mm"))
        legs = _positive(spec.get("legs"))
        if diameter is None or spacing is None or legs is None:
            return f"{region_name} stirrup diameter, spacing, or legs are unavailable"

        if clear_cover is not None:
            center_cover = clear_cover + diameter / 2.0
            b_center = width - 2.0 * center_cover
            h_center = depth - 2.0 * center_cover
        else:
            covers = centroid_geometry.get(region_name)
            if covers is None:
                return f"{region_name} final outer-row coordinates needed for ETABS cover semantics are unavailable"
            left_surface, right_surface, top_surface, bottom_surface = covers
            b_center = width - left_surface - right_surface - diameter
            h_center = depth - top_surface - bottom_surface - diameter
        if b_center <= 0 or h_center <= 0:
            return f"{region_name} stirrup centerline dimensions are nonpositive"
        area = math.pi * diameter**2 / 4.0
        total += zone_length / spacing * area * (2.0 * b_center + legs * h_center)
    return total


def _parent_zone_lengths(context: Any, length: float) -> dict[str, float] | None:
    if context is None:
        return None
    parent = float(context.parent_length_mm)
    start = float(context.segment_start_mm)
    left = min(2.0 * float(context.left_depth_mm), parent / 2.0)
    right = max(parent / 2.0, parent - 2.0 * float(context.right_depth_mm))
    end = start + length
    return {name: max(0.0, min(end, high) - max(start, low)) for name, (low, high) in {
        "left": (0.0, left), "middle": (left, right), "right": (right, parent)}.items()}


def _parent_zone_lengths_from_row(row: Mapping[str, Any], length: float) -> dict[str, float] | None:
    context = row.get("detailing_context", {}).get("parent_context") if isinstance(row.get("detailing_context"), Mapping) else None
    if not isinstance(context, Mapping):
        return None
    from .parent_context import ParentSpanContext
    return _parent_zone_lengths(ParentSpanContext(**context), length)


def _physical_rows_area(rows: Any) -> float | None:
    if rows is None:
        return None
    area = 0.0
    seen = False
    for row in rows:
        count = _positive(getattr(row, "count", None) if not isinstance(row, Mapping) else row.get("count"))
        diameter = _positive(
            getattr(row, "diameter_mm", None) if not isinstance(row, Mapping) else row.get("diameter_mm")
        )
        if count is None or diameter is None:
            return None
        area += int(count) * math.pi * diameter**2 / 4.0
        seen = True
    return area if seen else None


def _centroid_cover_geometry(region: Any, width: float) -> tuple[float, float, float, float] | None:
    output: list[tuple[float, float, float]] = []
    for face in ("top", "bottom"):
        design = getattr(region, face, None)
        rows = tuple(getattr(design, "rows", ()))
        centers = tuple(getattr(design, "row_centers_mm", ()))
        x_rows = tuple(getattr(design, "row_x_mm", ()))
        if not rows or not centers or not x_rows or not x_rows[0]:
            return None
        outer = rows[0]
        xs = tuple(float(value) for value in x_rows[0])
        db = float(outer.diameter_mm)
        vertical_surface = float(centers[0]) - db / 2.0
        left_surface = min(xs) - db / 2.0
        right_surface = float(width) - max(xs) - db / 2.0
        if min(vertical_surface, left_surface, right_surface) <= 0:
            return None
        output.append((left_surface, right_surface, vertical_surface))
    return (
        min(output[0][0], output[1][0]),
        min(output[0][1], output[1][1]),
        output[0][2],
        output[1][2],
    )


def _centroid_geometry_from_audit(value: Any, width: float) -> dict[str, tuple[float, float, float, float] | None]:
    if not isinstance(value, list):
        return {}
    faces: dict[tuple[str, str], Mapping[str, Any]] = {}
    for item in value:
        if isinstance(item, Mapping):
            faces[(str(item.get("region", "")), str(item.get("face", "")))] = item
    output: dict[str, tuple[float, float, float, float] | None] = {}
    for region in ("left", "middle", "right"):
        face_data: list[tuple[float, float, float]] = []
        for face in ("top", "bottom"):
            item = faces.get((region, face))
            if item is None:
                face_data = []
                break
            rows = item.get("rows")
            centers = item.get("row_centers_mm")
            x_rows = item.get("row_x_mm")
            if not rows or not centers or not x_rows or not x_rows[0] or not isinstance(rows[0], Mapping):
                face_data = []
                break
            db = _positive(rows[0].get("diameter_mm"))
            if db is None:
                face_data = []
                break
            xs = [_finite(x) for x in x_rows[0]]
            if not xs or any(x is None for x in xs):
                face_data = []
                break
            vertical = _finite(centers[0])
            if vertical is None:
                face_data = []
                break
            left_surface = min(float(x) for x in xs) - db / 2.0
            right_surface = float(width) - max(float(x) for x in xs) - db / 2.0
            vertical_surface = vertical - db / 2.0
            if min(left_surface, right_surface, vertical_surface) <= 0:
                face_data = []
                break
            face_data.append((left_surface, right_surface, vertical_surface))
        output[region] = (
            min(face_data[0][0], face_data[1][0]),
            min(face_data[0][1], face_data[1][1]),
            face_data[0][2],
            face_data[1][2],
        ) if len(face_data) == 2 else None
    return output


def _face_each_side_area(face: Any) -> float | None:
    if face is None:
        return None
    bars = getattr(face, "bars_each_side", None)
    mode = str(getattr(getattr(face, "mode", None), "value", getattr(face, "mode", ""))).upper()
    if bars is None:
        mode_name = str(getattr(getattr(face, "mode", None), "name", "")).upper()
        return 0.0 if mode in {"N.A.", "NA", "NOT APPLICABLE", "NOT_APPLICABLE"} or mode_name == "NOT_APPLICABLE" else None
    count = _positive(getattr(bars, "count", None))
    diameter = _positive(getattr(bars, "diameter_mm", None))
    if count is None or diameter is None:
        return None
    return int(count) * math.pi * diameter**2 / 4.0


def _face_area_from_text(value: Any) -> float | None:
    text = str(value or "").strip().upper()
    if text in {"N.A.", "NA", "NOT APPLICABLE"}:
        return 0.0
    if not text:
        return None
    matches = list(re.finditer(r"(\d+)\s*T\s*(\d+)", text))
    if not matches or "T" not in text:
        return None
    if re.sub(r"\d+\s*T\s*\d+", "", text).strip(" /+"):
        return None
    return sum(int(match.group(1)) * math.pi * int(match.group(2))**2 / 4.0 for match in matches)


def _same(values: list[float]) -> bool:
    return max(values) - min(values) <= max(1e-8, max(values) * 1e-10)


def _positive(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number > 0 else None


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _unavailable(reason: str, longitudinal: float | None = None) -> EquivalentSteelResult:
    return EquivalentSteelResult(False, None, longitudinal, None, reason)
