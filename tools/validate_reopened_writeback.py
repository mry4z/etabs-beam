"""Validate a saved ETABS writeback copy after reopening it.

The utility performs two independent checks against a completed full-audit
artifact:

1. reopen the saved EDB and compare every audited Frame Object attribute;
2. rebuild the topology/writeback plan and prove that a second run has no
   executable orientation or rename actions.

It is intentionally read-only after ``File.OpenFile`` and writes one JSON
evidence file outside the model.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
from typing import Any

from src.config import load_config
from src.core.beam_line_span_analyzer import BeamLineSpanSettings, analyze_beam_lines
from src.core.beam_line_span_analyzer import (
    _candidate_column_count,
    _candidate_is_primary,
    _build_segment_index,
    _column_coordinates,
    _oriented_path,
    _split_candidates_by_role,
    _stitch_candidates,
    _support_path_signature,
)
from src.core.beam_merger import BeamMerger
from src.core.etabs_connector import EtabsConnector, api_values
from src.core.geometry_extractor import GeometryExtractor
from src.core.model_writeback import (
    EtabsWriteback,
    FrameAuditState,
    build_writeback_plan,
    compare_frame_audits,
    exclude_writeback_objects,
)
from src.run_manifest import sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--exclude-writeback-objects", type=Path)
    parser.add_argument("--skip-attribute-audit", action="store_true")
    parser.add_argument("--diagnose-orientation-invariance", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    sap = _attach(args.pid, config.etabs.dll_path)
    model = args.model.resolve()
    _status_code(sap.File.OpenFile(str(model)))
    actual_model = Path(str(sap.GetModelFilename(True))).resolve()
    if actual_model.parent != model.parent or actual_model.stem.casefold() != model.stem.casefold():
        raise RuntimeError(f"重开模型不一致：{actual_model} != {model}")

    raw_audit = json.loads(args.audit.read_text(encoding="utf-8"))
    writeback = EtabsWriteback(sap)
    comparison = None
    if not args.skip_attribute_audit:
        expected = {guid: _audit_state(value) for guid, value in raw_audit["after"].items()}
        reopened = writeback.snapshot_audit(state.name for state in expected.values())
        comparison = compare_frame_audits(expected, reopened)

    connector = EtabsConnector(config.etabs.dll_path)
    connector.sap_model = sap
    from ETABSv1 import eUnits

    with connector.temporary_present_units(eUnits.N_mm_C):
        extractor = GeometryExtractor(sap)
        beams = extractor.extract("all")
        supports = extractor.column_supports()
        topology = analyze_beam_lines(
            beams,
            set(supports),
            BeamLineSpanSettings(
                coordinate_tolerance_mm=config.geometry.coordinate_tolerance_mm,
                source_span_overlap_tolerance_mm=(config.geometry.source_span_overlap_tolerance_mm),
                merge_angle_deg=config.geometry.merge_angle_limit_deg,
                intersection_z_tolerance_mm=config.geometry.coordinate_tolerance_mm,
                story_workers=4,
            ),
            column_support_coordinates=supports,
        )
        states = writeback.snapshot_from_geometry(beams)
        plan = build_writeback_plan(topology, beams, states)
        excluded = _load_excluded(args.exclude_writeback_objects)
        if excluded:
            plan = exclude_writeback_objects(plan, excluded)

        orientation_diagnostics = None
        if args.diagnose_orientation_invariance:
            reversed_names = {
                value["name"]
                for guid, value in raw_audit["after"].items()
                if (
                    value["point_i"],
                    value["point_j"],
                )
                == (
                    raw_audit["before"][guid]["point_j"],
                    raw_audit["before"][guid]["point_i"],
                )
                and value["point_i"] != raw_audit["before"][guid]["point_i"]
            }
            virtual_before = [
                (
                    replace(
                        beam,
                        point_i=beam.point_j,
                        point_j=beam.point_i,
                        start=beam.end,
                        end=beam.start,
                    )
                    if beam.name in reversed_names
                    else beam
                )
                for beam in beams
            ]
            before_topology = analyze_beam_lines(
                virtual_before,
                set(supports),
                BeamLineSpanSettings(
                    coordinate_tolerance_mm=config.geometry.coordinate_tolerance_mm,
                    source_span_overlap_tolerance_mm=(config.geometry.source_span_overlap_tolerance_mm),
                    merge_angle_deg=config.geometry.merge_angle_limit_deg,
                    intersection_z_tolerance_mm=(config.geometry.coordinate_tolerance_mm),
                    story_workers=4,
                ),
                column_support_coordinates=supports,
            )
            current_assignments = _object_assignments(topology)
            before_assignments = _object_assignments(before_topology)
            mismatches = {
                name: {
                    "virtual_before": before_assignments.get(name, []),
                    "current": current_assignments.get(name, []),
                }
                for name in sorted(set(before_assignments) | set(current_assignments))
                if before_assignments.get(name) != current_assignments.get(name)
            }
            orientation_diagnostics = {
                "reversed_objects": len(reversed_names),
                "assignment_mismatches": len(mismatches),
                "mismatches": mismatches,
                "virtual_before_diagnostics": [asdict(item) for item in before_topology.diagnostics],
                "current_diagnostics": [asdict(item) for item in topology.diagnostics],
                "affected_candidates": {
                    "virtual_before": _candidate_details(virtual_before, supports, config, set(mismatches)),
                    "current": _candidate_details(beams, supports, config, set(mismatches)),
                },
                "role_refinement_trace": {
                    "virtual_before": _role_refinement_trace(virtual_before, supports, config, set(mismatches)),
                    "current": _role_refinement_trace(beams, supports, config, set(mismatches)),
                },
            }

    orientation_changes = [
        asdict(action) for action in plan.orientations if action.status == "READY" and action.reverse
    ]
    rename_changes = [
        asdict(action) for action in plan.renames if action.status == "READY" and action.name != action.final_name
    ]
    blockers = sorted(
        {
            (action.name, action.status, action.reason)
            for action in (*plan.orientations, *plan.renames)
            if action.status != "READY"
        }
    )
    result: dict[str, Any] = {
        "status": (
            "OK"
            if (comparison is None or comparison.ok) and not orientation_changes and not rename_changes
            else "FAILED"
        ),
        "model": str(model),
        "model_sha256": sha256_file(model),
        "reopen_attribute_audit": ({"skipped": True} if comparison is None else asdict(comparison)),
        "idempotency": {
            "ok": not orientation_changes and not rename_changes,
            "beam_objects": len(beams),
            "beam_lines": len(topology.lines),
            "spans": len(topology.spans),
            "executable_orientation_changes": len(orientation_changes),
            "executable_rename_changes": len(rename_changes),
            "orientation_change_sample": orientation_changes[:20],
            "rename_change_sample": rename_changes[:20],
            "blocked_objects": len({name for name, _, _ in blockers}),
            "blocker_sample": blockers[:20],
        },
        "orientation_invariance_diagnostics": orientation_diagnostics,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.quiet:
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "output": str(args.output),
                    "reopen_attribute_audit": result["reopen_attribute_audit"],
                    "idempotency": {
                        key: result["idempotency"][key]
                        for key in (
                            "ok",
                            "beam_objects",
                            "beam_lines",
                            "spans",
                            "executable_orientation_changes",
                            "executable_rename_changes",
                        )
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "OK" else 2


def _attach(pid: int, dll_path: Path) -> Any:
    import clr

    clr.AddReference(str(dll_path))
    from ETABSv1 import Helper, cHelper

    app = cHelper(Helper()).GetObjectProcess("CSI.ETABS.API.ETABSObject", pid)
    if app is None:
        raise RuntimeError(f"ETABS PID {pid} 不可用")
    return app.SapModel


def _status_code(result: Any) -> int:
    api_values(result, "ETABS operation")
    return 0


def _audit_state(value: dict[str, Any]) -> FrameAuditState:
    return FrameAuditState(
        name=str(value["name"]),
        guid=str(value["guid"]),
        point_i=str(value["point_i"]),
        point_j=str(value["point_j"]),
        transformation_matrix=tuple(float(item) for item in value["transformation_matrix"]),
        attributes=value["attributes"],
    )


def _load_excluded(path: Path | None) -> set[str]:
    if path is None:
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(value) for value in payload.get("excluded_object_names", [])}


def _object_assignments(topology: Any) -> dict[str, list[str]]:
    assignments: dict[str, list[str]] = {}
    for span in topology.spans:
        for name in span.source_objects:
            assignments.setdefault(name, []).append(span.span_id)
    return {name: sorted(values) for name, values in assignments.items()}


def _candidate_details(
    beams: list[Any], supports: dict[str, Any], config: Any, affected: set[str]
) -> list[dict[str, Any]]:
    story_beams = [beam for beam in beams if beam.story == "GF-FG"]
    settings = BeamLineSpanSettings(
        coordinate_tolerance_mm=config.geometry.coordinate_tolerance_mm,
        source_span_overlap_tolerance_mm=(config.geometry.source_span_overlap_tolerance_mm),
        merge_angle_deg=config.geometry.merge_angle_limit_deg,
        intersection_z_tolerance_mm=config.geometry.coordinate_tolerance_mm,
        story_workers=1,
    )
    designs = {}
    merged = BeamMerger(angle_limit_deg=settings.merge_angle_deg).merge(story_beams, designs)
    candidates, _, _ = _stitch_candidates(merged.beams, settings)
    column_points = _column_coordinates(
        story_beams,
        set(supports),
        settings.coordinate_tolerance_mm,
        supports,
    )
    by_name = {beam.name: beam for beam in story_beams}
    details: list[dict[str, Any]] = []
    for candidate in candidates:
        names = tuple(candidate.source_names or (candidate.name,))
        if not affected.intersection(names):
            continue
        details.append(
            {
                "source_names": names,
                "path_points": candidate.path_points,
                "point_i": candidate.point_i,
                "point_j": candidate.point_j,
                "is_seed_primary": _candidate_is_primary(
                    candidate,
                    by_name,
                    set(supports),
                    column_points,
                    settings,
                ),
                "column_count": _candidate_column_count(candidate, column_points, settings),
                "source_supports": {
                    name: {
                        "point_i": by_name[name].point_i,
                        "point_j": by_name[name].point_j,
                        "i_is_column": by_name[name].point_i in supports,
                        "j_is_column": by_name[name].point_j in supports,
                    }
                    for name in names
                },
            }
        )
    return details


def _role_refinement_trace(
    beams: list[Any], supports: dict[str, Any], config: Any, affected: set[str]
) -> list[dict[str, Any]]:
    story_beams = [beam for beam in beams if beam.story == "GF-FG"]
    settings = BeamLineSpanSettings(
        coordinate_tolerance_mm=config.geometry.coordinate_tolerance_mm,
        source_span_overlap_tolerance_mm=(config.geometry.source_span_overlap_tolerance_mm),
        merge_angle_deg=config.geometry.merge_angle_limit_deg,
        intersection_z_tolerance_mm=config.geometry.coordinate_tolerance_mm,
        story_workers=1,
    )
    merged = BeamMerger(angle_limit_deg=settings.merge_angle_deg).merge(story_beams, {})
    candidates, _, _ = _stitch_candidates(merged.beams, settings)
    column_points = _column_coordinates(
        story_beams,
        set(supports),
        settings.coordinate_tolerance_mm,
        supports,
    )
    by_name = {beam.name: beam for beam in story_beams}
    seed_indices = {
        index
        for index, candidate in enumerate(candidates)
        if _candidate_is_primary(
            candidate,
            by_name,
            set(supports),
            column_points,
            settings,
        )
    }
    anchor_indices = {
        index for index in seed_indices if _candidate_column_count(candidates[index], column_points, settings) >= 2
    } or set(seed_indices)
    primary_paths = tuple(
        _oriented_path(candidates[index].path_points or (candidates[index].start, candidates[index].end))
        for index in sorted(seed_indices)
    )
    seed_index = _build_segment_index(primary_paths, settings.intersection_grid_mm)
    lines = _split_candidates_by_role(
        candidates,
        seed_indices,
        anchor_indices,
        column_points,
        seed_index,
        by_name,
        settings,
    )
    trace: list[dict[str, Any]] = []
    support_paths = primary_paths
    for iteration in range(7):
        trace.append(
            {
                "iteration": iteration,
                "support_signature": _support_path_signature(support_paths, settings.coordinate_tolerance_mm),
                "affected_lines": [
                    {
                        "beam_type": line.beam_type,
                        "source_objects": line.source_objects,
                        "path_points": line.path_points,
                    }
                    for line in lines
                    if affected.intersection(line.source_objects)
                ],
            }
        )
        support_paths = tuple(line.path_points for line in lines if line.beam_type == "PB")
        lines = _split_candidates_by_role(
            candidates,
            seed_indices,
            anchor_indices,
            column_points,
            _build_segment_index(support_paths, settings.intersection_grid_mm),
            by_name,
            settings,
            seed_intersection_index=seed_index,
        )
    return trace


if __name__ == "__main__":
    raise SystemExit(main())
