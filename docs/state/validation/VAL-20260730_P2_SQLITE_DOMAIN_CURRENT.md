---
id: VAL-20260730-P2-SQLITE-DOMAIN-CURRENT
type: validation
status: passed
owner: project
created: 2026-07-30
updated: 2026-07-30
run_id: p2-sqlite-domain-20260730T081820Z
git_commit: 7d3862752041fe18eb0201b21671f3575d70b52b
dirty: true
related:
  - ADR-0003
  - IP-P2-002
  - TASK-P2-SQLITE-DOMAIN-001
  - TASK-P2-SQLITE-001
  - BASELINE-001
supersedes: []
superseded_by:
  - VAL-20260730-P2-OAPI-SQLITE-WORKFLOW-PARITY
---

# VAL-20260730：P2 SQLite 只读领域数据源

## 1. Verdict

`PASSED`，范围为 `TASK-P2-SQLITE-DOMAIN-001`：当前 ETABS 22.7 / ACI 318-14 矩形混凝土梁 profile
已经具备生产 CLI、源身份与失效校核、梁/支承/设计 station/FrameType 回放、完整领域等价验证和
非覆盖 manifest。

本记录不关闭完整 `TASK-P2-SQLITE-001`。非矩形混凝土截面、OAPI/SQLite 工作流切换、CAD 共享
schema、写回后的自动失效联动、ETABS 23 重跑及正式 bundle 生命周期仍未完成。

## 2. 运行身份

| 项目 | 值 |
|---|---|
| 执行时间 | 2026-07-30 16:18 +08:00 |
| ETABS / API | ETABS `22.7.0`；`ETABSv1.dll 2.10.0.0` |
| API SHA-256 | `7cfbc7f317ffe7e88a930a04de4e8b2a5d8534e5b0e0411db0ab95a8d694ab6d` |
| 目标进程 | PID `60856`；模型在导出后仍锁定，设计结果仍可用 |
| 源 EDB | `build/output/p1_v645_working_20260730/EMHS-GF.v6.4.5.CRS_p1_working.EDB` |
| 源 EDB SHA-256 | `20347ccc0e3960ef10c91b107776b19dc1676648c8b621b79f8925202aff9c41` |
| 单位 / 规范 | `N_mm_C` / `ACI 318-14` |
| 快照 / 原始字段 / 领域映射 | `1.0` / `raw-oapi-table/1.0` / `beam-design-result/1.0` |
| Git HEAD / 工作树 | `7d3862752041fe18eb0201b21671f3575d70b52b` / `dirty: true` |
| 当前生产代码 content SHA-256 | `6b7b4fef3fc5ba2aa667698c45f683f4d6d44e12c300f29e56ff2c10d27ea276` |

content SHA-256 在本次代码、测试和实模校核完成后、写入本 Validation 等证据文档前生成；随后仅追加
治理 Markdown，不扩大本记录的代码证明范围。

## 3. 执行命令与只读边界

```text
python -m src.snapshot_cli
  --pid 60856
  --expected-model build/output/p1_v645_working_20260730/EMHS-GF.v6.4.5.CRS_p1_working.EDB
  --config build/p0_etabs22_config.yaml
  --output build/output/p2_sqlite_domain_20260730/etabs22_v645_domain_v2.sqlite
```

CLI 只读取公开 `DatabaseTables`、`FrameObj`、`DesignConcrete.GetSummaryResultsBeam_2` 和宿主/单位状态；
未保存、解锁、分析、设计或修改模型。导出后独立检查确认模型仍锁定、结果仍可用，durable EDB 哈希与
manifest 一致。源 EDB SHA-256 在 OAPI 采集前冻结，并在 SQLite 写入前和写入后再次匹配，覆盖完整
采集与发布窗口。

## 4. 成果

