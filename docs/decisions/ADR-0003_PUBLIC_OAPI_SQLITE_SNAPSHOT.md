---
id: ADR-0003
type: decision
status: accepted
owner: project
created: 2026-07-23
updated: 2026-07-25
decision_date: 2026-07-23
related:
  - ARCH-001
  - TASK-P2-SQLITE-001
supersedes:
  - LEGACY-SQLITE-ROUTE
superseded_by:
---

# ADR-0003：公开 OAPI 到项目自建 SQLite 快照

## Context

逐 Object OAPI 往返成本高，离线拓扑、Excel 和 DXF 需要稳定可重放数据。ETABS 23 界面存在 SQLite 导出，但当前公开 `ETABSv1.dll` 没有对应自动导出方法；内部程序集不属于公开合同。

## Decision

采用“公开 OAPI 批量表格读取 -> 项目自建只读 SQLite 快照”。快照只服务离线读取，不替代 EDB，不承担写回。模型修改、分析和设计继续使用公开 OAPI。

## Options Considered

- 继续全部逐 Object OAPI：兼容但往返多，保留作对照回退。
- 反射调用 ETABS 内部 SQLite 类：可能快，但补丁升级风险不可控，拒绝。
- GUI 自动化导出：不适合作为无人值守正式依赖，人工样本可用。
- 项目自建快照：边界可控、可版本化，选用。

## Rejected Tradeoffs

放弃直接复用 ETABS 内部实现的潜在性能优势，并接受项目维护 schema、映射和等价测试的成本。

## Consequences

- 快照使用 `*.partial`、完整性检查和原子改名。
- 元数据记录模型/API/单位/设计规范/表键/字段映射版本。
- 写回后旧快照立即过期。
- 在实现和等价验证完成前，Current Baseline 保持 `BLOCKED`。

## Reconsideration Triggers

CSI 发布公开、文档化的 SQLite 自动化接口后，可增加 `NativeEtabsSqliteSource` 与自建快照对照。
