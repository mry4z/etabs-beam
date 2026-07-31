---
id: ADR-0004
type: decision
status: accepted
owner: project
created: 2026-07-17
updated: 2026-07-25
decision_date: 2026-07-17
related:
  - SPEC-DET-001
  - SPEC-DESIGN-001
supersedes: []
superseded_by:
---

# ADR-0004：规范策略、项目策略与 ETABS 适配分离

## Context

配筋规则同时包含 ACI 条文、项目特定排布选择和 ETABS 字段语义。把三者混在一个算法中会使规范升级、项目参数调整和 API 版本变化互相影响。

## Decision

采用三个边界：

- ETABS adapter 证明并标准化原始字段语义。
- `CodeDetailingPolicy` 负责规范限值和适用条件。
- `ProjectDetailingPolicy` 负责启用钢筋目录、候选间距、两层转 face、PB/SB 连续性和 tie-breaking。

`BeamDetailingProfile` 显式区分 `SB_ORDINARY_NO_CH18`、`PB_OMF`、`PB_SMF` 和 `PB_REVIEW_REQUIRED`。

## Options Considered

- 在引擎中散落常数和条件：短期简单，但无法审计或换版。
- 只用 `is_smf` 布尔值：不能表达 SB 固定普通、PB OMF 和未知类型。
- adapter + code policy + project policy + profile：选用。

## Rejected Tradeoffs

接受更多类型和配置验证代码，放弃用单一布尔分支快速覆盖全部梁的做法。

## Consequences

- 规范版本、project policy 和原始字段来源进入每个正式结果。
- 未实现规范/profile 和未知 Frame Type fail-closed。
- Excel/工作簿只消费统一结果对象，不承担规则决策。
