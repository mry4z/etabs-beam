from itertools import combinations
import math

import pytest

from src.design.physical_row_slots import select_physical_row_slots


def _legacy_select(slots, count, minimum_distance, width):
    feasible = (
        candidate
        for candidate in combinations(slots, count)
        if all(
            right - left >= minimum_distance - 1e-9
            for left, right in zip(candidate, candidate[1:])
        )
    )
    return min(
        feasible,
        key=lambda values: (
            abs(values[0] + values[-1] - width),
            -(values[-1] - values[0]),
            values,
        ),
        default=None,
    )


@pytest.mark.parametrize(
    "slots",
    [
        tuple(float(value) for value in range(8)),
        (0.0, 0.4, 1.7, 2.1, 4.8, 6.0, 9.25),
        (-3.5, -1.0, -0.25, 2.0, 2.5, 7.75, 12.0),
    ],
)
def test_selector_matches_legacy_for_uniform_and_nonuniform_domains(slots):
    widths = (-4.0, 0.0, 2.5, 6.5, 11.0, 20.0)
    distances = (0.0, 0.4, 1.0, 1.0000000005, 2.5, 5.0, 15.0)

    for count in range(1, len(slots) + 2):
        for minimum_distance in distances:
            for width in widths:
                assert select_physical_row_slots(
                    slots, count, minimum_distance, width
                ) == _legacy_select(slots, count, minimum_distance, width)


def test_selector_matches_legacy_at_spacing_tolerance_adjacent_floats():
    minimum_distance = 3.2
    threshold = minimum_distance - 1e-9
    below = math.nextafter(threshold, -math.inf)
    above = math.nextafter(threshold, math.inf)
    # Offsetting the candidates exercises subtraction rounding as well as the
    # exact threshold and its adjacent representable values.
    slots = (10.0, 10.0 + below, 10.0 + threshold, 10.0 + above, 14.0)

    for count in range(1, len(slots) + 2):
        for width in (10.0, 24.0, 30.0):
            assert select_physical_row_slots(
                slots, count, minimum_distance, width
            ) == _legacy_select(slots, count, minimum_distance, width)


def test_selector_preserves_endpoint_and_lexicographic_tie_breaks():
    # Endpoints (0, 7) and (2, 5) have the same center error; the wider pair
    # wins. With fixed endpoints, the earliest valid interior slots win.
    assert select_physical_row_slots((0.0, 2.0, 5.0, 7.0), 2, 0.0, 8.0) == (
        0.0,
        7.0,
    )
    assert select_physical_row_slots(tuple(map(float, range(5))), 4, 1.0, 4.0) == (
        0.0,
        1.0,
        2.0,
        4.0,
    )


def test_selector_handles_wide_domain_without_combinatorial_enumeration():
    slots = tuple(float(value) for value in range(80))

    assert select_physical_row_slots(slots, 40, 1.0, 79.0) == (
        *tuple(float(value) for value in range(39)),
        79.0,
    )


def test_selector_rejects_zero_count_and_returns_none_when_count_exceeds_slots():
    with pytest.raises(ValueError, match="count must be at least one"):
        select_physical_row_slots((0.0, 1.0), 0, 1.0, 1.0)

    assert select_physical_row_slots((0.0, 1.0), 3, 1.0, 1.0) is None
