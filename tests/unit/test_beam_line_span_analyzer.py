from src.core.beam_line_span_analyzer import (
    BeamLineSpanSettings,
    _coalesce_parts_without_support,
    _build_segment_index,
    _path_intersections,
    _segment_key,
    _source_coverage_diagnostics,
    _support_path_signature,
    analyze_beam_lines,
)
from src.core.geometry_extractor import BeamGeometry, RigidLinkGeometry
from src.drawing.dxf_export import _midpoint, export_structural_spans_csv, export_structural_spans_dxf
from src.excel_span_adapter import build_span_schedule


def _beam(name, pi, pj, start, end, section="B"):
    return BeamGeometry(name, name, "F2", pi, pj, start, end, section, 600)


def test_rigid_gap_joins_pb_line_without_adding_rigid_length_or_source(tmp_path) -> None:
    right = _beam("RIGHT", "R1", "B", (1000, 0, 0), (2000, 0, 0))
    left = _beam("LEFT", "A", "C0", (-1000, 0, 0), (0, 0, 0))
    rigid = RigidLinkGeometry("RIGID-1", "F2", "C0", "R1", (0, 0, 0), (1000, 0, 0), "B1019_RIGID")

    result = analyze_beam_lines(
        [right, left],
        {"C0", "R1"},
        column_support_coordinates={"C0": (0, 0, 0), "R1": (1000, 0, 0)},
        column_anchor_names={"C0"},
        rigid_links=(rigid,),
    )

    assert len(result.lines) == 1
    assert result.lines[0].beam_line_id == "F2-PB1"
    assert result.lines[0].source_objects == ("LEFT", "RIGHT")
    assert result.lines[0].path_parts == (
        ((-1000, 0, 0), (0, 0, 0)),
        ((1000, 0, 0), (2000, 0, 0)),
    )
    assert [span.source_objects for span in result.spans] == [("LEFT",), ("RIGHT",)]
    assert [span.path_points for span in result.spans] == [
        ((-1000, 0, 0), (0, 0, 0)),
        ((1000, 0, 0), (2000, 0, 0)),
    ]
    assert [sum(abs(a[i] - b[i]) ** 2 for i in range(3)) ** 0.5 for span in result.spans for a, b in zip(span.path_points, span.path_points[1:])] == [1000, 1000]
    assert not any(item.code == "SOURCE_OBJECT_UNCOVERED" for item in result.diagnostics)

    schedule = build_span_schedule(result, [left, right], {})
    assert [record.geometry.length for record in schedule.records] == [1000, 1000]
    assert [tuple(part.source_object for part in record.object_parts) for record in schedule.records] == [
        ("LEFT",),
        ("RIGHT",),
    ]
    assert schedule.source_to_line_ids == {"LEFT": ("F2-PB1",), "RIGHT": ("F2-PB1",)}

    dxf = export_structural_spans_dxf(
        result.spans, tmp_path / "rigid-link-span-note.dxf", rigid_links=result.rigid_links
    )
    content = dxf.read_text(encoding="ascii")
    assert content.count("\nLINE\n") == 1
    assert content.count("\nTEXT\n") == 5
    assert "RIGID-1" not in content
    assert "20\n0.000\n30\n0.000\n11\n1000.000\n21\n0.000\n31\n0.000" in content


def test_two_one_hop_beam_endpoints_join_only_through_same_column_anchor() -> None:
    left = _beam("LEFT", "A", "L", (-2000, 0, 0), (-1000, 0, 0))
    right = _beam("RIGHT", "R", "B", (1000, 0, 0), (2000, 0, 0))
    links = (
        RigidLinkGeometry("RL", "F2", "C0", "L", (0, 0, 0), (-1000, 0, 0), "Rigid"),
        RigidLinkGeometry("RR", "F2", "C0", "R", (0, 0, 0), (1000, 0, 0), "Rigid"),
    )

    result = analyze_beam_lines(
        [left, right],
        {"L", "R"},
        column_support_coordinates={"L": (-1000, 0, 0), "R": (1000, 0, 0)},
        column_anchor_names={"C0"},
        rigid_links=links,
    )

    assert len(result.lines) == 1
    assert [span.source_objects for span in result.spans] == [("LEFT",), ("RIGHT",)]


