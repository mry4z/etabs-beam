---
id: SPEC-TOPO-002
type: spec
status: accepted
owner: project
created: 2026-07-25
updated: 2026-07-25
related:
  - SPEC-TOPO-001
  - ADR-0001
  - REF-SCHEMA-001
supersedes:
  - LEGACY-MANUAL-OVERRIDE
superseded_by:
---

# SPEC-TOPO-002：人工梁线与梁跨 Override

## 1. 目的和边界

人工 override 处理弧形、短折线、复杂转角和多候选路径。创建或保存 override 本身不修改结构分析模型；后续显式写回事务可以在工作副本上调整 I/J 和 Unique Name。

## 2. 类型

- `MANUAL_LINE`：成员强制属于同一 Beam Line，仍按正常支承规则分跨。
- `MANUAL_SPAN`：成员强制组成一个 Span，并保存明确 I→J。

## 3. 要求

| ID | 要求 |
|---|---|
| OVR-R001 | 外部版本化 JSON 是权威；ETABS Group 只镜像成员和颜色。 |
| OVR-R002 | 成员身份使用 GUID；创建时名称只供审计。 |
| OVR-R003 | 同组成员必须为同楼层梁 Object，GUID 唯一，端点图为一条连通开链。断开、分叉、重复边和闭环默认拒绝。 |
| OVR-R004 | 选择顺序不作为路径顺序；程序由连通图排序，用户只确认 I 端或反转方向。 |
| OVR-R005 | `MANUAL_SPAN` 遇内部真实支承时产生 `MANUAL_SPAN_SUPPORT_CONFLICT`。只有二级 `force_support_override`、原因、操作者和冲突清单齐全时才可继续。 |
| OVR-R006 | 自动批处理不得生成 `force_support_override`。 |
| OVR-R007 | 混合截面可保存拓扑组，但配筋和写回仍按独立 fail-closed 规则处理。 |
| OVR-R008 | 有效性优先级为 `MANUAL_SPAN > MANUAL_LINE + 自动分跨 > 自动算法`。 |
| OVR-R009 | 未受影响楼层和梁线保留原编号；受影响范围生成逐 GUID 旧值/新值差异并取得确认。 |
| OVR-R010 | 第二轮写回使用工作副本、检查点和两阶段改名；取消确认不得修改模型。 |
| OVR-R011 | GUID 缺失、几何不可解释变化、失去连通、新增支承、组重叠或模型指纹不匹配时标记 `STALE/BLOCKED`，不得静默回退自动结果。 |
| OVR-R012 | 同一 override 重放应得到零变化或有解释的最小变化。 |

## 4. 状态机

```text
AUTO_PLANNED -> AUTO_WRITTEN -> MANUAL_DRAFT -> MANUAL_CONFIRMED
             -> RENUMBER_PLANNED -> RENUMBER_WRITTEN -> VERIFIED
```

任一步取消或验证失败，模型保持最近已确认检查点，未确认结果不得进入正式 DXF、JSON、Excel 或写回计划。

## 5. 数据契约

必需字段包括 schema 版本、模型指纹、group ID/type、base run、楼层、按 I→J 排序的 GUID、创建时名称、自动编号、I 端点、原因、支承冲突、force 标志、创建者和时间。具体见 [Interchange Schemas](../reference/schemas/INTERCHANGE_SCHEMAS.md)。

## 6. 验收

- 小直线段弧形梁可人工组成一跨。
- 含柱支承的人工 Beam Line 仍正确分跨。
- 断开、分叉、环、跨楼层和冲突组拒绝。
- Object 改名后由 GUID 恢复成员、顺序和 I→J。
- 第二轮只改变受影响范围；重放幂等。
- 失效组进入诊断和独立审计图层。
