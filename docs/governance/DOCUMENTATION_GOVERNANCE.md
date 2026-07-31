---
id: GOV-001
type: governance
status: accepted
owner: project
created: 2026-07-25
updated: 2026-07-25
related:
  - IP-DOCS-001
  - REF-DOCS-INDEX
supersedes:
  - LEGACY-PROCESS-CONTROL
superseded_by:
---

# 开发文档治理纲领

## 1. 目的

本纲领确保“应该怎样、现在怎样、为什么这样选、下一步怎样做、哪次验证证明了什么”不会在同一份文档中相互覆盖。文档与代码共同采用 docs-as-code 管理，但不同问题由不同权威来源回答。

## 2. 治理原则

1. 一个事实只有一个权威归属，其他文档使用链接，不复制易变数字。
2. 目标状态和实际状态分离。代码偏离 Spec 时记录为符合性缺口，不自动反写成新要求。
3. 证据只证明其 commit、dirty state、模型、配置、环境和 run；文件时间较新不代表更有效。
4. Review 只提供建议。建议进入 Spec、ADR、Plan 或 Task 后才取得相应权威。
5. 已接受 ADR 不重写历史；变更通过后继 ADR 和 `supersedes` 表达。
6. 迁移先建立 successor，再更新链接，最后把旧路径改为迁移页或归档。
7. 原始运行产物保留在生成目录；受 Git 跟踪的 Validation Record 只保存身份、结论、限制和哈希链接。

## 3. 文档类型与边界

| 类型 | 回答的问题 | 禁止承载的内容 |
|---|---|---|
| Governance | 文档权威、生命周期和证据怎样管理 | 产品需求、当前运行统计 |
| Spec | 系统必须做什么、为什么、如何验收 | 每日进度、一次运行数字 |
| Milestone Spec | 当前阶段必须达到什么退出条件 | 函数级实现步骤 |
| Architecture | 已接受系统如何组织 | 任务勾选、运行时间线 |
| ADR | 为什么选择此方案、放弃了什么 | 大段需求和执行日志 |
| Roadmap | 阶段结果、依赖和顺序是什么 | 代码级修改方案 |
| Implementation Plan | 一次有边界变更如何实施 | 未裁决的长期方案选择 |
| Task | 下一个可独立验收动作是什么 | 重复的全局背景和长设计论证 |
| Current Baseline | 当前版本实际验证到哪里 | 新要求、无证据结论 |
| Validation | 一次精确运行证明了什么 | 新政策、未来计划 |
| Review | 某时点审查发现了什么 | 规范性权威 |
| Guide / Runbook | 如何执行一个当前工作流 | 路线图、状态数据库 |
| Reference | 稳定接口、schema 和术语是什么 | 当前进度和 TODO |
| Archive | 哪些历史材料仍需追溯 | 未加警告的当前指令 |

`current spec` 不是合法类型。现有事实进入 Current Baseline，阶段目标进入 Milestone Spec。通用 `plan` 也不是权威类型：长期结构进入 Architecture，一次变更进入 Implementation Plan。

## 4. 权威与冲突裁决

| 问题 | 唯一权威来源 |
|---|---|
| 文档治理规则 | Accepted Governance |
| 预期行为和验收标准 | Accepted Spec |
| 当前阶段目标 | Active/Accepted Milestone Spec |
| 长期结构 | Accepted Architecture |
| 选择依据 | Accepted ADR |
| 当前已验证行为 | Evidence 汇总后的 Current Baseline |
| 变更执行 | Approved/In-progress Implementation Plan |
| 下一动作 | Task Register |
| 操作和查阅 | Current Guide、Runbook 或 Reference |

冲突按问题类型裁决，不按修改日期裁决。不同模型或不同算法 run 的数量必须分别保留。若证据与 Spec 不一致，Baseline 记录缺口，并创建 Task、Plan、ADR 或 Spec 修订。

## 5. 生命周期

