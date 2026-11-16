import re
from pathlib import Path
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.core.beam_line_span_analyzer import BeamLine, BeamLineSpanResult, BeamSpan
from src.core.geometry_extractor import BeamGeometry
from src.design.constructability import SupportFaceContext
from src.design.detailing import DetailingError, make_detailing_engine
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation
from src.design.region_results import DRAWING_DETAILING_POLICY_ID
from src.design.offline_style import DRAWING_V3_POLICY_ID
from src.design.etabs_result import CapacityShearPoint
from src.excel_span_adapter import (
    SpanScheduleMapping,
    SpanScheduleRecord,
    build_span_schedule,
)
from src.rebar_workbook import (
    PAYLOAD_SCHEMA_VERSION,
    _frame_types,
    _clear_v3_unselected_design_fields,
    _derive_support_face_contexts,
    _run_artifact_builder,
    _run_rebar_preview_builder,
    _serialize_v3_line_recommendation,
    build_payload_from_schedule,
    build_secondary_schedule_payload,
    _empty_review,
    _empty_final,
    project_result_table_payload,
    write_capacity_shear_audit,
    redetail_payload,
    write_rebar_payload,
)


def test_v3_no_design_rows_are_explicit_n_a_without_rebar_candidates():
    row = _clear_v3_unselected_design_fields({
        "engine_status": "EXCLUDED_NO_DESIGN",
        "et1": "candidate",
        "s2": "candidate",
    })

    assert row["mode"] == "EXCLUDED_NO_DESIGN"
    assert row["engine_status"] == "EXCLUDED_NO_DESIGN"
    assert row["et1"] == row["et1_text"] == "N.A."
    assert row["s2"] == row["s1"] == row["face"] == "N.A."
    assert row["review_note"] == (
        "Excluded from concrete design by ETABS; retained in source geometry and numbering only."
    )
    from src.spreadsheet.render_rebar_workbook_xlsx import _exception_reason

    assert _exception_reason(row) == ""


def test_v3_serialization_marks_no_design_parent_break_as_external_scope(monkeypatch):
    from src import rebar_workbook as workbook

    monkeypatch.setattr(workbook, "_row_layout_fields", lambda _result: {
        "longitudinal_rows": {}, "region_stirrups": {}, "row_coordination_audit": [],
    })
    monkeypatch.setattr(workbook, "_result_layout_signature", lambda _result: ())
    result = SimpleNamespace(
        face_rebar=SimpleNamespace(bars_each_side=None),
        coordination_status="V3_PARENT_COORDINATION_UNKNOWN_INPUT_BLOCKED",
    )
    recommendation = SimpleNamespace(
        audit={},
        errors=("PARENT_COORDINATION_INCOMPLETE_MISSING_SUBSPAN", "INPUT_MISSING_CONTINUITY_BREAK"),
        selected=(result, None),
        alternatives=((result, None),),
        status="CONTINUITY_UNKNOWN_PARTIAL_SEGMENTS",
    )
    contexts = (
        ({
            "beam_no": "P1-1", "parent_span_id": "P1", "object_part_index": 1,
            "parent_segment_count": 2, "beam_line": "L1", "span_no": 1,
        }, None),
        ({
            "beam_no": "P1-2", "parent_span_id": "P1", "object_part_index": 2,
            "parent_segment_count": 2, "beam_line": "L1", "span_no": 1,
            "design_status_message": "EXCLUDED_NO_DESIGN; GetDesignProcedure=7 No Design",
        }, None),
    )

    record, row_audits = _serialize_v3_line_recommendation(
        recommendation,
        ("L1", "PB", "Line-1"),
        contexts,
        (object(), None),
    )

    assert record["status"] == "PARTIAL_RECOMMENDATION_OUT_OF_DESIGN_SCOPE"
    assert record["line_recommendation_complete"] is False
    assert record["line_continuity_across_input_gap"] == "EXTERNAL_SCOPE"
    assert record["parent_spans_with_missing_sections"] == []
    assert record["parent_spans_with_no_design_members"] == ["P1"]
    assert record["alternatives"][0]["spans"][0]["coordination_status"] == (
        "V3_PARENT_COORDINATION_EXTERNAL_SCOPE_NO_DESIGN"
    )
    assert record["alternatives"][0]["spans"][1]["status"] == "EXCLUDED_NO_DESIGN"
    assert record["alternatives"][0]["spans"][1]["geometry_and_numbering_retained"] is True
    assert row_audits[0]["v3_parent_continuity_external_scope"] is True
    assert row_audits[0]["v3_parent_continuity_unknown"] is False


def test_v3_selected_local_layout_bypasses_review_only_for_single_no_design_sibling(monkeypatch):
    from src import rebar_workbook as workbook
    from src.design.detailing import make_fresh_detailing_engine

    valid = BeamGeometry(
        "VALID", "VALID", "Story1", "P1", "P2", (0.0, 0.0, 0.0),
        (3000.0, 0.0, 0.0), "SEC900", 800.0,
    )
    excluded = BeamGeometry(
        "EXCLUDED", "EXCLUDED", "Story1", "P2", "P3", (3000.0, 0.0, 0.0),
        (6000.0, 0.0, 0.0), "RIB150x370", 370.0,
    )
    names = (valid.name, excluded.name)
    path = (valid.start, valid.end, excluded.end)
    span = BeamSpan("Story1", "PB", 41, 2, names, path, False, False)
    line = BeamLine("Story1", "PB", 41, "VARIES", names, path, False)
    valid_design = BeamDesignResult(
        valid.name,
        tuple(replace(_station(location, 800.0, 800.0), source_object=valid.name)
              for location in (0.0, 1500.0, 3000.0)),
        source_objects=(valid.name,),
    )

    def make_payload(sibling_design):
        schedule = build_span_schedule(
            BeamLineSpanResult((line,), (span,), (), ()),
            [valid, excluded],
            {valid.name: valid_design, excluded.name: sibling_design},
            detailing_policy_id=DRAWING_V3_POLICY_ID,
        )

        def section_loader(name):
            if name == "RIB150x370":
                return {**_section(name), "width_mm": 150.0, "depth_mm": 370.0}
            return _section(name)

        return build_payload_from_schedule(
            schedule,
            [valid, excluded],
            section_loader=section_loader,
            frame_types={valid.name: "Non Sway", excluded.name: "Non Sway"},
            metadata={"model": "fixture", "units": "N_mm_C"},
            clear_cover_to_stirrup_outer_mm=40.0,
            clear_cover_source="fixture",
            max_aggregate_size_mm=20.0,
            max_aggregate_size_source="fixture",
            engine=make_fresh_detailing_engine(DRAWING_V3_POLICY_ID),
        )

    no_design = BeamDesignResult(
        excluded.name,
        status=DesignResultStatus.SKIPPED,
        status_message="EXCLUDED_NO_DESIGN; GetDesignProcedure=7 No Design",
        source_objects=(excluded.name,),
    )
    payload = make_payload(no_design)
    selected = payload["raw_rows"][0]
    excluded_row = payload["raw_rows"][1]

    assert selected["engine_status"] in {"OK", "FEASIBLE_OPTIMIZATION_INCOMPLETE"}
    assert selected["v3_recommendation_status"] == "PARTIAL_RECOMMENDATION_OUT_OF_DESIGN_SCOPE"
    assert selected["parent_continuity_status"] == "EXTERNAL_SCOPE_NO_DESIGN"
    assert selected["parent_continuity_review_required"] is False
    assert selected["valid_station_domain_status"] == "AVAILABLE"
    assert selected["longitudinal_rows"]["left"]["bottom"]
    assert selected["row_coordination_audit"][0]["row_x_mm"]
    assert "MIXED_SECTION_REVIEW_REQUIRED" not in selected.get("accepted_warning_codes", "")
    assert "MIXED_SECTION_PARENT" in selected["warnings"]
    assert "整跨通过" not in selected.get("review_note", "")
    assert payload["metadata"]["v3_recommendation_sets"][0]["line_recommendation_complete"] is False
    assert payload["metadata"]["v3_recommendation_sets"][0]["line_continuity_across_input_gap"] == "EXTERNAL_SCOPE"
    assert excluded_row["engine_status"] == "EXCLUDED_NO_DESIGN"
    assert excluded_row["source_objects"] == excluded.name
    assert payload["final_rows"][1]["et1"] == "N.A."

    unknown = BeamDesignResult(
        excluded.name,
        status=DesignResultStatus.PARTIAL,
        status_message="DESIGN_RESULTS_PARTIAL; input evidence unavailable",
        source_objects=(excluded.name,),
    )
    unknown_payload = make_payload(unknown)
    unknown_selected = unknown_payload["raw_rows"][0]

    assert unknown_selected["engine_status"] == "MULTI_SECTION_CONTINUITY_REVIEW_REQUIRED"
    assert unknown_selected["v3_parent_continuity_unknown"] is True
    assert unknown_selected["parent_continuity_review_required"] is True

    multi_engine = SimpleNamespace(
        detailing_policy_id=DRAWING_V3_POLICY_ID,
        _last_v3_recommendation_row_audits={
            0: {
                "v3_parent_continuity_external_scope": True,
                "v3_parent_continuity_unknown": False,
                "v3_recommendation_status": "PARTIAL_RECOMMENDATION_OUT_OF_DESIGN_SCOPE",
            },
        },
    )
    multi_contexts = [
        ({"parent_span_id": "P1", "object_part_index": 1}, None),
        ({"parent_span_id": "P1", "object_part_index": 2}, None),
        ({"parent_span_id": "P1", "object_part_index": 3}, None),
    ]
    assert not workbook._v3_selected_row_has_only_no_design_parent_sibling(
        0, [selected, dict(selected), dict(selected)], [object(), object(), None],
        multi_contexts, multi_engine,
    )


