# ETABS 混凝土梁设计结果映射

当前实现调用：`SapModel.DesignConcrete.GetSummaryResultsBeam`。

在 pythonnet 3.x 中，调用需传入 `NumberItems` 和 14 个数组占位参数：

```python
result = sap.DesignConcrete.GetSummaryResultsBeam(
    frame_name, 0, *([None] * 14), eItemType.Objects
)
```

返回的 ETABS 数组被映射为：

| ETABS 字段 | `DesignStation` 字段 |
|---|---|
| `Location` | `location` |
| `TopCombo` / `TopArea` | `top_flexural_combo` / `top_flexural_area`（不含受扭纵筋） |
| `BotCombo` / `BotArea` | `bottom_flexural_combo` / `bottom_flexural_area`（不含受扭纵筋） |
| `VMajorCombo` / `VMajorArea` | `shear_combo` / `shear_area` |
| `TLCombo` / `TLArea` | `torsion_longitudinal_combo` / `torsion_longitudinal_area`（仅受扭需求，不是总纵筋） |
| `TTCombo` / `TTArea` | `torsion_trans_combo` / `torsion_trans_area` |
| `ErrorSummary` / `WarningSummary` | `error` / `warning` |

CSI 官方文档说明：`TopArea` / `BotArea` 是弯矩所需纵筋总面积，明确不含受扭钢筋；`TLArea` 是受扭所需纵筋总面积。调用成功只表示 API 读取成功；`error` 和 `warning` 中的内容是 ETABS 的设计校核信息，应保留在导出结果中，不应被程序吞掉。

所有数值在工作流临时切换到 `N_mm_C` 后读取。完整实测说明见 [ETABS_OAPI_VALIDATION.md](../ETABS_OAPI_VALIDATION.md)。
