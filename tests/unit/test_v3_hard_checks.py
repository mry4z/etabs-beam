from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.design.detailing import DetailingError, LongitudinalDesign, PhysicalRow
from src.design.v3_hard_checks import (
    prepare_light_regions,
    v3_hard_check_cache_statistics,
)


def _case(*, width=300.0, diameter=20, count=2, demand=300.0, hoop_legs=2, is_smf=False):
    section = SimpleNamespace(
        name="T1-PB1",
        width_mm=width,
        depth_mm=600.0,
        fc_mpa=30.0,
        fy_mpa=420.0,
        is_smf=is_smf,
        is_secondary=False,
    )
    design = LongitudinalDesign(
        rows=(PhysicalRow(count=count, diameter_mm=diameter, role="longitudinal"),),
        required_area_mm2=demand,
        effective_depth_mm=500.0,
        row_x_mm=((40.0, 260.0),) if count == 2 else (tuple(float(x) for x in range(count)),),
    )
    regions = tuple(
        SimpleNamespace(
            name=name,
            demand=SimpleNamespace(as_top_total_mm2=demand, as_bottom_total_mm2=demand),
            as_min_mm2=100.0,
            stirrup=SimpleNamespace(diameter_mm=10, spacing_mm=100, legs=hoop_legs),
            top=design,
            bottom=design,
        )
        for name in ("end_left", "middle", "end_right")
    )
    project = SimpleNamespace(
        max_main_bar_layers=3,
        catalog=SimpleNamespace(diameters_mm=(10, 12, 16, 20, 25)),
        sb_hanger_diameter_mm=12,
        minimum_credit_hanger_diameter_mm=12,
        smf_max_longitudinal_ratio=0.02,
        ordered_longitudinal_diameters_for=lambda _section: (20, 25),
        ordered_stirrup_diameters=lambda: (10, 12),
        allowed_spacings_for=lambda _section, _name: (100, 150, 200),
    )
    code = SimpleNamespace(
        minimum_legs=lambda section, _region: (
            2 if section.width_mm < 400 else 4 if section.width_mm < 550 else 6
        ),
        minimum_longitudinal_area=lambda *_args: 100.0,
        effective_depth=lambda *_args: 500.0,
    )
    engine = SimpleNamespace(
        project=project,
        code=code,
        strict_symmetric_rows=True,
        uniform_stirrup_diameter_per_span=True,
        _final_main_db=lambda _region, _section: diameter,
        _finalize_stirrup_controls=lambda _section, chosen: tuple(chosen),
    )

    def validate_revision_layout(_section, chosen):
        if len({region.stirrup.diameter_mm for region in chosen}) > 1:
            raise DetailingError("POLICY_SPAN_STIRRUP_DIAMETER_MUST_BE_UNIFORM")

    engine._validate_revision_layout = validate_revision_layout
    return SimpleNamespace(section=section), regions, engine


def test_prepare_light_regions_checks_physical_rows_areas_and_selected_stirrups():
    data, regions, engine = _case()
    checked_regions = []
    engine._finalize_stirrup_controls = lambda _section, chosen: (
        checked_regions.append(tuple(chosen)) or tuple(chosen)
    )

    assert prepare_light_regions(data, regions, engine) == regions
    assert checked_regions == [(region,) for region in regions]


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"diameter": 16}, "V3_MAIN_BAR_DIAMETER_OUTSIDE_ROLE_CATALOGUE"),
        ({"demand": 700.0}, "V3_TOTAL_LONGITUDINAL_AREA_FAILED"),
        ({"hoop_legs": 4}, "FIRST_ROW_HOOP_SUPPORT_FAILED"),
    ],
)
def test_prepare_light_regions_rejects_invalid_candidate_hard_checks(changes, expected):
    data, regions, engine = _case(**changes)

    with pytest.raises(DetailingError, match=expected):
        prepare_light_regions(data, regions, engine)


@pytest.mark.parametrize(
    "width,hoop_legs",
    [(400.0, 2), (500.0, 2), (550.0, 4)],
)
def test_prepare_light_regions_rejects_v3_hoops_below_width_minimum(width, hoop_legs):
    data, regions, engine = _case(width=width, hoop_legs=hoop_legs)

    with pytest.raises(DetailingError, match="V3_MINIMUM_HOOP_LEG_COUNT_FAILED"):
        prepare_light_regions(data, regions, engine)


