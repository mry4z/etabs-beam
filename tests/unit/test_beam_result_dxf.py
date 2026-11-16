from pathlib import Path
from dataclasses import replace

import pytest

from src.core.beam_line_span_analyzer import BeamSpan
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation
from src.design.region_results import DRAWING_DETAILING_POLICY_ID
import src.drawing.beam_result_dxf as beam_result_dxf_module
from src.drawing.beam_result_dxf import _result_labels, export_beam_result_and_section_dxfs
from src.excel_span_adapter import SpanObjectPart, SpanScheduleMapping, SpanScheduleRecord
from src.utils.geometry import distance


def _beam(name: str = "B1", section: str = "B400X800") -> BeamGeometry:
    return BeamGeometry(
        name,
        name,
        "F1",
        "P1",
        "P2",
        (0.0, 0.0, 3000.0),
        (10000.0, 0.0, 3000.0),
        section,
        800.0,
        path_points=((0.0, 0.0, 3000.0), (10000.0, 0.0, 3000.0)),
    )


def _schedule(beam: BeamGeometry, design: BeamDesignResult) -> SpanScheduleMapping:
    span = BeamSpan("F1", "PB", 1, 1, (beam.name,), beam.path_points, False, False)
    return SpanScheduleMapping((SpanScheduleRecord(span, beam, design),), {}, {})


def _text_entities(dxf: str) -> list[dict[str, str]]:
    lines = dxf.splitlines()
    entities: list[dict[str, str]] = []
    index = 0
    while index < len(lines):
        if index + 1 < len(lines) and lines[index] == "0" and lines[index + 1] == "TEXT":
            end = index + 2
            while end < len(lines) and lines[end] != "0":
                end += 1
            entity = lines[index:end]
            entities.append(dict(zip(entity[::2], entity[1::2])))
            index = end
            continue
        index += 1
    return entities


def _sample_design() -> BeamDesignResult:
    stations = tuple(
        DesignStation(
            location,
            "",
            top,
            "",
            bottom,
            "",
            shear,
            "",
            350.0,
            "",
            torsion_trans,
            top_total_area=top,
            bottom_total_area=bottom,
        )
        for location, top, bottom, shear, torsion_trans in (
            (0.0, 2100.0, 1400.0, 0.13, 0.0),
            (5000.0, 1200.0, 1800.0, 0.04, 0.01),
            (10000.0, 0.0, 2000.0, 0.13, 0.0),
        )
    )
    return BeamDesignResult("F1-PB1-S1", stations)


def _frozen_controls(top: tuple[float, float, float], bottom: tuple[float, float, float]):
    controls = {}
    for region, top_value, bottom_value in zip(("left", "middle", "right"), top, bottom):
        controls[region] = {
            "top_total_mm2": top_value,
            "top_demand_mm2": top_value,
            "bottom_total_mm2": bottom_value,
            "av_over_s": 0.0,
            "at_over_s": 0.0,
            "al_mm2": 0.0,
            "design_errors": "",
            "middle_top_window_invalid_reason": "",
            "middle_top_window_station_count": 1,
        }
    return controls


def test_result_and_section_dxfs_have_sample_labels_and_true_xy_geometry(tmp_path: Path) -> None:
    beam = _beam()
    exported = export_beam_result_and_section_dxfs(
        _schedule(beam, _sample_design()),
        [beam],
        lambda _: {"width_mm": 400.0, "depth_mm": 800.0},
        tmp_path,
        text_height=50.0,
    )

    result = exported.result_paths[0].read_text(encoding="ascii")
    section = exported.section_paths[0].read_text(encoding="ascii")
    for label in ("G1.3-0.6", "21-12-0", "14-18-20", "VT3.5-0.1"):
        assert label in result
    assert "400*800" in section
    assert "$ACADVER\n1\nAC1009" in result
    assert "7\nARIAL" in result
    assert {entity["8"] for entity in _text_entities(result)} == {"dsptext_beam"}
    assert {entity["8"] for entity in _text_entities(section)} == {"dsptext_beam"}
    assert not any(entity["1"].startswith(("wpj", "构件编号")) for entity in _text_entities(result + section))
    z_values = [result.splitlines()[index + 1] for index, value in enumerate(result.splitlines()) if value == "30"]
    assert z_values and set(z_values) == {"0.000"}
    assert "10000.000" in result
    assert not exported.issues


