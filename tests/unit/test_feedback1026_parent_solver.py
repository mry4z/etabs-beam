from dataclasses import asdict, replace
from collections import OrderedDict

from src.core.beam_line_span_analyzer import BeamLine, BeamLineSpanResult, BeamSpan
from src.core.geometry_extractor import BeamGeometry
from src.design.detailing import BeamDetailingInput, BeamDetailingProfile, BeamRegionDemand, BeamSection, make_detailing_engine
from src.design.constructability import SupportFaceContext, RuleCheck
from src.design.offline_style import DRAWING_POLICY_ID, DRAWING_POLICY_REVISION_R7_ID
from src.design.parent_context import ParentSpanContext
from src.design.provider import BeamDesignResult, DesignStation
from src.design.row_solver import coordinate_parent_line, _parent_cached_candidates, _parent_candidate_blockers
from src.design.equivalent_steel import _parent_zone_lengths
from src.excel_span_adapter import build_span_schedule
from src.rebar_workbook import build_payload_from_schedule, redetail_payload
from src.rebar_workbook import _coordinate_results_by_beam_line


def _engine():
    return make_detailing_engine(DRAWING_POLICY_ID, detailing_policy_revision_id=DRAWING_POLICY_REVISION_R7_ID)


def _data(name, depth, offset, length, *, parent=True):
    section = BeamSection(name, 300, depth, 40, 30, 420, 420, 25, length,
        BeamDetailingProfile.SB_ORDINARY_NO_CH18, beam_role="SB", actual_frame_type="Non Sway")
    demands = tuple(BeamRegionDemand(region, station, 450, 550, .18, 0, 0)
                    for region, station in zip(("left", "middle", "right"), (0, length / 2, length)))
    context = ParentSpanContext("F1-SB1-1", 7000, offset, 700, 800) if parent else None
    return BeamDetailingInput(section, demands, parent_context=context)


def test_common_bottom_and_hoop_survive_section_checks_and_external_support():
    engine = _engine()
    inputs = (_data("A", 700, 0, 5000), _data("B", 800, 5000, 2000),
              _data("next", 700, 0, 5000, parent=False))
    initial = tuple(engine.design(data, prepare_coordination_pool=False) for data in inputs)
    results = coordinate_parent_line(initial, ((0, 1), (2,)), engine.project.default_continuity_option, engine)
    signatures = [tuple((row.count, row.diameter_mm, row.role) for row in item.regions[0].bottom.rows)
                  for item in results[:2]]
    assert signatures[0] == signatures[1]
    hoops = [(region.stirrup.diameter_mm, region.stirrup.legs, region.stirrup.spacing_mm)
             for item in results[:2] for region in item.regions]
    assert len(set(hoops)) == 1
    assert results[0].regions[-1].top.rows[0].diameter_mm == results[1].regions[0].top.rows[0].diameter_mm
    assert results[1].regions[-1].top.rows[0].diameter_mm == results[2].regions[0].top.rows[0].diameter_mm
    assert all(item.coordination_status == "PARENT_COORDINATION_VERIFIED" for item in results)
    assert results[0].coordination_audit[-1]["strategy"] == "PARENT_R7_STRUCTURED_PROPOSALS"
    assert results[0].coordination_audit[-1]["catalog_exhaustive"] is False
    assert "ANCHORAGE_UNVERIFIED" in results[0].warnings


def test_smf_parent_uses_true_faces_and_has_compliant_interface_gap():
    engine = _engine()
    inputs = []
    for name, depth, offset, length in (("left", 700, 0, 5000), ("right", 800, 5000, 2000)):
        section = BeamSection(name, 400, depth, 40, 30, 420, 420, 25, length,
                              BeamDetailingProfile.SMF, beam_role="PB", actual_frame_type="Sway Special")
        demands = tuple(BeamRegionDemand(region, station, 1200, 1300, .3, 0, 0)
                        for region, station in zip(("left", "middle", "right"), (0, length/2, length)))
        inputs.append(BeamDetailingInput(section, demands,
            left_support=SupportFaceContext("I", 100, source="fixture"),
            right_support=SupportFaceContext("J", 6900, source="fixture"),
            parent_context=ParentSpanContext("F1-PB1-1", 7000, offset, 700, 800)))
    initial = tuple(engine.design(data, prepare_coordination_pool=False) for data in inputs)
    left, right = coordinate_parent_line(initial, ((0, 1),), engine.project.default_continuity_option, engine)
    assert [row.diameter_mm for row in left.regions[0].bottom.rows] == [row.diameter_mm for row in right.regions[0].bottom.rows]
    assert [row.count for row in left.regions[0].bottom.rows] == [row.count for row in right.regions[0].bottom.rows]
    assert len({region.stirrup.diameter_mm for item in (left, right) for region in item.regions}) == 1
    assert [(r.stirrup.spacing_mm, r.stirrup.legs) for r in left.regions] == [
        (r.stirrup.spacing_mm, r.stirrup.legs) for r in right.regions]
    before = max(h.location_mm for h in left.constructability.hoop_positions)
    after = 5000 + min(h.location_mm for h in right.constructability.hoop_positions)
    assert after - before <= left.regions[1].stirrup.spacing_mm
    assert _parent_zone_lengths(right.input_data.parent_context, 2000) == {"left": 0, "middle": 400, "right": 1600}
    assert not any(h.is_first_from_support and abs(h.location_mm - 5000) < 1e-9
                   for h in left.constructability.hoop_positions)


