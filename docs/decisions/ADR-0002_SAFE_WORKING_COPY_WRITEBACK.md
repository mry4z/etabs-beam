---
id: ADR-0002
type: decision
status: accepted
owner: project
created: 2026-07-16
updated: 2026-07-25
decision_date: 2026-07-16
related:
  - SPEC-002
  - ARCH-001
supersedes: []
superseded_by:
---

# ADR-0002：工作副本和 fail-closed 写回事务

## Context

方向和名称写回可能影响端部属性、荷载、释放、分析模型和设计结果。真实模型又包含跨 Span、多截面和需求等价异常对象。

## Decision

默认只读。所有写回仅在不可覆盖的工作副本上执行；先生成计划和阻塞清单，再由显式 `allow_mutation` 执行。任何无法证明安全的对象默认阻塞。I/J 使用 `EditFrame.ChangeConnectivity`，不使用只能旋转局部 2/3 轴的 `SetLocalAxes` 代替。

## Options Considered

- 直接修改原始 EDB：流程短，但不可接受。
- 遇异常静默跳过并仍标全量成功：产出更多，但失去审计边界。
- 工作副本 + fail-closed + 可选部分写回：选用。

## Rejected Tradeoffs

放弃直接操作原模型的速度，以及在阻塞项存在时生成看似完整编号的便利。

## Consequences

- 需要源/副本哈希、检查点、回滚、重开和全属性审计。
- 部分写回必须保持 `partial` 限制，阻塞对象不会自动销项。
- 写回后重新分析设计并重读结果，不能继续使用旧缓存。

## Reconsideration Triggers

只有 ETABS 提供可证明等价且原子化的官方事务 API，或项目建立更强的模型版本/恢复机制时才重新评估。
