---
id: REF-SCHEMA-001
type: reference
status: current
owner: project
created: 2026-07-25
updated: 2026-07-30
related:
  - SPEC-TOPO-002
  - SPEC-DRAW-001
  - SPEC-DESIGN-001
  - REF-API-001
supersedes: []
superseded_by:
---

# Interchange Schemas

本页区分三类数据：人工拓扑权威文件、CLI 审计输入/输出、正式图纸交换契约。三者用途不同，不能仅因都使用
JSON 而互相替代。JSON 使用 UTF-8；三维坐标顺序统一为 `[x, y, z]`，单位为 mm。

## 1. 人工拓扑 Override `1.0`

`src.core.topology_override` 读写以下根对象：

```json
{
  "schema_version": "1.0",
  "model_fingerprint": "<source-model-fingerprint>",
  "groups": [
    {
      "group_id": "MG-0001",
      "kind": "MANUAL_SPAN",
      "base_run_id": "<run-id>",
      "story": "F1",
      "object_guids_in_i_to_j_order": ["guid-1", "guid-2"],
      "object_reversed_in_i_to_j_order": [false, true],
      "object_names_at_creation": ["101", "102"],
      "automatic_span_ids_before_override": ["F1-PB5-3"],
      "i_end_coordinate_mm": [0.0, 0.0, 0.0],
      "reason": "人工确认连续路径",
      "force_support_override": false,
      "support_conflicts_mm": []
    }
  ]
}
```

`schema_version` 必须精确为 `1.0`；`kind` 只接受 `MANUAL_LINE`、`MANUAL_SPAN`。GUID 数组与方向数组
长度必须一致。名称只供审计，重新加载按 GUID 解析。集合拒绝重复 group ID 及同类 group 的 GUID 重叠。

## 2. CLI 审查输入

多截面确认接受两种等价根结构：

```json
["F1-PB5-3"]
```

```json
{"confirmed_span_ids": ["F1-PB5-3"]}
```

专项写回排除同样接受字符串数组，或：

```json
{"excluded_object_names": ["52", "56"]}
```

数组元素必须为非空字符串；加载后去空白和去重。

## 3. CLI 拓扑审计输出

### `topology_spans.csv`

固定列为：`梁编号, 楼层, 主次梁, 弧形, 悬挑, 源对象, 路径点坐标`。源对象用 `+` 分隔；路径点用
`;` 分隔，每点为 `x,y,z`。

### `requires_split_audit.json`

根节点为数组，每项字段为：

```text
object_name, guid, label, story, section,
point_i, point_j, start, end,
span_ids, beam_types, cantilevers, status, reason
```

同名 CSV 使用对应审查列；该文件表示一个 ETABS Object 贡献多个结构 Span，不是安全改名映射。

### `multi_section_span_review.json`

根节点为数组，每项字段为：

```text
span_id, story, beam_type, line_number, span_index,
sections, section_objects, source_objects, path_points,
etabs_group_name, status, reason
```

`status` 为 `REQUIRES_SECTION_REVIEW` 或 `CONFIRMED_STEP_BEAM`；Group 名由 `CODX_MS_<SpanID>`
清理并截断得到，仅是 ETABS 审查镜像。

### `topology_diagnostics.json`

根节点为 `TopologyDiagnostic` 数组，字段为 `code, severity, story, source_objects, message, candidates`。
`BLOCKED` 诊断参与写回门禁。

## 4. Run summary 与 manifest

`topology_summary.json` 是本次 CLI summary，包含模式、模型、单位、环境、结果计数、阻塞摘要和成果路径。
`run_manifest_<run-id>.json` 由 `RunManifest` 生成，记录参数、配置、Git、模型、输入指纹、返回状态及成果
哈希。消费者必须以 manifest 的 run ID 和输入身份关联成果，不能按文件名时间戳拼接不同运行。

## 5. 配筋工作簿 payload `2.0`

`src.rebar_workbook` 生成的正式配筋工作簿 payload 根对象固定包含：

```json
{
  "metadata": {
    "schema_version": "2.0",
    "units": "N_mm_C",
    "cover_calculation_basis": "ETABS_LONGITUDINAL_CENTROID",
    "clear_cover_to_stirrup_outer_mm": null,
    "clear_cover_source": "",
    "max_aggregate_size_mm": 25.0,
    "max_aggregate_size_source": "Project default: 25 mm maximum aggregate size"
  },
  "raw_rows": [],
  "review_rows": [],
  "final_rows": []
}
```

