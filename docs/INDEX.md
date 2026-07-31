---
id: REF-DOCS-INDEX
type: reference
status: current
owner: project
created: 2026-07-25
updated: 2026-07-31
related:
  - GOV-001
  - BASELINE-001
supersedes: []
superseded_by:
---

# 开发文档索引

本页是项目开发文档的唯一入口。README 只负责项目识别和快速开始；目标、当前事实、决策、执行状态和验证证据分别由下列文档承担。

## 权威入口

| 要回答的问题 | 权威文档 |
|---|---|
| 文档如何分类、冲突如何裁决 | [文档治理纲领](governance/DOCUMENTATION_GOVERNANCE.md) |
| 系统必须实现什么 | [产品范围](specs/SPEC-001_PRODUCT_SCOPE.md)及领域 Spec |
| 当前阶段必须达到什么 | [P1 配筋工程闭环](specs/milestones/MS-P1-001_REBAR_ENGINEERING_CLOSURE.md) |
| 系统当前如何组织 | [系统架构](architecture/ARCH-001_SYSTEM_ARCHITECTURE.md) |
| 为什么选择某个方案 | [决策记录](decisions/) |
| 当前实际验证到哪里 | [Current Baseline](state/CURRENT_BASELINE.md) |
| 下一步执行什么 | [Roadmap](delivery/ROADMAP.md)和[任务登记表](delivery/tasks/TASK_REGISTER.md) |
| 一次变更如何实施 | [活动实施计划](delivery/plans/active/) |
| 某次运行具体证明了什么 | [Validation Records](state/validation/) |
| 如何运行和开发 | [用户指南](guides/USER_GUIDE.md)和[开发指南](guides/DEVELOPMENT.md) |
| 如何构建和加载 ETABS 插件 | [ETABS 插件指南](guides/ETABS_PLUGIN.md) |
| API、OAPI 和 schema 如何查阅 | [Reference](reference/) |

## 领域规范

- [产品范围](specs/SPEC-001_PRODUCT_SCOPE.md)
- [工程不变量与安全边界](specs/SPEC-002_ENGINEERING_INVARIANTS.md)
- [梁线与结构分跨](specs/SPEC-TOPO-001_BEAM_LINE_SPAN.md)
- [人工拓扑 Override](specs/SPEC-TOPO-002_MANUAL_OVERRIDE.md)
- [编号、方向与图纸交换](specs/SPEC-DRAW-001_NUMBERING_INTERCHANGE.md)
- [ETABS 设计结果语义](specs/SPEC-DESIGN-001_RESULT_SEMANTICS.md)
- [ACI 318-14 配筋排布](specs/SPEC-DET-001_ACI318_14_DETAILING.md)
- [ETABS 交互插件初始边界](specs/SPEC-PLUGIN-001_ETABS_INTERACTIVE_PLUGIN.md)

## 决策记录

- [ADR-0001：GUID 身份与两阶段改名](decisions/ADR-0001_GUID_IDENTITY_AND_RENAMING.md)
- [ADR-0002：工作副本与 fail-closed 写回](decisions/ADR-0002_SAFE_WORKING_COPY_WRITEBACK.md)
- [ADR-0003：公开 OAPI 到项目 SQLite 快照](decisions/ADR-0003_PUBLIC_OAPI_SQLITE_SNAPSHOT.md)
- [ADR-0004：规范策略与项目策略分离](decisions/ADR-0004_DETAILING_POLICY_SEPARATION.md)
- [ADR-0005：ETABS 插件采用 .NET 8 薄交互层](decisions/ADR-0005_ETABS_PLUGIN_THIN_LAUNCHER.md)

## 当前状态与证据

- 当前阶段：P1 配筋工程闭环。
- 当前综合离线门禁：
  [VAL-20260730-P2-ETABS-PLUGIN-V01](state/validation/VAL-20260730_P2_ETABS_PLUGIN_V01.md)，
  `212 passed, 1 skipped`，三个 .NET 项目均 `0 warnings, 0 errors`。
- 当前 ETABS cover / 25 mm 骨料配筋验证：
  [VAL-20260730-P1-ETABS-COVER-25MM-CURRENT](state/validation/VAL-20260730_P1_ETABS_COVER_25MM_CURRENT.md)，
  自动门禁 `208 passed, 1 skipped`，实模 run 成功但 P1 verdict 为 `partial`。
- writeback、分析、设计和全属性审计历史全链：
  [VAL-20260730-P1-V645-CURRENT-FULL-CHAIN](state/validation/VAL-20260730_P1_V645_CURRENT_FULL_CHAIN.md)。
- P2 SQLite 快照技术 spike：
  [VAL-20260730-P2-SQLITE-SNAPSHOT-SPIKE](state/validation/VAL-20260730_P2_SQLITE_SNAPSHOT_SPIKE.md)。
- P2 当前矩形梁 OAPI / SQLite 只读工作流：
  [VAL-20260730-P2-OAPI-SQLITE-WORKFLOW-PARITY](state/validation/VAL-20260730_P2_OAPI_SQLITE_WORKFLOW_PARITY.md)；
  剩余 Gate 见 [IP-P2-002](delivery/plans/active/IP-P2-002_SQLITE_DOMAIN_SOURCE.md)。
- ETABS 插件 v0.3.0（ETABS API DLL 可定位 + 便携无 node 生成带公式配筋 xlsx）：
  [VAL-20260731-P2-ETABS-PLUGIN-V03](state/validation/VAL-20260731_P2_ETABS_PLUGIN_V03.md)（`partial`；
  ETABS 菜单加载和 ETABS 23 待人工/对应环境验收）；v0.2.0 正式封装见
  [VAL-20260731-P2-ETABS-PLUGIN-V02](state/validation/VAL-20260731_P2_ETABS_PLUGIN_V02.md)，v0.1.x 入口
  类型空值修复历史见 [VAL-20260730-P2-ETABS-PLUGIN-V01](state/validation/VAL-20260730_P2_ETABS_PLUGIN_V01.md)。
- 文档重构审查：[REV-20260725-DOCUMENT-GOVERNANCE](state/reviews/REV-20260725_DOCUMENTATION_GOVERNANCE.md)。

P1 的保护层/骨料输入已解决；当前阻塞为 408 个实模异常处置、最新代码 writeback 全链和结构工程师冻结签署；具体边界以
[Current Baseline](state/CURRENT_BASELINE.md) 为准。

## 迁移与备份

- [迁移指导和源文件处置表](governance/DOCUMENT_MIGRATION_GUIDE.md)
- [重构验证记录](state/validation/VAL-20260725_DOCUMENT_RESTRUCTURE.md)
- [原文档 ZIP 备份](../backups/documentation/2026-07-25-pre-governance.zip)
- [备份 SHA-256 清单](../backups/documentation/2026-07-25-pre-governance-manifest.json)

旧文档路径保留短迁移页一个过渡周期。迁移页不是当前权威来源。
