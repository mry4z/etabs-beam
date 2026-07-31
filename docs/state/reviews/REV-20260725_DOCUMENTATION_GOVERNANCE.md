---
id: REV-20260725-DOCUMENT-GOVERNANCE
type: review
status: final
owner: project
created: 2026-07-25
updated: 2026-07-25
authority: advisory
related:
  - GOV-001
  - IP-DOCS-001
  - BASELINE-001
supersedes: []
superseded_by:
---

# REV-20260725：开发文档治理审查

## 1. 范围和证据边界

审查覆盖迁移前 17 份受控开发 Markdown、Git 状态、测试配置、CLI 接口、历史 run manifests、P0 最终成果目录和当前默认测试。缓存、工具提示、生成目录和原始研究材料只用于证据核对，不作为文档权威。

## 2. 已确认事实

- 迁移前没有受治理的 frontmatter 或唯一 Current Baseline。
- 同一文档经常同时承担 Spec、Architecture、Task、Baseline 和 Validation。
- 当前 HEAD 为 `7d386275...` 且工作树包含用户未提交代码、测试、文档及一个生产 JavaScript 文件。
- 当前默认测试为 162 passed、1 skipped、78.89%。
- 最近可追溯实模 run 为 `20260722T083439Z-862d6985`，但对应 `4bf55e2...` dirty 工作树，并包含部分写回和未确认例外。

## 3. 主要发现

### F-01：应然与实然混合

`PROJECT_COMPLETION_CHECKLIST.md`、README 和路线图同时描述阶段目标、完成状态和历史 run，导致“P0 已关闭”与“写回事务仍未完成”等陈述共存。

### F-02：不同 run 数字被当作同一“最新状态”

3,009/3,010/3,020/3,070/3,074 Span，24/29/80 阻塞 Object，以及 36/38 多截面 Span 来自不同模型或算法，不能按日期拼接。

### F-03：测试叙述缺少版本绑定

旧文稿存在 84 至 160 passed 等多个数字，没有完整 commit、dirty state 和原始报告。已重新生成当前工作树测试证据。

### F-04：Review 承担了规范权威

两份 ACI review 同时包含条文、项目决策、代码审计、算例和状态。确认的规则应进入 Detailing Spec，方案取舍进入 ADR，未完成项进入 Plan/Task，Review 保持时间快照。

### F-05：Reference 和 Guide 成为状态数据库

API、用户指南和开发指南复制了成熟度、数量和 TODO。迁移后只保留操作与稳定接口，并链接 Baseline/Task。

## 4. 当前阶段判断

P0 可视为“存在可追溯历史 run 的受控 fail-closed 基线”，不能解释为全对象无阻塞的生产验收。当前阶段目标应为 P1 配筋工程闭环，并首先完成当前工作树实模重放。

## 5. 推荐顺序

1. 立 Governance、Index、Baseline 和证据边界。
2. 提取 Specs、Architecture 和 ADRs。
3. 建立 P1 Milestone、Implementation Plan 和 Tasks。
4. 重写 Guide/Reference，旧路径改迁移页。
5. 严格审计并固化重构记录。

## 6. 已提升的决策和要求

- 文档权威规则进入 [GOV-001](../../governance/DOCUMENTATION_GOVERNANCE.md)。
- 当前事实进入 [BASELINE-001](../CURRENT_BASELINE.md)。
- 梁线、设计结果、编号和配筋规则进入 [Specs](../../specs/)。
- GUID、写回、SQLite 和配筋策略选择进入 [ADRs](../../decisions/)。
- 执行项进入 [P1 Plan](../../delivery/plans/active/IP-P1-001_REBAR_ENGINEERING_CLOSURE.md)和[Task Register](../../delivery/tasks/TASK_REGISTER.md)。

## 7. 已知未知项

- 当前 dirty 代码尚未完成 ETABS 实模重放。
- ACI 条文和项目规则尚缺正式工程师签署记录。
- 29 个跨 Span Object、38 个多截面 Span及需求排除对象的最终处置未关闭。
- 稳定编号、CAD schema、人工 UI 和 SQLite 仍在后续路线中。
