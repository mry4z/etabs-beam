# ETABS Beam Generator — 项目结构与文件管理规范

**更新日期**：2026-07-16
**项目根目录**：`F:\2026\Research Study\etabs beam\`  
**当前状态**：MVP 已实现并完成 `bm.EDB` 真实模型验证

本文件描述当前实际目录、文件职责和后续新增文件的归属。`已实现`、`预留`与`计划`必须明确区分，避免将结构规划误当成功能完成。

---

## 1. 当前目录结构

```text
etabs beam/
├── src/                                      # Python 包：MVP 实现
│   ├── __init__.py
│   ├── config.py                              # 已实现：YAML 配置与路径解析
│   ├── workflow.py                            # 已实现：CLI 工作流编排
│   ├── excel_export.py                        # 已实现：兼容梁表、设计站点、梁分跨、说明和异常记录导出
│   ├── excel_span_adapter.py                  # 已实现：结构分跨到 Excel 的可替换适配层
│   ├── core/
│   │   ├── __init__.py
│   │   ├── etabs_connector.py                 # 已实现：附着、API 错误处理和临时单位恢复
│   │   ├── geometry_extractor.py              # 已实现：梁识别与几何提取
│   │   ├── beam_merger.py                     # 已实现：同截面过度分割梁段合并与特殊情况记录
│   │   ├── merge_comparison.py                 # 已实现：不同归并夹角的逻辑梁差异比较
│   │   ├── beam_line_span_analyzer.py          # 初版：梁线识别、支承分跨与 PB/SB 编号
│   │   └── continuity_analyzer.py             # 已实现：连续梁分组
│   ├── drawing/
│   │   ├── __init__.py
│   │   └── dxf_export.py                       # 已实现：归并差异及结构分跨 CAD/DXF 图形化复核
│   ├── design/
│   │   ├── __init__.py
│   │   ├── provider.py                        # 已实现：统一设计结果数据模型/协议
│   │   ├── etabs_result.py                    # 已实现：ETABS 混凝土设计摘要读取
│   │   ├── key_positions.py                   # 已实现：左中右（2D/跨中/2D）控制配筋选择
│   │   └── aci318.py                          # 预留：自研 ACI 318 接口，未实现
│   └── utils/
│       ├── __init__.py
│       ├── geometry.py                        # 已实现：距离、平面夹角等纯几何工具
│       ├── io.py                              # 已实现：防覆盖输出路径
│       └── logger.py                          # 已实现：文件与控制台日志配置
│
├── tests/
│   ├── __init__.py
│   ├── unit/
│   │   ├── test_geometry.py                   # 已实现：几何工具测试
│   │   ├── test_continuity.py                 # 已实现：连续梁判据测试
│   │   ├── test_excel_export.py               # 已实现：Excel 文件可读性测试
│   │   └── test_excel_span_adapter.py         # 已实现：分跨映射与梁分跨工作表测试
│   │   └── test_connector.py                  # 已实现：临时单位恢复测试
│   └── integration/
│       ├── __init__.py
│       └── test_workflow.py                   # 已实现：显式 opt-in 的 ETABS 集成测试
│
├── docs/
│   ├── TECHNICAL_ROADMAP.md                   # 原始技术路线
│   ├── ETABS_OAPI_VALIDATION.md               # 已实现：实测 API 用法与验证证据
│   ├── DEVELOPMENT.md                          # 已实现：开发与质量检查说明
│   ├── USER_GUIDE.md                           # 已实现：用户操作说明
│   ├── DRAWING_NUMBERING_INTERCHANGE.md        # 远期：逻辑梁编号、局部坐标与 CAD 中间数据规范
│   ├── BEAM_LINE_SPAN_RULES.md                  # 已确认：梁线、主次梁支承与结构分跨可执行规范
│   ├── API.md                                  # 已实现：当前代码 API 概览
│   └── api/design_results.md                   # 已实现：设计摘要字段映射
│
├── assets/
│   └── templates/                             # 预留：Excel 模板；当前导出不依赖模板
│
├── build/output/
│   ├── reports/                               # 生成的 .xlsx 梁表（不入库）
│   ├── drawing_data/                           # 归并差异/结构分跨 DXF 与 CAD 中间数据（不入库）
│   ├── model_archives/                        # 远期：ETABS SQLite 数据本地只读归档（不入库）
│   └── logs/                                  # 工作流日志（不入库）
│
├── extracted_frames/                          # 视频逆向资料，仅作本地研究参考，不入库
├── codex生成并失败/                           # 早期帧提取试验，归档资料
├── config.example.yaml                        # 可提交的配置示例
├── requirements.txt                           # Python 依赖声明
├── README.md                                  # 项目入口
├── PROJECT_STRUCTURE.md                       # 本文件
├── PROJECT_COMPLETION_CHECKLIST.md            # 当前进度与待办
└── PROCESS_CONTROL_REQUIREMENTS.md            # 历史需求管控与复核记录
```

以下内容在旧规划中出现、但当前尚不存在或不应被视为已完成：`.github/workflows/`、`setup.py`、Excel 模板文件和可提交的脱敏 ETABS 样本模型。`pyproject.toml`、文档体系和本机 opt-in 自动化集成测试均已实现。

---

## 2. 模块职责与数据流

```text
config
  ↓