def test_drawing_result_labels_share_upper_middle_top_demand_view() -> None:
    beam = _beam()
    design = BeamDesignResult(
        beam.name,
        tuple(
            DesignStation(
                location,
                "T",
                top,
                "B",
                1000.0,
                "V",
                0.1,
                "TL",
                10.0,
                "TV",
                0.02,
                top_total_area=top,
                top_min_area=top_min,
                bottom_total_area=1000.0,
            )
            for location, top, top_min in (
                (0.0, 4000.0, 3000.0),
                (2000.0, 5000.0, 3000.0),
                (3750.0, 1000.0, 1000.0),
                (5000.0, 2000.0, 2000.0),
                (6250.0, 3000.0, 6000.0),
                (10000.0, 5000.0, 3000.0),
            )
        ),
    )
    record = SpanScheduleRecord(
        BeamSpan("F1", "PB", 1, 1, (beam.name,), beam.path_points, False, False),
        beam,
        design,
    )

    legacy, legacy_error = _result_labels(record, 800.0)
    drawing, drawing_error = _result_labels(
        record,
        800.0,
        detailing_policy_id=DRAWING_DETAILING_POLICY_ID,
        parent_length_mm=beam.length,
    )

    assert legacy_error is None
    assert drawing_error is None
    assert legacy[1] == "40-50-50"
    assert drawing[1] == "40-60-50"


def test_explicit_frozen_controls_drive_result_text_even_when_source_stations_differ(tmp_path: Path) -> None:
    beam = _beam()
    exported = export_beam_result_and_section_dxfs(
        _schedule(beam, _sample_design()),
        [beam],
        lambda _: {"width_mm": 400.0, "depth_mm": 800.0},
        tmp_path,
        region_controls_by_row_id={
            "F1-PB1-1": _frozen_controls((100.0, 200.0, 300.0), (400.0, 500.0, 600.0)),
        },
    )
    result = exported.result_paths[0].read_text(encoding="ascii")
    assert "1-2-3" in result
    assert "4-5-6" in result
    assert "21-12-0" not in result


def test_v3_station_path_marks_missing_middle_top_window_unknown() -> None:
    beam = _beam()
    template = _sample_design().stations[0]
    design = BeamDesignResult(
        "F1-PB1-1",
        tuple(replace(template, location=location) for location in (0.0, 2000.0, 8000.0, 10000.0)),
    )
    span = BeamSpan("F1", "PB", 1, 1, (beam.name,), beam.path_points, False, False)
    record = SpanScheduleRecord(span, beam, design)

    legacy, legacy_error = _result_labels(record, 800.0)
    v3, v3_error = _result_labels(
        record,
        800.0,
        detailing_policy_id=beam_result_dxf_module.V3_DETAILING_POLICY_ID,
        parent_length_mm=beam.length,
    )

    assert legacy_error is None
    assert legacy[1]
    assert v3 == ("", "", "", "")
    assert v3_error is not None
    assert v3_error[0] == "DRAWING_MIDDLE_QUARTER_NO_STATIONS"


def test_v3_direct_dxf_uses_partial_region_evidence_without_nearest_grouping(monkeypatch) -> None:
    from src.design.region_results import DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE

    beam = _beam()
    template = _sample_design().stations[0]
    design = BeamDesignResult(
        "F1-PB1-1",
        (replace(template, location=0.0), replace(template, location=10000.0)),
    )
    span = BeamSpan("F1", "PB", 1, 1, (beam.name,), beam.path_points, False, False)
    record = SpanScheduleRecord(span, beam, design)
    original = beam_result_dxf_module.build_region_controls
    observed = {}

    def capture_controls(*args, **kwargs):
        observed["projection_mode"] = kwargs.get("projection_mode")
        controls = original(*args, **kwargs)
        observed["middle"] = controls["middle"]
        return controls

    def fail_nearest_grouping(*_args, **_kwargs):
        raise AssertionError("V3 DXF must consume strict region controls without nearest grouping")

    monkeypatch.setattr(beam_result_dxf_module, "build_region_controls", capture_controls)
    monkeypatch.setattr(beam_result_dxf_module, "group_design_stations", fail_nearest_grouping)
    labels, error = _result_labels(
        record,
        800.0,
        detailing_policy_id=beam_result_dxf_module.V3_DETAILING_POLICY_ID,
        parent_length_mm=beam.length,
    )

    assert labels == ("", "", "", "")
    assert error is not None and error[0] == "DRAWING_MIDDLE_QUARTER_NO_STATIONS"
    assert observed["projection_mode"] == DRAWING_V3_PARTIAL_EVIDENCE_RESEARCH_MODE
    assert observed["middle"]["region_evidence"]["status"] == "UNKNOWN"
    assert observed["middle"]["region_evidence"]["station_count"] == 0


