"""Read beam geometry from ETABS without making design assumptions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.etabs_connector import EtabsApiError, api_values
from src.utils.geometry import Point3D, length


class SelectionError(RuntimeError):
    """Raised when no suitable beam objects are available."""


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

    @property
    def length(self) -> float:
        return length(self.start, self.end)


class GeometryExtractor:
    FRAME_OBJECT_TYPE = 2
    BEAM_DESIGN_ORIENTATION = 2

    def __init__(self, sap_model: Any) -> None:
        self.sap = sap_model

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
        values = api_values(self.sap.FrameObj.GetNameList(0, None), "FrameObj.GetNameList")
        if len(values) != 2:
            raise EtabsApiError("FrameObj.GetNameList: 返回参数数量异常。")
        _, names = values
        return [str(name) for name in list(names) if self._is_beam(str(name))]

    def extract(self, selection_mode: str = "all") -> list[BeamGeometry]:
        if selection_mode not in {"all", "selected"}:
            raise ValueError("selection_mode 必须是 all 或 selected。")
        names = self.all_beam_names() if selection_mode == "all" else self.selected_beam_names()
        if not names:
            raise SelectionError("没有可导出的梁。")
        return [self._extract_one(name) for name in names]

    def _is_beam(self, name: str) -> bool:
        from ETABSv1 import eFrameDesignOrientation

        values = api_values(
            self.sap.FrameObj.GetDesignOrientation(name, eFrameDesignOrientation.Beam),
            f"GetDesignOrientation({name})",
        )
        return bool(values) and int(values[0]) == self.BEAM_DESIGN_ORIENTATION

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