EtabsConnector ──→ GeometryExtractor ──→ ContinuityAnalyzer
  ↓                       ↓                       ↓
SapModel          BeamGeometry[] → BeamMerger → 逻辑梁[]
                          ↓                       ↓
                    原始设计结果           ContinuityGroup[]
                          ↓
                 BeamLineSpanAnalyzer → SpanScheduleAdapter
  ↓
EtabsResultProvider ──→ BeamDesignResult[] ──→ ExcelExport
                                                    ↓
                                      build/output/reports/*.xlsx
```

| 模块 | 输入 | 输出 | 状态 |
|---|---|---|---|
| `EtabsConnector` | DLL 路径 | `SapModel`、当前模型路径、临时单位上下文 | 已实现 |
| `GeometryExtractor` | `SapModel`、选择模式 | `BeamGeometry` 列表 | 已实现 |
| `ContinuityAnalyzer` | `BeamGeometry` 列表 | `ContinuityGroup` 列表 | 已实现 |
| `EtabsResultProvider` | `SapModel`、梁对象名 | `BeamDesignResult` | 已实现 |
| `key_positions` | 梁几何、设计站点 | 左/中/右弯矩筋与抗剪筋控制值 | 已实现 |
| `BeamLineSpanAnalyzer` | 原始梁几何、柱支承点 | `BeamLineSpanResult` | 已实现：工作流已接入 |
| `SpanScheduleAdapter` | `BeamLineSpanResult`、源设计结果 | `SpanScheduleMapping` | 已实现：编号规则可替换 |
| `ExcelExport` | 几何、连续性、设计结果、可选分跨映射 | `.xlsx` | 已实现 |
| `ACI318Provider` | 待定义 | 自研设计结果 | 远景 |

### 关键数据模型

- `BeamGeometry`：梁对象名、标签、楼层、两端节点、全局坐标、截面和长度。
- `ContinuityGroup`：连续梁组编号、楼层和有序梁成员。
- `DesignStation`：单个设计站点的纵筋、剪力筋、扭转筋、控制组合及 ETABS 错误/警告文本。
- `BeamDesignResult`：一根梁的全部 `DesignStation`，并提供最大顶筋、底筋、剪力筋等汇总属性。

导出层只消费上述数据模型，不应直接访问 ETABS API。

---

## 3. 文件命名与生命周期

### Python 与测试

| 类型 | 规则 | 示例 |
|---|---|---|
| Python 模块 | `snake_case.py` | `geometry_extractor.py` |
| 类 | `PascalCase` | `EtabsConnector` |
| 函数/方法 | `snake_case()` | `get_beam_design()` |
| 常量 | `UPPER_SNAKE_CASE` | `PROJECT_ROOT` |
| 测试模块 | `test_<module>.py` | `test_continuity.py` |

纯 Python 几何、数据转换和 Excel 格式逻辑必须有单元测试。需要已打开 ETABS 的测试放入 `tests/integration/`，并显式跳过无 ETABS 环境；当前通过 `ETABS_INTEGRATION=1` 明确启用。

### 文档

| 类型 | 命名 | 位置 |
|---|---|---|
| 项目入口 | `README.md` | 根目录 |
| 结构、路线、状态类技术文档 | `UPPER_SNAKE_CASE.md` | 根目录或 `docs/` |
| 实测记录 | `ETABS_OAPI_VALIDATION.md` | `docs/` |
| 细粒度 API 参考 | `lowercase_noun.md` | `docs/api/` |
| 用户与开发手册 | `USER_GUIDE.md`、`DEVELOPMENT.md` | `docs/` |

对 ETABS OAPI 调用、DLL/版本、pythonnet 参数规则和验证结果的变更，必须同步更新 [docs/ETABS_OAPI_VALIDATION.md](docs/ETABS_OAPI_VALIDATION.md)。

### 输出与临时文件

| 文件 | 位置 | 是否入库 |
|---|---|---|
| 梁表 | `build/output/reports/beam_schedule_<timestamp>.xlsx` | 否 |
| 图纸中间数据（远期） | `build/output/drawing_data/` | 否 |
| 模型数据归档（远期） | `build/output/model_archives/` | 否 |
| 运行日志 | `build/output/logs/etabs_beam_<timestamp>.log` | 否 |
| 本机配置 | `config.yaml` 或 `config.local.yaml` | 否 |
| 配置示例 | `config.example.yaml` | 是 |

---

## 4. 配置管理

`src/config.py` 读取 `config.yaml`；若文件不存在，自动回退至 `config.example.yaml`。当前已读取的配置包括：

- `etabs.dll_path`、`mode`
- `geometry.coordinate_tolerance_mm`、`slope_limit_deg`；导出单位固定为 `N_mm_C`，由工作流记录并恢复原 ETABS 当前单位
- `output.reports_dir`、`logs_dir`、文件名模式
- `logging.level`
- `advanced.beam_selection_mode`

`config.example.yaml` 中仍保留部分尚未接入 MVP 的字段（Excel 模板、自动运行设计、支座反力等）。新增功能前应先决定实现该字段、删除它，或明确标为远景，不能让配置与代码行为不一致。

当前通过 `python -m src.workflow` 运行，支持：

```bash
# 所有被判为 Beam 的 Frame 对象
python -m src.workflow --selection all

# 仅当前 ETABS 选择集中的梁
python -m src.workflow --selection selected
```

ETABS、Python 和终端权限级别须一致；多 ETABS 实例时，必须先核对日志输出的实际模型路径。详细规则见 [docs/ETABS_OAPI_VALIDATION.md](docs/ETABS_OAPI_VALIDATION.md)。

---

## 5. 版本管理与生成文件

Git 仓库已于 2026-07-12 初始化，当前分支为 `master`，尚未创建首个提交。`.gitignore` 已覆盖虚拟环境、Python 缓存、`config.yaml`、视频/帧图、`build/output/` 生成的报告和日志，以及 `extracted_frames/`、早期提帧脚本、旧 README 草稿和历史截图；创建基线提交前仍应复核待跟踪清单。

初始化后采用 Conventional Commits：

```text
feat(connector): attach to running ETABS instance
fix(design): pass pythonnet out parameters to summary results API
test(continuity): cover non-collinear shared endpoint
docs(oapi): record validated pythonnet call signatures
```

不应提交 EDB 模型、原始视频、自动提取帧或运行导出的 Excel；若未来需要受控集成测试样本，应采用小型脱敏模型并单独明确版本管理策略。

---

## 6. 新增文件的管理流程

1. 先确定它属于现有 MVP、测试、文档或远景扩展；
2. 将生产代码放入 `src/` 的责任模块，避免在 `workflow.py` 堆积业务逻辑；
3. 为独立逻辑添加 `tests/unit/test_<module>.py`；
4. 若涉及 ETABS API，补充或更新 `docs/ETABS_OAPI_VALIDATION.md` 的签名、前置条件和实测结果；
5. 若引入生成物，放入 `build/output/` 或显式登记其版本管理规则；
6. 更新 [PROJECT_COMPLETION_CHECKLIST.md](PROJECT_COMPLETION_CHECKLIST.md) 的状态和验收证据。

---

## 7. 结构核对清单

- [x] 核心 MVP 模块与工作流已实现
- [x] 单元测试已覆盖几何、连续性和 Excel 导出基础逻辑
- [x] 真实 ETABS 模型已完成手工端到端验证
- [x] OAPI 实测记录已建立
- [x] 自动化 ETABS 集成测试（本机 opt-in）
- [x] 固定 `N_mm_C` 导出与原单位恢复
- [ ] 可提交的脱敏集成测试样本模型
- [ ] `utils/io.py`、`utils/logger.py` 的实现或清理
- [ ] 用户手册、开发者手册与完整 API 参考
- [x] Git 仓库初始化
- [x] 首个 Git 基线提交
- [ ] CI/CD
- [ ] ACI/GB 规范扩展、SAP2000、GUI、打包
