"""ETABS concrete-frame design-result reader."""

from __future__ import annotations

from typing import Any

from src.core.etabs_connector import EtabsApiError, api_values
from src.design.provider import BeamDesignResult, DesignResultStatus, DesignStation


class EtabsResultProvider:
    """Read ``DesignConcrete.GetSummaryResultsBeam`` for an object-level beam."""

    def __init__(self, sap_model: Any) -> None:
        self.sap = sap_model

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
