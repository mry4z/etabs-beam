import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.beam_line_span_analyzer import BeamSpan, analyze_beam_lines
from src.core.etabs_connector import EtabsApiError
from src.core.geometry_extractor import BeamGeometry, ColumnOutline, GeometryExtractor
from src.design.provider import BeamDesignResult, DesignStation
from src.drawing.beam_result_dxf import export_beam_result_and_section_dxfs
from src.drawing.dxf_export import export_structural_spans_dxf
from src.excel_span_adapter import SpanScheduleMapping, SpanScheduleRecord


class _FakeFrames:
    def __init__(self, frames):
        self.frames = frames

    def GetAllFrames(self, *_args):
        names = [frame["name"] for frame in self.frames]
        sections = [frame["section"] for frame in self.frames]
        point_i = [frame["point_i"] for frame in self.frames]
        point_j = [frame["point_j"] for frame in self.frames]
        starts = [frame["start"] for frame in self.frames]
        ends = [frame["end"] for frame in self.frames]
        arrays = [
            names,
            sections,
            ["F1"] * len(names),
            point_i,
            point_j,
            [point[0] for point in starts],
            [point[1] for point in starts],
            [point[2] for point in starts],
            [point[0] for point in ends],
            [point[1] for point in ends],
            [point[2] for point in ends],
            *([[0] * len(names) for _ in range(8)]),
        ]
        return (0, len(names), *arrays)

    def GetDesignOrientation(self, name, _default):
        return 0, next(frame["orientation"] for frame in self.frames if frame["name"] == name)

    def GetTransformationMatrix(self, name, *_args):
        frame = next(frame for frame in self.frames if frame["name"] == name)
        return 0, frame.get("matrix", (0, 0, 1, 1, 0, 0, 0, 1, 0))


class _FakePropFrame:
    def __init__(self, rectangles=None, circles=None, section_designer=()):
        self.rectangles = rectangles or {}
        self.circles = circles or {}
        self.section_designer = set(section_designer)

    def GetRectangle(self, name, *_args):
        if name not in self.rectangles:
            return 1
        t2, t3 = self.rectangles[name]
        return 0, "", "C30", t3, t2, 2, "", ""

    def GetCircle(self, name, *_args):
        if name not in self.circles:
            return 1
        return 0, "", "C30", self.circles[name], 2, "", ""

    def GetSDSection(self, name, *_args):
        if name not in self.section_designer:
            return 1
        return 0, "C30", 2, ["P1", "P2"], [1, 1], 0, 2, "", ""


class _FakeDatabaseTables:
    fields = ("SectionName", "ShapeName", "X", "Y")
    records = (
        ("SD", "P1", "0", "0"),
        ("SD", "P1", "0", "10"),
        ("SD", "P1", "5", "10"),
        ("SD", "P1", "5", "0"),
        ("SD", "P2", "20", "0"),
        ("SD", "P2", "20", "4"),
        ("SD", "P2", "22", "4"),
        ("SD", "P2", "22", "0"),
    )

    def GetTableForDisplayArray(self, *_args):
        flat = tuple(value for row in self.records for value in row)
        return 0, None, 1, self.fields, len(self.records), flat


class _EmptySectionDesignerPropFrame(_FakePropFrame):
    def GetSDSection(self, _name, *_args):
        return 0, "C30", 0, [], [], 0, 0, "", ""


class _EmptyDatabaseTables(_FakeDatabaseTables):
    records = ()


def _fake_model(frames, *, rectangles=None, circles=None, section_designer=(), table=False):
    sap = SimpleNamespace(
        FrameObj=_FakeFrames(frames),
        PropFrame=_FakePropFrame(rectangles, circles, section_designer),
    )
    if table:
        sap.DatabaseTables = _FakeDatabaseTables()
    return sap


def _frame(name, section, point_i, point_j, start, end, orientation=1, matrix=None):
    frame = {
        "name": name,
        "section": section,
        "point_i": point_i,
        "point_j": point_j,
        "start": start,
        "end": end,
        "orientation": orientation,
    }
    if matrix is not None:
        frame["matrix"] = matrix
    return frame


def test_upper_only_column_base_does_not_split_beam_or_become_support(monkeypatch):
    sap = _fake_model(
        [_frame("C1", "C400", "MID", "TOP", (5000, 0, 3000), (5000, 0, 6000))],
        rectangles={"C400": (400, 400)},
    )
    monkeypatch.setitem(sys.modules, "ETABSv1", SimpleNamespace(eFrameDesignOrientation=SimpleNamespace(Column=1)))
    supports = GeometryExtractor(sap).column_supports()
    beam = BeamGeometry("B1", "B1", "F1", "A", "B", (0, 0, 3000), (10000, 0, 3000), "B", 800)

    assert set(supports) == {"TOP"}
    assert "MID" not in supports
    result = analyze_beam_lines([beam], set(supports), column_support_coordinates=supports)
    assert len(result.spans) == 1
    assert result.spans[0].path_points == (beam.start, beam.end)