def test_explicit_no_design_is_n_a_in_dxf_summary_and_emits_no_error_text(tmp_path: Path) -> None:
    beam = _beam()
    design = BeamDesignResult(
        beam.name,
        status=DesignResultStatus.SKIPPED,
        status_message="EXCLUDED_NO_DESIGN; GetDesignProcedure=7 No Design",
    )
    schedule = replace(
        _schedule(beam, design),
        detailing_policy_id=beam_result_dxf_module.V3_DETAILING_POLICY_ID,
    )
    exported = export_beam_result_and_section_dxfs(
        schedule,
        [beam],
        lambda _: (_ for _ in ()).throw(AssertionError("excluded section must not be queried")),
        tmp_path,
        detailing_policy_id=beam_result_dxf_module.V3_DETAILING_POLICY_ID,
    )

    assert not exported.issues
    assert {(summary.output, summary.status) for summary in exported.summaries} == {
        ("section", "EXCLUDED_NO_DESIGN"),
        ("result", "EXCLUDED_NO_DESIGN"),
    }
    assert _text_entities(exported.result_paths[0].read_text(encoding="ascii")) == []
    assert _text_entities(exported.section_paths[0].read_text(encoding="ascii")) == []
    labels, error = _result_labels(
        SpanScheduleRecord(schedule.records[0].span, beam, design),
        800.0,
        detailing_policy_id=beam_result_dxf_module.V3_DETAILING_POLICY_ID,
    )
    assert labels == ("", "", "", "")
    assert error is None


def test_no_design_phrase_does_not_hide_a_real_api_failure_section(tmp_path: Path) -> None:
    beam = _beam()
    design = BeamDesignResult(
        beam.name,
        status=DesignResultStatus.API_ERROR,
        status_message="No Design API result was available",
    )
    exported = export_beam_result_and_section_dxfs(
        _schedule(beam, design),
        [beam],
        lambda _: {"width_mm": 400.0, "depth_mm": 800.0},
        tmp_path,
    )

    section = exported.section_paths[0].read_text(encoding="ascii")
    result_summaries = [summary.status for summary in exported.summaries if summary.output == "result"]
    assert "400*800" in section
    assert result_summaries == ["API_ERROR"]


def test_mixed_section_no_design_part_has_no_section_or_result_annotation(tmp_path: Path) -> None:
    first = BeamGeometry(
        "B1", "B1", "F1", "P1", "P2", (0.0, 0.0, 3000.0), (5000.0, 0.0, 3000.0),
        "A", 800.0, path_points=((0.0, 0.0, 3000.0), (5000.0, 0.0, 3000.0)),
    )
    second = BeamGeometry(
        "B2", "B2", "F1", "P2", "P3", (5000.0, 0.0, 3000.0), (10000.0, 0.0, 3000.0),
        "B", 900.0, path_points=((5000.0, 0.0, 3000.0), (10000.0, 0.0, 3000.0)),
    )
    parent_id = "F1-PB1-1"
    span = BeamSpan("F1", "PB", 1, 1, ("B1", "B2"), first.path_points + second.path_points[1:], False, False)
    normal_design = replace(
        _sample_design(),
        stations=tuple(replace(station, location=station.location / 2.0)
                       for station in _sample_design().stations),
    )
    parts = (
        SpanObjectPart(
            parent_id,
            f"{parent_id}.1",
            1,
            "B1",
            first,
            normal_design,
            True,
            False,
            section="A",
            parent_offset_mm=0.0,
        ),
        SpanObjectPart(
            parent_id,
            f"{parent_id}.2",
            2,
            "B2",
            second,
            BeamDesignResult(
                "B2",
                status=DesignResultStatus.SKIPPED,
                status_message="EXCLUDED_NO_DESIGN; GetDesignProcedure=7 No Design",
            ),
            False,
            True,
            section="B",
            parent_offset_mm=5000.0,
        ),
    )
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, first, normal_design, parts),), {}, {}
    )

    exported = export_beam_result_and_section_dxfs(
        schedule,
        [first, second],
        lambda section: {"width_mm": 400.0 if section == "A" else 500.0,
                         "depth_mm": 800.0 if section == "A" else 900.0},
        tmp_path,
    )

    section = exported.section_paths[0].read_text(encoding="ascii")
    result = exported.result_paths[0].read_text(encoding="ascii")
    result_summaries = {(summary.span_id, summary.status) for summary in exported.summaries
                        if summary.output == "result"}
    assert "400*800" in section
    assert "500*900" not in section
    assert section.count("\nPOLYLINE\n") == 1
    assert result.count("\nPOLYLINE\n") == 1
    assert (f"{parent_id}.1", "AVAILABLE") in result_summaries
    assert (f"{parent_id}.2", "EXCLUDED_NO_DESIGN") in result_summaries


