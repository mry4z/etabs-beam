"""Small independent Cartesian checks for bounded v3 path search."""

from __future__ import annotations

from itertools import product
from types import SimpleNamespace

from src.design.detailing import ContinuityOption, LongitudinalDesign, PhysicalRow
from src.design import v3


def _design(count: int, diameter: int = 16) -> LongitudinalDesign:
    return LongitudinalDesign(rows=(PhysicalRow(count, diameter),), required_area_mm2=0.0)


def _axis_signature(design: LongitudinalDesign):
    return tuple((row.count, row.diameter_mm, row.role) for row in design.rows)


def test_independent_cartesian_oracle_exposes_axis_pruning_loss_under_coupled_minimum():
    # The local demand is 400 mm2, while a later coupled check needs at least
    # 700 mm2. The third root-count option is the only option that meets the
    # downstream requirement but is cut by the two-state axis compression.
    local_axis = tuple(
        LongitudinalDesign(
            rows=(PhysicalRow(count, 16),),
            required_area_mm2=400.0,
        )
        for count in (2, 3, 4)
    )
    coupled_minimum = 700.0

    oracle = [design for design in local_axis if design.provided_area_mm2 >= coupled_minimum]
    compressed = v3._compress_axis(local_axis)
    searched = [design for design in compressed if design.provided_area_mm2 >= coupled_minimum]

    assert len(oracle) == 1
    assert _axis_signature(oracle[0]) == ((4, 16, "longitudinal"),)
    assert searched == oracle


def test_middle_bottom_search_keeps_the_only_coupled_pair_from_cartesian_oracle():
    middle = (_design(2, 16), _design(2, 20), _design(2, 25))
    bottoms = (_design(2, 16), _design(2, 20), _design(2, 25))
    required_middle = _axis_signature(middle[2])
    required_bottom = _axis_signature(bottoms[2])

    oracle = [
        (middle_design, bottom_design)
        for middle_design, bottom_design in product(middle, bottoms)
        if _axis_signature(middle_design) == required_middle
        and _axis_signature(bottom_design) == required_bottom
    ]
    searched_pairs = tuple(v3._best_first_pairs(middle, bottoms))
    bounded = [
        (middle_design, bottom_design)
        for middle_design, bottom_design in searched_pairs
        if _axis_signature(middle_design) == required_middle
        and _axis_signature(bottom_design) == required_bottom
    ]

    assert len(oracle) == 1
    assert len(searched_pairs) == 9
    assert bounded == oracle


def _unit_option(
    name,
    left,
    right,
    preference,
    heuristic,
    *,
    pb_vector=(),
    indices=None,
    record_count=1,
):
    records = tuple(
        SimpleNamespace(heuristic=(heuristic, name, record_index))
        for record_index in range(record_count)
    )
    return SimpleNamespace(
        indices=tuple(indices) if indices is not None else (name,),
        records=records,
        left_support=left,
        right_support=right,
        pb_row_diameters=pb_vector,
        preference_tier=preference,
        heuristic=(heuristic, name),
    )


def _oracle_rank(path):
    longitudinal = tuple(
        max((unit.preference_tier[index] for unit in path), default=0)
        for index in range(3)
    )
    hoop_penalty = sum(unit.preference_tier[3] for unit in path)
    return (
        (*longitudinal, hoop_penalty),
        tuple(record.heuristic for unit in path for record in unit.records),
        tuple((unit.left_support, unit.right_support) for unit in path),
    )


def _independent_strict_support_compatible(candidate, required):
    return bool(candidate and required) and len(candidate) == len(required) and all(
        left[1] == right[1] for left, right in zip(candidate, required)
    )


def _independent_support_compatible(candidate, required, *, exact):
    if required is None:
        return True
    if not candidate or not required or candidate[0][1] != required[0][1]:
        return False
    if exact and len(candidate) != len(required):
        return False
    return all(left[1] == right[1] for left, right in zip(candidate, required))


def _independent_merge_pb_vector(left, right):
    if not left:
        return tuple(right)
    if not right:
        return tuple(left)
    size = max(len(left), len(right))
    merged = []
    for index in range(size):
        a = left[index] if index < len(left) else None
        b = right[index] if index < len(right) else None
        if a is not None and b is not None and a != b:
            return None
        merged.append(a if a is not None else b)
    while merged and merged[-1] is None:
        merged.pop()
    return tuple(merged)


