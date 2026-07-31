---
id: SPEC-DESIGN-001
type: spec
status: accepted
owner: project
created: 2026-07-25
updated: 2026-07-25
related:
  - SPEC-DET-001
  - REF-API-DESIGN-RESULTS
supersedes:
  - LEGACY-DESIGN-RESULTS
superseded_by:
---

# SPEC-DESIGN-001：ETABS 梁设计结果语义

## 1. 范围

本 Spec 定义 ETABS 22/23 混凝土梁设计结果到内部领域字段的语义。调用签名属于 Reference，配筋规则属于 Detailing Spec。

## 2. 字段要求

| ID | ETABS / 内部语义 |
|---|---|
| DES-R001 | 旧 `GetSummaryResultsBeam.TopArea/BotArea` 对应 `AsTop/AsBot`，不含受扭纵筋，但已受最小配筋率下限控制，只作兼容和审计。 |
| DES-R002 | `_2.TopArea/BotArea` 对应 `AsTopTotal/AsBotTotal`，必须映射为 `top_total_area/bottom_total_area`，不得命名为纯受弯面积。 |
| DES-R003 | `VMajorArea` 是抗剪箍筋 `Av/s`；`TTArea` 是抗扭闭合箍单腿 `At/s`；`TLArea` 是总抗扭纵筋 `Al`。 |
| DES-R004 | 真实受弯排布需求逐 station 计算 `AsFlex=max(0, AsTotal-Al/2)`；不得用不同 station 的区域峰值相减。 |
| DES-R005 | `TopAreaProvided/BotAreaProvided=-1` 表示不可用，schema/Excel 归一化为空值，同时保留原始证据。 |
| DES-R006 | 保留原始字段名、station、组合、单位、Req/Min/Provided、error 和 warning。 |
| DES-R007 | ETABS API `ret=0` 只证明调用成功；设计 error/warning 是工程结果，必须报告，不能吞掉。 |
| DES-R008 | 空设计数组、未知字段映射和未完成设计必须显式阻塞或报告，不能解释为零需求。 |
| DES-R009 | 当前配筋策略执行前必须确认设计规范为 ACI 318-14。 |
| DES-R010 | 所有数值在 `N_mm_C` 上下文读取，并恢复原单位。 |

## 3. 兼容边界

早期基础梁表可以继续消费旧接口，但不得把旧字段与 `_2` Total 字段混在同一计算路径。正式配筋和三表工作流使用 `_2`，并在结果对象中明确标识来源。

## 4. 验收

- ETABS 22.7 和 23.3 的 `_2` 适配样本通过。
- Provided `-1`、空数组、error/warning 和未知规范均有测试。
- `AsFlex` 逐 station 计算测试覆盖不同控制 station。
- Excel/JSON 列名与内部语义一致。
