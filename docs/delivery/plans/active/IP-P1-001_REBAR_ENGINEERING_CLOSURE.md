---
id: IP-P1-001
type: implementation-plan
status: in_progress
owner: project
created: 2026-07-25
updated: 2026-07-25
related:
  - MS-P1-001
  - SPEC-DET-001
  - SPEC-DESIGN-001
  - ADR-0004
  - BASELINE-001
  - ROADMAP-001
supersedes: []
superseded_by:
---

# IP-P1-001：配筋工程闭环实施计划

## 1. 目标与非目标

本计划把 [MS-P1-001](../../../specs/milestones/MS-P1-001_REBAR_ENGINEERING_CLOSURE.md)
拆成可实现、可回退、可由同一次实模 run 验证的代码、schema、测试和工程审查工作。

本计划不修改梁线/分跨规范，不放宽工作副本和 fail-closed 安全边界，不实现稳定编号、
CAD 插件、SQLite、GUI 或其他设计规范，也不以删除异常行作为销项方式。

## 2. 当前基线

实施起点以 [BASELINE-001](../../../state/CURRENT_BASELINE.md) 为唯一权威。当前离线测试和
历史实模 run 证明不同版本与不同范围；实施过程中不得把二者组合成“当前工作树已实模
验收”的结论。

开始任何代码修改前应记录 HEAD、dirty diff、未跟踪生产文件和 payload/schema 版本。
已有用户修改必须原样保留并纳入新的证据身份，不得通过清理工作树建立假干净基线。

## 3. 已批准决策与规范

| 权威 | 对本计划的约束 |
|---|---|
| [SPEC-DET-001](../../../specs/SPEC-DET-001_ACI318_14_DETAILING.md) | `DET-R001` 至 `DET-R018`、PB/SB 规则、失败状态和验收范围。 |
| [SPEC-DESIGN-001](../../../specs/SPEC-DESIGN-001_RESULT_SEMANTICS.md) | ETABS 设计字段、状态、单位和来源语义。 |
| [SPEC-002](../../../specs/SPEC-002_ENGINEERING_INVARIANTS.md) | 工作副本、错误传播、证据和 fail-closed 不变量。 |
| [ADR-0004](../../../decisions/ADR-0004_DETAILING_POLICY_SEPARATION.md) | ETABS adapter、Code Policy、Project Policy 和 profile 分层。 |

执行中若发现 ACI 条文解释、profile 边界或持久 schema 需要新选择，应先修订 Spec 或建立
ADR；不得把未裁决选择隐藏在实现步骤中。

## 4. 代码与所有权变更

| 区域 | 预期改动 | 所有权边界 |
|---|---|---|
| `src/design/etabs_result.py` 及设计结果模型 | 区分错误、空数组、部分结果和 Provided 空值；保存 station、组合、单位及来源 Object | 只负责 OAPI 适配和语义标准化，不选择配筋 |
| `src/design/detailing.py` | 拆分 cover 语义；完成剪扭间距、最终 `db`、profile、PB/SB 协调和失败状态 | 规范限制与项目 tie-breaking 继续分层 |
| `src/rebar_workbook.py` | 版本化 payload、阻塞项传播、离线 redetail 和逐区控制原因 | 不从 Excel 文本反推业务规则 |
| `src/spreadsheet/*.mjs` | 展示统一结果、公式复核和 QA；审查当前未跟踪 builder 的受控归属 | 生成器只能消费 payload，不重算结构/配筋决策 |
| `src/cli.py`、`src/run_manifest.py` | 串联当前输入、分析设计、配筋、异常、成果哈希和 run verdict | 不弱化模型路径、工作副本或 partial 标记 |
| `tests/unit/`、`tests/integration/` | 建立 requirement-to-test 映射、边界 fixture 和 opt-in OAPI 场景 | 单元测试与实模证据保持不同 verdict |

## 5. 接口与 schema 变更

1. 输入必须分别表达 `etabs_longitudinal_cover`、
   `clear_cover_to_stirrup_outer` 和 `max_aggregate_size`，并记录各自来源。当前项目以 ETABS
   `CoverTop/CoverBot` 纵筋重心 cover 为计算口径；最大骨料缺省解析为 25 mm 和项目默认来源，
   显式配置或后续插件选择可以覆盖，但必须同时写入来源。显式净保护层作为其他项目的兼容口径。
   当前所选保护层口径缺少必需字段时返回 `COVER_SEMANTICS_UNRESOLVED`。
2. 每个左/中/右位置保留原始需求、控制组合、控制 limit、控制原因和失败状态；不得只保存
   最终配筋文本。
3. Detailing result 必须包含 profile、profile 来源、最终纵筋直径、箍筋直径/间距/legs、
   迭代状态和适用的 fail-closed code。
