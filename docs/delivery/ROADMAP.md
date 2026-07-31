---
id: ROADMAP-001
type: roadmap
status: active
owner: project
created: 2026-07-25
updated: 2026-07-30
related:
  - SPEC-001
  - BASELINE-001
  - MS-P1-001
  - IP-P1-001
  - IP-P2-002
supersedes:
  - LEGACY-TECHNICAL-ROADMAP
superseded_by:
---

# ROADMAP-001：交付路线图

## 1. 权威边界

本路线图只定义阶段性交付结果、依赖和顺序。当前能力、运行数量和测试结果以
[Current Baseline](../state/CURRENT_BASELINE.md)及其 Validation Record 为准；
当前可执行动作以[任务登记表](tasks/TASK_REGISTER.md)为准；单次跨模块变更以相应
Implementation Plan 为准。

## 2. 阶段顺序

| 阶段 | 交付结果 | 状态 | 进入条件 | 退出依据 | 执行入口 |
|---|---|---|---|---|---|
| P1-1 配筋工程闭环 | ACI 318-14 配筋输入、profile、校核、相邻跨协调、工作簿和实模证据形成同一可审计闭环 | `active` | [BASELINE-001](../state/CURRENT_BASELINE.md) | [MS-P1-001](../specs/milestones/MS-P1-001_REBAR_ENGINEERING_CLOSURE.md) 的 P1-G01 至 P1-G08 | [IP-P1-001](plans/active/IP-P1-001_REBAR_ENGINEERING_CLOSURE.md)、`TASK-P1-001` 至 `TASK-P1-008` |
| P1-2 稳定编号 | 一个 formatter 服务模型名称、JSON、DXF 和 Excel；GUID/空间指纹映射可报告新增、删除和重编号 | `backlog` | P1-1 的输入/schema 边界稳定；编号 Spec 保持 accepted | 同一输入编号确定；局部变化不导致无说明的全层漂移；override 冲突 fail-closed | `TASK-NUM-001` 至 `TASK-NUM-003` |
| P1-3 CAD 交换与成果生命周期 | 版本化 CAD JSON、严格 I→J 方向契约、DXF 派生和统一生成物生命周期 | `backlog` | P1-2 formatter 与稳定映射可用 | JSON schema、消费者兼容测试、方向复核、输出目录/忽略策略通过验收 | `TASK-CAD-001`、`TASK-ART-001` |
| P2-1 SQLite 只读快照 | 公开 OAPI 批量表可导出为带 manifest 的项目 SQLite，并与对象 API 领域对象做等价比较 | `in_progress` | [ADR-0003](../decisions/ADR-0003_PUBLIC_OAPI_SQLITE_SNAPSHOT.md) 已接受；矩形梁 profile 和 ETABS 22 样本已验证 | 非矩形样本、数据源切换、CAD schema、自动失效、ETABS 23 与 bundle 生命周期完成 | [IP-P2-002](plans/active/IP-P2-002_SQLITE_DOMAIN_SOURCE.md)、`TASK-P2-SQLITE-001` |
| P2-2 插件与交付扩展 | CAD/ETABS 交互、脱敏集成模型、CI/CD 和发布边界建立 | `backlog` | P1-2、P1-3 的交换契约稳定；P2-1 按需完成 | 每项扩展有独立 Milestone、Plan、安装/回滚和集成证据 | `TASK-P2-PLUGIN-001`、`TASK-P2-DELIVERY-001` |

## 3. 依赖与并行规则

1. P1-1 仍是当前工程关闭优先级最高的里程碑；P2-1 可作为独立只读技术轨道推进，但不得替代或放宽
   P1-1 的退出门禁。
2. P1-2 可先进行 formatter 和映射设计，但不得在 P1-1 schema 尚未稳定时冻结跨通道接口。
3. P1-3 的 schema 草案可与 P1-2 并行；正式兼容承诺必须等待稳定编号接口。
4. P2-1 的矩形梁 Reader、统一只读领域协议及 OAPI/SQLite 拓扑/Excel/配筋切换已经完成；
   非矩形样本、CAD schema、ETABS 23 和 bundle 生命周期完成前，不扩大当前 profile 的兼容承诺。
5. P2-2 中每个扩展必须单独立项；不得把 GUI、插件、CI/CD 和多宿主支持合并为一个无边界任务。
6. [IP-P2-003](plans/active/IP-P2-003_ETABS_PLUGIN_V01.md) 是用户优先级驱动的 ETABS v0.1 技术垂直切片；
   它可在正式 P2-2 进入条件完成前验证加载合同和只读交互，但不改变本表的阶段状态，也不解除
   `TASK-NUM-003`、`TASK-CAD-001`、ETABS Tools 菜单或 ETABS 23 门禁。

## 4. 延后范围

- SAP2000、其他 ACI 版本和 GB 规范不进入当前 P1 交付。
- GUI、安装包和正式插件不作为 P1-1 的关闭条件。
- SQLite 不承担分析加速承诺，也不承担 EDB 写入。
- 当前历史生成物不因路线图更新自动成为当前证据；必须由对应 Validation Record 裁定。

## 5. 更新规则

- 里程碑范围或退出门禁变化时，先更新 Milestone Spec，再更新本路线图。
- durable 方案变化时先建立或 supersede ADR；路线图只引用已裁决方案。
- 阶段开始时批准 Implementation Plan 并建立稳定 Task ID。
- 阶段完成时先冻结 Validation/Review，再更新 Current Baseline，最后将阶段状态改为 `completed`。
- 本路线图不得记录一次运行的计数、哈希、测试通过数或工作树状态。
