from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path

import pytest

from src.design.detailing import (
    BeamDetailingProfile,
    BeamSection,
    DetailingError,
    DRAWING_POLICY_ID,
    DRAWING_POLICY_REVISION_R6_ID,
    DRAWING_POLICY_REVISION_R5_ID,
    DRAWING_POLICY_REVISION_R7_ID,
    DRAWING_POLICY_REVISION_R8_ID,
    StirrupDesign,
    make_detailing_engine,
    make_fresh_detailing_engine,
)
from src.design.layout_dictionary import (
    LayoutDictionary,
    LayoutDictionaryBank,
    LayoutDictionaryMiss,
    build_layout_dictionary_bank,
    build_layout_dictionary,
    write_layout_dictionary,
    write_layout_dictionary_bank,
)
from src.design.row_candidates import candidates as row_candidates, signature


def _pb80_context():
    section = BeamSection(
        name="GF-FG-PB80-1",
        width_mm=400.0,
        depth_mm=800.0,
        clear_cover_to_stirrup_outer_mm=40.0,
        fc_mpa=25.0,
        fy_mpa=420.0,
        fyt_mpa=420.0,
        max_aggregate_size_mm=25.0,
        length_mm=10278.371343748973,
        detailing_profile=BeamDetailingProfile.SMF,
        etabs_longitudinal_cover_mm=50.0,
        etabs_longitudinal_cover_source=(
            "ETABS PropFrame.GetRebarBeam(B 400X800) CoverTop/CoverBot; "
            "concrete face to longitudinal-reinforcement centroid; governing maximum used"
        ),
        clear_cover_source="Nasiriyah SMF/SB beam measure rev.1 (2026-08-24), section 4 table basis",
        max_aggregate_size_source="Nasiriyah SMF/SB beam measure rev.1 (2026-08-24), section 4 table basis",
        profile_source="project default: Program Determined → SMF",
        beam_role="PB",
        actual_frame_type="Program Determined",
    )
    hoop = StirrupDesign(
        diameter_mm=12,
        spacing_mm=100,
        legs=4,
        provided_av_over_s_mm2_per_mm=0.0,
        provided_at_over_s_mm2_per_mm=0.0,
        maximum_spacing_mm=100.0,
        governing_reason="dictionary fixture",
    )
    engine = make_detailing_engine(
        detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
        ranking_mode="style",
    )
    return section, (hoop,), engine


@pytest.fixture(scope="module")
def pb80_catalog(tmp_path_factory):
    section, hoops, engine = _pb80_context()
    data = build_layout_dictionary(
        section, hoops, engine, first_diameter=None, max_layers=3,
        face="top", preserve_support_alternatives=True,
    )
    path = write_layout_dictionary(tmp_path_factory.mktemp("layout-dictionary") / "pb80.json", data)
    bank_data = build_layout_dictionary_bank([data])
    bank_path = write_layout_dictionary_bank(path.parent / "pb80.bank.json", bank_data)
    return path, bank_path, section, hoops


def _layout_state(layouts):
    return tuple(
        (
            signature(item),
            item.provided_area_mm2,
            item.required_area_mm2,
            item.clear_spacing_mm,
            item.row_centers_mm,
            item.row_x_mm,
            item.effective_depth_mm,
            item.constraint_sources,
        )
        for item in layouts
    )


def _memoize_exact_geometry(engine):
    original = engine._resolve_physical_layout
    cache = {}

    def resolve(section, hoop, design, *, face):
        key = (
            section,
            hoop.diameter_mm,
            face,
            tuple((row.count, row.diameter_mm, row.role, row.credited_area_mm2) for row in design.rows),
        )
        cached = cache.get(key)
        if cached is not None:
            return replace(cached, required_area_mm2=design.required_area_mm2)
        resolved = original(section, hoop, design, face=face)
        cache[key] = resolved
        return resolved

    engine._resolve_physical_layout = resolve


def test_dictionary_matches_r6_oracle_at_all_area_boundaries_and_real_queries(pb80_catalog):
    path, _bank_path, section, hoops = pb80_catalog
    dictionary = LayoutDictionary.load(path)
    engine = make_detailing_engine(
        detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
        ranking_mode="style",
    )
    _memoize_exact_geometry(engine)
    queries = set()
    for upper in dictionary.data["upper_bounds_mm2"]:
        queries.update((
            max(0.0, math.nextafter(upper, -math.inf)),
            upper,
            math.nextafter(upper, math.inf),
        ))
    queries.update((
        4607.067735915478,
        1767.1226975744653,
        4563.782600443811,
    ))
    geometry_cache = {}
    for required in sorted(queries):
        expected = row_candidates(
            section, hoops, required, engine,
            first_diameter=None, max_layers=3, face="top",
            preserve_support_alternatives=True, geometry_cache=geometry_cache,
        )
        actual = dictionary.query(
            required, section, hoops, engine,
            first_diameter=None, max_layers=3, face="top",
            preserve_support_alternatives=True,
        )
        assert _layout_state(actual) == _layout_state(expected), f"required As {required}"
        assert all(len(item.rows) <= 3 for item in actual)
        assert all(tuple(row.count for row in item.rows) != (4, 3) for item in actual)
        assert all(item.provided_area_mm2 <= 0.02 * section.width_mm * item.effective_depth_mm + 1e-9
                   for item in actual)


