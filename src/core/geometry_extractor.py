"""Read beam geometry from ETABS without making design assumptions."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Sequence

from src.core.etabs_connector import EtabsApiError, api_values
from src.utils.geometry import Point3D, length


class SelectionError(RuntimeError):
    """Raised when no suitable beam objects are available."""


def expand_rigid_support_points(column_points: Iterable[str], rigid_links: Iterable[tuple[str, str]]) -> set[str]:
    """Propagate real-column support points through anchored rigid links.

    A rigid helper member is a support-transfer edge only when it is connected
    to a known column point. Unanchored rigid members are deliberately ignored
    so they cannot silently turn an arbitrary beam endpoint into a primary
    support.
    """
    supports = {str(point) for point in column_points}
    links = [(str(left), str(right)) for left, right in rigid_links]
    changed = True
    while changed:
        changed = False
        for left, right in links:
            if left in supports and right not in supports:
                supports.add(right)
                changed = True
            elif right in supports and left not in supports:
                supports.add(left)
                changed = True
    return supports


def relevant_anchored_rigid_links(
    rigid_links: Iterable["RigidLinkGeometry"],
    beam_endpoint_names: Iterable[str],
    column_anchor_names: Iterable[str],
) -> tuple["RigidLinkGeometry", ...]:
    """Keep only rigid components that connect a lower-column anchor to a beam endpoint."""

    links = tuple(rigid_links)
    beam_points = {str(name) for name in beam_endpoint_names}
    anchors = {str(name) for name in column_anchor_names}
    by_story: dict[str, list[int]] = {}
    for index, link in enumerate(links):
        by_story.setdefault(link.story, []).append(index)

    selected: set[int] = set()
    for indices in by_story.values():
        adjacency: dict[str, list[int]] = {}
        for index in indices:
            link = links[index]
            adjacency.setdefault(link.point_i, []).append(index)
            adjacency.setdefault(link.point_j, []).append(index)
        visited: set[str] = set()
        for start in adjacency:
            if start in visited:
                continue
            component_nodes: set[str] = set()
            component_edges: set[int] = set()
            stack = [start]
            while stack:
                point = stack.pop()
                if point in component_nodes:
                    continue
                component_nodes.add(point)
                visited.add(point)
                for edge_index in adjacency.get(point, ()):
                    component_edges.add(edge_index)
                    edge = links[edge_index]
                    other = edge.point_j if edge.point_i == point else edge.point_i
                    if other not in component_nodes:
                        stack.append(other)
            component_anchors = component_nodes & anchors
            for beam_point in sorted(component_nodes & beam_points):
                pending = [beam_point]
                previous: dict[str, tuple[str, int] | None] = {beam_point: None}
                found_anchor = ""
                while pending:
                    point = pending.pop(0)
                    if point in component_anchors:
                        found_anchor = point
                        break
                    for edge_index in adjacency.get(point, ()):
                        if edge_index not in component_edges:
                            continue
                        edge = links[edge_index]
                        other = edge.point_j if edge.point_i == point else edge.point_i
                        if other not in previous:
                            previous[other] = (point, edge_index)
                            pending.append(other)
                if not found_anchor:
                    continue
                point = found_anchor
                while point != beam_point:
                    parent = previous.get(point)
                    if parent is None:
                        break
                    point, edge_index = parent
                    selected.add(edge_index)
    return tuple(links[index] for index in range(len(links)) if index in selected)


@dataclass(frozen=True)
class BeamGeometry:
    name: str
    label: str
    story: str
    point_i: str
    point_j: str
    start: Point3D
    end: Point3D
    section: str
    depth: float | None = None
    source_names: tuple[str, ...] = ()
    path_points: tuple[Point3D, ...] = ()

    @property
    def length(self) -> float:
        if len(self.path_points) > 1:
            return sum(length(start, end) for start, end in zip(self.path_points, self.path_points[1:]))
        return length(self.start, self.end)


@dataclass(frozen=True)
class RigidLinkGeometry:
    """Numbering-only geometry for one true rigid helper frame."""

    name: str
    story: str
    point_i: str
    point_j: str
    start: Point3D
    end: Point3D
    section: str = ""


@dataclass(frozen=True)
class ColumnOutline:
    """One column concrete outline in the global plan coordinate system."""

    frame_name: str
    support_name: str
    section: str
    shape_name: str
    support: Point3D
    vertices: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class _FrameSummary:
    """Fields needed for support discovery from one bulk OAPI row."""

    name: str
    label: str
    story: str
    section: str
    point_i: str
    point_j: str
    start: Point3D
    end: Point3D


def _upper_endpoint(summary: _FrameSummary) -> tuple[str, Point3D]:
    """Return the endpoint at the higher elevation of a column frame."""

    if summary.start[2] > summary.end[2]:
        return summary.point_i, summary.start
    return summary.point_j, summary.end


def _field_key(value: Any) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


class GeometryExtractor:
    FRAME_OBJECT_TYPE = 2
    BEAM_DESIGN_ORIENTATION = 2

    def __init__(self, sap_model: Any) -> None:
        self.sap = sap_model
        self._frame_summaries_cache: tuple[_FrameSummary, ...] | None = None
        self._design_orientation_cache: dict[str, int] = {}
        self._concrete_section_cache: dict[str, bool] = {}
        self._section_depth_cache: dict[str, float | None] = {}
        self._column_outline_cache: tuple[ColumnOutline, ...] | None = None
        self._column_section_shape_cache: dict[str, tuple[tuple[str, tuple[tuple[float, float], ...]], ...]] = {}

    def selected_beam_names(self) -> list[str]:
        values = api_values(self.sap.SelectObj.GetSelected(0, None, None), "SelectObj.GetSelected")
        if len(values) != 3:
            raise EtabsApiError("SelectObj.GetSelected: 返回参数数量异常。")
        count, object_types, object_names = values
        if int(count) == 0:
            return []
        names = [
            str(name)
            for object_type, name in zip(list(object_types), list(object_names))
            if int(object_type) == self.FRAME_OBJECT_TYPE and self._is_beam(str(name))
        ]
        if count and not names:
            raise SelectionError("当前选择中没有梁对象；请在 ETABS 中选择梁，或使用 --selection all。")
        return names

    def all_beam_names(self) -> list[str]:
        return [summary.name for summary in self._all_frame_summaries() if self._is_beam(summary.name, summary.section)]

    def column_point_names(self) -> set[str]:
        """Return real and rigid-transferred column support point names."""
        return self._column_support_names()

    def column_anchor_names(self) -> set[str]:
        """Return only endpoints of lower column segments before rigid transfer."""
        return {_upper_endpoint(summary)[0] for summary in self._lower_column_frames()}

    def rigid_links_for_beams(self, beams: Sequence[BeamGeometry]) -> tuple[RigidLinkGeometry, ...]:
        """Return anchored rigid frames adjacent to this beam scope for numbering only."""
        summaries = self._all_frame_summaries()
        anchors = self.column_anchor_names()
        links = tuple(
            RigidLinkGeometry(
                summary.name,
                summary.story,
                summary.point_i,
                summary.point_j,
                summary.start,
                summary.end,
                summary.section,
            )
            for summary in summaries
            if "rigid" in summary.section.casefold()
        )
        return relevant_anchored_rigid_links(
            links,
            (point for beam in beams for point in (beam.point_i, beam.point_j)),
            anchors,
        )

    def column_supports(self) -> dict[str, Point3D]:
        """Return top endpoints of lower column segments and rigid transfers.

        A beam at a column joint is supported only when a column continues
        downward from that joint.  The bottom end of an upper-only column is
        therefore excluded.  A rigid helper transfers a detected lower-column
        support only after it is anchored to such a support.

        Coordinates are read independently of Beam Object endpoints so a
        column landing inside one long Beam Object is retained for structural
        span splitting.
        """
        support_names, coordinates = self._column_support_data()
        return {
            point_name: coordinates[point_name] for point_name in sorted(support_names) if point_name in coordinates
        }

    def column_outlines(self) -> tuple[ColumnOutline, ...]:
        """Return plan outlines for lower-column supports, including SD polygons."""

        if self._column_outline_cache is not None:
            return self._column_outline_cache
        outlines: list[ColumnOutline] = []
        for summary in self._lower_column_frames():
            support_name, support = _upper_endpoint(summary)
            matrix_values = api_values(
                self.sap.FrameObj.GetTransformationMatrix(summary.name, [0.0] * 9, False),
                f"FrameObj.GetTransformationMatrix({summary.name})",
            )
            matrix = tuple(float(value) for value in list(matrix_values[0]))
            if len(matrix) != 9 or not all(math.isfinite(value) for value in matrix):
                raise EtabsApiError(f"FrameObj.GetTransformationMatrix({summary.name}): 方向余弦矩阵无效。")
            section_shapes = self._column_section_shapes(summary.section)
            for shape_name, local_vertices in section_shapes:
                global_vertices = tuple(
                    (
                        support[0] + local_2 * matrix[3] + local_3 * matrix[6],
                        support[1] + local_2 * matrix[4] + local_3 * matrix[7],
                    )
                    for local_2, local_3 in local_vertices
                )
                outlines.append(
                    ColumnOutline(
                        frame_name=summary.name,
                        support_name=support_name,
                        section=summary.section,
                        shape_name=shape_name,
                        support=support,
                        vertices=global_vertices,
                    )
                )
        self._column_outline_cache = tuple(outlines)
        return self._column_outline_cache

    def _column_section_shapes(self, section_name: str) -> tuple[tuple[str, tuple[tuple[float, float], ...]], ...]:
        """Read one rectangular/circular section or public SD concrete polygons."""

        if section_name not in self._column_section_shape_cache:
            self._column_section_shape_cache[section_name] = self._read_column_section_shapes(section_name)
        return self._column_section_shape_cache[section_name]

    def _read_column_section_shapes(
        self,
        section_name: str,
    ) -> tuple[tuple[str, tuple[tuple[float, float], ...]], ...]:

        try:
            values = api_values(
                self.sap.PropFrame.GetRectangle(section_name, "", "", 0.0, 0.0, -1, "", ""),
                f"PropFrame.GetRectangle({section_name})",
            )
        except EtabsApiError:
            values = None
        if values is not None:
            try:
                t3, t2 = float(values[2]), float(values[3])
            except (IndexError, TypeError, ValueError):
                t2 = t3 = 0.0
            if all(math.isfinite(value) and value > 0.0 for value in (t2, t3)):
                return (("", ((-t2 / 2, -t3 / 2), (t2 / 2, -t3 / 2), (t2 / 2, t3 / 2), (-t2 / 2, t3 / 2))),)

        try:
            values = api_values(
                self.sap.PropFrame.GetCircle(section_name, "", "", 0.0, -1, "", ""),
                f"PropFrame.GetCircle({section_name})",
            )
        except EtabsApiError:
            values = None
        if values is not None:
            try:
                diameter = float(values[2])
            except (IndexError, TypeError, ValueError):
                diameter = 0.0
            if math.isfinite(diameter) and diameter > 0.0:
                radius = diameter / 2.0
                vertices = tuple(
                    (radius * math.cos(2.0 * math.pi * index / 96), radius * math.sin(2.0 * math.pi * index / 96))
                    for index in range(96)
                )
                return (("", vertices),)

        try:
            values = api_values(
                self.sap.PropFrame.GetSDSection(section_name, "", 0, [], [], 0, 0, "", ""),
                f"PropFrame.GetSDSection({section_name})",
            )
        except EtabsApiError as exc:
            raise EtabsApiError(
                f"Column section {section_name}: no supported rectangular, circular, or Section Designer "
                "concrete-polygon geometry is available."
            ) from exc
        if len(values) != 8:
            raise EtabsApiError(f"PropFrame.GetSDSection({section_name}): 返回参数数量异常。")
        shape_count = int(values[1])
        table_values = api_values(
            self.sap.DatabaseTables.GetTableForDisplayArray(
                "Section Designer Shapes - Concrete Polygon",
                None,
                "",
                0,
                None,
                0,
                None,
            ),
            "DatabaseTables.GetTableForDisplayArray(Section Designer Shapes - Concrete Polygon)",
        )
        if len(table_values) != 5:
            raise EtabsApiError("Section Designer Concrete Polygon: 返回参数数量异常。")
        _requested_fields, _table_version, included_fields, record_count, flat_data = table_values
        fields = tuple(_field_key(value) for value in list(included_fields or ()))
        data = tuple(str(value) for value in list(flat_data or ()))
        count = int(record_count)
        if not fields or count < 0 or len(data) != count * len(fields):
            raise EtabsApiError("Section Designer Concrete Polygon: 字段/记录数据长度异常。")
        required = {"sectionname", "shapename", "x", "y"}
        if not required.issubset(fields):
            raise EtabsApiError("Section Designer Concrete Polygon: 缺少 SectionName/ShapeName/X/Y 字段。")
        column_indexes = {name: fields.index(name) for name in required}
        grouped: dict[str, list[tuple[float, float]]] = {}
        for offset in range(0, len(data), len(fields)):
            row = data[offset : offset + len(fields)]
            if row[column_indexes["sectionname"]] != section_name:
                continue
            shape_name = row[column_indexes["shapename"]].strip()
            try:
                # CSI stores X=local-3 and Y=local-2.
                local_2 = float(row[column_indexes["y"]])
                local_3 = float(row[column_indexes["x"]])
            except ValueError as exc:
                raise EtabsApiError(f"Section Designer Concrete Polygon {section_name}: 坐标无效。") from exc
            if not shape_name or not math.isfinite(local_2) or not math.isfinite(local_3):
                raise EtabsApiError(f"Section Designer Concrete Polygon {section_name}: ShapeName/坐标无效。")
            grouped.setdefault(shape_name, []).append((local_2, local_3))
        if not grouped:
            raise EtabsApiError(
                f"Section Designer {section_name}: Concrete Polygon 表没有匹配到截面多边形 "
                f"(shape_count={shape_count})。"
            )
        for shape_name, vertices in grouped.items():
            if len(vertices) < 3:
                raise EtabsApiError(f"Section Designer {section_name}/{shape_name}: 顶点少于 3 个。")
        return tuple((name, tuple(vertices)) for name, vertices in grouped.items())

    def _column_support_names(self) -> set[str]:
        return self._column_support_data()[0]

    def _column_support_data(self) -> tuple[set[str], dict[str, Point3D]]:
        """Discover supports with one bulk geometry call plus orientation reads.

        ``GetAllFrames`` returns section, connectivity and endpoint coordinates
        for the whole model in one COM round trip.  The previous implementation
        called ``GetPoints`` and ``GetSection`` for every Frame Object and then
        called ``GetCoordCartesian`` for every support; on a real 7,000-frame
        model that added tens of thousands of cross-process calls.
        """
        summaries = self._all_frame_summaries()
        points = { _upper_endpoint(summary)[0] for summary in self._lower_column_frames(summaries) }
        rigid_links: list[tuple[str, str]] = []
        coordinates: dict[str, Point3D] = {}
        for summary in summaries:
            coordinates.setdefault(summary.point_i, summary.start)
            coordinates.setdefault(summary.point_j, summary.end)
            if "rigid" in summary.section.casefold():
                rigid_links.append((summary.point_i, summary.point_j))
        return expand_rigid_support_points(points, rigid_links), coordinates

    def _lower_column_frames(
        self,
        summaries: tuple[_FrameSummary, ...] | None = None,
    ) -> tuple[_FrameSummary, ...]:
        from ETABSv1 import eFrameDesignOrientation

        candidates: list[_FrameSummary] = []
        for summary in summaries or self._all_frame_summaries():
            if self._design_orientation(summary.name, eFrameDesignOrientation.Column) != 1:
                continue
            if abs(summary.start[2] - summary.end[2]) <= 1.0e-6:
                continue
            candidates.append(summary)
        return tuple(candidates)

    def _all_frame_summaries(self) -> tuple[_FrameSummary, ...]:
        """Read model-wide Frame geometry in one call, with a legacy fallback."""

        if self._frame_summaries_cache is not None:
            return self._frame_summaries_cache

        get_all = getattr(self.sap.FrameObj, "GetAllFrames", None)
        if get_all is not None:
            # NumberNames + 19 by-ref output arrays + the Global coordinate system.
            values = api_values(
                get_all(*([0] + [None] * 19 + ["Global"])),
                "FrameObj.GetAllFrames",
            )
            if len(values) != 20:
                raise EtabsApiError("FrameObj.GetAllFrames: 返回参数数量异常。")
            count = int(values[0])
            arrays = [list(value) for value in values[1:12]]
            if any(len(value) != count for value in arrays):
                raise EtabsApiError("FrameObj.GetAllFrames: 数组长度不一致。")
            names, sections, stories, point_i, point_j = arrays[:5]
            x_i, y_i, z_i, x_j, y_j, z_j = arrays[5:11]
            labels = self._frame_labels_by_name()
            result = tuple(
                _FrameSummary(
                    str(names[index]),
                    labels.get(str(names[index]), str(names[index])),
                    str(stories[index]),
                    str(sections[index]),
                    str(point_i[index]),
                    str(point_j[index]),
                    (float(x_i[index]), float(y_i[index]), float(z_i[index])),
                    (float(x_j[index]), float(y_j[index]), float(z_j[index])),
                )
                for index in range(count)
            )
            self._frame_summaries_cache = result
            return result

        # ETABS 22/23 expose GetAllFrames.  Keep the older per-object path only
        # for test doubles and legacy compatible API builds.
        values = api_values(self.sap.FrameObj.GetNameList(0, None), "FrameObj.GetNameList")
        if len(values) != 2:
            raise EtabsApiError("FrameObj.GetNameList: 返回参数数量异常。")
        summaries: list[_FrameSummary] = []
        for raw_name in list(values[1]):
            name = str(raw_name)
            try:
                point_i, point_j = api_values(
                    self.sap.FrameObj.GetPoints(name, None, None),
                    f"GetPoints({name})",
                )
                section, _ = api_values(
                    self.sap.FrameObj.GetSection(name, None, None),
                    f"GetSection({name})",
                )
                summaries.append(
                    _FrameSummary(
                        name,
                        name,
                        "",
                        str(section),
                        str(point_i),
                        str(point_j),
                        self._point_coordinates(str(point_i)),
                        self._point_coordinates(str(point_j)),
                    )
                )
            except EtabsApiError:
                continue
        result = tuple(summaries)
        self._frame_summaries_cache = result
        return result

    def _frame_labels_by_name(self) -> dict[str, str]:
        get_labels = getattr(self.sap.FrameObj, "GetLabelNameList", None)
        if get_labels is None:
            return {}
        values = api_values(get_labels(0, None, None, None), "FrameObj.GetLabelNameList")
        if len(values) != 4:
            raise EtabsApiError("FrameObj.GetLabelNameList: 返回参数数量异常。")
        # ETABS 22/23 signature:
        # NumberNames, MyName (unique object name), MyLabel, MyStory.
        count, names, labels, _stories = values
        if int(count) != len(labels) or int(count) != len(names):
            raise EtabsApiError("FrameObj.GetLabelNameList: 数组长度不一致。")
        return {str(name): str(label) for name, label in zip(list(names), list(labels))}

    def extract(self, selection_mode: str = "all") -> list[BeamGeometry]:
        if selection_mode not in {"all", "selected"}:
            raise ValueError("selection_mode 必须是 all 或 selected。")
        names = self.all_beam_names() if selection_mode == "all" else self.selected_beam_names()
        if not names:
            raise SelectionError("没有可导出的梁。")
        summaries = {item.name: item for item in self._all_frame_summaries()}
        return [
            self._geometry_from_summary(summaries[name]) if name in summaries else self._extract_one(name)
            for name in names
        ]

    def _design_orientation(self, name: str, default: Any) -> int:
        cached = self._design_orientation_cache.get(name)
        if cached is not None:
            return cached
        values = api_values(
            self.sap.FrameObj.GetDesignOrientation(name, default),
            f"GetDesignOrientation({name})",
        )
        orientation = int(values[0]) if values else 0
        self._design_orientation_cache[name] = orientation
        return orientation

    def _is_beam(self, name: str, section_name: str | None = None) -> bool:
        from ETABSv1 import eFrameDesignOrientation

        if self._design_orientation(name, eFrameDesignOrientation.Beam) != self.BEAM_DESIGN_ORIENTATION:
            return False
        if section_name is None:
            section, _ = api_values(
                self.sap.FrameObj.GetSection(name, None, None),
                f"GetSection({name})",
            )
            section_name = str(section)
        return self._is_concrete_section(section_name)

    def _is_concrete_section(self, section_name: str) -> bool:
        """Return whether a frame property is eligible for concrete beam design.

        Rigid-end helper members are intentionally excluded before geometry
        extraction. For other properties, use the material type returned by
        ``PropFrame.GetNameInPropFile`` and ``PropMaterial.GetTypeOAPI``. The
        supported-shape fallback keeps compatibility with older OAPI builds.
        """
        cached = self._concrete_section_cache.get(section_name)
        if cached is not None:
            return cached
        result = self._compute_is_concrete_section(section_name)
        self._concrete_section_cache[section_name] = result
        return result

    def _compute_is_concrete_section(self, section_name: str) -> bool:
        if not section_name.strip() or section_name.strip().casefold() in {"none", "null"}:
            return False
        if "rigid" in section_name.casefold():
            return False
        try:
            from ETABSv1 import eFramePropType, eMatType

            values = api_values(
                self.sap.PropFrame.GetNameInPropFile(section_name, None, None, None, eFramePropType.Rectangular),
                f"PropFrame.GetNameInPropFile({section_name})",
            )
            material_name = str(values[2]) if len(values) >= 3 and values[2] else ""
            if material_name:
                material_type = api_values(
                    self.sap.PropMaterial.GetTypeOAPI(material_name, eMatType.Concrete, 0),
                    f"PropMaterial.GetTypeOAPI({material_name})",
                )[0]
                return material_type == eMatType.Concrete or "concrete" in str(material_type).casefold()
        except Exception:
            pass
        return self._section_depth(section_name) is not None

    def _geometry_from_summary(self, summary: _FrameSummary) -> BeamGeometry:
        return BeamGeometry(
            name=summary.name,
            label=summary.label,
            story=summary.story,
            point_i=summary.point_i,
            point_j=summary.point_j,
            start=summary.start,
            end=summary.end,
            section=summary.section,
            depth=self._section_depth(summary.section),
        )

    def _extract_one(self, name: str) -> BeamGeometry:
        label, story = api_values(self.sap.FrameObj.GetLabelFromName(name, None, None), f"GetLabelFromName({name})")
        point_i, point_j = api_values(self.sap.FrameObj.GetPoints(name, None, None), f"GetPoints({name})")
        start = self._point_coordinates(str(point_i))
        end = self._point_coordinates(str(point_j))
        section, _ = api_values(self.sap.FrameObj.GetSection(name, None, None), f"GetSection({name})")
        section = str(section)
        return BeamGeometry(
            name=name,
            label=str(label),
            story=str(story),
            point_i=str(point_i),
            point_j=str(point_j),
            start=start,
            end=end,
            section=section,
            depth=self._section_depth(section),
        )

    def _point_coordinates(self, point_name: str) -> Point3D:
        values = api_values(
            self.sap.PointObj.GetCoordCartesian(point_name, 0.0, 0.0, 0.0, "Global"),
            f"GetCoordCartesian({point_name})",
        )
        if len(values) != 3:
            raise EtabsApiError(f"GetCoordCartesian({point_name}): 返回参数数量异常。")
        return float(values[0]), float(values[1]), float(values[2])

    def _section_depth(self, section_name: str) -> float | None:
        """Return the section total height T3 when its OAPI type is supported."""
        if section_name in self._section_depth_cache:
            return self._section_depth_cache[section_name]
        result = self._read_section_depth(section_name)
        self._section_depth_cache[section_name] = result
        return result

    def _read_section_depth(self, section_name: str) -> float | None:
        from ETABSv1 import eFramePropType

        values = api_values(
            self.sap.PropFrame.GetTypeOAPI(section_name, eFramePropType.Rectangular),
            f"PropFrame.GetTypeOAPI({section_name})",
        )
        section_type = values[0]
        if section_type == eFramePropType.Rectangular:
            rectangle = api_values(
                self.sap.PropFrame.GetRectangle(section_name, None, None, 0.0, 0.0, 0, None, None),
                f"PropFrame.GetRectangle({section_name})",
            )
            return float(rectangle[2])
        if section_type == eFramePropType.ConcreteTee:
            tee = api_values(
                self.sap.PropFrame.GetConcreteTee(
                    section_name, None, None, 0.0, 0.0, 0.0, 0.0, 0.0, False, 0, None, None
                ),
                f"PropFrame.GetConcreteTee({section_name})",
            )
            return float(tee[2])
        if section_type == eFramePropType.General:
            general = api_values(
                self.sap.PropFrame.GetGeneral(section_name, None, None, 0.0, 0.0, *([0.0] * 12), 0, None, None),
                f"PropFrame.GetGeneral({section_name})",
            )
            return float(general[2])
        return None
