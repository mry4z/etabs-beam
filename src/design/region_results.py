"""Shared left/middle/right envelopes for span-level beam design outputs."""

from __future__ import annotations

from typing import Any, Iterable

from src.design.provider import BeamDesignResult, DesignStation


def group_design_stations(
    design: BeamDesignResult,
    length_mm: float,
    depth_mm: float,
    *,
    backfill_empty: bool = True,
) -> dict[str, tuple[DesignStation, ...]] | None:
    """Partition a span into the existing 2h end zones and middle zone."""

    if not design.stations or length_mm <= 0 or depth_mm <= 0:
        return None
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
) -> dict[str, dict[str, Any]] | None:
    """Build the workbook-compatible control envelope for each span region."""

    groups = group_design_stations(design, length_mm, depth_mm)
    if groups is None:
        return None
    return {name: _control_values(stations) for name, stations in groups.items()}


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


def _maximum_available(values: Iterable[float | None]) -> float | None:
    available = [value for value in values if value is not None]
    return max(available, default=None)
