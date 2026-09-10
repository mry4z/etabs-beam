"""Tower-only orchestration, independent of split/rename/delivery workflows."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Sequence

from src.core.etabs_tower import create_tower_review_groups
from src.core.tower_assignment import TowerAssignmentReport, write_tower_assignment_report


class TowerPreflightRequired(RuntimeError):
    """The report must be resolved or explicitly reviewed before continuing."""


def tower_enabled(args: argparse.Namespace) -> bool:
    return any(getattr(args, key, False) for key in ("tower_check", "tower_preflight_only", "tower_create_groups"))


def validate_tower_options(args: argparse.Namespace) -> None:
    if getattr(args, "allow_tower_group_mutation", False) and not getattr(args, "tower_create_groups", False):
        raise ValueError("--allow-tower-group-mutation 只能与 --tower-create-groups 一起使用。")
    if not tower_enabled(args):
        return
    if getattr(args, "tower_preflight_only", False) and getattr(args, "tower_create_groups", False):
        raise ValueError("--tower-preflight-only 不能与 --tower-create-groups 混用。")
    if getattr(args, "snapshot_input", None) is not None:
        raise ValueError("Tower 预检目前要求 ETABS OAPI，不支持离线快照入口。")
    if any(
        getattr(args, key, False)
        for key in (
            "writeback",
            "prepare_section_review_groups",
            "select_section_review_group",
            "run_analysis",
            "run_design",
        )
    ):
        raise ValueError("Tower 预检/审查 Group 不与写回、保存、分析或设计操作混用。")
    if getattr(args, "tower_create_groups", False) and not getattr(args, "allow_tower_group_mutation", False):
        raise PermissionError("创建 Tower 审查 Group 必须显式指定 --allow-tower-group-mutation。")


def _validate_tower_plan(path: Path | None, report: TowerAssignmentReport, model_sha256: str | None) -> None:
    if path is None:
        return
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Tower 计划必须是 JSON 对象。")
    identity = payload.get("model_identity") or {}
    expected = identity.get("source_model_sha256") or identity.get("model_sha256") or payload.get("model_sha256")
    if expected and model_sha256 and str(expected).casefold() != model_sha256.casefold():
        raise RuntimeError("TOWER_PLAN_MODEL_HASH_MISMATCH")
    # A confirmed report cannot authorize newly discovered groups or members.
    if payload.get("groups") != json.loads(json.dumps([asdict(group) for group in report.groups])):
        raise RuntimeError("TOWER_PLAN_GROUP_SET_MISMATCH")


def run_tower_preflight(
    args: argparse.Namespace,
    read_source: Any,
    beams: Sequence[Any],
    output_dir: Path,
    *,
    progress: Callable[[int, str], None] | None = None,
) -> dict[str, Any] | None:
    """Return report metadata; callers stop early for Tower-only operations."""
    if not tower_enabled(args):
        return None
    report_kwargs = {
        "selection": args.selection,
        "selected_beams": tuple(beam.name for beam in beams) if args.selection == "selected" else None,
    }
    if progress is not None:
        report_kwargs["progress"] = progress
    report = read_source.tower_assignment_report(**report_kwargs)
    report_dir = Path(getattr(args, "tower_report", None) or output_dir)
    if report_dir.suffix.casefold() == ".json":
        report_dir = report_dir.parent
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report_json, report_csv = write_tower_assignment_report(report, report_dir, run_id)
    if report.blocked:
        raise TowerPreflightRequired(
            "Tower 预检被阻断：" + "; ".join(report.diagnostics[:5]) + f"；报告：{report_json}"
        )
    if report.review_required and not (
        getattr(args, "tower_continue_on_review", False) or getattr(args, "tower_create_groups", False)
    ):
        raise TowerPreflightRequired(
            f"Tower 预检发现 {report.issue_count} 个疑似错误对象；请先复核并明确确认继续。报告：{report_json}"
        )
    if (
        report.review_required
        and getattr(args, "tower_continue_on_review", False)
        and getattr(args, "tower_plan", None)
    ):
        _validate_tower_plan(args.tower_plan, report, read_source.identity().get("source_model_sha256"))
    result: dict[str, Any] = {
        "tower_assignment": report.to_dict(),
        "outputs": {"tower_assignment_json": str(report_json), "tower_assignment_csv": str(report_csv)},
    }
    if getattr(args, "tower_create_groups", False):
        if bool(read_source.sap.GetModelIsLocked()):
            raise RuntimeError("模型处于锁定状态，拒绝创建 Tower 审查 Group。")
        if not getattr(args, "allow_tower_group_mutation", False):
            raise PermissionError("创建 Tower 审查 Group 必须显式确认。")
        identity = read_source.identity()
        model_hash = identity.get("source_model_sha256")
        _validate_tower_plan(getattr(args, "tower_plan", None), report, model_hash)
        registry = Path(getattr(args, "manifest_registry", None) or output_dir.parent / "manifests")
        model_key = hashlib.sha256(str(read_source.durable_model_path).casefold().encode("utf-8")).hexdigest()[:24]
        ownership = registry / f"tower_group_ownership_{model_key}.json"
        group_result = create_tower_review_groups(
            read_source.sap,
            report,
            allow_mutation=True,
            ownership_path=ownership,
            expected_model_sha256=model_hash,
        )
        result["tower_group_result"] = asdict(group_result)
        result["outputs"]["tower_group_ownership"] = str(ownership)
        audit = output_dir / f"tower_group_audit_{run_id}.json"
        audit.write_text(
            json.dumps(
                {
                    "operation": "tower-create-groups",
                    "model_identity": identity,
                    "confirmed_plan": str(getattr(args, "tower_plan", None) or ""),
                    "user_confirmed": True,
                    "saved": False,
                    "report": str(report_json),
                    "result": asdict(group_result),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        result["outputs"]["tower_group_audit"] = str(audit)
    return result