def test_prepare_light_regions_rejects_non_symmetric_candidate_geometry():
    data, regions, engine = _case()
    bad_face = SimpleNamespace(
        **{
            **vars(regions[0].top),
            "row_x_mm": ((40.0, 250.0),),
        }
    )
    bad_region = SimpleNamespace(**{**vars(regions[0]), "top": bad_face})
    regions = (bad_region, *regions[1:])

    with pytest.raises(DetailingError, match="V3_ROW_MIRROR_SYMMETRY_FAILED"):
        prepare_light_regions(data, regions, engine)


def test_prepare_light_regions_applies_existing_smf_longitudinal_limit():
    data, regions, engine = _case(is_smf=True)
    engine.project.smf_max_longitudinal_ratio = 0.001

    with pytest.raises(DetailingError, match="V3_SMF_LONGITUDINAL_HARD_CHECK_FAILED:SMF_LONGITUDINAL_RATIO_FAILED"):
        prepare_light_regions(data, regions, engine)


def test_region_cache_reuses_exact_success_and_reports_bounded_counters():
    data, regions, engine = _case()
    finalizations = []
    engine._finalize_stirrup_controls = lambda _section, chosen: (
        finalizations.append(chosen[0].name) or tuple(chosen)
    )

    assert prepare_light_regions(data, regions, engine) == regions
    assert prepare_light_regions(data, regions, engine) == regions

    assert finalizations == ["end_left", "middle", "end_right"]
    assert v3_hard_check_cache_statistics(engine) == {
        "capacity": 4096,
        "entries": 3,
        "lookups": 6,
        "hits": 3,
        "success_hits": 3,
        "failure_hits": 0,
        "misses": 3,
        "success_stores": 3,
        "failure_stores": 0,
        "evictions": 0,
    }


def test_region_cache_misses_when_demand_or_physical_coordinates_change():
    data, regions, engine = _case()
    finalizations = []
    engine._finalize_stirrup_controls = lambda _section, chosen: (
        finalizations.append(chosen[0].name) or tuple(chosen)
    )
    prepare_light_regions(data, regions, engine)

    changed_demand = SimpleNamespace(
        **{**vars(regions[0].demand), "as_top_total_mm2": 301.0}
    )
    demand_region = SimpleNamespace(**{**vars(regions[0]), "demand": changed_demand})
    prepare_light_regions(data, (demand_region, *regions[1:]), engine)

    moved_design = LongitudinalDesign(
        rows=regions[1].top.rows,
        required_area_mm2=regions[1].top.required_area_mm2,
        effective_depth_mm=regions[1].top.effective_depth_mm,
        row_x_mm=((30.0, 270.0),),
        constraint_sources=regions[1].top.constraint_sources,
    )
    moved_region = SimpleNamespace(
        **{**vars(regions[1]), "top": moved_design, "bottom": moved_design}
    )
    prepare_light_regions(data, (regions[0], moved_region, regions[2]), engine)

    counters = v3_hard_check_cache_statistics(engine)
    assert counters["misses"] == 5
    assert counters["hits"] == 4
    assert counters["entries"] == 5
    assert len(finalizations) == 5


def test_region_cache_replays_deterministic_failures():
    data, regions, engine = _case(demand=700.0)
    finalizations = []
    engine._finalize_stirrup_controls = lambda _section, chosen: (
        finalizations.append(chosen[0].name) or tuple(chosen)
    )

    for _ in range(2):
        with pytest.raises(DetailingError, match="V3_TOTAL_LONGITUDINAL_AREA_FAILED"):
            prepare_light_regions(data, regions, engine)

    assert finalizations == ["end_left"]
    counters = v3_hard_check_cache_statistics(engine)
    assert counters["misses"] == 1
    assert counters["failure_stores"] == 1
    assert counters["failure_hits"] == 1
    assert counters["entries"] == 1


def test_revision_layout_still_rejects_a_different_region_hoop_diameter():
    data, regions, engine = _case()
    finalized_regions = []
    engine._finalize_stirrup_controls = lambda _section, chosen: (
        finalized_regions.extend(chosen) or tuple(chosen)
    )
    changed_hoop = SimpleNamespace(**{**vars(regions[1].stirrup), "diameter_mm": 12})
    changed_region = SimpleNamespace(**{**vars(regions[1]), "stirrup": changed_hoop})

    with pytest.raises(DetailingError, match="POLICY_SPAN_STIRRUP_DIAMETER_MUST_BE_UNIFORM"):
        prepare_light_regions(data, (regions[0], changed_region, regions[2]), engine)

    assert finalized_regions == []
    assert v3_hard_check_cache_statistics(engine)["entries"] == 0
