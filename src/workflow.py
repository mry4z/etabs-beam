"""Command-line workflow for exporting ETABS beam schedules."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.config import AppConfig, load_config
from src.core.beam_merger import BeamMerger, MergeRecord
from src.core.continuity_analyzer import ContinuityAnalyzer
from src.core.etabs_connector import EtabsConnector
from src.core.geometry_extractor import GeometryExtractor
from src.core.beam_line_span_analyzer import BeamLineSpanSettings, analyze_beam_lines
from src.design.etabs_result import EtabsResultProvider
from src.design.provider import BeamDesignResult, DesignResultStatus
from src.excel_export import export_beam_schedule
from src.excel_span_adapter import build_span_schedule
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
        raw_beams = list(beams)
        column_supports = extractor.column_supports()
        column_anchor_names = extractor.column_anchor_names()
        rigid_links = extractor.rigid_links_for_beams(raw_beams)
        logger.info("已读取 %s 根梁（模式：%s）。", len(beams), mode)

        provider = EtabsResultProvider(sap)
        raw_designs = {}
        special_records: list[MergeRecord] = []
        excluded_design_names: set[str] = set()
        for beam in beams:
            if beam.depth is None or beam.section.strip().casefold() in {"", "none", "null"}:
                raw_designs[beam.name] = BeamDesignResult(
                    beam.name,
                    status=DesignResultStatus.SKIPPED,
                    status_message="非混凝土或无有效混凝土截面。",
                )
                excluded_design_names.add(beam.name)
                logger.info("梁 %s 截面 %s 不属于当前混凝土设计统计范围，跳过设计结果读取。", beam.name, beam.section)
                continue
            try:
                raw_designs[beam.name] = provider.get_beam_design(beam.name)
                if not raw_designs[beam.name].has_results:
                    special_records.append(
                        MergeRecord(
                            "警告",
                            "设计结果",
                            beam.name,
                            raw_designs[beam.name].status_message or "ETABS 未返回混凝土梁设计站点。",
                        )
                    )
            except Exception as exc:
                logger.warning("梁 %s 未读取到设计结果：%s", beam.name, exc)
                raw_designs[beam.name] = BeamDesignResult(
                    beam.name,
                    status=DesignResultStatus.API_ERROR,
                    status_message=str(exc),
                )
                special_records.append(MergeRecord("警告", "设计结果", beam.name, str(exc)))

        structural_result = analyze_beam_lines(
            raw_beams,
            set(column_supports),
            BeamLineSpanSettings(
                coordinate_tolerance_mm=config.geometry.coordinate_tolerance_mm,
                source_span_overlap_tolerance_mm=(config.geometry.source_span_overlap_tolerance_mm),
                merge_angle_deg=config.geometry.merge_angle_limit_deg,
                story_workers=4,
            ),
            column_support_coordinates=column_supports,
            column_anchor_names=column_anchor_names,
            rigid_links=rigid_links,
        )
        span_schedule = build_span_schedule(
            structural_result,
            raw_beams,
            raw_designs,
            config.geometry.coordinate_tolerance_mm,
        )
        special_records.extend(MergeRecord("信息", "结构分跨", "", message) for message in structural_result.records)
        logger.info(
            "结构分跨分析完成：%s 条梁线、%s 跨（PB=%s，SB=%s）。",
            len(structural_result.lines),
            len(structural_result.spans),
            sum(span.beam_type == "PB" for span in structural_result.spans),
            sum(span.beam_type == "SB" for span in structural_result.spans),
        )

        merge_result = BeamMerger(
            config.geometry.coordinate_tolerance_mm,
            config.geometry.merge_angle_limit_deg,
        ).merge(beams, raw_designs, excluded_design_names)
        beams = merge_result.beams
        designs = merge_result.designs
        special_records.extend(merge_result.records)
        logger.info("逻辑梁合并后剩余 %s 根梁。", len(beams))

        groups = ContinuityAnalyzer(
            config.geometry.coordinate_tolerance_mm,
            config.geometry.slope_limit_deg,
        ).analyze(beams)
        logger.info("已识别 %s 个连续梁组。", len(groups))

        output = export_beam_schedule(
            beams,
            groups,
            designs,
            config.output.reports_dir,
            config.output.report_filename,
            special_records,
            span_schedule,
        )

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
