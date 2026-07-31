---
id: REF-REPO-001
type: reference
status: current
owner: project
created: 2026-07-25
updated: 2026-07-30
related:
  - GOV-001
  - ARCH-001
  - REF-DOCS-INDEX
supersedes:
  - LEGACY-PROJECT-STRUCTURE
superseded_by:
---

# Repository Layout

本页说明当前仓库中源码、测试、受治理文档和生成物的归属。组件之间的数据流见
[ARCH-001](../architecture/ARCH-001_SYSTEM_ARCHITECTURE.md)。

```text
.
|-- src/
|   |-- core/          # ETABS 适配、几何、拓扑、override、审查、写回
|   |-- design/        # 设计结果领域模型和配筋策略
|   |-- drawing/       # DXF/CSV 生成
|   |-- spreadsheet/   # Node 工作簿生成器
|   |-- utils/         # 日志、IO 和纯几何工具
|   |-- cli.py         # 完整 PID/门禁/审计/写回入口
|   |-- workflow.py    # 简化活动实例基础 Excel 入口
|   |-- excel_export.py
|   |-- excel_span_adapter.py
|   |-- rebar_workbook.py
|   `-- run_manifest.py
|-- tests/
|   |-- unit/
|   `-- integration/
|-- plugins/
|   `-- etabs/         # x64 .NET 8 薄交互插件
|-- docs/
|   |-- governance/
|   |-- specs/
|   |-- architecture/
|   |-- decisions/
|   |-- delivery/
|   |-- state/
|   |-- guides/
|   |-- reference/
|   `-- archive/
|-- tools/             # 受跟踪的辅助工具
|-- config.example.yaml
|-- requirements.txt
|-- pyproject.toml
|-- package.json
|-- build/             # 生成报告、模型副本和运行产物
`-- outputs/           # 其他生成输出
```

## 源码责任

| 路径 | 所有权边界 |
|---|---|
| `src/core/etabs_connector.py` | DLL 加载、活动实例附着、版本和临时单位。 |
| `src/core/geometry_extractor.py` | 批量 Frame 快照、混凝土梁筛选、真实柱和锚定刚域支承。 |
| `src/core/beam_line_span_analyzer.py` | 不访问 ETABS 的 Beam Line/Span 领域算法。 |
| `src/core/topology_override.py` | GUID 人工拓扑 JSON 和纯验证。 |
| `src/core/section_review.py` | 多截面审计和 ETABS Group 交互。 |
| `src/core/model_writeback.py` | 工作副本帮助函数、计划、执行、验证、审计和回滚。 |
| `src/design/etabs_result.py` | OAPI 设计数组到领域模型的适配。 |
| `src/design/detailing.py` | 配筋排布策略；不负责 ETABS 附着或文件导出。 |
| `src/drawing/dxf_export.py` | 消费有向几何；不读取或修改模型。 |
| `src/excel_span_adapter.py` | Object 设计站点到结构 Span 的投影。 |
| `src/rebar_workbook.py` | JSON payload 和 Node 生成器编排。 |
| `src/cli.py` | 完整操作入口；业务规则仍归属相应领域模块。 |
| `plugins/etabs/BeamGenerator.EtabsPlugin/` | ETABS 进程内只读交互、选择 JSON 和 Python 只读 runner。 |
| `tools/build_etabs_plugin.ps1` | 不覆盖的 ETABS 插件发布、合同校验、manifest 和 ZIP。 |
| `tools/EtabsPluginValidator/` | 不依赖运行中 ETABS 的程序集合同/x64 打包校验。 |
| `tools/EtabsPluginSmokeHost/` | 使用真实 `SapModel` 的窗口、选择导出、callback 和 Python runner smoke。 |

## 测试归属

`tests/unit/` 不需要 ETABS，覆盖纯算法、适配边界、schema、CLI 门禁和写回事务替身。
`tests/integration/` 需要已打开 ETABS，并由 `ETABS_INTEGRATION=1` 显式启用。测试命令见
[开发指南](../guides/DEVELOPMENT.md)。

## 文档归属

- `docs/INDEX.md` 是唯一文档入口。
- `governance/` 定义权威、生命周期、证据和迁移规则。
- `specs/` 定义目标行为和验收；`architecture/` 定义系统组织；`decisions/` 记录取舍。
- `delivery/` 保存 Roadmap、实施计划和任务；`state/` 保存 Baseline、Review 和 Validation。
- `guides/` 只说明如何操作；`reference/` 只保存稳定接口、schema 和术语。
- 根部旧文档路径在迁移周期内是短迁移页，不是当前权威来源。

## 配置和依赖

- `config.example.yaml`：可提交的配置键和默认值；本机 `config.yaml` 不应包含可移植性假设。
- `requirements.txt`：Python 运行、导出、测试和开发依赖。
- `pyproject.toml`、`.flake8`：Black、pytest、coverage 和 Flake8 配置。
- `package.json`：工作簿生成所需的 Node 依赖和脚本。
- `plugins/etabs/**/*.csproj`：.NET 8 x64 插件；构建时显式传入所选 ETABS 安装目录的 API DLL。

## 生成与非权威区域

`build/`、`outputs/`、`.pytest_cache/`、coverage 文件、模型副本、视频、帧图和本机缓存可以承载原始证据，
但不能成为唯一生产实现或当前文档权威。可接受的结论由 `docs/state/validation/` 中的小型记录引用精确
manifest、路径和哈希；目录中“较新”的文件不自动覆盖旧证据。
