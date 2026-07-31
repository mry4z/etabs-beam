---
id: IP-P2-001
type: implementation-plan
status: verified
owner: project
created: 2026-07-30
updated: 2026-07-30
related:
  - ADR-0003
  - TASK-P2-SQLITE-SPIKE-001
  - TASK-P2-SQLITE-001
  - BASELINE-001
supersedes: []
superseded_by:
---

# IP-P2-001：SQLite 快照公开 OAPI 技术验证

## 1. 目标

在不等待 CAD 共享 schema 的情况下，先验证 [ADR-0003](../../../decisions/ADR-0003_PUBLIC_OAPI_SQLITE_SNAPSHOT.md)
中可独立实施的底层边界：通过公开 `DatabaseTables` OAPI 读取显式表键，把原始字段和记录写入项目自建
SQLite，并由严格 Reader 重放。

本计划是 P2-1 的技术 spike：除原始表快照外，允许建立仅面向矩形混凝土梁的受限
`SqliteSnapshotSource -> BeamGeometry` 原型并与 Object API 比较；它不是正式生产数据源，不替代 EDB，
不写回 ETABS，也不关闭 `TASK-P2-SQLITE-001`。完整任务仍等待 CAD 共享 schema 稳定、其他领域对象、
自动失效和生产 CLI。

## 2. 实施范围

1. 定义版本化快照 metadata、原始表/字段/行和来源身份。
2. 用公开 `GetAvailableTables` 和 `GetTableForDisplayArray` 读取调用方显式请求的表。
3. 对非零返回码、不可用表、字段重复、行宽不整除和记录数不一致 fail-closed。
4. 先写同目录 `*.partial`，提交事务后执行 `PRAGMA integrity_check`，再原子改名；失败时清理 partial。
5. Reader 校验快照 schema、完整性、必需表和精确字段契约。
6. 用 fake OAPI 完成单元测试，并在 ETABS 22.7 工作副本执行只读实模快照。
7. 用严格字段契约重建矩形混凝土梁 `BeamGeometry`，与同模型 Object API 比较名称、标签、截面、
   I/J 端点和几何误差；其他 shape 明确阻断。

## 3. 安全边界

- 只调用 CSI 公开 `ETABSv1.cDatabaseTables`；不反射或调用 ETABS 私有实现。
- 不调用 `SetTableForEditingArray`、`ApplyEditedTables` 或其他模型修改方法。
- 快照输出路径不得是 `.EDB`，不得覆盖既有快照。
- 每次导出记录源模型路径和 SHA-256、ETABS/API 版本、单位、设计规范、表版本、字段顺序和映射版本。
- 写回、分析或设计后，旧快照由上层按输入指纹判定过期；本 spike 不宣称自动替代当前数据源。

## 4. 验证

| 范围 | 证据 |
|---|---|
| OAPI 签名与解析 | fake 返回码、表数组、空表、坏行宽和记录数测试 |
| SQLite 原子性 | partial 生命周期、完整性检查、拒绝覆盖和失败清理测试 |
| Reader | 缺表、未知 schema、字段漂移、顺序重放测试 |
| ETABS 22.7 | 工作副本只读导出、快照哈希、metadata、表/行数量及 4,094 根矩形梁 Object API 等价 Validation |

## 5. 完成定义

1. `TASK-P2-SQLITE-SPIKE-001` 的验收全部具备同版本证据。
2. Architecture、Reference 和 Current Baseline 只声明已验证的原始表快照能力。
3. `TASK-P2-SQLITE-001` 保持 `backlog`，并明确剩余支承/DesignStation/非矩形截面、CAD schema、
   自动失效、正式 CLI 和完整领域等价测试。

验收证据见
[VAL-20260730-P2-SQLITE-SNAPSHOT-SPIKE](../../../state/validation/VAL-20260730_P2_SQLITE_SNAPSHOT_SPIKE.md)。
