from openpyxl import load_workbook
from dataclasses import replace

from src.core.beam_line_span_analyzer import (
    BeamLine,
    BeamLineSpanResult,
    BeamLineSpanSettings,
    BeamSpan,
    analyze_beam_lines,
)
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation
from src.design.region_results import DRAWING_DETAILING_POLICY_ID, V3_DETAILING_POLICY_ID
from src.drawing.dxf_export import export_structural_spans_dxf
from src.excel_export import export_beam_schedule
from src.excel_span_adapter import build_span_schedule, section_runs, span_object_part_paths


def _four_object_chain(*, reverse_second: bool = False):
    xs = (176969.84375, 179822.734375, 182669.84375, 184756.84375, 186569.84375)
    y, z = 43743.394531, -50.0
    sections = (
        "B54209_Rect300X900_C25",
        "B92503_Rect300X950_C25",
        "B54209_Rect300X900_C25",
        "B92503_Rect300X950_C25",
    )
    beams = []
    designs = {}
    for index, (name, left, right, section) in enumerate(
        zip(("645", "646", "647", "648"), xs, xs[1:], sections)
    ):
        reverse = reverse_second and index == 1
        start_x, end_x = (right, left) if reverse else (left, right)
        start, end = (start_x, y, z), (end_x, y, z)
        beam = BeamGeometry(
            name,
            name,
            "Story1",
            f"P{index + 1}" if not reverse else f"P{index + 2}",
            f"P{index + 2}" if not reverse else f"P{index + 1}",
            start,
            end,
            section,
            900.0 if "900" in section else 950.0,
            path_points=(start, end),
        )
        beams.append(beam)
        beam_length = abs(right - left)
        designs[name] = BeamDesignResult(
            name,
            tuple(
                DesignStation(
                    location,
                    "T",
                    1000.0 + index * 100.0 + station_index,
                    "B",
                    1200.0 + index * 100.0 + station_index,
                    "V",
                    0.35,
                    "TL",
                    10.0,
                    "TV",
                    0.08,
                    top_total_area=1000.0 + index * 100.0 + station_index,
                    bottom_total_area=1200.0 + index * 100.0 + station_index,
                    source_object=name,
                )
                for station_index, location in enumerate((0.0, beam_length / 2.0, beam_length))
            ),
            source_objects=(name,),
        )
    return tuple(beams), designs, tuple((x, y, z) for x in xs)


def test_span_adapter_clips_design_and_preserves_source_mapping(tmp_path) -> None:
    beam = BeamGeometry("B1", "B1", "F1", "C1", "C2", (0, 0, 0), (10, 0, 0), "B", 500)
    design = BeamDesignResult(
        "B1",
        (
            DesignStation(0, "T0", 1, "B0", 2, "V0", 3, "TL0", 4, "TV0", 5),
            DesignStation(5, "T5", 6, "B5", 7, "V5", 8, "TL5", 9, "TV5", 10),
            DesignStation(10, "T10", 11, "B10", 12, "V10", 13, "TL10", 14, "TV10", 15),
        ),
    )
    result = analyze_beam_lines([beam], {"C1", "C2"})
    mapping = build_span_schedule(result, [beam], {"B1": design})
    assert len(mapping.records) == 1
    record = mapping.records[0]
    assert record.span.span_id == "F1-PB1-S1"
    assert [station.location for station in record.design.stations] == [0, 5, 10]
    assert mapping.source_to_span_ids["B1"] == ("F1-PB1-S1",)

    output = export_beam_schedule([beam], [], {"B1": design}, tmp_path, span_schedule=mapping)
    workbook = load_workbook(output, read_only=True, data_only=True)
    assert "梁分跨" in workbook.sheetnames
    headers = [cell.value for cell in workbook["梁分跨"][1]]
    assert "梁编号" in headers
    assert workbook["梁分跨"].cell(2, headers.index("梁编号") + 1).value == "F1-PB1-S1"


def test_drawing_schedule_preserves_duplicate_stations_and_existing_clip_tolerance() -> None:
    beam = BeamGeometry("B1", "B1", "F1", "C1", "C2", (0, 0, 0), (10, 0, 0), "B", 500)
    design = BeamDesignResult(
        "B1",
        tuple(
            DesignStation(
                location,
                "T",
                100.0,
                "B",
                100.0,
                "V",
                0.1,
                "TL",
                1.0,
                "TV",
                0.01,
            )
            for location in (0.0, 5.0, 5.2, 10.0, 10.02)
        ),
    )
    structural = analyze_beam_lines([beam], {"C1", "C2"})

    legacy = build_span_schedule(structural, [beam], {"B1": design})
    drawing = build_span_schedule(
        structural,
        [beam],
        {"B1": design},
        detailing_policy_id=DRAWING_DETAILING_POLICY_ID,
    )
    drawing_v3 = build_span_schedule(
        structural,
        [beam],
        {"B1": design},
        detailing_policy_id=V3_DETAILING_POLICY_ID,
    )

    assert [item.location for item in legacy.records[0].design.stations] == [0.0, 5.0, 10.0]
    assert [item.location for item in drawing.records[0].design.stations] == [0.0, 5.0, 5.2, 10.0, 10.0]
    assert drawing_v3.detailing_policy_id == V3_DETAILING_POLICY_ID
    assert [item.location for item in drawing_v3.records[0].design.stations] == [0.0, 5.0, 5.2, 10.0, 10.0]


