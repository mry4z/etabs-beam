"""Read-only topology and workbook workflow driven by a SQLite snapshot."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.core.beam_line_span_analyzer import BeamLineSpanSettings, analyze_beam_lines
from src.core.continuity_analyzer import ContinuityAnalyzer
from src.core.model_writeback import build_writeback_plan, exclude_writeback_objects
from src.core.section_review import build_multi_section_span_records, write_multi_section_span_audit
from src.core.topology_audit import build_requires_split_records, write_requires_split_audit
from src.drawing.dxf_export import (
    export_requires_split_dxf,
    export_structural_spans_csv,
    export_structural_spans_dxf,
)
from src.drawing.beam_result_dxf import export_beam_result_and_section_dxfs
from src.excel_export import export_beam_schedule
from src.excel_span_adapter import build_span_schedule
from src.model_source import SqliteReadSource
from src.output_layout import model_name_from_path, resolve_output_layout
from src.rebar_workbook import (
    build_rebar_workbook_payload_from_domain,
    build_secondary_schedule_payload,
    run_secondary_workbook_builder,
    run_workbook_builder,
    write_rebar_payload,
)


def _scaled_progress(
    progress: Callable[[int, str], None] | None,
    start: int,
    end: int,
) -> Callable[[int, str], None] | None:
    if progress is None:
        return None

    def emit(percent: int, message: str) -> None:
        bounded = max(0, min(100, int(percent)))
        progress(start + round((end - start) * bounded / 100), message)

    return emit


def run_snapshot_workflow(
    args: Any,
    config: Any,
    output_dir: Path,
    *,
    progress: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """Run the production read-only consumers without attaching to ETABS."""

    _validate_snapshot_arguments(args)
    model_path = Path(args.expected_model).resolve()
    layout = resolve_output_layout(output_dir, model_path)
    output_dir = layout.root
    data_dir = layout.data
    source = SqliteReadSource(args.snapshot_input, model_path)
    source_hash = source.assert_current()
    source_identity = dict(source.identity())
    confirmed_section_spans = _load_string_set(
        args.confirmed_multi_section_spans,
        key="confirmed_span_ids",
        description="多截面确认",
    )
    excluded_writeback_objects = _load_string_set(
        args.exclude_writeback_objects,
        key="excluded_object_names",
        description="写回排除对象",
    )

    extraction_progress = _scaled_progress(progress, 0, 15)
    if extraction_progress is None:
        beams = source.extract_beams(args.selection)
    else:
        beams = source.extract_beams(
            args.selection,
            progress=extraction_progress,
        )
    if progress is not None:
        progress(15, f"读取几何完成（{len(beams)} 梁）")
    column_supports = source.column_supports()
    if progress is not None:
        progress(20, f"读取支承完成（{len(column_supports)} 个）")
    states = source.frame_states(beams)
    structural = analyze_beam_lines(
        beams,
        set(column_supports),
        BeamLineSpanSettings(
            coordinate_tolerance_mm=config.geometry.coordinate_tolerance_mm,
            source_span_overlap_tolerance_mm=config.geometry.source_span_overlap_tolerance_mm,
            merge_angle_deg=config.geometry.merge_angle_limit_deg,
            intersection_z_tolerance_mm=config.geometry.coordinate_tolerance_mm,
            story_workers=4,
        ),
        column_support_coordinates=column_supports,
    )
    if progress is not None:
        progress(35, f"拓扑划分完成（{len(structural.spans)} Span）")
    plan = build_writeback_plan(
        structural,
        beams,
        states,
        confirmed_multi_section_span_ids=confirmed_section_spans,
    )
    if excluded_writeback_objects:
        plan = exclude_writeback_objects(plan, excluded_writeback_objects)
    records = build_requires_split_records(structural, beams, states, plan)
    section_records = build_multi_section_span_records(
        structural,
        beams,
        confirmed_span_ids=confirmed_section_spans,
    )

    def span_id(span: Any) -> str:
        return f"{span.story}-{span.beam_type}{span.line_number}-{span.span_index}"

    spans_csv = export_structural_spans_csv(
        structural.spans,
        data_dir / "topology_spans.csv",
        span_id_resolver=span_id,
    )
    spans_dxf = export_structural_spans_dxf(
        structural.spans,
        layout.span_note,
        model_name=layout.model_name,
        span_id_resolver=span_id,
    )
    split_json, split_csv = write_requires_split_audit(records, data_dir, "requires_split_audit")
    split_dxf = export_requires_split_dxf(
        records,
        data_dir / "requires_split_audit.dxf",
        model_name=layout.model_name,
    )
    section_json, section_csv = write_multi_section_span_audit(section_records, data_dir)
    diagnostics_json = data_dir / "topology_diagnostics.json"
    diagnostics_json.write_text(
        json.dumps(
            [asdict(diagnostic) for diagnostic in structural.diagnostics],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if progress is not None:
        progress(50, "拓扑 DXF/审计导出完成")

    outputs = {
        "spans_csv": str(spans_csv),
        "spans_dxf": str(spans_dxf),
        "requires_split_json": str(split_json),
        "requires_split_csv": str(split_csv),
        "requires_split_dxf": str(split_dxf),
        "multi_section_review_json": str(section_json),
        "multi_section_review_csv": str(section_csv),
        "topology_diagnostics_json": str(diagnostics_json),
    }
    designs = None
    beam_dxf_export = None
    if args.export_excel or args.export_rebar_workbook or getattr(args, "export_beam_result_dxf", False):
        design_progress = _scaled_progress(progress, 55, 70)
        if design_progress is None:
            designs = source.designs(beams)
        else:
            designs = source.designs(
                beams,
                progress=design_progress,
            )
        if progress is not None:
            progress(71, f"读取设计结果完成（{len(designs)} 梁）")
        span_schedule = build_span_schedule(
            structural,
            beams,
            designs,
            config.geometry.coordinate_tolerance_mm,
        )
        if progress is not None:
            progress(73, f"Span schedule 完成（{len(span_schedule.records)} 跨）")
        if getattr(args, "export_beam_result_dxf", False):
            beam_dxf_export = export_beam_result_and_section_dxfs(
                span_schedule,
                beams,
                source.section_info,
                output_dir,
                text_height=float(getattr(args, "dxf_text_height", 50)),
            )
            if progress is not None:
                progress(82, "梁计算结果/截面 DXF 导出完成")
            outputs["beam_result_dxf"] = [str(path) for path in beam_dxf_export.result_paths]
            outputs["beam_section_dxf"] = [str(path) for path in beam_dxf_export.section_paths]
        if args.export_excel:
            groups = ContinuityAnalyzer(
                config.geometry.coordinate_tolerance_mm,
                config.geometry.slope_limit_deg,
            ).analyze(beams)
            outputs["excel"] = str(
                export_beam_schedule(
                    list(beams),
                    groups,
                    designs,
                    data_dir,
                    "basic_primarybeam_schedule_{timestamp}.xlsx",
                    span_schedule=span_schedule,
                    beam_type="PB",
                )
            )
            outputs["excel_primary"] = outputs["excel"]
            outputs["excel_secondary"] = str(
                export_beam_schedule(
                    list(beams),
                    groups,
                    designs,
                    data_dir,
                    "basic_secondarybeam_schedule_{timestamp}.xlsx",
                    span_schedule=span_schedule,
                    beam_type="SB",
                )
            )
            if progress is not None:
                progress(86, "Excel 梁表导出完成")
        if args.export_rebar_workbook:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            payload_path = data_dir / f"rebar_workbook_payload_{stamp}.json"
            workbook_path = layout.primary_schedule
            preview_dir = data_dir / f"rebar_workbook_previews_{stamp}"
            secondary_payload_path = data_dir / f"secondary_beam_payload_{stamp}.json"
            secondary_workbook_path = layout.secondary_schedule
            secondary_preview_dir = data_dir / f"secondary_beam_previews_{stamp}"
            payload = build_rebar_workbook_payload_from_domain(
                structural,
                beams,
                designs,
                section_loader=source.section_info,
                frame_types=source.frame_types(),
                model_path=model_path,
                design_code=source.design_code(),
                tolerance_mm=config.geometry.coordinate_tolerance_mm,
                clear_cover_to_stirrup_outer_mm=config.design.clear_cover_to_stirrup_outer_mm,
                clear_cover_source=config.design.clear_cover_source,
                max_aggregate_size_mm=config.design.max_aggregate_size_mm,
                max_aggregate_size_source=config.design.max_aggregate_size_source,
            )
            write_rebar_payload(payload, payload_path)
            write_rebar_payload(
                build_secondary_schedule_payload(payload),
                secondary_payload_path,
            )
            source.assert_current()
            run_workbook_builder(
                payload_path,
                workbook_path,
                preview_dir=preview_dir,
                node_executable=args.artifact_node,
                node_modules=args.artifact_node_modules,
                node_max_old_space_mb=args.artifact_node_max_old_space_mb,
                builder=getattr(args, "workbook_builder", "python"),
            )
            run_secondary_workbook_builder(
                secondary_payload_path,
                secondary_workbook_path,
                preview_dir=secondary_preview_dir,
                node_executable=args.artifact_node,
                node_modules=args.artifact_node_modules,
                node_max_old_space_mb=args.artifact_node_max_old_space_mb,
                builder=getattr(args, "workbook_builder", "python"),
            )
            if progress is not None:
                progress(92, "配筋表输入文件导出完成")
            outputs.update(
                {
                    "rebar_payload_json": str(payload_path),
                    "rebar_workbook": str(workbook_path),
                    "rebar_workbook_previews": str(preview_dir),
                    "secondary_rebar_payload_json": str(secondary_payload_path),
                    "secondary_rebar_workbook": str(secondary_workbook_path),
                    "secondary_rebar_workbook_previews": str(secondary_preview_dir),
                }
            )

    end_hash = source.assert_current()
    if end_hash != source_hash:
        raise RuntimeError(f"SNAPSHOT_SOURCE_MODEL_CHANGED_DURING_RUN: expected {source_hash}, got {end_hash}")
    blocking_diagnostics = tuple(
        diagnostic for diagnostic in structural.diagnostics if diagnostic.severity == "BLOCKED"
    )
    split_actions = tuple(action for action in plan.renames if action.status == "REQUIRES_SPLIT")
    section_review_actions = tuple(action for action in plan.renames if action.status == "REQUIRES_SECTION_REVIEW")
    metadata = source.reader.metadata()
    summary = {
        "status": "OK",
        "mode": "read-only-snapshot",
        "model": str(model_path),
        "model_input_sha256": source_hash,
        "original_units": "snapshot",
        "export_units": "N_mm_C",
        "design_standard": source.design_code(),
        "data_source": source_identity,
        "environment": {
            "etabs_version": {
                "available": True,
                "supported": str(metadata.get("etabs_version", "")).split(".", 1)[0] in {"22", "23"},
                "version": metadata.get("etabs_version"),
            },
            "api": {
                "file_version": metadata.get("api_version"),
                "sha256": metadata.get("api_sha256"),
            },
            "pid": None,
        },
        "beams": len(beams),
        "beam_lines": len(structural.lines),
        "spans": len(structural.spans),
        "primary_spans": sum(span.beam_type == "PB" for span in structural.spans),
        "secondary_spans": sum(span.beam_type == "SB" for span in structural.spans),
        "requires_split_objects": len(records),
        "requires_split_actions": len(split_actions),
        "multi_section_spans": len(section_records),
        "unconfirmed_multi_section_spans": sum(
            record.status == "REQUIRES_SECTION_REVIEW" for record in section_records
        ),
        "section_review_actions": len(section_review_actions),
        "confirmed_multi_section_spans": sorted(confirmed_section_spans),
        "excluded_writeback_objects": sorted(excluded_writeback_objects),
        "output_directory": str(output_dir),
        "data_directory": str(data_dir),
        "outputs": outputs,
        "blocking_topology_diagnostics": len(blocking_diagnostics),
        "record_count": len(structural.records),
        "record_sample": list(structural.records[:5]),
        "design_result_beams": 0 if designs is None else len(designs),
    }
    if beam_dxf_export is not None:
        summary["beam_result_dxf_issues"] = [asdict(issue) for issue in beam_dxf_export.issues]
        summary["beam_result_dxf_summary"] = [asdict(item) for item in beam_dxf_export.summaries]
    return summary


def _validate_snapshot_arguments(args: Any) -> None:
    if args.expected_model is None:
        raise ValueError("--snapshot-input 要求显式提供 --expected-model。")
    if args.pid is not None:
        raise ValueError("--snapshot-input 与 --pid 不能同时使用。")
    if args.selection != "all":
        raise ValueError("SQLite 快照不保存 ETABS 当前选择；--selection 必须为 all。")
    forbidden = {
        "--writeback": args.writeback,
        "--prepare-section-review-groups": args.prepare_section_review_groups,
        "--select-section-review-group": args.select_section_review_group,
        "--run-analysis": args.run_analysis,
        "--run-design": args.run_design,
        "--verify-full-writeback": args.verify_full_writeback,
        "--verify-demand-equivalence": args.verify_demand_equivalence,
    }
    selected = [name for name, value in forbidden.items() if value]
    if selected:
        raise ValueError(f"SQLite 快照只支持只读工作流，禁止选项：{', '.join(selected)}")


def _load_string_set(
    path: Path | None,
    *,
    key: str,
    description: str,
) -> set[str]:
    if path is None:
        return set()
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict):
        payload = payload.get(key)
    if not isinstance(payload, list) or not all(isinstance(value, str) and value.strip() for value in payload):
        raise ValueError(f"{description}文件必须是字符串数组，或含 {key} 的对象。")
    return {value.strip() for value in payload}
