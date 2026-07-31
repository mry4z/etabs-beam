---
id: REF-API-001
type: reference
status: current
owner: project
created: 2026-07-25
updated: 2026-07-30
related:
  - ARCH-001
  - SPEC-002
  - SPEC-TOPO-001
  - SPEC-DESIGN-001
  - ADR-0003
  - REF-OAPI-001
  - REF-API-DESIGN-RESULTS
supersedes:
  - LEGACY-API
superseded_by:
---

# Python API Reference

本页按受跟踪源码列出可复用的 Python 入口。它描述调用边界，不声明功能验收状态；验证范围见
[Current Baseline](../state/CURRENT_BASELINE.md)。以下未以下划线开头的函数和类是仓库内复用接口；
`src.cli` 中以下划线开头的帮助函数属于命令内部实现。

## 1. 运行入口

| 入口 | 返回值 | 边界 |
|---|---|---|
| `src.cli.main(argv=None)` | 进程状态码 | 完整 CLI；打印 JSON summary，异常返回 `2`。 |
| `src.cli.run(args)` | `dict[str, Any]` | 执行 CLI 参数对象，并在成功或失败时写 run manifest。 |
| `src.workflow.run(config, selection_mode=None)` | `Path` | 简化活动实例基础 Excel 入口；不接收 PID/期望模型，不提供写回和 manifest 编排。 |
| `src.config.load_config(path=None)` | `AppConfig` | 加载指定 YAML；未指定或文件不存在时使用仓库中的 `config.example.yaml`。 |

正式操作命令见 [用户指南](../guides/USER_GUIDE.md)。

## 2. 配置对象

`src.config` 使用冻结 dataclass：

- `EtabsSettings(dll_path, mode="attach")`；
- `GeometrySettings(coordinate_tolerance_mm=1.0, source_span_overlap_tolerance_mm=2.0,
  slope_limit_deg=1.0, merge_angle_limit_deg=15.0, export_unit="N_mm_C")`；
- `DesignSettings(standard="ACI 318-14", clear_cover_to_stirrup_outer_mm=None,
  clear_cover_source="", max_aggregate_size_mm=25.0,
  max_aggregate_size_source="Project default: 25 mm maximum aggregate size")`；
- `OutputSettings(reports_dir, logs_dir, report_filename, log_filename)`；
- `AppConfig(etabs, geometry, design, output, beam_selection_mode, log_level)`。

加载器拒绝 `N_mm_C` 以外的导出单位及 ACI 318-14 以外的设计规范。当前两个运行入口都执行 attach；
配置中的其他 mode 字符串不创建 ETABS 进程。

保护层计算有两种显式口径：配置提供 `clear_cover_to_stirrup_outer_mm/source` 时使用该净保护层；
留空时使用每个截面由 `PropFrame.GetRebarBeam` 返回的 `CoverTop/CoverBot` 纵筋重心 cover。后者直接
控制有效高度，并按候选纵筋/箍筋反算排布净宽。最大骨料未配置时解析为有来源的 25 mm 项目默认值；
显式配置或后续插件选择其他数值时必须同时提供来源，否则配置加载失败。领域 payload 仍要求解析后的
数值和来源完整，避免绕过配置层时静默补值。

## 3. ETABS 连接与几何

### `src.core.etabs_connector`

- `EtabsConnector(dll_path)`
- `attach() -> SapModel`
- `model_filename() -> str`
- `present_units() -> Any`
- `set_present_units(units) -> None`
- `temporary_present_units(target_units)`：上下文管理器，退出时恢复原单位。
- `api_values(result, context) -> tuple[Any, ...]`：检查首个整数返回码并返回其后的 `out/ref` 值。
- `etabs_version_info(sap_model) -> dict[str, Any]`：兼容 ETABS 22 ref 参数和 ETABS 23 无参签名。

### `src.core.geometry_extractor`

- `GeometryExtractor(sap_model).extract(selection_mode="all") -> list[BeamGeometry]`
- `selected_beam_names() -> list[str]`
- `all_beam_names() -> list[str]`
- `column_supports() -> dict[str, Point3D]`
- `expand_rigid_support_points(column_points, rigid_links) -> set[str]`

