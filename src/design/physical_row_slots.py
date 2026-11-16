"""Exact selection of aligned horizontal reinforcement row slots."""

from collections.abc import Sequence


def select_physical_row_slots(
    slots: Sequence[float],
    count: int,
    minimum_distance: float,
    width: float,
) -> tuple[float, ...] | None:
    """Return the exact legacy-ranked feasible subset without enumerating combinations.

    ``slots`` must be in ascending order. Feasibility uses the same subtraction
    and tolerance as the former combinations-based selector. For each possible
    first slot, the earliest feasible interior slots are lexicographically best;
    every feasible final endpoint after those interiors is then ranked by the
    legacy endpoint objective.
    """
    if count < 1:
        raise ValueError("count must be at least one")

    slot_count = len(slots)
    if count > slot_count:
        return None

    minimum_gap = minimum_distance - 1e-9
    best_values: tuple[float, ...] | None = None
    best_key: tuple[float, float, tuple[float, ...]] | None = None

    def consider(values: tuple[float, ...]) -> None:
        nonlocal best_key, best_values
        key = (
            abs(values[0] + values[-1] - width),
            -(values[-1] - values[0]),
            values,
        )
        if best_key is None or key < best_key:
            best_key = key
            best_values = values

    if count == 1:
        for value in slots:
            consider((value,))
        return best_values

    interior_count = count - 2
    for first_index, first_value in enumerate(slots):
        interiors: list[float] = []
        previous_value = first_value
        last_interior_index = first_index

        if interior_count:
            for index in range(first_index + 1, slot_count):
                value = slots[index]
                if value - previous_value >= minimum_gap:
                    interiors.append(value)
                    previous_value = value
                    last_interior_index = index
                    if len(interiors) == interior_count:
                        break

        if len(interiors) != interior_count:
            continue

        first_endpoint_index = last_interior_index + 1 if interior_count else first_index + 1
        for last_index in range(first_endpoint_index, slot_count):
            last_value = slots[last_index]
            if last_value - previous_value < minimum_gap:
                continue
            consider((first_value, *interiors, last_value))

    return best_values
