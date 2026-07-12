from pathlib import Path

from openpyxl import load_workbook

from src.core.continuity_analyzer import ContinuityGroup
from src.core.geometry_extractor import BeamGeometry
from src.design.provider import BeamDesignResult
from src.excel_export import export_beam_schedule


def test_export_creates_a_readable_workbook(tmp_path: Path) -> None:
    beam = BeamGeometry("B1", "B1", "L1", "P1", "P2", (0, 0, 0), (6, 0, 0), "B300x500")
    output = export_beam_schedule([beam], [ContinuityGroup(1, "L1", ("B1",))], {"B1": BeamDesignResult("B1")}, tmp_path)
    workbook = load_workbook(output)
    assert workbook.sheetnames == ["梁表", "设计站点", "导出说明"]
    assert workbook["梁表"].cell(2, 1).value == "B1"