def test_windows_process_worker_cap_without_launching_processes(monkeypatch):
    from concurrent.futures import Future
    from src import rebar_workbook as workbook

    observed = {}

    class FakeExecutor:
        def __init__(self, max_workers, **kwargs):
            observed["max_workers"] = max_workers

        def submit(self, function, job):
            key, indices, inputs, contexts = job
            future = Future()
            future.set_result({"line_key": key, "indices": indices, "contexts": contexts,
                "initial_results": (None,), "results": (None,), "design_errors": {},
                "coordination_errors": {}, "worker_pid": 1234, "started_at_epoch": 0.0,
                "ended_at_epoch": 0.001, "elapsed_seconds": 0.001})
            return future

        def shutdown(self, **kwargs):
            pass

    monkeypatch.setattr(workbook, "ProcessPoolExecutor", FakeExecutor)
    monkeypatch.setattr(workbook, "_worker_engine_spec", lambda engine: {})
    monkeypatch.setattr(workbook.os, "cpu_count", lambda: 128)
    monkeypatch.setattr(workbook.sys, "platform", "win32")
    contexts = [({"story": "T1-L1", "beam_type": "PB", "beam_line": str(i)}, None) for i in range(70)]
    outcome = workbook._dispatch_beam_line_work([object() for _ in contexts], contexts,
        SimpleNamespace(layout_dictionary_bank=None), workers=127)
    assert observed["max_workers"] == 61
    assert outcome[-1] == 61
    assert len(outcome[0]) == 70


def test_direct_rebar_api_defaults_remain_serial():
    from inspect import signature
    assert signature(build_payload_from_schedule).parameters["rebar_workers"].default == 1
    assert signature(redetail_payload).parameters["rebar_workers"].default == 1


def test_artifact_builder_removes_inspect_spill_after_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    builder = tmp_path / "builder.mjs"
    builder.write_text("// fixture", encoding="utf-8")
    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")
    output = tmp_path / "review.xlsx"
    spill = Path(f"{output}.inspect.ndjson")

    def fail_builder(*args, **kwargs):
        spill.write_bytes(b"scratch")
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(subprocess, "run", fail_builder)

    with pytest.raises(subprocess.CalledProcessError):
        _run_artifact_builder(
            builder,
            payload,
            output,
            preview_dir=tmp_path / "previews",
            node_executable="node",
            node_modules=None,
            node_max_old_space_mb=1024,
        )

    assert not spill.exists()


def test_large_workbook_previews_run_in_separate_processes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workbook = tmp_path / "review.xlsx"
    workbook.write_bytes(b"xlsx")
    builder = tmp_path / "render_preview.mjs"
    builder.write_text("// fixture", encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr("src.rebar_workbook.PREVIEW_BUILDER_PATH", builder)
    monkeypatch.setattr("src.rebar_workbook.subprocess.run", fake_run)

    _run_rebar_preview_builder(
        workbook,
        tmp_path / "previews",
        node_executable="node",
        node_modules=None,
        node_max_old_space_mb=8192,
    )

    assert len(calls) == 1
    assert [Path(call[0][-1]).stem for call in calls] == ["schedule"]
    assert all(call[1]["check"] is True for call in calls)


def test_large_workbook_preview_retries_transient_native_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workbook = tmp_path / "review.xlsx"
    workbook.write_bytes(b"xlsx")
    builder = tmp_path / "render_preview.mjs"
    builder.write_text("// fixture", encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if len(calls) == 1:
            raise subprocess.CalledProcessError(3221226505, command)

    monkeypatch.setattr("src.rebar_workbook.PREVIEW_BUILDER_PATH", builder)
    monkeypatch.setattr("src.rebar_workbook.subprocess.run", fake_run)
    monkeypatch.setattr("src.rebar_workbook.time.sleep", lambda _seconds: None)

    _run_rebar_preview_builder(
        workbook,
        tmp_path / "previews",
        node_executable="node",
        node_modules=None,
        node_max_old_space_mb=8192,
    )

    assert len(calls) == 2
    assert calls[0][0][-2] == "schedule"
    assert calls[1][0][-2] == "schedule"


def test_frame_type_table_is_strictly_parsed() -> None:
    database = SimpleNamespace(
        GetTableForDisplayArray=lambda *_args: (
            0,
            None,
            1,
            ("UniqueName", "FrameType"),
            2,
            ("B1", "Sway Ordinary", "B2", "Sway Special"),
        )
    )

    assert _frame_types(SimpleNamespace(DatabaseTables=database)) == {
        "B1": "Sway Ordinary",
        "B2": "Sway Special",
    }


def test_frame_type_table_rejects_truncated_rows() -> None:
    database = SimpleNamespace(
        GetTableForDisplayArray=lambda *_args: (
            0,
            None,
            1,
            ("UniqueName", "FrameType"),
            2,
            ("B1", "Sway Ordinary", "B2"),
        )
    )

    with pytest.raises(Exception, match="记录数或数据宽度不一致"):
        _frame_types(SimpleNamespace(DatabaseTables=database))


def _station(location: float, top: float, bottom: float) -> DesignStation:
    return DesignStation(
        location,
        "TOP-C",
        top,
        "BOT-C",
        bottom,
        "V-C",
        0.35,
        "T-C",
        400.0,
        "T-C",
        0.08,
        top_total_area=top,
        bottom_total_area=bottom,
        top_min_area=700.0,
        bottom_min_area=700.0,
        top_provided_area=top + 100.0,
        bottom_provided_area=bottom + 100.0,
    )


def _beam(
    name: str,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    section: str = "B500X800",
) -> BeamGeometry:
    return BeamGeometry(
        name,
        name,
        "F1",
        f"{name}-I",
        f"{name}-J",
        start,
        end,
        section,
        800.0,
    )


def _section(name: str) -> dict[str, float | str]:
    return {
        "section": name,
        "width_mm": 500.0,
        "depth_mm": 800.0,
        "etabs_longitudinal_cover_mm": 45.0,
        "etabs_longitudinal_cover_source": "ETABS GetRebarBeam test fixture",
        "fc_mpa": 30.0,
        "fy_mpa": 420.0,
        "fyt_mpa": 420.0,
    }


def _build_payload(
    schedule,
    beams,
    *,
    frame_types,
    resolved_frame_types=None,
    engine=None,
    sb_frame_type_override=None,
    rebar_workers=1,
    line_result_callback=None,
):
    return build_payload_from_schedule(
        schedule,
        beams,
        section_loader=_section,
        frame_types=frame_types,
        resolved_frame_types=resolved_frame_types,
        metadata={"model": "sample.EDB", "units": "N_mm_C"},
        clear_cover_to_stirrup_outer_mm=40.0,
        clear_cover_source="structural-general-notes",
        max_aggregate_size_mm=20.0,
        max_aggregate_size_source="concrete-mix-submittal",
        engine=engine,
        sb_frame_type_override=sb_frame_type_override,
        rebar_workers=rebar_workers,
        line_result_callback=line_result_callback,
    )


@pytest.mark.parametrize("policy_id", ["legacy", DRAWING_DETAILING_POLICY_ID])
def test_beam_line_process_dispatch_matches_serial_for_pb_and_sb_lines(policy_id) -> None:
    import os
    from src.design.detailing import make_fresh_detailing_engine

    beams = []
    lines = []
    spans = []
    records = []
    frame_types = {}
    for beam_type, line_number, y in (("PB", 1, 0.0), ("SB", 2, 5000.0)):
        names = (f"{beam_type}-A", f"{beam_type}-B")
        points = ((0.0, y, 0.0), (6000.0, y, 0.0), (12000.0, y, 0.0))
        lines.append(BeamLine("F1", beam_type, line_number, "B500X800", names, points, False))
        for index, name in enumerate(names, start=1):
            start, end = points[index - 1], points[index]
            beam = _beam(name, start, end)
            span = BeamSpan("F1", beam_type, line_number, index, (name,), (start, end), False, False)
            design = BeamDesignResult(name, (
                _station(0.0, 1800.0, 1400.0),
                _station(3000.0, 1200.0, 1600.0),
                _station(6000.0, 1800.0, 1400.0),
            ))
            beams.append(beam)
            spans.append(span)
            records.append(SpanScheduleRecord(span, beam, design))
            frame_types[name] = "Non Sway"

    schedule = SpanScheduleMapping(tuple(records), {}, {}, detailing_policy_id=policy_id)
    common_args = dict(
        frame_types=frame_types,
        engine=make_fresh_detailing_engine(policy_id),
    )
    serial = _build_payload(schedule, beams, **common_args, rebar_workers=1)
    events = []
    parallel = _build_payload(
        schedule,
        beams,
        **common_args,
        rebar_workers=2,
        line_result_callback=events.append,
    )

    assert parallel["raw_rows"] == serial["raw_rows"]
    assert parallel["review_rows"] == serial["review_rows"]
    assert parallel["final_rows"] == serial["final_rows"]
    assert parallel["metadata"]["rebar_parallelism"]["workers_effective"] == 2
    assert len(events) == 2
    assert {event["line_key"] for event in events} == {
        ("F1", "PB", "F1-PB1"),
        ("F1", "SB", "F1-SB2"),
    }
    assert all(len(event["indices"]) == 2 for event in events)
    assert all(event["worker_pid"] != os.getpid() for event in events)
    assert all(event["elapsed_seconds"] > 0 for event in events)
    assert parallel["metadata"]["detailing_policy_revision_id"] == serial["metadata"]["detailing_policy_revision_id"]
    if policy_id == DRAWING_DETAILING_POLICY_ID:
        assert parallel["metadata"]["detailing_policy_revision_id"] == "drawing_20261025_etabs_only_independent_skin_v1"
        assert all(row["capacity_shear_status"] == "ETABS_DESIGN_RESULTS_ONLY" for row in parallel["raw_rows"])

    serial_redetail = redetail_payload(serial, engine=make_fresh_detailing_engine(policy_id), rebar_workers=1)
    replay_events = []
    parallel_redetail = redetail_payload(
        serial,
        engine=make_fresh_detailing_engine(policy_id),
        rebar_workers=2,
        line_result_callback=replay_events.append,
    )
    assert parallel_redetail["raw_rows"] == serial_redetail["raw_rows"]
    assert parallel_redetail["review_rows"] == serial_redetail["review_rows"]
    assert parallel_redetail["final_rows"] == serial_redetail["final_rows"]
    assert len(replay_events) == 2


def test_v3_fresh_payload_uses_partial_region_evidence_without_nearest_backfill(monkeypatch) -> None:
    from src import rebar_workbook as workbook
    from src.design.detailing import make_fresh_detailing_engine
    from src.design.region_results import DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE

    beam = _beam("V3-PARTIAL", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan("F1", "PB", 1, 1, (beam.name,), (beam.start, beam.end), False, False)
    design = BeamDesignResult(beam.name, (_station(0.0, 1000.0, 800.0), _station(6000.0, 1000.0, 800.0)))
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beam, design),),
        {beam.name: (span.span_id,)},
        {beam.name: (span.beam_line_id,)},
        detailing_policy_id=DRAWING_V3_POLICY_ID,
    )
    original_region_controls = workbook._region_controls
    observed = []

    def capture_region_controls(*args, **kwargs):
        result = original_region_controls(*args, **kwargs)
        observed.append((kwargs.get("projection_mode"), result))
        return result

    monkeypatch.setattr(workbook, "_region_controls", capture_region_controls)

    def stop_before_detailing(*_args):
        raise DetailingError("REGION_PROJECTION_CAPTURE_ONLY")

    monkeypatch.setattr(workbook, "_engine_demand", stop_before_detailing)
    payload = _build_payload(
        schedule,
        [beam],
        frame_types={beam.name: "Non Sway"},
        engine=make_fresh_detailing_engine(DRAWING_V3_POLICY_ID),
    )

    assert len(observed) == 1
    projection_mode, controls = observed[0]
    assert projection_mode == DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE
    assert payload["metadata"]["region_control_projection_mode"] == projection_mode
    middle = controls["middle"]
    assert middle["region_evidence"]["status"] == "UNKNOWN"
    assert middle["region_evidence"]["station_count"] == 0
    assert middle["middle_top_window_station_count"] == 0
    assert payload["raw_rows"][0]["middle_projection_mode"] == projection_mode


