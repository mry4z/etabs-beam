---
id: REF-API-DESIGN-RESULTS
type: reference
status: current
owner: project
created: 2026-07-25
updated: 2026-07-25
related:
  - SPEC-DESIGN-001
  - SPEC-DET-001
  - REF-API-001
  - REF-OAPI-001
supersedes:
  - LEGACY-DESIGN-RESULTS
superseded_by:
---

# ETABS 混凝土梁设计结果映射

本页说明 `src.design.etabs_result.EtabsResultProvider` 与 `src.design.provider` 的实际字段契约。
工程语义和验收要求以 [SPEC-DESIGN-001](../../specs/SPEC-DESIGN-001_RESULT_SEMANTICS.md) 为准。

## 1. 两个读取入口

```python
provider.get_beam_design(object_name)
```

调用 `GetSummaryResultsBeam(name, 0, *([None] * 14), eItemType.Objects)`，用于兼容基础梁表。

```python
provider.get_beam_design_total(object_name)
```

调用 `GetSummaryResultsBeam_2(name, 0, *([None] * 23), eItemType.Objects)`，用于 Total/Min/Provided
设计结果和配筋工作流。调用方必须明确结果来源，不得把两个接口的同名 ETABS 字段混成一种语义。

## 2. 领域对象

`BeamDesignResult` 包含：

- `beam_name`；
- `stations: tuple[DesignStation, ...]`；
- `status: DesignResultStatus`；
- `status_message`；
- `has_results` 和各类最大值只读属性。

`DesignResultStatus` 值为 `AVAILABLE`、`NO_RESULTS`、`SKIPPED`、`API_ERROR`、`PARTIAL`。空 station
不会被解释为零需求。

## 3. 旧接口字段

| ETABS 字段 | `DesignStation` 字段 | 语义 |
|---|---|---|
| `Location` | `location` | Object station。 |
| `TopCombo / TopArea` | `top_flexural_combo / top_flexural_area` | 顶部 `AsTop`，不含受扭纵筋，已含适用最小配筋下限。 |
| `BotCombo / BotArea` | `bottom_flexural_combo / bottom_flexural_area` | 底部 `AsBot`，不含受扭纵筋，已含适用最小配筋下限。 |
| `VMajorCombo / VMajorArea` | `shear_combo / shear_area` | 抗剪箍筋 `Av/s`。 |
| `TLCombo / TLArea` | `torsion_longitudinal_combo / torsion_longitudinal_area` | 总抗扭纵筋 `Al`。 |
| `TTCombo / TTArea` | `torsion_trans_combo / torsion_trans_area` | 抗扭闭合箍单腿 `At/s`。 |
| `ErrorSummary / WarningSummary` | `error / warning` | ETABS 设计校核信息。 |

## 4. `_2` Total/Min/Provided 字段

`get_beam_design_total()` 继续填充兼容字段，并额外填充：

| ETABS 字段 | `DesignStation` 字段 |
|---|---|
| `TopArea` | `top_total_area` |
| `BotArea` | `bottom_total_area` |
| `TopAreaMin` | `top_min_area` |
| `BotAreaMin` | `bottom_min_area` |
| `TopAreaProvided` | `top_provided_area` |
| `BotAreaProvided` | `bottom_provided_area` |

`_2.TopArea/BotArea` 是顶部/底部总纵筋，不是纯受弯面积。兼容字段
`top_flexural_area/bottom_flexural_area` 在该入口中保存相同原始 Total 值，因此 `_2` 调用方应使用
`top_total_area/bottom_total_area` 或 `as_total_*` 属性表达语义。

`as_flex_top_mm2` 和 `as_flex_bottom_mm2` 逐 station 计算：

```text
max(0, AsTotal - Al / 2)
```

不得用不同 station 的区域峰值相减。

## 5. Provided 哨兵值

ETABS 返回 `-1` 时表示 Provided 不可用，不表示负钢筋面积或零。当前 provider 为保留 OAPI 原值，会把
该值以 `-1.0` 存入 `top_provided_area/bottom_provided_area`。交换或 Excel 适配器必须按
`DES-R005` 输出空值，并在需要审计时另存原始值。

## 6. 返回码、空数组和提示

- `api_values()` 对非零 OAPI 返回码抛出 `EtabsApiError`。
- 旧接口要求去除状态码后有 15 个值；`_2` 要求 24 个值。
- `count == 0` 返回 `NO_RESULTS`；数组为 `None` 或短于 `count` 时失败，不补零。
- `error`、`warning` 是 ETABS 工程结果，即使 API 返回码为零也必须保留和导出。
- 正式设计读取在 `N_mm_C` 单位上下文中执行，退出后恢复原单位。

## 7. 使用边界

基础 `src.workflow` 使用旧接口；`src.cli` 的设计导出使用 `_2`。写回后导出时，CLI 强制先重新分析和
设计，再重新提取当前名称、几何、拓扑和结果，避免消费写回前缓存。