def test_rigid_component_path_must_cross_its_actual_column_anchor() -> None:
    left = _beam("LEFT", "A", "R1", (-2000, 0, 0), (-1000, 0, 0))
    right = _beam("RIGHT", "R2", "B", (0, 0, 0), (1000, 0, 0))
    links = (
        RigidLinkGeometry("ANCHOR-R1", "F2", "C0", "R1", (-500, 0, 0), (-1000, 0, 0), "Rigid"),
        RigidLinkGeometry("R1-R2", "F2", "R1", "R2", (-1000, 0, 0), (0, 0, 0), "Rigid"),
    )

    result = analyze_beam_lines(
        [left, right],
        {"R1", "R2"},
        column_support_coordinates={"R1": (-1000, 0, 0), "R2": (0, 0, 0)},
        column_anchor_names={"C0"},
        rigid_links=links,
    )

    assert len([line for line in result.lines if line.beam_type == "PB"]) == 2
    assert {span.source_objects for span in result.spans} == {("LEFT",), ("RIGHT",)}


def test_unanchored_rigid_does_not_promote_or_join_secondary_lines() -> None:
    left = _beam("LEFT", "A", "L", (-2000, 0, 0), (-1000, 0, 0))
    right = _beam("RIGHT", "R", "B", (1000, 0, 0), (2000, 0, 0))
    rigid = RigidLinkGeometry("RIGID", "F2", "L", "R", (-1000, 0, 0), (1000, 0, 0), "Rigid")

    result = analyze_beam_lines([left, right], rigid_links=(rigid,))

    assert len(result.lines) == 2
    assert {line.beam_type for line in result.lines} == {"SB"}
    assert result.rigid_links == ()


def test_missing_source_object_is_a_blocking_diagnostic() -> None:
    diagnostic = _source_coverage_diagnostics(
        [_beam("LOST", "A", "B", (0, 0, 0), (10, 0, 0))],
        (),
    )[0]

    assert diagnostic.code == "SOURCE_OBJECT_UNCOVERED"
    assert diagnostic.severity == "BLOCKED"
    assert diagnostic.source_objects == ("LOST",)