`selection_mode` 只接受 `all`、`selected`。`column_supports()` 返回真实柱点和通过锚定 `rigid` 链传播的
等效支承及其三维坐标；未锚定 `rigid` 不产生支承。

`BeamGeometry` 字段为 `name, label, story, point_i, point_j, start, end, section, depth,
source_names, path_points`。GUID 不在该几何对象中；写回身份由 `FrameState` 单独保存。

## 4. Beam Line / Span

```python
analyze_beam_lines(
    beams,
    column_point_names=None,
    settings=None,
    column_support_coordinates=None,
) -> BeamLineSpanResult
```

入口位于 `src.core.beam_line_span_analyzer`。`BeamLineSpanSettings` 包含坐标、源 Object 重叠、折角、
穿支承角、交点网格、Z 高差容差和楼层 worker 数。结果包括：

- `BeamLine(story, beam_type, line_number, section, source_objects, path_points, is_curved)`；
- `BeamSpan(story, beam_type, line_number, span_index, source_objects, path_points, is_curved, is_cantilever)`；
- `TopologyDiagnostic(code, severity, story, source_objects, message, candidates)`；
- `BeamLineSpanResult(lines, spans, records, diagnostics)`。

`BeamSpan.span_id` 仍是内部兼容表示，格式含 `-S<index>`。交付编号不得直接使用该属性；CLI 和导出器必须
通过 resolver 产生 [SPEC-DRAW-001](../specs/SPEC-DRAW-001_NUMBERING_INTERCHANGE.md) 定义的不带 `S` 编号。

`src.excel_span_adapter.build_span_schedule(result, beams, designs, tolerance_mm=1.0)` 返回
`SpanScheduleMapping`，把源 Object 设计站点裁剪并映射到结构 Span。

## 5. 写回计划与事务

`src.core.model_writeback` 的主要接口：

- `model_file_hash(path) -> str`；
- `create_working_copy(source_path, output_dir, timestamp=None) -> Path`：不覆盖复制 EDB 和已有 sidecar；
- `plan_orientation(beams, states, ...) -> tuple[OrientationAction, ...]`；
- `plan_rename(result, states, confirmed_multi_section_span_ids=None) -> tuple[RenameAction, ...]`；
- `build_writeback_plan(result, beams, states, ...) -> WritebackPlan`；
- `exclude_writeback_objects(plan, names, ...) -> WritebackPlan`；
- `compare_frame_audits(before, after, ...) -> AuditComparison`；
- `compare_design_envelopes(before, after, ...) -> AuditComparison`。

`EtabsWriteback(sap_model)` 提供 `snapshot()`、`snapshot_from_geometry()`、`snapshot_audit()`、
`apply_orientation()`、`apply_rename()`、`verify_orientation()`、`verify_rename()` 和 `rollback_completed()`。
所有修改方法要求显式 `allow_mutation=True`。`create_working_copy()` 是库函数，`src.cli` 不自动调用它。

## 6. 人工拓扑与多截面审查

`src.core.topology_override`：

- `create_manual_group(...) -> ManualTopologyGroup`；
- `validate_override_collection(groups) -> None`；
- `validate_group_against_current_model(group, objects_by_guid, ...) -> OverrideValidation`；
- `read_manual_overrides(path) -> ManualTopologyOverrides`；
- `write_manual_overrides(overrides, path) -> Path`。

`src.core.section_review`：

- `build_multi_section_span_records(result, beams, confirmed_span_ids=None)`；
- `write_multi_section_span_audit(records, output_dir, stem=...)`；
- `create_etabs_section_review_groups(sap_model, records, allow_mutation=False, colors=None)`；
- `select_etabs_review_group(sap_model, group_name, clear_existing=True)`。

JSON 字段见 [Interchange Schemas](schemas/INTERCHANGE_SCHEMAS.md)。

## 7. 设计结果

- `EtabsResultProvider(sap_model).get_beam_design(name)`：旧 `GetSummaryResultsBeam` 兼容结果。
- `EtabsResultProvider(sap_model).get_beam_design_total(name)`：ETABS 22/23 `_2` Total/Min/Provided 结果。
- `DesignStation`：station、组合、纵筋、剪力、扭转、error/warning 及可选 Total/Min/Provided。
- `BeamDesignResult`：对象级 stations、`DesignResultStatus` 和汇总属性。
- `IDesignProvider`：只要求 `get_beam_design(name) -> BeamDesignResult` 的 Protocol。