def test_unavailable_units_and_invalid_values_are_red_err_without_zero_fill(tmp_path: Path) -> None:
    beam = _beam()
    bad = DesignStation(
        0.0,
        "",
        -1.0,
        "",
        1.0,
        "",
        0.0,
        "",
        0.0,
        "",
        0.0,
        top_total_area=-1.0,
        bottom_total_area=1.0,
    )
    design = BeamDesignResult(
        "F1-PB1-S1",
        (bad,),
        status=DesignResultStatus.AVAILABLE,
    )
    exported = export_beam_result_and_section_dxfs(
        _schedule(beam, design),
        [beam],
        lambda _: {"width_mm": 400.0, "depth_mm": 800.0},
        tmp_path,
    )
    result = exported.result_paths[0].read_text(encoding="ascii")
    for label in ("GERR-ERR", "ERR-ERR-ERR", "VTERR-ERR"):
        assert label in result
    assert {entity["8"] for entity in _text_entities(result)} == {"dsptext_beam"}
    assert "-0" not in result
    assert {issue.code for issue in exported.issues} == {"INVALID_DESIGN_VALUE"}


def test_mixed_source_sections_are_split_into_independent_subsegments(tmp_path: Path) -> None:
    first = BeamGeometry(
        "B1",
        "B1",
        "F1",
        "P1",
        "P2",
        (0.0, 0.0, 3000.0),
        (5000.0, 0.0, 3000.0),
        "B400X800",
        800.0,
        path_points=((0.0, 0.0, 3000.0), (5000.0, 0.0, 3000.0)),
    )
    second = BeamGeometry(
        "B2",
        "B2",
        "F1",
        "P2",
        "P3",
        (5000.0, 0.0, 3000.0),
        (10000.0, 0.0, 3000.0),
        "B500X900",
        900.0,
        path_points=((5000.0, 0.0, 3000.0), (10000.0, 0.0, 3000.0)),
    )
    span = BeamSpan(
        "F1",
        "PB",
        1,
        1,
        ("B1", "B2"),
        ((0.0, 0.0, 3000.0), (5000.0, 0.0, 3000.0), (10000.0, 0.0, 3000.0)),
        False,
        False,
    )
    design = replace(
        _sample_design(),
        stations=tuple(
            replace(station, source_object="B1" if station.location < 5000.0 else "B2")
            for station in _sample_design().stations
        ),
    )
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, first, design),),
        {},
        {},
    )
    exported = export_beam_result_and_section_dxfs(
        schedule,
        [first, second],
        lambda section: {
            "width_mm": 400.0 if section == "B400X800" else 500.0,
            "depth_mm": 800.0 if section == "B400X800" else 900.0,
        },
        tmp_path,
    )
    section = exported.section_paths[0].read_text(encoding="ascii")
    result = exported.result_paths[0].read_text(encoding="ascii")
    assert section.count("\nPOLYLINE\n") == 2
    assert "400*800" in section
    assert "500*900" in section
    assert result.count("\nPOLYLINE\n") == 2
    assert not any(issue.code == "MIXED_OR_MISSING_SECTION" for issue in exported.issues)
    assert {item.span_id for item in exported.summaries} == {
        "F1-PB1-1.1",
        "F1-PB1-1.2",
    }


