---
id: ARCH-001
type: architecture
status: accepted
owner: project
created: 2026-07-25
updated: 2026-07-30
related:
  - SPEC-001
  - SPEC-002
  - ADR-0001
  - ADR-0002
  - ADR-0003
  - ADR-0005
supersedes:
  - LEGACY-PROJECT-STRUCTURE
  - LEGACY-TECHNICAL-ROADMAP
superseded_by:
---

# ARCH-001：系统架构

## 1. 系统边界

系统核心是 ETABS 进程外的 Python 工具；可选的 ETABS 进程内 .NET 插件只承担交互和显式启动入口。
公开 OAPI 是模型读取和写入边界；领域算法、Excel/DXF/JSON 和工作簿生成不应直接依赖 COM 调用细节，
也不得在插件中重复实现。

## 2. 当前数据流

```text
ETABS 22/23 + ETABSv1.dll
        |
        +--> .NET 8 薄插件 --> 宿主/选择集 JSON
        |                   `--> 显式参数 --> Python CLI --read-only
        |
        v
连接/版本/单位/批量几何快照
        |
        v
BeamGeometry + 支承 + DesignStation
        |
        v
Beam Line / Span + Diagnostics + Override
        |
        +--> 只读审计、DXF/CSV/JSON
        +--> SpanScheduleMapping --> 基础 Excel
        +--> Detailing Engine --> Payload --> 三表工作簿
        `--> Writeback Plan --> 工作副本事务 --> 分析/设计/重读
                                                 |
                                                 v
                                  Manifest + Validation Evidence
```

## 3. 组件职责

| 组件 | 责任 |
|---|---|
| `src/core/etabs_connector.py` | 宿主连接、版本差异、临时单位上下文。 |
| `src/core/geometry_extractor.py` | 批量 Frame 快照、梁筛选、柱和锚定刚域支承。 |
| `src/core/beam_line_span_analyzer.py` | Beam Line/Span、PB/SB 固定点、路径切割和诊断。 |
| `src/core/topology_override.py` | GUID 人工 override、连通、顺序、冲突和失效。 |
| `src/core/section_review.py` | 多截面 Span 审计及 ETABS Group 镜像。 |
| `src/core/model_writeback.py` | 快照、计划、方向/改名事务、回滚、属性和幂等复核。 |
| `src/design/etabs_result.py` | ETABS 设计数组到稳定领域对象。 |
| `src/design/detailing.py` | Code Policy、Project Policy、profile 和配筋排布。 |
| `src/excel_span_adapter.py` | 将源 Object 设计站点投影到结构 Span。 |
| `src/drawing/dxf_export.py` | 只消费有向拓扑，不访问 ETABS。 |
| `src/excel_export.py` | 消费领域对象生成基础工作簿。 |
| `src/rebar_workbook.py` | 配筋 payload、离线 redetail 和工作簿编排。 |
| `src/model_source.py` | 实时 OAPI 与 SQLite 共用的只读领域协议及源身份门禁。 |
| `src/snapshot/sqlite_snapshot.py` | 公开 OAPI 原始表、严格 Reader、全精度梁/支承/设计结果及 FrameType/截面回放。 |
| `src/snapshot/exporter.py` | 版本化 profile、bundle 导出、源哈希失效和 live OAPI 领域等价验证。 |
| `src/snapshot/workflow.py` | 禁止模型修改的 SQLite 拓扑、Excel 和配筋完整编排。 |
| `src/snapshot_cli.py` | 按显式 PID/EDB 身份执行只读 SQLite profile 导出。 |
| `src/run_manifest.py` | 运行身份、模型/成果哈希和注册表有效性。 |
| `src/workflow.py` | 早期简化只读 Excel 入口。 |
| `src/cli.py` | 当前完整编排入口：只读、写回、分析设计、审计和导出。 |
| `plugins/etabs/BeamGenerator.EtabsPlugin/` | ETABS 插件合同、只读模型/选择集交互和 Python `--read-only` runner；不承载领域算法或 mutation。 |
| `tools/EtabsPluginValidator/` | 离线校验 x64、目标框架、`<AssemblyName>.cPlugin` 的 ETABS 加载器解析/实例化、接口签名和 API DLL 打包边界。 |
| `tools/EtabsPluginSmokeHost/` | 以真实 ETABS `SapModel` 调用精确插件合同的可重复 smoke；不替代 ETABS 菜单加载验收。 |

## 4. 核心数据模型

