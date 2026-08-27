---
id: VAL-20260827-TOWER-MIGRATION
type: validation
status: partial
owner: project
created: 2026-08-27
updated: 2026-08-27
related: [SPEC-PLUGIN-001]
supersedes: []
superseded_by:
---

# VAL-20260827：分塔检查最小迁移

## 身份与范围

- 目标目录：`F:\2026\Research Study\etabs beam-v2`，分支 `codex/clean-start`，起点 `6107b4b`。
- 来源：旧目录 `F:\2026\Research Study\etabs beam` 的工作区文件，不是旧目录 HEAD 的完整功能。
- 原样迁入 `src/core/tower_assignment.py`、`src/core/etabs_tower.py` 及两个同名单元测试文件。
- 适配 v2 已有 `model_source`、CLI、插件请求/运行器；分塔 GUI 独立在 `MainForm.Tower.cs`。
- 未迁入旧目录的拆分、编号、分析/设计、保存、交付布局及进度框架。没有更改旧目录。
- 原有次梁选筋、PB38 诊断及其文档/测试修改不属于此次迁移提交。

## 接口与安全边界

- `--tower-preflight-only` 仅检查并输出 JSON/CSV；在拓扑计算、设计结果读取和成果导出之前返回。
- 普通梁表可通过 `--tower-check` 执行前置检查；疑似问题需 `--tower-continue-on-review` 显式确认；BLOCKED 不可跳过。
- `--tower-create-groups --allow-tower-group-mutation` 为独立、明确确认的审查分组操作；不更改 Tower 归属，不保存/解锁/分析/设计。
- 已确认 Group 计划与当前模型哈希、分组成员不一致时拒绝操作；持久 ownership 按模型路径隔离，记录操作审计。
- GUI 恢复“检查 Tower 分配”“创建 Tower 审查 Group”入口。输出运行目录互不覆盖。
- Snapshot 分塔入口暂不迁入，明确报错，不能静默当作已检查。

## 迁移验证

- 原版分塔测试：14 passed。
- 分塔、CLI、连接器定向测试：55 passed。
- 仅暂存区内容导出的独立快照：完整离线测试 288 passed、1 skipped，覆盖率 80.56%；插件编译及 4 组参数测试再次通过。快照未包含前序未提交配筋改动。
- 当前完整工作区（包括保留的前序配筋改动）：297 passed、1 skipped，覆盖率 80.58%。两次测试范围不同，不能混为迁移提交测试数量。
- `dotnet run --project tests/plugin/PluginOfflineTests.csproj -c Release`：4 组参数场景通过；不附着 ETABS。
- 插件 Release 编译：0 warning / 0 error；引用 ETABS 23 安装目录的公共 `ETABSv1.dll`，没有打包或启动实模插件。
- 文档预检存在 31 个历史输出链接缺失；不属于此次迁移范围。

## 已知缺口与结论

迁移阶段通过离线门禁，不等于选择集问题已修复：此提交仍保留旧版逐塔切换、仅恢复活动塔的读取行为。
用户要求的顺序是先单独提交迁移，再补选择集保护、异常恢复和 GUI 连续运行回归。
本记录只证明迁移；后续修复须另留验证证据。未操作、分析或保存 ETABS 实际模型，未发布打包或推送远端。