def test_failed_parent_does_not_erase_neighbor_results(monkeypatch):
    engine = _engine()
    normal = engine.design(_data("neighbor", 700, 0, 5000, parent=False), prepare_coordination_pool=False)
    part = engine.design(_data("part", 700, 0, 5000), prepare_coordination_pool=False)
    base = {"story": "F1", "beam_type": "SB", "beam_line": "F1-SB1"}
    contexts = [
        ({**base, "span_no": 1, "object_part_index": 0}, None),
        ({**base, "span_no": 2, "object_part_index": 1, "parent_segment_count": 2}, None),
        ({**base, "span_no": 2, "object_part_index": 2, "parent_segment_count": 2}, None),
        ({**base, "span_no": 3, "object_part_index": 0}, None),
    ]
    results, errors = _coordinate_results_by_beam_line([normal, part, None, normal], contexts, engine)
    assert results[0] is not None and results[3] is not None
    assert results[0].coordination_status == "NEIGHBOR_COORDINATION_REVIEW_REQUIRED"
    assert results[3].coordination_status == "NEIGHBOR_COORDINATION_REVIEW_REQUIRED"
    assert results[1] is None and errors[1] == "PARENT_COORDINATION_INCOMPLETE_SECTION"


def test_parent_candidate_cache_keys_physical_hoop_inputs(monkeypatch):
    from src.design.detailing import StirrupDesign
    from src.design import row_solver
    engine, data = _engine(), _data("A", 700, 0, 5000)
    engine._parent_candidate_cache = OrderedDict()
    calls = []
    def fake(section, hoops, required, engine, **kwargs):
        calls.append((section, hoops, required, kwargs))
        return ("candidate",)
    monkeypatch.setattr(row_solver, "candidates", fake)
    hoop = StirrupDesign(10, 100, 2, 1, 1, 200, "test")
    args = dict(max_layers=3, face="top", first_diameter=20)
    assert _parent_cached_candidates(data.section, (hoop,), 1000, engine, **args) == ("candidate",)
    assert _parent_cached_candidates(data.section, (replace(hoop, spacing_mm=150),), 1000, engine, **args) == ("candidate",)
    assert len(calls) == 1
    _parent_cached_candidates(data.section, (replace(hoop, legs=4),), 1000, engine, **args)
    _parent_cached_candidates(replace(data.section, width_mm=350), (hoop,), 1000, engine, **args)
    _parent_cached_candidates(data.section, (hoop,), 1100, engine, **args)
    _parent_cached_candidates(data.section, (replace(hoop, diameter_mm=12),), 1000, engine, **args)
    _parent_cached_candidates(data.section, (hoop,), 1000, engine, **{**args, "face": "bottom"})
    _parent_cached_candidates(replace(data.section, clear_cover_to_stirrup_outer_mm=45), (hoop,), 1000, engine, **args)
    assert len(calls) == 7


def test_parent_cache_preserves_full_sb_and_smf_results(monkeypatch):
    from src.design import row_solver
    sb = (_data("A", 700, 0, 5000), _data("B", 800, 5000, 2000),
          _data("next", 700, 0, 5000, parent=False))
    pb = []
    for name, depth, offset, length in (("left", 700, 0, 5000), ("right", 800, 5000, 2000)):
        section = BeamSection(name, 400, depth, 40, 30, 420, 420, 25, length,
                              BeamDetailingProfile.SMF, beam_role="PB", actual_frame_type="Sway Special")
        demands = tuple(BeamRegionDemand(region, station, 1200, 1300, .3, 0, 0)
                        for region, station in zip(("left", "middle", "right"), (0, length/2, length)))
        pb.append(BeamDetailingInput(section, demands,
            left_support=SupportFaceContext("I", 100, source="fixture"),
            right_support=SupportFaceContext("J", 6900, source="fixture"),
            parent_context=ParentSpanContext("F1-PB1-1", 7000, offset, 700, 800)))
    def solve(inputs, groups):
        engine = _engine()
        initial = tuple(engine.design(data, prepare_coordination_pool=False) for data in inputs)
        return tuple(asdict(result) for result in coordinate_parent_line(
            initial, groups, engine.project.default_continuity_option, engine))
    cached = (solve(sb, ((0, 1), (2,))), solve(pb, ((0, 1),)))
    monkeypatch.setattr(row_solver, "_parent_cached_candidates",
                        lambda section, hoops, required, engine, **kw: row_solver.candidates(
                            section, hoops, required, engine, **kw))
    uncached = (solve(sb, ((0, 1), (2,))), solve(pb, ((0, 1),)))
    assert cached == uncached


