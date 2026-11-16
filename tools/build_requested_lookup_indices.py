"""Compile exact candidate intervals for explicitly recorded contexts."""
from __future__ import annotations
import argparse
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.design.detailing import (BeamDetailingProfile, BeamSection, DRAWING_POLICY_ID,
    DRAWING_POLICY_REVISION_R8_ID, StirrupDesign, make_detailing_engine)
from src.design.layout_dictionary import _engine_context, _bank_key, normalized_bank_context
from src.design.lookup_catalog import LookupCatalog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--templates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    requests = json.loads(args.requests.read_text(encoding="utf-8"))["requests"]
    engine = make_detailing_engine(detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R8_ID, ranking_mode="style")
    requests.sort(key=lambda request: request["section"]["width_mm"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    current_width = None
    catalog = None
    with gzip.open(temporary, "wt", encoding="utf-8") as stream:
        stream.write(json.dumps({"format": "beam-row-interval-indices-jsonl@1"}) + "\n")
        for number, request in enumerate(requests, 1):
            if request["engine"] != _engine_context(engine):
                raise ValueError("unsupported non-default engine context")
            values = dict(request["section"])
            values["detailing_profile"] = BeamDetailingProfile(values["detailing_profile"])
            section = BeamSection(**values)
            if section.width_mm != current_width:
                current_width = section.width_mm
                path = args.templates / f"width_{int(current_width)}.json.gz"
                if not path.exists() or current_width != int(current_width):
                    raise ValueError(f"no exact width template: {current_width}")
                catalog = LookupCatalog.load(path)
            controls = request["candidate_controls"]
            hoops = tuple(StirrupDesign(diameter_mm=d, spacing_mm=100, legs=legs,
                provided_av_over_s_mm2_per_mm=0, provided_at_over_s_mm2_per_mm=0,
                maximum_spacing_mm=100, governing_reason="index")
                for d, legs in controls["hoops_diameter_mm_legs_ordered"])
            key = catalog.prepare(section, hoops, engine,
                first_diameter=controls["first_diameter_mm"], max_layers=controls["max_layers"],
                face=controls["face"], preserve_support_alternatives=controls["preserve_support_alternatives"])
            if key != _bank_key(normalized_bank_context(request)):
                raise ValueError("normalized request context mismatch")
            context = catalog.contexts[key]
            entries = [[low, high, rows, context["records"][rows][0]["as_min_mm2"]]
                       for low, high, rows in context["membership"]]
            stream.write(json.dumps({"key": key, "membership": entries}, separators=(",", ":"),
                                    allow_nan=False) + "\n")
            catalog.contexts.clear()
            catalog._context_key_cache.clear()
            if number % 20 == 0:
                print(f"prepared {number}/{len(requests)} width={current_width:g}", flush=True)
    temporary.replace(args.output)
    print(f"prepared {len(requests)} contexts: {args.output}")


if __name__ == "__main__":
    main()