def test_role_marker_without_current_support_is_not_kept_as_span_boundary() -> None:
    parts = [
        ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
        ((5.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
    ]

    assert _coalesce_parts_without_support(parts, (), 1.0) == (((0.0, 0.0, 0.0), (5.0, 0.0, 0.0), (10.0, 0.0, 0.0)),)
    assert _coalesce_parts_without_support(parts, ((5.0, 0.0, 0.0),), 1.0) == tuple(parts)


def test_support_path_signature_ignores_path_order_and_direction() -> None:
    first = (
        ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
        ((5.0, -5.0, 0.0), (5.0, 5.0, 0.0)),
    )
    second = tuple(tuple(reversed(path)) for path in reversed(first))

    assert _support_path_signature(first, 1.0) == _support_path_signature(second, 1.0)


def test_refined_curved_self_subsegments_are_not_primary_intersections() -> None:
    path = ((0.0, 0.0, 0.0), (5.0, 2.0, 0.0), (10.0, 0.0, 0.0))
    refined_self = (
        (
            (0.0, 0.0, 0.0),
            (2.5, 1.0, 0.0),
            (5.0, 2.0, 0.0),
            (7.5, 1.0, 0.0),
            (10.0, 0.0, 0.0),
        ),
    )
    settings = BeamLineSpanSettings()

    assert (
        _path_intersections(
            path,
            _build_segment_index(refined_self, settings.intersection_grid_mm),
            settings,
            {_segment_key(left, right) for left, right in zip(path, path[1:])},
        )
        == ()
    )


def test_section_changes_across_supports_keep_one_primary_beam_line() -> None:
    beams = [
        _beam("A", "C1", "P", (0, 0, 0), (10, 0, 0), "B600"),
        _beam("B", "P", "C2", (10, 0, 0), (20, 0, 0), "B800"),
        _beam("C", "C2", "FREE", (20, 0, 0), (30, 0, 0), "B700"),
    ]
    result = analyze_beam_lines(beams, {"C1", "C2"})
    assert [line.beam_line_id for line in result.lines] == ["F2-PB1"]
    assert result.lines[0].section == "VARIES"
    assert [span.span_id for span in result.spans] == ["F2-PB1-S1", "F2-PB1-S2"]
    assert result.spans[-1].is_cantilever is True
    assert all(name in result.lines[0].source_objects for name in ("A", "B", "C"))


def test_step_beam_section_change_without_support_is_one_span() -> None:
    """A section step is not by itself a structural span boundary."""

    beams = [
        _beam("PB15", "C1", "J", (0, 0, 0), (10000, 0, 0), "B300X600"),
        _beam("PB16", "J", "C2", (10000, 0, 0), (20000, 0, 0), "B500X600"),
    ]

    result = analyze_beam_lines(beams, {"C1", "C2"})

    assert len(result.lines) == 1
    assert result.lines[0].section == "VARIES"
    assert result.lines[0].source_objects == ("PB15", "PB16")
    assert len(result.spans) == 1
    assert result.spans[0].source_objects == ("PB15", "PB16")
    assert not any(item.severity == "BLOCKED" for item in result.diagnostics)


def test_section_changes_on_secondary_line_split_only_at_primary_intersection() -> None:
    beams = [
        _beam("P1", "C1", "C2", (0, 0, 0), (10, 0, 0)),
        _beam("P2", "C3", "C4", (0, 5, 0), (10, 5, 0)),
        _beam("P3", "C5", "C6", (0, 10, 0), (10, 10, 0)),
        _beam("S1", "Q1", "Q2", (5, 0, 0), (5, 5, 0), "B600"),
        _beam("S2", "Q2", "Q3", (5, 5, 0), (5, 10, 0), "B800"),
    ]
    result = analyze_beam_lines(beams, {"C1", "C2", "C3", "C4", "C5", "C6"})
    secondary = [span for span in result.spans if span.beam_type == "SB"]
    assert len([line for line in result.lines if line.beam_type == "SB"]) == 1
    assert [span.span_id for span in secondary] == ["F2-SB1-S1", "F2-SB1-S2"]
    assert [span.source_objects for span in secondary] == [("S1",), ("S2",)]


def test_plan_crossing_at_different_z_does_not_create_secondary_support() -> None:
    beams = [
        _beam("P", "C1", "C2", (0, 0, 0), (10, 0, 0)),
        _beam("S", "Q1", "Q2", (5, -5, 100), (5, 5, 100)),
    ]
    result = analyze_beam_lines(beams, {"C1", "C2"})
    secondary = [span for span in result.spans if span.beam_type == "SB"]
    assert len(secondary) == 1
    assert secondary[0].path_points == ((5, -5, 100), (5, 5, 100))


def test_column_connected_line_changes_to_secondary_after_first_primary_intersection() -> None:
    beams = [
        _beam("P", "C1", "C2", (20, 0, 0), (20, 10, 0)),
        _beam("L", "FREE", "C3", (-20, 5, 0), (30, 5, 0)),
    ]
    result = analyze_beam_lines(beams, {"C1", "C2", "C3"})
    primary = [line for line in result.lines if line.beam_type == "PB"]
    secondary = [line for line in result.lines if line.beam_type == "SB"]
    assert len(primary) == 2  # the anchor PB and the PB portion of L
    assert len(secondary) == 1
    line_primary_part = next(line for line in primary if line.source_objects == ("L",))
    assert line_primary_part.source_objects == ("L",)
    assert secondary[0].source_objects == ("L",)
    assert line_primary_part.path_points == ((20.0, 5.0, 0.0), (30, 5, 0))
    assert secondary[0].path_points == ((-20, 5, 0), (20.0, 5.0, 0.0))


def test_multi_column_line_still_changes_to_secondary_before_first_main_beam_intersection() -> None:
    beams = [
        _beam("P", "C1", "C2", (20, 0, 0), (20, 10, 0)),
        _beam("L1", "FREE", "C3", (-20, 5, 0), (30, 5, 0)),
        _beam("L2", "C3", "C4", (30, 5, 0), (40, 5, 0)),
    ]
    result = analyze_beam_lines(beams, {"C1", "C2", "C3", "C4"})
    l_parts = [line for line in result.lines if any(name in line.source_objects for name in ("L1", "L2"))]
    assert {line.beam_type for line in l_parts} == {"PB", "SB"}
    assert next(line for line in l_parts if line.beam_type == "PB").path_points == (
        (20.0, 5.0, 0.0),
        (30, 5, 0),
        (40, 5, 0),
    )
    assert next(line for line in l_parts if line.beam_type == "SB").path_points == ((-20, 5, 0), (20.0, 5.0, 0.0))


def test_dxf_midpoint_uses_path_length_not_middle_vertex() -> None:
    assert _midpoint(((0, 0, 0), (1, 0, 0), (1, 9, 0))) == (1, 4.0, 0.0)


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


def test_primary_column_to_column_line_does_not_split_at_beam_crossing() -> None:
    """An interior PB/SB (or PB/PB) crossing is not a structural support."""
    beams = [
        _beam("PB9", "C1", "C2", (0, 0, 0), (10, 0, 0)),
        # This one-column primary extension terminates at the crossing.
        _beam("PB22", "C3", "Q", (5, -5, 0), (5, 0, 0)),
        # A secondary beam also meets the same point; neither crossing is a
        # column and therefore neither may create an additional PB9 span.
        _beam("SB27", "R", "S", (5, 0, 0), (5, 5, 0)),
    ]
    result = analyze_beam_lines(beams, {"C1", "C2", "C3"})
    pb9 = next(line for line in result.lines if line.source_objects == ("PB9",))
    assert [span.span_id for span in result.spans if span.beam_line_id == pb9.beam_line_id] == [
        f"{pb9.beam_line_id}-S1"
    ]


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


def test_negligible_support_overlap_does_not_assign_object_to_two_spans() -> None:
    """A 1.5 mm support offset must not block otherwise safe name writeback."""

    beams = [
        _beam("P", "C1", "C2", (1001.5, -100, 0), (1001.5, 100, 0)),
        _beam("S0", "Q0", "Q1", (0, 0, 0), (1000, 0, 0)),
        _beam("S1", "Q1", "Q2", (1000, 0, 0), (8800, 0, 0)),
    ]

    result = analyze_beam_lines(beams, {"C1", "C2"})
    secondary_spans = [span for span in result.spans if span.beam_type == "SB"]

    assert len(secondary_spans) == 2
    assert secondary_spans[0].source_objects == ("S0",)
    assert secondary_spans[1].source_objects == ("S1",)


def test_material_support_overlap_still_requires_object_in_both_spans() -> None:
    """A genuinely support-crossing Object must remain visible in both Spans."""

    beams = [
        _beam("P", "C1", "C2", (2000, -100, 0), (2000, 100, 0)),
        _beam("S", "Q0", "Q1", (0, 0, 0), (8800, 0, 0)),
    ]

    result = analyze_beam_lines(beams, {"C1", "C2"})
    secondary_spans = [span for span in result.spans if span.beam_type == "SB"]

    assert len(secondary_spans) == 2
    assert all(span.source_objects == ("S",) for span in secondary_spans)


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
    csv_text = csv.read_text(encoding="utf-8-sig")
    assert "F2-PB1-2" in csv_text
    assert "F2-PB1-S2" not in csv_text


def test_story_groups_can_be_analyzed_in_parallel_with_same_result() -> None:
    beams = [
        _beam("F1-A", "C1", "C2", (0, 0, 0), (10, 0, 0)),
        BeamGeometry("F2-A", "F2-A", "F3", "C1", "C2", (0, 0, 3000), (10, 0, 3000), "B", 600),
    ]
    supports = {"C1", "C2"}
    serial = analyze_beam_lines(beams, supports)
    parallel = analyze_beam_lines(beams, supports, settings=BeamLineSpanSettings(story_workers=2))
    assert [span.span_id for span in serial.spans] == [span.span_id for span in parallel.spans]
    assert [span.path_points for span in serial.spans] == [span.path_points for span in parallel.spans]


def test_column_coordinate_inside_single_object_splits_primary_span() -> None:
    """A real column need not be an endpoint of the long Beam Object."""
    beams = [_beam("LONG", "C1", "C2", (0, 0, 0), (20, 0, 0))]

    result = analyze_beam_lines(
        beams,
        {"C1", "C_INTERNAL", "C2"},
        column_support_coordinates={
            "C1": (0, 0, 0),
            "C_INTERNAL": (10, 0, 0),
            "C2": (20, 0, 0),
        },
    )

    assert len(result.lines) == 1
    assert [span.path_points for span in result.spans] == [
        ((0, 0, 0), (10.0, 0.0, 0.0)),
        ((10.0, 0.0, 0.0), (20, 0, 0)),
    ]
    assert all(span.source_objects == ("LONG",) for span in result.spans)


def test_internal_column_coordinates_are_filtered_to_current_story_z() -> None:
    beams = [_beam("LONG", "C1", "C2", (0, 0, 0), (20, 0, 0))]

    result = analyze_beam_lines(
        beams,
        {"C1", "C2", "OTHER_FLOOR"},
        column_support_coordinates={
            "C1": (0, 0, 0),
            "C2": (20, 0, 0),
            "OTHER_FLOOR": (10, 0, 3000),
        },
    )

    assert len(result.spans) == 1
    assert result.spans[0].path_points == ((0, 0, 0), (20, 0, 0))


def test_rigid_equivalent_support_inside_object_splits_primary_span() -> None:
    """A rigid-transferred point uses the same explicit support contract."""
    beams = [_beam("LONG", "FREE1", "FREE2", (0, 0, 0), (20, 0, 0))]

    result = analyze_beam_lines(
        beams,
        {"RIGID_EQ"},
        column_support_coordinates={"RIGID_EQ": (10, 0, 0)},
    )

    assert len(result.lines) == 1
    assert result.lines[0].beam_type == "PB"
    assert [span.path_points for span in result.spans] == [
        ((0, 0, 0), (10.0, 0.0, 0.0)),
        ((10.0, 0.0, 0.0), (20, 0, 0)),
    ]
    assert all(span.is_cantilever for span in result.spans)


def test_multiple_fifteen_degree_continuations_are_blocked_and_reported() -> None:
    beams = [
        _beam("A", "A0", "J", (-10, 0, 0), (0, 0, 0), "B400"),
        _beam("B", "J", "B1", (0, 0, 0), (10, 0, 0), "B500"),
        _beam("C", "J", "C1", (0, 0, 0), (20, 2, 0), "B600"),
    ]

    result = analyze_beam_lines(beams)

    blocked = [item for item in result.diagnostics if item.code == "STITCH_MULTIPLE_CANDIDATES"]
    assert blocked
    assert all(item.severity == "BLOCKED" for item in blocked)
    assert any({"B", "C"}.issubset({name for group in item.candidates for name in group}) for item in blocked)
    assert len(result.lines) == 3
