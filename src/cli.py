"""Formal ETABS 22/23 topology/writeback command-line entry point.

The command intentionally makes read-only the default.  It writes audit
artifacts for every run and refuses an unsafe writeback unless
``--allow-partial-writeback`` is explicit.  Mixed-section structural Spans are
held for a separate, explicit user review decision.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import load_config
from src.etabs_locate import search_etabs_dll_path
from src.core.beam_line_span_analyzer import BeamLineSpanSettings, analyze_beam_lines
from src.core.continuity_analyzer import ContinuityAnalyzer
from src.core.etabs_connector import EtabsConnector, etabs_version_info
from src.core.geometry_extractor import GeometryExtractor
from src.core.model_writeback import (
    EtabsWriteback,
    WritebackExecutionError,
    build_writeback_plan,
    compare_design_envelopes,
    compare_frame_audits,
    exclude_writeback_objects,
    write_frame_audit,
)
from src.core.section_review import (
    build_multi_section_span_records,
    create_etabs_section_review_groups,
    select_etabs_review_group,
    write_multi_section_span_audit,
)
from src.core.topology_audit import build_requires_split_records, write_requires_split_audit
from src.drawing.dxf_export import export_requires_split_dxf, export_structural_spans_csv, export_structural_spans_dxf
from src.design.etabs_result import EtabsResultProvider
from src.design.provider import BeamDesignResult, DesignResultStatus
from src.excel_export import export_beam_schedule
from src.excel_span_adapter import build_span_schedule
from src.model_source import EtabsOapiReadSource
from src.rebar_workbook import (
    build_rebar_workbook_payload_from_domain,
    build_secondary_schedule_payload,
    run_secondary_workbook_builder,
    run_workbook_builder,
    write_rebar_payload,
)
from src.run_manifest import RunManifest, artifact_record, resolve_model_file, sha256_file


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        summary = _runtime_self_test(args) if args.runtime_self_test else run(args)
    except Exception as exc:  # CLI boundary: preserve a concise machine-readable error.
        print(json.dumps({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _ensure_etabs_dll(config: Any, explicit: Any) -> Any:
    """Resolve the ETABS API DLL path for a standalone CLI run.

    Priority: explicit ``--etabs-api`` > ``ETABS_API_PATH`` env > config value >
    automatic search of well-known install roots.  The plugin always passes the
    host assembly path explicitly, so this only affects direct CLI / portable
    workflow runs.
    """
    if explicit is not None:
        return config
    env_path = os.environ.get("ETABS_API_PATH")
    if env_path:
        candidate = Path(env_path)
        if candidate.is_file():
            return replace(config, etabs=replace(config.etabs, dll_path=candidate.resolve()))
    if config.etabs.dll_path.is_file():
        return config
    found = search_etabs_dll_path()
    if found is None:
        raise FileNotFoundError(
            "未找到 ETABS API DLL。已搜索常见安装目录；"
            "请安装 ETABS 22/23、设置 ETABS_API_PATH 环境变量，"
            "或用 --etabs-api 显式指定 ETABSv1.dll。"
        )
    return replace(config, etabs=replace(config.etabs, dll_path=found))


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Run the workflow and always leave a success or failure manifest."""

    config = load_config(args.config, etabs_dll_path=args.etabs_api)
    config = _ensure_etabs_dll(config, args.etabs_api)
    output_dir = Path(args.output_dir or config.output.reports_dir / "topology_run")
    output_dir.mkdir(parents=True, exist_ok=True)
    registry_dir = Path(args.manifest_registry or output_dir.parent / "manifests")
    manifest = RunManifest(
        output_dir=output_dir,
        registry_dir=registry_dir,
        config=config,
        arguments=vars(args),
    )
    try:
        summary = _run_impl(args, config, output_dir)
    except Exception as exc:
        manifest.finalize(
            status="FAILED",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    manifest_path = manifest.finalize(status="SUCCESS", summary=summary)
    summary["run_id"] = manifest.run_id
    summary["outputs"]["manifest"] = str(manifest_path)
    (output_dir / "topology_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _run_impl(args: argparse.Namespace, config: Any, output_dir: Path) -> dict[str, Any]:
    if args.snapshot_input is not None:
        from src.snapshot.workflow import run_snapshot_workflow

        return run_snapshot_workflow(args, config, output_dir)
    if args.pid is None:
        raise ValueError("ETABS OAPI 工作流要求 --pid；离线工作流应提供 --snapshot-input。")
    if args.allow_partial_writeback and not args.writeback:
        raise ValueError("--allow-partial-writeback 只能与 --writeback 一起使用。")
    if (args.verify_full_writeback or args.verify_demand_equivalence) and not args.writeback:
        raise ValueError("写回验证选项只能与 --writeback 一起使用。")
    if args.verify_demand_equivalence and not (args.run_analysis and args.run_design):
        raise ValueError("需求等价验证必须同时指定 --run-analysis 和 --run-design。")
    if (
        args.writeback
        and (args.export_excel or args.export_rebar_workbook)
        and not (args.run_analysis and args.run_design)
    ):
        raise ValueError(
            "写回后导出设计结果必须同时指定 --run-analysis 和 --run-design，" "禁止导出写回前的旧设计结果。"
        )
    sap, actual_model = _attach(args.pid, config.etabs.dll_path)
    durable_model = resolve_model_file(actual_model)
    model_input_sha256 = sha256_file(durable_model) if durable_model else None
    environment = {
        "etabs_version": _etabs_version(sap),
        "api": _api_environment(config.etabs.dll_path),
        "pid": args.pid,
    }
    expected = Path(args.expected_model).resolve() if args.expected_model else None
    if expected and not _same_model_path(actual_model, expected):
        raise RuntimeError(f"attached model mismatch: {actual_model} != {expected}")
    if args.select_section_review_group:
        select_etabs_review_group(sap, args.select_section_review_group)
        return {
            "status": "OK",
            "mode": "select-section-review-group",
            "model": str(actual_model),
            "selected_group": args.select_section_review_group,
        }
    initially_locked = bool(sap.GetModelIsLocked())
    if args.prepare_section_review_groups and initially_locked:
        raise RuntimeError("模型处于锁定状态，拒绝创建审查 Group。")
    if args.writeback and initially_locked and not args.verify_demand_equivalence:
        raise RuntimeError("模型处于锁定状态；仅需求等价验证流程可先读取基线结果后自动解锁。")

    confirmed_section_spans = _load_confirmed_multi_section_spans(args.confirmed_multi_section_spans)

    connector = EtabsConnector(config.etabs.dll_path)
    connector.sap_model = sap
    from ETABSv1 import eUnits

    original_units = str(sap.GetPresentUnits())
    with connector.temporary_present_units(eUnits.N_mm_C):
        read_source = EtabsOapiReadSource(
            sap,
            actual_model,
            durable_model_path=durable_model,
            expected_model_sha256=model_input_sha256,
        )
        beams = read_source.extract_beams(args.selection)
        column_supports = read_source.column_supports()
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
        writeback = EtabsWriteback(sap)
        states = read_source.frame_states(beams)
        plan = build_writeback_plan(
            structural,
            beams,
            states,
            confirmed_multi_section_span_ids=confirmed_section_spans,
        )
        excluded_writeback_objects = _load_string_set(
            args.exclude_writeback_objects,
            key="excluded_object_names",
            description="写回排除对象",
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
            return f"{span.story}-{span.beam_type}" f"{span.line_number}-{span.span_index}"

        spans_csv = export_structural_spans_csv(
            structural.spans,
            output_dir / "topology_spans.csv",
            span_id_resolver=span_id,
        )
        spans_dxf = export_structural_spans_dxf(
            structural.spans,
            output_dir / "topology_spans.dxf",
            model_name=str(actual_model),
            span_id_resolver=span_id,
        )
        split_json, split_csv = write_requires_split_audit(records, output_dir, "requires_split_audit")
        split_dxf = export_requires_split_dxf(
            records,
            output_dir / "requires_split_audit.dxf",
            model_name=str(actual_model),
        )
        section_json, section_csv = write_multi_section_span_audit(
            section_records,
            output_dir,
        )
        diagnostics_json = output_dir / "topology_diagnostics.json"
        diagnostics_json.write_text(
            json.dumps(
                [asdict(diagnostic) for diagnostic in structural.diagnostics],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        excel_output = None
        rebar_payload_path = None
        rebar_workbook_output = None
        rebar_preview_dir = None
        secondary_rebar_payload_path = None
        secondary_rebar_workbook_output = None
        secondary_rebar_preview_dir = None

        blocked = tuple(action for action in plan.renames if action.status != "READY")
        split_actions = tuple(action for action in plan.renames if action.status == "REQUIRES_SPLIT")
        section_review_actions = tuple(action for action in plan.renames if action.status == "REQUIRES_SECTION_REVIEW")
        blocking_diagnostics = tuple(
            diagnostic for diagnostic in structural.diagnostics if diagnostic.severity == "BLOCKED"
        )
        review_groups = ()
        full_audit_path = None
        demand_audit_path = None
        full_audit_comparison = None
        demand_comparison = None
        ready = ()
        resolver: dict[str, str] = {}
        orientation_applied = False
        rename_applied = False
        if args.prepare_section_review_groups:
            review_groups = create_etabs_section_review_groups(
                sap,
                section_records,
                allow_mutation=True,
            )
            orientation = rename = after_rename = None
            analysis_status = design_status = save_status = None
        elif args.writeback:
            if blocking_diagnostics:
                codes = ", ".join(sorted({diagnostic.code for diagnostic in blocking_diagnostics}))
                raise RuntimeError(
                    "写回门禁拒绝：存在 "
                    f"{len(blocking_diagnostics)} 个拓扑歧义诊断（{codes}）；"
                    "必须先通过人工拓扑 override 消除歧义。"
                )
            if blocked and not args.allow_partial_writeback:
                raise RuntimeError(
                    f"写回门禁拒绝：存在 {len({action.name for action in blocked})} 个阻塞 Object；"
                    f"其中跨 Object {len({action.name for action in split_actions})} 个、"
                    f"多截面待确认 {len({action.span_id for action in section_review_actions})} 跨；"
                    "如确认只写回安全对象，必须显式指定 --allow-partial-writeback。"
                )
            ready = tuple(action for action in plan.renames if action.status == "READY")
            resolver = {action.name: action.final_name for action in ready}
            before_audit = writeback.snapshot_audit(states) if args.verify_full_writeback else None
            before_designs = None
            if args.verify_demand_equivalence:
                if not sap.DesignConcrete.GetResultsAvailable():
                    raise RuntimeError("需求等价验证要求写回前已有混凝土设计结果。")
                before_designs = _read_designs(sap, tuple(beams))
            if bool(sap.GetModelIsLocked()):
                _status_code(sap.SetModelIsLocked(False))
            analysis_status = design_status = None
            try:
                orientation = writeback.apply_orientation(plan.orientations, allow_mutation=True)
                if not orientation.ok:
                    raise WritebackExecutionError("方向写后验证失败：" + "; ".join(orientation.failures[:10]))
                orientation_applied = True
                rename = writeback.apply_rename(ready, allow_mutation=True) if ready else None
                if rename is not None and not rename.ok:
                    raise WritebackExecutionError("名称写后验证失败：" + "; ".join(rename.failures[:10]))
                rename_applied = True
                after_rename = writeback.verify_orientation(plan.orientations, name_resolver=resolver)
                if not after_rename.ok:
                    raise WritebackExecutionError("改名后方向验证失败：" + "; ".join(after_rename.failures[:10]))
                if args.run_analysis:
                    analysis_status = _status_code(sap.Analyze.RunAnalysis())
                if args.run_design:
                    design_status = _status_code(sap.DesignConcrete.StartDesign())
                current_names = tuple(resolver.get(name, name) for name in states)
                if before_audit is not None:
                    after_audit = writeback.snapshot_audit(current_names)
                    reversed_guids = {
                        action.guid for action in plan.orientations if action.status == "READY" and action.reverse
                    }
                    full_audit_comparison = compare_frame_audits(
                        before_audit,
                        after_audit,
                        renamed=resolver,
                        reversed_guids=reversed_guids,
                    )
                    full_audit_path = write_frame_audit(
                        output_dir / "writeback_full_attribute_audit.json",
                        before_audit,
                        after_audit,
                        full_audit_comparison,
                    )
                    if not full_audit_comparison.ok:
                        raise WritebackExecutionError(
                            "全属性写前/写后比较失败：" + "; ".join(full_audit_comparison.failures[:10])
                        )
                if before_designs is not None:
                    current_beams = GeometryExtractor(sap).extract(args.selection)
                    after_designs = _read_designs(sap, tuple(current_beams))
                    demand_comparison = compare_design_envelopes(
                        before_designs,
                        after_designs,
                        renamed=resolver,
                    )
                    demand_audit_path = output_dir / "design_demand_equivalence.json"
                    demand_audit_path.write_text(
                        json.dumps(asdict(demand_comparison), ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    if not demand_comparison.ok:
                        raise WritebackExecutionError(
                            "方向反转前后需求包络不等价：" + "; ".join(demand_comparison.failures[:10])
                        )
            except Exception as exc:
                rollback = _rollback_transaction(
                    sap,
                    writeback,
                    plan.orientations if orientation_applied else (),
                    ready if rename_applied else (),
                )
                if not rollback.ok:
                    raise WritebackExecutionError(
                        "写回事务失败且回滚失败；必须放弃当前副本：" + "; ".join(rollback.failures)
                    ) from exc
                raise
            # Save only after the temporary export-unit context exits. ETABS
            # persists the current display units in the EDB; saving while
            # N_mm_C is active would silently change the user's model units.
            save_status = None
        else:
            orientation = rename = after_rename = None
            analysis_status = design_status = save_status = None

        if args.export_excel or args.export_rebar_workbook:
            if not sap.DesignConcrete.GetResultsAvailable():
                raise RuntimeError("设计结果导出要求 ETABS 已存在当前模型状态的混凝土设计结果。")
            if args.writeback:
                export_source = EtabsOapiReadSource(
                    sap,
                    actual_model,
                    durable_model_path=durable_model,
                )
                export_beams = export_source.extract_beams(args.selection)
                export_supports = export_source.column_supports()
                export_structural = analyze_beam_lines(
                    export_beams,
                    set(export_supports),
                    BeamLineSpanSettings(
                        coordinate_tolerance_mm=(config.geometry.coordinate_tolerance_mm),
                        source_span_overlap_tolerance_mm=(config.geometry.source_span_overlap_tolerance_mm),
                        merge_angle_deg=config.geometry.merge_angle_limit_deg,
                        intersection_z_tolerance_mm=(config.geometry.coordinate_tolerance_mm),
                        story_workers=4,
                    ),
                    column_support_coordinates=export_supports,
                )
            else:
                export_source = read_source
                export_beams = beams
                export_structural = structural
            designs = export_source.designs(export_beams)
            span_schedule = build_span_schedule(
                export_structural,
                export_beams,
                designs,
                config.geometry.coordinate_tolerance_mm,
            )
            if args.export_excel:
                groups = ContinuityAnalyzer(
                    config.geometry.coordinate_tolerance_mm,
                    config.geometry.slope_limit_deg,
                ).analyze(export_beams)
                excel_output = export_beam_schedule(
                    list(export_beams),
                    groups,
                    designs,
                    output_dir,
                    "beam_schedule_{timestamp}.xlsx",
                    span_schedule=span_schedule,
                )
            if args.export_rebar_workbook:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                rebar_payload_path = output_dir / f"rebar_workbook_payload_{stamp}.json"
                rebar_workbook_output = output_dir / f"beam_rebar_review_{stamp}.xlsx"
                rebar_preview_dir = output_dir / f"rebar_workbook_previews_{stamp}"
                secondary_rebar_payload_path = output_dir / f"secondary_beam_payload_{stamp}.json"
                secondary_rebar_workbook_output = output_dir / f"secondary_beam_schedule_{stamp}.xlsx"
                secondary_rebar_preview_dir = output_dir / f"secondary_beam_previews_{stamp}"
                payload = build_rebar_workbook_payload_from_domain(
                    export_structural,
                    export_beams,
                    designs,
                    section_loader=export_source.section_info,
                    frame_types=export_source.frame_types(),
                    model_path=actual_model,
                    design_code=export_source.design_code(),
                    tolerance_mm=config.geometry.coordinate_tolerance_mm,
                    clear_cover_to_stirrup_outer_mm=(config.design.clear_cover_to_stirrup_outer_mm),
                    clear_cover_source=config.design.clear_cover_source,
                    max_aggregate_size_mm=config.design.max_aggregate_size_mm,
                    max_aggregate_size_source=(config.design.max_aggregate_size_source),
                )
                write_rebar_payload(payload, rebar_payload_path)
                write_rebar_payload(
                    build_secondary_schedule_payload(payload),
                    secondary_rebar_payload_path,
                )

    if not args.writeback and not args.prepare_section_review_groups:
        data_source_identity = dict(read_source.identity())
    else:
        data_source_identity = {
            "kind": "etabs-oapi",
            "model_path": str(actual_model),
            "durable_model_path": str(durable_model) if durable_model else None,
            "source_model_sha256_before_mutation": model_input_sha256,
        }
    summary = {
        "status": "OK",
        "mode": (
            "writeback"
            if args.writeback
            else "prepare-section-review-groups" if args.prepare_section_review_groups else "read-only"
        ),
        "model": str(actual_model),
        "model_input_sha256": model_input_sha256,
        "original_units": original_units,
        "export_units": "N_mm_C",
        "design_standard": config.design.standard,
        "data_source": data_source_identity,
        "environment": environment,
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
        "outputs": {
            "spans_csv": str(spans_csv),
            "spans_dxf": str(spans_dxf),
            "requires_split_json": str(split_json),
            "requires_split_csv": str(split_csv),
            "requires_split_dxf": str(split_dxf),
            "multi_section_review_json": str(section_json),
            "multi_section_review_csv": str(section_csv),
            "topology_diagnostics_json": str(diagnostics_json),
        },
        "blocking_topology_diagnostics": len(blocking_diagnostics),
        "record_count": len(structural.records),
        "record_sample": list(structural.records[:5]),
    }
    if args.writeback:
        save_status = _save_writeback_or_rollback(
            sap,
            writeback,
            plan.orientations if orientation_applied else (),
            ready if rename_applied else (),
        )
        summary["writeback"] = {
            "orientation_ok": bool(orientation and orientation.ok),
            "rename_ok": bool(rename is None or rename.ok),
            "orientation_after_rename_ok": bool(after_rename is None or after_rename.ok),
            "analysis_status": analysis_status,
            "design_status": design_status,
            "save_status": save_status,
            "full_attribute_audit_ok": (None if full_audit_comparison is None else full_audit_comparison.ok),
            "demand_equivalence_ok": (None if demand_comparison is None else demand_comparison.ok),
        }
        if full_audit_path is not None:
            summary["outputs"]["writeback_full_attribute_audit"] = str(full_audit_path)
        if demand_audit_path is not None:
            summary["outputs"]["design_demand_equivalence"] = str(demand_audit_path)
    elif args.prepare_section_review_groups:
        save_status = _status_code(sap.File.Save())
        summary["section_review_groups"] = {
            "created": list(review_groups),
            "save_status": save_status,
        }
    if rebar_payload_path is not None:
        run_workbook_builder(
            rebar_payload_path,
            rebar_workbook_output,
            preview_dir=rebar_preview_dir,
            node_executable=args.artifact_node,
            node_modules=args.artifact_node_modules,
            node_max_old_space_mb=args.artifact_node_max_old_space_mb,
            builder=getattr(args, "workbook_builder", "python"),
        )
        summary["outputs"]["rebar_payload_json"] = str(rebar_payload_path)
        summary["outputs"]["rebar_workbook"] = str(rebar_workbook_output)
        summary["outputs"]["rebar_workbook_previews"] = str(rebar_preview_dir)
        run_secondary_workbook_builder(
            secondary_rebar_payload_path,
            secondary_rebar_workbook_output,
            preview_dir=secondary_rebar_preview_dir,
            node_executable=args.artifact_node,
            node_modules=args.artifact_node_modules,
            node_max_old_space_mb=args.artifact_node_max_old_space_mb,
            builder=getattr(args, "workbook_builder", "python"),
        )
        summary["outputs"]["secondary_rebar_payload_json"] = str(secondary_rebar_payload_path)
        summary["outputs"]["secondary_rebar_workbook"] = str(secondary_rebar_workbook_output)
        summary["outputs"]["secondary_rebar_workbook_previews"] = str(secondary_rebar_preview_dir)
    if excel_output is not None:
        summary["outputs"]["excel"] = str(excel_output)
    return summary


def _etabs_version(sap: Any) -> dict[str, Any]:
    """Return ETABS 22/23 compatibility metadata for the run manifest."""

    return etabs_version_info(sap)


def _api_environment(dll_path: Path) -> dict[str, Any]:
    """Record the API assembly identity using both file version and content hash."""

    record = artifact_record(dll_path)
    try:
        from System.Diagnostics import FileVersionInfo

        info = FileVersionInfo.GetVersionInfo(str(dll_path))
        record["file_version"] = str(info.FileVersion)
        record["product_version"] = str(info.ProductVersion)
    except Exception as exc:
        record["version_error"] = f"{type(exc).__name__}: {exc}"
    return record


def _runtime_self_test(args: argparse.Namespace) -> dict[str, Any]:
    """Verify the packaged runtime and ETABS API bridge without attaching to a model."""

    if args.etabs_api is None:
        found = search_etabs_dll_path()
        if found is None:
            raise ValueError("--runtime-self-test requires --etabs-api (或设置 ETABS_API_PATH / 自动搜索安装目录).")
        args.etabs_api = found
    api_path = Path(args.etabs_api).resolve()
    if not api_path.is_file():
        raise FileNotFoundError(f"ETABS API DLL not found: {api_path}")
    config = load_config(args.config, etabs_dll_path=api_path)

    import clr
    import openpyxl
    import pandas
    import yaml

    clr.AddReference(str(config.etabs.dll_path))
    from ETABSv1 import cHelper, cPluginContract

    return {
        "status": "OK",
        "mode": "runtime-self-test",
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "architecture": platform.architecture()[0],
        "python": platform.python_version(),
        "pandas": pandas.__version__,
        "openpyxl": openpyxl.__version__,
        "pyyaml": yaml.__version__,
        "etabs_api": _api_environment(config.etabs.dll_path),
        "etabs_types": [cHelper.__name__, cPluginContract.__name__],
    }


def _attach(pid: int, dll_path: Path) -> tuple[Any, Path]:
    import clr

    clr.AddReference(str(dll_path))
    from ETABSv1 import Helper, cHelper

    app = cHelper(Helper()).GetObjectProcess("CSI.ETABS.API.ETABSObject", int(pid))
    if app is None:
        raise RuntimeError(f"ETABS PID {pid} unavailable")
    sap = app.SapModel
    return sap, Path(str(sap.GetModelFilename(True))).resolve()


def _same_model_path(actual: Path, expected: Path) -> bool:
    """Treat ETABS' transient ``.$et`` path as the matching EDB model.

    During/after a save ETABS 23 can report the lock/working sidecar
    ``model.$et`` through ``GetModelFilename`` while the user-facing model is
    ``model.EDB``.  Both are accepted only when they share the same directory
    and stem; a different model is still rejected.
    """

    return actual.parent == expected.parent and actual.stem.casefold() == expected.stem.casefold()


def _status_code(result: Any) -> int:
    values = tuple(result) if isinstance(result, tuple) else (result,)
    if not values:
        raise RuntimeError("ETABS API 未返回状态码")
    code = int(values[0])
    if code != 0:
        raise RuntimeError(f"ETABS API 返回错误码 {code}")
    return code


def _rollback_transaction(
    sap: Any,
    writeback: EtabsWriteback,
    orientations: Any,
    renames: Any,
) -> Any:
    """Unlock a failed analysis state and roll back completed model changes."""

    try:
        if bool(sap.GetModelIsLocked()):
            _status_code(sap.SetModelIsLocked(False))
    except Exception:
        pass
    return writeback.rollback_completed(
        orientations,
        renames,
        allow_mutation=True,
    )


def _save_writeback_or_rollback(
    sap: Any,
    writeback: EtabsWriteback,
    orientations: Any,
    renames: Any,
) -> int:
    """Save a completed transaction or restore and persist the pre-write state."""

    try:
        return _status_code(sap.File.Save())
    except Exception as exc:
        rollback = _rollback_transaction(sap, writeback, orientations, renames)
        if not rollback.ok:
            raise WritebackExecutionError(
                "保存失败且回滚失败；必须放弃当前副本：" + "; ".join(rollback.failures)
            ) from exc
        try:
            _status_code(sap.File.Save())
        except Exception as rollback_save_exc:
            raise WritebackExecutionError(f"保存失败；内存状态已回滚，但回滚状态保存失败：{rollback_save_exc}") from exc
        raise WritebackExecutionError("保存失败；写回事务已回滚并保存。") from exc


def _load_confirmed_multi_section_spans(path: Path | None) -> set[str]:
    """Load an explicit user confirmation list without coupling the Excel flow.

    Accepted JSON forms are either ``["F1-PB1-1"]`` or
    ``{"confirmed_span_ids": ["F1-PB1-1"]}``.
    """

    return _load_string_set(
        path,
        key="confirmed_span_ids",
        description="多截面确认",
    )


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


def _read_designs(sap: Any, beams: tuple[Any, ...]) -> dict[str, BeamDesignResult]:
    """Read the ETABS 22/23 total-rebar contract for the current object set."""

    provider = EtabsResultProvider(sap)
    designs: dict[str, BeamDesignResult] = {}
    for beam in beams:
        if beam.depth is None or str(beam.section).strip().casefold() in {"", "none", "null"}:
            designs[beam.name] = BeamDesignResult(
                beam.name,
                status=DesignResultStatus.SKIPPED,
                status_message="非混凝土或无有效截面。",
            )
            continue
        try:
            designs[beam.name] = provider.get_beam_design_total(beam.name)
        except Exception as exc:
            designs[beam.name] = BeamDesignResult(
                beam.name,
                status=DesignResultStatus.API_ERROR,
                status_message=str(exc),
            )
    return designs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ETABS 22/23 梁拓扑、诊断和安全写回工作流")
    parser.add_argument("--pid", type=int, help="已运行 ETABS 进程 PID；与 --snapshot-input 二选一")
    parser.add_argument(
        "--snapshot-input",
        type=Path,
        help="项目自建 SQLite 快照；启用不附着 ETABS 的只读工作流",
    )
    parser.add_argument("--expected-model", type=Path, help="期望当前打开的 EDB 路径")
    parser.add_argument(
        "--etabs-api",
        type=Path,
        help="覆盖 config 中的 ETABSv1.dll；便携插件使用当前宿主程序集的绝对路径",
    )
    parser.add_argument(
        "--source-model",
        type=Path,
        help="工作副本对应的未修改源 EDB；用于 manifest 源/副本哈希",
    )
    parser.add_argument("--config", type=Path, help="配置文件路径")
    parser.add_argument(
        "--runtime-self-test",
        action="store_true",
        help="验证便携 Python/依赖/ETABS API 装载后退出，不附着或修改模型",
    )
    parser.add_argument("--selection", choices=["all", "selected"], default="all")
    parser.add_argument("--output-dir", type=Path, help="本次运行输出目录")
    parser.add_argument(
        "--manifest-registry",
        type=Path,
        help="跨运行 manifest 注册目录；默认使用输出目录同级 manifests/",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--read-only", action="store_true", help="只读模式（默认）")
    mode.add_argument("--writeback", action="store_true", help="执行方向/编号写回")
    mode.add_argument(
        "--prepare-section-review-groups",
        action="store_true",
        help="为多截面跨创建彩色 ETABS Group 并保存，供人工复核",
    )
    mode.add_argument(
        "--select-section-review-group",
        metavar="GROUP",
        help="选中/高亮一个已创建的多截面审查 Group",
    )
    parser.add_argument("--allow-partial-writeback", action="store_true")
    parser.add_argument(
        "--confirmed-multi-section-spans",
        type=Path,
        help="已人工确认可作为 step beam 归为一跨的 SpanID JSON 文件",
    )
    parser.add_argument(
        "--exclude-writeback-objects",
        type=Path,
        help="因需求等价或专项审查而禁止方向/编号写回的 Object 名称 JSON",
    )
    parser.add_argument("--run-analysis", action="store_true", help="写回后运行分析")
    parser.add_argument("--run-design", action="store_true", help="写回后运行混凝土设计")
    parser.add_argument(
        "--verify-full-writeback",
        action="store_true",
        help="比较写前/写后 GUID、I/J、矩阵、截面、材料、释放、偏移、荷载、组、铰、弹簧和设计覆盖项",
    )
    parser.add_argument(
        "--verify-demand-equivalence",
        action="store_true",
        help="重新分析设计后比较写回前后纵筋、剪力和扭转需求包络",
    )
    parser.add_argument(
        "--export-excel",
        action="store_true",
        help="在当前已存在设计结果时导出统一梁表 Excel（使用 GetSummaryResultsBeam_2）",
    )
    parser.add_argument(
        "--export-rebar-workbook",
        action="store_true",
        help="导出三表配筋评审 Excel，并同时导出独立 SB 梁表 Excel",
    )
    parser.add_argument(
        "--workbook-builder",
        choices=["python", "node"],
        default="python",
        help="工作簿生成器：python（openpyxl，便携默认，无需 node）或 node（artifact-tool 对照基线）",
    )
    parser.add_argument(
        "--artifact-node",
        default="node",
        help="运行正式 artifact-tool 工作簿生成器的 Node.js 可执行文件（仅 --workbook-builder node 生效）",
    )
    parser.add_argument(
        "--artifact-node-modules",
        type=Path,
        help="可选 artifact-tool node_modules；提供时在临时目录创建只读链接",
    )
    parser.add_argument(
        "--artifact-node-max-old-space-mb",
        type=int,
        default=8192,
        help="大型工作簿生成时 Node.js 最大堆内存，默认 8192 MB",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
