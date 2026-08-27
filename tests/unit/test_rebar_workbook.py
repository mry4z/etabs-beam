import re
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from src.core.beam_line_span_analyzer import BeamSpan
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation
from src.design.etabs_result import CapacityShearPoint
from src.excel_span_adapter import (
    SpanScheduleMapping,
    SpanScheduleRecord,
)
from src.rebar_workbook import (
    PAYLOAD_SCHEMA_VERSION,
    _frame_types,
    _derive_support_face_contexts,
    _run_artifact_builder,
    _run_rebar_preview_builder,
    build_payload_from_schedule,
    build_secondary_schedule_payload,
    _empty_review,
    _empty_final,
    write_capacity_shear_audit,
    redetail_payload,
    write_rebar_payload,
)


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


def _build_payload(schedule, beams, *, frame_types, resolved_frame_types=None):
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
    )


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
    final = payload["final_rows"][0]
    assert final["eb1"] == final["b1"] == final["eb2"]
    assert final["bottom_uniform_regions_match"] is True
    assert payload["review_rows"][0]["s2_left_av_required"] == 0.35
    assert payload["review_rows"][0]["s2_left_shear_source"] == (
        "GetSummaryResultsBeam_2.VMajorArea (Av/s)"
    )

    destination = write_rebar_payload(payload, tmp_path / "payload.json")
    assert destination.is_file()
    redetailed = redetail_payload(payload)
    assert redetailed["review_rows"][0]["engine_status"] == "OK"


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
    assert payload["final_rows"][0]["legs"] == 2
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
    assert first["engine_status"] == second["engine_status"] == "OK"
    assert first["et2"] == second["et1"]
    assert first["eb1"] == first["b1"] == first["eb2"]
    assert second["eb1"] == second["b1"] == second["eb2"]
    assert first["bottom_uniform_regions_match"] is True
    assert second["bottom_uniform_regions_match"] is True


def test_mixed_section_span_is_fail_closed() -> None:
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

    assert payload["raw_rows"][0]["engine_status"] == ("MIXED_SECTION_REVIEW_REQUIRED")
    assert payload["review_rows"][0]["et1_text"].endswith(("T16", "T18", "T20", "T22", "T25", "T28", "T32"))
    assert "不得直接用于施工图" in payload["review_rows"][0]["review_note"]
    assert payload["final_rows"][0]["engine_status"] == ("MIXED_SECTION_REVIEW_REQUIRED")


def test_empty_candidate_rows_are_never_blank_even_without_area_demands() -> None:
    common = {
        "beam_no": "F1-PB1-1",
        "beam_type": "PB",
        "effective_frame_type": "SMF",
    }
    review = _empty_review(common, "DESIGN_RESULTS_STALE")
    final = _empty_final(common, "DESIGN_RESULTS_STALE")

    assert review["et1_text"] == "2T20"
    assert review["t1_text"] == "2T20"
    assert review["s1_text"] == "2T8@100"
    assert review["face_text"] == "2T20（面筋候选）"
    assert final["eb2"] == "2T20"
    assert final["s2"] == "2T8@100"
    assert "不得直接用于施工图" in final["review_note"]


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