def test_v3_pure_no_design_rows_are_n_a_before_frame_type_blockers() -> None:
    from src.design.detailing import make_fresh_detailing_engine

    beam = _beam("NO-DESIGN", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan("F1", "PB", 1, 1, (beam.name,), (beam.start, beam.end), False, False)
    design = BeamDesignResult(
        beam.name,
        status=DesignResultStatus.SKIPPED,
        status_message="EXCLUDED_NO_DESIGN; GetDesignProcedure=7 No Design",
        source_objects=(beam.name,),
    )
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beam, design),),
        {beam.name: (span.span_id,)},
        {beam.name: (span.beam_line_id,)},
        detailing_policy_id=DRAWING_V3_POLICY_ID,
    )
    payload = _build_payload(
        schedule,
        [beam],
        frame_types={},
        engine=make_fresh_detailing_engine(DRAWING_V3_POLICY_ID),
    )

    raw = payload["raw_rows"][0]
    assert raw["engine_status"] == "EXCLUDED_NO_DESIGN"
    assert raw["excluded_no_design_diagnostics"] == "FRAME_TYPE_UNRESOLVED"
    assert payload["final_rows"][0]["et1"] == "N.A."

    mixed_design = BeamDesignResult(
        beam.name,
        (_station(0.0, 1000.0, 800.0), _station(6000.0, 1000.0, 800.0)),
        status=DesignResultStatus.PARTIAL,
        status_message="One source has No Design; eligible source stations are retained.",
        source_objects=(beam.name,),
    )
    mixed_schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beam, mixed_design),),
        {beam.name: (span.span_id,)},
        {beam.name: (span.beam_line_id,)},
        detailing_policy_id=DRAWING_V3_POLICY_ID,
    )
    mixed_payload = _build_payload(
        mixed_schedule,
        [beam],
        frame_types={beam.name: "Non Sway"},
        engine=make_fresh_detailing_engine(DRAWING_V3_POLICY_ID),
    )
    assert mixed_payload["raw_rows"][0]["engine_status"] == "DESIGN_RESULTS_PARTIAL"


def test_user_confirmed_sb_frame_type_is_span_scoped_and_survives_redetail(monkeypatch) -> None:
    from tools.drawing_dataset_inputs import (
        apply_frame_type_override_plan,
        build_user_frame_type_override_plan,
    )

    beam = _beam("SHARED", (0, 0, 0), (6000, 0, 0))
    pb = BeamSpan("F1", "PB", 1, 1, ("SHARED",), (beam.start, beam.end), False, False)
    sb = BeamSpan("F1", "SB", 2, 1, ("SHARED",), (beam.start, beam.end), False, False)
    pb_line = BeamLine("F1", "PB", 1, beam.section, ("SHARED",), pb.path_points, False)
    sb_line = BeamLine("F1", "SB", 2, beam.section, ("SHARED",), sb.path_points, False)
    structural = BeamLineSpanResult((pb_line, sb_line), (pb, sb), ())
    design_pb = BeamDesignResult("F1-PB1-1", (_station(0.0, 1800.0, 1200.0), _station(3000.0, 1000.0, 1600.0), _station(6000.0, 1900.0, 1300.0)))
    design_sb = BeamDesignResult("F1-SB2-1", (_station(0.0, 1800.0, 1200.0), _station(3000.0, 1000.0, 1600.0), _station(6000.0, 1900.0, 1300.0)))
    schedule = SpanScheduleMapping(
        (
            SpanScheduleRecord(pb, beam, design_pb),
            SpanScheduleRecord(sb, beam, design_sb),
        ),
        {"SHARED": (pb.span_id, sb.span_id)},
        {"SHARED": (pb.beam_line_id, sb.beam_line_id)},
    )
    plan = build_user_frame_type_override_plan(
        structural,
        {"SHARED": {"value": "Sway Special", "provenance": "frozen raw row"}},
        all_sb_nonsway=True,
    )
    payload = _build_payload(
        schedule,
        [beam],
        frame_types=plan.source_frame_types,
        sb_frame_type_override="Non Sway",
    )
    apply_frame_type_override_plan(payload, plan)

    rows = {row["beam_type"]: row for row in payload["raw_rows"]}
    assert rows["PB"]["frame_type"] == "Sway Special"
    assert rows["PB"]["actual_frame_type"] == "Sway Special"
    assert rows["SB"]["frame_type"] == "Sway Special"
    assert rows["SB"]["actual_frame_type"] == "Non Sway"

    engine = make_detailing_engine()
    seen_frame_types = []

    def capture_input(detailing_input):
        seen_frame_types.append(detailing_input.section.actual_frame_type)
        raise DetailingError("capture only")

    monkeypatch.setattr(engine, "design", capture_input)
    for row in payload["raw_rows"]:
        row["engine_status"] = "OK"
    redetail_payload(payload, engine=engine)

    assert seen_frame_types == ["Sway Special", "Non Sway"]