- [SQLite 快照](../../../build/output/p2_sqlite_domain_20260730/etabs22_v645_domain_v2.sqlite)：
  `249,294,848` bytes，SHA-256
  `5cc6664279906472af0e5a5787ff67651917f5d47475b7aaf8e485dd314caf1f`。
- [快照 manifest](../../../build/output/p2_sqlite_domain_20260730/etabs22_v645_domain_v2.sqlite.manifest.json)：
  `4,069` bytes，SHA-256
  `e76dc9f67a1d3fadd65537ead98af2047082328d79f3c2746c70d18166d728a0`。

快照包含 11 张公开 OAPI 表和 1 张项目归一化设计结果表。主要行数：

| 表 / 领域 | 行 |
|---|---:|
| Point Object Connectivity | 7,102 |
| Beam Object Connectivity | 4,144 |
| Column Object Connectivity | 1,082 |
| Frame Assignments - Section Properties / Summary | 各 7,075 |
| Concrete Beam Overwrites - ACI 318-14 | 4,094 |
| Brace Object Connectivity | 10 |
| `__project__.BeamDesignResult.v1` | 65,881 |

## 5. 领域等价结果

| 检查 | Live OAPI | SQLite 回放 | 结果 |
|---|---:|---:|---|
| 矩形混凝土梁 | 4,094 | 4,094 | 名称与字段集合相同；端点/截面深度误差均不超过 `0.01 mm` |
| 柱及锚定刚域支承点 | 1,441 | 1,441 | 名称集合相同；坐标误差均不超过 `0.01 mm` |
| 设计结果梁 | 4,094 | 4,094 | `BeamDesignResult` dataclass 全字段精确往返 |
| 设计 station | 65,881 | 65,881 | 组合、需求、最小量、Provided 原值、错误/警告、来源和单位精确往返 |
| FrameType | 4,094 | 4,094 | 所有几何梁均有记录，严格字段契约通过 |

设计状态为 `4,071 AVAILABLE`、`23 PARTIAL`。该统计是源 Object 级设计结果状态；P1 工作簿的最终状态
有不同的 fail-closed 优先级，不能直接用其 22 个 `DESIGN_RESULTS_PARTIAL` 行反推源结果数量。

## 6. 自动门禁

| 检查 | 结果 |
|---|---:|
| pytest | `197 passed, 1 skipped` |
| coverage | `79.49%`，门槛 `60%` |
| `compileall` | 通过 |
| Black | 通过，63 files unchanged |
| Flake8 | 通过 |
| 两个生产 `.mjs` 的 bundled Node `--check` | 通过 |
| `git diff --check` | 通过 |

原始离线证据：

- [pytest.xml](../../../build/output/validation/20260730-p2-domain-current/pytest.xml)，SHA-256
  `a17f8e30362921a861b6935a7e067a7feed320e7591c541032c7e71708159d4e`。
- [coverage.xml](../../../build/output/validation/20260730-p2-domain-current/coverage.xml)，SHA-256
  `9a3458cb6ee53ea090bc6224aa5827f2ef39908f9035221b290887e0a1e3c805`。

新增测试覆盖显式 PID 附着与 EDB/`.$et` 身份、全精度设计结果往返、源哈希失效、快照文件变化、
采集窗口源文件变化、锚定刚域支承、FrameType、bundle manifest、拒绝覆盖及验证失败清理。

## 7. 剩余边界

1. 当前实模只有矩形混凝土梁；没有真实 Concrete Tee / General 梁样本，故不作这些 shape 的兼容承诺。
2. Reader 已能检测源 EDB 哈希变化，但尚未接入写回 manifest 自动使旧 bundle 失效。
3. 当前拓扑、Excel 和 CAD 编排入口尚未提供 OAPI/SQLite 数据源选择。
4. CAD 正式交换 schema、ETABS 23 重跑及快照保留/升级策略仍属于
   [IP-P2-002](../../delivery/plans/active/IP-P2-002_SQLITE_DOMAIN_SOURCE.md)。
