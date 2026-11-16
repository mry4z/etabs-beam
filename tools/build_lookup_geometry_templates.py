"""Compile reusable physical geometry templates for a given standard width.

The files contain geometry templates only. Complete candidate contexts must be
prepared before query; this tool does not claim to build area interval indices.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.design.detailing import (BeamDetailingProfile, BeamSection, DRAWING_POLICY_ID,
    DRAWING_POLICY_REVISION_R8_ID, StirrupDesign, make_detailing_engine)
from src.design.lookup_catalog import LookupCatalog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.width < 200 or args.width > 800 or args.width % 50:
        parser.error("standard width must be 200..800 in 50 mm increments")
    if args.output.exists():
        parser.error("output already exists")
    section = BeamSection(name="STANDARD_TEMPLATE", width_mm=args.width, depth_mm=1200,
        clear_cover_to_stirrup_outer_mm=40, fc_mpa=25, fy_mpa=420, fyt_mpa=420,
        max_aggregate_size_mm=25, length_mm=1, detailing_profile=BeamDetailingProfile.OMF)
    engine = make_detailing_engine(detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R8_ID, ranking_mode="style")
    catalog = LookupCatalog()
    for diameter in engine.project.ordered_stirrup_diameters():
        hoop = StirrupDesign(diameter_mm=diameter, spacing_mm=100, legs=2,
            provided_av_over_s_mm2_per_mm=0, provided_at_over_s_mm2_per_mm=0,
            maximum_spacing_mm=100, governing_reason="template")
        catalog.prepare(section, (hoop,), engine, max_layers=3)
        # Candidate records are specific to this synthetic section. Only the
        # shared geometry templates apply to arbitrary heights and profiles.
        catalog.contexts.clear()
    catalog.save(args.output)
    print(f"width={args.width} templates={len(catalog._geometry_templates)} output={args.output}")


if __name__ == "__main__":
    main()