4. SB 三个位置输出同一全跨箍筋规格，同时保存每个位置各自的需求与通过情况。
5. 任何破坏兼容性的 payload 变化必须提升 schema 版本。旧 payload 只能通过显式兼容读取
   或 redetail 迁移，禁止用缺省值静默补齐新语义。

## 6. 数据迁移与兼容

- 历史 payload、工作簿和 manifest 保持原样，只作为其精确版本的证据。
- 新 schema reader 必须明确接受、迁移或拒绝每个旧版本；拒绝时给出机器可读状态。
- 混合截面、未知/混合 Frame Type、无来源 cover 和空设计数组不得在迁移中变成零值。
- 当前未跟踪生产 builder 必须在任务关闭前纳入受控源码，或由受跟踪实现替代并明确排除。
- 任何算法/schema 变化必须使旧成果按 manifest 规则失效，不覆盖旧目录。

## 7. 有序实施

| 顺序 | 动作 | Task | 完成条件 |
|---:|---|---|---|
| 1 | 冻结 cover、骨料和截面输入语义 | `TASK-P1-001` | schema、错误状态和边界测试完成 |
| 2 | 标准化 ETABS 设计结果状态和来源 | `TASK-P1-002` | error/empty/partial/provided 语义可区分且可追溯 |
| 3 | 完成普通、高剪和抗扭箍筋控制 | `TASK-P1-003` | 每个位置输出控制 limit/reason 并通过边界测试 |
| 4 | 完成 PB OMF/SMF 校核和最终 `db` 闭环 | `TASK-P1-004` | profile 不静默推断，SMF 使用最终实配直径 |
| 5 | 完成 PB option 2 和 Beam Line 相邻跨协调 | `TASK-P1-005` | 迭代稳定或显式阻塞，差异可审计 |
| 6 | 收口 SB 全跨统一箍筋及工作簿传播 | `TASK-P1-006` | 三个位置同规格且逐区校核，payload/工作簿一致 |
| 7 | 用当前工作树执行同次实模全链路 | `TASK-P1-007` | 新 manifest、成果、异常和 Validation Record 完整 |
| 8 | 完成 requirement 映射、工程审查和里程碑收口 | `TASK-P1-008` | P1-G01 至 P1-G08 均有关闭证据 |

`TASK-P1-006` 已有进行中的用户实现，可以继续开发；其最终验收仍依赖步骤 1 和步骤 3 的
输入/控制语义，不能只凭 Excel 三个单元格文本相同关闭。

## 8. 测试矩阵

| 范围 | 最低覆盖 | 主要 Task | 证据形式 |
|---|---|---|---|
| 输入/schema | 三类 cover、骨料、混合截面/类型、缺字段、旧 schema | P1-001、P1-002 | 单元测试、schema fixture、错误快照 |
| 箍筋校核 | 普通低剪、高剪、抗扭叠加、最小量、候选间距、无合法候选 | P1-003 | 参数化单元测试及控制原因断言 |
| profile/SMF | SB 固定普通、PB OMF/SMF/Review、最终 `db`、容量剪力来源 | P1-004 | profile/条文映射测试和工程审查 |
| 相邻跨 | PB option 1/2、混合直径/分层、收敛/不收敛、SB 公共支座 | P1-005 | Beam Line fixture 和差异报告 |
| SB 全跨箍筋 | 左/中/右不同控制需求、同规格输出、逐区通过、payload/Excel 一致 | P1-006 | 单元测试、工作簿公式/内容检查 |
| 编排与 manifest | 错误传播、partial 标记、schema/算法身份、成果哈希 | P1-007 | CLI/manifest 测试及实模 Validation |
| 规范闭环 | 每个 `DET-R*` 和 `P1-G*` 的 test/sign-off 映射 | P1-008 | 冻结矩阵和 final Review |

默认离线测试通过只证明被标识工作树的纯代码路径。ETABS OAPI、模型写回、分析设计和
工程工作簿必须由步骤 7 的精确 run 另行证明。

## 9. 实模验证

1. 固定当前 commit/dirty content fingerprint、配置、算法/schema、ETABS/API 和源 EDB 哈希。
2. 从未修改源 EDB 创建不可覆盖的工作副本；默认先执行只读门禁。
3. 在同一次 run 内完成当前范围所需的分析、设计、设计结果读取、配筋、基础/三表工作簿、
   异常清单和 manifest。
4. 保留所有 `BLOCKED`、`REVIEW_REQUIRED`、排除项和 partial 选项；不得删除失败行改善 verdict。
5. 对 PB/SB、普通/高剪/抗扭、SMF、option 2、混合类型和 SB 全跨箍筋抽取可复核样本。
6. 新建 Validation Record，记录输出路径与哈希、限制和 verdict；不得引用历史 run 代替当前
   工作树。
