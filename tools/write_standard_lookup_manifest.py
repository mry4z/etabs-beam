"""Validate the recorded preset matrix and write an honest coverage manifest."""
from __future__ import annotations
import argparse
import gzip
import itertools
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--indices-dir", type=Path, required=True)
    parser.add_argument("--templates-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    widths = list(range(200, 801, 50))
    depths = sorted(set(range(300, 1201, 50)) | {370,420,470,670,1270,1650,1750,1850})
    expected = set(itertools.product(depths, ("OMF", "SMF"), (10, 12), (2, 4, 6),
                                      (("top", True), ("bottom", False))))
    files = []
    for width in widths:
        path = args.indices_dir / f"width_{width}.jsonl.gz"
        geometry = args.templates_dir / f"width_{width}.json.gz"
        if not geometry.is_file():
            raise ValueError(f"missing geometry templates for {width}")
        seen = set()
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            if json.loads(stream.readline()).get("format") != "beam-row-interval-indices-jsonl@1":
                raise ValueError(f"invalid index header: {path}")
            for line in stream:
                item = json.loads(line)
                context = json.loads(item["key"])
                section, controls = context["section"], context["candidate_controls"]
                hoop = controls["hoops_diameter_mm_legs_ordered"]
                if (section["width_mm"] != width or section["clear_cover_to_stirrup_outer_mm"] != 40
                    or section["max_aggregate_size_mm"] != 25 or section["fc_mpa"] != 25
                    or section["fy_mpa"] != 420 or section["fyt_mpa"] != 420
                    or section["beam_role"] != "PB" or section["actual_frame_type"] != ""
                    or section["etabs_longitudinal_cover_mm"] is not None
                    or controls["first_diameter_mm"] is not None or controls["max_layers"] != 3
                    or len(hoop) != 1):
                    raise ValueError(f"unexpected context in {path}")
                entry = (section["depth_mm"], section["detailing_profile"], hoop[0][0], hoop[0][1],
                         (controls["face"], controls["preserve_support_alternatives"]))
                if entry in seen:
                    raise ValueError(f"duplicate context in {path}")
                seen.add(entry)
            if seen != expected:
                raise ValueError(f"incomplete preset coverage for {width}: {len(seen)}")
        files.append({"width_mm": width, "path":path.name, "bytes":path.stat().st_size,
                      "contexts":len(seen), "geometry_path":geometry.name})
    output = {"format":"beam-row-standard-interval-index-manifest@1",
        "description":"Exact closed float demand intervals, with shared geometry templates.",
        "widths_mm":widths, "depths_mm":depths, "profiles":["OMF", "SMF"],
        "beam_role":"PB", "actual_frame_type":"", "etabs_longitudinal_cover_mm":None,
        "clear_cover_mm":40, "aggregate_mm":25, "fc_mpa":25, "fy_mpa":420, "fyt_mpa":420,
        "first_diameter_mm":None, "max_layers":3, "hoop_diameters_mm":[10,12],
        "hoop_legs":[2,4,6], "face_preserve_pairs":[["top",True],["bottom",False]],
        "demand":"All finite nonnegative float values; no truncation at 10000 mm2.",
        "excluded":"Other profiles, beam roles, frame metadata, first diameters, hoop combinations and nonstandard policies require exact context preparation.",
        "context_count":sum(item["contexts"] for item in files), "files":files}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"validated {output['context_count']} preset contexts: {args.output}")


if __name__ == "__main__":
    main()
