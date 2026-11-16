from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.design.detailing import (
    BeamDetailingInput,
    BeamDetailingProfile,
    BeamRegionDemand,
    BeamSection,
    FaceRebarDesign,
    FaceRebarMode,
    LongitudinalDesign,
    PhysicalRow,
    StirrupDesign,
    make_detailing_engine,
)
from src.design.offline_style import (
    DRAWING_POLICY_ID,
    DRAWING_POLICY_REVISION_R5_ID,
    DRAWING_POLICY_REVISION_R6_ID,
    _demand_bucket,
    longitudinal_signature,
)


def _input(*, kind="PB", width=400, depth=700, frame="Program Determined", profile=None,
           top=1500.0, bottom=1500.0, av=0.2):
    if profile is None:
        profile = (
            BeamDetailingProfile.SMF
            if frame == "Program Determined"
            else BeamDetailingProfile.NON_SWAY
        )
    section = BeamSection(
        f"{kind}-{width}x{depth}", width, depth, 40, 25, 420, 420, 25, 6000,
        profile, beam_role=kind, actual_frame_type=frame,
    )
    demands = tuple(
        BeamRegionDemand(name, index * 2000, top, bottom, av, 0.0, 0.0)
        for index, name in enumerate(("left", "middle", "right"))
    )
    return BeamDetailingInput(section, demands)


def _result_with_rows(data, rows=(PhysicalRow(4, 20),), *, stirrups=None, face=None):
    design = LongitudinalDesign(rows=rows, required_area_mm2=1000.0)
    if stirrups is None:
        stirrups = tuple(
            StirrupDesign(10, 130, 4, 1.0, 0.5, 150.0, "test")
            for _ in range(3)
        )
    if face is None:
        face = FaceRebarDesign(FaceRebarMode.NOT_APPLICABLE, None, 0, 0, "test")
    regions = tuple(
        SimpleNamespace(top=design, bottom=design, stirrup=stirrup)
        for stirrup in stirrups
    )
    return SimpleNamespace(regions=regions, face_rebar=face)


def test_r6_applicability_cache_matches_authoritative_path_and_invalidates_context():
    data = _input()
    engine = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
    )
    library = engine.offline_style_library
    expected = library._applicable_fields_uncached(data, engine)

    returned = library.applicable_fields(data, engine)
    assert returned == expected
    returned.clear()
    assert library.applicable_fields(data, engine) == expected
    assert any(entry["input_data"] is data for entry in library._r6_applicability_cache.values())
    assert library.r6_lookup_counters_snapshot()["context_cache_hits"] >= 1

    before = library.r6_lookup_counters_snapshot()["context_compilations"]
    changed_input = replace(
        data,
        section=replace(data.section, actual_frame_type="Non Sway"),
    )
    assert library.applicable_fields(changed_input, engine) != expected
    assert library.r6_lookup_counters_snapshot()["context_compilations"] == before + 1

    before = library.r6_lookup_counters_snapshot()["context_compilations"]
    changed_demand = replace(
        data,
        regions=(
            data.regions[0],
            replace(data.regions[1], as_top_total_mm2=2500.0),
            data.regions[2],
        ),
    )
    library.applicable_fields(changed_demand, engine)
    assert library.r6_lookup_counters_snapshot()["context_compilations"] == before + 1

    before = library.r6_lookup_counters_snapshot()["context_compilations"]
    original_code = engine.code
    engine.code = type(original_code)(original_code.base_policy)
    library.applicable_fields(data, engine)
    assert library.r6_lookup_counters_snapshot()["context_compilations"] == before + 1
    engine.code = original_code

    before = library.r6_lookup_counters_snapshot()["context_compilations"]
    original_avoid_flag = engine.avoid_two_row_4_3
    engine.avoid_two_row_4_3 = False
    library.applicable_fields(data, engine)
    assert library.r6_lookup_counters_snapshot()["context_compilations"] == before + 1
    engine.avoid_two_row_4_3 = original_avoid_flag

    before = library.r6_lookup_counters_snapshot()["context_compilations"]
    original_project = engine.project
    engine.project = replace(original_project)
    library.applicable_fields(data, engine)
    assert library.r6_lookup_counters_snapshot()["context_compilations"] == before + 1
    engine.project = original_project

    before = library.r6_lookup_counters_snapshot()["context_compilations"]
    library.rules = tuple([*library.rules])
    library.applicable_fields(data, engine)
    assert library.r6_lookup_counters_snapshot()["context_compilations"] == before + 1

    empty = replace(data, section=replace(data.section, actual_frame_type=""))
    assert library.applicable_fields(empty, engine) == {}
    assert library.applicable_fields(empty, engine) == {}

    other_engine = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
    )
    assert other_engine.offline_style_library._r6_applicability_cache == {}


