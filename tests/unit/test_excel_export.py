from pathlib import Path

from openpyxl import load_workbook

from src.core.continuity_analyzer import ContinuityGroup
from src.core.beam_line_span_analyzer import BeamSpan
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult
from src.design.provider import DesignResultStatus, DesignStation
from src.excel_export import export_beam_schedule
from src.excel_span_adapter import SpanScheduleMapping, SpanScheduleRecord


def test_export_creates_a_readable_workbook(tmp_path: Path) -> None:
    beam = BeamGeometry("B1", "B1", "L1", "P1", "P2", (0, 0, 0), (6, 0, 0), "B300x500")
    output = export_beam_schedule([beam], [ContinuityGroup(1, "L1", ("B1",))], {"B1": BeamDesignResult("B1")}, tmp_path)
    workbook = load_workbook(output)
    assert workbook.sheetnames == ["梁表", "设计站点", "导出说明", "异常记录"]
    assert workbook["梁表"].cell(2, 1).value == "B1"
    headers = [cell.value for cell in workbook["梁表"][1]]
    assert workbook["梁表"].cell(2, headers.index("设计结果状态") + 1).value == "有设计结果"


def test_export_includes_maximum_torsion_longitudinal_area(tmp_path: Path) -> None:
    beam = BeamGeometry("B1", "B1", "L1", "P1", "P2", (0, 0, 0), (6, 0, 0), "B300x500")
    stations = (
        DesignStation(0, "", 1, "", 2, "", 3, "", 4, "", 5),
        DesignStation(1, "", 1, "", 2, "", 3, "", 8, "", 5),
    )
    output = export_beam_schedule(
        [beam], [ContinuityGroup(1, "L1", ("B1",))], {"B1": BeamDesignResult("B1", stations)}, tmp_path
    )
    worksheet = load_workbook(output, read_only=True, data_only=True)["梁表"]
    headers = [cell.value for cell in worksheet[1]]
    assert worksheet.cell(2, headers.index("最大受扭纵筋面积（仅受扭需求）") + 1).value == 8


def test_export_beam_type_filter_writes_fixed_primary_and_secondary_paths(tmp_path: Path) -> None:
    pb = BeamGeometry("PB", "PB", "L1", "P1", "P2", (0, 0, 0), (6, 0, 0), "B300x500", path_points=((0, 0, 0), (6, 0, 0)))
    sb = BeamGeometry("SB", "SB", "L1", "P2", "P3", (6, 0, 0), (12, 0, 0), "B300x500", path_points=((6, 0, 0), (12, 0, 0)))
    pb_span = BeamSpan("L1", "PB", 1, 1, ("PB",), pb.path_points, False, False)
    sb_span = BeamSpan("L1", "SB", 2, 1, ("SB",), sb.path_points, False, False)
    schedule = SpanScheduleMapping(
        (
            SpanScheduleRecord(pb_span, pb, BeamDesignResult("PB")),
            SpanScheduleRecord(sb_span, sb, BeamDesignResult("SB")),
        ),
        {"PB": (pb_span.span_id,), "SB": (sb_span.span_id,)},
        {"PB": (pb_span.beam_line_id,), "SB": (sb_span.beam_line_id,)},
    )
    groups = [ContinuityGroup(1, "L1", ("PB",)), ContinuityGroup(2, "L1", ("SB",))]
    primary = export_beam_schedule(
        [pb, sb], groups, {"PB": BeamDesignResult("PB"), "SB": BeamDesignResult("SB")},
        tmp_path, span_schedule=schedule, beam_type="PB", output_path=tmp_path / "M_primarybeam_schedule.xlsx",
    )
    secondary = export_beam_schedule(
        [pb, sb], groups, {"PB": BeamDesignResult("PB"), "SB": BeamDesignResult("SB")},
        tmp_path, span_schedule=schedule, beam_type="SB", output_path=tmp_path / "M_secondarybeam_schedule.xlsx",
    )
    assert primary.name == "M_primarybeam_schedule.xlsx"
    assert secondary.name == "M_secondarybeam_schedule.xlsx"
    assert list(load_workbook(primary, read_only=True)["梁表"].iter_rows(min_row=2, values_only=True))[0][0] == "PB"
    assert list(load_workbook(secondary, read_only=True)["梁表"].iter_rows(min_row=2, values_only=True))[0][0] == "SB"


def test_basic_beam_schedule_hides_only_explicit_no_design_rows(tmp_path: Path) -> None:
    beams = [
        BeamGeometry("B1", "B1", "L1", "P1", "P2", (0, 0, 0), (6, 0, 0), "B300x500"),
        BeamGeometry("B2", "B2", "L1", "P2", "P3", (6, 0, 0), (12, 0, 0), "B300x500"),
        BeamGeometry("B3", "B3", "L1", "P3", "P4", (12, 0, 0), (18, 0, 0), "B300x500"),
    ]
    designs = {
        "B1": BeamDesignResult(
            "B1",
            status=DesignResultStatus.SKIPPED,
            status_message="EXCLUDED_NO_DESIGN; GetDesignProcedure=7 No Design",
        ),
        "B2": BeamDesignResult("B2", status=DesignResultStatus.NO_RESULTS, status_message="N.A."),
        "B3": BeamDesignResult(
            "B3", status=DesignResultStatus.API_ERROR, status_message="No Design API result was available"
        ),
    }

    output = export_beam_schedule(beams, [], designs, tmp_path)
    rows = list(load_workbook(output, read_only=True)["梁表"].iter_rows(min_row=2, values_only=True))

    assert [row[0] for row in rows] == ["B2", "B3"]
