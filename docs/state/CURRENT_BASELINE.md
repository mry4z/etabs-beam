---
id: BASELINE-001
type: current-baseline
status: current
owner: project
created: 2026-07-25
updated: 2026-07-31
as_of: 2026-07-31T23:55:00+08:00
git_commit: 7d3862752041fe18eb0201b21671f3575d70b52b
dirty: true
accepted_manifest: p1-writeback:20260730T072626Z-5101501d;p1-cover25:20260730T103252Z-d96440dc;p2-offline:20260730T095035Z-d1abb748;p2-plugin-v01:20260730T131855Z-53226a75
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
supersedes: []
superseded_by:
---

# Current Baseline

## 1. 基线身份

| 项目 | 当前值 |
|---|---|
| 截止时间 | 2026-07-31 22:40:00 +08:00 |
| Git HEAD | `7d3862752041fe18eb0201b21671f3575d70b52b` |
| 工作树 | `dirty: true` |
| 当前离线门禁 | [VAL-20260730-P2-ETABS-PLUGIN-V01](validation/VAL-20260730_P2_ETABS_PLUGIN_V01.md)，`212 passed, 1 skipped`，三个 .NET 项目 `0 warnings, 0 errors` |
| 当前实模配筋 | [VAL-20260730-P1-ETABS-COVER-25MM-CURRENT](validation/VAL-20260730_P1_ETABS_COVER_25MM_CURRENT.md)，run `20260730T103252Z-d96440dc`，`partial` |
| writeback 全链证据 | [VAL-20260730-P1-V645-CURRENT-FULL-CHAIN](validation/VAL-20260730_P1_V645_CURRENT_FULL_CHAIN.md)，较早代码身份 |
| P2 技术验证 | [VAL-20260730-P2-SQLITE-SNAPSHOT-SPIKE](validation/VAL-20260730_P2_SQLITE_SNAPSHOT_SPIKE.md)，`passed`（spike 范围） |
| P2 当前领域证据 | [VAL-20260730-P2-OAPI-SQLITE-WORKFLOW-PARITY](validation/VAL-20260730_P2_OAPI_SQLITE_WORKFLOW_PARITY.md)，`passed`（ETABS 22 矩形梁只读工作流） |
| ETABS 插件 v0.3.0 | [VAL-20260731-P2-ETABS-PLUGIN-V03](validation/VAL-20260731_P2_ETABS_PLUGIN_V03.md)，`partial`（ETABS API DLL 可定位 + 便携无 node 生成带公式配筋 xlsx；菜单加载与 ETABS 23 待人工/环境验收） |
| 活动阶段 | [MS-P1-001 配筋工程闭环](../specs/milestones/MS-P1-001_REBAR_ENGINEERING_CLOSURE.md) |

当前代码身份已完成离线门禁、ETABS 22.7 只读配筋链和 ETABS 插件 v0.1 技术垂直切片；run 技术状态为 `SUCCESS`。ETABS 设计保护层与
25 mm 骨料输入已经解决，P1 工程门禁仍为 `PARTIAL`，因为 408 个异常 Span 尚未全部处置、最新代码身份
没有重跑 writeback 全链且缺少结构工程师冻结签署。P2-1 的 ETABS 22 矩形梁只读工作流已通过实时 OAPI /
SQLite 精确等价，但完整 P2 仍为 `in_progress`。