@pytest.mark.parametrize("policy_id", [DRAWING_DETAILING_POLICY_ID, DRAWING_V3_POLICY_ID])
def test_fresh_drawing_policy_sb_override_retains_original_frame_type(policy_id: str) -> None:
    from src.design.detailing import make_fresh_detailing_engine

    beam = _beam("SB-A", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan("F1", "SB", 2, 1, ("SB-A",), (beam.start, beam.end), False, False)
    line = BeamLine("F1", "SB", 2, beam.section, ("SB-A",), span.path_points, False)
    design = BeamDesignResult(
        "F1-SB2-1",
        (
            _station(0.0, 1800.0, 1200.0),
            _station(3000.0, 1000.0, 1600.0),
            _station(6000.0, 1900.0, 1300.0),
        ),
    )
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beam, design),),
        {"SB-A": (span.span_id,)},
        {"SB-A": (line.beam_line_id,)},
        detailing_policy_id=policy_id,
    )

    payload = _build_payload(
        schedule,
        [beam],
        frame_types={"SB-A": "Sway Special"},
        engine=make_fresh_detailing_engine(policy_id),
        sb_frame_type_override="Non Sway",
    )
    row = payload["raw_rows"][0]
    manifest = payload["metadata"]["frame_type_override_manifest"]

    assert row["frame_type"] == "Sway Special"
    assert row["originalFrameType"] == "Sway Special"
    assert row["actual_frame_type"] == "Non Sway"
    assert row["frame_type_override_applied"] is True
    assert manifest["user_confirmed"] is True
    assert manifest["original_frame_types"] == {"SB-A": "Sway Special"}


def test_v3_sb_nonsway_override_resolves_mixed_frame_type_parent() -> None:
    from src.design.detailing import make_fresh_detailing_engine

    first = _beam("SB-A", (0, 0, 0), (6000, 0, 0))
    second = _beam("SB-B", (6000, 0, 0), (12000, 0, 0))
    path = (first.start, first.end, second.end)
    parent = BeamGeometry(
        "SB-PARENT", "SB-PARENT", "F1", "P1", "P3", first.start, second.end,
        first.section, first.depth, source_names=(first.name, second.name), path_points=path,
    )
    span = BeamSpan("F1", "SB", 9, 1, (first.name, second.name), path, False, False)
    design = BeamDesignResult(
        span.span_id,
        tuple(_station(location, 1800.0, 1400.0) for location in (0.0, 3000.0, 6000.0, 9000.0, 12000.0)),
        source_objects=(first.name, second.name),
    )
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, parent, design),),
        {first.name: (span.span_id,), second.name: (span.span_id,)},
        {first.name: (span.beam_line_id,), second.name: (span.beam_line_id,)},
        detailing_policy_id=DRAWING_V3_POLICY_ID,
    )
    payload = _build_payload(
        schedule,
        [first, second],
        frame_types={first.name: "Sway Special", second.name: "Program Determined"},
        engine=make_fresh_detailing_engine(DRAWING_V3_POLICY_ID),
        sb_frame_type_override="Non Sway",
    )

    row = payload["raw_rows"][0]
    assert row["frame_type"] == "Sway Special, Program Determined"
    assert row["actual_frame_type"] == "Non Sway"
    assert "MIXED_FRAME_TYPE" in row["warnings"]
    assert row["engine_status"] != "MIXED_FRAME_TYPE_REVIEW_REQUIRED"
    assert payload["metadata"]["frame_type_override_manifest"]["original_frame_types"] == {
        "SB-A": "Sway Special",
        "SB-B": "Program Determined",
    }


def test_formal_payload_contains_audit_rows_and_can_redetail(tmp_path: Path) -> None:
    beam = _beam("A", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan(
        "F1",
        "PB",
        1,
        1,
        ("A",),
        (beam.start, beam.end),
        False,
        False,
    )
    design = BeamDesignResult(
        "F1-PB1-1",
        (
            _station(0.0, 1800.0, 1200.0),
            _station(3000.0, 1000.0, 1600.0),
            _station(6000.0, 1900.0, 1300.0),
        ),
    )
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beam, design),),
        {"A": ("F1-PB1-S1",)},
        {"A": ("F1-PB1",)},
    )

    payload = _build_payload(
        schedule,
        [beam],
        frame_types={"A": "Sway Ordinary"},
    )

    assert len(payload["raw_rows"]) == 1
    assert payload["review_rows"][0]["engine_status"] == "OK"
    assert payload["review_rows"][0]["et1_text"]
    assert payload["final_rows"][0]["beam_no"] == "F1-PB1-1"
    assert payload["raw_rows"][0]["detailing_profile"] == "OMF"
    assert payload["raw_rows"][0]["effective_frame_type"] == "OMF"
    assert payload["raw_rows"][0]["left_top_total_combo"] == "TOP-C"
    assert payload["raw_rows"][0]["right_top_total_station_mm"] == 6000.0
    assert payload["raw_rows"][0]["etabs_longitudinal_cover_mm"] == 45.0
    assert payload["raw_rows"][0]["clear_cover_to_stirrup_outer_mm"] == 40.0
    assert payload["metadata"]["smf_end_max_spacing_mm"] == pytest.approx(100.0)
    assert payload["metadata"]["smf_max_longitudinal_ratio"] == pytest.approx(0.01613)
    assert payload["metadata"]["rebar_workbook_layout_version"] == "single-sheet-static-v1"
    assert payload["metadata"]["bottom_reinforcement_strategy"] == "FULL_SPAN_UNIFORM_PER_SPAN"
    assert payload["metadata"]["adjacent_span_bottom_strategy"] == "INDEPENDENT_PER_SPAN"
    assert "SPACING_THEN_DIAMETER_THEN_LEGS" in payload["metadata"]["project_policy_version"]
    assert payload["metadata"]["pb_preferred_max_stirrup_legs"] == 4
    assert payload["metadata"]["pb_stirrup_selection_strategy"] == "SPACING_THEN_DIAMETER_THEN_LEGS"
    assert payload["metadata"]["enabled_diameters_mm"] == [10, 12, 16, 20, 25]
    assert payload["metadata"]["sb_top_preferred_max_diameter_mm"] == 20
    assert payload["metadata"]["sb_midspan_top_pair_strategy"] == "MATCHING_DIAMETER_PAIR"
    final = payload["final_rows"][0]
    assert final["eb1"] == final["b1"] == final["eb2"]
    assert final["bottom_uniform_regions_match"] is True
    assert payload["review_rows"][0]["s2_left_av_required"] == 0.35
    assert payload["review_rows"][0]["s2_left_shear_source"] == (
        "GetSummaryResultsBeam_2.VMajorArea (Av/s)"
    )

    destination = write_rebar_payload(payload, tmp_path / "payload.json")
    assert destination.is_file()
    payload["metadata"].update(
        {
            "project_policy_version": "previous-policy",
            "pb_preferred_max_stirrup_legs": 6,
            "pb_stirrup_selection_strategy": "DIAMETER_FIRST",
        }
    )
    redetailed = redetail_payload(payload)
    assert redetailed["review_rows"][0]["engine_status"] == "OK"
    assert redetailed["metadata"]["sb_top_preferred_max_diameter_mm"] == 20
    assert "SPACING_THEN_DIAMETER_THEN_LEGS" in redetailed["metadata"]["project_policy_version"]
    assert redetailed["metadata"]["pb_preferred_max_stirrup_legs"] == 4
    assert redetailed["metadata"]["pb_stirrup_selection_strategy"] == "SPACING_THEN_DIAMETER_THEN_LEGS"
    assert payload["metadata"]["project_policy_version"] == "previous-policy"


