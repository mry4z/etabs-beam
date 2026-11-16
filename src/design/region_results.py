"""Shared left/middle/right envelopes for span-level beam design outputs."""

from __future__ import annotations

from math import isfinite
from typing import Any, Iterable

from src.design.provider import BeamDesignResult, DesignStation
from src.design.offline_style import DRAWING_V3_POLICY_ID, DRAWING_V3_REVISION_ID

LEGACY_DETAILING_POLICY_ID = "legacy"
DRAWING_DETAILING_POLICY_ID = "drawing_20260924_v1"
V3_DETAILING_POLICY_ID = DRAWING_V3_POLICY_ID
V3_DETAILING_POLICY_REVISION_ID = DRAWING_V3_REVISION_ID
DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE = "PARTIAL_REGION_EVIDENCE_RESEARCH_ONLY"
SUPPORTED_DETAILING_POLICY_IDS = frozenset(
    {LEGACY_DETAILING_POLICY_ID, DRAWING_DETAILING_POLICY_ID, V3_DETAILING_POLICY_ID}
)


def group_design_stations(
    design: BeamDesignResult,
    length_mm: float,
    depth_mm: float,
    *,
    backfill_empty: bool = True,
    parent_length_mm: float | None = None,
    segment_start_mm: float = 0.0,
    parent_end_depths_mm: tuple[float, float] | None = None,
    retain_empty_regions: bool = False,
) -> dict[str, tuple[DesignStation, ...]] | None:
    """Partition a span into the existing 2h end zones and middle zone."""

    if not design.stations or length_mm <= 0 or depth_mm <= 0:
        return None
    if parent_end_depths_mm is not None:
        parent_length = float(parent_length_mm if parent_length_mm is not None else length_mm)
        left_depth, right_depth = parent_end_depths_mm
        if parent_length <= 0 or min(left_depth, right_depth) <= 0:
            raise ValueError("Parent 2h projection requires positive length and end depths.")
        left_end = min(2.0 * left_depth, parent_length / 2.0)
        right_start = max(parent_length / 2.0, parent_length - 2.0 * right_depth)
        bounds = {"left": (0.0, left_end), "middle": (left_end, right_start),
                  "right": (right_start, parent_length)}
        groups = {}
        for name, (lower, upper) in bounds.items():
            overlap_start = max(segment_start_mm, lower)
            overlap_end = min(segment_start_mm + length_mm, upper)
            if overlap_start >= overlap_end - 1e-9:
                groups[name] = tuple(design.stations)
            else:
                groups[name] = tuple(station for station in design.stations
                    if overlap_start - 1e-9 <= segment_start_mm + station.location <= overlap_end + 1e-9)
                if not groups[name] and not retain_empty_regions:
                    return None
        return groups
    end_zone = min(2.0 * depth_mm, length_mm / 2.0)
    groups: dict[str, list[DesignStation]] = {
        "left": [station for station in design.stations if station.location <= end_zone],
        "middle": [
            station
            for station in design.stations
            if end_zone < station.location < length_mm - end_zone
        ],
        "right": [station for station in design.stations if station.location >= length_mm - end_zone],
    }
    if backfill_empty:
        targets = {
            "left": end_zone,
            "middle": length_mm / 2.0,
            "right": length_mm - end_zone,
        }
        for name, stations in groups.items():
            if not stations:
                groups[name] = [
                    min(
                        design.stations,
                        key=lambda station: abs(station.location - targets[name]),
                    )
                ]
    return {name: tuple(stations) for name, stations in groups.items()}


