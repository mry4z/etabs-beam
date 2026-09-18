from pathlib import Path
from dataclasses import replace

import pytest

from src.core.beam_line_span_analyzer import BeamSpan
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation
import src.drawing.beam_result_dxf as beam_result_dxf_module
from src.drawing.beam_result_dxf import export_beam_result_and_section_dxfs
from src.excel_span_adapter import SpanScheduleMapping, SpanScheduleRecord


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
        "F1-PB1-1#1",
        "F1-PB1-1#2",
    }


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
    assert ("F1-PB1-1#1", "AVAILABLE") in statuses
    assert ("F1-PB1-1#2", "NO_RESULTS") in statuses
    assert result.count("GERR-ERR") == 1


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
