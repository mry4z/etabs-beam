---
id: ADR-0001
type: decision
status: accepted
owner: project
created: 2026-07-16
updated: 2026-07-25
decision_date: 2026-07-16
related:
  - SPEC-002
  - SPEC-DRAW-001
supersedes: []
superseded_by:
---

# ADR-0001：GUID 作为身份并采用两阶段改名

## Context

ETABS Object Unique Name 会在交付编号写回时改变，Label/Story 也不能稳定标识对象；直接从旧名改为最终名还会遇到名称占用和互换冲突。

## Decision

内部身份统一使用 Object GUID。名称只作为可变交付投影。批量改名固定执行“当前名 -> 临时唯一名 -> 最终名”，写后按 GUID 解析当前名称并验证。

## Options Considered

- 以 Unique Name 为身份：实现简单，但改名后失去稳定关联。
- 以几何/顺序为身份：可用于辅助指纹，但模型编辑后可能漂移。
- GUID + 两阶段改名：稳定且能处理名称占用，选用。

## Rejected Tradeoffs

放弃了单阶段改名的较少 API 调用和更简单日志，也放弃了让文件顺序决定身份的便利。

## Consequences

- Snapshot、override、差异表和验证必须保存 GUID。
- ETABS 版本或模型操作导致 GUID 不可用时必须阻塞。
- 日志同时保留创建时名称和当前名称，便于人工审计。

## Verification

单元测试覆盖名称占用、幂等和失败回滚；历史实模重开证据见 [VAL-20260722](../state/validation/VAL-20260722-083439_V643_FULL_CHAIN.md)。