def build_region_controls(
    design: BeamDesignResult,
    length_mm: float,
    depth_mm: float,
    *,
    detailing_policy_id: str = LEGACY_DETAILING_POLICY_ID,
    parent_length_mm: float | None = None,
    segment_start_mm: float = 0.0,
    parent_end_depths_mm: tuple[float, float] | None = None,
    projection_mode: str = "APPROVED_REGION_CONTROLS",
) -> dict[str, dict[str, Any]] | None:
    """Build the workbook-compatible control envelope for each span region.

    The drawing policy projects only the upper-middle top demand from the
    inclusive central quarter of its parent span.  Stations remain local to
    this record's actual span/subsegment, so the parent offset is supplied
    separately when a structural span has been split into object parts.
    """

    if detailing_policy_id not in SUPPORTED_DETAILING_POLICY_IDS:
        raise ValueError(f"Unsupported detailing policy: {detailing_policy_id}")
    if projection_mode not in {"APPROVED_REGION_CONTROLS", DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE}:
        raise ValueError(f"Unsupported demand projection mode: {projection_mode}")
    is_partial_research = projection_mode == DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE
    if is_partial_research and detailing_policy_id not in {DRAWING_DETAILING_POLICY_ID, V3_DETAILING_POLICY_ID}:
        raise ValueError("Partial-region evidence research requires an existing drawing demand policy.")

    groups = group_design_stations(design, length_mm, depth_mm, parent_length_mm=parent_length_mm,
                                   segment_start_mm=segment_start_mm, parent_end_depths_mm=parent_end_depths_mm,
                                   backfill_empty=not is_partial_research,
                                   retain_empty_regions=is_partial_research)
    if groups is None:
        return None
    local_middle_bounds: tuple[float, float] | None = None
    local_middle_not_applicable = False
    if is_partial_research and parent_end_depths_mm is None:
        local_end_zone = min(2.0 * depth_mm, length_mm / 2.0)
        local_middle_bounds = (local_end_zone, length_mm - local_end_zone)
        local_middle_not_applicable = local_middle_bounds[1] <= local_middle_bounds[0] + 1e-9
        if local_middle_not_applicable:
            # The local 2h middle zone has zero geometric width. Its controls use
            # the same explicit full-range envelope as a nonintersecting parent
            # region; the central-quarter top projection below remains separate.
            groups["middle"] = tuple(design.stations)
    controls = {
        name: (_unknown_control_values() if is_partial_research and not stations else _control_values(stations))
        for name, stations in groups.items()
    }
    if is_partial_research:
        parent_bounds = _parent_region_bounds(
            parent_length_mm, length_mm, segment_start_mm, parent_end_depths_mm,
        )
        for name, stations in groups.items():
            basis = "PARENT_2H_REGION" if parent_end_depths_mm is not None else "LOCAL_2H_REGION"
            missing_reason = (
                "NO_STATION_IN_PARENT_2H_REGION" if parent_end_depths_mm is not None
                else "NO_STATION_IN_LOCAL_2H_REGION"
            )
            parent_region_not_intersecting = parent_bounds is not None and not parent_bounds[name]["intersects_segment"]
            local_middle_not_intersecting = name == "middle" and local_middle_not_applicable
            if parent_region_not_intersecting or local_middle_not_intersecting:
                basis = "SEGMENT_FULL_RANGE_ENVELOPE"
                missing_reason = "REGION_NOT_INTERSECTING_SEGMENT_FULL_RANGE_ENVELOPE"
            _attach_region_evidence(
                controls[name],
                stations,
                basis=basis,
                missing_reason=missing_reason,
            )
            if name == "middle":
                if parent_bounds is not None:
                    geometry_interval = [
                        parent_bounds[name]["lower_parent_mm"],
                        parent_bounds[name]["upper_parent_mm"],
                    ]
                    geometry_status = "PRESENT" if parent_bounds[name]["intersects_segment"] else "NOT_APPLICABLE"
                elif local_middle_bounds is not None:
                    geometry_interval = list(local_middle_bounds)
                    geometry_status = "NOT_APPLICABLE" if local_middle_not_applicable else "PRESENT"
                else:
                    geometry_interval = None
                    geometry_status = "PRESENT"
                controls[name]["region_evidence"]["geometry_status"] = geometry_status
                controls[name]["region_evidence"]["geometry_interval_mm"] = geometry_interval
            controls[name]["projection_mode"] = projection_mode
    if detailing_policy_id in {DRAWING_DETAILING_POLICY_ID, V3_DETAILING_POLICY_ID} or is_partial_research:
        parent_length = float(parent_length_mm if parent_length_mm is not None else length_mm)
        segment_start = float(segment_start_mm)
        if parent_length <= 0.0 or segment_start < 0.0:
            raise ValueError("Drawing demand projection requires a positive parent length and nonnegative offset.")
        _project_upper_middle_top(
            controls["middle"], design, length_mm, parent_length, segment_start,
            retain_field_evidence=is_partial_research,
        )
    return controls


