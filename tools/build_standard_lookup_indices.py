"""Build exact standard preset demand intervals for one width."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.design.detailing import (BeamDetailingProfile, BeamSection, DRAWING_POLICY_ID,
    DRAWING_POLICY_REVISION_R8_ID, StirrupDesign, make_detailing_engine)
from src.design.layout_dictionary import _context_record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--templates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.width not in range(200, 801, 50):
        parser.error("standard width must be 200..800 in steps of 50")
    engine = make_detailing_engine(detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R8_ID, ranking_mode="style")
    requests = []
    depths = sorted(set(range(300, 1201, 50)) | {370,420,470,670,1270,1650,1750,1850})
    for depth in depths:
        for profile in (BeamDetailingProfile.OMF, BeamDetailingProfile.SMF):
            section = BeamSection(name="STANDARD", width_mm=float(args.width), depth_mm=float(depth),
                clear_cover_to_stirrup_outer_mm=40.0, fc_mpa=25.0, fy_mpa=420.0, fyt_mpa=420.0,
                max_aggregate_size_mm=25.0, length_mm=1.0, detailing_profile=profile)
            for diameter in (10, 12):
                for legs in (2, 4, 6):
                    hoop = StirrupDesign(diameter_mm=diameter, spacing_mm=100, legs=legs,
                        provided_av_over_s_mm2_per_mm=0, provided_at_over_s_mm2_per_mm=0,
                        maximum_spacing_mm=100, governing_reason="standard index")
                    for face, preserve in (("top", True), ("bottom", False)):
                        requests.append(_context_record(section, (hoop,), engine, first_diameter=None,
                            max_layers=3, face=face, preserve_support_alternatives=preserve))
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "requests.json"
        path.write_text(json.dumps({"requests":requests}, separators=(",", ":")), encoding="utf-8")
        subprocess.run([sys.executable, str(ROOT / "tools/build_requested_lookup_indices.py"),
            "--requests", str(path), "--templates", str(args.templates), "--output", str(args.output)], check=True)


if __name__ == "__main__":
    main()
