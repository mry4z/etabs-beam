# 当前 API 与稳定性边界

**更新日期**：2026-07-22
**当前宿主基线**：Python 3.11、ETABS 22.7.0 / 23.3.1、ACI 318-14

本页区分“正式基础入口”“已验证原型”和“待生产化入口”。ETABS OAPI 的实测调用签名、权限和版本差异见 [ETABS_OAPI_VALIDATION.md](ETABS_OAPI_VALIDATION.md)。项目整体优先级和验收条件见 [PROJECT_COMPLETION_CHECKLIST.md](../PROJECT_COMPLETION_CHECKLIST.md)。

## 1. 正式基础入口

| 组件 | 主要入口 | 作用 |
|---|---|---|
| `EtabsConnector` | `attach()` | 附着到 ETABS 并返回 `SapModel`；当前正式类尚未暴露 PID/目标路径参数 |
| `EtabsConnector` | `temporary_present_units()` | 临时切换 ETABS 当前单位并在退出时恢复 |
| `GeometryExtractor` | `extract("all" | "selected")` | 返回筛选后的混凝土 `BeamGeometry` 列表 |
| `GeometryExtractor` | `column_supports()` | 通过批量 Frame 快照返回真实柱及锚定 `rigid` 等效支承的点名→三维坐标 |
| `analyze_beam_lines` | `analyze_beam_lines(beams, column_point_names, settings, column_support_coordinates=...)` | 生成 `BeamLineSpanResult`、固定点 PB/SB 拓扑和 fail-closed 诊断 |
| `build_span_schedule` | `build_span_schedule(result, beams, designs)` | 把源 Object 设计站点裁剪并映射到结构 Span |
| `ContinuityAnalyzer` | `analyze(beams)` | 返回旧梁表兼容用的 `ContinuityGroup` |
| `EtabsResultProvider` | `get_beam_design(name)` | 读取旧 `GetSummaryResultsBeam` 数据 |
| `EtabsResultProvider` | `get_beam_design_total(name)` | 读取 ETABS 22/23 `GetSummaryResultsBeam_2` Total/Min/Provided 数据 |
| `export_beam_schedule()` | 几何、连续性、设计结果、可选 Span 映射 | 写入基础 Excel 梁表 |
| `build_rebar_workbook_payload()` | ETABS 模型、结构分跨、几何和设计结果 | 构建版本化三表负载；混合截面/Frame Type 默认 fail-closed |
| `redetail_payload()` | 已保存三表负载、可选配筋策略 | 离线重新排布配筋，不重新访问 ETABS |
| `run_workbook_builder()` | 三表负载、输出路径、Node/artifact-tool 运行时 | 生成三表 `.xlsx`、检查公式并渲染 QA 预览 |
| `RunManifest` | CLI 参数、配置、运行汇总和成果路径 | 生成 run manifest、哈希成果并维护 CURRENT/SUPERSEDED/INVALID 注册表 |

## 2. 已验证但尚未完全生产化的原型

| 组件 | 入口 | 当前边界 |
|---|---|---|
| `EtabsWriteback` | `snapshot()` / `snapshot_from_geometry()`、`apply_orientation()`、`apply_rename()`、`verify_orientation()` | 已在模型副本验证 I/J 反向和两阶段改名；批量几何快照仅补读 GUID；尚未完成全属性快照、幂等与全部失败路径 |
| `build_writeback_plan` | `build_writeback_plan(result, beams, states)` | 按 GUID、Beam Line/Span 生成计划；跨 Span Object 标记 `REQUIRES_SPLIT` |
| `create_working_copy` | `create_working_copy(source_path, output_dir)` | 创建不覆盖原文件的 EDB 副本并复制可用 sidecar |
| `BeamDetailingEngine` | `detail(input)` | 生成左/中/右纵筋、箍筋和 face rebar；相邻跨 option 和异常处置仍待工程闭环 |
| `coordinate_adjacent_spans` | 相邻两跨结果、连续性 option | 纯函数原型；尚未在整根 Beam Line 正式迭代协调 |
| DXF/CSV exporter | `export_structural_spans_dxf/csv()` | 用于分跨图形复核；正式 CAD 文字必须改为严格 I→J，不得可读性翻转 |
| `topology_override` | `create_manual_group()`、`validate_group_against_current_model()`、`read/write_manual_overrides()` | GUID 人工 Line/Span Group 数据契约、I→J 排序、支承冲突二级确认和失效检测；插件 UI 尚未实现 |
| `section_review` | `build/write_multi_section_span_audit()`、`create_etabs_section_review_groups()`、`select_etabs_review_group()` | 多截面 Span 审计、ETABS 彩色 Group 镜像及选中高亮；创建 Group 必须显式允许模型变更 |
| `build_writeback_plan` | `confirmed_multi_section_span_ids=...` | 未确认多截面 Span 的方向/编号均为 `REQUIRES_SECTION_REVIEW`；确认后才产生可执行动作 |