def test_mixed_section_children_use_their_own_frozen_control_row_ids(tmp_path: Path) -> None:
    first = BeamGeometry(
        "B1", "B1", "F1", "P1", "P2", (0.0, 0.0, 3000.0), (5000.0, 0.0, 3000.0),
        "B400X800", 800.0, path_points=((0.0, 0.0, 3000.0), (5000.0, 0.0, 3000.0)),
    )
    second = BeamGeometry(
        "B2", "B2", "F1", "P2", "P3", (5000.0, 0.0, 3000.0), (10000.0, 0.0, 3000.0),
        "B500X900", 900.0, path_points=((5000.0, 0.0, 3000.0), (10000.0, 0.0, 3000.0)),
    )
    span = BeamSpan(
        "F1", "PB", 1, 1, ("B1", "B2"),
        ((0.0, 0.0, 3000.0), (5000.0, 0.0, 3000.0), (10000.0, 0.0, 3000.0)), False, False,
    )
    design = _sample_design()
    parts = (
        SpanObjectPart("F1-PB1-1", "F1-PB1-1.1", 1, "B1", first, design, True, False,
                       section="B400X800", parent_offset_mm=0.0),
        SpanObjectPart("F1-PB1-1", "F1-PB1-1.2", 2, "B2", second, design, False, True,
                       section="B500X900", parent_offset_mm=5000.0),
    )
    schedule = SpanScheduleMapping((SpanScheduleRecord(span, first, design, parts),), {}, {})
    frozen = {
        "F1-PB1-1.1": _frozen_controls((100.0, 200.0, 300.0), (400.0, 500.0, 600.0)),
        "F1-PB1-1.2": _frozen_controls((700.0, 800.0, 900.0), (1000.0, 1100.0, 1200.0)),
    }
    exported = export_beam_result_and_section_dxfs(
        schedule,
        [first, second],
        lambda section: {"width_mm": 400.0 if section == "B400X800" else 500.0,
                         "depth_mm": 800.0 if section == "B400X800" else 900.0},
        tmp_path,
        region_controls_by_row_id=frozen,
    )
    result = exported.result_paths[0].read_text(encoding="ascii")
    assert "1-2-3" in result
    assert "7-8-9" in result
    assert "21-12-0" not in result

    with pytest.raises(KeyError, match="FROZEN_REGION_CONTROLS_MISSING_ROW: F1-PB1-1.1"):
        export_beam_result_and_section_dxfs(
            schedule,
            [first, second],
            lambda section: {"width_mm": 400.0 if section == "B400X800" else 500.0,
                             "depth_mm": 800.0 if section == "B400X800" else 900.0},
            tmp_path / "missing-child",
            region_controls_by_row_id={"F1-PB1-1": frozen["F1-PB1-1.1"]},
        )


def test_adjacent_same_section_source_objects_are_merged(tmp_path: Path) -> None:
    first = BeamGeometry(
        "B1",
        "B1",
        "F1",
        "P1",
        "P2",
        (0.0, 0.0, 3000.0),
        (5000.0, 0.0, 3000.0),
        "B400X800",
        800.0,
        path_points=((0.0, 0.0, 3000.0), (5000.0, 0.0, 3000.0)),
    )
    second = replace(
        first,
        name="B2",
        label="B2",
        point_i="P2",
        point_j="P3",
        start=(5000.0, 0.0, 3000.0),
        end=(10000.0, 0.0, 3000.0),
        path_points=((5000.0, 0.0, 3000.0), (10000.0, 0.0, 3000.0)),
    )
    span = BeamSpan(
        "F1",
        "PB",
        1,
        1,
        ("B1", "B2"),
        ((0.0, 0.0, 3000.0), (5000.0, 0.0, 3000.0), (10000.0, 0.0, 3000.0)),
        False,
        False,
    )
    exported = export_beam_result_and_section_dxfs(
        SpanScheduleMapping((SpanScheduleRecord(span, first, _sample_design()),), {}, {}),
        [first, second],
        lambda _: {"width_mm": 400.0, "depth_mm": 800.0},
        tmp_path,
    )
    section = exported.section_paths[0].read_text(encoding="ascii")
    result = exported.result_paths[0].read_text(encoding="ascii")
    assert section.count("\nPOLYLINE\n") == 1
    assert result.count("\nPOLYLINE\n") == 1
    assert "400*800" in section
    assert not exported.issues


def test_non_adjacent_same_section_objects_remain_separate(tmp_path: Path) -> None:
    points = tuple((value, 0.0, 3000.0) for value in (0.0, 3000.0, 6000.0, 10000.0))
    first = BeamGeometry("B1", "B1", "F1", "P1", "P2", points[0], points[1], "A", 800.0)
    middle = BeamGeometry("B2", "B2", "F1", "P2", "P3", points[1], points[2], "B", 900.0)
    last = BeamGeometry("B3", "B3", "F1", "P3", "P4", points[2], points[3], "A", 800.0)
    span = BeamSpan("F1", "PB", 1, 1, ("B1", "B2", "B3"), points, False, False)
    exported = export_beam_result_and_section_dxfs(
        SpanScheduleMapping((SpanScheduleRecord(span, first, _sample_design()),), {}, {}),
        [first, middle, last],
        lambda section: {
            "width_mm": 400.0 if section == "A" else 500.0,
            "depth_mm": 800.0 if section == "A" else 900.0,
        },
        tmp_path,
    )
    section = exported.section_paths[0].read_text(encoding="ascii")
    assert section.count("\nPOLYLINE\n") == 3
    assert section.count("400*800") == 2
    assert section.count("500*900") == 1


