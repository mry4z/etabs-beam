from __future__ import annotations

from math import pi
from types import SimpleNamespace

from src.design.detailing import (
    BarSpec,
    FaceRebarDesign,
    FaceRebarMode,
    LongitudinalDesign,
    PhysicalRow,
    StirrupDesign,
)
from src.design.v3 import (
    _deduplicate_ranked_materialized,
    _physical_line_fingerprint,
)


def _line_result(
    *, x_shift=0.0, middle_spacing=150, main_count=4, side_count=2,
    seed_hoop_legs=4,
):
    def longitudinal():
        x = (58.0, 119.333333, 180.666667, 242.0)
        if main_count != 4:
            x = tuple(x[:main_count])
        x = tuple(value + x_shift for value in x)
        return LongitudinalDesign(
            rows=(PhysicalRow(main_count, 16, role="longitudinal"),),
            required_area_mm2=742.0,
            row_centers_mm=(58.0,),
            row_x_mm=(x,),
            effective_depth_mm=742.0,
        )

    regions = []
    for name, spacing in (
        ("left", 150),
        ("middle", middle_spacing),
        ("right", 150),
    ):
        regions.append(SimpleNamespace(
            name=name,
            top=longitudinal(),
            bottom=longitudinal(),
            stirrup=StirrupDesign(
                diameter_mm=10,
                spacing_mm=spacing,
                legs=4,
                provided_av_over_s_mm2_per_mm=4 * (pi * 10**2 / 4) / spacing,
                provided_at_over_s_mm2_per_mm=0.0,
                maximum_spacing_mm=200.0,
                governing_reason="TEST_PHYSICAL_FINGERPRINT",
            ),
        ))

    side_bar = BarSpec(
        diameter_mm=10,
        count=side_count,
        layers=(side_count,),
        area_mm2=pi * 10**2 / 4,
        role="face",
    )
    return SimpleNamespace(
        beam_name="GF-FG-SB1-1",
        section=SimpleNamespace(name="B 300X800", width_mm=300.0, depth_mm=800.0),
        regions=tuple(regions),
        # Search-only seed metadata is deliberately absent from the physical key.
        hoop_seed_signature=(10, 150, seed_hoop_legs),
        face_rebar=FaceRebarDesign(
            mode=FaceRebarMode.SKIN,
            bars_each_side=side_bar,
            required_each_side_mm2=0.0,
            provided_each_side_mm2=side_count * side_bar.area_mm2,
            control_reason="TEST_SIDE_CONSTRUCTION",
        ),
    )


def test_physical_line_fingerprint_includes_aligned_none_rows_coordinates_hoops_and_side():
    # The physical data below is the saved GF-FG-SB1 rank-9/rank-10 layout:
    # four T16 per top/bottom region, T10@150/4, and 2T10 construction bars.
    base = _line_result()
    same_physical = _line_result(seed_hoop_legs=6)

    assert _physical_line_fingerprint((base, None, base)) == _physical_line_fingerprint(
        (same_physical, None, same_physical)
    )
    assert _physical_line_fingerprint((base, None, base)) != _physical_line_fingerprint(
        (base, base, None)
    )
    assert _physical_line_fingerprint((base, None, base)) != _physical_line_fingerprint(
        (_line_result(x_shift=1.0), None, base)
    )
    assert _physical_line_fingerprint((base, None, base)) != _physical_line_fingerprint(
        (_line_result(middle_spacing=160), None, base)
    )
    assert _physical_line_fingerprint((base, None, base)) != _physical_line_fingerprint(
        (_line_result(side_count=3), None, base)
    )
    assert _physical_line_fingerprint((base, None, base)) != _physical_line_fingerprint(
        (_line_result(main_count=3), None, base)
    )


def test_ranked_physical_dedup_keeps_first_unique_and_does_not_refill_attempts():
    first = _line_result()
    duplicate_of_first = _line_result()
    second_physical_plan = _line_result(middle_spacing=160)
    attempted = [
        ((1,), (first, None, first)),
        ((0,), (duplicate_of_first, None, duplicate_of_first)),
        ((2,), (second_physical_plan, None, second_physical_plan)),
    ]
    audit = {
        "complete_materializations": 3,
        "complete_validation_calls": 3,
        "complete_validation_passes": 3,
        "duplicate_physical_schemes_discarded": 0,
        "candidate_count": 0,
    }

    unique, discarded = _deduplicate_ranked_materialized(attempted, audit)

    assert [rank for rank, _path in unique] == [(0,), (2,)]
    assert discarded == 1
    assert len(unique) == 2
    assert audit["complete_materializations"] == 3
    assert audit["complete_validation_calls"] == 3
    assert audit["complete_validation_passes"] == 3
    assert audit["duplicate_physical_schemes_discarded"] == 1
    assert audit["candidate_count"] == 2
    assert len(attempted) == 3
