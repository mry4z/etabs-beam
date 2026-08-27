"""ETABS concrete-frame design-result reader."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.core.etabs_connector import EtabsApiError, api_values
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation


@dataclass(frozen=True)
class CapacityShearPoint:
    """One normalized row from ETABS' ACI 318-14 beam shear envelope."""

    object_name: str
    location_mm: float
    combination: str
    shear_kN: float
    status: str = ""
    table_version: int | None = None
    source: str = "Concrete Beam Shear Envelope - ACI 318-14"
    units: str = "N_mm_C"


class EtabsResultProvider:
    """Read ``DesignConcrete.GetSummaryResultsBeam`` for an object-level beam."""

    def __init__(self, sap_model: Any) -> None:
        self.sap = sap_model

    def get_seismic_framing_types(self) -> dict[str, str]:
        """Read ETABS-resolved seismic frame types through the public OAPI.

        This is an audit companion to the model overwrite table.  Explicit
        overwrite values remain the detailing input; the resolved values are
        retained to explain what ETABS itself selected for Program Determined.
        Hosts/builds without this capability return an empty mapping so the
        offline and older snapshot paths remain usable.
        """

        method = getattr(getattr(self.sap, "DesignConcrete", None), "GetSeismicFramingType", None)
        if method is None:
            return {}
        try:
            from ETABSv1 import eItemType

            item_type = eItemType.Objects
        except Exception:
            item_type = None
        try:
            result = method(None, 0, None, None, item_type)
        except TypeError:
            # A few pythonnet hosts require an empty object name for the bulk
            # overload even when eItemType.Objects is supplied.
            result = method("", 0, None, None, item_type)
        values = api_values(result, "DesignConcrete.GetSeismicFramingType")
        if len(values) != 3:
            raise EtabsApiError("DesignConcrete.GetSeismicFramingType: 返回参数数量异常。")
        count, names, types = int(values[0]), list(values[1] or ()), list(values[2] or ())
        if count < 0 or len(names) != count or len(types) != count:
            raise EtabsApiError("DesignConcrete.GetSeismicFramingType: 数组长度不一致。")
        return {str(name): str(frame_type) for name, frame_type in zip(names, types)}

    def get_beam_shear_envelope(self) -> dict[str, tuple[CapacityShearPoint, ...]]:
        """Read the public ACI 318-14 beam shear-envelope table.

        ``Shear`` is a force result and ``VRebar`` is an Av/s result; they are
        intentionally kept as separate normalized fields.  Missing tables or
        malformed rows raise a checked API error for the caller to record as a
        process-only fallback condition.
        """

        table_key = "Concrete Beam Shear Envelope - ACI 318-14"
        values = api_values(
            self.sap.DatabaseTables.GetTableForDisplayArray(
                table_key,
                None,
                "",
                0,
                None,
                0,
                None,
            ),
            f"DatabaseTables.GetTableForDisplayArray({table_key})",
        )
        if len(values) != 5:
            raise EtabsApiError(f"{table_key}: 返回参数数量异常。")
        _requested, table_version, included_fields, record_count, flat_data = values
        fields = [str(field) for field in list(included_fields or ())]
        required = ("UniqueName", "Location", "VCombo", "Shear", "Status")
        if any(field not in fields for field in required):
            raise EtabsApiError(f"{table_key}: 缺少字段 {required!r}。")
        data = [str(value) for value in list(flat_data or ())]
        count = int(record_count)
        width = len(fields)
        if count < 0 or len(data) != count * width:
            raise EtabsApiError(f"{table_key}: 记录数或数据宽度不一致。")
        index = {field: fields.index(field) for field in required}
        grouped: dict[str, list[CapacityShearPoint]] = {}
        for offset in range(0, len(data), width):
            row = data[offset : offset + width]
            try:
                point = CapacityShearPoint(
                    object_name=row[index["UniqueName"]],
                    location_mm=float(row[index["Location"]]),
                    combination=row[index["VCombo"]],
                    shear_kN=abs(float(row[index["Shear"]])),
                    status=row[index["Status"]],
                    table_version=int(table_version),
                )
            except (TypeError, ValueError) as exc:
                raise EtabsApiError(f"{table_key}: 存在无法解析的剪力记录。") from exc
            grouped.setdefault(point.object_name, []).append(point)
        return {name: tuple(points) for name, points in grouped.items()}

    def get_design_preferences(self) -> dict[str, str]:
        """Read model-level ACI 318-14 concrete-frame design preferences."""

        table_key = "Concrete Frame Design Preferences - ACI 318-14"
        values = api_values(
            self.sap.DatabaseTables.GetTableForDisplayArray(
                table_key, None, "", 0, None, 0, None
            ),
            f"DatabaseTables.GetTableForDisplayArray({table_key})",
        )
        if len(values) != 5:
            raise EtabsApiError(f"{table_key}: 返回参数数量异常。")
        _requested, _version, fields, count, flat = values
        names = [str(field) for field in list(fields or ())]
        data = [str(value) for value in list(flat or ())]
        if count < 0 or len(names) == 0 or len(data) != int(count) * len(names):
            raise EtabsApiError(f"{table_key}: 记录数或数据宽度不一致。")
        # Preferences are normally one row.  Returning the first row as a
        # name/value map keeps the model-level context compact and auditable.
        return {
            names[index]: data[index]
            for index in range(len(names))
        }

    def get_beam_design(self, beam_name: str) -> BeamDesignResult:
        try:
            from ETABSv1 import eItemType

            item_type = eItemType.Objects
        except Exception as exc:
            raise EtabsApiError("ETABSv1 未加载；请先连接 ETABS。") from exc

        values = api_values(
            self.sap.DesignConcrete.GetSummaryResultsBeam(beam_name, 0, *([None] * 14), item_type),
            f"GetSummaryResultsBeam({beam_name})",
        )
        if len(values) != 15:
            raise EtabsApiError(f"GetSummaryResultsBeam({beam_name}): 返回参数数量异常。")

        count = int(values[0])
        if any(column is None for column in values[1:]):
            raise EtabsApiError(f"GetSummaryResultsBeam({beam_name}): ETABS 返回空设计数组。")
        columns = [list(column) for column in values[1:]]
        if count == 0:
            return BeamDesignResult(
                beam_name,
                status=DesignResultStatus.NO_RESULTS,
                status_message="ETABS 未返回混凝土梁设计站点。",
            )
        if any(len(column) < count for column in columns):
            raise EtabsApiError(f"GetSummaryResultsBeam({beam_name}): 结果数组长度不一致。")

        stations = tuple(
            DesignStation(
                location=float(columns[1][index]),
                top_flexural_combo=str(columns[2][index]),
                top_flexural_area=float(columns[3][index]),
                bottom_flexural_combo=str(columns[4][index]),
                bottom_flexural_area=float(columns[5][index]),
                shear_combo=str(columns[6][index]),
                shear_area=float(columns[7][index]),
                torsion_longitudinal_combo=str(columns[8][index]),
                torsion_longitudinal_area=float(columns[9][index]),
                torsion_trans_combo=str(columns[10][index]),
                torsion_trans_area=float(columns[11][index]),
                error=str(columns[12][index]),
                warning=str(columns[13][index]),
                source_object=str(columns[0][index]),
                units="N_mm_C",
                result_source="GetSummaryResultsBeam",
            )
            for index in range(count)
        )
        return _beam_result(
            beam_name,
            stations,
            result_source="GetSummaryResultsBeam",
        )

    def get_beam_design_total(self, beam_name: str) -> BeamDesignResult:
        """Read ETABS 22/23 ``GetSummaryResultsBeam_2`` total-rebar fields.

        This is opt-in while the legacy Excel workflow remains on the original
        API.  The returned stations retain the legacy field names for
        compatibility and additionally expose ``as_total_*``, minimum and
        provided areas for the detailing engine.
        """

        try:
            from ETABSv1 import eItemType

            item_type = eItemType.Objects
        except Exception as exc:
            raise EtabsApiError("ETABSv1 未加载；请先连接 ETABS。") from exc

        values = api_values(
            self.sap.DesignConcrete.GetSummaryResultsBeam_2(beam_name, 0, *([None] * 23), item_type),
            f"GetSummaryResultsBeam_2({beam_name})",
        )
        if len(values) != 24:
            raise EtabsApiError(f"GetSummaryResultsBeam_2({beam_name}): 返回参数数量异常。")
        count = int(values[0])
        if count == 0:
            return BeamDesignResult(
                beam_name,
                status=DesignResultStatus.NO_RESULTS,
                status_message="ETABS 未返回混凝土梁设计站点。",
                source_objects=(beam_name,),
                units="N_mm_C",
                result_source="GetSummaryResultsBeam_2",
            )
        if any(column is None for column in values[1:]):
            raise EtabsApiError(f"GetSummaryResultsBeam_2({beam_name}): ETABS 返回空设计数组。")
        columns = [list(column) for column in values[1:]]
        if any(len(column) < count for column in columns):
            raise EtabsApiError(f"GetSummaryResultsBeam_2({beam_name}): 结果数组长度不一致。")

        stations: list[DesignStation] = []
        for index in range(count):
            raw_top_provided = float(columns[6][index])
            raw_bottom_provided = float(columns[11][index])
            stations.append(
                DesignStation(
                    location=float(columns[1][index]),
                    top_flexural_combo=str(columns[2][index]),
                    top_flexural_area=float(columns[3][index]),
                    bottom_flexural_combo=str(columns[7][index]),
                    bottom_flexural_area=float(columns[8][index]),
                    shear_combo=str(columns[12][index]),
                    shear_area=float(columns[13][index]),
                    torsion_longitudinal_combo=str(columns[17][index]),
                    torsion_longitudinal_area=float(columns[18][index]),
                    torsion_trans_combo=str(columns[19][index]),
                    torsion_trans_area=float(columns[20][index]),
                    error=str(columns[21][index]),
                    warning=str(columns[22][index]),
                    top_total_area=float(columns[3][index]),
                    bottom_total_area=float(columns[8][index]),
                    top_min_area=float(columns[5][index]),
                    bottom_min_area=float(columns[10][index]),
                    top_provided_area=_provided_or_none(raw_top_provided),
                    bottom_provided_area=_provided_or_none(raw_bottom_provided),
                    raw_top_provided_area=raw_top_provided,
                    raw_bottom_provided_area=raw_bottom_provided,
                    source_object=str(columns[0][index]),
                    units="N_mm_C",
                    result_source="GetSummaryResultsBeam_2",
                )
            )
        return _beam_result(
            beam_name,
            tuple(stations),
            result_source="GetSummaryResultsBeam_2",
        )


def _provided_or_none(value: float) -> float | None:
    """Normalize ETABS' -1 unavailable sentinel while preserving valid zero."""

    return None if value == -1.0 else value


def _beam_result(
    beam_name: str,
    stations: tuple[DesignStation, ...],
    *,
    result_source: str,
) -> BeamDesignResult:
    errors = tuple(dict.fromkeys(station.error.strip() for station in stations if station.error.strip()))
    warnings = tuple(dict.fromkeys(station.warning.strip() for station in stations if station.warning.strip()))
    status = DesignResultStatus.PARTIAL if errors else DesignResultStatus.AVAILABLE
    messages = [*(f"ERROR: {value}" for value in errors), *(f"WARNING: {value}" for value in warnings)]
    source_objects = tuple(dict.fromkeys(station.source_object or beam_name for station in stations))
    return BeamDesignResult(
        beam_name,
        stations,
        status,
        "；".join(messages),
        source_objects,
        "N_mm_C",
        result_source,
    )
