"""Offline comparison of PB hoop ranking using an existing process payload.

No ETABS attachment, model mutation, Excel export or release packaging occurs.
The baseline freezes only the preceding stirrup ranking; both runs use the
same current demand reconstruction, SB policy and longitudinal coordination.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.design.detailing import BeamDetailingEngine, ProjectDetailingPolicy
from src.rebar_workbook import redetail_payload, write_rebar_payload


class DiameterFirstBaseline(BeamDetailingEngine):
    def _preferred_stirrup(self, section, candidates):
        if section.width_mm <= self.project.prefer_two_leg_max_width_mm + 1e-9:
            return min(candidates, key=lambda item: (item[2].legs, -item[1], item[0]))[2]
        return min(candidates, key=lambda item: (item[0], -item[1], item[2].legs))[2]


def _statuses(rows):
    return dict(Counter(row.get("engine_status", "") for row in rows))


def _brief(row):
    return {
        key: row.get(key)
        for key in (
            "beam_no",
            "beam_type",
            "engine_status",
            "et1",
            "t1",
            "et2",
            "b1",
            "s2",
            "s1",
            "legs",
            "iteration_status",
            "constructability_status",
            "constructability_blocking_statuses",
            "smf_check_statuses",
            "engine_warnings",
        )
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source.resolve(strict=True), args.output_dir.resolve()
    # A new directory prevents overwriting either the source or prior evidence.
    output.mkdir(parents=True, exist_ok=False)
    source_bytes = source.read_bytes()
    payload = json.loads(source_bytes)
    input_hash = sha256(source_bytes).hexdigest()
    code_files = [ROOT / name for name in ("src/design/detailing.py", "src/rebar_workbook.py")]
    code_hashes = {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in code_files}

    start = perf_counter()
    previous = redetail_payload(
        payload,
        engine=DiameterFirstBaseline(project_policy=replace(ProjectDetailingPolicy(), version="legacy-diameter-first")),
    )
    previous["metadata"]["pb_stirrup_selection_strategy"] = "LEGACY_DIAMETER_THEN_SPACING_THEN_LEGS"
    baseline_seconds = perf_counter() - start
    start = perf_counter()
    current = redetail_payload(payload)
    current_seconds = perf_counter() - start
    keys = [row["beam_no"] for row in payload["raw_rows"]]
    assert len(keys) == len(set(keys)), "Duplicate beam identity in comparison input"
    for result in (previous, current):
        for collection in ("raw_rows", "review_rows", "final_rows"):
            assert [row["beam_no"] for row in result[collection]] == keys, "Beam rows changed or reordered"
        for original, calculated in zip(payload["raw_rows"], result["raw_rows"]):
            for name in ("left", "middle", "right"):
                for suffix in (
                    "top_total_mm2",
                    "bottom_total_mm2",
                    "top_flex_mm2",
                    "bottom_flex_mm2",
                    "av_over_s",
                    "at_over_s",
                    "al_mm2",
                ):
                    field = f"{name}_{suffix}"
                    assert original.get(field) == calculated.get(
                        field
                    ), f"Source demand changed: {original['beam_no']}/{field}"

    sb_differences = []
    for collection in ("raw_rows", "review_rows", "final_rows"):
        for before, after in zip(previous[collection], current[collection]):
            if after["beam_type"] == "SB" and before != after:
                sb_differences.append({"collection": collection, "beam_no": after["beam_no"]})
    assert not sb_differences, "PB change altered an SB result"
    assert source.read_bytes() == source_bytes, "Historical source changed during replay"
    assert all(
        sha256(path.read_bytes()).hexdigest() == code_hashes[str(path.relative_to(ROOT))] for path in code_files
    ), "Engine source changed during replay"

    changes = []
    new_engine_failures, resolved_engine_failures = [], []
    changed_smf_checks = []
    for before, after in zip(previous["final_rows"], current["final_rows"]):
        if _brief(before) != _brief(after):
            changes.append({"before": _brief(before), "after": _brief(after)})
        if before.get("engine_status") == "OK" and after.get("engine_status") != "OK":
            new_engine_failures.append(_brief(after))
        if before.get("engine_status") != "OK" and after.get("engine_status") == "OK":
            resolved_engine_failures.append(_brief(after))
        if before.get("smf_check_statuses", "") != after.get("smf_check_statuses", ""):
            changed_smf_checks.append({"before": _brief(before), "after": _brief(after)})

    report = {
        "scope": "offline ranking comparison, not live model or engineering acceptance",
        "source": str(source),
        "source_sha256": input_hash,
        "code_sha256": code_hashes,
        "policy_version": current["metadata"]["project_policy_version"],
        "rows": len(keys),
        "rows_by_role": dict(Counter(row["beam_type"] for row in current["raw_rows"])),
        "baseline_seconds": baseline_seconds,
        "current_seconds": current_seconds,
        "baseline_statuses": _statuses(previous["final_rows"]),
        "current_statuses": _statuses(current["final_rows"]),
        "sb_changed_rows": sb_differences,
        "new_engine_failures": new_engine_failures,
        "resolved_engine_failures": resolved_engine_failures,
        "changed_smf_checks": changed_smf_checks,
        "pb38": next((item for item in changes if item["after"]["beam_no"] == "T2-Story2-PB38-1"), None),
        "changed_rows": changes,
    }
    write_rebar_payload(previous, output / "baseline_payload.json")
    write_rebar_payload(current, output / "redetailed_payload.json")
    write_rebar_payload(report, output / "comparison.json")
    print(
        json.dumps(
            {
                "report": str(output / "comparison.json"),
                "rows": report["rows"],
                "baseline_statuses": report["baseline_statuses"],
                "current_statuses": report["current_statuses"],
                "changed_rows": len(changes),
                "new_engine_failures": len(new_engine_failures),
                "resolved_engine_failures": len(resolved_engine_failures),
                "sb_changed_rows": len(sb_differences),
                "changed_smf_checks": len(changed_smf_checks),
                "pb38": report["pb38"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