def test_mixed_section_child_payload_rebases_only_true_end_supports_and_requires_parent_review() -> None:
    xs = (0.0, 2852.890625, 5700.0, 7787.0, 9600.0)
    sections = ("SEC900", "SEC950", "SEC900", "SEC950")
    names = ("645", "646", "647", "648")
    beams = []
    designs = {}
    for index, (name, left, right, section) in enumerate(zip(names, xs, xs[1:], sections)):
        beam = BeamGeometry(
            name,
            name,
            "Story1",
            f"P{index + 1}",
            f"P{index + 2}",
            (left, 0.0, -50.0),
            (right, 0.0, -50.0),
            section,
            900.0 if section == "SEC900" else 950.0,
        )
        beams.append(beam)
        designs[name] = BeamDesignResult(
            name,
            tuple(
                replace(
                    _station(location, 1000.0 + index * 100.0, 1200.0 + index * 100.0),
                    source_object=name,
                )
                for location in (0.0, beam.length / 2.0, beam.length)
            ),
            source_objects=(name,),
        )
    path = tuple((x, 0.0, -50.0) for x in xs)
    span = BeamSpan("Story1", "SB", 41, 2, names, path, False, False)
    line = BeamLine("Story1", "SB", 41, "VARIES", names, path, False)
    schedule = build_span_schedule(
        BeamLineSpanResult((line,), (span,), (), ()),
        beams,
        designs,
    )
    parent_id = "Story1-SB41-2"
    support_pair = (
        SupportFaceContext("LEFT", 300.0, 0.0, 600.0, "fixture"),
        SupportFaceContext("RIGHT", 9000.0, 9600.0, 1200.0, "fixture"),
    )

    def section_loader(name: str):
        return {**_section(name), "depth_mm": 900.0 if name == "SEC900" else 950.0}

    payload = build_payload_from_schedule(
        schedule,
        beams,
        section_loader=section_loader,
        frame_types={name: "Sway Ordinary" for name in names},
        metadata={"model": "offline fixture", "units": "N_mm_C"},
        clear_cover_to_stirrup_outer_mm=40.0,
        clear_cover_source="fixture",
        max_aggregate_size_mm=20.0,
        max_aggregate_size_source="fixture",
        support_face_contexts={span.span_id: support_pair},
    )

    assert [row["beam_no"] for row in payload["raw_rows"]] == [
        f"{parent_id}.1",
        f"{parent_id}.2",
        f"{parent_id}.3",
        f"{parent_id}.4",
    ]
    assert len({row["span_no"] for row in payload["raw_rows"]}) == 1
    assert [row["section"] for row in payload["raw_rows"]] == list(sections)
    assert [row["source_objects"] for row in payload["raw_rows"]] == list(names)
    assert [row["engine_status"] for row in payload["raw_rows"]] == [
        "MULTI_SECTION_CONTINUITY_REVIEW_REQUIRED"
    ] * 4
    for raw, review, final in zip(payload["raw_rows"], payload["review_rows"], payload["final_rows"]):
        assert raw["bottom_reinforcement_strategy"] == "MULTI_SECTION_SEGMENT_CANDIDATE_REQUIRES_PARENT_COORDINATION"
        assert raw["bottom_uniform_regions_match"] is None
        assert review["engine_status"] == final["engine_status"] == raw["engine_status"]
        assert "上部第一排直径协调尚未完成" in review["review_note"]

    contexts = [row["detailing_context"] for row in payload["raw_rows"]]
    assert contexts[0]["left_support"]["face_location_mm"] == 300.0
    assert contexts[0]["right_support"] is None
    assert all(context["left_support"] is None and context["right_support"] is None for context in contexts[1:3])
    assert contexts[-1]["left_support"] is None
    assert contexts[-1]["right_support"]["face_location_mm"] == 1213.0
    assert contexts[-1]["right_support"]["centerline_location_mm"] == 1813.0
    for context, beam in zip((contexts[0]["left_support"], contexts[-1]["right_support"]), (beams[0], beams[-1])):
        assert 0.0 <= context["face_location_mm"] <= beam.length


def test_payload_preserves_cantilever_flag_and_canonical_beam_number() -> None:
    beam = _beam("OBJ1", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan(
        "T1",
        "PB",
        23,
        1,
        ("OBJ1",),
        (beam.start, beam.end),
        False,
        True,
    )
    design = BeamDesignResult(
        "T1-PB23-1",
        (_station(0.0, 1000.0, 1000.0), _station(6000.0, 1000.0, 1000.0)),
    )
    payload = _build_payload(
        SpanScheduleMapping((SpanScheduleRecord(span, beam, design),), {}, {}),
        [beam],
        frame_types={"OBJ1": "Sway Ordinary"},
    )

    assert [row["beam_no"] for row in payload["raw_rows"]] == ["T1-PB23-1"]
    assert [row["beam_no"] for row in payload["review_rows"]] == ["T1-PB23-1"]
    assert [row["beam_no"] for row in payload["final_rows"]] == ["T1-PB23-1"]
    assert all(
        row["is_cantilever"] is True
        for group in ("raw_rows", "review_rows", "final_rows")
        for row in payload[group]
    )
    projected = project_result_table_payload(payload, "PB")
    assert projected["final_rows"][0]["beam_no"] == "T1-PB23-1"
    assert projected["final_rows"][0]["is_cantilever"] is True


@pytest.mark.parametrize("prior_status, expected", [
    ("FEASIBLE_OPTIMIZATION_INCOMPLETE", "OK"),
    ("F1-PB1-1/left: NO_ALLOWED_SPACING", "OK"),
    ("F1-PB1-1/left: NO_ALLOWED_SPACING; maximum_spacing_mm=75", "OK"),
    ("F1-PB1-1: ROW_CATALOG_NO_FEASIBLE_LAYOUT", "OK"),
    ("MIXED_SECTION_REVIEW_REQUIRED", "OK"),
    ("MIXED_FRAME_TYPE_REVIEW_REQUIRED", "MIXED_FRAME_TYPE_REVIEW_REQUIRED"),
    ("DESIGN_RESULTS_PARTIAL", "DESIGN_RESULTS_PARTIAL"),
])
def test_redetail_retries_solver_failures_but_preserves_source_gates(prior_status, expected):
    beam = _beam("A", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan("F1", "PB", 1, 1, ("A",), (beam.start, beam.end), False, False)
    design = BeamDesignResult("F1-PB1-1", tuple(
        _station(station, 1800.0, 1200.0) for station in (0.0, 3000.0, 6000.0)
    ))
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beam, design),),
        {"A": ("F1-PB1-S1",)}, {"A": ("F1-PB1",)},
    )
    payload = _build_payload(schedule, [beam], frame_types={"A": "Sway Ordinary"})
    payload["raw_rows"][0]["engine_status"] = prior_status
    result = redetail_payload(payload)
    for key in ("raw_rows", "review_rows", "final_rows"):
        assert result[key][0]["engine_status"] == expected
    if prior_status == "FEASIBLE_OPTIMIZATION_INCOMPLETE":
        assert result["raw_rows"][0]["longitudinal_rows"]
        assert result["final_rows"][0]["et1"]
        payload["raw_rows"][0]["left_top_total_mm2"] = None
        missing = redetail_payload(payload)
        assert missing["raw_rows"][0]["engine_status"] == "NO_RESULTS"
    assert payload["raw_rows"][0]["engine_status"] == prior_status


def test_support_faces_are_derived_from_column_centres_and_dimensions() -> None:
    beam = _beam("A", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan("F1", "PB", 1, 1, ("A",), (beam.start, beam.end), False, False)
    design = BeamDesignResult("F1-PB1-1", (_station(0.0, 1000.0, 1000.0), _station(6000.0, 1000.0, 1000.0)))
    schedule = SpanScheduleMapping((SpanScheduleRecord(span, beam, design),), {}, {})
    contexts = _derive_support_face_contexts(
        schedule.records,
        {"C1": (0.0, 0.0, 0.0), "C2": (6000.0, 0.0, 0.0)},
        {"C1": 600.0, "C2": 800.0},
        tolerance_mm=1.0,
    )
    left, right = contexts[span.span_id]
    assert left is not None and right is not None
    assert left.face_location_mm == 300.0
    assert right.face_location_mm == 5600.0
    assert "ETABS column support point" in left.source


def test_sb_sway_special_frame_type_remains_smf() -> None:
    beam = _beam("SB-A", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan(
        "F1",
        "SB",
        1,
        1,
        ("SB-A",),
        (beam.start, beam.end),
        False,
        False,
    )
    design = BeamDesignResult(
        "F1-SB1-1",
        (
            _station(0.0, 1000.0, 1000.0),
            _station(3000.0, 1000.0, 1000.0),
            _station(6000.0, 1000.0, 1000.0),
        ),
    )
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beam, design),),
        {"SB-A": ("F1-SB1-S1",)},
        {"SB-A": ("F1-SB1",)},
    )

    payload = _build_payload(
        schedule,
        [beam],
        frame_types={"SB-A": "Sway Special"},
    )

    raw = payload["raw_rows"][0]
    assert raw["detailing_profile"] == "SMF"
    assert raw["effective_frame_type"] == "SMF"
    assert raw["is_smf"] is True
    assert raw["engine_status"] == "OK"
    assert payload["final_rows"][0]["legs"] == 4
    assert payload["final_rows"][0]["s1"] == payload["final_rows"][0]["s2"]
    secondary = build_secondary_schedule_payload(payload)
    assert len(secondary["schedule_rows"]) == 1
    assert secondary["schedule_rows"][0]["stirrups"] == payload["final_rows"][0]["s1"]
    assert secondary["exception_rows"] == []
    assert len(secondary["raw_rows"]) == 1
    assert len(secondary["review_rows"]) == 1
    assert len(secondary["final_rows"]) == 1
    assert secondary["metadata"]["secondary_workbook_layout"] == "single-sheet-static-v1"
    assert secondary["metadata"]["bottom_reinforcement_strategy"] == "FULL_SPAN_UNIFORM_PER_SPAN"


