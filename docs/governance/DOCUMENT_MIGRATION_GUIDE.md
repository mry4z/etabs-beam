---
id: IP-DOCS-001
type: implementation-plan
status: verified
owner: project
created: 2026-07-25
updated: 2026-07-25
related:
  - GOV-001
  - REV-20260725-DOCUMENT-GOVERNANCE
supersedes: []
superseded_by:
---

# 开发文档迁移指导与处置表

## 1. 目标与非目标

目标是把现有 17 份受控开发文档重组为规范、架构、决策、执行、状态证据和使用资料六个清晰层次，并保留原路径兼容性和可恢复备份。

本次不修改业务代码、ETABS 模型和生成成果；不通过文档迁移裁决尚未完成的工程问题；不把历史 run 描述提升为当前工作树证明。

## 2. 备份

迁移前备份已创建：

- [2026-07-25-pre-governance.zip](../../backups/documentation/2026-07-25-pre-governance.zip)
- [2026-07-25-pre-governance-manifest.json](../../backups/documentation/2026-07-25-pre-governance-manifest.json)
- 源文档数：17
- ZIP 条目数：17
- ZIP SHA-256：`54b8f2918673aa015601a3e670caeb7ad23c136f72cd36b5003c1c263f8dc60b`

备份捕获当前工作树版本，包括用户尚未提交的 SB 统一箍筋规则文稿修改。

## 3. 目标信息架构

```text
docs/
  INDEX.md
  governance/
  specs/
    milestones/
  architecture/
  decisions/
  delivery/
    plans/active/
    plans/archive/
    tasks/
  state/
    validation/
    reviews/
  guides/
  reference/
    api/
    schemas/
  archive/
```

## 4. 源文件处置矩阵

| 源文件 | 主处置 | 主要问题 | 权威 successor |
|---|---|---|---|
| `README.md` | `KEEP` | 混入状态、测试数和路线图 | 根 README 瘦身；状态转入 Baseline |
| `README form CODEX start.md` | `ARCHIVE` | 早期路径和宿主范围过时 | `README.md`、本迁移页 |
| `PROJECT_COMPLETION_CHECKLIST.md` | `SPLIT` | Baseline、Milestone、Task、Validation 混合 | `CURRENT_BASELINE.md`、Milestone、Roadmap、Task、Validation |
| `PROJECT_STRUCTURE.md` | `SPLIT` | 目录、架构、状态和计划混合 | `ARCH-001`、`REPOSITORY_LAYOUT.md`、Roadmap |
| `PROCESS_CONTROL_REQUIREMENTS.md` | `ARCHIVE` | 2026-07-10 时点审查事实已过时 | `REV-20260710-PROJECT-READINESS.md`、`GOV-001` |
| `docs/TECHNICAL_ROADMAP.md` | `SPLIT` | 目标、架构、历史进度和任务混合 | Product Spec、Architecture、Roadmap、Milestone |
| `docs/BEAM_LINE_SPAN_RULES.md` | `SPLIT` | Spec、算法设计、状态和待办混合 | `SPEC-TOPO-001`、`ARCH-001`、Task、Validation |
| `docs/MANUAL_BEAM_TOPOLOGY_OVERRIDE.md` | `SPLIT` | Spec、状态机、schema 和插件计划混合 | `SPEC-TOPO-002`、`ARCH-001`、`INTERCHANGE_SCHEMAS.md` |
| `docs/DRAWING_NUMBERING_INTERCHANGE.md` | `SPLIT` | Spec、架构、schema 和状态混合 | `SPEC-DRAW-001`、`ARCH-001`、schema Reference |
| `docs/API.md` | `SPLIT` | Reference 混入成熟度和 TODO | `reference/API.md`、Baseline、Task |
| `docs/api/design_results.md` | `SPLIT` | Reference、Spec、Validation 混合 | `reference/api/design-results.md`、`SPEC-DESIGN-001` |
| `docs/USER_GUIDE.md` | `SPLIT` | Guide 混入运行数字和未来功能 | `guides/USER_GUIDE.md`、Baseline、Roadmap |
| `docs/DEVELOPMENT.md` | `SPLIT` | Guide 混入阶段状态和旧测试数 | `guides/DEVELOPMENT.md`、Architecture、Governance |
| `docs/ETABS_OAPI_VALIDATION.md` | `SPLIT` | 稳定接口与多次历史 run 混合 | `reference/ETABS_OAPI.md`、Validation Records |
| `docs/ETABS_SQLITE_SNAPSHOT_ROUTE.md` | `SPLIT` | 决策、架构和实施计划混合 | `ADR-0003`、Architecture、Roadmap/Task |
| `docs/PRIMARY_BEAM_REBAR_DETAILING_ACI318_14_REVIEW.md` | `SPLIT` | Spec、代码审计、算例和历史状态混合 | `SPEC-DET-001`、`ADR-0004`、Review、Plan/Task |
| `docs/ORDINARY_BEAM_REBAR_DETAILING_ACI318_14_REVIEW.md` | `SPLIT` | Spec、已确认决策、代码审计和测试混合 | `SPEC-DET-001`、`ADR-0004`、Review、Plan/Task |