字段语义和哨兵值见 [Design Results Reference](api/design-results.md)。

## 8. 导出与工作簿

- `export_structural_spans_dxf/csv()`：一跨一条有向拓扑记录；接受编号 resolver。
- `export_requires_split_dxf()`：阻塞 Object 审计图。
- `export_beam_schedule(...) -> Path`：基础 Excel。
- `build_span_schedule(...) -> SpanScheduleMapping`：Span 到 Excel 的适配。
- `build_rebar_workbook_payload(...) -> dict`：从 ETABS、拓扑和设计结果构建版本化 payload。
- `build_secondary_schedule_payload(payload) -> dict`：从主 payload 构建独立次梁表负载。
- `redetail_payload(payload, ...) -> dict`：不访问 ETABS的离线重新排布。
- `write_rebar_payload(payload, path) -> Path`。
- `run_workbook_builder(...)`、`run_secondary_workbook_builder(...)`：调用 Node artifact-tool
  生成并导出工作簿；主工作簿的命名预览在独立 Node 进程中逐张渲染，瞬态原生进程异常最多重试
  3 次，超过上限即显式失败，不把缺失预览当作成功。

导出层接受领域对象，不应直接读取或修改 ETABS。

## 9. 公开 OAPI SQLite 快照与只读数据源

`src.snapshot.sqlite_snapshot` 的快照接口：

- `PublicOapiTableSource(sap_model).available_tables()`：调用公开 `GetAvailableTables`；
- `read_table(table_key)` / `read_tables(table_keys)`：调用公开
  `GetTableForDisplayArray`，非零返回码、坏字段和行宽不一致均阻断；
- `SnapshotMetadata(...)`：保存源模型路径/哈希、ETABS/API、单位、规范和 schema 身份；
- `write_snapshot(path, metadata, tables)`：拒绝 `.EDB` 和覆盖，使用同目录 `.partial`、
  `PRAGMA integrity_check` 后发布；
- `SnapshotReader(path)`：只读校验 metadata、必需表、字段契约和原始行；
- `build_project_beam_geometry_table(beams)` / `build_project_column_support_table(supports)`：
  以 `.17g` 文本保存公共 `GetAllFrames` 的全精度规范化几何和支座；
- `build_project_design_table(designs)`：全精度保存 `_2` 设计结果；
- `SqliteSnapshotSource(reader)`：回放梁、支座、FrameState、FrameType 和截面/材料；
- `SqliteDesignProvider(reader)`：回放完整 `BeamDesignResult`；
- `export_snapshot_bundle(...)`：发布前后校核源 EDB 哈希，同时校核原始显示表容差和项目表精确往返；
- `src.snapshot_cli.main(...)`：按显式 PID/EDB 身份导出只读 bundle。

`src.model_source` 定义统一的 `ReadModelSource` 协议，并提供：

- `EtabsOapiReadSource`：实时公开 OAPI；
- `SqliteReadSource`：只读 SQLite，使用前校核 snapshot、schema、profile、源 EDB 路径与 SHA-256。

`src.cli --snapshot-input <sqlite>` 通过同一协议运行拓扑、基础 Excel、配筋 payload 和主/SB
工作簿；该分支禁止模型选择、写回、Group 修改、分析和设计。运行开始、工作簿生成前后均复核源与
快照身份。schema 为 `1.0`，原始字段映射为 `raw-oapi-table/1.0`，当前领域映射为
`beam-domain/2.0`。非矩形真实样本、CAD 正式交换、ETABS 23 及 bundle 生命周期仍不在该接口的
当前兼容承诺内。

## 10. Run Manifest

`src.run_manifest` 提供 `create_run_id()`、`sha256_file()`、`artifact_record()`、`git_state()`、
`fingerprint()` 和 `RunManifest`。`RunManifest.finalize(status, summary=None, error=None)` 写入输出目录及注册目录，
并按输入指纹维护有效性。dirty worktree 的 content fingerprint 同时覆盖 tracked binary diff 和未跟踪文件
内容清单；manifest 的注册状态不是工程验收 verdict，解释规则见文档治理纲领。