def test_secondary_schedule_rejects_non_unified_stirrup_row() -> None:
    payload = {
        "metadata": {"schema_version": PAYLOAD_SCHEMA_VERSION},
        "final_rows": [
            {
                "beam_type": "SB",
                "beam_no": "F1-SB1-1",
                "engine_status": "OK",
                "effective_frame_type": "OMF",
                "s2": "T10@100",
                "s1": "T10@200",
            },
            {
                "beam_type": "PB",
                "beam_no": "F1-PB1-1",
                "engine_status": "OK",
                "s2": "T10@100",
                "s1": "T10@200",
            },
        ],
    }

    secondary = build_secondary_schedule_payload(payload)

    assert secondary["schedule_rows"] == []
    assert len(secondary["exception_rows"]) == 1
    assert secondary["exception_rows"][0]["engine_status"] == ("SB_SPAN_STIRRUP_NOT_UNIFIED")


def test_program_determined_pb_defaults_to_smf_after_offline_redetail() -> None:
    beam = _beam("PB-PD", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan(
        "F1",
        "PB",
        1,
        1,
        ("PB-PD",),
        (beam.start, beam.end),
        False,
        False,
    )
    design = BeamDesignResult(
        "F1-PB1-1",
        (
            _station(0.0, 1000.0, 1000.0),
            _station(3000.0, 1000.0, 1000.0),
            _station(6000.0, 1000.0, 1000.0),
        ),
    )
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beam, design),),
        {},
        {},
    )
    payload = _build_payload(
        schedule,
        [beam],
        frame_types={"PB-PD": "Program Determined"},
    )

    assert payload["raw_rows"][0]["effective_frame_type"] == "SMF"
    assert payload["raw_rows"][0]["engine_status"] == "OK"
    redetailed = redetail_payload(payload)
    assert redetailed["raw_rows"][0]["engine_status"] == "OK"
    assert redetailed["final_rows"][0]["engine_status"] == "OK"


def test_capacity_shear_audit_is_jsonl_process_output(tmp_path: Path) -> None:
    beam = _beam("SMF-A", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan("F1", "PB", 1, 1, ("SMF-A",), (beam.start, beam.end), False, False)
    design = BeamDesignResult(
        "F1-PB1-1",
        (_station(0.0, 1000.0, 1000.0), _station(3000.0, 1000.0, 1000.0), _station(6000.0, 1000.0, 1000.0)),
    )
    payload = _build_payload(
        SpanScheduleMapping((SpanScheduleRecord(span, beam, design),), {}, {}),
        [beam],
        frame_types={"SMF-A": "Sway Special"},
    )
    path = write_capacity_shear_audit(
        payload,
        {
            "SMF-A": (
                CapacityShearPoint("SMF-A", 0.0, "EQX", 120.0),
            )
        },
        tmp_path / "capacity.jsonl",
    )
    line = path.read_text(encoding="utf-8").strip()
    assert '"schema":"etabs-beam-generator/capacity-shear@1"' in line
    assert '"effective_frame_type":"SMF"' in line
    assert '"shear_kN":120.0' in line


def test_program_determined_api_conflict_is_recorded_without_changing_smf() -> None:
    beam = _beam("SMF-PD", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan("F1", "PB", 1, 1, ("SMF-PD",), (beam.start, beam.end), False, False)
    design = BeamDesignResult(
        "F1-PB1-1",
        (_station(0.0, 1000.0, 1000.0), _station(3000.0, 1000.0, 1000.0), _station(6000.0, 1000.0, 1000.0)),
    )
    payload = _build_payload(
        SpanScheduleMapping((SpanScheduleRecord(span, beam, design),), {}, {}),
        [beam],
        frame_types={"SMF-PD": "Program Determined"},
        resolved_frame_types={"SMF-PD": "Sway Ordinary"},
    )
    row = payload["raw_rows"][0]
    assert row["effective_frame_type"] == "SMF"
    assert "PROGRAM_DEFAULT_MODEL_CONFLICT" in row["warnings"]


def test_payload_coordinates_adjacent_sb_support_reinforcement_by_beam_line() -> None:
    beam_a = _beam("SB-1", (0, 0, 0), (6000, 0, 0))
    beam_b = _beam("SB-2", (6000, 0, 0), (12000, 0, 0))
    span_a = BeamSpan("F1", "SB", 1, 1, ("SB-1",), (beam_a.start, beam_a.end), False, False)
    span_b = BeamSpan("F1", "SB", 1, 2, ("SB-2",), (beam_b.start, beam_b.end), False, False)
    design_a = BeamDesignResult(
        "F1-SB1-1",
        (
            _station(0.0, 1000.0, 1000.0),
            _station(3000.0, 1000.0, 1000.0),
            _station(6000.0, 1800.0, 1000.0),
        ),
    )
    design_b = BeamDesignResult(
        "F1-SB1-2",
        (
            _station(0.0, 1000.0, 1800.0),
            _station(3000.0, 1000.0, 1000.0),
            _station(6000.0, 1000.0, 1000.0),
        ),
    )
    schedule = SpanScheduleMapping(
        (
            SpanScheduleRecord(span_a, beam_a, design_a),
            SpanScheduleRecord(span_b, beam_b, design_b),
        ),
        {},
        {},
    )
    payload = _build_payload(
        schedule,
        [beam_a, beam_b],
        frame_types={"SB-1": "Non-Sway", "SB-2": "Non-Sway"},
    )

    first, second = payload["final_rows"]
    first_raw, second_raw = payload["raw_rows"]
    assert first["engine_status"] == second["engine_status"] == "OK"
    assert first_raw["longitudinal_rows"]["right"]["top"][0]["diameter_mm"] == second_raw["longitudinal_rows"]["left"]["top"][0]["diameter_mm"]
    assert first_raw["longitudinal_rows"]["right"]["top"][0]["count"] >= 2
    assert second_raw["longitudinal_rows"]["left"]["top"][0]["count"] >= 2
    assert first["eb1"] == first["b1"] == first["eb2"]
    assert second["eb1"] == second["b1"] == second["eb2"]
    assert first["bottom_uniform_regions_match"] is True
    assert second["bottom_uniform_regions_match"] is True


def test_r5_redetail_refreshes_raw_layout_geometry_after_line_coordination() -> None:
    import json

    from src.design.offline_style import DRAWING_POLICY_REVISION_R5_ID

    beam_a = _beam("SB-1", (0, 0, 0), (6000, 0, 0))
    beam_b = _beam("SB-2", (6000, 0, 0), (12000, 0, 0))
    span_a = BeamSpan("F1", "SB", 1, 1, ("SB-1",), (beam_a.start, beam_a.end), False, False)
    span_b = BeamSpan("F1", "SB", 1, 2, ("SB-2",), (beam_b.start, beam_b.end), False, False)
    design_a = BeamDesignResult(
        "F1-SB1-1",
        (
            _station(0.0, 1000.0, 1000.0),
            _station(3000.0, 1000.0, 1000.0),
            _station(6000.0, 3000.0, 1000.0),
        ),
    )
    design_b = BeamDesignResult(
        "F1-SB1-2",
        (
            _station(0.0, 600.0, 3000.0),
            _station(3000.0, 800.0, 1000.0),
            _station(6000.0, 800.0, 1000.0),
        ),
    )
    engine = make_detailing_engine(
        DRAWING_DETAILING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R5_ID,
        ranking_mode="legacy",
    )
    schedule = SpanScheduleMapping(
        (
            SpanScheduleRecord(span_a, beam_a, design_a),
            SpanScheduleRecord(span_b, beam_b, design_b),
        ),
        {},
        {},
        detailing_policy_id=DRAWING_DETAILING_POLICY_ID,
    )
    payload = _build_payload(
        schedule,
        [beam_a, beam_b],
        frame_types={"SB-1": "Non Sway", "SB-2": "Non Sway"},
        engine=engine,
    )
    redetailed = redetail_payload(payload, engine=engine)

    assert all(row["engine_status"] == "OK" for row in redetailed["raw_rows"])
    assert any(
        row["coordination_initial_signature"] != row["coordination_final_signature"]
        for row in redetailed["raw_rows"]
    )
    for raw in redetailed["raw_rows"]:
        geometry = json.loads(raw["rebar_layout_geometry_json"])
        for index, region in enumerate(("left", "middle", "right")):
            for face in ("top", "bottom"):
                rows = raw["longitudinal_rows"][region][face]
                assert geometry[f"{face}_layers"][index] == [
                    float(row["count"]) for row in rows
                ]
                audit = next(
                    item
                    for item in raw["row_coordination_audit"]
                    if item["region"] == region and item["face"] == face
                )
                assert audit["rows"] == rows


def test_mixed_section_span_is_accepted_with_a_review_warning_and_locations() -> None:
    beam_a = _beam("A", (0, 0, 0), (3000, 0, 0), "B500X800")
    beam_b = _beam("B", (3000, 0, 0), (6000, 0, 0), "B400X800")
    geometry = BeamGeometry(
        "F1-PB1-S1",
        "F1-PB1-S1",
        "F1",
        "A-I",
        "B-J",
        beam_a.start,
        beam_b.end,
        beam_a.section,
        800.0,
        ("A", "B"),
        (beam_a.start, beam_a.end, beam_b.end),
    )
    span = BeamSpan(
        "F1",
        "PB",
        1,
        1,
        ("A", "B"),
        geometry.path_points,
        False,
        False,
    )
    design = BeamDesignResult(
        "F1-PB1-1",
        (_station(0.0, 1000.0, 1000.0), _station(6000.0, 1000.0, 1000.0)),
    )
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, geometry, design),),
        {},
        {},
    )

    payload = _build_payload(
        schedule,
        [beam_a, beam_b],
        frame_types={"A": "Sway Ordinary", "B": "Sway Ordinary"},
    )

    assert payload["raw_rows"][0]["engine_status"] == "OK"
    assert payload["raw_rows"][0]["accepted_warning_codes"] == "MIXED_SECTION_REVIEW_REQUIRED"
    assert payload["raw_rows"][0]["warnings"] == "MIXED_SECTION"
    assert payload["final_rows"][0]["engine_status"] == "OK"
    assert payload["final_rows"][0]["et1"]
    assert "source_object_locations" not in payload["final_rows"][0]


