---
id: BASELINE-001
type: current-baseline
status: current
owner: project
created: 2026-07-25
updated: 2026-09-10
as_of: 2026-09-10
git_commit: 59a40b26f99b5dac35fdc7159a01c164e3c3f075
dirty: true
accepted_manifest: "none for current dirty worktree; historical manifests retained below"
related:
  - MS-P1-001
  - VAL-20260730-P1-P2-OFFLINE-CURRENT
  - VAL-20260730-P1-V645-CURRENT-FULL-CHAIN
  - VAL-20260730-P1-ETABS-COVER-25MM-CURRENT
  - VAL-20260730-P2-SQLITE-SNAPSHOT-SPIKE
  - VAL-20260730-P2-OAPI-SQLITE-WORKFLOW-PARITY
  - IP-P2-002
  - VAL-20260730-P2-ETABS-PLUGIN-V01
  - IP-P2-003
  - VAL-20260731-P2-ETABS-PLUGIN-V02
  - VAL-20260731-P2-ETABS-PLUGIN-V03
  - VAL-20260908-DRAWING-EXPORT-REVIEW
  - VAL-20260910-REBAR-REVIEW-SCOPE
supersedes: []
superseded_by:
---

# Current Baseline

## 1. 基线身份

| 项目 | 当前值 |
|---|---|
| 截止时间 | 2026-09-10 |
| Git HEAD | `59a40b26f99b5dac35fdc7159a01c164e3c3f075` |
| 分支 / 工作树 | `codex/clean-start` / `dirty: true` |
| 当前 v2 离线事实 | 梁图导出定向验证见 [VAL-20260908-DRAWING-EXPORT-REVIEW](validation/VAL-20260908_DRAWING_EXPORT_REVIEW.md)；梁表当前复核范围见 [VAL-20260910-REBAR-REVIEW-SCOPE](validation/VAL-20260910_REBAR_REVIEW_SCOPE.md)；分塔选择集、PB/SB 单表 JSON/Excel 分别见对应 Validation，不在本表重复测试计数 |
| 当前 v2 实模/GUI | `UNVERIFIED`：本轮未连接 ETABS、未截断模型、未写回模型，菜单加载和完整出图门禁仍待人工验收 |
| 历史实模与全链证据 | [VAL-20260730-P1-ETABS-COVER-25MM-CURRENT](validation/VAL-20260730_P1_ETABS_COVER_25MM_CURRENT.md)、[VAL-20260730-P1-V645-CURRENT-FULL-CHAIN](validation/VAL-20260730_P1_V645_CURRENT_FULL_CHAIN.md)，仅对各自历史代码身份有效 |
| 历史 P2 技术/插件证据 | [VAL-20260730-P2-SQLITE-SNAPSHOT-SPIKE](validation/VAL-20260730_P2_SQLITE_SNAPSHOT_SPIKE.md)、[VAL-20260730-P2-OAPI-SQLITE-WORKFLOW-PARITY](validation/VAL-20260730_P2_OAPI_SQLITE_WORKFLOW_PARITY.md)、[VAL-20260731-P2-ETABS-PLUGIN-V03](validation/VAL-20260731_P2_ETABS_PLUGIN_V03.md)，不作为当前 v2 验收 |
| 活动阶段 | [MS-P1-001 配筋工程闭环](../specs/milestones/MS-P1-001_REBAR_ENGINEERING_CLOSURE.md) |

当前基线只承认 dirty v2 工作树中已有明确 Validation 的能力；历史 run 的 `SUCCESS`、实模等价和发布事实
不向当前代码身份外推。P1 工程门禁、模型截断/I/J 协调、持久稳定编号、正式 CAD JSON、完整出图门禁和结构工程师签署仍未关闭。

## 2. 当前工作树边界

当前唯一基线对应 v2 `codex/clean-start` 的 dirty 工作树（HEAD `59a40b26f99b5dac35fdc7159a01c164e3c3f075`）；
本节以下较早段落中的 run、哈希和测试计数只对各自记录的历史代码身份有效，不构成当前 v2 验收。
2026-09-10 本轮新增的梁表当前复核范围由 [配筋梁表复核范围记录](validation/VAL-20260910_REBAR_REVIEW_SCOPE.md) 单独记录；
2026-09-08 本轮新增的当前事实由 [梁图导出验证记录](validation/VAL-20260908_DRAWING_EXPORT_REVIEW.md)
单独记录；分塔预检/选择集恢复以[对应验证记录](validation/VAL-20260827_TOWER_SELECTION_RECOVERY.md)为准，
PB/SB 单表静态 Excel 与 JSON 审计以[对应验证记录](validation/VAL-20260827_REBAR_SINGLE_SHEET_STATIC.md)为准。