def test_missing_design_stations_mark_only_the_affected_subsegment(tmp_path: Path) -> None:
    first = BeamGeometry("B1", "B1", "F1", "P1", "P2", (0.0, 0.0, 0.0), (5000.0, 0.0, 0.0), "A")
    second = BeamGeometry("B2", "B2", "F1", "P2", "P3", (5000.0, 0.0, 0.0), (10000.0, 0.0, 0.0), "B")
    span = BeamSpan(
        "F1", "PB", 1, 1, ("B1", "B2"), ((0.0, 0.0, 0.0), (5000.0, 0.0, 0.0), (10000.0, 0.0, 0.0)), False, False
    )
    first_station = replace(_sample_design().stations[0], source_object="B1")
    design = BeamDesignResult(
        "F1-PB1-S1",
        (first_station,),
        status=DesignResultStatus.PARTIAL,
        status_message="B2 缺少设计结果",
    )
    exported = export_beam_result_and_section_dxfs(
        SpanScheduleMapping((SpanScheduleRecord(span, first, design),), {}, {}),
        [first, second],
        lambda _: {"width_mm": 400.0, "depth_mm": 800.0},
        tmp_path,
    )
    result = exported.result_paths[0].read_text(encoding="ascii")
    statuses = {(item.span_id, item.status) for item in exported.summaries if item.output == "result"}
    assert ("F1-PB1-1.1", "AVAILABLE") in statuses
    assert ("F1-PB1-1.2", "NO_RESULTS") in statuses
    assert result.count("GERR-ERR") == 1


def test_unresolved_mixed_object_part_keeps_ordinal_and_does_not_fallback_to_merged_parent_design(
    tmp_path: Path,
) -> None:
    points = tuple((value, 0.0, 0.0) for value in (0.0, 2.5, 5.0, 7.5, 10.0))
    beams = (
        BeamGeometry("B1", "B1", "F1", "P1", "P2", points[0], points[1], "A"),
        BeamGeometry("B2", "B2", "F1", "P2", "P3", points[1], points[2], "B"),
        BeamGeometry("B3", "B3", "F1", "P3", "P4", points[2], points[3], "B"),
        BeamGeometry("B4", "B4", "F1", "P4", "P5", points[3], points[4], "A"),
    )
    span = BeamSpan("F1", "PB", 1, 1, tuple(beam.name for beam in beams), points, False, False)
    parent_design = _sample_design()
    parts = tuple(
        SpanObjectPart(
            "F1-PB1-1",
            f"F1-PB1-1.{index}",
            index,
            beam.name,
            None if index == 2 else beam,
            BeamDesignResult(
                f"F1-PB1-1.{index}",
                status=DesignResultStatus.API_ERROR if index == 2 else DesignResultStatus.NO_RESULTS,
                status_message="geometry unresolved" if index == 2 else "missing part stations",
                source_objects=(beam.name,),
            ),
            index == 1,
            index == 4,
            "Object B2 cannot be projected" if index == 2 else "",
            beam.section,
            sum(distance(points[i], points[i + 1]) for i in range(index - 1)),
        )
        for index, beam in enumerate(beams, start=1)
    )
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beams[0], parent_design, parts),),
        {},
        {},
    )

    exported = export_beam_result_and_section_dxfs(
        schedule,
        beams,
        lambda section: {"width_mm": 400.0, "depth_mm": 800.0},
        tmp_path,
    )

    assert {item.span_id for item in exported.summaries if item.output == "section"} == {
        "F1-PB1-1.1",
        "F1-PB1-1.2",
        "F1-PB1-1.3",
        "F1-PB1-1.4",
    }
    assert {item.span_id for item in exported.summaries if item.output == "result"} == {
        "F1-PB1-1.1",
        "F1-PB1-1.2",
        "F1-PB1-1.3",
        "F1-PB1-1.4",
    }
    unresolved = [issue for issue in exported.issues if issue.span_id == "F1-PB1-1.2"]
    assert {(issue.output, issue.code) for issue in unresolved} == {
        ("section", "OBJECT_PART_GEOMETRY_UNRESOLVED"),
        ("result", "OBJECT_PART_GEOMETRY_UNRESOLVED"),
    }
    assert sum(
        path.read_text(encoding="ascii").count("\nPOLYLINE\n")
        for path in exported.section_paths
    ) == 3
    assert sum(
        path.read_text(encoding="ascii").count("\nPOLYLINE\n")
        for path in exported.result_paths
    ) == 3


