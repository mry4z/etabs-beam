"""Production export and validation for project-owned ETABS SQLite snapshots."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.core.geometry_extractor import BeamGeometry
from src.design.etabs_result import EtabsResultProvider
from src.design.provider import BeamDesignResult, DesignResultStatus
from src.run_manifest import artifact_record, sha256_file
from src.snapshot.sqlite_snapshot import (
    PROJECT_BEAM_DESIGN_MAPPING_VERSION,
    OapiTable,
    PublicOapiTableSource,
    SnapshotMetadata,
    SnapshotReader,
    SnapshotTableError,
    SqliteDesignProvider,
    SqliteSnapshotSource,
    build_project_beam_geometry_table,
    build_project_column_support_table,
    build_project_design_table,
    write_snapshot,
)
from src.utils.geometry import Point3D


@dataclass(frozen=True)
class SnapshotProfile:
    """An explicit set of public OAPI tables and domain mappings."""

    name: str
    version: str
    required_tables: tuple[str, ...]
    optional_tables: tuple[str, ...] = ()


ACI31814_RECTANGULAR_BEAM_PROFILE = SnapshotProfile(
    name="etabs-aci31814-rectangular-concrete-beam",
    version="1.1",
    required_tables=(
        "Story Definitions",
        "Point Object Connectivity",
        "Beam Object Connectivity",
        "Column Object Connectivity",
        "Frame Assignments - Section Properties",
        "Frame Assignments - Summary",
        "Frame Section Property Definitions - Concrete Rectangular",
        "Frame Section Property Definitions - Concrete Beam Reinforcing",
        "Material Properties - Concrete Data",
        "Material Properties - Rebar Data",
        "Concrete Beam Overwrites - ACI 318-14",
    ),
    optional_tables=("Brace Object Connectivity",),
)


def collect_profile_tables(
    sap_model: Any,
    profile: SnapshotProfile = ACI31814_RECTANGULAR_BEAM_PROFILE,
) -> tuple[OapiTable, ...]:
    """Read one fail-closed profile entirely through public DatabaseTables."""

    source = PublicOapiTableSource(sap_model)
    available = {table.key for table in source.available_tables()}
    missing = sorted(set(profile.required_tables) - available)
    if missing:
        raise SnapshotTableError(f"SNAPSHOT_PROFILE_TABLES_MISSING: {', '.join(missing)}")
    keys = (*profile.required_tables, *(key for key in profile.optional_tables if key in available))
    return source.read_tables(keys)


def collect_exact_design_results(
    sap_model: Any,
    beam_names: Sequence[str],
) -> dict[str, BeamDesignResult]:
    """Read every beam's exact normalized `_2` result, preserving per-beam failures."""

    names = tuple(str(name) for name in beam_names)
    if not names or len(set(names)) != len(names):
        raise SnapshotTableError("设计结果导出的梁名必须非空且唯一。")
    provider = EtabsResultProvider(sap_model)
    results: dict[str, BeamDesignResult] = {}
    for name in names:
        try:
            results[name] = provider.get_beam_design_total(name)
        except Exception as exc:
            results[name] = BeamDesignResult(
                name,
                status=DesignResultStatus.API_ERROR,
                status_message=f"{type(exc).__name__}: {exc}",
                source_objects=(name,),
                units="N_mm_C",
                result_source="GetSummaryResultsBeam_2",
            )
    if not any(result.stations for result in results.values()):
        raise SnapshotTableError("SNAPSHOT_NO_USABLE_BEAM_DESIGN_RESULTS")
    return results


