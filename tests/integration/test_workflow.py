"""Opt-in ETABS integration test.

Run only with an already open ETABS instance and explicit permission:
ETABS_INTEGRATION=1 python -m pytest tests/integration -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from openpyxl import load_workbook

from src.config import load_config
from src.core.continuity_analyzer import ContinuityAnalyzer
from src.core.etabs_connector import EtabsConnector
from src.core.geometry_extractor import GeometryExtractor
from src.design.etabs_result import EtabsResultProvider
from src.excel_export import export_beam_schedule

pytestmark = pytest.mark.skipif(
    os.getenv("ETABS_INTEGRATION") != "1",
    reason="需要已打开的 ETABS；设置 ETABS_INTEGRATION=1 后显式运行。",
)


def test_read_and_export_open_etabs_model(tmp_path: Path) -> None:
    config = load_config()
    connector = EtabsConnector(config.etabs.dll_path)
    sap = connector.attach()

    from ETABSv1 import eUnits

    with connector.temporary_present_units(eUnits.N_mm_C):
        beams = GeometryExtractor(sap).extract("all")
        groups = ContinuityAnalyzer(
            config.geometry.coordinate_tolerance_mm,
            config.geometry.slope_limit_deg,
        ).analyze(beams)
        designs = {beam.name: EtabsResultProvider(sap).get_beam_design(beam.name) for beam in beams}
        output = export_beam_schedule(beams, groups, designs, tmp_path)

    workbook = load_workbook(output, read_only=True, data_only=True)
    assert len(beams) > 0
    assert sum(len(result.stations) for result in designs.values()) > 0
    assert workbook["梁表"].max_row - 1 == len(beams)
    assert workbook["设计站点"].max_row - 1 == sum(len(result.stations) for result in designs.values())
    assert workbook["导出说明"]["B2"].value.startswith("N_mm_C")

    headers = [cell.value for cell in workbook["设计站点"][1]]
    rows_by_beam: dict[str, list[tuple[object, ...]]] = {}
    for row in workbook["设计站点"].iter_rows(min_row=2, values_only=True):
        rows_by_beam.setdefault(str(row[0]), []).append(row)
    for beam in (beams[0], beams[len(beams) // 2], beams[-1]):
        expected = designs[beam.name].stations
        exported = rows_by_beam[beam.name]
        for index in (0, len(expected) // 2, len(expected) - 1):
            station = expected[index]
            row = exported[index]
            assert row[headers.index("位置")] == pytest.approx(station.location)
            assert row[headers.index("顶筋面积")] == pytest.approx(station.top_area)
            assert row[headers.index("底筋面积")] == pytest.approx(station.bottom_area)
            assert row[headers.index("剪力筋 Av/s")] == pytest.approx(station.shear_area)