def test_parent_allows_only_program_determined_frame_source_review():
    engine = _engine()
    section = BeamSection("pb", 400, 700, 40, 30, 420, 420, 25, 7000,
                          BeamDetailingProfile.SMF, beam_role="PB", actual_frame_type="Sway Special")
    regions = tuple(BeamRegionDemand(name, x, 1200, 1300, .3, 0, 0)
                    for name, x in zip(("left", "middle", "right"), (0, 3500, 7000)))
    data = BeamDetailingInput(section, regions, left_support=SupportFaceContext("I", 100, source="fixture"),
                              right_support=SupportFaceContext("J", 6900, source="fixture"))
    result = engine.design(data, prepare_coordination_pool=False)
    frame_review = RuleCheck("SMF-FRAME-TYPE", "ACI", "SMF_DESIGN_SOURCE_UNVERIFIED", actual="Program Determined")
    code_review = RuleCheck("SMF-DESIGN-CODE", "ACI", "SMF_DESIGN_SOURCE_UNVERIFIED", actual="Other code")
    allowed = replace(result, constructability=replace(result.constructability, status="REVIEW", checks=(frame_review,)))
    assert not _parent_candidate_blockers(allowed)
    refused = replace(allowed, constructability=replace(allowed.constructability, checks=(frame_review, code_review)))
    assert _parent_candidate_blockers(refused) == {"SMF_DESIGN_SOURCE_UNVERIFIED"}
    assert frame_review.status == "SMF_DESIGN_SOURCE_UNVERIFIED"


def test_workbook_merges_runs_and_roundtrips_parent_context():
    points = (0, 3000, 6000, 8000)
    beams, designs = [], {}
    for index, (start, end, section) in enumerate(zip(points, points[1:], ("A", "A", "B"))):
        name = str(index + 1)
        beam = BeamGeometry(name, name, "F1", str(index), str(index + 1),
                            (start, 0, 0), (end, 0, 0), section, 700 if section == "A" else 800)
        beams.append(beam)
        stations = tuple(DesignStation(x, "T", 450, "B", 550, "V", .18, "TL", 0, "TV", 0,
                          top_total_area=450, bottom_total_area=550, source_object=name)
                         for x in range(0, end - start + 1, 500))
        designs[name] = BeamDesignResult(name, stations, source_objects=(name,))
    points3 = tuple((x, 0, 0) for x in points)
    span = BeamSpan("F1", "SB", 1, 1, ("1", "2", "3"), points3, False, False)
    line = BeamLine("F1", "SB", 1, "A", span.source_objects, points3, False)
    schedule = build_span_schedule(BeamLineSpanResult((line,), (span,), (), ()), beams, designs,
                                   detailing_policy_id=DRAWING_POLICY_ID)
    def section_loader(name):
        return dict(width_mm=300, depth_mm=700 if name == "A" else 800,
                    etabs_longitudinal_cover_mm=45, etabs_longitudinal_cover_source="fixture",
                    fc_mpa=30, fy_mpa=420, fyt_mpa=420)
    payload = build_payload_from_schedule(schedule, beams, section_loader=section_loader,
        frame_types={beam.name: "Non Sway" for beam in beams}, metadata={},
        clear_cover_to_stirrup_outer_mm=40, clear_cover_source="fixture",
        max_aggregate_size_mm=25, max_aggregate_size_source="fixture", engine=_engine())
    assert [row["source_objects"] for row in payload["raw_rows"]] == ["1, 2", "3"]
    assert all(row["parent_continuity_status"] == "PARENT_COORDINATION_VERIFIED" for row in payload["raw_rows"])
    assert payload["raw_rows"][1]["detailing_context"]["parent_context"]["segment_start_mm"] == 6000
    replayed = redetail_payload(payload, engine=_engine())
    assert all(row["parent_continuity_status"] == "PARENT_COORDINATION_VERIFIED" for row in replayed["raw_rows"])
