---
id: TASK-REGISTER-001
type: task
status: in_progress
owner: project
created: 2026-07-25
updated: 2026-07-31
related:
  - ROADMAP-001
  - MS-P1-001
  - IP-P1-001
  - IP-P2-002
  - BASELINE-001
supersedes:
  - LEGACY-PROJECT-CHECKLIST
superseded_by:
---

# TASK-REGISTER-001：交付任务登记表

## 1. 使用规则

- `Outcome` 必须是可独立验收的交付结果，不是长期目标或工作日志。
- `done` 必须链接精确 Validation、Review 或其他验收证据；“待生成”表示任务尚未关闭。
- 任务发现新要求或 durable 选择时，先更新 Spec、ADR 或 Implementation Plan，再继续执行。
- 运行数量、测试统计和模型哈希只记录在 Validation/Current Baseline，本表不复制。
- 合法状态为 `backlog`、`ready`、`in_progress`、`blocked`、`done`、`cancelled`。

## 2. 当前里程碑：MS-P1-001

Parent：[MS-P1-001](../../specs/milestones/MS-P1-001_REBAR_ENGINEERING_CLOSURE.md)；
执行方案：[IP-P1-001](../plans/active/IP-P1-001_REBAR_ENGINEERING_CLOSURE.md)。

| ID | Outcome | Depends on | Status | Acceptance | 证据状态 / 关闭链接 |
|---|---|---|---|---|---|
| `TASK-P1-001` | 冻结 cover、骨料和截面输入 schema | `SPEC-DET-001` | `in_progress` | 三类 cover/aggregate 字段分义；最大骨料缺省为有来源的 25 mm，显式/插件覆盖必须有来源；缺失保护层必需值返回 `COVER_SEMANTICS_UNRESOLVED`；无默认 50 mm；新旧 schema 边界有测试 | 默认值实现与单元测试已完成；当前代码身份的全量/实模验证待生成 |
| `TASK-P1-002` | 标准化 ETABS 设计结果状态和来源 | `TASK-P1-001`, `SPEC-DESIGN-001` | `done` | API error、空数组、partial、Provided 空值和有效零值可区分；station、组合、单位、Object 来源可追溯；混合输入 fail-closed | [当前离线门禁](../../state/validation/VAL-20260730_P1_P2_OFFLINE_CURRENT.md)；实模 22 个 partial 行保持可见 |
| `TASK-P1-003` | 完成普通、高剪、抗扭和最小横筋的逐区控制 | `TASK-P1-001`, `TASK-P1-002` | `done` | 每个位置独立计算强度、最小量和最大间距；输出 controlling limit/reason；无合法候选返回 `NO_ALLOWED_SPACING`；边界测试覆盖 | [P1 requirement 映射](../../state/reviews/REV-20260730_P1_REQUIREMENT_MAPPING.md)及当前离线门禁；确认工程输入后的实模成功样本仍属 P1 Gate |
| `TASK-P1-004` | 完成 PB OMF/SMF profile 与最终 `db` 闭环 | `TASK-P1-002`, `TASK-P1-003` | `done` | 未知/混合 PB 不静默推断；SMF `6db` 使用最终实配纵筋直径；容量剪力不重复叠加；条文映射可审查 | [P1 requirement 映射](../../state/reviews/REV-20260730_P1_REQUIREMENT_MAPPING.md)；实模未知/混合类型由当前 Validation 阻断 |
| `TASK-P1-005` | 实现 PB option 2 与 Beam Line 相邻跨协调 | `TASK-P1-003`, `TASK-P1-004` | `done` | Option 2 保持共同直径、位置和排列基线，仅在需求大侧增根；迭代稳定或返回 `ADJACENT_SPAN_COORDINATION_NOT_CONVERGED`；差异可审计 | 当前离线门禁和 payload 2.0 字段验证；确认输入后的实模协调样本仍属 P1 Gate |
| `TASK-P1-006` | 收口 SB 全跨统一箍筋和工作簿传播 | `TASK-P1-001`, `TASK-P1-003`（关闭前） | `done` | 左/中/右输出同一直径、间距和 legs；每区分别满足需求/间距；payload、Excel 和公式一致；生产 builder 有受控归属 | [当前实模验证](../../state/validation/VAL-20260730_P1_ETABS_COVER_25MM_CURRENT.md)：1,402 个 SB 自动行同规格通过，42 个异常行独立保留 |
| `TASK-P1-007` | 用当前工作树完成同一次 ETABS 实模全链路 | `TASK-P1-001` 至 `TASK-P1-006` | `done` | 同一代码身份、模型、配置和 run 生成 manifest、基础/三表工作簿、异常清单及成果哈希；所有 partial/排除项可见；新建 Validation Record | [VAL-20260730-P1-V645-CURRENT-FULL-CHAIN](../../state/validation/VAL-20260730_P1_V645_CURRENT_FULL_CHAIN.md)，run `20260730T072626Z-5101501d` |
| `TASK-P1-008` | 完成 requirement 映射、工程签署和 P1-1 收口 | `TASK-P1-007` | `blocked` | `DET-R001` 至 `DET-R018`、P1-G01 至 P1-G08 均映射到 test/sign-off；冻结 final Review；无未处置 P1 blocker；Plan/Baseline 状态更新 | cover/25 mm 输入已解决；当前仍有 408 个工程阻断 Span、当前代码 writeback 全链和结构工程师冻结签署待完成 |