def _project_upper_middle_top(
    middle: dict[str, Any],
    design: BeamDesignResult,
    segment_length_mm: float,
    parent_length_mm: float,
    segment_start_mm: float,
    *,
    retain_field_evidence: bool = False,
) -> None:
    lower = parent_length_mm * 3.0 / 8.0
    upper = parent_length_mm * 5.0 / 8.0
    segment_end = segment_start_mm + segment_length_mm
    window_start = max(lower, segment_start_mm)
    window_end = min(upper, segment_end)
    comparison_epsilon = max(1e-9, parent_length_mm * 1e-12)
    overlaps_window = window_start <= window_end + comparison_epsilon
    # Keep every raw station in the inclusive intersection.  In particular,
    # this does not select a nearest station when the intersection is empty.
    candidates = [
        station
        for station in design.stations
        if not overlaps_window or (window_start - comparison_epsilon
        <= segment_start_mm + station.location
        <= window_end + comparison_epsilon)
    ]
    invalid_values: list[dict[str, Any]] = []
    valid_top_candidates: list[DesignStation] = []
    valid_min_candidates: list[DesignStation] = []
    for station in candidates:
        raw_total = station.top_total_area
        if raw_total is None:
            invalid_values.append({"location_mm": station.location, "field": "TopTotal", "value": None})
        elif not _valid_nonnegative(raw_total):
            invalid_values.append({"location_mm": station.location, "field": "TopTotal", "value": raw_total})
        else:
            valid_top_candidates.append(station)
        raw_min = station.top_min_area
        if raw_min is None:
            continue
        if not _valid_nonnegative(raw_min):
            invalid_values.append({"location_mm": station.location, "field": "TopAsMin", "value": raw_min})
        else:
            valid_min_candidates.append(station)

    top = max(valid_top_candidates, key=lambda station: station.as_total_top_mm2, default=None)
    min_candidates = valid_min_candidates
    top_min = max(min_candidates, key=lambda station: float(station.top_min_area), default=None)
    top_flex = max(valid_top_candidates, key=lambda station: station.as_flex_top_mm2, default=None)
    top_demand_area = max(
        (station.as_total_top_mm2 for station in valid_top_candidates),
        default=None,
    )
    top_min_area = max(
        (float(station.top_min_area) for station in min_candidates),
        default=None,
    )
    if top_min_area is not None and (top_demand_area is None or top_min_area > top_demand_area):
        top_demand_area = top_min_area
        top_demand_station = top_min
        top_demand_source = "ETABS_TOP_AS_MIN"
    else:
        top_demand_station = top
        top_demand_source = (
            "TOP_TOTAL_AND_ETABS_TOP_AS_MIN"
            if top_min_area is not None and top_min_area == top_demand_area
            else "TOP_TOTAL"
        )
    if invalid_values:
        top_demand_area = None
        top_demand_station = None
        top_demand_source = ""
    provided = [station.top_provided_area for station in candidates if station.top_provided_area is not None]
    raw_provided = [station.raw_top_provided_area for station in candidates if station.raw_top_provided_area is not None]
    middle.update(
        {
            "top_total_mm2": top.as_total_top_mm2 if top is not None else None,
            "top_total_station_mm": top.location if top is not None else None,
            "top_total_combo": top.top_flexural_combo if top is not None else "",
            "as_min_top_mm2": top_min_area,
            "as_min_top_station_mm": top_min.location if top_min is not None else None,
            "top_demand_mm2": top_demand_area,
            "top_demand_station_mm": (
                top_demand_station.location if top_demand_station is not None else None
            ),
            "top_demand_source": top_demand_source if top_demand_area is not None else "",
            "top_demand_combo": (
                top_demand_station.top_flexural_combo if top_demand_station is not None else ""
            ),
            "top_flex_mm2": top_flex.as_flex_top_mm2 if top_flex is not None else None,
            "top_flex_station_mm": top_flex.location if top_flex is not None else None,
            "top_flex_combo": top_flex.top_flexural_combo if top_flex is not None else "",
            "as_provided_top_mm2": max(provided, default=None),
            "raw_as_provided_top": max(raw_provided, default=None),
            "middle_top_window_invalid_reason": (
                "; ".join(
                    f"{item['field']}@{item['location_mm']}: {item['value']}"
                    for item in invalid_values
                )
                if invalid_values
                else ""
            ),
            "middle_top_window_invalid_values": invalid_values,
            "middle_top_window_start_parent_mm": window_start if overlaps_window else None,
            "middle_top_window_end_parent_mm": window_end if overlaps_window else None,
            "middle_top_window_station_count": len(candidates),
            "middle_top_demand_basis": ("PARENT_CENTRAL_WINDOW" if overlaps_window
                                        else "SEGMENT_FULL_RANGE_ENVELOPE"),
        }
    )
    if retain_field_evidence:
        middle["region_evidence"]["top_window"] = {
            "basis": "PARENT_CENTRAL_WINDOW" if overlaps_window else "SEGMENT_FULL_RANGE_ENVELOPE",
            "start_parent_mm": window_start if overlaps_window else None,
            "end_parent_mm": window_end if overlaps_window else None,
            "station_count": len(candidates),
            "station_locations_mm": [station.location for station in candidates],
        }
        for field in _TOP_WINDOW_FIELDS:
            value = middle.get(field)
            available = value is not None and (not isinstance(value, str) or bool(value))
            missing_reason = "" if available else (
                "NO_STATION_IN_PARENT_CENTRAL_WINDOW" if overlaps_window and not candidates
                else "NO_VALID_VALUE_IN_PARENT_CENTRAL_WINDOW" if overlaps_window
                else "NO_VALID_VALUE_IN_SEGMENT_ENVELOPE"
            )
            middle["field_evidence"][field] = {
                "status": "AVAILABLE" if available else "UNKNOWN",
                "basis": "PARENT_CENTRAL_WINDOW" if overlaps_window else "SEGMENT_FULL_RANGE_ENVELOPE",
                "station_count": len(candidates),
                "station_locations_mm": [station.location for station in candidates],
                "missing_reason": missing_reason,
            }


