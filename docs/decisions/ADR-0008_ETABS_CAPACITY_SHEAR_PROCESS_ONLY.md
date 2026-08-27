---
id: ADR-0008
type: decision
status: accepted
owner: project
created: 2026-08-10
updated: 2026-08-10
decision_date: 2026-08-10
related:
  - SPEC-DET-001_ACI318_14_DETAILING.md
  - SPEC-DESIGN-001_RESULT_SEMANTICS.md
  - ARCH-003_DETAILING_CONSTRUCTABILITY_PIPELINE.md
  - ADR-0004_DETAILING_POLICY_SEPARATION.md
  - ADR-0006_REBAR_CONFLICT_RESOLUTION_ORDER.md
supersedes:
  - ADR-0007
superseded_by: []
---

# ADR-0008：精确结构体系解析与容量剪力过程审计

## 决策

1. 梁用途和结构体系分开保存。`PB/SB` 只表示主梁或次梁；ETABS 明确定义的
   `Non Sway`、`Sway Ordinary`、`Sway Intermediate`、`Sway Special` 分别采用
   `NON_SWAY`、`OMF`、`IMF`、`SMF`，不能被 PB/SB 或 API 结果静默覆盖。
2. 只有原始值为 `Program Determined` 时使用项目默认 `SMF`，并在成果中标明“项目默认”；
   缺失、未知或同一处理跨内无法分段的混合类型采用 `UNRESOLVED`，输出候选但标红复核。
3. SMF 容量剪力先读取 ETABS 公共表
   `Concrete Beam Shear Envelope - ACI 318-14`。确认有效时保留组合、站点、状态、单位和
   模型身份；`VMajorArea/VRebar` 只能按横向钢筋需求解释，不能直接当作力值。
4. 最终采用纵筋计算 `Mpr` 和处理后梁跨的引擎复核值。当 API 数值不可用但最终配筋和重力
   剪力足够时，使用 `ENGINE_MPR_FALLBACK`；两类来源都不成立时保持
   `CAPACITY_SHEAR_SOURCE_UNVERIFIED`，不得假定通过。
5. 容量剪力数值、`Mpr`、`Vu`、控制组合、计算公式和来源只写入
   `_history/<run_id>/intermediate/capacity_shear_audit_<run_id>.jsonl`。交付 Excel 的三张表
   不包含这些数值、公式或隐藏辅助列，只显示 `PASS`、`REVIEW_REQUIRED` 或
   `REDESIGN_REQUIRED` 等综合状态及通用复核提示。

## 原因与安全顺序

安全相关的读取值优先于施工便利性。先保留 ETABS 明确类型、设计结果来源和最终实配对应的
容量剪力；只有在安全输入已经确定后，才进行直径、层数、箍筋模数和梁线协调。类型交界处
局部拆分，不能为了整齐而让一处不兼容阻断整条梁线。SMF 端部主要受弯纵筋采用项目 D20
下限，避免与最小 `@100` 箍筋模数和 `6d_b` 限值冲突；该限制不扩展到 Non Sway、OMF、
IMF、架立筋或腰筋。

## 实现边界

- ETABS OAPI/COM 只在读取阶段单线程调用；逐跨 `Mpr`、容量剪力和选箍使用不可变输入，
  后续可在证明性能瓶颈后受控并行。
- API 数据、引擎回退和失败原因逐梁写入 JSONL，并在 manifest 中记录路径、哈希和统计。
- Excel 由 payload 渲染，不参与配筋选择；渲染器必须过滤所有 `capacity_shear_*` 字段。
- 本决定不改变只读边界、ACI 318-14 版本、梁跨处理算法或模型写回策略。

## 验证要求

必须覆盖五种 ETABS 原始类型、PB/SB 同规则解析、Program Determined 默认与冲突提示、混合
类型分段、API 有效/缺失/回退、三张 Excel 无容量剪力字段，以及 ETABS 22/23 实机 Shear
Envelope 对比。离线测试通过不等同于实机验收。
