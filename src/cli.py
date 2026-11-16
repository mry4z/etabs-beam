"""Formal ETABS 22/23 topology/writeback command-line entry point.

The command intentionally makes read-only the default.  It writes audit
artifacts for every run and refuses an unsafe writeback unless
``--allow-partial-writeback`` is explicit.  Mixed-section structural Spans are
held for a separate, explicit user review decision.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import platform
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.config import load_config, parse_rebar_workers
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
from src.core.tower_assignment import compact_tower_assignment_summary
from src.drawing.beam_result_dxf import export_beam_result_and_section_dxfs
from src.drawing.dxf_export import export_requires_split_dxf, export_structural_spans_csv, export_structural_spans_dxf
from src.design.etabs_result import EtabsResultProvider
from src.design.detailing import make_fresh_detailing_engine
from src.design.offline_style import (
    DRAWING_POLICY_REVISION_R7_ID,
    DRAWING_POLICY_REVISION_R8_ID,
    DRAWING_POLICY_REVISION_R9_ID,
    DRAWING_V3_POLICY_ID,
    DRAWING_V3_REVISION_ID,
    default_runtime_rules_path,
)
from src.design.provider import BeamDesignResult, DesignResultStatus
from src.design.region_results import (
    DRAWING_DETAILING_POLICY_ID,
    LEGACY_DETAILING_POLICY_ID,
    SUPPORTED_DETAILING_POLICY_IDS,
)
from src.design.no_design import excluded_no_design_source_names
from src.excel_export import export_beam_schedule
from src.excel_span_adapter import build_span_schedule, span_object_part_paths
from src.model_source import EtabsOapiReadSource
from src.output_layout import model_name_from_path, resolve_output_layout
from src.rebar_workbook import (
    build_rebar_workbook_payload_from_domain,
    build_secondary_schedule_payload,
    run_secondary_workbook_builder,
    run_workbook_builder,
    write_rebar_payload,
    redetail_payload,
)
from src.run_manifest import RunManifest, artifact_record, resolve_model_file, sha256_file
from src.tower_preflight import run_tower_preflight, validate_tower_options


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.allow_policy_revision_change and (
            args.replay_payload is None or args.detailing_policy is None
        ):
            raise ValueError(
                "--allow-policy-revision-change requires --replay-payload and explicit --detailing-policy"
            )
        if args.replay_payload is not None:
            summary = _replay_payload(args)
        else:
            summary = _runtime_self_test(args) if args.runtime_self_test else run(args)
    except Exception as exc:  # CLI boundary: preserve a concise machine-readable error.
        print(json.dumps({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(_display_summary(summary), ensure_ascii=False), flush=True)
    return 0


def _tower_progress(percent: int, message: str) -> None:
    """Render a small phase bar; per-object audit remains in the report file."""

    width = 24
    bounded = max(0, min(100, int(percent)))
    filled = round(width * bounded / 100)
    if filled >= width:
        bar = "=" * width
    elif filled <= 0:
        bar = ">" + "-" * (width - 1)
    else:
        bar = "=" * (filled - 1) + ">" + "-" * (width - filled)
    print(f"[tower] [{bar}] {bounded:3d}% {message}", flush=True)


def _beam_progress(percent: int, message: str) -> None:
    """Render throttled ordinary-workflow phase progress.

    The callback may be invoked from per-object ETABS reads.  Keeping the
    last rendered integer here makes those reads visible without producing a
    line for every beam and prevents a later phase from moving the bar back.
    """

    state = getattr(_beam_progress, "_state", None)
    if state is None:
        state = {"percent": -1}
        _beam_progress._state = state
    bounded = max(0, min(100, int(percent)))
    if bounded < state["percent"] or bounded == state["percent"]:
        return
    state["percent"] = bounded
    width = 24
    filled = round(width * bounded / 100)
    if filled >= width:
        bar = "=" * width
    elif filled <= 0:
        bar = ">" + "-" * (width - 1)
    else:
        bar = "=" * (filled - 1) + ">" + "-" * (width - filled)
    print(f"[beam] [{bar}] {bounded:3d}% {message}", flush=True)


def _scaled_progress(
    progress: Callable[[int, str], None] | None,
    start: int,
    end: int,
) -> Callable[[int, str], None] | None:
    """Map a source-local 0..100 progress callback into one workflow phase."""

    if progress is None:
        return None

    def emit(percent: int, message: str) -> None:
        bounded = max(0, min(100, int(percent)))
        progress(start + round((end - start) * bounded / 100), message)

    return emit


def _compact_workflow_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Keep the successful ordinary CLI line useful without dumping audits."""

    dxf_issues = summary.get("beam_result_dxf_issues") or ()
    error_count = len(dxf_issues) + int(summary.get("blocking_topology_diagnostics", 0) or 0)
    compact: dict[str, Any] = {
        "status": summary.get("status"),
        "mode": summary.get("mode"),
        "beams": summary.get("beams", 0),
        "spans": summary.get("spans", 0),
        "errors": error_count,
        "outputs": summary.get("outputs") or {},
    }
    for key in (
        "run_id",
        "detailing_policy_id",
        "detailing_policy_revision_id",
        "beam_lines",
        "design_result_beams",
        "requires_split_actions",
        "unconfirmed_multi_section_spans",
        "optimization_incomplete_count",
        "rebar_parallelism",
        "rebar_dispatch_seconds",
    ):
        if key in summary:
            compact[key] = summary[key]
    if dxf_issues:
        compact["beam_result_dxf_errors"] = len(dxf_issues)
    return compact


