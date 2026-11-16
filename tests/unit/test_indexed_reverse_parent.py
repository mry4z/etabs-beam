"""R9 reversal and parent-subsection integration regression."""
from src.core.beam_line_span_analyzer import BeamLine, BeamLineSpanResult, BeamSpan
from src.core.geometry_extractor import BeamGeometry
from src.design.constructability import SupportFaceContext
from src.design.detailing import DRAWING_POLICY_ID, make_fresh_detailing_engine
from src.design.equivalent_steel import _parent_zone_lengths
from src.design.provider import BeamDesignResult, DesignStation
from src.excel_span_adapter import build_span_schedule
from src.rebar_workbook import build_payload_from_schedule


def test_r9_reversed_child_uses_parent_zones_and_real_support_faces():
    # B is drawn from parent J toward I. Its original I-end station has the
    # lower demand, so accidentally retaining source I/J is observable.
    beams = (
        BeamGeometry('A', 'A', 'F1', 'P1', 'P2', (0, 0, 0), (4000, 0, 0), 'A', 700),
        BeamGeometry('B', 'B', 'F1', 'P3', 'P2', (7000, 0, 0), (4000, 0, 0), 'B', 800),
    )
    designs = {}
    for name, length in (('A', 4000), ('B', 3000)):
        base = 800 if name == 'A' else 1000
        stations = tuple(DesignStation(location, 'T', base + location / 10, 'B', 550,
            'V', 0.18, 'TL', 0, 'TV', 0, top_total_area=base + location / 10,
            bottom_total_area=550, source_object=name)
            for location in range(0, length + 1, 500))
        designs[name] = BeamDesignResult(name, stations, source_objects=(name,))
    path = ((0, 0, 0), (4000, 0, 0), (7000, 0, 0))
    span = BeamSpan('F1', 'SB', 1, 1, ('A', 'B'), path, False, False)
    line = BeamLine('F1', 'SB', 1, 'A', span.source_objects, path, False)
    schedule = build_span_schedule(BeamLineSpanResult((line,), (span,), (), ()),
        beams, designs, detailing_policy_id=DRAWING_POLICY_ID)
    parts = schedule.records[0].object_parts
    assert len(schedule.records) == 1 and len(parts) == 2
    assert parts[1].design.stations[0].top_total_area == 1300
    assert parts[1].design.stations[-1].top_total_area == 1000

    def section_loader(name):
        return dict(width_mm=300, depth_mm=700 if name == 'A' else 800,
            etabs_longitudinal_cover_mm=45, etabs_longitudinal_cover_source='fixture',
            fc_mpa=30, fy_mpa=420, fyt_mpa=420)

    engine = make_fresh_detailing_engine(DRAWING_POLICY_ID, candidate_lookup='auto')
    faces = (SupportFaceContext('I', 100, source='fixture'),
             SupportFaceContext('J', 6900, source='fixture'))
    payload = build_payload_from_schedule(schedule, beams, section_loader=section_loader,
        frame_types={'A': 'Non Sway', 'B': 'Non Sway'}, metadata={},
        clear_cover_to_stirrup_outer_mm=40, clear_cover_source='fixture',
        max_aggregate_size_mm=25, max_aggregate_size_source='fixture',
        support_face_contexts={span.span_id: faces}, engine=engine)
    first, second = payload['raw_rows']
    assert first['parent_span_id'] == second['parent_span_id'] == 'F1-SB1-1'
    assert [first['parent_offset_mm'], second['parent_offset_mm']] == [0, 4000]
    assert [first['length_mm'], second['length_mm']] == [4000, 3000]
    assert second['middle_top_total_mm2'] == 1300
    assert second['right_top_total_mm2'] == 1150
    for row in (first, second):
        saved = row['detailing_context']
        assert saved['left_support']['face_location_mm'] == 100
        assert saved['right_support']['face_location_mm'] == 6900
        assert saved['parent_context']['parent_length_mm'] == 7000
        assert row['parent_continuity_status'] == 'PARENT_COORDINATION_VERIFIED'
    from src.design.parent_context import ParentSpanContext
    contexts = [ParentSpanContext(**row['detailing_context']['parent_context']) for row in (first, second)]
    assert _parent_zone_lengths(contexts[0], 4000) == {'left': 1400, 'middle': 2600, 'right': 0}
    assert _parent_zone_lengths(contexts[1], 3000) == {'left': 0, 'middle': 1400, 'right': 1600}
    assert all(row['longitudinal_rows'] for row in (first, second))
    assert payload['metadata']['coordination_solver_revision_id'] == 'coordination_20261031_indexed_candidates_v1'
    lookup = payload['metadata']['rebar_performance']['candidate_lookup_statistics']
    assert lookup['query_calls'] > 0
    assert lookup['geometry_template_hits'] > 0
    # V2 source loader retains compressed failure banks; V1 doesn't.
    provider = engine.candidate_lookup_provider
    assert provider._failure_banks
    assert provider.metadata()['query_audit']['physical_layout_computations'] == 0