def test_empty_candidate_rows_are_never_blank_even_without_area_demands() -> None:
    from src.design.detailing import ProjectDetailingPolicy

    common = {"beam_no": "F1-PB1-1", "beam_type": "PB", "effective_frame_type": "SMF"}
    project = ProjectDetailingPolicy()
    review = _empty_review(common, "DESIGN_RESULTS_STALE", project=project)
    final = _empty_final(common, "DESIGN_RESULTS_STALE", project=project)
    assert "需复核" in review["et1_text"]
    assert "需复核" in review["s1_text"]
    assert "需复核" in final["s2"]
    assert "不得直接用于施工图" in final["review_note"]
    controls = {region: {"top_total_mm2": 0, "top_demand_mm2": 0, "bottom_total_mm2": 0, "av_over_s": 0} for region in ("left", "middle", "right")}
    review = _empty_review(common, "DESIGN_RESULTS_STALE", controls, project)
    assert review["et1_text"] == "2T20"
    assert review["s1_text"] == "2T10@100"
    assert "目录不可用" in _empty_review(common, "DESIGN_RESULTS_STALE", controls)["s1_text"]


def test_mixed_pb_frame_type_is_fail_closed_with_spec_status() -> None:
    beam_a = _beam("A", (0, 0, 0), (3000, 0, 0))
    beam_b = _beam("B", (3000, 0, 0), (6000, 0, 0))
    geometry = BeamGeometry(
        "F1-PB1-S1",
        "F1-PB1-S1",
        "F1",
        "A-I",
        "B-J",
        beam_a.start,
        beam_b.end,
        beam_a.section,
        800.0,
        ("A", "B"),
        (beam_a.start, beam_a.end, beam_b.end),
    )
    span = BeamSpan(
        "F1",
        "PB",
        1,
        1,
        ("A", "B"),
        geometry.path_points,
        False,
        False,
    )
    design = BeamDesignResult(
        "F1-PB1-1",
        (_station(0.0, 1000.0, 1000.0), _station(6000.0, 1000.0, 1000.0)),
    )
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, geometry, design),),
        {},
        {},
    )

    payload = _build_payload(
        schedule,
        [beam_a, beam_b],
        frame_types={"A": "Sway Ordinary", "B": "Sway Special"},
    )

    assert payload["raw_rows"][0]["engine_status"] == ("MIXED_FRAME_TYPE_REVIEW_REQUIRED")
    assert payload["final_rows"][0]["engine_status"] == ("MIXED_FRAME_TYPE_REVIEW_REQUIRED")
    assert "source_object_locations" not in payload["final_rows"][0]


def test_missing_cover_or_aggregate_semantics_is_fail_closed() -> None:
    beam = _beam("A", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan(
        "F1",
        "PB",
        1,
        1,
        ("A",),
        (beam.start, beam.end),
        False,
        False,
    )
    design = BeamDesignResult(
        "F1-PB1-1",
        (
            _station(0.0, 1000.0, 1000.0),
            _station(6000.0, 1000.0, 1000.0),
        ),
    )
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beam, design),),
        {},
        {},
    )

    payload = build_payload_from_schedule(
        schedule,
        [beam],
        section_loader=_section,
        frame_types={"A": "Sway Ordinary"},
        metadata={"model": "sample.EDB", "units": "N_mm_C"},
        clear_cover_to_stirrup_outer_mm=None,
        clear_cover_source="",
        max_aggregate_size_mm=None,
        max_aggregate_size_source="",
    )

    assert payload["raw_rows"][0]["engine_status"] == "COVER_SEMANTICS_UNRESOLVED"

    etabs_cover_payload = build_payload_from_schedule(
        schedule,
        [beam],
        section_loader=_section,
        frame_types={"A": "Sway Ordinary"},
        metadata={"model": "sample.EDB", "units": "N_mm_C"},
        clear_cover_to_stirrup_outer_mm=None,
        clear_cover_source="",
        max_aggregate_size_mm=25,
        max_aggregate_size_source="user-confirmed project input",
    )

    assert etabs_cover_payload["raw_rows"][0]["engine_status"] == "OK"
    assert etabs_cover_payload["raw_rows"][0]["cover_calculation_basis"] == "ETABS_LONGITUDINAL_CENTROID"


def test_redetail_rejects_legacy_schema_without_silent_defaults() -> None:
    with pytest.raises(Exception, match="UNSUPPORTED_PAYLOAD_SCHEMA"):
        redetail_payload({"metadata": {"schema_version": "1.2"}})


def test_redetail_requires_fresh_raw_snapshot_for_policy_change() -> None:
    engine = SimpleNamespace(detailing_policy_id=DRAWING_DETAILING_POLICY_ID)

    with pytest.raises(DetailingError, match="CROSS_STRATEGY_REQUIRES_RAW_SNAPSHOT"):
        redetail_payload(
            {
                "metadata": {
                    "schema_version": PAYLOAD_SCHEMA_VERSION,
                    "detailing_policy_id": "legacy",
                },
                "raw_rows": [],
            },
            engine=engine,
        )


def test_drawing_payload_projects_top_demand_and_serializes_final_regions() -> None:
    beam = _beam("A", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan("F1", "PB", 1, 1, ("A",), (beam.start, beam.end), False, False)
    design = BeamDesignResult(
        "F1-PB1-1",
        (
            _station(0.0, 1800.0, 1300.0),
            _station(2000.0, 2500.0, 1200.0),
            _station(3000.0, 1000.0, 600.0),
            _station(3750.0, 1100.0, 700.0),
            _station(6000.0, 1900.0, 1000.0),
        ),
    )
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beam, design),),
        {},
        {},
        detailing_policy_id=DRAWING_DETAILING_POLICY_ID,
    )
    engine = make_detailing_engine(DRAWING_DETAILING_POLICY_ID, ranking_mode="legacy")

    payload = _build_payload(
        schedule,
        [beam],
        frame_types={"A": "Sway Ordinary"},
        engine=engine,
    )

    raw = payload["raw_rows"][0]
    review = payload["review_rows"][0]
    final = payload["final_rows"][0]
    assert payload["metadata"]["detailing_policy_id"] == DRAWING_DETAILING_POLICY_ID
    assert payload["metadata"]["middle_top_parent_fraction"] == [3 / 8, 5 / 8]
    assert payload["metadata"]["detailing_policy_exceptions"] == [
        "NET_SPACING_MAX_25_DB",
        "REMOVE_PH_OVER_8",
    ]
    assert payload["metadata"]["ranking_mode"] == "legacy"
    assert payload["metadata"]["detailing_policy_rules_version"] == "drawing-style-runtime@1"
    assert payload["metadata"]["detailing_policy_frozen_revision"] == DRAWING_DETAILING_POLICY_ID
    assert payload["metadata"]["detailing_policy_frozen_on"] == "20261024"
    assert payload["metadata"]["detailing_policy_rules_used_for_ranking"] is False
    assert raw["actual_frame_type"] == "Sway Ordinary"
    assert raw["middle_top_total_mm2"] == 1100.0
    assert raw["middle_as_min_top_mm2"] == 700.0
    assert raw["middle_top_demand_mm2"] == 1100.0
    assert raw["middle_top_flex_mm2"] == 900.0
    assert review["t1_required_mm2"] == 1100.0
    assert raw["region_stirrups"]["middle"].keys() == {"diameter_mm", "spacing_mm", "legs"}
    assert final["region_stirrups"]["middle"] == raw["region_stirrups"]["middle"]
    assert "detailing_policy_audit_json" in raw
    assert "detailing_policy_audit_json" in final


