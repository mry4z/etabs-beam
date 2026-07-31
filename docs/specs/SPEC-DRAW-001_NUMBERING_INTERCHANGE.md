---
id: SPEC-DRAW-001
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
  - LEGACY-DRAWING-NUMBERING
superseded_by:
---

# SPEC-DRAW-001：编号、方向与图纸交换

## 1. 目标

模型、Excel、DXF、JSON 和 CAD 必须消费同一有向 Beam Line/Span 结果，避免局部坐标、编号和梁表左/中/右互相漂移。

## 2. 编号要求

| ID | 要求 |
|---|---|
| DRAW-R001 | 交付 Span 编号为 `<楼层>-<PB|SB><梁线序号>-<跨号>`，例如 `F1-PB5-3`；跨号不含 `S`。 |
| DRAW-R002 | 同一 Span 内 Object 为 `<Span编号>.1`, `.2`, ...，顺序沿 I→J。 |
| DRAW-R003 | Excel、DXF、JSON、工作簿和写回调用同一 formatter/resolver，不得各自拼接字符串。 |
| DRAW-R004 | GUID 是身份，编号是可变投影；新增对象导致编号变化时必须生成 added/removed/renumbered 差异。 |
| DRAW-R005 | 未受 override 影响的楼层和梁线应保持编号稳定。 |

## 3. 方向要求

| ID | 要求 |
|---|---|
| DRAW-R006 | Beam Line 保存唯一 `i_point/j_point` 和 `local_1_direction`。 |
| DRAW-R007 | 模型 Object I→J、局部 1 轴、Span 路径、梁表左中右和 CAD 文字基线必须同向。 |
| DRAW-R008 | CAD 不得为屏幕可读性单独旋转文字 180°。 |
| DRAW-R009 | 方向缺失、零向量、路径切线不一致或 CAD 基线与 local 1 点积为负时阻止正式出图。 |

## 4. 交换数据要求

正式交换包至少包含：

- `schema_version`、生成时间、run ID、模型路径/哈希、源/副本标识。
- ETABS/API、Git/dirty、配置、拓扑/编号算法版本和单位。
- Beam Line/Span ID、PB/SB、楼层、GUID、当前 Object 名、I/J 路径和三维坐标。
- 支承来源、弧形/悬挑/多截面状态、异常和人工 override 来源。
- 设计/配筋引用或稳定关联键，不在多个文件复制需求数字。

字段见 [Interchange Schemas](../reference/schemas/INTERCHANGE_SCHEMAS.md)。

## 5. 模型修改边界

模型整理和图纸导出是两个阶段。写回必须生成预览、使用工作副本、显式确认并按 GUID 验证；CAD 只消费确认后的交换数据，不直接修改 ETABS。

## 6. 验收顺序

1. 拓扑不变量通过。
2. I→J 与模型写回计划一致。
3. 编号重复运行稳定。
4. DXF 一跨一条 polyline，文字方向一致。
5. JSON schema 和哈希完整。
6. CAD 导入后与 Excel 左/中/右抽样对照。