def test_coordinate_paths_match_independent_cartesian_oracle_when_all_fit_under_k():
    first = (
        _unit_option("a0", ((2, 25),), ((2, 16),), (0, 0, 0, 0), 0.0),
        _unit_option("a1", ((2, 25),), ((2, 20),), (0, 0, 0, 1), 0.0),
    )
    second = (
        _unit_option("b0", ((2, 16),), ((2, 16),), (0, 0, 0, 0), 0.2),
        _unit_option("b1", ((2, 16),), ((2, 20),), (0, 0, 0, 1), 0.1),
        _unit_option("b2", ((2, 20),), ((2, 20),), (0, 0, 0, 0), 0.3),
    )
    third = (
        _unit_option("c0", ((2, 16),), ((2, 16),), (0, 0, 0, 0), 0.1),
        _unit_option("c1", ((2, 20),), ((2, 20),), (0, 0, 0, 1), 0.1),
    )
    units = tuple(
        (
            (index,),
            choices,
            (SimpleNamespace(section=SimpleNamespace(is_secondary=False)),),
        )
        for index, choices in enumerate((first, second, third))
    )
    k = 10

    oracle = [
        path
        for path in product(first, second, third)
        if _independent_strict_support_compatible(path[1].left_support, path[0].right_support)
        and _independent_strict_support_compatible(path[2].left_support, path[1].right_support)
    ]
    oracle.sort(key=_oracle_rank)
    actual = v3._coordinate_units(
        units,
        ContinuityOption.OPTION_1_STRICT_EQUAL,
        SimpleNamespace(),
        k=k,
    )

    assert len(oracle) <= 10
    assert actual == tuple(oracle)


def test_coordinate_pattern_index_matches_pairwise_oracle_with_parent_reindex_and_pb_vector():
    ordinary = SimpleNamespace(section=SimpleNamespace(is_secondary=False))
    first = (
        _unit_option("a0", ((2, 16),), ((2, 16), (2, 25)), (0, 0, 0, 0), 0.0,
                     pb_vector=(20,)),
        _unit_option("a1", ((2, 16),), ((3, 16),), (0, 0, 0, 1), 0.1,
                     pb_vector=(25,)),
    )
    parent = (
        # This option is incompatible with both the preceding and following
        # interfaces; backward reachability removes it before forward indexing.
        _unit_option("dead", ((9, 25),), ((3, 25),), (0, 0, 0, 0), 0.0,
                     pb_vector=(20,), indices=(1, 2), record_count=2),
        _unit_option("b0", ((7, 16), (3, 25)), ((3, 20),), (0, 0, 0, 0), 0.2,
                     pb_vector=(20,), indices=(1, 2), record_count=2),
        _unit_option("b1", ((4, 16),), ((6, 20), (2, 25)), (0, 0, 0, 1), 0.3,
                     pb_vector=(25,), indices=(1, 2), record_count=2),
    )
    last = (
        _unit_option("c0", ((8, 20),), ((3, 16),), (0, 0, 0, 0), 0.4,
                     pb_vector=(20,)),
        _unit_option("c1", ((4, 20), (4, 25)), ((4, 16),), (0, 0, 0, 1), 0.5,
                     pb_vector=(25,)),
    )
    units = (
        ((0,), first, (ordinary,)),
        ((1, 2), parent, (ordinary, ordinary)),
        ((3,), last, (ordinary,)),
    )

    for option in (
        ContinuityOption.OPTION_1_STRICT_EQUAL,
        ContinuityOption.OPTION_2_COUNT_ADJUSTMENT,
    ):
        exact = option is ContinuityOption.OPTION_1_STRICT_EQUAL
        oracle = []
        for path in product(first, parent, last):
            if not all(
                _independent_support_compatible(
                    following.left_support,
                    previous.right_support,
                    exact=exact,
                )
                for previous, following in zip(path, path[1:])
            ):
                continue
            vector = ()
            for candidate in path:
                vector = _independent_merge_pb_vector(
                    vector,
                    candidate.pb_row_diameters,
                )
                if vector is None:
                    break
            if vector is not None:
                oracle.append(path)
        oracle.sort(key=_oracle_rank)
        audit = {}
        actual = v3._coordinate_units(
            units,
            option,
            SimpleNamespace(),
            k=10,
            audit=audit,
        )

        assert len(oracle) == 2
        assert actual == tuple(oracle)
        assert audit["backward_pruned"] == 1
        assert audit["coordinate_support_pattern_pair_checks"] > 0