def test_each_story_gets_independent_result_and_section_files(tmp_path: Path) -> None:
    first = _beam("B1")
    second = BeamGeometry(
        "B2",
        "B2",
        "F2",
        "P3",
        "P4",
        (0.0, 1000.0, 6000.0),
        (10000.0, 1000.0, 6000.0),
        "B400X800",
        800.0,
        path_points=((0.0, 1000.0, 6000.0), (10000.0, 1000.0, 6000.0)),
    )
    first_span = BeamSpan("F1", "PB", 1, 1, ("B1",), first.path_points, False, False)
    second_span = BeamSpan("F2", "PB", 1, 1, ("B2",), second.path_points, False, False)
    schedule = SpanScheduleMapping(
        (
            SpanScheduleRecord(first_span, first, _sample_design()),
            SpanScheduleRecord(second_span, second, BeamDesignResult("F2-PB1-S1", _sample_design().stations)),
        ),
        {},
        {},
    )
    exported = export_beam_result_and_section_dxfs(
        schedule,
        [first, second],
        lambda _: {"width_mm": 400.0, "depth_mm": 800.0},
        tmp_path,
    )
    assert all(path.parent == tmp_path for path in (*exported.result_paths, *exported.section_paths))
    assert not (tmp_path / "beam_design_results").exists()
    assert not (tmp_path / "beam_sections").exists()
    assert {path.name for path in exported.result_paths} == {"wpj-F1.dxf", "wpj-F2.dxf"}
    assert {path.name for path in exported.section_paths} == {"构件编号-F1.dxf", "构件编号-F2.dxf"}
    assert all(path.read_text(encoding="ascii").count("\nPOLYLINE\n") == 1 for path in exported.result_paths)


def test_repeated_export_removes_only_stale_dxf_families(tmp_path: Path) -> None:
    stale_result = tmp_path / "wpj-OLD.dxf"
    stale_section = tmp_path / "构件编号-OLD.dxf"
    unrelated = tmp_path / "keep.txt"
    stale_result.write_text("old result", encoding="ascii")
    stale_section.write_text("old section", encoding="ascii")
    unrelated.write_text("keep", encoding="ascii")

    exported = export_beam_result_and_section_dxfs(
        _schedule(_beam(), _sample_design()),
        [_beam()],
        lambda _: {"width_mm": 400.0, "depth_mm": 800.0},
        tmp_path,
    )

    assert {path.name for path in exported.result_paths} == {"wpj-F1.dxf"}
    assert {path.name for path in exported.section_paths} == {"构件编号-F1.dxf"}
    assert not stale_result.exists()
    assert not stale_section.exists()
    assert unrelated.read_text(encoding="ascii") == "keep"


def test_failed_export_keeps_previous_dxf_families(monkeypatch, tmp_path: Path) -> None:
    stale_result = tmp_path / "wpj-OLD.dxf"
    stale_section = tmp_path / "构件编号-OLD.dxf"
    stale_result.write_text("old result", encoding="ascii")
    stale_section.write_text("old section", encoding="ascii")

    def fail_write(*args, **kwargs):
        raise OSError("simulated DXF write failure")

    monkeypatch.setattr(beam_result_dxf_module, "_write_dxf", fail_write)
    with pytest.raises(OSError, match="simulated DXF write failure"):
        export_beam_result_and_section_dxfs(
            _schedule(_beam(), _sample_design()),
            [_beam()],
            lambda _: {"width_mm": 400.0, "depth_mm": 800.0},
            tmp_path,
        )

    assert stale_result.read_text(encoding="ascii") == "old result"
    assert stale_section.read_text(encoding="ascii") == "old section"