def test_lookup_hit_does_not_call_candidate_generator_or_geometry_resolver(pb80_catalog, monkeypatch):
    from src.design import layout_dictionary as dictionary_module

    path, _bank_path, section, hoops = pb80_catalog
    dictionary = LayoutDictionary.load(path)
    engine = make_detailing_engine(
        detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
        ranking_mode="style",
    )

    def fail(*args, **kwargs):
        raise AssertionError("lookup path attempted candidate/geometry generation")

    monkeypatch.setattr(dictionary_module, "oracle_candidates", fail)
    monkeypatch.setattr(engine, "_resolve_physical_layout", fail)
    result = dictionary.query(4607.067735915478, section, hoops, engine, max_layers=3)
    assert result
    assert dictionary.query_audit["row_candidates_calls"] == 0
    assert dictionary.query_audit["physical_layout_resolver_calls"] == 0
    assert dictionary.query_audit["interval_binary_searches"] == 1


def test_context_mismatch_is_an_explicit_miss(pb80_catalog):
    path, _bank_path, section, hoops = pb80_catalog
    dictionary = LayoutDictionary.load(path)
    engine = make_detailing_engine(
        detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
        ranking_mode="style",
    )
    wrong_cover = replace(section, clear_cover_to_stirrup_outer_mm=20.0)
    with pytest.raises(LayoutDictionaryMiss, match="DICT_MISS"):
        dictionary.query(4607.067735915478, wrong_cover, hoops, engine, max_layers=3)
    assert dictionary.query_audit["context_misses"] == 1


def test_runtime_area_above_catalogured_upper_bound_returns_no_candidate(pb80_catalog):
    path, _bank_path, section, hoops = pb80_catalog
    dictionary = LayoutDictionary.load(path)
    engine = make_detailing_engine(
        detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
        ranking_mode="style",
    )
    result = dictionary.query(
        dictionary.data["upper_bounds_mm2"][-1] + 1.0,
        section, hoops, engine, max_layers=3,
    )
    assert result == ()
    assert dictionary.query_audit["area_misses"] == 1


def test_bank_normalization_reuses_only_irrelevant_source_fields_and_ranking_mode(pb80_catalog):
    _path, bank_path, section, hoops = pb80_catalog
    bank = LayoutDictionaryBank.load(bank_path)
    engine = make_detailing_engine(
        detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
        ranking_mode="legacy",
    )
    renamed = replace(
        section,
        name="OTHER-BEAM",
        length_mm=5500.0,
        clear_cover_source="different provenance",
        max_aggregate_size_source="different provenance",
        etabs_longitudinal_cover_source="different provenance",
        profile_source="different provenance",
    )
    expected = row_candidates(renamed, hoops, 1767.1226975744653, engine, max_layers=3)
    actual = bank.query(1767.1226975744653, renamed, hoops, engine, max_layers=3)
    assert _layout_state(actual) == _layout_state(expected)
    assert bank.context_count == 1
    assert bank.query_audit["context_hits"] == 1


@pytest.mark.parametrize(
    "section_change",
    [
        {"clear_cover_to_stirrup_outer_mm": 20.0},
        {"etabs_longitudinal_cover_mm": 20.0},
        {"fy_mpa": 500.0},
        {"detailing_profile": BeamDetailingProfile.NON_SWAY},
    ],
)
def test_bank_rejects_physical_context_changes(section_change, pb80_catalog):
    _path, bank_path, section, hoops = pb80_catalog
    bank = LayoutDictionaryBank.load(bank_path)
    engine = make_detailing_engine(
        detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
        ranking_mode="style",
    )
    changed = replace(section, **section_change)
    with pytest.raises(LayoutDictionaryMiss, match="DICT_MISS"):
        bank.query(1767.1226975744653, changed, hoops, engine, max_layers=3)


