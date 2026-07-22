"""ACI 318-14 single-layer longitudinal bar capacity lookup.

The public lookup uses engineering dimensions directly so callers do not need
to translate beam widths or bar diameters into array indices.  Standard project
combinations are precomputed; non-standard combinations use the same formula.

``clear_cover_mm`` is measured from the concrete surface to the outer face of
the stirrup.
"""

from __future__ import annotations

import math
from typing import Final

BEAM_WIDTHS_MM: Final = (200, 250, 300, 400, 500, 600)
CLEAR_COVERS_TO_STIRRUP_OUTER_MM: Final = (40, 50, 60, 75)
STIRRUP_DIAMETERS_MM: Final = (10, 12, 16)
MAX_AGGREGATE_SIZES_MM: Final = (20, 25)
LONGITUDINAL_BAR_DIAMETERS_MM: Final = (10, 12, 16, 20, 25)

SingleLayerCapacityKey = tuple[int, int, int, int, int]


def minimum_clear_bar_spacing_mm(
    longitudinal_bar_diameter_mm: float,
    maximum_aggregate_size_mm: float,
) -> float:
    """Return the ACI 318-14 minimum clear spacing between parallel bars."""

    if longitudinal_bar_diameter_mm <= 0 or maximum_aggregate_size_mm <= 0:
        raise ValueError("bar diameter and aggregate size must be positive")
    return max(
        25.0,
        float(longitudinal_bar_diameter_mm),
        4.0 * float(maximum_aggregate_size_mm) / 3.0,
    )


def calculate_single_layer_max_bars(
    beam_width_mm: float,
    clear_cover_mm: float,
    stirrup_diameter_mm: float,
    maximum_aggregate_size_mm: float,
    longitudinal_bar_diameter_mm: float,
) -> int:
    """Calculate the maximum number of equal bars that fit in one layer."""

    dimensions = (
        beam_width_mm,
        clear_cover_mm,
        stirrup_diameter_mm,
        maximum_aggregate_size_mm,
        longitudinal_bar_diameter_mm,
    )
    if any(value <= 0 for value in dimensions):
        raise ValueError("all section and reinforcement dimensions must be positive")

    inside_stirrup_width_mm = beam_width_mm - 2.0 * (clear_cover_mm + stirrup_diameter_mm)
    if inside_stirrup_width_mm < longitudinal_bar_diameter_mm:
        return 0

    clear_spacing_mm = minimum_clear_bar_spacing_mm(
        longitudinal_bar_diameter_mm,
        maximum_aggregate_size_mm,
    )
    # n*db + (n-1)*s_clear <= available inside-stirrup width.
    return max(
        0,
        math.floor(
            (inside_stirrup_width_mm + clear_spacing_mm + 1e-9) / (longitudinal_bar_diameter_mm + clear_spacing_mm)
        ),
    )


ACI318_14_SINGLE_LAYER_MAX_BARS: Final[dict[SingleLayerCapacityKey, int]] = {
    (beam_width, cover, stirrup, aggregate, longitudinal_bar): (
        calculate_single_layer_max_bars(
            beam_width,
            cover,
            stirrup,
            aggregate,
            longitudinal_bar,
        )
    )
    for beam_width in BEAM_WIDTHS_MM
    for cover in CLEAR_COVERS_TO_STIRRUP_OUTER_MM
    for stirrup in STIRRUP_DIAMETERS_MM
    for aggregate in MAX_AGGREGATE_SIZES_MM
    for longitudinal_bar in LONGITUDINAL_BAR_DIAMETERS_MM
}


def single_layer_max_bars(
    beam_width_mm: float,
    clear_cover_mm: float,
    stirrup_diameter_mm: float,
    maximum_aggregate_size_mm: float,
    longitudinal_bar_diameter_mm: float,
) -> int:
    """Look up a standard combination, falling back for non-standard sizes."""

    values = (
        beam_width_mm,
        clear_cover_mm,
        stirrup_diameter_mm,
        maximum_aggregate_size_mm,
        longitudinal_bar_diameter_mm,
    )
    if all(float(value).is_integer() for value in values):
        key: SingleLayerCapacityKey = (
            int(beam_width_mm),
            int(clear_cover_mm),
            int(stirrup_diameter_mm),
            int(maximum_aggregate_size_mm),
            int(longitudinal_bar_diameter_mm),
        )
        cached = ACI318_14_SINGLE_LAYER_MAX_BARS.get(key)
        if cached is not None:
            return cached

    return calculate_single_layer_max_bars(*values)