def export_snapshot_bundle(
    *,
    destination: str | Path,
    source_model_path: str | Path,
    api_dll_path: str | Path,
    etabs_version: str,
    api_version: str,
    design_code: str,
    raw_tables: Sequence[OapiTable],
    live_beams: Sequence[BeamGeometry],
    live_supports: Mapping[str, Point3D],
    live_designs: Mapping[str, BeamDesignResult],
    live_frame_states: Mapping[str, Any] | None = None,
    live_section_info: Mapping[str, Mapping[str, Any]] | None = None,
    profile: SnapshotProfile = ACI31814_RECTANGULAR_BEAM_PROFILE,
    coordinate_tolerance_mm: float = 0.01,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    """Write, replay, validate and manifest one non-overwriting snapshot bundle."""

    target = Path(destination).resolve()
    source_model = Path(source_model_path).resolve()
    api_dll = Path(api_dll_path).resolve()
    manifest_path = target.with_suffix(f"{target.suffix}.manifest.json")
    if target.exists() or manifest_path.exists():
        existing = target if target.exists() else manifest_path
        raise FileExistsError(f"快照 bundle 已存在，拒绝覆盖：{existing}")
    if not source_model.is_file():
        raise FileNotFoundError(source_model)
    if not api_dll.is_file():
        raise FileNotFoundError(api_dll)
    if coordinate_tolerance_mm <= 0:
        raise ValueError("coordinate_tolerance_mm 必须为正数。")
    raw_keys = tuple(table.key for table in raw_tables)
    missing_tables = sorted(set(profile.required_tables) - set(raw_keys))
    if missing_tables:
        raise SnapshotTableError(f"SNAPSHOT_PROFILE_TABLES_MISSING: {', '.join(missing_tables)}")
    live_beam_names = {beam.name for beam in live_beams}
    if len(live_beam_names) != len(live_beams):
        raise SnapshotTableError("SNAPSHOT_GEOMETRY_DUPLICATE_BEAM")
    if live_beam_names != set(live_designs):
        raise SnapshotTableError("SNAPSHOT_DESIGN_BEAM_SET_MISMATCH")

    source_sha256_before = sha256_file(source_model)
    if expected_source_sha256 is not None and source_sha256_before.casefold() != expected_source_sha256.casefold():
        raise SnapshotTableError(
            "SNAPSHOT_SOURCE_CHANGED_DURING_CAPTURE: "
            f"before capture {expected_source_sha256}, before write {source_sha256_before}"
        )
    metadata = SnapshotMetadata(
        source_model_path=str(source_model),
        source_model_sha256=source_sha256_before,
        etabs_version=etabs_version,
        api_version=api_version,
        api_sha256=sha256_file(api_dll),
        units="N_mm_C",
        design_code=design_code,
        export_profile=profile.name,
        export_profile_version=profile.version,
        domain_mapping_version=PROJECT_BEAM_DESIGN_MAPPING_VERSION,
    )
    tables = (
        *raw_tables,
        build_project_beam_geometry_table(live_beams),
        build_project_column_support_table(live_supports),
        build_project_design_table(live_designs),
    )
    created_snapshot = False
    try:
        write_snapshot(target, metadata, tables)
        created_snapshot = True
        source_sha256_after = sha256_file(source_model)
        if source_sha256_after != source_sha256_before:
            raise SnapshotTableError(
                "SNAPSHOT_SOURCE_CHANGED_DURING_EXPORT: " f"before {source_sha256_before}, after {source_sha256_after}"
            )
        validation = validate_snapshot(
            target,
            source_model_path=source_model,
            live_beams=live_beams,
            live_supports=live_supports,
            live_designs=live_designs,
            live_frame_states=live_frame_states,
            live_section_info=live_section_info,
            coordinate_tolerance_mm=coordinate_tolerance_mm,
        )
        summary = {
            "status": "OK",
            "snapshot": artifact_record(target),
            "source_model": artifact_record(source_model),
            "api_dll": artifact_record(api_dll),
            "metadata": asdict(metadata),
            "tables": [
                {
                    "key": table.key,
                    "version": table.version,
                    "field_count": len(table.fields),
                    "row_count": len(table.rows),
                }
                for table in tables
            ],
            "validation": validation,
        }
        _write_json_new(manifest_path, summary)
        summary["manifest"] = artifact_record(manifest_path)
        return summary
    except Exception:
        if created_snapshot and target.is_file():
            target.unlink()
        partial_manifest = manifest_path.with_name(f"{manifest_path.name}.partial")
        if partial_manifest.is_file():
            partial_manifest.unlink()
        raise


def validate_snapshot(
    snapshot_path: str | Path,
    *,
    source_model_path: str | Path,
    live_beams: Sequence[BeamGeometry],
    live_supports: Mapping[str, Point3D],
    live_designs: Mapping[str, BeamDesignResult],
    live_frame_states: Mapping[str, Any] | None = None,
    live_section_info: Mapping[str, Mapping[str, Any]] | None = None,
    coordinate_tolerance_mm: float,
) -> dict[str, Any]:
    """Replay every supported domain and require equivalence to live OAPI data."""

    reader = SnapshotReader(snapshot_path)
    reader.assert_source_current(source_model_path)
    source = SqliteSnapshotSource(reader)
    raw_replay_beams = source.extract_raw_table_beams()
    _validate_beams(live_beams, raw_replay_beams, coordinate_tolerance_mm)
    replay_beams = source.extract_beams()
    _validate_beams(live_beams, replay_beams, coordinate_tolerance_mm)
    geometry_exact = {beam.name: beam for beam in live_beams} == {beam.name: beam for beam in replay_beams}
    if not geometry_exact:
        raise SnapshotTableError("SNAPSHOT_PROJECT_GEOMETRY_NOT_EXACT")
    raw_replay_supports = source.raw_table_column_supports()
    _validate_points(
        live_supports,
        raw_replay_supports,
        coordinate_tolerance_mm,
        "support",
    )
    replay_supports = source.column_supports()
    _validate_points(live_supports, replay_supports, coordinate_tolerance_mm, "support")
    supports_exact = dict(live_supports) == replay_supports
    if not supports_exact:
        raise SnapshotTableError("SNAPSHOT_PROJECT_SUPPORTS_NOT_EXACT")
    if live_frame_states is not None:
        replay_states = source.frame_states(replay_beams)
        if replay_states != dict(live_frame_states):
            mismatch = next(
                (
                    name
                    for name in sorted(set(replay_states) | set(live_frame_states))
                    if replay_states.get(name) != live_frame_states.get(name)
                ),
                "unknown",
            )
            raise SnapshotTableError(f"SNAPSHOT_FRAME_STATE_REPLAY_MISMATCH: {mismatch}")
    if live_section_info is not None:
        replay_sections = {name: source.section_info(name) for name in sorted(live_section_info)}
        if replay_sections != dict(live_section_info):
            mismatch = next(
                (
                    name
                    for name in sorted(set(replay_sections) | set(live_section_info))
                    if replay_sections.get(name) != live_section_info.get(name)
                ),
                "unknown",
            )
            raise SnapshotTableError(f"SNAPSHOT_SECTION_INFO_REPLAY_MISMATCH: {mismatch}")
    provider = SqliteDesignProvider(reader)
    replay_designs = {name: provider.get_beam_design(name) for name in provider.beam_names()}
    if replay_designs != dict(live_designs):
        mismatch = next(
            (
                name
                for name in sorted(set(replay_designs) | set(live_designs))
                if replay_designs.get(name) != live_designs.get(name)
            ),
            "unknown",
        )
        raise SnapshotTableError(f"SNAPSHOT_DESIGN_REPLAY_MISMATCH: {mismatch}")
    frame_types = source.frame_types()
    beam_names = {beam.name for beam in live_beams}
    missing_frame_types = sorted(beam_names - set(frame_types))
    if missing_frame_types:
        raise SnapshotTableError(f"SNAPSHOT_FRAME_TYPE_MISSING: {missing_frame_types[0]}")
    return {
        "source_hash_current": True,
        "geometry": {
            "live_count": len(live_beams),
            "replay_count": len(replay_beams),
            "raw_table_replay_count": len(raw_replay_beams),
            "coordinate_tolerance_mm": coordinate_tolerance_mm,
            "raw_table_equivalent_within_tolerance": True,
            "equivalent": True,
            "exact_round_trip": geometry_exact,
        },
        "supports": {
            "live_count": len(live_supports),
            "replay_count": len(replay_supports),
            "raw_table_replay_count": len(raw_replay_supports),
            "coordinate_tolerance_mm": coordinate_tolerance_mm,
            "raw_table_equivalent_within_tolerance": True,
            "equivalent": True,
            "exact_round_trip": supports_exact,
        },
        "design_results": {
            "beam_count": len(replay_designs),
            "station_count": sum(len(result.stations) for result in replay_designs.values()),
            "status_counts": dict(sorted(Counter(result.status.name for result in replay_designs.values()).items())),
            "exact_round_trip": True,
        },
        "frame_types": {
            "row_count": len(frame_types),
            "all_geometry_beams_present": True,
        },
        "frame_states": {
            "count": len(live_frame_states or {}),
            "exact_round_trip": live_frame_states is not None,
        },
        "section_info": {
            "count": len(live_section_info or {}),
            "exact_round_trip": live_section_info is not None,
        },
    }


def _validate_beams(
    expected: Sequence[BeamGeometry],
    actual: Sequence[BeamGeometry],
    tolerance: float,
) -> None:
    expected_by_name = {beam.name: beam for beam in expected}
    actual_by_name = {beam.name: beam for beam in actual}
    if len(expected_by_name) != len(expected) or len(actual_by_name) != len(actual):
        raise SnapshotTableError("SNAPSHOT_GEOMETRY_DUPLICATE_BEAM")
    if set(expected_by_name) != set(actual_by_name):
        raise SnapshotTableError("SNAPSHOT_GEOMETRY_BEAM_SET_MISMATCH")
    for name in sorted(expected_by_name):
        left = expected_by_name[name]
        right = actual_by_name[name]
        header_left = (left.label, left.story, left.point_i, left.point_j, left.section)
        header_right = (right.label, right.story, right.point_i, right.point_j, right.section)
        if header_left != header_right:
            raise SnapshotTableError(f"SNAPSHOT_GEOMETRY_FIELD_MISMATCH: {name}")
        _validate_point(left.start, right.start, tolerance, f"{name}.start")
        _validate_point(left.end, right.end, tolerance, f"{name}.end")
        if left.depth is None or right.depth is None:
            if left.depth != right.depth:
                raise SnapshotTableError(f"SNAPSHOT_GEOMETRY_DEPTH_MISMATCH: {name}")
        elif abs(left.depth - right.depth) > tolerance:
            raise SnapshotTableError(f"SNAPSHOT_GEOMETRY_DEPTH_MISMATCH: {name}")


def _validate_points(
    expected: Mapping[str, Point3D],
    actual: Mapping[str, Point3D],
    tolerance: float,
    description: str,
) -> None:
    if set(expected) != set(actual):
        raise SnapshotTableError(f"SNAPSHOT_{description.upper()}_SET_MISMATCH")
    for name in sorted(expected):
        _validate_point(expected[name], actual[name], tolerance, f"{description}.{name}")


def _validate_point(
    expected: Point3D,
    actual: Point3D,
    tolerance: float,
    description: str,
) -> None:
    delta = max(abs(left - right) for left, right in zip(expected, actual))
    if delta > tolerance:
        raise SnapshotTableError(
            f"SNAPSHOT_COORDINATE_MISMATCH: {description}; delta {delta:.12g} mm > {tolerance:.12g} mm"
        )


def _write_json_new(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    if partial.exists():
        raise FileExistsError(partial)
    enriched = {
        **payload,
        "manifest_created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    try:
        partial.write_text(
            json.dumps(enriched, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.rename(partial, path)
    except Exception:
        if partial.exists():
            partial.unlink()
        raise
