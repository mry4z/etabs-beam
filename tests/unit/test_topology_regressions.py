"""Named geometry regressions distilled from the reviewed real ETABS model."""

from src.core.beam_line_span_analyzer import analyze_beam_lines
from src.core.geometry_extractor import BeamGeometry


def _beam(name, pi, pj, start, end, section="B"):
    return BeamGeometry(name, name, "F2", pi, pj, start, end, section, 600)


def test_object_302_column_to_column_is_not_split_by_beam_intersection() -> None:
    beams = [
        _beam("302", "C1", "C2", (0, 0, 0), (20, 0, 0)),
        _beam("PB22", "C3", "J", (10, -10, 0), (10, 0, 0)),
        _beam("SB27", "J", "FREE", (10, 0, 0), (10, 10, 0)),
    ]

    result = analyze_beam_lines(beams, {"C1", "C2", "C3"})
    spans = [span for span in result.spans if span.source_objects == ("302",)]

    assert len(spans) == 1
    assert spans[0].beam_type == "PB"


def test_pb17_fixture_changes_from_secondary_to_primary_at_first_pb() -> None:
    beams = [
        _beam("PB72", "C1", "C2", (10, -5, 0), (10, 5, 0)),
        _beam("PB17", "FREE", "C3", (-20, 0, 0), (20, 0, 0)),
    ]

    result = analyze_beam_lines(beams, {"C1", "C2", "C3"})
    parts = [line for line in result.lines if "PB17" in line.source_objects]

    assert {(line.beam_type, line.path_points) for line in parts} == {
        ("SB", ((-20, 0, 0), (10.0, 0.0, 0.0))),
        ("PB", ((10.0, 0.0, 0.0), (20, 0, 0))),
    }


def test_pb_sb_roles_and_numbers_are_invariant_to_object_ij_and_names() -> None:
    original = [
        _beam("100", "C1", "C2", (10, -5, 0), (10, 5, 0)),
        _beam("101", "FREE", "C3", (-20, 0, 0), (20, 0, 0)),
        _beam("102", "A", "B", (-20, 10, 0), (0, 10, 0)),
        _beam("103", "B", "C", (0, 10, 0), (20, 10, 0)),
    ]
    rewritten = [
        _beam("F2-PB1-1.1", "C2", "C1", (10, 5, 0), (10, -5, 0)),
        _beam("F2-SB1-1.1", "C3", "FREE", (20, 0, 0), (-20, 0, 0)),
        _beam("F2-SB2-1.1", "B", "A", (0, 10, 0), (-20, 10, 0)),
        _beam("F2-SB2-1.2", "C", "B", (20, 10, 0), (0, 10, 0)),
    ]

    def signature(beams):
        result = analyze_beam_lines(beams, {"C1", "C2", "C3"})
        return [(line.beam_type, line.line_number, line.path_points) for line in result.lines]

    assert signature(original) == signature(rewritten)


def test_continuous_secondary_group_keeps_one_line_across_primary_supports() -> None:
    beams = [
        _beam("P1", "C1", "C2", (0, 0, 0), (10, 0, 0)),
        _beam("P2", "C3", "C4", (0, 10, 0), (10, 10, 0)),
        _beam("P3", "C5", "C6", (0, 20, 0), (10, 20, 0)),
        _beam("SB17_24", "Q1", "Q2", (5, 0, 0), (5, 20, 0)),
    ]

    result = analyze_beam_lines(beams, {"C1", "C2", "C3", "C4", "C5", "C6"})
    lines = [line for line in result.lines if "SB17_24" in line.source_objects]
    spans = [span for span in result.spans if "SB17_24" in span.source_objects]

    assert len(lines) == 1
    assert lines[0].beam_type == "SB"
    assert len(spans) == 2
    assert len({span.beam_line_id for span in spans}) == 1


def test_primary_cantilever_after_last_column_is_separate_span() -> None:
    beams = [
        _beam("CORE", "C1", "C2", (0, 0, 0), (10, 0, 0)),
        _beam("CANT", "C2", "FREE", (10, 0, 0), (20, 0, 0)),
    ]

    result = analyze_beam_lines(beams, {"C1", "C2"})

    assert len(result.lines) == 1
    assert len(result.spans) == 2
    assert result.spans[0].is_cantilever is False
    assert result.spans[1].is_cantilever is True


def test_short_segment_arc_is_one_curved_primary_span() -> None:
    beams = [
        _beam("ARC1", "C1", "A", (0, 0, 0), (1000, 0, 0)),
        _beam("ARC2", "A", "B", (1000, 0, 0), (2000, 100, 0)),
        _beam("ARC3", "B", "C2", (2000, 100, 0), (3000, 300, 0)),
    ]

    result = analyze_beam_lines(beams, {"C1", "C2"})

    assert len(result.lines) == 1
    assert result.lines[0].beam_type == "PB"
    assert result.lines[0].is_curved is True
    assert len(result.spans) == 1
    assert result.spans[0].source_objects == ("ARC1", "ARC2", "ARC3")


def test_secondary_to_secondary_crossing_does_not_add_span() -> None:
    beams = [
        _beam("P1", "C1", "C2", (0, 0, 0), (10, 0, 0)),
        _beam("P2", "C3", "C4", (0, 10, 0), (10, 10, 0)),
        _beam("SV", "Q1", "Q2", (5, 0, 0), (5, 10, 0)),
        _beam("SH", "Q3", "Q4", (0, 5, 0), (10, 5, 0)),
    ]

    result = analyze_beam_lines(beams, {"C1", "C2", "C3", "C4"})
    vertical = [span for span in result.spans if "SV" in span.source_objects]
    horizontal = [span for span in result.spans if "SH" in span.source_objects]

    assert len(vertical) == 1
    assert len(horizontal) == 1
