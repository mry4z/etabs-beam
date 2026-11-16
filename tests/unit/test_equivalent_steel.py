from math import pi
from types import SimpleNamespace as NS

from src.design.equivalent_steel import equivalent_steel_mm3
from src.design.detailing import FaceRebarMode


def _row(count, diameter, role="longitudinal"):
    return NS(count=count, diameter_mm=diameter, role=role)


def _sample_result():
    regions = []
    for name, top_rows, spacing in (
        ("left", (_row(2, 20), _row(2, 12, "hanger")), 100),
        ("middle", (_row(2, 20),), 200),
        ("right", (_row(2, 20),), 100),
    ):
        regions.append(NS(
            name=name,
            top=NS(rows=top_rows, row_centers_mm=(), row_x_mm=()),
            bottom=NS(rows=(_row(4, 20),), row_centers_mm=(), row_x_mm=()),
            stirrup=NS(diameter_mm=10, spacing_mm=spacing, legs=2),
        ))
    return NS(
        section=NS(
            length_mm=10000.0,
            width_mm=300.0,
            depth_mm=600.0,
            clear_cover_to_stirrup_outer_mm=40.0,
            etabs_longitudinal_cover_mm=None,
        ),
        regions=tuple(regions),
        face_rebar=NS(mode=FaceRebarMode.NOT_APPLICABLE, bars_each_side=None),
    )


def test_equivalent_steel_counts_physical_hangers_and_clipped_2h_regions():
    result = _sample_result()
    measure = equivalent_steel_mm3(result)

    as20 = pi * 20**2 / 4
    as12 = pi * 12**2 / 4
    expected_longitudinal = 10000.0 * (4 * as20 + (2 * as20 + 2 * 2 * as20 + 2 * as20) / 4 + 2 * as12 / 4)
    link_area = pi * 10**2 / 4
    perimeter_factor = 2 * (300 - 2 * 45) + 2 * (600 - 2 * 45)
    expected_transverse = (1200 / 100 + 7600 / 200 + 1200 / 100) * link_area * perimeter_factor

    assert measure.available
    assert measure.longitudinal_mm3 == expected_longitudinal
    assert measure.transverse_mm3 == expected_transverse
    assert measure.total_mm3 == expected_longitudinal + expected_transverse


def test_serialized_row_matches_object_metric_and_missing_stirrups_are_unavailable():
    result = _sample_result()
    obj_measure = equivalent_steel_mm3(result)
    rows = {
        region.name: {
            face: [
                {"count": row.count, "diameter_mm": row.diameter_mm, "role": row.role}
                for row in getattr(region, face).rows
            ]
            for face in ("top", "bottom")
        }
        for region in result.regions
    }
    serialized = {
        "length_mm": 10000.0,
        "width_mm": 300.0,
        "depth_mm": 600.0,
        "clear_cover_to_stirrup_outer_mm": 40.0,
        "face": "N.A.",
        "longitudinal_rows": rows,
        "region_stirrups": {
            region.name: {
                "diameter_mm": region.stirrup.diameter_mm,
                "spacing_mm": region.stirrup.spacing_mm,
                "legs": region.stirrup.legs,
            }
            for region in result.regions
        },
    }
    row_measure = equivalent_steel_mm3(serialized)
    assert row_measure == obj_measure

    serialized.pop("region_stirrups")
    missing = equivalent_steel_mm3(serialized)
    assert not missing.available
    assert missing.total_mm3 is None
    assert missing.longitudinal_mm3 == obj_measure.longitudinal_mm3