def test_four_mixed_section_objects_keep_one_parent_and_canonical_part_mapping() -> None:
    beams, designs, points = _four_object_chain(reverse_second=True)
    structural = analyze_beam_lines(
        list(beams),
        {"P1", "P5"},
        BeamLineSpanSettings(story_workers=1),
    )
    assert len(structural.spans) == 1
    assert structural.spans[0].source_objects == ("645", "646", "647", "648")
    assert structural.spans[0].path_points == points

    line = structural.lines[0]
    span = structural.spans[0]
    line = replace(line, beam_type="SB", line_number=41)
    span = replace(span, beam_type="SB", line_number=41, span_index=2)
    delivery = build_span_schedule(
        BeamLineSpanResult((line,), (span,), structural.records, structural.diagnostics),
        list(beams),
        designs,
    )
    record = delivery.records[0]

    assert len(delivery.records) == 1
    assert record.object_parts[0].parent_span_id == "Story1-SB41-2"
    assert [part.object_part_id for part in record.object_parts] == [
        "Story1-SB41-2.1",
        "Story1-SB41-2.2",
        "Story1-SB41-2.3",
        "Story1-SB41-2.4",
    ]
    assert delivery.source_to_object_part_ids["648"] == ("Story1-SB41-2.4",)
    assert [part.geometry.section for part in record.object_parts] == [
        "B54209_Rect300X900_C25",
        "B92503_Rect300X950_C25",
        "B54209_Rect300X900_C25",
        "B92503_Rect300X950_C25",
    ]
    assert [round(part.geometry.length, 3) for part in record.object_parts] == [
        2852.891,
        2847.109,
        2087.0,
        1813.0,
    ]
    for source, part in zip(("645", "646", "647", "648"), record.object_parts):
        assert part.design.source_objects == (source,)
        assert all(station.source_object == source for station in part.design.stations)
        assert all(-1e-6 <= station.location <= part.geometry.length + 1e-6 for station in part.design.stations)
    # Object 646 has reversed I/J; its station order is rebased to canonical parent I→J.
    assert record.object_parts[1].design.stations[0].top_total_area == 1102.0
    assert record.object_parts[1].design.stations[-1].top_total_area == 1100.0


def test_equal_section_source_objects_still_map_to_parts_without_expanding_parent() -> None:
    first = BeamGeometry("A", "A", "F1", "P1", "P2", (0, 0, 0), (5, 0, 0), "SAME")
    second = BeamGeometry("B", "B", "F1", "P2", "P3", (5, 0, 0), (10, 0, 0), "SAME")
    span = BeamSpan("F1", "PB", 1, 1, ("A", "B"), ((0, 0, 0), (5, 0, 0), (10, 0, 0)), False, False)
    line = BeamLine("F1", "PB", 1, "SAME", ("A", "B"), span.path_points, False)
    design = BeamDesignResult("parent", ())
    schedule = build_span_schedule(
        BeamLineSpanResult((line,), (span,), (), ()),
        [first, second],
        {"A": design, "B": design},
    )

    assert len(schedule.records) == 1
    assert [part.object_part_id for part in schedule.records[0].object_parts] == ["F1-PB1-1.1", "F1-PB1-1.2"]
    assert schedule.source_to_object_part_ids == {"A": ("F1-PB1-1.1",), "B": ("F1-PB1-1.2",)}


def test_section_runs_follow_reversed_parent_and_preserve_nonadjacent_sections() -> None:
    beams, designs, points = _four_object_chain(reverse_second=True)
    beams = tuple(replace(beam, section=section) for beam, section in zip(beams, ("A", "B", "A", "A")))
    span = BeamSpan("Story1", "PB", 1, 1, ("645", "646", "647", "648"), tuple(reversed(points)), False, False)
    line = BeamLine("Story1", "PB", 1, "A", span.source_objects, points, False)
    schedule = build_span_schedule(BeamLineSpanResult((line,), (span,), (), ()), beams, designs,
                                   detailing_policy_id=DRAWING_DETAILING_POLICY_ID)
    runs = section_runs(schedule.records[0].object_parts)
    assert [run.geometry.source_names for run in runs] == [("648", "647"), ("646",), ("645",)]
    assert [run.section for run in runs] == ["A", "B", "A"]
    assert [run.object_part_index for run in runs] == [1, 2, 3]
    assert runs[0].is_span_start and runs[-1].is_span_end
    assert runs[0].design.stations[0].source_object == "648"
    assert runs[0].design.stations[0].top_total_area == 1302.0
    assert runs[-1].design.stations[-1].top_total_area == 1000.0
    assert schedule.source_to_object_part_ids["647"] == (runs[0].object_part_id,)


def test_span_note_parts_hide_only_explicitly_excluded_sources_without_renumbering(tmp_path) -> None:
    beams, designs, points = _four_object_chain()
    designs["646"] = BeamDesignResult(
        "646",
        status=DesignResultStatus.SKIPPED,
        status_message="EXCLUDED_NO_DESIGN; GetDesignProcedure=7 No Design",
    )
    designs["647"] = BeamDesignResult(
        "647",
        status=DesignResultStatus.API_ERROR,
        status_message="No Design API result was available",
    )
    structural = analyze_beam_lines(list(beams), {"P1", "P5"}, BeamLineSpanSettings(story_workers=1))
    schedule = build_span_schedule(structural, list(beams), designs)

    labels = span_object_part_paths(schedule, {"646"})["Story1-PB1-1"]

    assert [part_id for part_id, _path in labels] == [
        "Story1-PB1-1.1",
        "Story1-PB1-1.3",
        "Story1-PB1-1.4",
    ]
    output = export_structural_spans_dxf(
        structural.spans,
        tmp_path / "mixed-no-design.dxf",
        object_part_labels={"Story1-PB1-1": labels},
        excluded_source_names={"646"},
    )
    content = output.read_text(encoding="ascii")

    assert "Story1-PB1-1" in content
    assert "Story1-PB1-1.1" in content
    assert "Story1-PB1-1.2" not in content
    assert "Story1-PB1-1.3" in content