7. 只有 Validation 与工程 Review 满足 Milestone exit gates 后，才能更新 Current Baseline。

## 10. 发布与回退

- 所有变更先通过离线门禁，再在新工作副本执行 opt-in 验证；禁止覆盖历史模型和成果目录。
- schema 变更在 manifest/payload 中显式版本化；消费者不支持时 fail-closed。
- 任一步失败时保留失败 manifest、异常清单和已生成证据，并停止后续交付标记。
- 代码回退使用受控版本变更；回退后生成的新成果使用新 run ID，不能复用失败或已失效成果。
- 实模发现规范/需求问题时回到 Spec/ADR/本计划修订，不在工作簿生成器中打补丁绕过。

## 11. 风险、假设与阻塞

- ACI 条文和勘误需要项目持有的授权文本及结构工程师签署；缺少签署时 P1-G08 不得关闭。
- ETABS 实模验证依赖可用宿主、匹配 DLL、相同权限级别和目标模型访问。
- dirty 工作树和未跟踪生产文件必须由 fingerprint 标识；任何生产代码变化都会使先前离线
  Validation 失效。
- 当前模型的受控拓扑例外可继续 fail-closed，但必须在 P1 run 和 Review 中保持可见。
- ETABS cover 或 Frame Type 来源无法确认时，任务状态保持 `blocked` 或输出 `REVIEW_REQUIRED`。
  最大骨料未显式设置时使用已批准的 25 mm 项目默认值；显式覆盖缺少来源则配置加载失败。
  Frame Type、截面、间距和工程签署异常继续阻断。

## 12. 应产生的证据

- 一份绑定最终代码身份的离线测试 Validation Record。
- 一份当前工作树的 ETABS 实模全链路 Validation Record及原始 manifest/成果哈希。
- `DET-R001` 至 `DET-R018`、P1-G01 至 P1-G08 的 requirement-to-test/sign-off 矩阵。
- 一份冻结的结构工程 Review，记录适用条文、项目规则、例外和签署结论。
- 更新后的 Current Baseline；仅引用上述同版本证据。

当前离线与实模配筋证据见
[VAL-20260730-P1-ETABS-COVER-25MM-CURRENT](../../../state/validation/VAL-20260730_P1_ETABS_COVER_25MM_CURRENT.md)；
写回、分析、设计和全属性审计证据仍见
[VAL-20260730-P1-V645-CURRENT-FULL-CHAIN](../../../state/validation/VAL-20260730_P1_V645_CURRENT_FULL_CHAIN.md)。
工程 Review 仍为 `draft`，不得预先关闭 P1-G08。

## 13. 相关任务

所有动作、依赖、状态和验收入口见
[TASK-REGISTER-001](../../tasks/TASK_REGISTER.md)。

## 14. 当前执行结果

- `TASK-P1-001` 至 `TASK-P1-007` 的实现、自动门禁和历史实模 writeback 全链路已经完成。
- 当前项目采用 ETABS `CoverTop/CoverBot` 纵筋重心口径和默认 25 mm 骨料；run
  `20260730T103252Z-d96440dc` 为 `SUCCESS`，3,018 个 Span 全部进入当前 payload/工作簿。
- 自动配筋 `OK` 2,610 行（PB 1,208；SB 1,402）；2480 行常规稳定，130 行使用通过全部最终控制的
  `STABLE_CONSERVATIVE_CYCLE_BREAK`。
- 408 行继续阻断：224 profile 边界、104 无允许间距、28 混合截面、22 设计 partial、
  10 固定直径纵筋布置、8 未知 FrameType、8 SB 底筋通长布置、4 混合 FrameType。
- `TASK-P1-008` 保持 `blocked`：需要工程处置上述 408 行、由结构工程师冻结签署 Review；若严格要求
  P1-G06 绑定同一最新代码身份，还需重新执行 writeback / analysis / design / full-audit run。

因此本 Plan 保持 `active`，P1 里程碑不得宣称完成。

## 15. 完成定义

1. `TASK-P1-001` 至 `TASK-P1-008` 全部为 `done`，并链接精确证据。
2. [MS-P1-001](../../../specs/milestones/MS-P1-001_REBAR_ENGINEERING_CLOSURE.md)
   的 P1-G01 至 P1-G08 全部关闭。
3. 无未裁决的长期选择隐藏在代码、payload 或工作簿生成器中。
4. 新 schema、兼容边界、失败状态和 rollback 行为有自动测试。
5. 当前工作树实模 Validation 与工程 Review 已冻结，Current Baseline 已更新。
6. 本计划状态改为 `verified`，后续工作进入 Roadmap 的 P1-2 或 P1-3。
