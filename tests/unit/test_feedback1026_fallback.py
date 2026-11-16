from src.rebar_workbook import _fallback_rebar_notation, _fallback_region_texts
from src.design.detailing import ProjectDetailingPolicy


def test_fallback_uses_project_catalog_and_preserves_invalid_demands():
    project = ProjectDetailingPolicy()
    assert _fallback_rebar_notation(0, project=project) == '2T16'
    assert _fallback_rebar_notation(0, effective_frame_type='SMF', project=project) == '2T20'
    assert _fallback_rebar_notation(10000, project=project).endswith('T25')
    for value in (None, float('nan'), -1):
        assert '需复核' in _fallback_rebar_notation(value, project=project)
    assert '需复核' in _fallback_rebar_notation(0)
    controls = {region: {'top_total_mm2': 0, 'bottom_total_mm2': 0, 'av_over_s': 0} for region in ('left', 'middle', 'right')}
    controls['middle']['bottom_total_mm2'] = None
    assert '需复核' in _fallback_region_texts(controls, {}, project)['b1']


def test_stirrup_fallback_catalog_and_demands():
    from src.rebar_workbook import _fallback_stirrup_text
    project = ProjectDetailingPolicy()
    call = lambda value: _fallback_stirrup_text(value, source='test', project=project)
    for value in (None, float('nan'), -1):
        assert '需复核' in call(value)
    assert call(0) == '2T10@100'
    assert call(10) == '10T12@100'
    from types import SimpleNamespace
    project = SimpleNamespace(preferred_stirrup_diameters_mm=(12, 16), enabled_diameters_mm=(10, 12), allowed_spacings_mm=(150, 130))
    assert call(0) == '2T12@130'
    project.preferred_stirrup_diameters_mm = (16,)
    assert '需复核' in call(0)
    project = ProjectDetailingPolicy()
    controls = {r: {'av_over_s': 0} for r in ('left', 'middle', 'right')}
    controls['left']['av_over_s'] = None
    assert '需复核' in _fallback_region_texts(controls, {}, project)['s2']