2026-08-27 项目策略增量：已按《纳西里耶医疗城项目 SMF 主梁+次梁措施-1版 20260824》更新
`nasiriyah-smf-sb-2026-08-24-r1`，包括 SMF 最大纵筋率 `0.01613`、端部 `2h` 区 `s<=100 mm`、
配置表格口径 40 mm 箍筋外缘净保护层和 25 mm 最大骨料；悬挑梁上部纵筋 `1.2` 放大保留人工复核。
本次提交内容从暂存区独立导出后运行 `python -m pytest tests/unit -q`：`259 passed`，coverage
`80.73%`。含其他未提交改动的原工作树同命令为 `344 passed`、coverage `77.61%`；两者验证范围不同，
不得互相替代。尚未以该策略进行 ETABS 22/23 实模重跑或重建发布包。

P1 实模 manifest 对其 dirty 工作树记录：

- tracked diff SHA-256：`2019c1f4f5f03b3452b6039951c6e6ac38c917912bcfe1f53e9e829c3748fdd4`
- tracked diff 与全部未跟踪文件内容 SHA-256：
  `8f4dbca345bdd7505dc3e364a189d4f46ee67067b924110276ab80a35cb09dec`
- 每个未跟踪文件的路径、大小和 SHA-256 均保存在 manifest 中，不再只哈希文件名。

该 identity 覆盖 P1 实现、工作簿 builder、P2 SQLite spike 及当时的受治理文档。其后新增的 P2
生产代码由 [P2 当前领域证据](validation/VAL-20260730_P2_SQLITE_DOMAIN_CURRENT.md)独立冻结：

- tracked diff SHA-256：`dbd211715b25dc213aaeab21a22bfbfd78776b6faa83633e110565f54e769a85`
- tracked diff 与全部未跟踪文件内容 SHA-256：
  `6b7b4fef3fc5ba2aa667698c45f683f4d6d44e12c300f29e56ff2c10d27ea276`

该 P2 content identity 在代码、测试和实模验证完成后、追加本次治理 Markdown 前生成。

当前保护层/骨料与 OAPI 配筋 run 对代码身份重新冻结：

- tracked diff SHA-256：`6167eb029d2272a9cc88e8e9624c667b61361b12ed5c5d7fc2f7aa54ebe1ac5a`
- tracked diff 与全部未跟踪文件内容 SHA-256：
  `2f9990f0b475023a248939b2dfd524f33573dd20fbf5d4ab68125a246a70d8fc`

该 identity 在新 Validation/Baseline Markdown 写入前生成；随后只追加治理证据。

ETABS 插件 v0.1 源码、构建/合同/smoke 工具和最终门禁另行冻结：

- tracked diff SHA-256：`d27ef5ec5a22543e3dd65d7e5f189f1421efa566891c608ffe2630ad844d0f00`
- tracked diff 与全部未跟踪文件内容 SHA-256：
  `9e91cbd52257286fe2702d330323db396bf665138313c602194031812def67f5`

该 identity 在追加插件 Validation 及最终 Architecture/Task/Baseline 治理 Markdown 前生成。

v0.2.0 正式封装构建对修复后的构建脚本、插件源码和便携运行器重新冻结：

- tracked diff SHA-256：`78188f69ca3f250f87dba999353b20cc747948a5502bde940ecf3ba3e7b8169c`
- tracked diff 与全部未跟踪文件内容 SHA-256：
  `7e5351801a7e775cf93151138f63446a891c773dafd1781dcf27a63ec8428716`

该 identity 在 v0.2.0 发布封装构建、构建脚本环境修复（PowerShell 7 guard、manifest 兼容写法）及本次
Validation/Guide/Task/Baseline 治理 Markdown 写入前生成。

v0.3.0 对新增的 DLL 定位、便携配筋渲染器和插件 UI 改动重新冻结：

- tracked diff SHA-256：`bb0f10c140e7a075159cf4956a1eb19142e0d865f78362a60d01d46e9bb18636`
- tracked diff 与全部未跟踪文件内容 SHA-256：
  `477bb677d1a703d3e657b6870b73f1716761db0349d4954b8e0e44b94559158f`

该 identity 在 v0.3.0 重打包与 Validation/Guide/Task/Baseline 治理 Markdown 写入前生成。

## 3. 能力矩阵