`etabs_longitudinal_cover_mm`、`clear_cover_to_stirrup_outer_mm` 和
`max_aggregate_size_mm` 是相互独立的工程输入；各自来源随行或在 metadata 中保留。
`cover_calculation_basis` 明确当前选择：

- `ETABS_LONGITUDINAL_CENTROID`：ETABS `CoverTop/CoverBot` 是混凝土面至纵筋重心距离；有效高度
  直接扣该值，排布净宽针对候选纵筋/箍筋反算；
- `CLEAR_TO_STIRRUP_OUTER`：使用有来源的显式箍筋外缘净保护层。

当前项目使用前一种口径，最大骨料在配置层缺省解析为有来源的 25 mm；显式配置或后续插件选择可覆盖，
但必须写入来源。进入 payload 后，缺少所选口径必需的 cover、解析后骨料、材料或截面语义时，
行状态为 `COVER_SEMANTICS_UNRESOLVED`，不得以历史默认值补齐。

每个 `raw_rows` 项保留左、中、右区的原始需求、Provided 原始值和归一化值、station、控制组合、
单位、来源 Object、ETABS error/warning，以及 profile、初始配筋、相邻跨协调状态和最终签名。
`review_rows` 展开逐区需求、最终纵筋直径、箍筋强度/最小量/最大间距控制和迭代状态；
`final_rows` 只呈现同一结果的交付字段与机器可读 `engine_status`，不得覆盖前两类审计信息。

`2.0` 是 cover/aggregate 分义后的破坏性版本。离线 redetail 和独立 SB schedule 只接受精确
`2.0`；`1.2` 或缺失版本返回 `UNSUPPORTED_PAYLOAD_SCHEMA`，不能静默迁移。SB 成功行必须在三个
位置使用同一直径、间距和 legs；无法统一或相邻跨无法收敛时分别以
`SB_SPAN_STIRRUP_NOT_UNIFIED` 或 `ADJACENT_SPAN_COORDINATION_NOT_CONVERGED` 阻断。

## 6. 正式图纸交换契约

[SPEC-DRAW-001](../../specs/SPEC-DRAW-001_NUMBERING_INTERCHANGE.md) 要求正式交换包至少包含：

```json
{
  "schema_version": "drawing-beam-interchange/1.0",
  "generated_at": "2026-07-25T00:00:00+08:00",
  "run_id": "<run-id>",
  "units": "N_mm_C",
  "model": {
    "path": "<model-path>",
    "sha256": "<sha256>",
    "role": "source-or-working-copy"
  },
  "environment": {
    "etabs_version": "<version>",
    "api_sha256": "<sha256>",
    "git_commit": "<commit>",
    "dirty": false,
    "configuration_fingerprint": "<fingerprint>",
    "topology_version": "<version>",
    "numbering_policy_version": "<version>"
  },
  "spans": [
    {
      "logical_beam_id": "F1-PB5-3",
      "beam_line_id": "F1-PB5",
      "story": "F1",
      "beam_type": "PB",
      "span_index": 3,
      "source_object_guids": ["<guid>"],
      "source_object_names": ["F1-PB5-3.1"],
      "i_point": [0.0, 0.0, 0.0],
      "j_point": [6000.0, 0.0, 0.0],
      "path_points": [[0.0, 0.0, 0.0], [6000.0, 0.0, 0.0]],
      "local_1_direction": [1.0, 0.0, 0.0],
      "supports": [],
      "states": [],
      "override_group_ids": []
    }
  ]
}
```

`logical_beam_id` 的跨号不含 `S`；Object 名按 I→J 为 `.1`, `.2`, ...。`local_1_direction` 是有向
单位向量，CAD 不得为可读性翻转。正式包必须携带单位、模型和算法身份，不能依赖目录旁的隐含上下文。

当前 `src.cli` 输出审计 CSV/JSON，但没有序列化上述正式包的独立 writer。因此审计文件不得标记为
`drawing-beam-interchange/1.0`，CAD 消费者也不得从审计 DXF/CSV 猜测缺失的 GUID、方向或证据字段。

## 7. 兼容与变更

- schema 消费者按 `schema_version` 分派，未知版本 fail-closed。
- 新增可选字段不得改变既有字段语义；删除、改名或单位变化必须升级 schema 版本。
- JSON 中保留原始 ETABS error/warning 和不可用哨兵的审计值；业务字段按
  [Design Results Reference](../api/design-results.md) 归一化。
- schema 更新同时修改序列化源码、测试、本 Reference 和关联 Spec/ADR。
