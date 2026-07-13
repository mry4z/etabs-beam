from src.core.beam_line_span_analyzer import analyze_beam_lines
from src.core.geometry_extractor import BeamGeometry
from src.drawing.dxf_export import export_structural_spans_csv, export_structural_spans_dxf


def _beam(name, pi, pj, start, end):
    return BeamGeometry(name, name, "F2", pi, pj, start, end, "B", 600)


def test_primary_line_splits_at_columns_and_keeps_cantilever() -> None:
    beams = [
        _beam("B1", "C1", "P2", (0, 0, 0), (10, 0, 0)),
        _beam("B2", "P2", "C2", (10, 0, 0), (20, 0, 0)),
        _beam("B3", "C2", "C3", (20, 0, 0), (30, 0, 0)),
        _beam("B4", "C3", "FREE", (30, 0, 0), (40, 0, 0)),
    ]
    result = analyze_beam_lines(beams, {"C1", "C2", "C3"})
    assert len(result.lines) == 1
    assert result.lines[0].beam_line_id == "F2-PB1"
    assert [span.span_id for span in result.spans] == ["F2-PB1-S1", "F2-PB1-S2", "F2-PB1-S3"]
    assert result.spans[-1].is_cantilever is True


def test_secondary_splits_only_at_primary_and_not_secondary_intersection() -> None:
    beams = [
        _beam("P1", "C1", "C2", (0, 0, 0), (10, 0, 0)),
        _beam("P3", "C5", "C6", (0, 5, 0), (10, 5, 0)),
        _beam("P2", "C3", "C4", (0, 10, 0), (10, 10, 0)),
        _beam("S1", "P1", "Q", (5, 0, 0), (5, 5, 0)),
        _beam("S2", "Q", "P2", (5, 5, 0), (5, 10, 0)),
        _beam("S3", "L", "R", (0, 2.5, 0), (10, 2.5, 0)),
    ]
    result = analyze_beam_lines(beams, {"C1", "C2", "C3", "C4", "C5", "C6"})
    secondary_spans = [span for span in result.spans if span.beam_type == "SB"]
    assert len(secondary_spans) == 3
    assert sum(span.span_id.endswith("-S1") for span in secondary_spans) == 2
    assert sum(span.span_id.endswith("-S2") for span in secondary_spans) == 1
    assert all(not span.is_cantilever for span in secondary_spans)


def test_curved_primary_does_not_inherit_adjacent_straight_line_number() -> None:
    beams = [
        _beam("A", "C1", "P", (0, 0, 0), (500, 0, 0)),
        _beam("B", "P", "C2", (500, 0, 0), (1000, 100, 0)),
        _beam("C", "C2", "C3", (1000, 100, 0), (2000, 100, 0)),
    ]
    result = analyze_beam_lines(beams, {"C1", "C2", "C3"})
    primary_lines = [line for line in result.lines if line.beam_type == "PB"]
    assert len(primary_lines) == 2
    assert {line.is_curved for line in primary_lines} == {True, False}


def test_structural_span_dxf_has_one_polyline_and_matching_arial_label(tmp_path) -> None:
    beams = [
        _beam("B1", "C1", "C2", (0, 0, 0), (10, 0, 0)),
        _beam("B2", "C2", "C3", (10, 0, 0), (20, 0, 0)),
    ]
    result = analyze_beam_lines(beams, {"C1", "C2", "C3"})
    dxf = export_structural_spans_dxf(result.spans, tmp_path / "spans.dxf")
    text = dxf.read_text(encoding="ascii")
    assert text.count("\nPOLYLINE\n") == 2
    assert text.count("\nTEXT\n") == 5  # 2 labels plus 3 legend lines
    assert "7\nARIAL" in text
    csv = export_structural_spans_csv(result.spans, tmp_path / "spans.csv")
    assert "F2-PB1-S2" in csv.read_text(encoding="utf-8-sig")