状态只使用 `VERIFIED`、`PARTIAL`、`BLOCKED`、`UNVERIFIED`。

| Requirement / 能力 | 状态 | Evidence | 已知缺口 | 下一任务 |
|---|---|---|---|---|
| 默认离线测试与覆盖率门槛 | `PARTIAL` | 历史 [VAL-20260730-P1-P2-OFFLINE-CURRENT](validation/VAL-20260730_P1_P2_OFFLINE_CURRENT.md)；当前 v2 不在本基线重复测试计数 | 当前 dirty 身份需按对应 Validation 重跑；ETABS 集成测试默认跳过 | 后续变更持续门禁 |
| ETABS 22.7 / 23.3 OAPI 连接与字段兼容 | `PARTIAL` | 历史 ETABS 22.7 run、ETABS 23.3 样本和适配测试 | 当前代码未以同一 dirty 身份重跑全链路 | 后续兼容矩阵 |
| Beam Line / Span 拓扑算法 | `PARTIAL` | 历史 run：4,094 Object、1,215 Line、3,018 Span；0 blocking diagnostic | 历史输入/身份不等于当前 v2；split、多截面和工程复核仍保留 | TASK-P1-008 |
| fail-closed 方向与编号写回 | `PARTIAL` | 历史 run 返回码 0、对象全属性和需求等价无 failure | 当前 dirty 身份未重跑 writeback 全链；审查项不得外推为当前成功 | TASK-P1-008 |
| 设计结果 `_2` 字段映射 | `PARTIAL` | 历史 payload 保留 raw Provided、station、组合、单位、来源；单元测试证据 | 当前 dirty 身份的实模 partial 仍需工程处置 | TASK-P1-008 |
| ACI 318-14 配筋排布 | `PARTIAL` | 历史 ETABS 重心 cover +25 mm：2,610/3,018 行 `OK`；2026-08-27 新策略提交快照 `259 passed`、coverage `80.73%` | 40 mm/25 mm 新配置和 1.613%/100 mm 新控制尚未实模重跑；408 个历史异常及工程签署未完成 | TASK-P1-008 |
| SB 全跨统一箍筋规格 | `PARTIAL` | 1,402/1,444 个 SB 行同规格通过；42 行保留异常 | 22 混合截面、12 design partial、8 底筋布置异常 | TASK-P1-008 |
| 三表配筋工作簿（历史能力） | `UNVERIFIED` | 历史证据见 [VAL-20260730-P1-V645-CURRENT-FULL-CHAIN](validation/VAL-20260730_P1_V645_CURRENT_FULL_CHAIN.md)；不作为当前 v2 能力 | 当前 dirty 身份的三表成果和工程签发未重新验收 | TASK-P1-008 |
| 人工拓扑 Override 核心契约 | `PARTIAL` | 单元测试覆盖 JSON、GUID、连通和失效 | ETABS 交互 UI 与第二轮写回未实现 | TASK-NUM-003 |
| 稳定编号和 CAD 正式交换 schema | `PARTIAL` | 当前 formatter、DXF/CSV 空间顺序一致性、文字显示边界和方向测试；[VAL-20260908-DRAWING-EXPORT-REVIEW](validation/VAL-20260908_DRAWING_EXPORT_REVIEW.md) | 持久稳定编号映射、模型截断/I/J 协调、正式 JSON 和完整 CAD 出图门禁未闭环 | TASK-NUM-001、TASK-CAD-001 |
| 分塔预检与完整选择集恢复 | `PARTIAL` | [VAL-20260827-TOWER-SELECTION-RECOVERY](validation/VAL-20260827_TOWER_SELECTION_RECOVERY.md)：公共表快路径、`PreviousSelection` 优先、身份/责任交接和离线回归 | ETABS 22/23 实模菜单/GUI 验收仍未完成 | TASK-P2-PLUGIN-001 |
| PB/SB 单表静态 Excel 与 JSON 审计 | `PARTIAL` | [VAL-20260827-REBAR-SINGLE-SHEET-STATIC](validation/VAL-20260827_REBAR_SINGLE_SHEET_STATIC.md)：静态布局、raw/review/final 审计和离线视觉契约 | 最新 dirty 身份的实模重跑和工程签发未完成 | TASK-P1-008 |
| SQLite 快照数据源 | `PARTIAL` | ETABS 22 矩形梁：4,094 梁、1,441 支承、65,881 station；六个拓扑成果逐字节一致，3,018 个主/SB payload 行逐字段一致 | 非矩形真实样本、CAD schema、ETABS 23 和 bundle 生命周期未完成 | TASK-P2-SQLITE-001 |
| ETABS 交互插件 v0.3.0（历史能力） | `UNVERIFIED` | 原始历史证据见 [VAL-20260731-P2-ETABS-PLUGIN-V03](validation/VAL-20260731_P2_ETABS_PLUGIN_V03.md)；不作为当前 v2 能力 | 历史版本的菜单加载、非空选择、ETABS 23 和安装签名仍未形成当前验收 | TASK-P2-PLUGIN-001 |
| ETABS 交互插件 v2 / 当前源码 | `PARTIAL` | 当前源码职责见 [ARCH-001](../architecture/ARCH-001_SYSTEM_ARCHITECTURE.md) 与 [ETABS 插件指南](../guides/ETABS_PLUGIN.md)；选择集恢复见 [VAL-20260827-TOWER-SELECTION-RECOVERY](validation/VAL-20260827_TOWER_SELECTION_RECOVERY.md)；当前 v0.4.1-r6d 包见 [RELEASE_NOTES](../../plugins/etabs/RELEASE_NOTES.md) | 已完成 ETABS 23 离线编译、运行时自检和插件契约校验；仍未进行 ETABS 22/23 实模菜单/GUI 加载和安装签收 | TASK-P2-PLUGIN-001 |
| SAP2000、CAD 插件、正式 GUI/安装器、CI/CD | `UNVERIFIED` | 无当前实现证据 | P2 范围 | Roadmap P2 |

