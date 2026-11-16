from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from src.core.beam_line_span_analyzer import BeamSpan
from src.core.geometry_extractor import BeamGeometry
from src.design.constructability import SeismicDetailingContext, SupportFaceContext
from src.design.detailing import (
    BeamDetailingInput, BeamDetailingProfile, BeamRegionDemand, BeamSection,
    DetailingError, LongitudinalDesign, PhysicalRow, RegionDetailing,
    ReinforcementMode, StirrupDesign, make_detailing_engine,
)
from src.design.offline_style import (
    DRAWING_POLICY_ID, DRAWING_POLICY_REVISION_R6_ID, DRAWING_POLICY_REVISION_R7_ID,
)
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation
from src.design.skin_rebar import SkinConstructionPolicy, select_skin_rebar
from src.excel_span_adapter import SpanScheduleMapping, SpanScheduleRecord
from src.rebar_workbook import (
    _coordination_pool_indices, _demand_view_status, _run_line_work_item,
    build_payload_from_schedule, redetail_payload,
)


def _engine(**kwargs):
    return make_detailing_engine(DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R7_ID, **kwargs)


def _input():
    section = BeamSection("R7", 550, 900, 40, 30, 420, 420, 25, 7000,
                          BeamDetailingProfile.SMF, beam_role="PB", actual_frame_type="Sway Special")
    regions = tuple(BeamRegionDemand(name, station, 1600, 1800, 0.8, 0.1, 100)
                    for name, station in zip(("left", "middle", "right"), (0, 3500, 7000)))
    return BeamDetailingInput(section, regions,
        left_support=SupportFaceContext("I", 100, source="test support"),
        right_support=SupportFaceContext("J", 6900, source="test support"),
        seismic_context=SeismicDetailingContext(is_smf=True, frame_type="Sway Special",
            capacity_shear_verified=False, left_mpr_kNm=10000, right_mpr_kNm=10000,
            gravity_shear_kN=1000, mpr_strength_factor=1.25))


def _manual_result(engine, data, count):
    layout = LongitudinalDesign(rows=(PhysicalRow(count, 25),), required_area_mm2=1800)
    regions = tuple(RegionDetailing(d.name, d, 1000, d.as_flex_top_mm2, d.as_flex_bottom_mm2,
                    engine._select_stirrup(data.section, d, 25), layout, layout) for d in data.regions)
    return engine._build_row_result(data, ReinforcementMode.NORMAL, regions)


def _payload(*, status=DesignResultStatus.AVAILABLE):
    beam = BeamGeometry("A", "A", "F1", "I", "J", (0, 0, 0), (7000, 0, 0), "B550X900", 900)
    span = BeamSpan("F1", "PB", 1, 1, ("A",), (beam.start, beam.end), False, False)
    stations = tuple(DesignStation(station, "TOP", 1500, "BOT", 1700, "SHEAR", .8,
                    "TORSION", 100, "TRANS", .1, top_total_area=1600, bottom_total_area=1800,
                    source_object="A", result_source="GetSummaryResultsBeam_2")
                    for station in (0, 3500, 7000))
    design = BeamDesignResult("F1-PB1-1", stations if status is not DesignResultStatus.NO_RESULTS else (),
                             status, "source diagnostic", ("A",), "N_mm_C", "GetSummaryResultsBeam_2")
    schedule = SpanScheduleMapping((SpanScheduleRecord(span, beam, design),), {}, {},
                                   detailing_policy_id=DRAWING_POLICY_ID)
    return build_payload_from_schedule(schedule, [beam],
        section_loader=lambda name: dict(width_mm=550, depth_mm=900, etabs_longitudinal_cover_mm=45,
            etabs_longitudinal_cover_source="ETABS source", fc_mpa=30, fy_mpa=420, fyt_mpa=420),
        frame_types={"A": "Sway Ordinary"}, metadata={"units": "N_mm_C"},
        clear_cover_to_stirrup_outer_mm=40, clear_cover_source="construction notes",
        max_aggregate_size_mm=25, max_aggregate_size_source="construction notes", engine=_engine(ranking_mode="legacy"))