def test_r6_compiled_matching_equals_uncached_for_pb_sb_and_asymmetric_hoops():
    pb_data = _input()
    pb_engine = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
    )
    pb_library = pb_engine.offline_style_library
    pb_applicable = pb_library.applicable_fields(pb_data, pb_engine)
    assert {rule["mode_type"] for rule in pb_applicable["bottom_rebar"]} >= {
        "longitudinal_core",
        "longitudinal_rows_known",
    }
    pb_result = pb_engine.design(pb_data)
    assert pb_library.matched_fields(pb_data, pb_result, pb_engine) == (
        pb_library._matched_fields_uncached(pb_data, pb_result, pb_engine)
    )

    left, middle, right = (region.stirrup for region in pb_result.regions)
    changed_spacing = 110 if right.spacing_mm != 110 else 120
    asymmetric = replace(
        pb_result,
        regions=(
            pb_result.regions[0],
            pb_result.regions[1],
            replace(pb_result.regions[2], stirrup=replace(right, spacing_mm=changed_spacing)),
        ),
    )
    fast_asymmetric = pb_library.matched_fields(pb_data, asymmetric, pb_engine)
    slow_asymmetric = pb_library._matched_fields_uncached(pb_data, asymmetric, pb_engine)
    assert fast_asymmetric == slow_asymmetric
    assert not any(match.field == "stirrup_near_support" for match in fast_asymmetric)

    no_face = replace(
        pb_result,
        face_rebar=FaceRebarDesign(FaceRebarMode.NOT_APPLICABLE, None, 0, 0, "test"),
    )
    assert pb_library.matched_fields(pb_data, no_face, pb_engine) == (
        pb_library._matched_fields_uncached(pb_data, no_face, pb_engine)
    )

    sb_data = _input(
        kind="SB", width=300, depth=600, frame="Non Sway",
        profile=BeamDetailingProfile.NON_SWAY, top=900, bottom=900,
    )
    sb_engine = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
    )
    sb_library = sb_engine.offline_style_library
    sb_result = sb_engine.design(sb_data)
    assert sb_library.matched_fields(sb_data, sb_result, sb_engine) == (
        sb_library._matched_fields_uncached(sb_data, sb_result, sb_engine)
    )
    candidate_audit = next(
        item for item in sb_result.coordination_audit
        if item.get("audit_version") == "R6_CANDIDATE_SEARCH_AUDIT_V3"
    )
    assert candidate_audit["style_context_cache_hits"] > 0
    assert candidate_audit["style_compiled_pattern_entries"] > 0
    assert candidate_audit["style_compiled_pattern_lookups"] > 0


def test_r6_compiled_pattern_winner_keeps_existing_duplicate_and_tie_order():
    data = _input(top=300, bottom=300)
    engine = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
    )
    library = engine.offline_style_library
    design = LongitudinalDesign(rows=(PhysicalRow(4, 20),), required_area_mm2=300)
    core_signature = longitudinal_signature(design, include_rows=False)
    known_signature = longitudinal_signature(design, include_rows=True)
    bucket = _demand_bucket(library._longitudinal_demand(data, "top_rebar_middle"))

    def rule(rule_id, mode, signature, family_ids):
        return {
            "rule_id": rule_id,
            "kind": "PB",
            "field": "top_rebar_middle",
            "mode_type": mode,
            "section_width_mm": data.section.width_mm,
            "section_depth_mm": data.section.depth_mm,
            "actual_frame_type": data.section.actual_frame_type,
            "resolved_profile": data.section.detailing_profile.value,
            "required_demand_level": bucket,
            "pattern_signature": signature,
            "development_family_count": 6,
            "support_family_ids": family_ids,
        }

    first_row_winner = rule(
        "R-ROW-Z", "longitudinal_rows_known", known_signature,
        [f"row-first-{index}" for index in range(6)],
    )
    library.rules = (
        rule("R-CORE-Z", "longitudinal_core", core_signature,
             [f"core-{index}" for index in range(6)]),
        rule("R-ROW-A", "longitudinal_rows_known", known_signature,
             [f"row-loser-{index}" for index in range(6)]),
        first_row_winner,
    )
    result = _result_with_rows(data, rows=design.rows)

    fast = library.matched_fields(data, result, engine)
    slow = library._matched_fields_uncached(data, result, engine)
    assert fast == slow
    assert len(fast) == 1
    assert fast[0].rule_id == "R-ROW-Z"
    assert fast[0].mode_type == "longitudinal_rows_known"
    assert fast[0].family_ids == tuple(sorted(first_row_winner["support_family_ids"]))


def test_r6_context_cache_is_bounded_and_r5_uses_only_uncached_matchers(monkeypatch):
    r6 = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
    )
    library = r6.offline_style_library
    blank_frame = _input().section
    blank_frame = replace(blank_frame, actual_frame_type="")
    seed = BeamDetailingInput(blank_frame, _input().regions)
    library.applicable_fields(seed, r6)
    for index in range(library.R6_CONTEXT_CACHE_LIMIT + 1):
        section = replace(blank_frame, name=f"EMPTY-{index}")
        library.applicable_fields(replace(seed, section=section), r6)
    assert len(library._r6_applicability_cache) == library.R6_CONTEXT_CACHE_LIMIT
    before = library.r6_lookup_counters_snapshot()["context_compilations"]
    library.applicable_fields(seed, r6)
    assert library.r6_lookup_counters_snapshot()["context_compilations"] == before + 1

    r5 = make_detailing_engine(
        DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R5_ID,
    )
    r5_library = r5.offline_style_library
    data = _input()
    result = _result_with_rows(data)
    monkeypatch.setattr(
        r5_library,
        "_r6_applicability_context",
        lambda *_args: pytest.fail("R5 must bypass the R6 compiled lookup"),
    )
    assert r5_library.applicable_fields(data, r5) == r5_library._applicable_fields_uncached(data, r5)
    assert r5_library.matched_fields(data, result, r5) == r5_library._matched_fields_uncached(
        data, result, r5
    )
    assert not r5_library._r6_applicability_cache