## 4. 历史实模证据边界（非当前 v2 验收）

历史 writeback run `20260730T072626Z-5101501d` 只对其精确输入和代码身份有效：

- 30 个 `REQUIRES_SPLIT` Object / 60 个动作保持审计可见。
- 28 个多截面 Span 全部未确认。
- `allow_partial_writeback=true`，但没有外部排除 Object。
- 同次需求等价检查 4,094 个 Object，0 failure。
- 该 run 的配筋输入尚未冻结，因此不能作为当前配筋成果；其 writeback、分析、设计和审计证据继续有效。

历史 read-only 配筋 run `20260730T103252Z-d96440dc`：

- 2,610 行 `OK`；408 行保留工程状态。
- 224 行跨 profile 协调边界、104 行无允许间距。
- 28 混合截面、22 design partial、12 未知/混合 Frame Type。
- 10 固定直径纵筋布置、8 SB 底筋通长布置失败。
- EDB SHA-256 在运行前后均为
  `20347ccc0e3960ef10c91b107776b19dc1676648c8b621b79f8925202aff9c41`。

这些例外是 fail-closed 基线的一部分，不等于问题已经处置或 P1 已完成。

## 5. 失效和冲突声明

- 旧 3,070/3,074 Span 工作簿基于已修正拓扑，只能用于历史 API/工作簿验证。
- v6.4.1 的 3,009/3,010 Span、v6.4.3 的 3,020 Span 与当前 v6.4.5 的 3,018 Span 不是同一模型事实。
- 历史文档中的 84 至 208 passed 以及历史 212 项门禁都不能证明当前工作树；测试计数只在具体 Validation Record 的精确身份边界内有效。
- manifest 的 `validity.state=CURRENT` 是注册表输入指纹状态，不是“当前仓库已验收”的同义词。
- P2 SQLite 的 ETABS 22 矩形梁 profile 与拓扑/Excel/配筋只读工作流已交付并验证；完整 P2 数据源
  仍未完成，不能把该 profile 扩大解释为非矩形截面、ETABS 23、CAD schema 或正式 bundle 生命周期已获支持。
- v0.1.0/v0.1.1 正式包缺少 `workflow/` 便携运行时，不能作为完整插件包交付；单独的便携运行器目录
  （EXE + config）也不是插件包。v0.2.0 包才同时包含插件 DLL 与便携计算引擎，且须整体部署。
- v0.3.0 的便携带公式配筋工作簿由 openpyxl 渲染，与 Node artifact-tool 版本可能存在样式/条件格式的
  亚像素差异（公式语义一致）；PNG 预览仅在开发机生成。

## 6. 更新规则

以下任一事件要求新建或更新 Baseline：

1. Git commit 或生产代码 dirty diff 改变。
2. ETABS 模型哈希、宿主/API、配置或算法版本改变。
3. 活动里程碑、正式 run verdict 或受控例外改变。
4. 当前工作树完成新的实模 run，并生成带模型和成果哈希的 Validation Record。
