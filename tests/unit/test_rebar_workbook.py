from pathlib import Path
import re

from src.core.beam_line_span_analyzer import BeamSpan
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignStation
from src.excel_span_adapter import (
    SpanScheduleMapping,
    SpanScheduleRecord,
)
from src.rebar_workbook import (
    build_payload_from_schedule,
    redetail_payload,
    write_rebar_payload,
)


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
        "cover_mm": 40.0,
        "fc_mpa": 30.0,
        "fy_mpa": 420.0,
        "fyt_mpa": 420.0,
    }


def test_formal_payload_contains_three_sheet_rows_and_can_redetail(tmp_path: Path) -> None:
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

    payload = build_payload_from_schedule(
        schedule,
        [beam],
        section_loader=_section,
        frame_types={"A": "Sway Ordinary"},
        metadata={"model": "sample.EDB", "units": "N_mm_C"},
    )

    assert len(payload["raw_rows"]) == 1
    assert payload["review_rows"][0]["engine_status"] == "OK"
    assert payload["review_rows"][0]["et1_text"]
    assert payload["final_rows"][0]["beam_no"] == "F1-PB1-1"
    assert payload["raw_rows"][0]["detailing_profile"] == "PB_OMF"
    assert payload["raw_rows"][0]["left_top_total_combo"] == "TOP-C"
    assert payload["raw_rows"][0]["right_top_total_station_mm"] == 6000.0

    destination = write_rebar_payload(payload, tmp_path / "payload.json")
    assert destination.is_file()
    redetailed = redetail_payload(payload)
    assert redetailed["review_rows"][0]["engine_status"] == "OK"


def test_sb_sway_special_frame_type_stays_ordinary_without_chapter_18() -> None:
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

    payload = build_payload_from_schedule(
        schedule,
        [beam],
        section_loader=_section,
        frame_types={"SB-A": "Sway Special"},
        metadata={"model": "sample.EDB", "units": "N_mm_C"},
    )

    raw = payload["raw_rows"][0]
    assert raw["detailing_profile"] == "SB_ORDINARY_NO_CH18"
    assert raw["is_smf"] is False
    assert raw["engine_status"] == "OK"
    assert "SB_ETABS_SWAY_DESIGN_RESULT_REVIEW" in raw["warnings"]
    assert payload["final_rows"][0]["legs"] == 2


def test_program_determined_pb_stays_blocked_after_offline_redetail() -> None:
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
    payload = build_payload_from_schedule(
        schedule,
        [beam],
        section_loader=_section,
        frame_types={"PB-PD": "Program Determined"},
        metadata={"model": "sample.EDB", "units": "N_mm_C"},
    )

    assert payload["raw_rows"][0]["engine_status"] == "FRAME_TYPE_REVIEW_REQUIRED"
    redetailed = redetail_payload(payload)
    assert redetailed["raw_rows"][0]["engine_status"] == "FRAME_TYPE_REVIEW_REQUIRED"
    assert redetailed["final_rows"][0]["engine_status"] == "FRAME_TYPE_REVIEW_REQUIRED"


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
    payload = build_payload_from_schedule(
        schedule,
        [beam_a, beam_b],
        section_loader=_section,
        frame_types={"SB-1": "Non-Sway", "SB-2": "Non-Sway"},
        metadata={"model": "sample.EDB", "units": "N_mm_C"},
    )

    first, second = payload["final_rows"]
    assert first["engine_status"] == second["engine_status"] == "OK"
    assert first["et2"] == second["et1"]
    assert re.search(r"T(\d+)", first["eb2"]).group(1) == re.search(r"T(\d+)", second["eb1"]).group(1)


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

    payload = build_payload_from_schedule(
        schedule,
        [beam_a, beam_b],
        section_loader=_section,
        frame_types={"A": "Sway Ordinary", "B": "Sway Ordinary"},
        metadata={"model": "sample.EDB", "units": "N_mm_C"},
    )

    assert payload["raw_rows"][0]["engine_status"] == ("MIXED_SECTION_DETAILING_BLOCKED")
    assert payload["review_rows"][0]["et1_text"] == ""
    assert payload["final_rows"][0]["engine_status"] == ("MIXED_SECTION_DETAILING_BLOCKED")