## 3. 正式工作流的当前缺口

`workflow.run()` 仍是早期只读 Excel 工作流；P0 正式入口为 `python -m src.cli`，已经受 Git 跟踪并支持 PID/模型校验、只读拓扑、DXF/CSV/JSON 审计、方向/编号写回门禁、分析/设计、基础 Excel 和三表配筋 Excel。当前缺口为：

- 把工作副本创建整合成 CLI 显式检查点；
- 全属性写前/写后比较、幂等和失败回滚；
- ETABS 内人工 Line/Span Group 选择、局部重排预览和第二次写回 UI。

多截面审查已有非 UI 入口：`--prepare-section-review-groups` 创建并保存 `CODX_MS_*` Group，`--select-section-review-group GROUP` 选中/高亮一个组，`--confirmed-multi-section-spans file.json` 把用户确认的 step beam SpanID 输入写回计划。确认文件格式为字符串数组，或 `{"confirmed_span_ids": [...]}`。这不替代 P2 的交互界面。

后续正式化仍须保留只读、写回和允许部分写回的显式边界。不得把被 `.gitignore` 忽略的历史验证脚本当作公开 API。

`--export-rebar-workbook` 启用正式三表输出；Node 路径、`node_modules` 和堆上限分别由 `--artifact-node`、`--artifact-node-modules`、`--artifact-node-max-old-space-mb` 指定，默认堆上限为 8192 MB。写回模式下若同时导出 Excel，CLI 强制要求 `--run-analysis --run-design`，并在写回、分析和设计完成后重新读取当前 Object 名称、几何、拓扑和设计结果，避免导出改名前名称或旧设计缓存。

每次 CLI 运行自动写入唯一 `run_manifest_<run-id>.json`，并在输出目录同级 `manifests/` 维护注册表。模型副本运行使用 `--source-model` 指向未修改源 EDB；`--manifest-registry` 可显式指定跨运行注册目录。成果有效性由模型、配置、Git/算法、规范、ETABS/API 和关键选项组成的 input fingerprint 判断，不按文件时间判断。

## 4. 核心数据模型

- `BeamGeometry`：Object 名、标签、楼层、节点、坐标、截面、深度和路径。
- `BeamLine`：完整绘图梁线、PB/SB 角色、编号、源 Object 和有向路径。
- `BeamSpan`：结构跨、路径、源 Object、悬挑和弧形状态。
- `TopologyDiagnostic`：诊断代码、严重度、楼层、源 Object、候选和说明；`BLOCKED` 直接参与写回门禁。
- `ManualTopologyGroup/Overrides`：GUID 驱动的人工 Line/Span Group、顺序、I→J、支承冲突和模型指纹。
- `MultiSectionSpanRecord`：跨内截面、截面→Object 映射、路径、审查 Group 和 `REQUIRES_SECTION_REVIEW` 状态。
- `DesignStation`：ETABS 站点、控制组合、剪扭需求及 Total/Min/Provided 可选字段。
- `BeamDesignResult`：一根源 Object 或结构 Span 的全部设计站点和状态。
- `BeamDetailingInput/Result`：配筋排布引擎输入、模式、左中右结果、face rebar 和异常原因。

## 5. 编号与单位契约

- 正式梁跨编号为 `F1-PB5-3`、`RF-SB10-1`，跨号不含 `S`。
- ETABS Object 最终名称为 `F1-PB5-3.1`、`F1-PB5-3.2`。
- 当前 `BeamSpan.span_id` 内部仍带 `S`，不同导出器通过 resolver 去除；P1 必须收敛为唯一 formatter/policy，防止 Excel、DXF、JSON 和写回格式漂移。
- 当前公开数据只保证在 `N_mm_C` 上下文中读取和输出；调用完成后必须恢复 ETABS 原始当前单位。
- 正式 CAD/JSON 的有向文字和左/中/右定义必须与模型 I→J 一致。
