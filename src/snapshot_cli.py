"""Read-only CLI for exporting a validated ETABS public-OAPI SQLite bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.config import load_config
from src.core.etabs_connector import EtabsConnector, etabs_version_info, model_paths_match
from src.core.geometry_extractor import GeometryExtractor
from src.model_source import EtabsOapiReadSource
from src.run_manifest import sha256_file
from src.snapshot.exporter import (
    ACI31814_RECTANGULAR_BEAM_PROFILE,
    collect_exact_design_results,
    collect_profile_tables,
    export_snapshot_bundle,
)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        summary = run(arguments)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    """Attach to one explicit PID and export without model mutation or save."""

    config = load_config(arguments.config)
    expected_model = Path(arguments.expected_model).resolve()
    if not expected_model.is_file() or expected_model.suffix.casefold() != ".edb":
        raise FileNotFoundError(f"expected durable EDB not found: {expected_model}")

    connector = EtabsConnector(config.etabs.dll_path)
    sap_model = connector.attach_process(arguments.pid)
    actual_model = Path(connector.model_filename()).resolve()
    if not model_paths_match(actual_model, expected_model):
        raise RuntimeError(f"attached model mismatch: {actual_model} != {expected_model}")

    version = etabs_version_info(sap_model)
    if not version.get("available") or not version.get("supported"):
        raise RuntimeError(f"unsupported ETABS host: {version}")
    if not bool(sap_model.DesignConcrete.GetResultsAvailable()):
        raise RuntimeError("ETABS concrete design results are unavailable; refusing snapshot export")
    source_sha256_before_capture = sha256_file(expected_model)

    from ETABSv1 import eUnits

    with connector.temporary_present_units(eUnits.N_mm_C):
        extractor = GeometryExtractor(sap_model)
        beams = extractor.extract("all")
        supports = extractor.column_supports()
        live_source = EtabsOapiReadSource(
            sap_model,
            actual_model,
            durable_model_path=expected_model,
            expected_model_sha256=source_sha256_before_capture,
        )
        frame_states = live_source.frame_states(beams)
        section_info = {name: live_source.section_info(name) for name in sorted({beam.section for beam in beams})}
        raw_tables = collect_profile_tables(sap_model)
        designs = collect_exact_design_results(
            sap_model,
            [beam.name for beam in beams],
        )
        summary = export_snapshot_bundle(
            destination=arguments.output,
            source_model_path=expected_model,
            api_dll_path=config.etabs.dll_path,
            etabs_version=str(version.get("version") or "unknown"),
            api_version=_api_file_version(config.etabs.dll_path),
            design_code=config.design.standard,
            raw_tables=raw_tables,
            live_beams=beams,
            live_supports=supports,
            live_designs=designs,
            live_frame_states=frame_states,
            live_section_info=section_info,
            profile=ACI31814_RECTANGULAR_BEAM_PROFILE,
            coordinate_tolerance_mm=arguments.coordinate_tolerance_mm,
            expected_source_sha256=source_sha256_before_capture,
        )
    summary["host"] = {
        "pid": arguments.pid,
        "actual_model": str(actual_model),
        "expected_model": str(expected_model),
        "etabs": version,
        "read_only": True,
    }
    return summary


def _api_file_version(path: Path) -> str:
    try:
        from System.Diagnostics import FileVersionInfo

        info = FileVersionInfo.GetVersionInfo(str(path))
        return str(info.FileVersion or info.ProductVersion or "unknown")
    except Exception:
        return "unknown"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a validated read-only SQLite snapshot from public ETABS OAPI data.",
    )
    parser.add_argument("--pid", type=int, required=True, help="ETABS process ID")
    parser.add_argument(
        "--expected-model",
        required=True,
        help="Exact durable .EDB path expected in the target process",
    )
    parser.add_argument("--config", help="Project YAML config")
    parser.add_argument("--output", required=True, help="New .sqlite output path")
    parser.add_argument(
        "--coordinate-tolerance-mm",
        type=float,
        default=0.01,
        help="Maximum public-table coordinate replay delta (default: 0.01 mm)",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
