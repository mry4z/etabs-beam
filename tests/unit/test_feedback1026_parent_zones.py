from src.design.constructability import build_constructability, SupportFaceContext
from src.design.parent_context import ParentSpanContext


def args():
    return dict(is_smf=True, span_length_mm=1825.25, beam_depth_mm=1000, effective_depth_mm=850,
                smallest_primary_bar_diameter_mm=20, left_support=SupportFaceContext('L', 0, source='test'),
                right_support=SupportFaceContext('R', 9600, source='test'), left_spacing_mm=100,
                middle_spacing_mm=200, right_spacing_mm=100, etabs_design_results_only=True)


def test_parent_projection_and_actual_end():
    a = args()
    c = ParentSpanContext('P', 9600, 7774.75, 950, 1000)
    result = build_constructability(**a, parent_context=c)
    assert [z.name for z in result.zones] == ['smf_right_2h']
    assert result.zones[0].start_mm == 0
    assert result.zones[0].end_mm == 1825.25
    assert all(0 <= h.location_mm <= 1825.25 for h in result.hoop_positions)
    assert [h.location_mm for h in result.hoop_positions if h.is_first_from_support] == [1775.25]
    assert not any(ch.rule_id == 'SMF-DIMENSION-CLEAR-SPAN' for ch in result.checks)


def test_cut_does_not_restart_hoops_and_missing_support():
    a = args()
    a['span_length_mm'] = 7774.75
    c = ParentSpanContext('P', 9600, 0, 950, 1000)
    result = build_constructability(**a, parent_context=c)
    assert all(h.location_mm != 7774.75 for h in result.hoop_positions)
    a['right_support'] = None
    assert build_constructability(**a, parent_context=c).status == 'REVIEW'


def test_ordinary_parent_and_legacy():
    a = args()
    a['is_smf'] = False
    plain = build_constructability(**a)
    parent = build_constructability(**a, parent_context=ParentSpanContext('P', 9600, 7774.75, 950, 1000))
    assert plain.zones[0].end_mm == parent.zones[0].end_mm == 1825.25
    assert plain.zones[0].maximum_spacing_mm == 1825.25
    assert parent.zones[0].maximum_spacing_mm == 9600
    assert build_constructability(**a) == plain
