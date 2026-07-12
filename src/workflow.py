"""Command-line workflow for exporting ETABS beam schedules."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.config import AppConfig, load_config
from src.core.continuity_analyzer import ContinuityAnalyzer
from src.core.etabs_connector import EtabsConnector
from src.core.geometry_extractor import GeometryExtractor
from src.design.etabs_result import EtabsResultProvider
from src.excel_export import export_beam_schedule
from src.utils.logger import configure_logger


def run(config: AppConfig, selection_mode: str | None = None) -> Path:
    logger = configure_logger(config.output.logs_dir, config.output.log_filename, config.log_level)
    connector = EtabsConnector(config.etabs.dll_path)
    sap = connector.attach()
    logger.info("已连接 ETABS：%s", connector.model_filename())

    from ETABSv1 import eUnits

    original_units = connector.present_units()
    logger.info("ETABS 原始当前单位：%s", _unit_name(original_units))
    target_unit = getattr(eUnits, config.geometry.export_unit)
    with connector.temporary_present_units(target_unit):
        logger.info("导出单位已固定为：%s", _unit_name(connector.present_units()))
        extractor = GeometryExtractor(sap)
        mode = selection_mode or config.beam_selection_mode
        beams = extractor.extract(mode)
        logger.info("已读取 %s 根梁（模式：%s）。", len(beams), mode)

        groups = ContinuityAnalyzer(
            config.geometry.coordinate_tolerance_mm,
            config.geometry.slope_limit_deg,
        ).analyze(beams)
        logger.info("已识别 %s 个连续梁组。", len(groups))

        provider = EtabsResultProvider(sap)
        designs = {}
        for beam in beams:
            try:
                designs[beam.name] = provider.get_beam_design(beam.name)
            except Exception as exc:
                logger.warning("梁 %s 未读取到设计结果：%s", beam.name, exc)

        output = export_beam_schedule(beams, groups, designs, config.output.reports_dir, config.output.report_filename)

    logger.info("ETABS 当前单位已恢复为：%s", _unit_name(connector.present_units()))
    logger.info("梁表已导出：%s", output)
    return output


def _unit_name(units: object) -> str:
    return units.ToString() if hasattr(units, "ToString") else str(units)


def main() -> int:
    parser = argparse.ArgumentParser(description="ETABS 梁表导出工具")
    parser.add_argument(
        "--config", type=Path, default=None, help="配置文件路径，默认使用 config.yaml 或 config.example.yaml"
    )
    parser.add_argument("--selection", choices=["all", "selected"], default=None, help="梁选择范围")
    arguments = parser.parse_args()
    output = run(load_config(arguments.config), arguments.selection)
    print(f"导出完成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