def test_r7_keeps_approved_detailing_rules_and_rejects_capacity_calculation(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("R7 must not calculate or assess selected-rebar capacity shear")
    monkeypatch.setattr("src.design.detailing.calculate_smf_capacity_shear", forbidden)
    monkeypatch.setattr("src.design.detailing.evaluate_smf_capacity_shear", forbidden)
    monkeypatch.setattr("src.design.constructability.evaluate_smf_capacity_shear", forbidden)
    engine, data = _engine(), _input()
    assert engine.project.max_main_bar_layers == 3
    assert engine.project.smf_max_longitudinal_ratio == .02
    assert engine.avoid_two_row_4_3 and engine.uniform_stirrup_diameter_per_span
    small, large = (_manual_result(engine, data, count) for count in (4, 6))
    for result in (small, large, engine.refresh_capacity_shear_after_layout_change(large)):
        review = result.capacity_shear_review
        assert review.status == "ETABS_DESIGN_RESULTS_ONLY"
        assert review.actual_capacity_shear_kN is None
        assert review.required_av_over_s_mm2_per_mm is None
        assert review.left_mpr_kNm is None and review.vc_zero is None
        assert "CAPACITY_SHEAR_SOURCE_UNVERIFIED" not in result.warnings
        assert not any(check.rule_id.startswith("SMF-CAPACITY-SHEAR") for check in result.constructability.checks)
        assert any(check.rule_id == "SMF-TRANSVERSE-DEMAND-SOURCE" for check in result.constructability.checks)
        assert tuple(region.demand.av_over_s_mm2_per_mm for region in result.regions) == (.8,) * 3
        assert all(region.demand.capacity_shear_av_over_s_mm2_per_mm is None for region in result.regions)
    assert [r.stirrup.required_combo_over_s_mm2_per_mm for r in small.regions] == [
        r.stirrup.required_combo_over_s_mm2_per_mm for r in large.regions]


def test_r7_strips_stale_overrides_before_search_and_forwards_pool_choice(monkeypatch):
    data = _input()
    data = replace(data, regions=tuple(replace(d, capacity_shear_av_over_s_mm2_per_mm=999,
                    capacity_shear_demand_kN=999, capacity_shear_source="old Mpr") for d in data.regions))
    captured = {}
    def fake_span(actual, engine, **kwargs):
        captured.update(data=actual, options=kwargs)
        return "designed"
    monkeypatch.setattr("src.design.row_solver.design_span", fake_span)
    assert _engine().design(data, prepare_coordination_pool=False) == "designed"
    actual = captured["data"]
    assert actual.regions[0].av_over_s_mm2_per_mm == .8
    assert actual.regions[0].capacity_shear_av_over_s_mm2_per_mm is None
    assert actual.seismic_context.left_mpr_kNm is None
    assert actual.seismic_context.mpr_strength_factor is None
    assert captured["options"] == {"prepare_coordination_pool": False}
    assert data.regions[0].capacity_shear_av_over_s_mm2_per_mm == 999


def test_skin_is_section_only_and_absent_from_style_search():
    engine, data = _engine(), _input()
    expected = engine._construction_face_rebar(data.section)
    changed = tuple(replace(d, as_top_total_mm2=9000, as_bottom_total_mm2=12000,
                           av_over_s_mm2_per_mm=20, at_over_s_mm2_per_mm=8, al_torsion_mm2=20000) for d in data.regions)
    assert engine._select_face_rebar(data.section, changed, ReinforcementMode.FACE_TORSION) == expected
    assert expected.required_each_side_mm2 == 0
    assert expected.bars_each_side.diameter_mm == 10
    assert expected.bars_each_side.count == 3
    assert "face_rebar" not in engine.offline_style_library.applicable_fields(data, engine)
    assert select_skin_rebar(replace(data.section, depth_mm=799), SkinConstructionPolicy()).status == "NOT_APPLICABLE"
    assert select_skin_rebar(replace(data.section, depth_mm=800), SkinConstructionPolicy()).count_each_side == 2


@pytest.mark.parametrize("status, expected", [
    (DesignResultStatus.NO_RESULTS, "NO_RESULTS"),
    (DesignResultStatus.API_ERROR, "DESIGN_RESULT_API_ERROR"),
    (DesignResultStatus.PARTIAL, "DESIGN_RESULTS_PARTIAL"),
])
def test_r7_missing_and_error_source_results_stay_blocked(status, expected):
    payload = _payload(status=status)
    assert payload["raw_rows"][0]["engine_status"] == expected
    assert payload["raw_rows"][0]["design_status_message"] == "source diagnostic"
    assert payload["final_rows"][0]["engine_status"] == expected


def test_r7_missing_transverse_value_is_not_zero():
    controls = {name: dict(top_total_mm2=1600, bottom_total_mm2=1800, top_total_station_mm=3500,
                          av_over_s=None, at_over_s=.1, al_mm2=100)
                for name in ("left", "middle", "right")}
    assert _demand_view_status(controls, DRAWING_POLICY_ID, etabs_design_results_only=True) == "NO_RESULTS"


def test_r7_replay_cleans_capacity_evidence_and_preserves_etabs_values():
    payload = _payload()
    old = make_detailing_engine(DRAWING_POLICY_ID, detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID)
    payload["metadata"].update(detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
                               detailing_policy_parameters=old.detailing_policy_parameters)
    raw = payload["raw_rows"][0]
    raw.update(capacity_shear_status="PASS", capacity_shear_actual_kN=999,
               left_capacity_shear_av_over_s_mm2_per_mm=999)
    raw["detailing_context"]["seismic_context"] = dict(left_mpr_kNm=999, right_mpr_kNm=999,
            mpr_strength_factor=1.25, capacity_shear_final_kN=999)
    result = redetail_payload(payload, engine=_engine(ranking_mode="legacy"), allow_policy_revision_change=True)
    assert result["metadata"]["detailing_policy_revision_id"] == DRAWING_POLICY_REVISION_R7_ID
    assert result["metadata"]["transverse_reinforcement_source"] == "ETABS_DESIGN_RESULTS_ONLY"
    assert result["metadata"]["side_reinforcement_policy"] == "INDEPENDENT_CONSTRUCTION_ONLY"
    raw = result["raw_rows"][0]
    assert raw["engine_status"] == "OK"
    assert raw["left_av_over_s"] == .8
    assert raw["capacity_shear_status"] == "ETABS_DESIGN_RESULTS_ONLY"
    assert raw["capacity_shear_actual_kN"] is None
    assert "left_capacity_shear_av_over_s_mm2_per_mm" not in raw
    assert "left_mpr_kNm" not in raw["detailing_context"]["seismic_context"]
    assert json.loads(raw["capacity_shear_review_json"])["required_av_over_s_mm2_per_mm"] is None
    assert raw["side_reinforcement_strength_credit"] is False


@pytest.mark.parametrize("status, expected", [
    (DesignResultStatus.NO_RESULTS, "NO_RESULTS"),
    (DesignResultStatus.API_ERROR, "DESIGN_RESULT_API_ERROR"),
])
def test_r7_blocked_replay_discards_old_capacity_assessment_but_preserves_source_errors(status, expected):
    payload = _payload(status=status)
    raw = payload["raw_rows"][0]
    old_check = dict(rule_id="SMF-CAPACITY-SHEAR", status="PASS", actual=999, limit=1000)
    raw.update(capacity_shear_status="PASS", capacity_shear_actual_kN=999,
               constructability_status="OK", constructability_blocking_statuses="",
               constructability_checks_json=json.dumps([old_check]),
               constructability_zones_json=json.dumps([dict(capacity_shear=999)]),
               smf_check_statuses="SMF-CAPACITY-SHEAR=PASS",
               detailing_policy_audit_json=json.dumps([old_check]),
               candidate_search_audit_json=json.dumps([old_check]),
               engine_warnings="CAPACITY_SHEAR_SOURCE_UNVERIFIED; SOURCE_WARNING",
               left_design_errors="ETABS reinforcing design failed",
               left_design_warnings="ETABS source warning")
    result = redetail_payload(payload, engine=_engine(ranking_mode="legacy"))
    raw = result["raw_rows"][0]
    assert raw["engine_status"] == expected
    assert raw["design_status_message"] == "source diagnostic"
    assert raw["left_design_errors"] == "ETABS reinforcing design failed"
    assert raw["left_design_warnings"] == "ETABS source warning"
    assert raw["engine_warnings"] == "SOURCE_WARNING"
    assert not any(key.startswith(("capacity_shear_", "constructability_")) for key in raw)
    assert "smf_check_statuses" not in raw
    assert "detailing_policy_audit_json" not in raw
    assert "candidate_search_audit_json" not in raw
    for table in ("review_rows", "final_rows"):
        row = result[table][0]
        assert row["engine_status"] == expected
        assert row["capacity_shear_status"] == expected
        assert row["constructability_checks_json"] == "[]"


def test_only_contiguous_eligible_original_spans_prepare_coordination_pools():
    contexts = tuple((dict(span_no=span, object_part_index=part), None) for span, part in (
        (1, 0), (2, 0), (3, 1), (4, 0), (5, 0), (6, 0), (7, 0), (9, 0)))
    inputs = (object(), object(), object(), None, object(), object(), None, object())
    assert _coordination_pool_indices(inputs, contexts) == {0, 1, 4, 5}


def test_r7_dictionary_kernel_keeps_revision_keys_strict():
    from src.design.layout_dictionary import (
        LayoutDictionaryBank, LayoutDictionaryMiss, build_layout_dictionary, build_layout_dictionary_bank,
    )
    from src.design.row_candidates import candidates

    engine = _engine()
    section = replace(_input().section, width_mm=300, depth_mm=600,
                      detailing_profile=BeamDetailingProfile.NON_SWAY)
    hoop = StirrupDesign(10, 130, 2, 1, .5, 200, "test")
    data = build_layout_dictionary(section, (hoop,), engine, first_diameter=20,
                                   max_layers=1, build_timeout_seconds=5)
    bank = LayoutDictionaryBank(build_layout_dictionary_bank((data,)))
    expected = candidates(section, (hoop,), 900, engine, first_diameter=20, max_layers=1)
    actual = bank.query(900, section, (hoop,), engine, first_diameter=20, max_layers=1)
    assert actual == expected
    old = make_detailing_engine(DRAWING_POLICY_ID, detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID)
    with pytest.raises(LayoutDictionaryMiss, match="DICT_MISS"):
        bank.query(900, section, (hoop,), old, first_diameter=20, max_layers=1)