- `BeamGeometry`：源 Object、GUID/名称、楼层、节点、三维路径、截面和材料。
- `BeamLine` / `BeamSpan`：有向逻辑梁、PB/SB、支承、源 Object 和编号。
- `TopologyDiagnostic`：严重度、代码、源对象、候选和说明。
- `ManualTopologyGroup/Overrides`：GUID 驱动的人工关系、方向、冲突和模型指纹。
- `DesignStation/BeamDesignResult`：station、组合、需求、状态和原始字段。
- `SpanScheduleMapping`：源 Object 站点到 Span 的可追溯映射。
- `BeamDetailingInput/Result`：profile、控制原因、配筋和失败状态。
- `WritebackPlan`：方向、改名、阻塞项和预期状态。
- `RunManifest`：输入身份、环境、Git、算法、返回码、计数和成果哈希。

## 5. 两条运行路径

### 只读路径

确认实例和模型后读取几何/设计结果，生成拓扑、诊断和导出。只读路径不保存 EDB，不运行分析设计。

### 写回路径

原始 EDB哈希 -> 不覆盖工作副本 -> 只读计划 -> 门禁 -> 显式方向事务 -> 两阶段改名 -> 保存重开 -> 分析设计 -> 全属性/需求/幂等复核 -> 导出 -> manifest。

部分写回是一种带限制的执行模式，不是新的成功定义。

## 6. 接口边界

- OAPI 适配层处理 pythonnet `out/ref`、版本和单位差异。
- 领域算法只接受 Python 数据对象。
- 导出层不直接读取或修改 ETABS。
- 编号变化通过 formatter/adapter，不修改 Excel 核心列契约。
- 生成器输入为版本化 payload；工作簿不是业务规则权威。
- 插件与 Python 只通过显式路径、结构化命令参数和版本化输出交互；插件源码不生成 mutation 参数。

## 7. SQLite 数据源扩展

[ADR-0003](../decisions/ADR-0003_PUBLIC_OAPI_SQLITE_SNAPSHOT.md) 接受 OAPI 批量表到项目 SQLite
快照路线。当前 ETABS 22 / ACI 318-14 矩形混凝土梁 profile 已建立以下独立层：

```text
Public DatabaseTables OAPI + FrameObj.GetAllFrames
        |
        v
OapiTable (table version + exact field order + raw rows)
        +
DesignConcrete.GetSummaryResultsBeam_2
        |
        v
project-normalized BeamGeometry + ColumnSupport + BeamDesignResult
(full-precision text round trip)
        |
        v
project-owned SQLite (*.partial -> integrity_check -> atomic publish)
        |
        +--> strict SnapshotReader + source SHA invalidation
        +--> SqliteSnapshotSource -> BeamGeometry + supports + FrameState
        |                           + FrameType + section/material
        `--> SqliteDesignProvider -> DesignStation + status/source
                                     |
                                     v
                         SqliteReadSource / ReadModelSource
                                     |
              topology + base Excel + rebar/SB workbooks
```

当前矩形 profile 的真实规模、版本与等价结果只记录在
[P2 Validation](../state/validation/VAL-20260730_P2_SQLITE_DOMAIN_CURRENT.md)。完整 P2 数据源仍需其他
混凝土截面真实样本、CAD 共享 schema、ETABS 23 重跑和 bundle 生命周期。原生 ETABS SQLite 只有在
CSI 提供公开自动化接口后接入。

## 8. ETABS 插件交互层

[ADR-0005](../decisions/ADR-0005_ETABS_PLUGIN_THIN_LAUNCHER.md)定义初始插件为 x64 `.NET 8` 薄交互层。
ETABS 通过 `cPlugin.Main(ref SapModel, ref cPluginCallback)` 传入当前模型；插件直接读取宿主身份和选择集，
可导出 `plugin-selection@1`，或用当前 ETABS PID 和 durable EDB 路径启动外部
`python -m src.cli --read-only`。窗口关闭时恰好一次调用 `Finish`。

插件不复制 `ETABSv1.dll`，不设置单位，不修改选择、Group 或对象，也不提供保存、解锁、分析、设计和写回
命令。正式加载矩阵与当前限制以
[Plugin Validation](../state/validation/VAL-20260730_P2_ETABS_PLUGIN_V01.md)为准。

## 9. 验证模型

每层验证不同责任：纯算法单元测试、OAPI opt-in 集成测试、精确实模 run、成果哈希和人工图形/工程复核。Current Baseline 只汇总同版本证据。