def _display_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Replace the stdout Tower payload with a compact UI-safe summary."""

    displayed = dict(summary)
    tower = displayed.get("tower_assignment")
    if isinstance(tower, dict):
        outputs = displayed.get("outputs") or {}
        displayed["tower_assignment"] = compact_tower_assignment_summary(
            tower,
            report_path=outputs.get("tower_assignment_json"),
        )
        return displayed
    if summary.get("mode") in {
        "read-only",
        "writeback",
        "read-only-snapshot",
        "prepare-section-review-groups",
    }:
        return _compact_workflow_summary(summary)
    return displayed


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

    config = load_config(
        args.config,
        etabs_dll_path=args.etabs_api,
        detailing_policy_id=getattr(args, "detailing_policy", None),
        layout_dictionary_path=getattr(args, "layout_dictionary", None),
        rebar_workers=getattr(args, "rebar_workers", None),
        candidate_lookup=getattr(args, "candidate_lookup", None),
    )
    config = _ensure_etabs_dll(config, args.etabs_api)
    model_hint = getattr(args, "expected_model", None) or getattr(args, "source_model", None) or "model"
    layout = resolve_output_layout(
        args.output_dir or config.output.reports_dir,
        model_hint,
    )
    output_dir = layout.root
    data_dir = layout.data
    registry_dir = Path(args.manifest_registry or data_dir / "manifests")
    manifest = RunManifest(
        output_dir=data_dir,
        registry_dir=registry_dir,
        config=config,
        arguments=vars(args),
    )
    try:
        tower_requested = any(
            getattr(args, key, False)
            for key in ("tower_check", "tower_preflight_only", "tower_create_groups")
        )
        if not tower_requested:
            _beam_progress._state = {"percent": -1}
        progress = _tower_progress if tower_requested else _beam_progress
        summary = (
            _run_impl(args, config, output_dir, progress=progress)
        )
    except Exception as exc:
        manifest.finalize(
            status="FAILED",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    manifest_path = manifest.finalize(status="SUCCESS", summary=summary)
    summary["run_id"] = manifest.run_id
    summary["outputs"]["manifest"] = str(manifest_path)
    summary.setdefault("outputs", {})["topology_summary"] = str(data_dir / "topology_summary.json")
    (data_dir / "topology_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not tower_requested:
        _beam_progress(100, "写审计/完成")
    return summary


def _run_impl(
    args: argparse.Namespace,
    config: Any,
    output_dir: Path,
    *,
    progress: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    validate_tower_options(args)
    detailing_policy_id = (
        getattr(args, "detailing_policy", None) or config.design.detailing_policy
    )
    if getattr(args, "v3_coverage_schedule", False):
        if args.snapshot_input is None:
            raise ValueError("--v3-coverage-schedule requires --snapshot-input")
        if detailing_policy_id != DRAWING_V3_POLICY_ID:
            raise ValueError("--v3-coverage-schedule requires --detailing-policy drawing_v3")
        if not getattr(args, "export_rebar_workbook", False):
            raise ValueError("--v3-coverage-schedule requires --export-rebar-workbook")
    if detailing_policy_id == DRAWING_V3_POLICY_ID:
        detailing_policy_revision_id = DRAWING_V3_REVISION_ID
    elif detailing_policy_id == DRAWING_DETAILING_POLICY_ID:
        detailing_policy_revision_id = (
            DRAWING_POLICY_REVISION_R9_ID
            if config.design.candidate_lookup == "auto"
            else DRAWING_POLICY_REVISION_R8_ID
        )
    else:
        detailing_policy_revision_id = LEGACY_DETAILING_POLICY_ID
    data_dir = output_dir / "_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    # Tower preflight owns the complete 0..100 progress range.  Do not feed
    # its renderer into the ordinary geometry/design phases before the Tower
    # reader starts, otherwise the bar would visibly jump back to 0%.
    phase_progress = None if progress is _tower_progress else progress
    if args.snapshot_input is not None:
        from src.snapshot.workflow import run_snapshot_workflow

        if phase_progress is not None:
            phase_progress(0, "读取 SQLite 快照")
        return run_snapshot_workflow(args, config, output_dir, progress=phase_progress)
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
        and (
            args.export_excel
            or args.export_rebar_workbook
            or getattr(args, "export_beam_result_dxf", False)
        )
        and not (args.run_analysis and args.run_design)
    ):
        raise ValueError(
            "写回后导出设计结果必须同时指定 --run-analysis 和 --run-design，" "禁止导出写回前的旧设计结果。"
        )
    if phase_progress is not None:
        phase_progress(0, "附加 ETABS")
    sap, actual_model = _attach(args.pid, config.etabs.dll_path)
    if phase_progress is not None:
        phase_progress(5, "ETABS 已附加，读取模型身份")
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
        extraction_progress = _scaled_progress(phase_progress, 5, 20)
        if extraction_progress is None:
            beams = read_source.extract_beams(args.selection)
        else:
            beams = read_source.extract_beams(
                args.selection,
                progress=extraction_progress,
            )
        design_exclusions = tuple(
            getattr(read_source, "design_object_exclusions", lambda: ())()
        )
        excluded_no_design_names = excluded_no_design_source_names(design_exclusions)
        if phase_progress is not None:
            phase_progress(20, f"读取几何完成（{len(beams)} 梁）")
        tower_result = run_tower_preflight(args, read_source, beams, data_dir, progress=progress)
        if tower_result is not None and (args.tower_preflight_only or args.tower_create_groups):
            return {
                "status": "OK",
                "mode": "tower-create-groups" if args.tower_create_groups else "tower-preflight",
                "model": str(actual_model), "model_input_sha256": model_input_sha256,
                "environment": environment, "original_units": original_units, "export_units": "N_mm_C",
                **tower_result,
            }
        column_supports = read_source.column_supports()
        column_anchor_names = getattr(read_source, "column_anchor_names", lambda: set())()
        rigid_links = getattr(read_source, "rigid_links_for_beams", lambda _beams: ())(beams)
        column_outlines = read_source.column_outlines()
        if phase_progress is not None:
            phase_progress(23, f"读取支承完成（{len(column_supports)} 个）")
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
            column_anchor_names=column_anchor_names,
            rigid_links=rigid_links,
        )
        if phase_progress is not None:
            phase_progress(35, f"拓扑划分完成（{len(structural.spans)} Span）")
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
        if phase_progress is not None:
            phase_progress(42, "拓扑审计记录已构建")

        def span_id(span: Any) -> str:
            return f"{span.story}-{span.beam_type}" f"{span.line_number}-{span.span_index}"

        spans_csv = export_structural_spans_csv(
            structural.spans,
            data_dir / "topology_spans.csv",
            span_id_resolver=span_id,
        )
        numbering_schedule = build_span_schedule(
            structural,
            beams,
            {},
            config.geometry.coordinate_tolerance_mm,
            detailing_policy_id=detailing_policy_id,
        )
        spans_dxf = export_structural_spans_dxf(
            structural.spans,
            output_dir / f"{model_name_from_path(durable_model or actual_model)}_span_note.dxf",
            model_name=model_name_from_path(durable_model or actual_model),
            span_id_resolver=span_id,
            column_outlines=column_outlines,
            rigid_links=structural.rigid_links,
            object_part_labels=span_object_part_paths(numbering_schedule, excluded_no_design_names),
            excluded_source_names=excluded_no_design_names,
        )
        split_json, split_csv = write_requires_split_audit(records, data_dir, "requires_split_audit")
        split_dxf = export_requires_split_dxf(
            records,
            data_dir / "requires_split_audit.dxf",
            model_name=model_name_from_path(durable_model or actual_model),
        )
        section_json, section_csv = write_multi_section_span_audit(
            section_records,
            data_dir,
        )
        diagnostics_json = data_dir / "topology_diagnostics.json"
        diagnostics_json.write_text(
            json.dumps(
                [asdict(diagnostic) for diagnostic in structural.diagnostics],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if phase_progress is not None:
            phase_progress(50, "拓扑 DXF/审计导出完成")
        design_exclusion_path = None
        if design_exclusions:
            design_exclusion_path = data_dir / "excluded_non_design_objects.json"
            design_exclusion_path.write_text(
                json.dumps(
                    {
                        "schema": "etabs-beam-generator/excluded-non-design-objects@1",
                        "count": len(design_exclusions),
                        "objects": list(design_exclusions),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        excel_output = None
        excel_primary_output = None
        excel_secondary_output = None
        rebar_payload_path = None
        rebar_workbook_output = None
        rebar_preview_dir = None
        secondary_rebar_payload_path = None
        secondary_rebar_workbook_output = None
        secondary_rebar_preview_dir = None
        beam_dxf_export = None

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
                        data_dir / "writeback_full_attribute_audit.json",
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
                    demand_audit_path = data_dir / "design_demand_equivalence.json"
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

        if args.export_excel or args.export_rebar_workbook or getattr(args, "export_beam_result_dxf", False):
            if not sap.DesignConcrete.GetResultsAvailable():
                raise RuntimeError("设计结果导出要求 ETABS 已存在当前模型状态的混凝土设计结果。")
            if args.writeback:
                export_source = EtabsOapiReadSource(
                    sap,
                    actual_model,
                    durable_model_path=durable_model,
                )
                extraction_progress = _scaled_progress(phase_progress, 50, 56)
                if extraction_progress is None:
                    export_beams = export_source.extract_beams(
                        args.selection,
                        include_design_ineligible=False,
                    )
                else:
                    export_beams = export_source.extract_beams(
                        args.selection,
                        include_design_ineligible=False,
                        progress=extraction_progress,
                    )
                export_supports = export_source.column_supports()
                export_column_anchors = export_source.column_anchor_names()
                export_rigid_links = export_source.rigid_links_for_beams(export_beams)
                export_column_outlines = export_source.column_outlines()
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
                    column_anchor_names=export_column_anchors,
                    rigid_links=export_rigid_links,
                )
            else:
                export_source = read_source
                # Ordinary read-only exports must remain projections of the
                # canonical topology built above.  Re-extracting the model
                # here used to request design-ineligible objects for DXF and
                # then renumber a second topology, so span_note/topology and
                # Excel/rebar/DXF labels could refer to different physical
                # spans.  Non-design objects stay in the exclusion audit only.
                export_beams = beams
                export_structural = structural
                export_column_outlines = column_outlines
            if phase_progress is not None:
                phase_progress(60, f"导出对象拓扑完成（{len(export_beams)} 梁）")
            design_progress = _scaled_progress(phase_progress, 60, 72)
            if design_progress is None:
                designs = export_source.designs(export_beams)
            else:
                designs = export_source.designs(
                    export_beams,
                    progress=design_progress,
                )
            if phase_progress is not None:
                phase_progress(73, f"读取设计结果完成（{len(designs)} 梁）")
            span_schedule = build_span_schedule(
                export_structural,
                export_beams,
                designs,
                config.geometry.coordinate_tolerance_mm,
                detailing_policy_id=detailing_policy_id,
                valid_station_domain=(detailing_policy_revision_id == DRAWING_V3_REVISION_ID),
            )
            if phase_progress is not None:
                phase_progress(75, f"Span schedule 完成（{len(span_schedule.records)} 跨）")
            if getattr(args, "export_beam_result_dxf", False):
                beam_dxf_export = export_beam_result_and_section_dxfs(
                    span_schedule,
                    export_beams,
                    export_source.section_info,
                    output_dir,
                    text_height=float(getattr(args, "dxf_text_height", 50)),
                    column_outlines=export_column_outlines,
                )
                if phase_progress is not None:
                    phase_progress(82, "梁计算结果/截面 DXF 导出完成")
            if args.export_excel:
                groups = ContinuityAnalyzer(
                    config.geometry.coordinate_tolerance_mm,
                    config.geometry.slope_limit_deg,
                ).analyze(export_beams)
                model_name = model_name_from_path(durable_model or actual_model)
                primary_excel = export_beam_schedule(
                    list(export_beams),
                    groups,
                    designs,
                    data_dir,
                    "basic_primarybeam_schedule_{timestamp}.xlsx",
                    span_schedule=span_schedule,
                    beam_type="PB",
                )
                secondary_excel = export_beam_schedule(
                    list(export_beams),
                    groups,
                    designs,
                    data_dir,
                    "basic_secondarybeam_schedule_{timestamp}.xlsx",
                    span_schedule=span_schedule,
                    beam_type="SB",
                )
                excel_output = primary_excel
                excel_primary_output = primary_excel
                excel_secondary_output = secondary_excel
                if phase_progress is not None:
                    phase_progress(86, "Excel 梁表导出完成")
            if args.export_rebar_workbook:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                rebar_payload_path = data_dir / f"rebar_workbook_payload_{stamp}.json"
                rebar_workbook_output = output_dir / f"{model_name_from_path(durable_model or actual_model)}_primarybeam_schedule.xlsx"
                rebar_preview_dir = data_dir / f"rebar_workbook_previews_{stamp}"
                secondary_rebar_payload_path = data_dir / f"secondary_beam_payload_{stamp}.json"
                secondary_rebar_workbook_output = output_dir / f"{model_name_from_path(durable_model or actual_model)}_secondarybeam_schedule.xlsx"
                secondary_rebar_preview_dir = data_dir / f"secondary_beam_previews_{stamp}"
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
                    engine=make_fresh_detailing_engine(
                        detailing_policy_id,
                        layout_dictionary_path=config.design.layout_dictionary_path,
                        candidate_lookup=config.design.candidate_lookup,
                        candidate_lookup_output_dir=data_dir,
                    ),
                    detailing_policy_id=detailing_policy_id,
                    rebar_workers=config.design.rebar_workers,
                    sb_frame_type_override=(
                        "Non Sway"
                        if detailing_policy_id in {DRAWING_DETAILING_POLICY_ID, DRAWING_V3_POLICY_ID}
                        else None
                    ),
                )
                write_rebar_payload(payload, rebar_payload_path)
                write_rebar_payload(
                    build_secondary_schedule_payload(payload),
                    secondary_rebar_payload_path,
                )
                if phase_progress is not None:
                    phase_progress(92, "配筋表输入文件导出完成；实际进程数={}，梁串分派耗时={:.1f}秒".format(
                        payload["metadata"]["rebar_parallelism"]["workers_effective"],
                        payload["metadata"].get("rebar_performance", {}).get("dispatch_seconds", 0.0),
                    ))

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
        "detailing_policy_id": detailing_policy_id,
        "detailing_policy_revision_id": detailing_policy_revision_id,
        "data_source": data_source_identity,
        "environment": environment,
        "beams": len(beams),
        "beam_lines": len(structural.lines),
        "spans": len(structural.spans),
        "primary_spans": sum(span.beam_type == "PB" for span in structural.spans),
        "secondary_spans": sum(span.beam_type == "SB" for span in structural.spans),
        "excluded_non_design_objects": len(design_exclusions),
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
        summary["optimization_incomplete_count"] = sum(row.get("coordination_status") == "FEASIBLE_OPTIMIZATION_INCOMPLETE" for row in payload.get("raw_rows", []))
        summary["rebar_parallelism"] = payload["metadata"]["rebar_parallelism"]
        summary["rebar_performance"] = payload["metadata"].get("rebar_performance", {})
        summary["rebar_dispatch_seconds"] = summary["rebar_performance"].get("dispatch_seconds")
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
        summary["outputs"]["excel_primary"] = str(excel_primary_output or excel_output)
        if excel_secondary_output is not None:
            summary["outputs"]["excel_secondary"] = str(excel_secondary_output)
    if beam_dxf_export is not None:
        summary["outputs"]["beam_result_dxf"] = [str(path) for path in beam_dxf_export.result_paths]
        summary["outputs"]["beam_section_dxf"] = [str(path) for path in beam_dxf_export.section_paths]
        summary["beam_result_dxf_issues"] = [asdict(issue) for issue in beam_dxf_export.issues]
        summary["beam_result_dxf_summary"] = [asdict(item) for item in beam_dxf_export.summaries]
    if tower_result is not None:
        summary["tower_assignment"] = tower_result["tower_assignment"]
        summary["outputs"].update(tower_result["outputs"])
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
    config = load_config(
        args.config,
        etabs_dll_path=api_path,
        detailing_policy_id=getattr(args, "detailing_policy", None),
        layout_dictionary_path=getattr(args, "layout_dictionary", None),
        rebar_workers=getattr(args, "rebar_workers", None),
        candidate_lookup=getattr(args, "candidate_lookup", None),
    )
    selected_policy_id = config.design.detailing_policy
    selected_engine = make_fresh_detailing_engine(
        selected_policy_id,
        layout_dictionary_path=config.design.layout_dictionary_path,
        candidate_lookup=getattr(config.design, "candidate_lookup", "off"),
    )
    drawing_engine = (
        selected_engine
        if selected_policy_id == DRAWING_DETAILING_POLICY_ID
        else make_fresh_detailing_engine(DRAWING_DETAILING_POLICY_ID)
    )
    style_library = drawing_engine.offline_style_library
    if style_library is None:
        raise RuntimeError("PACKAGED_DETAILING_POLICY_RESOURCE_UNAVAILABLE")
    drawing_rule_summary = style_library.effective_rule_summary(drawing_engine)
    if selected_policy_id == DRAWING_V3_POLICY_ID:
        expected_drawing_revision = DRAWING_V3_REVISION_ID
    elif selected_policy_id == DRAWING_DETAILING_POLICY_ID and getattr(config.design, "candidate_lookup", "off") == "auto":
        expected_drawing_revision = DRAWING_POLICY_REVISION_R9_ID
    else:
        expected_drawing_revision = DRAWING_POLICY_REVISION_R8_ID
    if selected_policy_id in {DRAWING_DETAILING_POLICY_ID, DRAWING_V3_POLICY_ID} and (
        selected_engine.detailing_policy_revision_id != expected_drawing_revision
    ):
        raise RuntimeError("FRESH_DETAILING_POLICY_REVISION_UNEXPECTED")
    if drawing_rule_summary.get("eligible_rule_count") != 380:
        raise RuntimeError(
            "PACKAGED_DETAILING_POLICY_RULE_COUNT_INVALID: "
            f"expected 380 eligible rules, got {drawing_rule_summary.get('eligible_rule_count')}"
        )

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
        "detailing_policy_id": selected_policy_id,
        "detailing_policy_revision_id": selected_engine.detailing_policy_revision_id,
        "layout_dictionary": selected_engine.layout_dictionary_metadata,
        "detailing_policy_resource": {
            "path": str(default_runtime_rules_path()),
            "policy_revision_id": drawing_engine.detailing_policy_revision_id,
            "frozen_rule_count": drawing_rule_summary.get("frozen_rule_count"),
            "eligible_rule_count": drawing_rule_summary.get("eligible_rule_count"),
        },
    }
    if design_exclusion_path is not None:
        summary["outputs"]["excluded_non_design_objects"] = str(design_exclusion_path)


def _replay_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Redetail a frozen payload without ETABS attachment or model writeback."""
    source = Path(args.replay_payload).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Replay payload not found: {source}")
    output_dir = Path(args.output_dir or source.parent / "v042-replay").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    input_read_start = time.perf_counter()
    payload = json.loads(source.read_text(encoding="utf-8"))
    input_read_seconds = time.perf_counter() - input_read_start
    generation_start = time.perf_counter()
    requested_policy = getattr(args, "detailing_policy", None)
    allow_revision_change = bool(getattr(args, "allow_policy_revision_change", False))
    if allow_revision_change and requested_policy is None:
        raise ValueError("--allow-policy-revision-change requires explicit --detailing-policy")
    if (getattr(args, "candidate_lookup", None) == "off" and requested_policy is None
            and payload.get("metadata", {}).get("detailing_policy_revision_id") == DRAWING_POLICY_REVISION_R9_ID):
        raise ValueError("R9 replay with --candidate-lookup off requires --detailing-policy and --allow-policy-revision-change")
    config = load_config(
        getattr(args, "config", None),
        detailing_policy_id=requested_policy,
        layout_dictionary_path=getattr(args, "layout_dictionary", None),
        rebar_workers=getattr(args, "rebar_workers", None),
        candidate_lookup=getattr(args, "candidate_lookup", None),
    )
    layout_dictionary_path = config.design.layout_dictionary_path
    replay_lookup = (config.design.candidate_lookup if requested_policy is not None
                     or getattr(args, "candidate_lookup", None) is not None
                     or allow_revision_change else "off")
    if requested_policy is None and layout_dictionary_path is None and replay_lookup == "off":
        result = redetail_payload(payload, rebar_workers=config.design.rebar_workers,
                                 candidate_lookup_output_dir=output_dir / "_data")
    else:
        selected_policy = requested_policy or config.design.detailing_policy
        engine = make_fresh_detailing_engine(
            selected_policy, layout_dictionary_path=layout_dictionary_path, candidate_lookup=replay_lookup,
            candidate_lookup_output_dir=output_dir / "_data"
        )
        result = redetail_payload(
            payload,
            engine=engine,
            allow_policy_revision_change=(
                allow_revision_change
                or layout_dictionary_path is not None
                or getattr(args, "candidate_lookup", None) == "auto"
            ),
            rebar_workers=config.design.rebar_workers,
        )
    generation_seconds = time.perf_counter() - generation_start
    destination = (output_dir / source.name).resolve()
    if destination == source:
        raise ValueError("offline replay refuses to overwrite its source payload")
    serialization_start = time.perf_counter()
    destination = write_rebar_payload(result, destination)
    serialization_seconds = time.perf_counter() - serialization_start
    return {
        "status": "OK",
        "mode": "offline-payload-replay",
        "source_payload": str(source),
        "output_payload": str(destination),
        "schema_version": result.get("metadata", {}).get("schema_version"),
        "detailing_policy_id": result.get("metadata", {}).get("detailing_policy_id", LEGACY_DETAILING_POLICY_ID),
        "detailing_policy_revision_id": result.get("metadata", {}).get("detailing_policy_revision_id", LEGACY_DETAILING_POLICY_ID),
        "layout_dictionary": result.get("metadata", {}).get("layout_dictionary"),
        "row_count": len(result.get("raw_rows", [])),
        "optimization_incomplete_count": sum(row.get("coordination_status") == "FEASIBLE_OPTIMIZATION_INCOMPLETE" for row in result.get("raw_rows", [])),
        "model_writeback": False,
        "rebar_parallelism": result.get("metadata", {}).get("rebar_parallelism", {}),
        "rebar_dispatch_seconds": result.get("metadata", {}).get("rebar_performance", {}).get("dispatch_seconds"),
        "rebar_generation_seconds": generation_seconds,
        "rebar_input_load_seconds": input_read_seconds,
        "rebar_serialization_seconds": serialization_seconds,
        "rebar_generation_timing_scope": "after JSON load through engine initialization and redetail return; includes worker spawn; excludes input read and output serialization",
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
        "--detailing-policy",
        choices=sorted(SUPPORTED_DETAILING_POLICY_IDS),
        default=None,
        help="本次新运行使用的稳定配筋策略 ID；离线回放未指定时保留载荷记录的策略与修订",
    )
    parser.add_argument(
        "--layout-dictionary",
        type=Path,
        help="可选的预计算纵筋候选字典库；仅显式绘图策略 R6 支持",
    )
    parser.add_argument(
        "--rebar-workers",
        type=parse_rebar_workers,
        default=None,
        help="BeamLine processes: auto uses logical CPU count minus one; integer 1..64 overrides config.",
    )
    parser.add_argument("--candidate-lookup", choices=["auto", "off"], default=None,
                        help="Use the prepared R9 candidate lookup for drawing policy.")
    parser.add_argument(
        "--v3-coverage-schedule",
        action="store_true",
        help="Enable experimental V3 top-10 coverage coordination for a drawing_v3 SQLite snapshot run.",
    )
    parser.add_argument(
        "--runtime-self-test",
        action="store_true",
        help="验证便携 Python/依赖/ETABS API 装载后退出，不附着或修改模型",
    )
    parser.add_argument(
        "--replay-payload",
        type=Path,
        help="离线重算既有梁表 JSON；不连接 ETABS、不写回模型",
    )
    parser.add_argument(
        "--allow-policy-revision-change",
        action="store_true",
        help="Allow an explicit same-policy revision change during offline replay; requires --detailing-policy.",
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
    mode.add_argument("--tower-create-groups", action="store_true",
                      help="创建/更新插件所有的 Tower 审查 Group，不修改 Tower 归属或保存模型")
    parser.add_argument("--tower-check", action="store_true", help="运行前检查 Tower/Story/端点分配")
    parser.add_argument("--tower-preflight-only", action="store_true", help="只运行 Tower 预检，导出 JSON/CSV")
    parser.add_argument("--tower-continue-on-review", action="store_true", help="已人工确认 Tower 疑似问题，允许继续")
    parser.add_argument("--tower-report", type=Path, help="Tower 报告目录，默认本次输出目录")
    parser.add_argument("--tower-plan", type=Path, help="已确认的 Tower 报告/Group 计划")
    parser.add_argument("--allow-tower-group-mutation", action="store_true", help="明确允许审查 Group 修改，不自动保存")
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
        help="分别导出 PB/SB 单页静态配筋结果 Excel（无公式）",
    )
    parser.add_argument(
        "--export-beam-result-dxf",
        action="store_true",
        help="按楼层导出梁计算结果 DXF，并同时导出真实截面 width*depth DXF",
    )
    parser.add_argument(
        "--dxf-text-height",
        type=int,
        choices=(25, 50, 75, 100),
        default=50,
        help="梁结果和截面 DXF 的文字高度（mm）",
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
