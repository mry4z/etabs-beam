"""Symmetric selection of physical reinforcement row slots for v3."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence


_MIRROR_TOLERANCE = 1e-9
_GAP_TOLERANCE = 1e-9


def select_symmetric_physical_row_slots(
    slots: Sequence[float],
    count: int,
    minimum_distance: float,
    width: float,
) -> tuple[float, ...] | None:
    """Choose a symmetric subset of slots, preferring the widest outer span.

    Slot coordinates are preserved from ``slots``.  A mirrored pair may differ
    from the exact floating-point reflection by at most 1e-9 mm; this only
    absorbs coordinate-generation rounding.  Odd rows require a slot at the
    section center.  Ties are resolved by the lexicographically smallest
    ascending coordinate tuple.
    """
    if count < 2:
        raise ValueError("count must be at least two")

    values = tuple(sorted({float(value) for value in slots}))
    if count > len(values):
        return None

    center = float(width) / 2.0
    center_value: float | None = None
    if count % 2:
        centers = tuple(
            value for value in values
            if abs(value - center) <= _MIRROR_TOLERANCE
        )
        if not centers:
            return None
        center_value = min(centers, key=lambda value: (abs(value - center), value))

    left_values = tuple(value for value in values if value < center)
    right_values = tuple(value for value in values if value > center)
    pairs: list[tuple[float, float]] = []
    used_right: set[int] = set()
    for left in left_values:
        target = float(width) - left
        index = bisect_left(right_values, target)
        matches = []
        for candidate_index in (index - 1, index):
            if 0 <= candidate_index < len(right_values) and candidate_index not in used_right:
                right = right_values[candidate_index]
                error = abs((left + right) - float(width))
                if error <= _MIRROR_TOLERANCE:
                    matches.append((error, right, candidate_index))
        if matches:
            _error, right, candidate_index = min(matches)
            pairs.append((left, right))
            used_right.add(candidate_index)

    pair_count = count // 2
    if pair_count > len(pairs):
        return None

    gap = float(minimum_distance) - _GAP_TOLERANCE
    best: tuple[float, ...] | None = None
    best_key: tuple[float, tuple[float, ...]] | None = None

    for outer_index, outer_pair in enumerate(pairs):
        selected = [outer_pair]
        cursor = outer_index + 1
        while len(selected) < pair_count:
            previous_left, previous_right = selected[-1]
            next_choice: tuple[int, tuple[float, float]] | None = None
            for pair_index in range(cursor, len(pairs)):
                left, right = pairs[pair_index]
                if left - previous_left >= gap and previous_right - right >= gap:
                    next_choice = (pair_index, (left, right))
                    break
            if next_choice is None:
                break
            cursor, pair = next_choice
            selected.append(pair)
            cursor += 1

        if len(selected) != pair_count:
            continue

        inner_left, inner_right = selected[-1]
        if center_value is not None:
            if center_value - inner_left < gap or inner_right - center_value < gap:
                continue
        elif inner_right - inner_left < gap:
            continue

        candidate = (
            tuple(pair[0] for pair in selected)
            + ((center_value,) if center_value is not None else ())
            + tuple(pair[1] for pair in reversed(selected))
        )
        if any(
            right - left < gap
            for left, right in zip(candidate, candidate[1:])
        ):
            continue
        key = (-(candidate[-1] - candidate[0]), candidate)
        if best_key is None or key < best_key:
            best_key = key
            best = candidate

    return best
