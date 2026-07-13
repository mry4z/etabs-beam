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
            )
            for index in range(count)
        )
        return BeamDesignResult(beam_name, stations)
