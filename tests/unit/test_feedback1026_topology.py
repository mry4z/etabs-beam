from src.core.beam_line_span_analyzer import BeamLineSpanSettings, _build_segment_index, _path_intersections, _segment_intersection, _segment_key, analyze_beam_lines
from src.core.geometry_extractor import BeamGeometry


def beam(name, pi, pj, start, end):
    return BeamGeometry(name, name, 'F2', pi, pj, start, end, 'B', 600)


def test_near_end_secondary_and_primary_end_support():
    beams = [
        beam('P', 'C1', 'C2', (0, -5000, 0), (0, 5000, 0)),
        beam('Q', 'C3', 'C4', (10000, -5000, 0), (10000, 5000, 0)),
        beam('S', 'A', 'B', (-0.00390625, 0, 0), (10000, 0, 0)),
        beam('R', 'C5', 'B', (5000, 5000, 0), (10000, 5000, 0)),
        beam('T', 'C6', 'FREE', (20000, 0, 0), (25000, 0, 0)),
    ]
    result = analyze_beam_lines(beams, {'C1', 'C2', 'C3', 'C4', 'C5', 'C6'})
    spans = {span.source_objects[0]: span for span in result.spans}
    assert spans['S'].beam_type == 'SB' and not spans['S'].is_cantilever
    assert spans['R'].beam_type == 'PB' and not spans['R'].is_cantilever
    assert spans['T'].is_cantilever
    assert len([span for span in result.spans if 'Q' in span.source_objects]) == 1


def test_intersection_tolerance_and_height():
    cross = ((0, -10, 0), (0, 10, 0))
    assert _segment_intersection((-1, 0, 0), (-10, 0, 0), *cross, 1, 1) == (-1, 0, 0)
    assert _segment_intersection((-1.01, 0, 0), (-10, 0, 0), *cross, 1, 1) is None
    assert _segment_intersection((-0.5, 0, 2), (-10, 0, 2), *cross, 1, 1) is None


def test_self_path_is_not_support_even_when_refined():
    path = ((0, 0, 0), (100, 0, 0))
    settings = BeamLineSpanSettings()
    index = _build_segment_index((((0, 0, 0), (50, 0, 0)),), settings.intersection_grid_mm)
    assert not _path_intersections(path, index, settings, {_segment_key(*path)})
