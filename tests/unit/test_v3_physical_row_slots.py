from itertools import combinations
import math

import pytest

from src.design.v3_physical_row_slots import select_symmetric_physical_row_slots


def _combinations_oracle(slots, count, minimum_distance, width):
    """Independent small-domain oracle using exhaustive subset enumeration."""
    gap = minimum_distance - 1e-9
    feasible = []
    for candidate in combinations(sorted(set(float(value) for value in slots)), count):
        if any(right - left < gap for left, right in zip(candidate, candidate[1:])):
            continue
        if any(
            abs(candidate[index] + candidate[-1 - index] - width) > 1e-9
            for index in range(count // 2)
        ):
            continue
        if count % 2 and abs(candidate[count // 2] - width / 2.0) > 1e-9:
            continue
        feasible.append(candidate)
    return min(
        feasible,
        key=lambda values: (-(values[-1] - values[0]), values),
        default=None,
    )


@pytest.mark.parametrize(
    ("slots", "width"),
    [
        (tuple(float(value) for value in range(7)), 6.0),
        ((0.0, 1.0, 2.0, 3.0, 4.0), 4.0),
        ((5.0, 20.0, 48.0, 75.0, 125.0, 152.0, 180.0, 195.0), 200.0),
        ((12.345678, 39.25, 150.0, 260.75, 287.654322), 300.0),
        ((0.0, 1.0, 2.0, 3.0, 5.0, 6.0, 7.0, 8.0), 8.0),
    ],
)
def test_selector_matches_exhaustive_combinations_oracle(slots, width):
    for count in range(2, len(slots) + 2):
        for minimum_distance in (0.0, 0.75, 1.0, 1.0000000005, 2.5, 6.0):
            assert select_symmetric_physical_row_slots(
                slots, count, minimum_distance, width
            ) == _combinations_oracle(slots, count, minimum_distance, width)


def test_selector_requires_center_for_odd_rows_and_preserves_source_slots():
    slots = (0.0, 4.0 / 3.0, 8.0 / 3.0, 4.0)

    assert select_symmetric_physical_row_slots(slots, 2, 0.0, 4.0) == (0.0, 4.0)
    assert select_symmetric_physical_row_slots(slots, 3, 0.0, 4.0) is None
    assert select_symmetric_physical_row_slots(slots, 4, 0.0, 4.0) == slots


def test_five_slot_outer_row_supports_two_and_three_bar_symmetric_inset_rows():
    outer = (0.0, 1.0, 2.0, 3.0, 4.0)
    five = select_symmetric_physical_row_slots(outer, 5, 0.0, 4.0)
    assert five == outer
    assert select_symmetric_physical_row_slots(five, 2, 0.0, 4.0) == (0.0, 4.0)
    assert select_symmetric_physical_row_slots(five, 3, 0.0, 4.0) == (0.0, 2.0, 4.0)


def test_four_slot_outer_row_cannot_supply_three_bar_symmetric_inset_row():
    outer = (0.0, 4.0 / 3.0, 8.0 / 3.0, 4.0)
    assert select_symmetric_physical_row_slots(outer, 4, 0.0, 4.0) == outer
    assert select_symmetric_physical_row_slots(outer, 3, 0.0, 4.0) is None


def test_three_nested_rows_remain_symmetric_subsets_at_each_layer():
    slots = tuple(float(value) for value in range(9))
    outer = select_symmetric_physical_row_slots(slots, 5, 2.0, 8.0)
    middle = select_symmetric_physical_row_slots(outer, 3, 2.0, 8.0)
    inner = select_symmetric_physical_row_slots(middle, 2, 2.0, 8.0)

    assert outer == (0.0, 2.0, 4.0, 6.0, 8.0)
    assert middle == (0.0, 4.0, 8.0)
    assert inner == (0.0, 8.0)
    assert set(inner) <= set(middle) <= set(outer) <= set(slots)
    for row in (outer, middle, inner):
        assert all(math.isclose(a + b, 8.0, abs_tol=1e-9) for a, b in zip(row, reversed(row)))
        assert all(b - a >= 2.0 - 1e-9 for a, b in zip(row, row[1:]))


def test_selector_rejects_single_bar_rows_and_returns_none_without_enough_mirror_pairs():
    with pytest.raises(ValueError, match="count must be at least two"):
        select_symmetric_physical_row_slots((0.0, 2.0, 4.0), 1, 1.0, 4.0)
    with pytest.raises(ValueError, match="count must be at least two"):
        select_symmetric_physical_row_slots((0.0, 2.0, 4.0), 0, 1.0, 4.0)

    assert select_symmetric_physical_row_slots((0.0, 1.0, 3.0, 4.0), 3, 1.0, 4.0) is None
