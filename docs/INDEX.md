---
id: REF-DOCS-INDEX
type: reference
status: current
owner: project
created: 2026-07-25
updated: 2026-09-08
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

- v2 次梁上筋优选与 PB38-1 诊断：[2026-08-27 验证记录](state/validation/VAL-20260827_SB_TOP_AND_PB38_DIAGNOSIS.md)。
- v2 分塔功能：[独立迁移记录](state/validation/VAL-20260827_TOWER_MIGRATION.md)、[选择集保护与连续运行修复](state/validation/VAL-20260827_TOWER_SELECTION_RECOVERY.md)；源码离线验证与实模插件验收分开记录。
- v2 梁图导出整改：[2026-09-08 验证记录](state/validation/VAL-20260908_DRAWING_EXPORT_REVIEW.md)；梁组 DXF/CSV 共享空间编号，结构跨默认交付编号不含 `S`，文字可读性翻转不改变路径。
- PB/SB 单表静态 Excel 与 JSON 审计：[2026-08-27 验证记录](state/validation/VAL-20260827_REBAR_SINGLE_SHEET_STATIC.md)；实模和工程签发仍单独受 Current Baseline 约束。
- 当前梁表复核范围与 ETABS Frame Design 设计力来源确认：[2026-09-10 验证记录](state/validation/VAL-20260910_REBAR_REVIEW_SCOPE.md)；锚固、搭接、抗扭周边布置为超远期待办，非当前 Excel 红字门禁。
- 当前阶段：P1 配筋工程闭环。
- 历史离线门禁（不是当前 v2 dirty worktree 验收）：
  [VAL-20260730-P2-ETABS-PLUGIN-V01](state/validation/VAL-20260730_P2_ETABS_PLUGIN_V01.md)。
- 历史 ETABS cover / 25 mm 骨料配筋验证（不是当前 v2 dirty worktree 验收）：
  [VAL-20260730-P1-ETABS-COVER-25MM-CURRENT](state/validation/VAL-20260730_P1_ETABS_COVER_25MM_CURRENT.md)。
- writeback、分析、设计和全属性审计历史全链（仅限其记录的代码身份）：
  [VAL-20260730-P1-V645-CURRENT-FULL-CHAIN](state/validation/VAL-20260730_P1_V645_CURRENT_FULL_CHAIN.md)。
- P2 SQLite 快照技术 spike：
  [VAL-20260730-P2-SQLITE-SNAPSHOT-SPIKE](state/validation/VAL-20260730_P2_SQLITE_SNAPSHOT_SPIKE.md)。
- P2 矩形梁 OAPI / SQLite 只读历史 profile（非当前 v2 能力）：
  [VAL-20260730-P2-OAPI-SQLITE-WORKFLOW-PARITY](state/validation/VAL-20260730_P2_OAPI_SQLITE_WORKFLOW_PARITY.md)；
  剩余 Gate 见 [IP-P2-002](delivery/plans/active/IP-P2-002_SQLITE_DOMAIN_SOURCE.md)。
- 当前 v2 ETABS 插件源码与交互边界：
  [ETABS 插件指南](guides/ETABS_PLUGIN.md)；已有 v0.4.1 包记录见
  [RELEASE_NOTES](../plugins/etabs/RELEASE_NOTES.md)；当前策略已重建
  [v0.4.1-r6d 可加载包](../build/etabs-plugin/v0.4.1-v2-etabs23-loadable-20260910-r6d.zip)，未进行实模验收。
- 历史 ETABS 插件 v0.3.0（ETABS API DLL 可定位 + 便携无 node 生成带公式配筋 xlsx）：
  [VAL-20260731-P2-ETABS-PLUGIN-V03](state/validation/VAL-20260731_P2_ETABS_PLUGIN_V03.md)（`partial`；
  ETABS 菜单加载和 ETABS 23 待人工/对应环境验收）；v0.2.0 正式封装见
  [VAL-20260731-P2-ETABS-PLUGIN-V02](state/validation/VAL-20260731_P2_ETABS_PLUGIN_V02.md)，v0.1.x 入口
  类型空值修复历史见 [VAL-20260730-P2-ETABS-PLUGIN-V01](state/validation/VAL-20260730_P2_ETABS_PLUGIN_V01.md)。
- 文档重构审查：[REV-20260725-DOCUMENT-GOVERNANCE](state/reviews/REV-20260725_DOCUMENTATION_GOVERNANCE.md)。

当前 v2 dirty worktree 的能力、实模边界和历史证据隔离以唯一 [Current Baseline](state/CURRENT_BASELINE.md) 为准；本索引不重复维护跨版本测试计数。

## 迁移与备份

- [迁移指导和源文件处置表](governance/DOCUMENT_MIGRATION_GUIDE.md)
- [重构验证记录](state/validation/VAL-20260725_DOCUMENT_RESTRUCTURE.md)
- [原文档 ZIP 备份](../backups/documentation/2026-07-25-pre-governance.zip)
- [备份 SHA-256 清单](../backups/documentation/2026-07-25-pre-governance-manifest.json)

旧文档路径保留短迁移页一个过渡周期。迁移页不是当前权威来源。