def test_shared_tower_story_suffix_is_exported_once(tmp_path: Path) -> None:
    first = replace(_beam("B1"), story="T1-L1")
    second = replace(
        _beam("B2"),
        story="T2-L1",
        start=(0.0, 1000.0, 3000.0),
        end=(10000.0, 1000.0, 3000.0),
        path_points=((0.0, 1000.0, 3000.0), (10000.0, 1000.0, 3000.0)),
    )
    first_span = BeamSpan("T1-L1", "PB", 1, 1, ("B1",), first.path_points, False, False)
    second_span = BeamSpan("T2-L1", "PB", 1, 1, ("B2",), second.path_points, False, False)
    schedule = SpanScheduleMapping(
        (
            SpanScheduleRecord(first_span, first, _sample_design()),
            SpanScheduleRecord(second_span, second, _sample_design()),
        ),
        {},
        {},
    )
    exported = export_beam_result_and_section_dxfs(
        schedule,
        [first, second],
        lambda _: {"width_mm": 400.0, "depth_mm": 800.0},
        tmp_path,
    )
    assert [path.name for path in exported.result_paths] == ["wpj-L1.dxf"]
    assert [path.name for path in exported.section_paths] == ["构件编号-L1.dxf"]
    assert exported.result_paths[0].read_text(encoding="ascii").count("\nPOLYLINE\n") == 2
    assert exported.section_paths[0].read_text(encoding="ascii").count("\nPOLYLINE\n") == 2
    assert {item.story for item in exported.summaries} == {"L1"}


def test_tower_suffixes_and_ordinary_hyphen_stories_are_not_overmerged(tmp_path: Path) -> None:
    records = []
    beams = []
    for index, story in enumerate(("T1-L1", "T2-L2", "Wing-A"), start=1):
        beam = replace(
            _beam(f"B{index}"),
            story=story,
            start=(index * 1000.0, 0.0, 3000.0),
            end=(index * 1000.0 + 10000.0, 0.0, 3000.0),
            path_points=((index * 1000.0, 0.0, 3000.0), (index * 1000.0 + 10000.0, 0.0, 3000.0)),
        )
        span = BeamSpan(story, "PB", 1, 1, (beam.name,), beam.path_points, False, False)
        beams.append(beam)
        records.append(SpanScheduleRecord(span, beam, _sample_design()))
    exported = export_beam_result_and_section_dxfs(
        SpanScheduleMapping(tuple(records), {}, {}),
        beams,
        lambda _: {"width_mm": 400.0, "depth_mm": 800.0},
        tmp_path,
    )
    assert {path.name for path in exported.result_paths} == {"wpj-T1-L1.dxf", "wpj-T2-L2.dxf", "wpj-Wing-A.dxf"}
    assert {item.story for item in exported.summaries} == {"T1-L1", "T2-L2", "Wing-A"}


def test_text_height_controls_layout_spacing_and_rotation_remains_readable(tmp_path: Path) -> None:
    beam = _beam()
    schedule = _schedule(beam, _sample_design())
    for height in (25, 50, 75, 100):
        exported = export_beam_result_and_section_dxfs(
            schedule,
            [beam],
            lambda _: {"width_mm": 400.0, "depth_mm": 800.0},
            tmp_path / str(height),
            text_height=height,
        )
        text = exported.result_paths[0].read_text(encoding="ascii")
        assert f"40\n{height:.3f}" in text
        assert f"21\n{height * 2.25:.3f}" in text

    diagonal = BeamGeometry(
        "BD",
        "BD",
        "F3",
        "P1",
        "P2",
        (1000.0, 1000.0, 0.0),
        (0.0, 0.0, 0.0),
        "B400X800",
        800.0,
        path_points=((1000.0, 1000.0, 0.0), (0.0, 0.0, 0.0)),
    )
    diagonal_length = diagonal.length
    station = DesignStation(
        0.0,
        "",
        100.0,
        "",
        100.0,
        "",
        0.0,
        "",
        0.0,
        "",
        0.0,
        top_total_area=100.0,
        bottom_total_area=100.0,
    )
    last_station = DesignStation(
        diagonal_length,
        "",
        100.0,
        "",
        100.0,
        "",
        0.0,
        "",
        0.0,
        "",
        0.0,
        top_total_area=100.0,
        bottom_total_area=100.0,
    )
    diagonal_schedule = _schedule(diagonal, BeamDesignResult("F3-PB1-S1", (station, last_station)))
    diagonal_output = export_beam_result_and_section_dxfs(
        diagonal_schedule,
        [diagonal],
        lambda _: {"width_mm": 400.0, "depth_mm": 800.0},
        tmp_path / "diagonal",
    )
    diagonal_text = diagonal_output.result_paths[0].read_text(encoding="ascii")
    assert "50\n45.000" in diagonal_text
