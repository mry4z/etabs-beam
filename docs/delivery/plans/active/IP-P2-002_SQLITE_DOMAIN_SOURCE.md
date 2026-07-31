---
id: IP-P2-002
type: implementation-plan
status: in_progress
owner: project
created: 2026-07-30
updated: 2026-07-30
related:
  - ADR-0003
  - TASK-P2-SQLITE-DOMAIN-001
  - TASK-P2-SQLITE-001
  - VAL-20260730-P2-OAPI-SQLITE-WORKFLOW-PARITY
  - BASELINE-001
supersedes: []
superseded_by:
---

# IP-P2-002：SQLite 只读领域数据源

## 1. 目标

把 [IP-P2-001](IP-P2-001_SQLITE_SNAPSHOT_TECHNICAL_SPIKE.md) 验证过的公开 OAPI 原始表容器推进为可重复
运行、可失效判断、可按领域回放的只读数据源。该工作是 P2-1 的并行技术轨道，不替代仍受工程输入和签署
阻塞的 P1-1。

完整 `TASK-P2-SQLITE-001` 只有在受支持截面、工作流切换、CAD 共享 schema 和写回后自动失效均形成证据后
才能关闭。

## 2. 已完成交付

`TASK-P2-SQLITE-DOMAIN-001` 已由
[VAL-20260730-P2-OAPI-SQLITE-WORKFLOW-PARITY](../../../state/validation/VAL-20260730_P2_OAPI_SQLITE_WORKFLOW_PARITY.md)
验证：

1. `src/snapshot_cli.py` 按显式 PID 和 durable EDB 身份附着，只允许 ETABS 22/23，并在临时
   `N_mm_C` 上下文中执行只读导出。
2. `SnapshotProfile` 明确必需/可选公开表；当前 profile 只承诺 ACI 318-14 矩形混凝土梁。
3. 快照同时保存原始 `DatabaseTables` 行和项目归一化 `BeamDesignResult`；后者由公开
   `DesignConcrete.GetSummaryResultsBeam_2` 逐对象读取，以 `.17g` 文本保留 IEEE-754 往返精度。
4. `SqliteSnapshotSource` 回放梁几何、柱/锚定刚域支承和 `FrameType`；
   `SqliteDesignProvider` 回放 station、组合、需求、Provided 原值、错误、警告、状态、单位和来源。
5. Exporter 在发布前后复核源 EDB SHA-256，Reader 可在每次使用前 `assert_source_current`；
   快照本体变化后既有 Reader 也会 fail-closed。
6. 新快照和 manifest 均拒绝覆盖；领域等价验证失败时删除本次新建 bundle。
7. `ReadModelSource` 统一实时 OAPI 与 SQLite 的梁、支座、FrameState、设计、FrameType、截面和
   设计规范接口；主 CLI 可由 `--snapshot-input` 切换为离线只读工作流。
8. 项目规范化表以 `.17g` 保存公共 `GetAllFrames` 梁端点和支座坐标，同时保留原始
   `DatabaseTables` 行作来源审计；避免显示精度改变 1 mm 拓扑判定。
9. SQLite 分支使用同一拓扑、基础 Excel、配筋 payload 和主/SB 工作簿消费者；开始、工作簿生成前后
   均复核 snapshot 与源 EDB 身份，且在 manifest 中记录 `data_source` 和 snapshot SHA-256。

## 3. 当前安全边界

- 只使用 CSI 公开 `ETABSv1.dll`；不读取 EDB 内部表，不调用 ETABS 私有 SQLite 实现。
- 不保存、解锁、分析、设计或修改模型；设计结果必须已存在。
- 当前 profile 遇到有设计结果的非矩形混凝土梁时阻断，不静默丢弃。
- `.$et` 只可与同目录、同 stem 的显式 durable `.EDB` 匹配。
- 快照是派生只读证据，不替代 EDB，也不承担写回。

## 4. 剩余实施

| Gate | 当前状态 | 完成证据要求 |
|---|---|---|
| 非矩形混凝土梁 | `pending` | 获得包含 Concrete Tee / General 等实际样本；冻结公开表字段映射并完成 Object API 等价验证 |
| 数据源选择接口 | `verified-read-workflow` | ETABS 22 矩形梁的六个拓扑 JSON/CSV 逐字节一致，3,018 个主/SB payload 行逐字段一致；CAD 仍由独立依赖控制 |
| 写回后自动失效 | `verified-consumer-side` | SQLite 每次运行校核 durable EDB 路径/SHA 和 snapshot 自身身份；写回改变 EDB 后旧快照不能启动；注册表主动撤销仍可作为生命周期增强 |
| CAD 共享 schema | `blocked-by-dependency` | `TASK-CAD-001` 冻结后，快照映射版本与正式交换 schema 建立兼容测试 |
| 宿主兼容矩阵 | `partial` | ETABS 22.7 已验证；ETABS 23 当前版本重跑同一 profile 与完整领域等价门禁 |
| 发布与保留策略 | `pending` | 249 MB 级 bundle 的命名、归档、清理和兼容升级规则进入正式交付方案 |

## 5. 完成定义

1. `TASK-P2-SQLITE-001` 的全部 Acceptance 具备同一受支持版本证据。
2. 非矩形 shape 不被静默丢弃；无样本的 shape 不作兼容承诺。
3. OAPI 与 SQLite 可通过稳定领域协议切换，设计、拓扑和 CAD 消费结果等价。
4. 写回或源 EDB 内容变化会自动使旧快照失效。
5. ETABS 22/23 兼容矩阵、bundle 生命周期和升级/回退规则可复现。