## 3. 后续阶段与并行技术轨道

除 `TASK-P2-SQLITE-DOMAIN-001` / `TASK-P2-SQLITE-001` 已进入 [IP-P2-002](../plans/active/IP-P2-002_SQLITE_DOMAIN_SOURCE.md)
并行技术轨道外，其余任务尚未进入活动 Implementation Plan。P2 技术工作不得替代 P1-1 工程关闭门禁。

| ID | Outcome | Parent | Depends on | Status | Acceptance | 证据状态 / 关闭链接 |
|---|---|---|---|---|---|---|
| `TASK-NUM-001` | 建立跨通道唯一编号 formatter | [ROADMAP P1-2](../ROADMAP.md) | `TASK-P1-008` | `backlog` | 模型名称、JSON、DXF、基础/三表 Excel 消费同一 formatter；正式跨号不含 `S`；契约测试覆盖 | 当前缺口见 [BASELINE-001](../../state/CURRENT_BASELINE.md)；关闭证据待生成 |
| `TASK-NUM-002` | 建立 GUID/空间指纹持久映射和编号差异 | [ROADMAP P1-2](../ROADMAP.md) | `TASK-NUM-001` | `backlog` | 局部新增/删除产生 added/removed/renumbered 报告；冲突、重复和失效映射 fail-closed；同输入确定 | 关闭证据待对应 Plan/Validation |
| `TASK-NUM-003` | 完成 ETABS 内人工分组、差异预览和第二轮写回 | [ROADMAP P1-2](../ROADMAP.md) | `TASK-NUM-002`, `SPEC-TOPO-002` | `backlog` | 选择集可生成/加载 GUID override；显示旧名到新名差异；确认后仅在工作副本执行；Group 不是权威身份 | 当前核心契约状态见 [BASELINE-001](../../state/CURRENT_BASELINE.md)；UI/实模证据待生成 |
| `TASK-CAD-001` | 冻结版本化 CAD JSON 和严格 I→J 方向契约 | [ROADMAP P1-3](../ROADMAP.md) | `TASK-NUM-001`, `TASK-NUM-002` | `backlog` | schema 含 GUID、源/新名称、楼层、Line/Span、路径、I→J、文字锚点/角度和版本；DXF 为派生物；兼容测试通过 | 当前缺口见 [BASELINE-001](../../state/CURRENT_BASELINE.md)；关闭证据待生成 |
| `TASK-ART-001` | 收敛 `build/output/` 与 `outputs/` 生命周期 | [ROADMAP P1-3](../ROADMAP.md) | `TASK-P1-007` | `backlog` | 生产源码均受跟踪；生成物、交付物和瞬时 QA 位置明确；ignore/manifest/保留策略有自动检查 | 关闭证据待对应 Plan/Validation |
| `TASK-P2-SQLITE-SPIKE-001` | 验证公开 OAPI 原始表到项目 SQLite 的安全快照基座 | [ROADMAP P2-1](../ROADMAP.md) | `ADR-0003` | `done` | 可列出并导出显式表键；metadata 保留模型/API/单位/表版本/字段；使用 `.partial`、完整性检查和原子改名；Reader 对缺表和字段漂移 fail-closed；ETABS 22 工作副本只读验证通过 | [VAL-20260730-P2-SQLITE-SNAPSHOT-SPIKE](../../state/validation/VAL-20260730_P2_SQLITE_SNAPSHOT_SPIKE.md)；本 spike 不关闭完整 P2 数据源任务 |
| `TASK-P2-SQLITE-DOMAIN-001` | 交付 ETABS 22 / ACI 318-14 矩形混凝土梁只读 SQLite profile | [IP-P2-002](../plans/active/IP-P2-002_SQLITE_DOMAIN_SOURCE.md) | `TASK-P2-SQLITE-SPIKE-001` | `done` | 生产 CLI 按 PID/EDB 身份只读导出；梁、支承、全精度设计 station 和 FrameType 可回放；源哈希/文件变化 fail-closed；bundle 非覆盖且验证失败清理；实模领域等价通过 | [OAPI / SQLite 全工作流等价](../../state/validation/VAL-20260730_P2_OAPI_SQLITE_WORKFLOW_PARITY.md) |
| `TASK-P2-SQLITE-001` | 完成公开 OAPI 表到项目 SQLite 的可切换生产数据源 | [ROADMAP P2-1](../ROADMAP.md) | `ADR-0003`, `TASK-P2-SQLITE-DOMAIN-001`, `TASK-CAD-001`（完整关闭前） | `in_progress` | 在已验证矩形 profile 上补齐真实非矩形样本、OAPI/SQLite 领域协议切换、CAD 共享 schema、写回后自动失效、ETABS 23 等价与 bundle 生命周期；缺表/schema 漂移继续 fail-closed；只读不写 EDB | ETABS 22 矩形梁的拓扑、基础 Excel、主/SB 配筋工作流已精确等价；剩余 Gate 见 [IP-P2-002](../plans/active/IP-P2-002_SQLITE_DOMAIN_SOURCE.md) |
| `TASK-P2-PLUGIN-001` | 分别建立 CAD 与 ETABS 插件的交互交付 | [ROADMAP P2-2](../ROADMAP.md) | `TASK-NUM-003`, `TASK-CAD-001` | `in_progress` | 每个插件有独立 Plan、安装/卸载、权限、事务、回滚和集成验收；不共享未经版本化的隐式状态 | ETABS v0.3.0 完成 ETABS API DLL 定位（UI 覆盖 + CLI 自动搜索）与便携带公式配筋 xlsx（openpyxl，无 node），见 [VAL-20260731-P2-ETABS-PLUGIN-V03](../../state/validation/VAL-20260731_P2_ETABS_PLUGIN_V03.md)；v0.2.0 封装见 [V02](../../state/validation/VAL-20260731_P2_ETABS_PLUGIN_V02.md)。v0.3.0 的 ETABS 菜单加载、ETABS 23、CAD 插件及正式依赖仍未关闭 |
| `TASK-P2-DELIVERY-001` | 建立脱敏集成模型、CI/CD 和发布基线 | [ROADMAP P2-2](../ROADMAP.md) | P1 交付契约稳定 | `backlog` | 脱敏模型获准入库；CI 明确无 ETABS/有 ETABS 边界；发布包、兼容矩阵和回退流程可复现 | 关闭证据待独立 Milestone/Plan |

## 4. 状态更新门禁

1. `ready`：Parent 已接受，依赖满足，验收和所需证据明确。
2. `in_progress`：有正在执行的有界改动，代码/数据 owner 已明确。
3. `blocked`：外部输入、决策或环境缺失，并记录解除条件；普通依赖未到只保持 `backlog`。
4. `done`：Acceptance 全部满足，证据列已替换为精确、现存的 Validation/Review 链接。
5. Task 关闭后先更新 Current Baseline，再决定是否推进 Roadmap 阶段状态。