## 2. 当前工作树边界

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
| 默认离线测试与覆盖率门槛 | `VERIFIED` | `212 passed, 1 skipped`, coverage `79.54%`；当前 Plugin Validation | ETABS 集成测试默认跳过，由实模 Validation 补充 | 后续变更持续门禁 |
| ETABS 22.7 / 23.3 OAPI 连接与字段兼容 | `PARTIAL` | 当前 ETABS 22.7 run；历史 ETABS 23.3 样本；适配测试 | 当前代码未在 ETABS 23.3 重跑全链路 | 后续兼容矩阵 |
| Beam Line / Span 拓扑算法 | `VERIFIED` | 当前 run：4,094 Object、1,215 Line、3,018 Span；0 blocking diagnostic | 30 个 split Object、28 个多截面 Span 保持人工审查 | TASK-P1-008 |
| fail-closed 方向与编号写回 | `VERIFIED` | 当前 run 返回码 0；4,094 对象全属性和需求等价均 0 failure | 使用受控 partial writeback；审查项未伪装为成功 | TASK-P1-008 |
| 设计结果 `_2` 字段映射 | `VERIFIED` | 当前 payload 保留 raw Provided、station、组合、单位、来源；单元测试 | 22 个实模 partial 仍需工程处置 | TASK-P1-008 |
| ACI 318-14 配筋排布 | `PARTIAL` | ETABS 重心 cover +25 mm：2,610/3,018 行 `OK`，130 个保守循环解通过最终控制 | 408 个异常处置和工程签署未完成 | TASK-P1-008 |
| SB 全跨统一箍筋规格 | `PARTIAL` | 1,402/1,444 个 SB 行同规格通过；42 行保留异常 | 22 混合截面、12 design partial、8 底筋布置异常 | TASK-P1-008 |
| 三表配筋工作簿 | `PARTIAL` | payload 2.0、主/SB 工作簿；公式错误扫描 0；9 张预览检查通过 | 自动成功行仍需工程签发；异常行未销项 | TASK-P1-008 |
| 人工拓扑 Override 核心契约 | `PARTIAL` | 单元测试覆盖 JSON、GUID、连通和失效 | ETABS 交互 UI 与第二轮写回未实现 | TASK-NUM-003 |
| 稳定编号和 CAD 正式交换 schema | `PARTIAL` | 当前 formatter、DXF/CSV 和方向测试 | 稳定编号映射、正式 JSON 和 CAD 消费未闭环 | TASK-NUM-001、TASK-CAD-001 |
| SQLite 快照数据源 | `PARTIAL` | ETABS 22 矩形梁：4,094 梁、1,441 支承、65,881 station；六个拓扑成果逐字节一致，3,018 个主/SB payload 行逐字段一致 | 非矩形真实样本、CAD schema、ETABS 23 和 bundle 生命周期未完成 | TASK-P2-SQLITE-001 |
| ETABS 交互插件 v0.3.0 | `PARTIAL` | v0.3.0 支持 ETABS API DLL 定位（插件 UI 覆盖 + CLI 自动搜索常见安装目录）；便携 `--export-rebar-workbook` 在无 node 下用 openpyxl 生成带公式三表/SB xlsx（239 passed，coverage 80.52%；便携 run 3023 行，公式扫描 0 错误）；v0.2.0 封装证据仍有效 | v0.3.0 的 ETABS Tools 菜单加载、非空选择、ETABS 23、安装签名待验证 | TASK-P2-PLUGIN-001 |
| SAP2000、CAD 插件、正式 GUI/安装器、CI/CD | `UNVERIFIED` | 无当前实现证据 | P2 范围 | Roadmap P2 |

## 4. 当前实模基线允许的受控例外

writeback run `20260730T072626Z-5101501d` 只对其精确输入有效：

- 30 个 `REQUIRES_SPLIT` Object / 60 个动作保持审计可见。
- 28 个多截面 Span 全部未确认。
- `allow_partial_writeback=true`，但没有外部排除 Object。
- 同次需求等价检查 4,094 个 Object，0 failure。
- 该 run 的配筋输入尚未冻结，因此不能作为当前配筋成果；其 writeback、分析、设计和审计证据继续有效。

当前 read-only 配筋 run `20260730T103252Z-d96440dc`：

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
- 历史文档中的 84 至 208 passed 不能证明当前工作树；本基线只引用当前 212 项通过记录。
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
