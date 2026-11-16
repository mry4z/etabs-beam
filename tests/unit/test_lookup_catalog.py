from dataclasses import replace
import gzip
import math

import pytest

from tests.unit.test_layout_dictionary import _pb80_context, _layout_state
from src.design.detailing import DRAWING_POLICY_REVISION_R8_ID, LongitudinalDesign, PhysicalRow, DetailingError, make_detailing_engine
from src.design.lookup_catalog import LookupCatalog
from src.design.row_candidates import candidates


@pytest.mark.parametrize("width,face,first,layers", [
    (300, "top", None, 3), (400, "bottom", 20, 2),
])
def test_compiled_lookup_matches_r8_boundaries_and_roundtrip(tmp_path, monkeypatch, width, face, first, layers):
    section, hoops, old = _pb80_context()
    section = replace(section, width_mm=width)
    engine = make_detailing_engine(detailing_policy_id=old.detailing_policy_id,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R8_ID, ranking_mode="style")
    catalog = LookupCatalog()
    catalog.prepare(section, hoops, engine, first_diameter=first, max_layers=layers, face=face)
    records = next(iter(catalog.contexts.values()))["records"]
    areas = sorted({item[0]["provided_area_mm2"] for item in records.values() if item})
    samples = {0.0, 1767.1226975744653, 4607.067735915478, 10000.0, 11000.0}
    for area in areas[::max(1, len(areas) // 15)]:
        upper = area + 1e-9
        samples.update((math.nextafter(upper, -math.inf), upper, math.nextafter(upper, math.inf)))
    expected = {required: _layout_state(candidates(section, hoops, required, engine,
        first_diameter=first, max_layers=layers, face=face)) for required in samples}
    path = catalog.save(tmp_path / "catalog.json.gz")
    catalog = LookupCatalog.load(path)
    def forbidden(*args, **kwargs):
        raise AssertionError("query resolved geometry")
    monkeypatch.setattr(engine, "_resolve_physical_layout", forbidden)
    for required in samples:
        actual = catalog.query(required, section, hoops, engine,
            first_diameter=first, max_layers=layers, face=face)
        assert _layout_state(actual) == expected[required], required



def test_geometry_hits_reproduce_success_and_failure(tmp_path):
    section, hoops, engine = _pb80_context()
    catalog = LookupCatalog()
    catalog.prepare(section, hoops, engine, max_layers=3)
    catalog = LookupCatalog.load(catalog.save(tmp_path / "geometry.gz"))
    records = next(iter(catalog.contexts.values()))["geometry"]
    for desired_failure in (False, True):
        (diameter, rows), record = next((key, value) for key, value in records.items()
            if ("error" in value) == desired_failure)
        design = LongitudinalDesign(rows=tuple(PhysicalRow(*row) for row in rows),
                                    required_area_mm2=1234, constraint_sources=("CALLER",))
        if desired_failure:
            with pytest.raises(DetailingError) as expected:
                engine._resolve_physical_layout(section, hoops[0], design, face="top")
            with pytest.raises(DetailingError, match=str(expected.value)):
                catalog.resolve_geometry(section, hoops[0], design, face="top", engine=engine)
        else:
            expected = engine._resolve_physical_layout(section, hoops[0], design, face="top")
            assert catalog.resolve_geometry(section, hoops[0], design, face="top", engine=engine) == expected


def test_templates_reused_across_depths_after_reload(tmp_path, monkeypatch):
    section, hoops, engine = _pb80_context()
    shallow = replace(section, depth_mm=300)
    expected = _layout_state(candidates(shallow, hoops, 2000, engine, max_layers=3))
    catalog = LookupCatalog()
    catalog.prepare(section, hoops, engine, max_layers=3)
    catalog = LookupCatalog.load(catalog.save(tmp_path / "shared.gz"))
    def forbidden(*args, **kwargs):
        raise AssertionError("shared geometry was recomputed")
    monkeypatch.setattr(engine, "_resolve_physical_layout_uncached", forbidden)
    catalog.prepare(shallow, hoops, engine, max_layers=3)
    assert _layout_state(catalog.query(2000, shallow, hoops, engine, max_layers=3)) == expected


def test_r9_uses_r8_context_and_resolves_prepared_geometry():
    from src.design.offline_style import DRAWING_POLICY_REVISION_R9_ID
    section, hoops, old = _pb80_context()
    catalog = LookupCatalog()
    r8 = make_detailing_engine(detailing_policy_id=old.detailing_policy_id,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R8_ID, ranking_mode="style")
    expected = candidates(section, hoops, 2000, r8, max_layers=2)
    catalog.prepare(section, hoops, r8, max_layers=2)
    r9 = make_detailing_engine(detailing_policy_id=old.detailing_policy_id,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R9_ID, ranking_mode="style",
        candidate_lookup_provider=catalog)
    catalog.prepare(section, hoops, r9, max_layers=2)
    assert len(catalog.contexts) == 1
    assert _layout_state(candidates(section, hoops, 2000, r9, max_layers=2)) == _layout_state(expected)
    with pytest.raises(DetailingError, match="DICT_MISS"):
        candidates(section, hoops, 2000, r9, max_layers=1)
    assert r9._resolve_physical_layout(section, hoops[0], expected[0], face="top") == expected[0]


def test_templates_resolve_before_any_context_query(tmp_path, monkeypatch):
    section, hoops, engine = _pb80_context()
    design = LongitudinalDesign(rows=(PhysicalRow(4, 20),), required_area_mm2=1000,
                                constraint_sources=("CALLER",))
    expected = engine._resolve_physical_layout(section, hoops[0], design, face="bottom")
    catalog = LookupCatalog()
    catalog.prepare(section, hoops, engine, max_layers=3)
    catalog.contexts.clear()
    catalog = LookupCatalog.load(catalog.save(tmp_path / "templates.gz"))
    def forbidden(*args, **kwargs):
        raise AssertionError("geometry was recomputed")
    monkeypatch.setattr(engine, "_resolve_physical_layout_uncached", forbidden)
    assert catalog.resolve_geometry(section, hoops[0], design, face="bottom", engine=engine) == expected


def test_large_areas_are_not_clipped():
    from src.design.detailing import BeamDetailingProfile
    section, hoops, old = _pb80_context()
    section = replace(section, width_mm=600, depth_mm=1200, detailing_profile=BeamDetailingProfile.OMF)
    engine = make_detailing_engine(detailing_policy_id=old.detailing_policy_id,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R8_ID, ranking_mode="style")
    catalog = LookupCatalog()
    catalog.prepare(section, hoops, engine, max_layers=3)
    actual = catalog.query(11000, section, hoops, engine, max_layers=3)
    assert actual and max(item.provided_area_mm2 for item in actual) > 11000
    assert _layout_state(actual) == _layout_state(candidates(section, hoops, 11000, engine, max_layers=3))


def test_repeated_hits_do_not_rebuild_policy_json(monkeypatch):
    import src.design.lookup_catalog as module
    section, hoops, engine = _pb80_context()
    catalog = LookupCatalog()
    catalog.prepare(section, hoops, engine, max_layers=3)
    selected = catalog.query(2000, section, hoops, engine, max_layers=3)[0]
    catalog.resolve_geometry(section, hoops[0], selected, face="top", engine=engine)
    def forbidden(*args, **kwargs):
        raise AssertionError("policy JSON rebuilt")
    monkeypatch.setattr(module, "_engine_context", forbidden)
    assert catalog.query(2000, section, hoops, engine, max_layers=3)
    assert catalog.resolve_geometry(section, hoops[0], selected, face="top", engine=engine) == selected


def test_prebuilt_indices_use_shared_templates_without_geometry_resolution(tmp_path, monkeypatch):
    section, hoops, engine = _pb80_context()
    catalog = LookupCatalog()
    catalog.prepare(section, hoops, engine, max_layers=3)
    expected = _layout_state(catalog.query(2000, section, hoops, engine, max_layers=3))
    index_path = catalog.save_indices(tmp_path / "indices.gz")
    catalog.contexts.clear()
    geometry_path = catalog.save(tmp_path / "templates.gz")
    loaded = LookupCatalog.load(geometry_path).load_indices(index_path, only_keys=set())
    assert not loaded.contexts
    loaded.load_indices(index_path)
    loaded.prepare(section, hoops, engine, max_layers=2)
    mixed = loaded.save_indices(tmp_path / "mixed.gz")
    fast = loaded.save_indices(tmp_path / "mixed_fast.gz", compresslevel=1)
    with gzip.open(mixed, "rb") as first, gzip.open(fast, "rb") as second:
        assert first.read() == second.read()
    assert LookupCatalog().load_indices(mixed).contexts == LookupCatalog().load_indices(fast).contexts
    assert len(LookupCatalog().load_indices(mixed).contexts) == 2
    loaded = LookupCatalog.load(loaded.save_compact(tmp_path / "worker.gz"))
    def forbidden(*args, **kwargs):
        raise AssertionError("resolver called")
    monkeypatch.setattr(engine, "_resolve_physical_layout_uncached", forbidden)
    result = loaded.query(2000, section, hoops, engine, max_layers=3)
    assert _layout_state(result) == expected
    monkeypatch.setattr(loaded, "resolve_geometry", forbidden)
    assert loaded.query(2000, section, hoops, engine, max_layers=3) is result


def test_shared_geometry_base_preserves_legs_face_and_root_controls():
    from src.design.detailing import BeamDetailingProfile
    section, hoops, old = _pb80_context()
    section = replace(section, width_mm=500, detailing_profile=BeamDetailingProfile.OMF)
    engine = make_detailing_engine(detailing_policy_id=old.detailing_policy_id,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R8_ID, ranking_mode="style")
    catalog = LookupCatalog()
    for legs in (2, 4, 6):
        hoop = replace(hoops[0], legs=legs)
        for face, preserve in (("top", True), ("bottom", False)):
            for first in (None, 16, 20, 25):
                controls = dict(first_diameter=first, max_layers=3, face=face,
                                preserve_support_alternatives=preserve)
                catalog.prepare(section, (hoop,), engine, **controls)
                for required in (0, 1000, 3500, 7000):
                    assert _layout_state(catalog.query(required, section, (hoop,), engine, **controls)) == \
                        _layout_state(candidates(section, (hoop,), required, engine, **controls))


def test_query_cache_is_exact_and_avoids_rematerialization(monkeypatch):
    import src.design.lookup_catalog as module
    from src.design.layout_dictionary import LayoutDictionaryMiss
    section, hoops, engine = _pb80_context()
    other = replace(section, depth_mm=700)
    catalog = LookupCatalog()
    catalog.prepare(section, hoops, engine, max_layers=3)
    catalog.prepare(other, hoops, engine, max_layers=3)
    result = catalog.query(2000, section, hoops, engine, max_layers=3)
    before = catalog.metadata()["query_audit"]
    def forbidden(*args, **kwargs):
        raise AssertionError("cached result was rematerialized or sorted")
    with monkeypatch.context() as patch:
        patch.setattr(module, "_materialize", forbidden)
        patch.setattr(module, "_candidate_sort_key", forbidden)
        assert catalog.query(2000, section, hoops, engine, max_layers=3) is result
    after = catalog.metadata()["query_audit"]
    assert after["query_cache_hits"] == before["query_cache_hits"] + 1
    assert after["materialized_count"] == before["materialized_count"]
    assert after["sort_calls"] == before["sort_calls"]
    catalog.query(2000, other, hoops, engine, max_layers=3)
    catalog.query(2000.0000000000002, section, hoops, engine, max_layers=3)
    assert catalog.metadata()["query_audit"]["query_cache_misses"] == before["query_cache_misses"] + 2
    missing = replace(section, depth_mm=777)
    for _ in range(2):
        with pytest.raises(LayoutDictionaryMiss):
            catalog.query(2000, missing, hoops, engine, max_layers=3)
    assert catalog.metadata()["query_audit"]["context_misses"] == 2


def test_query_cache_stores_empty_results_and_is_bounded():
    section, hoops, engine = _pb80_context()
    catalog = LookupCatalog()
    catalog.prepare(section, hoops, engine, max_layers=1)
    assert catalog.query(100000, section, hoops, engine, max_layers=1) == ()
    assert catalog.query(100000, section, hoops, engine, max_layers=1) == ()
    assert catalog.metadata()["query_audit"]["query_cache_hits"] == 1
    for demand in range(100001, 100513):
        catalog.query(demand, section, hoops, engine, max_layers=1)
    assert catalog.metadata()["query_cache_entries"] == 512
    before = catalog.metadata()["query_audit"]["query_cache_misses"]
    catalog.query(100000, section, hoops, engine, max_layers=1)
    assert catalog.metadata()["query_audit"]["query_cache_misses"] == before + 1


def test_span_geometry_cache_preserves_inputs_failures_and_lifetime(monkeypatch):
    from src.design.layout_dictionary import LayoutDictionaryMiss
    from src.design.offline_style import DRAWING_POLICY_REVISION_R9_ID
    section, hoops, old = _pb80_context()
    catalog = LookupCatalog()
    r8 = make_detailing_engine(detailing_policy_id=old.detailing_policy_id,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R8_ID, ranking_mode="style")
    catalog.prepare(section, hoops, r8, max_layers=3)
    other_section = replace(section, depth_mm=700)
    catalog.prepare(other_section, hoops, r8, max_layers=3)
    r9 = make_detailing_engine(detailing_policy_id=old.detailing_policy_id,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R9_ID, ranking_mode="style",
        candidate_lookup_provider=catalog)
    design = LongitudinalDesign(rows=(PhysicalRow(4, 20),), required_area_mm2=1000)
    expected = r8._resolve_physical_layout(section, hoops[0], design, face="top")
    context = next(iter(catalog.contexts.values()))
    (_diameter, bad_rows), _ = next((key, record) for key, record in context["geometry"].items()
                                      if "error" in record)
    bad = LongitudinalDesign(rows=tuple(PhysicalRow(*row) for row in bad_rows))
    missing = LongitudinalDesign(rows=(PhysicalRow(100, 25),))
    before = catalog.metadata()["query_audit"]
    with r9._r7_span_cache_scope(object()):
        resolved = r9._resolve_physical_layout(section, hoops[0], design, face="top")
        assert resolved == expected
        assert r9._resolve_physical_layout(section, hoops[0], design, face="top") is resolved
        # An already-resolved input remains a distinct cache key.
        assert r9._resolve_physical_layout(section, hoops[0], resolved, face="top") == resolved
        changed_coordinates = replace(resolved, row_x_mm=((1.0, 2.0, 3.0, 4.0),))
        assert r9._resolve_physical_layout(section, hoops[0], changed_coordinates, face="top") == expected
        other_expected = r8._resolve_physical_layout(other_section, hoops[0], design, face="top")
        assert r9._resolve_physical_layout(other_section, hoops[0], design, face="top") == other_expected
        # Hoop and face metadata are part of the complete key.
        assert r9._resolve_physical_layout(section, replace(hoops[0], spacing_mm=110), design, face="top") == expected
        assert r9._resolve_physical_layout(section, hoops[0], design, face="bottom") == expected
        failure_messages = []
        for _ in range(2):
            with pytest.raises(DetailingError) as failure:
                r9._resolve_physical_layout(section, hoops[0], bad, face="top")
            failure_messages.append(str(failure.value))
        assert failure_messages[0] == failure_messages[1]
        for _ in range(2):
            with pytest.raises(LayoutDictionaryMiss):
                r9._resolve_physical_layout(section, hoops[0], missing, face="top")
        audit = catalog.metadata()["query_audit"]
        assert audit["geometry_span_cache_hits"] - before["geometry_span_cache_hits"] == 2
        assert audit["geometry_actual_resolves"] - before["geometry_actual_resolves"] == 9
        assert audit["geometry_misses"] - before["geometry_misses"] == 2
    with r9._r7_span_cache_scope(object()):
        assert r9._resolve_physical_layout(section, hoops[0], design, face="top") == expected
    assert catalog.metadata()["query_audit"]["geometry_actual_resolves"] == audit["geometry_actual_resolves"] + 1


def test_span_geometry_cache_is_bounded():
    from src.design.offline_style import DRAWING_POLICY_REVISION_R9_ID
    section, hoops, old = _pb80_context()
    catalog = LookupCatalog()
    r8 = make_detailing_engine(detailing_policy_id=old.detailing_policy_id,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R8_ID, ranking_mode="style")
    catalog.prepare(section, hoops, r8, max_layers=3)
    r9 = make_detailing_engine(detailing_policy_id=old.detailing_policy_id,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R9_ID, ranking_mode="style",
        candidate_lookup_provider=catalog)
    with r9._r7_span_cache_scope(object()):
        for demand in range(4097):
            design = LongitudinalDesign(rows=(PhysicalRow(4, 20),), required_area_mm2=demand)
            r9._resolve_physical_layout(section, hoops[0], design, face="top")
        assert len(r9._r7_span_caches["r9_lookup_geometry"]) == 4096
        assert catalog.metadata()["query_audit"]["geometry_span_cache_evictions"] == 1
        first = LongitudinalDesign(rows=(PhysicalRow(4, 20),), required_area_mm2=0)
        r9._resolve_physical_layout(section, hoops[0], first, face="top")
        assert catalog.metadata()["query_audit"]["geometry_span_cache_misses"] == 4098


def test_unprepared_material_and_cover_miss_then_match_r8():
    from src.design.layout_dictionary import LayoutDictionaryMiss
    section, hoops, old = _pb80_context()
    section = replace(section, width_mm=550, depth_mm=670)
    engine = make_detailing_engine(detailing_policy_id=old.detailing_policy_id,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R8_ID, ranking_mode="style")
    catalog = LookupCatalog()
    catalog.prepare(section, hoops, engine, max_layers=2)
    variants = (replace(section, fc_mpa=30), replace(section, fy_mpa=500),
                replace(section, clear_cover_to_stirrup_outer_mm=45))
    for variant in variants:
        with pytest.raises(LayoutDictionaryMiss, match="DICT_MISS"):
            catalog.query(2000, variant, hoops, engine, max_layers=2)
        expected = _layout_state(candidates(variant, hoops, 2000, engine, max_layers=2))
        catalog.prepare(variant, hoops, engine, max_layers=2)
        assert _layout_state(catalog.query(2000, variant, hoops, engine, max_layers=2)) == expected


def test_jsonl_key_filter_skips_unselected_memberships_and_handles_fallback(tmp_path, monkeypatch):
    import gzip
    import json
    import src.design.lookup_catalog as module
    selected = 'quoted "key" \\ path\nline'
    skipped = {"key": 'not selected "quoted" \\ slash\nline', "membership": [[0, 1, [[2, 20]], 0]] * 2000}
    normal = {"key": selected, "membership": [[0, 1, [[2, 20]], 0]]}
    reordered = {"membership": [[0, 2, [[2, 25]], 0]], "key": "reordered"}
    path = tmp_path / "indices.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write(json.dumps({"format": "beam-row-interval-indices-jsonl@1"}) + "\n")
        for item in (skipped, normal, reordered):
            stream.write(json.dumps(item, separators=(",", ":")) + "\n")
        stream.write('{"key":"first","membership":[],"key":"duplicate","membership":[]}\n')
    original = module.json.loads
    parsed = []
    def tracked(value, *args, **kwargs):
        parsed.append(value)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(module.json, "loads", tracked)
    result = module.LookupCatalog().load_indices(path, only_keys={selected, "reordered", "duplicate"})
    assert set(result.contexts) == {selected, "reordered", "duplicate"}
    assert not any('not selected' in value for value in parsed)
    assert any('reordered' in value for value in parsed)


def test_context_key_cache_uses_only_proven_normalization(monkeypatch):
    import src.design.lookup_catalog as module
    section, hoops, engine = _pb80_context()
    catalog = LookupCatalog()
    controls = dict(first_diameter=None, max_layers=3, face="top", preserve_support_alternatives=True)
    original = module.context_key
    calls = []
    def tracked(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "context_key", tracked)
    initial = catalog._key(section, hoops, engine, **controls)
    renamed = replace(section, name="different", length_mm=1234.0,
        profile_source="other", clear_cover_source="other",
        max_aggregate_size_source="other", etabs_longitudinal_cover_source="other")
    assert catalog._key(renamed, hoops, engine, **controls) == initial
    assert len(calls) == 1
    changed = replace(section, depth_mm=section.depth_mm + 50)
    assert catalog._key(changed, hoops, engine, **controls) != initial
    # JSON contexts distinguish integer and floating representation: avoid
    # confusing equal Python numbers with equal canonical context strings.
    integer_width = replace(section, width_mm=int(section.width_mm))
    assert catalog._key(integer_width, hoops, engine, **controls) != initial
    assert len(calls) == 3


def test_template_resolve_reuses_only_unchanged_checked_geometry():
    section, hoops, old = _pb80_context()
    engine = make_detailing_engine(detailing_policy_id=old.detailing_policy_id,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R8_ID, ranking_mode="style")
    catalog = LookupCatalog()
    hoop = hoops[0]
    other_hoop = replace(hoop, diameter_mm=10)
    catalog.prepare_geometry(section, hoop, engine)
    catalog.prepare_geometry(section, other_hoop, engine)
    prefix = catalog._prefix(section, engine)
    raw = LongitudinalDesign(rows=(PhysicalRow(4, 20),), required_area_mm2=1000,
                             constraint_sources=("CALLER",))
    resolved = catalog._template_resolve(section, hoop, raw, face="top", engine=engine, prefix=prefix)
    assert resolved == engine._resolve_physical_layout_uncached(section, hoop, raw, face="top")
    assert resolved is not raw
    assert catalog._template_resolve(section, hoop, resolved, face="top", engine=engine, prefix=prefix) is resolved
    changed = replace(resolved, row_x_mm=((1.0, 2.0, 3.0, 4.0),))
    corrected = catalog._template_resolve(section, hoop, changed, face="top", engine=engine, prefix=prefix)
    assert corrected is not changed
    assert corrected == engine._resolve_physical_layout_uncached(section, hoop, changed, face="top")
    changed_hoop = catalog._template_resolve(section, other_hoop, resolved, face="top", engine=engine, prefix=prefix)
    assert changed_hoop is not resolved
    assert changed_hoop == engine._resolve_physical_layout_uncached(section, other_hoop, resolved, face="top")
    shallow = replace(section, depth_mm=100)
    with pytest.raises(DetailingError, match="ROW_VERTICAL_CLEARANCE_FAILED"):
        catalog._template_resolve(shallow, hoop, resolved, face="top", engine=engine, prefix=prefix)


def test_compact_template_codec_roundtrip_and_v1_extra(tmp_path):
    from src.design.lookup_template_codec import build_data, write_data
    from src.design.layout_dictionary import LayoutDictionaryError, LayoutDictionaryMiss
    section, hoops, engine = _pb80_context()
    original = LookupCatalog()
    original.prepare(section, hoops, engine, max_layers=3)
    old_path = original.save(tmp_path / "v1.json.gz")
    compact_path = tmp_path / "v2.json.gz"
    write_data(compact_path, build_data(original._geometry_templates))
    compact = LookupCatalog.load(compact_path)
    assert original.template_prefixes() == compact.template_prefixes()
    assert all(compact._template_value(key) == value
               for key, value in original._geometry_templates.items())
    success_key = next(key for key, value in original._geometry_templates.items()
                       if not isinstance(value, str))
    prefix, diameter, _ = success_key
    extra_row = PhysicalRow(100, 25)
    extra_key = (prefix, diameter, ((extra_row.count, extra_row.diameter_mm,
                                   extra_row.role, extra_row.credited_area_mm2),))
    assert compact._template_value(extra_key) is None
    extra = LookupCatalog()
    extra._geometry_templates[extra_key] = "{section}/{face}: EXTRA_FAILURE"
    extra_path = extra.save(tmp_path / "extra_v1.json.gz")
    mixed = LookupCatalog.load_many((compact_path, extra_path))
    assert mixed._template_value(extra_key) == extra._geometry_templates[extra_key]
    assert all(mixed._template_value(key) == value for key, value in original._geometry_templates.items())
    mixed.contexts = original.contexts
    expected = original.query(2000, section, hoops, engine, max_layers=3)
    reloaded = LookupCatalog.load(mixed.save_compact(tmp_path / "worker.json.gz"))
    assert reloaded.query(2000, section, hoops, engine, max_layers=3) == expected
    assert reloaded._template_value(extra_key) == extra._geometry_templates[extra_key]
    assert all(reloaded._template_value(key) == value for key, value in original._geometry_templates.items())
    with pytest.raises(LayoutDictionaryError, match="DUPLICATE_GEOMETRY"):
        LookupCatalog.load_many((compact_path, old_path))
    changed = LongitudinalDesign(rows=(PhysicalRow(2, 20, role="hanger"),))
    with pytest.raises(LayoutDictionaryMiss):
        compact.resolve_geometry(section, hoops[0], changed, face="top", engine=engine)
