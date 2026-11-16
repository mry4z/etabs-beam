import gzip
import json
import lzma
from pathlib import Path

from src.design.detailing import (
    BeamDetailingProfile,
    BeamSection,
    DRAWING_POLICY_ID,
    StirrupDesign,
    make_fresh_detailing_engine,
)
from src.design.lookup_catalog import LookupCatalog, context_key


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOOKUP_ASSETS = PROJECT_ROOT / "src" / "design" / "lookup_assets"


def test_r9_query_hits_saved_r7_context_and_geometry_assets():
    context_index = LOOKUP_ASSETS / "context_indices_first.jsonl.xz"
    geometry_asset_path = LOOKUP_ASSETS / "width_200.json.gz"
    with lzma.open(context_index, "rt", encoding="utf-8") as stream:
        header = json.loads(stream.readline())
        first_saved_context = json.loads(stream.readline())

    assert header["format"] == "beam-row-interval-indices-jsonl@1"
    saved_key = first_saved_context["key"]
    saved_context = json.loads(saved_key)
    section_record = saved_context["section"]
    controls = saved_context["candidate_controls"]

    engine = make_fresh_detailing_engine(DRAWING_POLICY_ID, candidate_lookup="auto")
    section = BeamSection(
        "saved-lookup-context-probe",
        float(section_record["width_mm"]),
        float(section_record["depth_mm"]),
        float(section_record["clear_cover_to_stirrup_outer_mm"]),
        float(section_record["fc_mpa"]),
        float(section_record["fy_mpa"]),
        float(section_record["fyt_mpa"]),
        float(section_record["max_aggregate_size_mm"]),
        6000.0,
        BeamDetailingProfile(section_record["detailing_profile"]),
        float(section_record["etabs_longitudinal_cover_mm"]),
        beam_role=section_record["beam_role"],
        actual_frame_type=section_record["actual_frame_type"],
    )
    hoops = tuple(
        StirrupDesign(int(diameter), 100, int(legs), 0.0, 0.0, 300.0, "saved-asset-probe")
        for diameter, legs in controls["hoops_diameter_mm_legs_ordered"]
    )
    assert engine.detailing_policy_revision_id == "drawing_20261031_lookup_v1"

    requested_key = context_key(
        section,
        hoops,
        engine,
        first_diameter=controls["first_diameter_mm"],
        max_layers=controls["max_layers"],
        face=controls["face"],
        preserve_support_alternatives=controls["preserve_support_alternatives"],
    )
    assert requested_key == saved_key

    provider = LookupCatalog()
    provider.load_indices(context_index, only_keys={saved_key})
    geometry_asset = LookupCatalog.load(geometry_asset_path)
    provider._geometry_templates.update(geometry_asset._geometry_templates)
    provider._failure_banks.extend(geometry_asset._failure_banks)
    prefix = provider._prefix(section, engine)
    assert prefix in geometry_asset.template_prefixes()

    required_area = provider.contexts[saved_key]["membership"][0][0]
    results = provider.query(
        required_area,
        section,
        hoops,
        engine,
        first_diameter=controls["first_diameter_mm"],
        max_layers=controls["max_layers"],
        face=controls["face"],
        preserve_support_alternatives=controls["preserve_support_alternatives"],
    )

    assert results
    assert provider.query_audit["context_misses"] == 0
    assert provider.query_audit["geometry_template_hits"] > 0
    assert provider.query_audit["physical_layout_computations"] == 0