def test_v3_no_recommendation_projects_no_guessed_rebar_or_stirrups(monkeypatch) -> None:
    from src.design.detailing import make_fresh_detailing_engine
    from src import rebar_workbook as workbook

    beam = _beam("SB-A", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan("F1", "SB", 2, 1, ("SB-A",), (beam.start, beam.end), False, False)
    design = BeamDesignResult("F1-SB2-1", tuple(
        _station(station, 1800.0, 1200.0) for station in (0.0, 3000.0, 6000.0)
    ))
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beam, design),),
        {"SB-A": (span.span_id,)},
        {},
        detailing_policy_id=DRAWING_V3_POLICY_ID,
    )

    monkeypatch.setattr(
        "src.design.v3.design_line_v3",
        lambda *args, **kwargs: SimpleNamespace(
            status="NO_RECOMMENDATION_FOUND_WITHIN_BUDGET",
            selected=(None,),
            alternatives=(),
            errors=("NO_RECOMMENDATION_FOUND_WITHIN_BUDGET",),
            audit={"candidate_count": 0},
        ),
    )
    payload = _build_payload(
        schedule,
        [beam],
        frame_types={"SB-A": "Non Sway"},
        engine=make_fresh_detailing_engine(DRAWING_V3_POLICY_ID, candidate_lookup="auto"),
    )

    review = payload["review_rows"][0]
    final = payload["final_rows"][0]
    projected = workbook.project_result_table_payload(payload, "SB")["final_rows"][0]
    labels = (
        "et1", "t1", "et2", "eb1", "b1", "eb2", "s1", "s2", "stirrups", "legs",
        "et1_text", "t1_text", "et2_text", "eb1_text", "b1_text", "eb2_text",
        "s1_text", "s2_text", "s2_left_text", "s2_right_text",
    )

    assert payload["raw_rows"][0]["left_top_total_mm2"] > 0
    assert review["et1_required_mm2"] > 0
    for row in (review, final, projected):
        assert row["engine_status"] == "NO_RECOMMENDATION_FOUND_WITHIN_BUDGET"
        assert row["v3_result_selected"] is False
        assert all(row.get(field, "") == "" for field in labels)
        assert row.get("region_stirrups", {}) == {}
        assert row.get("longitudinal_rows", {}) == {}
        assert row.get("row_coordination_audit", []) == []
        assert row["bottom_uniform_text"] == ""
        assert "具体状态" in row["engine_warnings"]


def test_cross_strategy_redetail_reprojects_from_explicit_fresh_schedule() -> None:
    beam = _beam("A", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan("F1", "PB", 1, 1, ("A",), (beam.start, beam.end), False, False)
    design = BeamDesignResult(
        "F1-PB1-1",
        (
            _station(0.0, 1800.0, 1300.0),
            _station(2000.0, 2500.0, 1200.0),
            _station(3000.0, 1000.0, 600.0),
            _station(3750.0, 1100.0, 700.0),
            _station(6000.0, 1900.0, 1000.0),
        ),
    )
    legacy_schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beam, design),),
        {},
        {},
    )
    legacy_payload = _build_payload(
        legacy_schedule,
        [beam],
        frame_types={"A": "Sway Ordinary"},
    )
    fresh_schedule = replace(legacy_schedule, detailing_policy_id=DRAWING_DETAILING_POLICY_ID)
    engine = make_detailing_engine(DRAWING_DETAILING_POLICY_ID, ranking_mode="legacy")

    redetailed = redetail_payload(legacy_payload, engine=engine, raw_snapshot=fresh_schedule)

    raw = redetailed["raw_rows"][0]
    assert raw["middle_top_total_mm2"] == 1100.0
    assert raw["middle_as_min_top_mm2"] == 700.0
    assert raw["middle_top_demand_mm2"] == 1100.0
    assert raw["middle_bottom_total_mm2"] == 1200.0
    assert redetailed["metadata"]["detailing_policy_id"] == DRAWING_DETAILING_POLICY_ID
    assert redetailed["metadata"]["raw_snapshot_scope"] == (
        "explicit fresh target-policy raw span schedule supplied to redetail"
    )


def test_redetail_without_engine_reuses_recorded_policy_revision_and_ranking(monkeypatch) -> None:
    from src.design import detailing as detailing_module

    original_factory = detailing_module.make_detailing_engine
    calls = []
    revision_id = "drawing_20261025_t10_130_spacing_v1"

    def recording_factory(**kwargs):
        calls.append(kwargs)
        return original_factory(**kwargs)

    monkeypatch.setattr(detailing_module, "make_detailing_engine", recording_factory)
    rules_path = str(Path("src/design/policies/drawing_20260924_v1.json").resolve())
    revision_engine = original_factory(
        detailing_policy_id=DRAWING_DETAILING_POLICY_ID,
        ranking_mode="legacy",
        rules_path=rules_path,
        detailing_policy_revision_id=revision_id,
    )
    payload = {
        "metadata": {
            "schema_version": PAYLOAD_SCHEMA_VERSION,
            "detailing_policy_id": DRAWING_DETAILING_POLICY_ID,
            "detailing_policy_revision_id": revision_id,
            "detailing_policy_parameters": revision_engine.detailing_policy_parameters,
            "ranking_mode": "legacy",
            "detailing_policy_rules_file": rules_path,
        },
        "raw_rows": [],
    }

    output = redetail_payload(payload)

    assert calls == [
        {
            "detailing_policy_id": DRAWING_DETAILING_POLICY_ID,
            "ranking_mode": "legacy",
            "rules_path": rules_path,
            "detailing_policy_revision_id": revision_id,
        }
    ]
    assert output["metadata"]["detailing_policy_id"] == DRAWING_DETAILING_POLICY_ID
    assert output["metadata"]["detailing_policy_revision_id"] == revision_id
    assert output["metadata"]["detailing_policy_parameters"] == revision_engine.detailing_policy_parameters
    assert output["metadata"]["ranking_mode"] == "legacy"


def test_redetail_requires_opt_in_for_explicit_same_policy_revision_upgrade() -> None:
    source = {
        "metadata": {
            "schema_version": PAYLOAD_SCHEMA_VERSION,
            "detailing_policy_id": DRAWING_DETAILING_POLICY_ID,
            "detailing_policy_revision_id": "drawing_20261025_t10_130_spacing_v1",
            "detailing_policy_parameters": {"revision_marker": "R3"},
            "ranking_mode": "legacy",
        },
        "raw_rows": [],
    }
    target = SimpleNamespace(
        detailing_policy_id=DRAWING_DETAILING_POLICY_ID,
        detailing_policy_revision_id="drawing_20261025_uniform_hoop_no_4_3_v1",
        detailing_policy_parameters={"revision_marker": "R4"},
    )

    with pytest.raises(DetailingError, match="DETAILING_POLICY_REVISION_MISMATCH"):
        redetail_payload(source, engine=target)


@pytest.mark.parametrize(
    "revision_id",
    [None, "drawing_20260924_v1"],
)
def test_redetail_without_engine_rejects_missing_or_old_drawing_revision(revision_id) -> None:
    payload = {
        "metadata": {
            "schema_version": PAYLOAD_SCHEMA_VERSION,
            "detailing_policy_id": DRAWING_DETAILING_POLICY_ID,
            "detailing_policy_revision_id": revision_id,
            "detailing_policy_parameters": {
                "allowed_spacings_mm": [100, 110, 130, 150, 170, 200],
                "smf_allowed_spacings_mm": [100, 110, 130, 150, 170, 200],
            },
            "ranking_mode": "legacy",
        },
        "raw_rows": [],
    }

    with pytest.raises(DetailingError, match="DETAILING_POLICY_REVISION"):
        redetail_payload(payload)


def test_partial_design_result_is_preserved_and_fail_closed() -> None:
    beam = _beam("A", (0, 0, 0), (6000, 0, 0))
    span = BeamSpan(
        "F1",
        "PB",
        1,
        1,
        ("A",),
        (beam.start, beam.end),
        False,
        False,
    )
    design = BeamDesignResult(
        "F1-PB1-1",
        (
            _station(0.0, 1000.0, 1000.0),
            _station(6000.0, 1000.0, 1000.0),
        ),
        DesignResultStatus.PARTIAL,
        "ERROR: reinforcing design incomplete",
        ("A",),
        "N_mm_C",
        "GetSummaryResultsBeam_2",
    )
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beam, design),),
        {},
        {},
    )

    payload = _build_payload(
        schedule,
        [beam],
        frame_types={"A": "Sway Ordinary"},
    )

    raw = payload["raw_rows"][0]
    assert raw["engine_status"] == "DESIGN_RESULTS_PARTIAL"
    assert raw["design_status_message"] == ("ERROR: reinforcing design incomplete")
    assert raw["design_result_source"] == "GetSummaryResultsBeam_2"
