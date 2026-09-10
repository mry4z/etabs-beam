---
id: VAL-20260730-P2-SQLITE-SNAPSHOT-SPIKE
type: validation
status: passed
owner: project
created: 2026-07-30
updated: 2026-07-30
run_id: p2-sqlite-spike-20260730T063652Z
git_commit: 7d3862752041fe18eb0201b21671f3575d70b52b
dirty: true
related:
  - ADR-0003
  - IP-P2-001
  - TASK-P2-SQLITE-SPIKE-001
  - TASK-P2-SQLITE-001
  - BASELINE-001
supersedes: []
superseded_by:
---

# VAL-20260730：P2 SQLite 快照技术验证

## 1. Verdict

`PASSED`，范围仅为 `TASK-P2-SQLITE-SPIKE-001`：公开 `DatabaseTables` 原始表、项目自建 SQLite
容器、严格 Reader，以及当前模型中矩形混凝土梁 `BeamGeometry` 的 Object API 等价验证。

本记录不关闭 `TASK-P2-SQLITE-001`，也不证明支承、设计 station、其他混凝土截面、CAD 共享 schema、
写回后自动失效或生产 CLI 已完成。

## 2. 运行身份

| 项目 | 值 |
|---|---|
| ETABS / API | ETABS `22.7.0`；`ETABSv1.dll 2.10.0.0` |
| API SHA-256 | `7cfbc7f317ffe7e88a930a04de4e8b2a5d8534e5b0e0411db0ab95a8d694ab6d` |
| 源角色 | P1 非覆盖工作副本；只读 `DatabaseTables` 提取 |
| 源 EDB | `build/output/p1_v645_working_20260730/EMHS-GF.v6.4.5.CRS_p1_working.EDB` |
| 源 EDB SHA-256 | `5de2b42ae528c3893df91434ff6a37465dd65de402f7eb811f4f20bc133e9054` |
| 单位 / 规范 | `N_mm_C` / `ACI 318-14` |
| 快照 schema | `1.0` |
| 字段映射 | `raw-oapi-table/1.0` |

仅调用公开 `GetAvailableTables` 和 `GetTableForDisplayArray`；未调用
`SetTableForEditingArray`、`ApplyEditedTables`、ETABS 私有程序集或模型写回方法。

## 3. SQLite 成果

快照：
[etabs22_v645_public_tables.sqlite](<F:/2026/Research Study/etabs beam/build/output/p2_sqlite_spike_20260730/etabs22_v645_public_tables.sqlite>)（原工作区历史证据），
大小 `22,347,776` bytes，SHA-256
`2c2d909d057ad684a91ec14eaaf303124cd42a0a87ffa4c2a0f34c3753a5742e`。

| 公开表键 | Table version | 字段 | 行 |
|---|---:|---:|---:|
| `Story Definitions` | 1 | 8 | 18 |
| `Point Object Connectivity` | 1 | 10 | 7,102 |
| `Beam Object Connectivity` | 2 | 7 | 4,144 |
| `Frame Assignments - Section Properties` | 1 | 6 | 7,075 |
| `Frame Assignments - Summary` | 1 | 12 | 7,075 |
| `Frame Section Property Definitions - Concrete Rectangular` | 2 | 20 | 69 |
| `Frame Section Property Definitions - Concrete Beam Reinforcing` | 1 | 9 | 62 |
| `Material Properties - Concrete Data` | 2 | 11 | 3 |

写入使用同目录 `.partial`、事务、外键和 `PRAGMA integrity_check`；成功后发布为新文件，未覆盖 EDB
或已有 SQLite。

## 4. Object API 等价

同一模型、同一 `N_mm_C` 上下文分别执行：

1. `GeometryExtractor.extract("all")`；
2. `SqliteSnapshotSource(SnapshotReader(...)).extract_beams()`。

结果：

| 检查 | 结果 |
|---|---:|
| Object API 混凝土梁 | 4,094 |
| SQLite 重建混凝土梁 | 4,094 |
| UniqueName 集合 | 完全相同 |
| label/story/point I/J/section 不一致 | 0 |
| depth 不一致 | 0 |
| 端点坐标最大差异 | `0.006403124238737337 mm` |
| 坐标差异大于 `1 mm` | 0 |

首轮比较发现 `GeometryExtractor._frame_labels_by_name()` 把公开 OAPI 的 `MyName` 和 `MyLabel`
顺序解释反了；按 ETABS 22/23 签名修正并增加单元测试后重新比较，字段不一致归零。该发现同时修复
P1 当前几何标签证据。

## 5. 自动测试

- SQLite 专项：7 个测试通过，覆盖 OAPI 返回解析、坏行宽、缺表、未知 schema、字段漂移、拒绝覆盖、
  partial 生命周期和领域重建。
- 当前完整默认测试：`188 passed, 1 skipped`，coverage `80.12%`；精确原始证据见
  [VAL-20260730-P1-P2-OFFLINE-CURRENT](VAL-20260730_P1_P2_OFFLINE_CURRENT.md)。

## 6. 限制与后续

1. 当前领域适配只接受精确字段契约的 `Concrete Rectangular`；遇到其他 concrete shape 明确阻断。
2. 支承、刚域传播、`DesignStation`、设计错误/警告和写回快照尚未由 SQLite 重建。
3. 快照 metadata 已携带源模型哈希，但上层尚未在每次 ETABS 写回后自动标记旧快照失效。
4. 尚无正式 CLI/发布契约；本次调用是有界技术验证。
5. 完整 `TASK-P2-SQLITE-001` 仍等待 CAD 共享 schema 依赖及全部领域对象等价验证。
