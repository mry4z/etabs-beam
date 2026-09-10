---
id: ARCH-001
type: architecture
status: accepted
owner: project
created: 2026-07-25
updated: 2026-09-10
related:
  - SPEC-001
  - SPEC-002
  - ADR-0001
  - ADR-0002
  - ADR-0003
  - ADR-0005
  - VAL-20260827-TOWER-SELECTION-RECOVERY
  - VAL-20260908-DRAWING-EXPORT-REVIEW
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
        +--> Detailing Engine --> PB/SB 单表静态 Excel + JSON 审计
        `--> Writeback Plan --> 工作副本事务 --> 分析/设计/重读
                                                 |
                                                 v
                                  Manifest + Validation Evidence
```

## 3. 组件职责

| 组件 | 责任 |
|---|---|
| `src/core/etabs_connector.py` | 宿主连接、版本差异、临时单位上下文。 |
| `src/core/etabs_selection.py` | 完整选择集快照、公开 `PreviousSelection` 批量恢复、模型身份守卫和恢复审计。 |
| `src/core/etabs_tower.py` | 公共 Story/Point/Connectivity 表充分性判断、分塔预检、按塔回退和阶段计时。 |
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
| `src/rebar_workbook.py` | 生成版本化配筋 JSON 结果/审计 payload，并编排 PB/SB 静态结果投影；不定义业务规则。 |
| `src/spreadsheet/render_rebar_workbook_xlsx.py` | 将单个 PB 或 SB 角色投影为单表静态 Excel；不写公式、不改变业务规则。 |
| `src/model_source.py` | 实时 OAPI 与 SQLite 共用的只读领域协议及源身份门禁。 |
| `src/snapshot/sqlite_snapshot.py` | 公开 OAPI 原始表、严格 Reader、全精度梁/支承/设计结果及 FrameType/截面回放。 |
| `src/snapshot/exporter.py` | 版本化 profile、bundle 导出、源哈希失效和 live OAPI 领域等价验证。 |
| `src/snapshot/workflow.py` | 禁止模型修改的 SQLite 拓扑、Excel 和配筋完整编排。 |
| `src/snapshot_cli.py` | 按显式 PID/EDB 身份执行只读 SQLite profile 导出。 |
| `src/run_manifest.py` | 运行身份、模型/成果哈希和注册表有效性。 |
| `src/workflow.py` | 早期简化只读 Excel 入口。 |
| `src/cli.py` | 当前完整编排入口：只读、写回、分析设计、审计和导出。 |
| `plugins/etabs/BeamGenerator.EtabsPlugin/` | ETABS 插件合同、宿主 UI 会话/选择集交互、受控恢复审计、Python `--read-only` runner 和显式 Group 审查入口；不承载领域算法，普通成果工作流不修改模型业务数据。 |
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
- 梁组 DXF/CSV 共享分类、空间顺序和旧 `-S1` 输出编号；结构跨默认使用不含 `S` 的交付编号，`BeamSpan.span_id` 仍是内部身份，自定义 resolver 优先。
- CAD 文字可为可读性单独翻转显示角度，但不改变结构 I→J、路径顶点或梁表左中右；方向缺失/零向量/路径异常仍属于正式出图门禁。
- 生成器输入为版本化 payload；工作簿不是业务规则权威。
- 插件与 Python 只通过显式路径、结构化命令参数和版本化输出交互；普通成果工作流不生成模型 mutation，显式 Group 审查仅经确认门禁修改插件所有的 Group 元数据/成员。

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

## 8. 分塔预检与状态恢复

`EtabsTowerReader` 优先消费字段完整的公共 Story Definitions、Point 和 Beam Connectivity 表；同名
Story 即使共享绝对标高，也按有限 Point Z 和同标高连接关系划分拓扑 Region，再在每个 Region 内用
Tower 标签的一致性定位异常构件。该路径不切换活动 Tower，也不写选择集。信息不足时才进入按 Tower
的只读 API 路径，并在开始前保存模型身份、活动 Tower、单位和完整 `(ObjectType, UniqueName)` 选择快照。

同标高 Region 只有唯一主导 Tower 时才形成预期归属；拓扑并列、缺失有限 Point Z 或缺失连接端点时保持
`REVIEW_REQUIRED`/`BLOCKED`，不使用当前 Tower 标签循环证明自身正确。

Tower 预检是独立的可选审查操作；普通梁表工作流不自动等待、不重复调用，也不把预检 `BLOCKED` 作为梁表入口门禁。
预检状态和诊断原因仍须保留在报告、日志和界面中；只有显式的审查 Group 操作才消费通过的审查计划。

选择恢复优先调用公开 `SelectObj.PreviousSelection()`，校验返回码并重新读取完整集合；不符时才使用一次受控的
逐对象回退。Python 与 .NET 宿主通过恢复审计交接责任，避免正常结束后的二次全量恢复；取消、硬终止、审计缺失、
模型身份改变或最终集合不符均保持明确的 `BLOCKED`/人工处理边界。相关离线证据见
[分塔选择集恢复记录](../state/validation/VAL-20260827_TOWER_SELECTION_RECOVERY.md)。

## 9. ETABS 插件交互层

[ADR-0005](../decisions/ADR-0005_ETABS_PLUGIN_THIN_LAUNCHER.md)定义初始插件为 x64 `.NET 8` 薄交互层。
ETABS 通过 `cPlugin.Main(ref SapModel, ref cPluginCallback)` 传入当前模型；插件直接读取宿主身份和选择集，
可导出 `plugin-selection@1`，或用当前 ETABS PID 和 durable EDB 路径启动外部
`python -m src.cli --read-only`。窗口关闭时恰好一次调用 `Finish`。

插件不复制 `ETABSv1.dll`。普通成果工作流不修改模型几何、Tower 归属、Group 或对象，也不提供保存、解锁、
分析、设计和编号写回命令；宿主 `UiSessionCheckpoint` 允许在受控审计下临时恢复捕获的活动 Tower、显示单位和
完整选择集，这是 UI 会话状态恢复，不是业务模型修改。显式的 Tower 审查 Group 操作另有确认门禁，只修改插件
所有的 Group 元数据/成员，不选择对象、不保存模型，并保留失败回退。初始 v0.1 的“完全不写 UI 状态”描述仅适用
历史边界，不覆盖当前 v2 的恢复实现。正式加载矩阵与当前限制以
[Current Baseline](../state/CURRENT_BASELINE.md) 和对应 Validation Records 为准。

## 10. 验证模型

每层验证不同责任：纯算法单元测试、OAPI opt-in 集成测试、精确实模 run、成果哈希和人工图形/工程复核。Current Baseline 只汇总同版本证据。