def _valid_nonnegative(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return isfinite(number) and number >= 0.0


def _parent_region_bounds(
    parent_length_mm: float | None,
    segment_length_mm: float,
    segment_start_mm: float,
    parent_end_depths_mm: tuple[float, float] | None,
) -> dict[str, dict[str, Any]] | None:
    if parent_end_depths_mm is None:
        return None
    parent_length = float(parent_length_mm if parent_length_mm is not None else segment_length_mm)
    left_depth, right_depth = parent_end_depths_mm
    left_end = min(2.0 * left_depth, parent_length / 2.0)
    right_start = max(parent_length / 2.0, parent_length - 2.0 * right_depth)
    bounds = {
        "left": (0.0, left_end),
        "middle": (left_end, right_start),
        "right": (right_start, parent_length),
    }
    output = {}
    for name, (lower, upper) in bounds.items():
        overlap_start = max(segment_start_mm, lower)
        overlap_end = min(segment_start_mm + segment_length_mm, upper)
        output[name] = {
            "lower_parent_mm": lower,
            "upper_parent_mm": upper,
            "intersects_segment": overlap_start < overlap_end - 1e-9,
        }
    return output


def _control_values(stations: Iterable[DesignStation]) -> dict[str, Any]:
    items = list(stations)
    top_total = max(items, key=lambda station: station.as_total_top_mm2)
    top_flex = max(items, key=lambda station: station.as_flex_top_mm2)
    bottom_total = max(items, key=lambda station: station.as_total_bottom_mm2)
    bottom_flex = max(items, key=lambda station: station.as_flex_bottom_mm2)
    shear = max(items, key=lambda station: station.shear_area)
    tl = max(items, key=lambda station: station.torsion_longitudinal_area)
    tt = max(items, key=lambda station: station.torsion_trans_area)
    return {
        "top_total_mm2": top_total.as_total_top_mm2,
        "top_total_station_mm": top_total.location,
        "top_total_combo": top_total.top_flexural_combo,
        "top_flex_mm2": top_flex.as_flex_top_mm2,
        "top_flex_station_mm": top_flex.location,
        "top_flex_combo": top_flex.top_flexural_combo,
        "bottom_total_mm2": bottom_total.as_total_bottom_mm2,
        "bottom_total_station_mm": bottom_total.location,
        "bottom_total_combo": bottom_total.bottom_flexural_combo,
        "bottom_flex_mm2": bottom_flex.as_flex_bottom_mm2,
        "bottom_flex_station_mm": bottom_flex.location,
        "bottom_flex_combo": bottom_flex.bottom_flexural_combo,
        "as_min_top_mm2": max((station.top_min_area or 0.0 for station in items), default=0.0),
        "as_min_bottom_mm2": max((station.bottom_min_area or 0.0 for station in items), default=0.0),
        "as_provided_top_mm2": _maximum_available(station.top_provided_area for station in items),
        "as_provided_bottom_mm2": _maximum_available(station.bottom_provided_area for station in items),
        "raw_as_provided_top": _maximum_available(station.raw_top_provided_area for station in items),
        "raw_as_provided_bottom": _maximum_available(station.raw_bottom_provided_area for station in items),
        "av_over_s": shear.shear_area,
        "av_station_mm": shear.location,
        "av_combo": shear.shear_combo,
        "al_mm2": tl.torsion_longitudinal_area,
        "al_station_mm": tl.location,
        "al_combo": tl.torsion_longitudinal_combo,
        "at_over_s": tt.torsion_trans_area,
        "at_station_mm": tt.location,
        "at_combo": tt.torsion_trans_combo,
        "source_station_count": len(items),
        "source_objects": tuple(dict.fromkeys(station.source_object for station in items if station.source_object)),
        "units": ",".join(dict.fromkeys(station.units for station in items if station.units)),
        "result_source": ",".join(dict.fromkeys(station.result_source for station in items if station.result_source)),
        "design_errors": "; ".join(dict.fromkeys(station.error for station in items if station.error.strip())),
        "design_warnings": "; ".join(dict.fromkeys(station.warning for station in items if station.warning.strip())),
    }


_TOP_WINDOW_FIELDS = (
    "top_total_mm2", "top_total_station_mm", "top_total_combo",
    "top_flex_mm2", "top_flex_station_mm", "top_flex_combo",
    "as_min_top_mm2", "as_min_top_station_mm", "as_provided_top_mm2",
    "raw_as_provided_top", "top_demand_mm2", "top_demand_station_mm",
    "top_demand_source", "top_demand_combo",
)


def _unknown_control_values() -> dict[str, Any]:
    """Return explicit unknowns for an empty region in the research-only policy."""
    nullable_fields = (
        "top_total_mm2", "top_total_station_mm", "top_flex_mm2", "top_flex_station_mm",
        "bottom_total_mm2", "bottom_total_station_mm", "bottom_flex_mm2", "bottom_flex_station_mm",
        "as_min_top_mm2", "as_min_bottom_mm2", "as_provided_top_mm2", "as_provided_bottom_mm2",
        "raw_as_provided_top", "raw_as_provided_bottom", "av_over_s", "av_station_mm",
        "al_mm2", "al_station_mm", "at_over_s", "at_station_mm",
        "top_demand_mm2", "top_demand_station_mm",
    )
    combo_fields = (
        "top_total_combo", "top_flex_combo", "bottom_total_combo", "bottom_flex_combo",
        "av_combo", "al_combo", "at_combo", "top_demand_source", "top_demand_combo",
    )
    return {
        **{field: None for field in nullable_fields},
        **{field: "" for field in combo_fields},
        "source_station_count": 0,
        "source_objects": (),
        "units": "",
        "result_source": "",
        "design_errors": "",
        "design_warnings": "",
    }


def _attach_region_evidence(
    control: dict[str, Any],
    stations: Iterable[DesignStation],
    *,
    basis: str,
    missing_reason: str,
) -> None:
    items = tuple(stations)
    locations = [station.location for station in items]
    fields = {}
    for field, value in control.items():
        if field in {"region_evidence", "field_evidence"} or field in {
            "source_station_count", "source_objects", "units", "result_source",
            "design_errors", "design_warnings",
        }:
            continue
        available = value is not None and (not isinstance(value, str) or bool(value))
        fields[field] = {
            "status": "AVAILABLE" if available else "UNKNOWN",
            "basis": basis,
            "basis_reason": (
                "REGION_NOT_INTERSECTING_SEGMENT_FULL_RANGE_ENVELOPE"
                if basis == "SEGMENT_FULL_RANGE_ENVELOPE" else ""
            ),
            "station_count": len(items),
            "station_locations_mm": locations,
            "missing_reason": "" if available else missing_reason if not items else "SOURCE_FIELD_UNAVAILABLE",
        }
    control["region_evidence"] = {
        "status": "AVAILABLE" if items else "UNKNOWN",
        "basis": basis,
        "basis_reason": (
            "REGION_NOT_INTERSECTING_SEGMENT_FULL_RANGE_ENVELOPE"
            if basis == "SEGMENT_FULL_RANGE_ENVELOPE" else ""
        ),
        "station_count": len(items),
        "station_locations_mm": locations,
        "missing_reason": "" if items else missing_reason,
    }
    control["field_evidence"] = fields


def _maximum_available(values: Iterable[float | None]) -> float | None:
    available = [value for value in values if value is not None]
    return max(available, default=None)
