# ETABS 混凝土梁设计结果映射

**更新日期**：2026-07-18
**当前配筋主路径**：ETABS 22/23 `GetSummaryResultsBeam_2` + ACI 318-14 排布引擎

项目同时保留两个读取入口：早期兼容梁表使用 `GetSummaryResultsBeam`；配筋引擎和正式三表工作流使用 ETABS 22/23 `GetSummaryResultsBeam_2`。调用方必须明确自己消费的是“旧弯矩字段”还是“Total 字段”。

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
| `VMajorCombo` / `VMajorArea` | `shear_combo` / `shear_area`（仅抗剪箍筋 `Av/s`，且已包含 18.6.5 capacity shear） |
| `TLCombo` / `TLArea` | `torsion_longitudinal_combo` / `torsion_longitudinal_area`（总抗扭纵筋 `Al`，不是顶部/底部总纵筋） |
| `TTCombo` / `TTArea` | `torsion_trans_combo` / `torsion_trans_area`（抗扭闭合箍单腿 `At/s`） |
| `ErrorSummary` / `WarningSummary` | `error` / `warning` |

CSI 官方文档说明：旧接口的 `TopArea` / `BotArea` 是不含受扭钢筋的顶部/底部纵筋面积；实模对照进一步确认它们对应数据库表的 `AsTop` / `AsBot`，已经按 `AsMinTop` / `AsMinBot` 施加最小配筋下限。公开 API 与数据库表均未暴露“施加最小配筋下限之前”的精确纯受弯面积。

`GetSummaryResultsBeam_2` 同时返回 `TopAreaReq/Min/Provided`、`BotAreaReq/Min/Provided` 和剪力的 Req/Min/Provided；已在 ETABS 22.7 和 23.3 实测。该接口的 `TopArea` / `BotArea` 对应数据库表 `AsTopTotal` / `AsBotTotal`，即受弯、受扭及适用时剪力共同作用后的顶部/底部总纵筋；不要将 `_2.TopArea` 继续映射为“弯矩纵筋”。数据库表还提供 `AtTrnTotal`。完整字段对照样本见 `build/output/reports/T3-F1-PB25-2.1_database_summary_api4572.csv`。

`TopAreaProvided` / `BotAreaProvided` 是模型用于设计校核的实际提供纵筋面积，不是需求面积。本样本返回 `-1`，表示没有可用的 provided 值；业务层必须把 `-1` 解释为“未设置/不可用”，不能当成负钢筋面积。当前数据类仍原样保存 `-1` 以保留 API 证据，正式 schema/Excel 适配器需完成空值归一化。该字段第一阶段只透传保存，第二阶段用于性能计算、非线性构件及柱/梁实配钢筋设置接口。

## 当前语义约束与待收敛项

- `_2.TopArea/BotArea` 必须以 `top_total_area/bottom_total_area` 消费；当前兼容数据模型还会把相同数值放入旧 `top_flexural_area/bottom_flexural_area` 字段，调用方不得据此误认为纯受弯面积。
- 每个 station 的真实受弯排布需求按 `AsFlex=max(0, AsTotal-Al/2)` 反算，禁止把不同 station 的区域峰值相减。
- `VMajorArea` 仅为抗剪箍筋 `Av/s`；`TTArea` 为抗扭闭合箍单腿 `At/s`；`TLArea` 为总抗扭纵筋 `Al`。
- 工作流必须先确认 ETABS 当前混凝土设计规范为 ACI 318-14；其他规范不得直接套用当前排布策略。
- 混合截面 Span、`Program Determined` 抗震类型、空设计数组和 ETABS error/warning 必须显式阻塞或报告，不得静默采用默认值。

调用成功只表示 API 读取成功；`error` 和 `warning` 中的内容是 ETABS 的设计校核信息，应保留在导出结果中，不应被程序吞掉。

所有数值在工作流临时切换到 `N_mm_C` 后读取。完整实测说明见 [ETABS_OAPI_VALIDATION.md](../ETABS_OAPI_VALIDATION.md)。
