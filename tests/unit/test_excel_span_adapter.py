from openpyxl import load_workbook

from src.core.beam_line_span_analyzer import analyze_beam_lines
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult, DesignStation
from src.excel_export import export_beam_schedule
from src.excel_span_adapter import build_span_schedule


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