def test_engine_bank_hook_has_no_fallback_and_is_r6_only(pb80_catalog, monkeypatch):
    from src.design import row_candidates as candidate_module

    _path, bank_path, section, hoops = pb80_catalog
    bank = LayoutDictionaryBank.load(bank_path)
    expected_engine = make_detailing_engine(
        detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
        ranking_mode="style",
    )
    expected = row_candidates(section, hoops, 1767.1226975744653, expected_engine, max_layers=3)
    engine = make_detailing_engine(
        detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
        ranking_mode="style",
        layout_dictionary_bank=bank,
    )

    def fail(*args, **kwargs):
        raise AssertionError("configured bank attempted recursive generation or geometry resolution")

    monkeypatch.setattr(engine, "_resolve_physical_layout", fail)
    actual = candidate_module.candidates(
        section, hoops, 1767.1226975744653, engine, max_layers=3,
    )
    assert _layout_state(actual) == _layout_state(expected)
    with pytest.raises(DetailingError, match="DICT_MISS"):
        candidate_module.candidates(
            replace(section, width_mm=401.0), hoops, 1767.1226975744653, engine, max_layers=3,
        )
    with pytest.raises(DetailingError, match="DICT_MISS"):
        candidate_module.candidates(
            section, hoops, 1767.1226975744653, engine, max_layers=None,
        )
    assert engine.layout_dictionary_metadata["context_count"] == 1
    assert engine.layout_dictionary_metadata["query_audit"]["context_misses"] == 2
    with pytest.raises(DetailingError, match="LAYOUT_DICTIONARY_REQUIRES_EXPLICIT_R6_ENGINE"):
        make_detailing_engine(
            detailing_policy_id=DRAWING_POLICY_ID,
            detailing_policy_revision_id=DRAWING_POLICY_REVISION_R5_ID,
            layout_dictionary_bank=bank,
        )


def test_fresh_engine_selects_r8_and_rejects_r6_bank(pb80_catalog):
    _path, bank_path, _section, _hoops = pb80_catalog
    assert make_fresh_detailing_engine(DRAWING_POLICY_ID).detailing_policy_revision_id == DRAWING_POLICY_REVISION_R8_ID
    assert make_detailing_engine(DRAWING_POLICY_ID, detailing_policy_revision_id=DRAWING_POLICY_REVISION_R7_ID).detailing_policy_revision_id == DRAWING_POLICY_REVISION_R7_ID
    with pytest.raises(DetailingError, match="LAYOUT_DICTIONARY_REVISION_MISMATCH"):
        make_fresh_detailing_engine(DRAWING_POLICY_ID, layout_dictionary_path=bank_path)


def test_cli_fresh_replay_rejects_r6_bank_and_wrong_policy(pb80_catalog, tmp_path):
    from src.cli import _parser, _replay_payload
    from src.rebar_workbook import PAYLOAD_SCHEMA_VERSION

    _path, bank_path, _section, _hoops = pb80_catalog
    recorded = make_detailing_engine(
        detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
        ranking_mode="style",
    )
    source = tmp_path / "dictionary-replay.json"
    source.write_text(json.dumps({
        "metadata": {
            "schema_version": PAYLOAD_SCHEMA_VERSION,
            "detailing_policy_id": DRAWING_POLICY_ID,
            "detailing_policy_revision_id": recorded.detailing_policy_revision_id,
            "detailing_policy_parameters": recorded.detailing_policy_parameters,
            "ranking_mode": recorded.ranking_mode,
        },
        "raw_rows": [],
    }), encoding="utf-8")
    args = _parser().parse_args([
        "--replay-payload", str(source),
        "--output-dir", str(tmp_path / "banked"),
        "--detailing-policy", DRAWING_POLICY_ID,
        "--layout-dictionary", str(bank_path),
    ])

    with pytest.raises(DetailingError, match="LAYOUT_DICTIONARY_REVISION_MISMATCH"):
        _replay_payload(args)

    wrong_policy = _parser().parse_args([
        "--replay-payload", str(source),
        "--output-dir", str(tmp_path / "wrong-policy"),
        "--detailing-policy", "legacy",
        "--layout-dictionary", str(bank_path),
    ])
    with pytest.raises(DetailingError, match="LAYOUT_DICTIONARY_REQUIRES_DRAWING_POLICY"):
        _replay_payload(wrong_policy)


def test_redetail_metadata_drops_stale_dictionary_provenance_without_bank():
    from src.rebar_workbook import _record_detailing_policy_metadata

    engine = make_detailing_engine(
        detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R6_ID,
        ranking_mode="style",
    )
    metadata = {"layout_dictionary": {"mode": "PRECOMPUTED_PHYSICAL_ROW_LOOKUP", "context_count": 29}}

    _record_detailing_policy_metadata(metadata, engine)

    assert "layout_dictionary" not in metadata