| 类型 | 合法状态 |
|---|---|
| Governance | `draft`, `review`, `accepted`, `superseded` |
| Spec / Architecture | `draft`, `review`, `accepted`, `deprecated`, `superseded` |
| ADR | `proposed`, `accepted`, `rejected`, `superseded` |
| Roadmap | `proposed`, `active`, `completed`, `superseded` |
| Implementation Plan | `draft`, `approved`, `in_progress`, `verified`, `cancelled`, `superseded` |
| Task | `backlog`, `ready`, `in_progress`, `blocked`, `done`, `cancelled` |
| Current Baseline | `current`, `stale`, `superseded` |
| Validation | `passed`, `partial`, `failed`, `invalidated`, `superseded` |
| Review | `draft`, `final` |
| Guide / Reference / Runbook | `current`, `deprecated` |
| Archive | `archived`, `superseded` |

仓库只能有一个 `current` Current Baseline。

## 6. 元数据与标识

所有受治理 Markdown 至少包含：

```yaml
---
id: ADR-0001
type: decision
status: accepted
owner: project
created: YYYY-MM-DD
updated: YYYY-MM-DD
related: []
supersedes: []
superseded_by:
---
```

推荐前缀：`SPEC-*`、`MS-*`、`ARCH-*`、`ADR-*`、`IP-*`、`TASK-*`、`BASELINE-*`、`VAL-*`、`REV-*`、`REF-*`。文件名使用稳定 ID 和主题，不把 `done`、负责人或可变版本写入文件名。

## 7. 证据政策

- 单元测试证明指定工作树的局部代码行为。
- 集成测试证明指定环境的集成路径。
- ETABS 实模 run 只证明精确模型、宿主、API、配置、算法和工作树。
- test cache、收集到的 node ID 或旧 coverage 文件不能证明当前测试通过。
- `dirty: true` 的 manifest 必须保存 status/content fingerprint；它不自动证明后续 commit。
- `allow_partial_writeback`、排除对象或未确认审查项必须在 verdict 中保持可见。
- 不同 run 不得无说明组合成一次验收。

正式实模证据应记录 commit、dirty state、模型哈希、API/DLL、配置、算法/schema 版本、run ID、返回码、输出哈希、排除项和限制。

## 8. 迁移、链接和归档

每个源文档必须有一个主处置：`KEEP`、`SPLIT`、`MERGE`、`SUPERSEDE`、`ARCHIVE` 或 `EXCLUDE`。旧路径有入站链接时保留迁移页一个过渡周期。完整原文通过带哈希清单的备份保存，不在活跃文档目录复制第二份权威内容。

下列范围默认不纳入受治理开发文档：`.codex/`、`.agents/`、`.claude/`、`.pytest_cache/`、`build/`、`outputs/`、视频帧、原始媒体和研究抽取材料。它们可作为证据或上下文，但必须由 Validation Record 引用后才可支持结论。

## 9. 更新触发器

- 要求或验收标准变化：更新 Spec；长期取舍另建 ADR。
- 组件、接口或数据流变化：更新 Architecture 并链接 ADR。
- 一次跨模块变更开始：批准 Implementation Plan 并建立 Tasks。
- 变更完成：先产生 Evidence，再更新 Current Baseline。
- 模型、算法、配置或输入改变：旧 Baseline/Validation 标记 stale、superseded 或 invalidated。
- 命令或操作方式改变：行为验证后更新 Guide/Runbook。
- Review 完成：冻结内容，并链接被提升的正式文档。

## 10. 文档变更完成定义

1. 所有源文档有处置和 successor。
2. 活跃要求、决策、任务和基线各自只有一个权威来源。
3. 当前能力声明可追溯到同一版本的证据；不能证明的内容标为 `UNVERIFIED`。
4. 所有活动 Task 关联 Milestone 或 Implementation Plan。
5. 本地文件链接、ID、类型和状态检查通过。
6. README 和 Guide 不再保存易变状态数字。
7. 旧路径有迁移页或归档警告，原始文稿有可恢复备份。