`.codex/`、缓存、`build/`、`outputs/`、抽帧文稿和原始媒体处置为 `EXCLUDE`，不计入上述 17 份受控源文档。

## 5. 冲突裁决

1. `3,009/3,010/3,020/3,070/3,074 Span` 属于不同模型、算法或 run。Baseline 只把 `3,020` 绑定到 run `20260722T083439Z-862d6985`，其他数字保留为历史或失效证据。
2. `24/29/80 REQUIRES_SPLIT` 同理不得合并；当前历史验收 run 的值是 29。
3. `36/38` 个多截面 Span 以精确 manifest 的 38 为该 run 事实。
4. `84/96/105/122/140/150/160 passed` 均为无完整版本绑定的历史叙述。本次重新运行结果 `162 passed, 1 skipped` 进入独立 Validation Record。
5. 2026-07-22 实模 run 的 `validity.state=CURRENT` 只表示其注册表输入指纹状态；由于其 commit 为 `4bf55e2...` 且 `dirty=true`，不能证明当前 HEAD `7d38627...`。
6. P0 “关闭”解释为受控、fail-closed 工程基线，不解释为所有对象均已写回或全部配筋问题已解决。
7. 用户当前 SB 统一箍筋规则修改优先保留，并提升到 `SPEC-DET-001` 和 P1 验收任务；原文精确版本保存在迁移前备份。

## 6. 迁移顺序

| 顺序 | 动作 | 状态 |
|---:|---|---|
| 1 | 创建备份、清单和证据边界 | 完成 |
| 2 | 建立 Governance、Index 和本迁移表 | 完成 |
| 3 | 建立 Current Baseline、Milestone 和 Validation | 完成 |
| 4 | 提取 Specs、Architecture 和 ADRs | 完成 |
| 5 | 建立 Roadmap、Implementation Plan 和 Task Register | 完成 |
| 6 | 重写 Guide/Reference，旧路径改迁移页 | 完成 |
| 7 | 运行链接、ID、元数据和内容检查 | 完成 |
| 8 | 固化重构 Validation Record，将本计划标记 `verified` | 完成 |

## 7. 兼容策略

所有旧受控路径保留短迁移页一个过渡周期。迁移页链接新权威来源和原文 ZIP，不继续维护正文。根 README 保持原路径，但只承载项目入口和导航。

## 8. 验证

迁移完成后执行：

```powershell
python C:\Users\zci8\.codex\skills\govern-development-docs\scripts\audit_docs.py . --format markdown --check
python C:\Users\zci8\.codex\skills\govern-development-docs\scripts\audit_docs.py . --format markdown --strict --exclude .codex --exclude extracted_frames --exclude video_frames
python -m pytest -q
```

审计器不校验 heading anchors 和所有 CommonMark 扩展；还需搜索旧权威措辞、检查相对链接并人工抽查导航。

执行结果见 [VAL-20260725-DOCUMENT-RESTRUCTURE](../state/validation/VAL-20260725_DOCUMENT_RESTRUCTURE.md)：

- 普通审计：54 份 Markdown，0 个断链、0 个错误；5 个 warning 均来自明确排除的工具提示和抽帧研究材料。
- 严格受治理范围：49 份 Markdown，49 份含 frontmatter，0 个断链、0 个错误、0 个 warning。
- 迁移前 ZIP：17/17 条目逐文件 SHA-256 与 manifest 一致。
- 默认测试：162 passed、1 skipped，coverage 78.89%。

## 9. 完成标准

- 17 份源文档均有一个主处置和 successor。
- 新文档有唯一 ID、合法类型和状态。
- 目标、事实、决策、执行和证据已分离。
- 当前能力矩阵不把不同 run 合成一次验收。
- 旧路径可访问且明确标为非权威。
- 备份 ZIP 和清单哈希可验证。
- 文档审计、严格治理检查和默认测试通过。
