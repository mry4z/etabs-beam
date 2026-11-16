"""End-to-end Cartesian oracle over a deliberately tiny real-layout catalog.

The beam section, demands, physical rows, coordinates, and hard/final checks
come from the frozen ordinary SB input and the real V3 lookup/geometry code.
Only this test's candidate directory is narrowed: one T10@180 hoop state,
two real T16 top layouts at each region, and one real T16 bottom layout. The
independent oracle forms and validates their complete Cartesian product; it
does not ask the V3 recommender to produce the oracle's feasible set.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import product
from types import SimpleNamespace

from src.design.detailing import (
    BeamDetailingResult,
    ContinuityOption,
    DetailingError,
    ReinforcementMode,
    RegionDetailing,
    make_fresh_detailing_engine,
)
from src.design.offline_style import DRAWING_V3_POLICY_ID
from src.design.row_candidates import candidates as physical_row_candidates
from src.design.row_solver import _required_hoop_legs, _style_hoop, finalize_rows
from src.design.v3 import design_line_v3
from src.design.v3_hard_checks import prepare_light_regions
from tools.drawing_v3_validation_data import (
    decode_frozen_line_inputs,
    read_frozen_line,
)


def _row_signature(design):
    return tuple((row.count, row.diameter_mm, row.role) for row in design.rows)


def _result_signature(result):
    return tuple(
        (
            region.name,
            _row_signature(region.top),
            tuple(tuple(round(value, 6) for value in row) for row in region.top.row_x_mm),
            _row_signature(region.bottom),
            tuple(tuple(round(value, 6) for value in row) for row in region.bottom.row_x_mm),
            (region.stirrup.diameter_mm, region.stirrup.spacing_mm, region.stirrup.legs),
        )
        for region in result.regions
    )


def _real_axis(data, engine, hoops, *, face, required, preserve_support_alternatives):
    """Read real physical rows directly from LookupCatalog, outside V3 search."""
    choices = physical_row_candidates(
        data.section,
        tuple(hoops),
        required,
        engine,
        max_layers=1,
        face=face,
        preserve_support_alternatives=preserve_support_alternatives,
    )
    unique = {}
    for design in choices:
        design = engine._with_sb_hangers(
            design, data.section, hoops[0].diameter_mm, hoops[0].legs
        )
        if design is None:
            continue
        main = tuple(row for row in design.rows if row.role != "hanger")
        if len(main) != 1 or main[0].diameter_mm != 16:
            continue
        effective_depth = design.effective_depth_mm or engine.code.effective_depth(
            data.section, hoops[0].diameter_mm, design.maximum_main_diameter_mm
        )
        minimum = engine.code.minimum_longitudinal_area(
            data.section.width_mm,
            effective_depth,
            data.section.fc_mpa,
            data.section.fy_mpa,
        )
        if design.provided_area_mm2 + 1e-9 < max(required, minimum):
            continue
        unique.setdefault(_row_signature(design), design)
    return tuple(sorted(
        unique.values(),
        key=lambda design: (design.provided_area_mm2, _row_signature(design)),
    ))


def _independent_final_check(data, tops, bottom, hoops, engine, option):
    regions = []
    for demand, top, hoop in zip(data.regions, tops, hoops):
        minimum = engine.code.minimum_longitudinal_area(
            data.section.width_mm,
            min(top.effective_depth_mm, bottom.effective_depth_mm),
            data.section.fc_mpa,
            data.section.fy_mpa,
        )
        regions.append(RegionDetailing(
            name=demand.name,
            demand=demand,
            as_min_mm2=minimum,
            as_flex_top_mm2=demand.as_flex_top_mm2,
            as_flex_bottom_mm2=demand.as_flex_bottom_mm2,
            stirrup=hoop,
            top=top,
            bottom=bottom,
        ))

    required_legs = []
    for region in regions:
        depth = min(region.top.effective_depth_mm, region.bottom.effective_depth_mm)
        final_db = engine._final_main_db(region, data.section)
        legs = _required_hoop_legs(
            data.section,
            region.demand,
            engine,
            region.stirrup.diameter_mm,
            region.stirrup.spacing_mm,
            max_main=final_db,
            effective_depth=depth,
        )
        if legs is None:
            return None
        required_legs.append(legs)
    common_legs = max(required_legs, default=0)
    if common_legs < 2 or any(
        min(region.top.rows[0].count, region.bottom.rows[0].count) < common_legs
        for region in regions
    ):
        return None
    area = engine.project.catalog.area(regions[0].stirrup.diameter_mm)
    regions = tuple(replace(
        region,
        stirrup=replace(
            region.stirrup,
            legs=common_legs,
            provided_av_over_s_mm2_per_mm=(
                common_legs * area / region.stirrup.spacing_mm
            ),
        ),
    ) for region in regions)
    try:
        checked_regions = prepare_light_regions(data, regions, engine)
        provisional = BeamDetailingResult(
            beam_name=data.section.name,
            code_edition=engine.code.edition,
            code_policy_version=engine.code.version,
            project_policy_version=engine.project.version,
            detailing_profile=data.section.detailing_profile,
            section=data.section,
            mode=ReinforcementMode.NORMAL,
            regions=checked_regions,
            face_rebar=engine._construction_face_rebar(data.section),
            input_data=data,
            continuity_option=option,
            coordination_status="V3_RECOMMENDATION_SET_ONLY",
        )
        finalized = finalize_rows(provisional, engine)
        # This singleton has no inter-span interface or multi-section parent;
        # keep the real final-line checker in the independent acceptance path.
        from src.design.v3 import _validate_final_line

        _validate_final_line([finalized], [SimpleNamespace(index=0)], option, ((0,),))
        return finalized
    except (DetailingError, ValueError):
        return None


def _independent_rank(result, engine):
    used = {
        row.diameter_mm
        for region in result.regions
        for design in (region.top, region.bottom)
        for row in design.rows
        if row.role != "hanger"
    }
    tier = (
        int(25 in used),
        int(result.section.is_secondary and 20 in used),
        int(result.section.is_secondary and 12 in used),
        sum(region.stirrup.diameter_mm != 10 for region in result.regions),
    )
    from src.design.row_solver import _style_path_rank

    return tier, _style_path_rank((result,), engine)


def test_v3_matches_exhaustive_real_layout_cartesian_oracle(monkeypatch):
    frozen = read_frozen_line(("GF-FG", "SB", "GF-FG-SB1"))
    source = decode_frozen_line_inputs(frozen)[0]
    assert source.section.beam_role == "SB"
    assert source.section.detailing_profile.value == "NON_SWAY"

    engine = make_fresh_detailing_engine(DRAWING_V3_POLICY_ID, candidate_lookup="auto")
    engine.project = replace(
        engine.project,
        sb_main_diameters_mm=(16,),
        sb_preferred_main_diameters_mm=(16,),
        allowed_stirrup_diameters_mm=(10,),
        preferred_stirrup_diameters_mm=(10,),
        allowed_spacings_mm=(180,),
        smf_allowed_spacings_mm=(180,),
        max_main_bar_layers=1,
    )
    data = engine._etabs_only_input(source)
    hoops = tuple(
        _style_hoop(
            data.section,
            demand,
            {"diameter_mm": 10, "spacing_mm": 180, "legs": 2},
            engine,
        )
        for demand in data.regions
    )
    assert all(hoops)
    hoop_state = (hoops,)

    top_axes = tuple(
        _real_axis(
            data,
            engine,
            (hoop,),
            face="top",
            required=demand.as_top_total_mm2,
            preserve_support_alternatives=index in (0, 2),
        )[:2]
        for index, (demand, hoop) in enumerate(zip(data.regions, hoops))
    )
    bottom_required = max(demand.as_bottom_total_mm2 for demand in data.regions)
    bottom_axis = _real_axis(
        data,
        engine,
        hoops,
        face="bottom",
        required=bottom_required,
        preserve_support_alternatives=True,
    )[:1]
    assert all(len(axis) == 2 for axis in top_axes)
    assert len(bottom_axis) == 1
    assert len(top_axes[0]) * len(top_axes[1]) * len(top_axes[2]) * len(bottom_axis) <= 10

    option = ContinuityOption.OPTION_1_STRICT_EQUAL
    oracle = []
    for tops in product(*top_axes):
        for bottom in bottom_axis:
            finalized = _independent_final_check(data, tops, bottom, hoops, engine, option)
            if finalized is not None:
                oracle.append(finalized)
    oracle_by_signature = {_result_signature(result): result for result in oracle}
    assert 1 <= len(oracle_by_signature) <= 10

    top_position = 0

    def restricted_axis(_data, _hoops, _hoop, _required, _engine, *, face, **_kwargs):
        nonlocal top_position
        if face == "top":
            result = top_axes[top_position]
            top_position += 1
            return result
        return bottom_axis

    monkeypatch.setattr("src.design.v3._candidate_axis", restricted_axis)
    monkeypatch.setattr("src.design.v3._v3_hoop_states", lambda *_args, **_kwargs: hoop_state)

    fast = design_line_v3((data,), ((0,),), option, engine, k=10)
    fast_results = tuple(path[0] for path in fast.alternatives if path[0] is not None)
    fast_by_signature = {_result_signature(result): result for result in fast_results}

    assert top_position == 3
    assert len(fast_by_signature) == len(fast_results)
    assert set(fast_by_signature) == set(oracle_by_signature)
    assert fast.audit["complete_materializations"] == len(oracle_by_signature)
    assert fast.audit["complete_validation_calls"] == len(oracle_by_signature)
    assert fast.audit["complete_validation_passes"] == len(oracle_by_signature)
    assert not fast.audit["truncated"]
    expected_winner = min(oracle, key=lambda result: _independent_rank(result, engine))
    assert _result_signature(fast.selected[0]) == _result_signature(expected_winner)