def test_section_designer_polygons_are_transformed_and_only_lower_section_is_at_joint(monkeypatch):
    matrix = (0, 0, 1, 0, 1, 0, -1, 0, 0)
    sap = _fake_model(
        [
            _frame("LOWER", "SD", "BASE", "JOINT", (1000, 2000, 0), (1000, 2000, 3000), matrix=matrix),
            _frame("UPPER", "RECT", "JOINT", "ROOF", (1000, 2000, 3000), (1000, 2000, 6000)),
        ],
        rectangles={"RECT": (400, 600)},
        section_designer={"SD"},
        table=True,
    )
    monkeypatch.setitem(sys.modules, "ETABSv1", SimpleNamespace(eFrameDesignOrientation=SimpleNamespace(Column=1)))

    outlines = GeometryExtractor(sap).column_outlines()

    at_joint = [outline for outline in outlines if outline.support_name == "JOINT"]
    assert [outline.shape_name for outline in at_joint] == ["P1", "P2"]
    assert all(outline.frame_name == "LOWER" for outline in at_joint)
    assert at_joint[0].vertices == ((1000.0, 2000.0), (1000.0, 2010.0), (995.0, 2010.0), (995.0, 2000.0))
    assert at_joint[1].vertices == ((980.0, 2000.0), (980.0, 2004.0), (978.0, 2004.0), (978.0, 2000.0))


def test_rectangular_and_circular_column_shapes_use_real_dimensions(monkeypatch):
    sap = _fake_model(
        [
            _frame("RECT_COL", "RECT", "R0", "R1", (0, 0, 0), (0, 0, 3000)),
            _frame("CIRC_COL", "CIRCLE", "C0", "C1", (1000, 0, 0), (1000, 0, 3000)),
        ],
        rectangles={"RECT": (400, 600)},
        circles={"CIRCLE": 500},
    )
    monkeypatch.setitem(sys.modules, "ETABSv1", SimpleNamespace(eFrameDesignOrientation=SimpleNamespace(Column=1)))

    outlines = GeometryExtractor(sap).column_outlines()
    rectangle = next(outline for outline in outlines if outline.frame_name == "RECT_COL")
    circle = next(outline for outline in outlines if outline.frame_name == "CIRC_COL")

    assert rectangle.vertices == ((-200.0, -300.0), (200.0, -300.0), (200.0, 300.0), (-200.0, 300.0))
    assert len(circle.vertices) == 96
    assert max(x for x, _ in circle.vertices) == 1250.0
    assert min(x for x, _ in circle.vertices) == 750.0


def test_unsupported_column_section_geometry_fails_with_section_context():
    extractor = GeometryExtractor(_fake_model([]))

    with pytest.raises(EtabsApiError, match="Column section STEEL:.*geometry is available"):
        extractor._column_section_shapes("STEEL")


def test_empty_section_designer_geometry_fails_with_section_context():
    sap = _fake_model([], table=True)
    sap.PropFrame = _EmptySectionDesignerPropFrame()
    sap.DatabaseTables = _EmptyDatabaseTables()
    extractor = GeometryExtractor(sap)

    with pytest.raises(EtabsApiError, match=r"Section Designer EMPTY:.*shape_count=0"):
        extractor._column_section_shapes("EMPTY")


def _column_outline(z=3000.0):
    return ColumnOutline("C1", "P1", "C400", "", (0.0, 0.0, z), ((-200.0, -300.0), (200.0, -300.0), (200.0, 300.0), (-200.0, 300.0)))


def _assert_closed_yellow_column(path: Path):
    content = path.read_text(encoding="ascii")
    assert "0\nLAYER\n2\ncol\n70\n0\n62\n2\n" in content
    assert content.count("0\nPOLYLINE\n8\ncol\n62\n2\n66\n1\n70\n1\n") == 1


def test_all_beam_dxfs_include_closed_yellow_column_outline(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    span = BeamSpan("F1", "PB", 1, 1, ("B1",), ((0, 0, 3000), (10000, 0, 3000)), False, False)
    note = export_structural_spans_dxf([span], tmp_path / "span_note.dxf", column_outlines=[_column_outline()])
    assert "0\nPOLYLINE\n8\ncol\n62\n2\n66\n1\n70\n1\n" in note.read_text(encoding="ascii")

    beam = BeamGeometry("B1", "B1", "F1", "A", "B", (0, 0, 3000), (10000, 0, 3000), "B400X800", 800)
    station = DesignStation(0, "", 1000, "", 1000, "", 0.1, "", 0, "", 0, top_total_area=1000, bottom_total_area=1000)
    schedule = SpanScheduleMapping(
        (SpanScheduleRecord(span, beam, BeamDesignResult("B1", (station,))),),
        {},
        {},
    )
    exported = export_beam_result_and_section_dxfs(
        schedule,
        [beam],
        lambda _: {"width_mm": 400, "depth_mm": 800},
        tmp_path,
        column_outlines=[_column_outline()],
    )
    _assert_closed_yellow_column(exported.result_paths[0])
    _assert_closed_yellow_column(exported.section_paths[0])
    for path in (note, exported.result_paths[0], exported.section_paths[0]):
        document = ezdxf.readfile(path)
        assert document.layers.get("col").dxf.color == 2
        outlines = [entity for entity in document.modelspace() if entity.dxf.layer == "col"]
        assert len(outlines) == 1
        assert outlines[0].dxftype() == "POLYLINE"
        assert outlines[0].is_closed
        assert outlines[0].dxf.color == 2


def test_span_note_excludes_upper_only_column_outline_at_other_elevation(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    span = BeamSpan("F1", "PB", 1, 1, ("B1",), ((0, 0, 3000), (10000, 0, 3000)), False, False)

    path = export_structural_spans_dxf(
        [span],
        tmp_path / "span_note.dxf",
        column_outlines=[_column_outline(z=6000)],
    )

    document = ezdxf.readfile(path)
    assert not [entity for entity in document.modelspace() if entity.dxf.layer == "col"]
