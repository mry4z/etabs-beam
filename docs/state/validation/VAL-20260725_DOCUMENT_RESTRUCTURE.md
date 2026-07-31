---
id: VAL-20260725-DOCUMENT-RESTRUCTURE
type: validation
status: passed
owner: project
created: 2026-07-25
updated: 2026-07-25
run_id: docs-restructure-20260725
git_commit: 7d3862752041fe18eb0201b21671f3575d70b52b
dirty: true
related:
  - GOV-001
  - IP-DOCS-001
  - REF-DOCS-INDEX
  - REV-20260725-DOCUMENT-GOVERNANCE
  - VAL-20260725-OFFLINE-TESTS
supersedes: []
superseded_by:
---

# VAL-20260725：开发文档重构记录

## 1. 目的与范围

本记录验证 2026-07-25 文档治理重构：迁移前备份 17 份受控开发 Markdown，建立分类权威链，提取 successor，并把原路径改为导航入口或非权威迁移页。

本次不修改业务代码、测试代码、ETABS 模型或既有生成成果。工作树在重构开始前已经包含用户未提交的代码、测试、SB 配筋评审和一个未跟踪生产 JavaScript 文件；其身份见 [Current Baseline](../CURRENT_BASELINE.md) 与 [离线测试记录](VAL-20260725_OFFLINE_TESTS.md)。

## 2. 备份验证

| 检查项 | 结果 |
|---|---|
| 源文档数 | 17 |
| ZIP 条目数 | 17 |
| ZIP SHA-256 | `54b8f2918673aa015601a3e670caeb7ad23c136f72cd36b5003c1c263f8dc60b` |
| 原始相对路径 | 全部保留在 ZIP |
| 逐文件身份 | manifest 保存 bytes、mtime 和 SHA-256 |

- [迁移前 ZIP](../../../backups/documentation/2026-07-25-pre-governance.zip)
- [备份 manifest](../../../backups/documentation/2026-07-25-pre-governance-manifest.json)

备份包含迁移开始时用户尚未提交的 SB 全跨统一箍筋文稿；对应决策已提升到 [SPEC-DET-001](../../specs/SPEC-DET-001_ACI318_14_DETAILING.md)。

## 3. 重构结果

建立的权威链为：

```text
Governance
  -> Spec / Milestone Spec
  -> Architecture + ADR
  -> Implementation Plan -> Task Register
  -> Code / Data -> Validation Record -> Current Baseline
```

具体结果：

1. `README.md` 只保留项目身份、最短快速开始、文档入口和安全边界。
2. 其余 16 个源路径均保留为 `archive/superseded` 迁移页，明确非权威并链接 successor 和备份。
3. 当前目标、架构、决策、执行计划、任务、当前事实、验证、指南和参考分别进入受治理目录。
4. 不同模型、算法、commit 或 dirty state 的运行数字不再合并为“最新状态”。
5. 旧评审中的 SB 全跨统一箍筋决策已进入 Detailing Spec、P1 Plan 和 Task，不因原路径瘦身丢失。

完整源到目标映射见[迁移指导与处置表](../../governance/DOCUMENT_MIGRATION_GUIDE.md)。

## 4. 校验执行

| 校验 | 状态 | 结果 / 证据 |
|---|---|---|
| ZIP 哈希及条目 | `PASSED` | ZIP 哈希一致；17/17 条目逐文件 SHA-256 与 manifest 一致 |
| 17 个源路径处置 | `PASSED` | 1 个 current README；16 个 superseded 迁移页 |
| 文档普通审计 | `PASSED` | 54 份 Markdown；0 个断链、0 个错误；5 个 warning 仅来自排除范围 |
| 文档严格审计 | `PASSED` | 49/49 含 frontmatter；0 个断链、0 个错误、0 个 warning |
| 旧权威措辞及相对链接抽查 | `PASSED` | 旧名称仅用于迁移映射、历史边界或原变更身份；16 个迁移页均明确非权威 |
| 默认离线测试 | `PASSED` | `162 passed, 1 skipped`；coverage `78.89%`，门槛 60% |

原始报告：

- [普通文档审计](../../../build/output/validation/20260725-doc-restructure/docs-audit.md)
- [严格文档审计](../../../build/output/validation/20260725-doc-restructure/docs-audit-strict.md)
- [pytest JUnit](../../../build/output/validation/20260725-doc-restructure/pytest.xml)及[coverage XML](../../../build/output/validation/20260725-doc-restructure/coverage.xml)

默认测试在迁移结束后又以 `python -m pytest -q` 复跑并得到相同结果。审计器覆盖 Markdown 文件目标链接，不校验 heading anchor；本次另行确认活动文档没有 heading-anchor 链接，并人工抽查了权威入口和迁移导航。

## 5. 限制

- 本记录验证文档重构，不重新验证 ETABS OAPI、模型写回、分析设计或工作簿工程结果。
- 新文档对历史证据采取保守解释；未由精确 manifest 支持的旧数字不提升为当前事实。
- 迁移页计划保留一个过渡周期，移除前必须确认没有必要的入站链接。
- ACI 318-14 配筋 Spec 仍为 `review`，需要授权正文、勘误和结构工程师签署。

## 6. Verdict

`PASSED`。迁移前文稿可按哈希恢复；17 个源路径均有明确处置；当前受治理文档的 ID、类型、状态和本地链接检查通过；默认代码测试未因文档重构回归。该 verdict 不扩大到 ETABS 实模或配筋工程验收。
