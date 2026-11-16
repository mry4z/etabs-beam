"""Discover and compile exact R6 row-candidate contexts from saved payloads."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.design.detailing import (
    BeamDetailingProfile,
    BeamSection,
    DRAWING_POLICY_ID,
    DRAWING_POLICY_REVISION_R6_ID,
    StirrupDesign,
    make_detailing_engine,
)
from src.design.layout_dictionary import (
    LayoutDictionaryError,
    _canonical,
    _context_record,
    build_layout_dictionary,
    build_layout_dictionary_bank,
    normalized_bank_context,
    write_layout_dictionary_bank,
)
from src.design import row_candidates, row_solver
from src.rebar_workbook import redetail_payload


def discover_requests(payload_paths: list[Path], ranking_modes: tuple[str, ...], *,
                      max_discovery_seconds: float) -> tuple[
                          dict[str, dict], dict[str, list[float]], dict[str, int], dict[str, float]
                      ]:
    original_row_candidates = row_candidates.candidates
    original_solver_candidates = row_solver.candidates
    requests: dict[str, dict] = {}
    query_areas: dict[str, set[float]] = {}
    calls_by_mode: Counter[str] = Counter()
    replay_seconds: dict[str, float] = {}
    started = time.perf_counter()

    def capture(section, hoops, required, engine, *, first_diameter=None, max_layers=None, face="top",
                preserve_support_alternatives=True, search_stats=None, geometry_cache=None):
        context = _context_record(
            section, tuple(hoops), engine,
            first_diameter=first_diameter,
            max_layers=max_layers,
            face=face,
            preserve_support_alternatives=preserve_support_alternatives,
        )
        normalized = normalized_bank_context(context)
        key = _canonical(normalized)
        request = requests.get(key)
        if request is None:
            request = {
                "normalized_context": normalized,
                "section": section,
                "hoops": tuple(hoops),
                "engine": engine,
                "first_diameter": first_diameter,
                "max_layers": max_layers,
                "face": face,
                "preserve_support_alternatives": preserve_support_alternatives,
                "strict_context": context,
                "ranking_modes_seen": set(),
                "call_count": 0,
            }
            requests[key] = request
        request["ranking_modes_seen"].add(engine.ranking_mode)
        request["call_count"] += 1
        query_areas.setdefault(key, set()).add(float(required))
        calls_by_mode[engine.ranking_mode] += 1
        return original_row_candidates(
            section,
            hoops,
            required,
            engine,
            first_diameter=first_diameter,
            max_layers=max_layers,
            face=face,
            preserve_support_alternatives=preserve_support_alternatives,
            search_stats=search_stats,
            geometry_cache=geometry_cache,
        )

    row_candidates.candidates = capture
    row_solver.candidates = capture
    try:
        for payload_path in payload_paths:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            for ranking_mode in ranking_modes:
                replay_key = f"{payload_path.name}:{ranking_mode}"
                replay_started = time.perf_counter()
                engine = make_detailing_engine(
                    detailing_policy_id=DRAWING_POLICY_ID,
                    detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
                    ranking_mode=ranking_mode,
                )
                redetail_payload(
                    payload,
                    engine=engine,
                    allow_policy_revision_change=True,
                )
                replay_seconds[replay_key] = time.perf_counter() - replay_started
                if time.perf_counter() - started > max_discovery_seconds:
                    raise LayoutDictionaryError(
                        f"DICTIONARY_DISCOVERY_TIMEOUT: max_seconds={max_discovery_seconds:g}"
                    )
    finally:
        row_candidates.candidates = original_row_candidates
        row_solver.candidates = original_solver_candidates
    return requests, {key: sorted(values) for key, values in query_areas.items()}, {
        **dict(calls_by_mode), "total_calls": sum(calls_by_mode.values()),
    }, replay_seconds


def hydrate_discovery_report(path: Path) -> tuple[dict[str, dict], dict[str, list[float]], dict[str, int]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("format") != "beam-layout-dictionary-bank-discovery@1":
        raise ValueError("unsupported dictionary discovery report format")
    requests: dict[str, dict] = {}
    query_areas: dict[str, list[float]] = {}
    for entry in report.get("normalized_contexts", ()):
        normalized = entry.get("context")
        key = str(entry.get("context_key", ""))
        if not isinstance(normalized, dict) or _canonical(normalized) != key:
            raise ValueError("discovery report normalized context key mismatch")
        if key in requests:
            raise ValueError("duplicate normalized context in discovery report")
        section_data = dict(normalized["section"])
        section_data.update({
            "name": "LAYOUT_DICTIONARY_PRECOMPUTED_CONTEXT",
            "length_mm": 1.0,
            "clear_cover_source": "precomputed dictionary context; provenance excluded by normalization v1",
            "max_aggregate_size_source": "precomputed dictionary context; provenance excluded by normalization v1",
            "etabs_longitudinal_cover_source": "precomputed dictionary context; provenance excluded by normalization v1",
            "profile_source": "precomputed dictionary context; provenance excluded by normalization v1",
        })
        section_data["detailing_profile"] = BeamDetailingProfile(section_data["detailing_profile"])
        section = BeamSection(**section_data)
        mode = "style" if "style" in entry.get("ranking_modes_seen", ()) else "legacy"
        engine = make_detailing_engine(
            detailing_policy_id=DRAWING_POLICY_ID,
            detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
            ranking_mode=mode,
        )
        controls = normalized["candidate_controls"]
        if controls.get("max_layers") is None:
            raise ValueError("discovery report contains an unspecified max_layers control")
        hoops = tuple(StirrupDesign(
            diameter_mm=int(diameter),
            spacing_mm=100,
            legs=int(legs),
            provided_av_over_s_mm2_per_mm=0.0,
            provided_at_over_s_mm2_per_mm=0.0,
            maximum_spacing_mm=100.0,
            governing_reason="bank compiler; only diameter and support legs affect candidate lookup",
        ) for diameter, legs in controls["hoops_diameter_mm_legs_ordered"])
        reconstructed = normalized_bank_context(_context_record(
            section,
            hoops,
            engine,
            first_diameter=controls.get("first_diameter_mm"),
            max_layers=controls.get("max_layers"),
            face=str(controls["face"]),
            preserve_support_alternatives=bool(controls["preserve_support_alternatives"]),
        ))
        if reconstructed != normalized:
            raise ValueError("discovery report context cannot be reconstructed exactly")
        requests[key] = {
            "normalized_context": normalized,
            "section": section,
            "hoops": hoops,
            "engine": engine,
            "first_diameter": controls.get("first_diameter_mm"),
            "max_layers": controls.get("max_layers"),
            "face": str(controls["face"]),
            "preserve_support_alternatives": bool(controls["preserve_support_alternatives"]),
            "ranking_modes_seen": set(entry.get("ranking_modes_seen", ())),
            "call_count": int(entry.get("candidate_call_count", 0)),
        }
        query_areas[key] = [float(value) for value in entry.get("exact_required_As_mm2", ())]
    if len(requests) != int(report.get("normalized_context_count", -1)):
        raise ValueError("discovery report context count mismatch")
    return requests, query_areas, dict(report.get("candidate_call_counts", {}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payloads", nargs="*", type=Path)
    parser.add_argument("--output-bank", required=True, type=Path)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--ranking-modes", default="legacy,style")
    parser.add_argument("--max-discovery-seconds", type=float, default=300.0)
    parser.add_argument("--max-contexts-before-review", type=int, default=20)
    parser.add_argument("--estimated-seconds-per-context", type=float, default=2.6)
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--allow-large-bank", action="store_true")
    parser.add_argument(
        "--compile-discovery-report",
        type=Path,
        help="Compile a previously recorded discovery report without replaying its payloads",
    )
    args = parser.parse_args()
    if args.compile_discovery_report is not None:
        requests, query_areas, calls_by_mode = hydrate_discovery_report(args.compile_discovery_report.resolve())
        payload_paths = []
        ranking_modes = ()
        replay_seconds = {}
    else:
        if not args.payloads:
            raise ValueError("provide saved payloads or --compile-discovery-report")
        payload_paths = [Path(path).resolve() for path in args.payloads]
        if any(not path.is_file() for path in payload_paths):
            missing = [str(path) for path in payload_paths if not path.is_file()]
            raise FileNotFoundError(f"saved payloads not found: {missing}")
        ranking_modes = tuple(dict.fromkeys(value.strip() for value in args.ranking_modes.split(",") if value.strip()))
        if not ranking_modes or any(value not in {"legacy", "style"} for value in ranking_modes):
            raise ValueError("--ranking-modes must be a comma-separated subset of legacy,style")
        requests, query_areas, calls_by_mode, replay_seconds = discover_requests(
            payload_paths,
            ranking_modes,
            max_discovery_seconds=args.max_discovery_seconds,
        )
    context_count = len(requests)
    estimated_compile_seconds = context_count * args.estimated_seconds_per_context
    unsupported_controls = [
        key for key, request in requests.items() if request["max_layers"] is None
    ]
    report_path = args.report or args.output_bank.with_suffix(
        ".compiled_report.json" if args.compile_discovery_report is not None else ".discovery.json"
    )
    report = {
        "format": "beam-layout-dictionary-bank-discovery@1",
        "payloads": [str(path) for path in payload_paths],
        "ranking_modes": list(ranking_modes),
        "candidate_call_counts": calls_by_mode,
        "replay_seconds": replay_seconds,
        "normalized_context_count": context_count,
        "normalized_contexts": [
            {
                "context_key": key,
                "context": request["normalized_context"],
                "ranking_modes_seen": sorted(request["ranking_modes_seen"]),
                "candidate_call_count": request["call_count"],
                "exact_required_As_mm2": query_areas[key],
                "max_layers": request["max_layers"],
            }
            for key, request in sorted(requests.items())
        ],
        "estimated_compile_seconds": estimated_compile_seconds,
        "max_contexts_before_review": args.max_contexts_before_review,
        "max_dictionary_build_seconds_per_context": 180.0,
        "unsupported_unspecified_max_layers_context_count": len(unsupported_controls),
        "compile_status": "NOT_STARTED",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if unsupported_controls:
        report["compile_status"] = "BLOCKED_UNSPECIFIED_MAX_LAYERS"
    elif args.discover_only and args.compile_discovery_report is None:
        report["compile_status"] = "DISCOVERY_ONLY"
    elif (not args.allow_large_bank and args.compile_discovery_report is None
          and (context_count > args.max_contexts_before_review or estimated_compile_seconds > 300.0)):
        report["compile_status"] = "REVIEW_REQUIRED_BEFORE_COMPILE"
    else:
        strict_dictionaries = []
        context_build_seconds = {}
        for context_key, request in sorted(requests.items()):
            build_started = time.perf_counter()
            dictionary = build_layout_dictionary(
                request["section"],
                request["hoops"],
                request["engine"],
                first_diameter=request["first_diameter"],
                max_layers=request["max_layers"],
                face=request["face"],
                preserve_support_alternatives=request["preserve_support_alternatives"],
                build_timeout_seconds=180.0,
            )
            context_build_seconds[context_key] = time.perf_counter() - build_started
            strict_dictionaries.append(dictionary)
        bank = build_layout_dictionary_bank(strict_dictionaries)
        destination = write_layout_dictionary_bank(args.output_bank, bank)
        report["compile_status"] = "COMPILED"
        report["compiled_bank_path"] = str(destination)
        report["context_build_seconds"] = context_build_seconds
        report["bank_audit"] = bank["bank_audit"]
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "report": str(report_path),
        "compile_status": report["compile_status"],
        "normalized_context_count": context_count,
        "estimated_compile_seconds": estimated_compile_seconds,
        "candidate_call_counts": calls_by_mode,
        "replay_seconds": replay_seconds,
        "compiled_bank_path": report.get("compiled_bank_path"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
