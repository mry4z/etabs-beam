# ETABS Beam Generator — 项目结构与文件管理规范

**更新日期**：2026-07-22
**项目根目录**：`F:\2026\Research Study\etabs beam\`
**当前状态**：P0-1/P0-2/P0-3 已关闭；Beam Line/Span、模型副本写回、ETABS 设计结果、DXF、ACI 318-14 配筋引擎、三表 Excel 和成果 manifest 均已接入正式 CLI。下一阶段为写回事务强化和最新模型重放。

本文件描述当前真实目录、模块职责、生成物生命周期和后续文件归属。文中的“已实现”“已验证原型”“待生产化”“预留”含义不同，不得把本机运行脚本或一次性输出误认为稳定公共接口。

## 1. 当前目录结构

```text
etabs beam/
├── src/                                      # 应提交的正式 Python 包
│   ├── __init__.py
│   ├── cli.py                                 # 正式 ETABS 22/23 CLI：只读、门禁写回、分析/设计、DXF 和 Excel 检查点
│   ├── config.py                              # 已实现：YAML 配置、路径与分跨参数
│   ├── workflow.py                            # 已实现：当前只读 CLI 编排
│   ├── excel_export.py                        # 已实现：基础梁表、站点、分跨和异常表输出
│   ├── excel_span_adapter.py                  # 已实现：结构分跨到 Excel 的稳定适配层
│   ├── rebar_workbook.py                      # 已实现：正式三表负载、redetail 和 artifact-tool 编排
│   ├── run_manifest.py                        # 已实现：run ID、哈希、注册表和成果失效
│   ├── spreadsheet/
│   │   └── build_rebar_workbook.mjs           # 已实现：三表公式、格式、QA 渲染和 xlsx 导出
│   ├── core/
│   │   ├── __init__.py
│   │   ├── etabs_connector.py                 # 已实现：pythonnet 附着、API 错误和临时单位恢复
│   │   ├── geometry_extractor.py              # 已实现：梁/柱、坐标、截面和选择集读取
│   │   ├── continuity_analyzer.py             # 已实现：传统连续梁分组
│   │   ├── beam_merger.py                     # 已实现：过度分割梁段归并与异常记录
│   │   ├── merge_comparison.py                # 已实现：不同夹角规则差异比较
│   │   ├── beam_line_span_analyzer.py          # 已实现：PB/SB、内部支承、固定点收敛和 fail-closed 诊断
│   │   ├── model_writeback.py                 # 已实现原型：计划、I/J 反向、两阶段改名和写后验证
│   │   ├── section_review.py                  # 已实现：多截面 Span 清单、ETABS 彩色 Group 和高亮选择
│   │   ├── topology_audit.py                  # 已实现：REQUIRES_SPLIT JSON/CSV/DXF 审计
│   │   └── topology_override.py               # 已实现：GUID 人工 Line/Span Group 数据契约和校验
│   ├── design/
│   │   ├── __init__.py
│   │   ├── provider.py                        # 已实现：统一设计结果数据模型/协议
│   │   ├── etabs_result.py                    # 已实现：ETABS 混凝土梁摘要读取
│   │   ├── key_positions.py                   # 已实现：左/中/右关键位置控制值
│   │   ├── detailing.py                       # 原型：ACI 318-14 + 项目策略配筋排布引擎
│   │   └── aci318.py                          # 预留：其他规范版本 provider
│   ├── drawing/
│   │   ├── __init__.py
│   │   └── dxf_export.py                       # 已实现：差异和完整分跨 DXF/CSV 输出
│   └── utils/
│       ├── __init__.py
│       ├── geometry.py                        # 已实现：距离、夹角等纯几何函数
│       ├── io.py                              # 已实现：输出防覆盖路径
│       └── logger.py                          # 已实现：控制台/文件日志配置
│
├── tests/
│   ├── __init__.py
│   ├── unit/                                  # 20 个单元测试模块
│   │   ├── test_beam_line_span_analyzer.py
│   │   ├── test_beam_merger.py
│   │   ├── test_cli.py
│   │   ├── test_config_and_io.py
│   │   ├── test_connector.py
│   │   ├── test_continuity.py
│   │   ├── test_detailing.py
│   │   ├── test_dxf_export.py
│   │   ├── test_excel_export.py
│   │   ├── test_excel_span_adapter.py
│   │   ├── test_geometry.py
│   │   ├── test_key_positions.py
│   │   ├── test_merge_comparison.py
│   │   ├── test_model_writeback.py
│   │   ├── test_rebar_workbook.py
│   │   ├── test_section_review.py
│   │   ├── test_topology_audit.py
│   │   ├── test_topology_override.py
│   │   ├── test_topology_regressions.py
│   │   └── __init__.py
│   └── integration/
│       ├── __init__.py
│       └── test_workflow.py                   # 已实现：ETABS 显式 opt-in 集成测试
│
├── docs/
│   ├── API.md
│   ├── BEAM_LINE_SPAN_RULES.md                 # 梁线、PB/SB、支承和分跨可执行规则
│   ├── DEVELOPMENT.md                          # 开发原则与质量检查
│   ├── DRAWING_NUMBERING_INTERCHANGE.md        # 编号、I/J 与 CAD 交换契约
│   ├── ETABS_OAPI_VALIDATION.md                # API 签名、权限和真实模型实测记录
│   ├── ETABS_SQLITE_SNAPSHOT_ROUTE.md          # ETABS 23 SQLite 快照路线
│   ├── MANUAL_BEAM_TOPOLOGY_OVERRIDE.md        # 人工 Beam Line/Span 分组和 ETABS 插件交互契约
│   ├── ORDINARY_BEAM_REBAR_DETAILING_ACI318_14_REVIEW.md # SB 普通梁与 PB/SMF 规则对照
│   ├── PRIMARY_BEAM_REBAR_DETAILING_ACI318_14_REVIEW.md
│   ├── TECHNICAL_ROADMAP.md
│   ├── USER_GUIDE.md
│   └── api/design_results.md
│
├── build/                                     # 整目录被 .gitignore 忽略
│   ├── output/
│   │   ├── drawing_data/                      # DXF/CSV
│   │   ├── logs/                              # 运行日志
│   │   ├── model_writeback/                   # EDB 工作副本
│   │   └── reports/                           # 审计 JSON、数据提取和本机运行脚本
│   ├── spreadsheet_analysis/                  # 工作簿检查临时数据
│   └── spreadsheet_work/                      # 历史三表验证脚本和中间数据；非运行依赖
│
├── outputs/                                   # 当前未忽略的评审成果，生命周期待收敛
│   ├── rebar_v641_writeback_20260718_004759/
│   ├── rebar_v643_20260717/
│   └── rebar_v643_20260718_practical_layers/
│
├── assets/templates/                          # 预留；当前输出不依赖模板
├── config.example.yaml                        # 可提交配置示例
├── requirements.txt                           # 依赖声明；openpyxl 已约束为 >=3.1.0,<4
├── package.json                               # artifact-tool 正式生成器依赖和命令声明
├── pyproject.toml                             # Black/Pytest 配置，目标 Python 3.11
├── README.md                                  # 项目入口与当前状态
├── PROJECT_STRUCTURE.md                       # 本文件
├── PROJECT_COMPLETION_CHECKLIST.md            # 进度、优先级和验收证据
└── PROCESS_CONTROL_REQUIREMENTS.md             # 历史需求管控与复核记录
```

`extracted_frames/`、视频、早期提帧脚本和历史资料不纳入当前项目交付。旧规划中出现但当前不存在的 `.github/workflows/`、`setup.py`、正式 Excel 模板和脱敏 EDB 测试模型，不得写成已完成。

## 2. 两条当前数据流

### 2.1 正式 `src/` 只读入口

```text
config
  ↓
EtabsConnector ─→ GeometryExtractor ─→ 原始 BeamGeometry / 柱支承点
                              ├──────→ EtabsResultProvider ─→ BeamDesignResult
                              ├──────→ BeamLineSpanAnalyzer ─→ BeamLineSpanResult
                              │                                  ↓
                              │                           SpanScheduleAdapter
                              └──────→ BeamMerger ─→ ContinuityAnalyzer
                                                                   ↓
                                                             ExcelExport
```

`python -m src.workflow` 负责只读基础梁表；`python -m src.cli` 是 P0 正式入口，按 PID/模型路径校验，默认只读，并提供写回门禁、分析/设计、拓扑审计和统一梁表 Excel 检查点。

### 2.2 已验证但待生产化的完整链路

```text
原始 EDB
  ↓ create_working_copy + SHA-256
工作副本
  ↓ Beam Line/Span + GUID 映射
orientation plan ─→ ChangeConnectivity ─→ I/J 验证
rename plan      ─→ 临时名 ─→ 最终 Unique Name ─→ GUID/连接关系验证
  ↓
ETABS 分析 + ACI 318-14 设计
  ├─→ DXF/CSV 图形复核
  └─→ 设计站点/AsTotal/Al/Av/s/At/s
          ↓
    BeamDetailingEngine
          ↓
原始分跨数据 + 配筋调整复核 + 整理梁表（三表 Excel）
```

这条链路已在 ETABS 22/23 真实模型副本上通过 P0 CLI 跑通。配筋细节三表的数据适配、redetail 和 artifact-tool 生成器已迁入正式 `src/`；`build/` 中旧脚本只保留历史证据，不再是运行依赖。

## 3. 模块职责和成熟度

| 模块 | 输入 | 输出 | 当前状态 |
|---|---|---|---|
| `EtabsConnector` | ETABS 22/23 DLL/活动实例 | `SapModel`、版本能力、模型路径、单位上下文 | 已实现并实测 |
| `GeometryExtractor` | `SapModel`、选择模式 | 梁几何、柱/刚域等效支承和截面数据 | 已实现；`GetAllFrames` 批量快照实测 |
| `BeamLineSpanAnalyzer` | 原始梁、柱坐标、容差/角度规则 | Beam Line、Span、PB/SB、源 Object 映射和诊断 | P0-1 已冻结；内部支承、固定点收敛、支承边界门禁已实测 |
| `SpanScheduleAdapter` | 分跨结果、源几何和设计站点 | 分跨级 Excel 数据 | 已实现，隔离编号算法与 Excel 布局 |
| `BeamMerger` / `ContinuityAnalyzer` | 梁几何和设计结果 | 传统逻辑梁/连续梁组 | 已实现，保留兼容输出 |
| `EtabsResultProvider` | `SapModel`、Object 名 | `BeamDesignResult` | 已实现；支持 ETABS 22/23 `_2` 扩展字段 |
| `model_writeback` | 分跨结果、Frame GUID/连接快照 | I/J 与改名计划、写后校验 | 第一版已实现；只允许模型副本 |
| `section_review` | 分跨结果、源截面、人工确认 SpanID | 多截面审查 JSON/CSV、ETABS Group、写回门禁 | 已实现非 UI 入口；插件交互留在 P2 |
| `topology_audit` | 分跨结果、写回计划 | 阻塞 Object JSON/CSV/DXF | 已实现；数量按模型/算法版本记录 |
| `topology_override` | GUID、选中 Object、I→J、支承 | 人工 Line/Span Group JSON 与失效诊断 | 核心契约已实现；ETABS 选择 UI 留在 P2 |
| `src.cli` | PID、模型路径、配置、模式开关 | 拓扑、写回、分析/设计、基础/三表 Excel | P0 正式入口，默认只读；写回后 Excel 强制使用重新分析设计结果 |
| `dxf_export` | 梁线/分跨结果 | 分层 DXF/CSV | 已实现；正式文字方向契约待收口 |
| `BeamDetailingEngine` | 标准化需求、ACI/项目策略 | 左中右纵筋、箍筋、face rebar | 第一版已实现、测试并接入正式三表 CLI |
| `ExcelExport` | 几何、连续性、设计、分跨映射 | 基础 `.xlsx` | 已实现 |
| `rebar_workbook` | 分跨、源截面/FrameType、`GetSummaryResultsBeam_2` | 版本化三表负载、fail-closed 状态、redetail | 已实现并接入正式 CLI |
| `spreadsheet/build_rebar_workbook.mjs` | 正式 JSON 负载 | 原始分跨、调整复核、整理梁表 `.xlsx` 和 QA 预览 | 已实现；大模型默认 Node 堆 8192 MB |
| `run_manifest` | CLI 参数、配置、模型/环境、汇总和输出路径 | 版本化 manifest、成果哈希、CURRENT/SUPERSEDED/INVALID 注册表 | 已实现并通过 ETABS 22.7.0 只读实模验证 |
| SQLite snapshot | ETABS 23 表格导出 | 本地只读数据库/manifest | 路线已记录，未实现 |

### 关键数据模型

- `BeamGeometry`：源 Object 名、Label、楼层、I/J 点、坐标、截面、梁高、路径及源对象集合。
- `BeamLine` / `BeamSpan`：结构梁线与结构跨；内部 `span_id` 目前仍使用 `-S<n>`，最终外部编号要求为 `<楼层>-<PB/SB编号>-<跨号>`，需统一编号策略接口。
- `SpanScheduleMapping`：源 Object 到 Beam Line/Span 的稳定映射，避免 Excel 布局直接依赖拓扑算法。
- `DesignStation` / `BeamDesignResult`：站点位置、受弯/剪切/扭转需求、控制组合、警告和 ETABS 22/23 total/min/provided 字段。
- `WritebackPlan`：方向与改名两个独立事务；阻塞项使用 `REQUIRES_SPLIT` 或 `REQUIRES_SECTION_REVIEW`，不静默写入错误编号。
- `MultiSectionSpanRecord`：同一结构跨内的截面→Object 映射、路径、审查 Group 和用户确认状态。
- 三表 payload：元数据、`raw_rows/review_rows/final_rows`；Excel 公式和版式不反向耦合 ETABS API。
- `BeamDetailingInput` 及策略对象：把规范版本、钢筋目录和项目排布选择与 ETABS API 解耦。

导出、绘图和配筋层只应消费稳定数据模型，不应直接访问 ETABS API。

## 4. 当前验证基线

| 项目 | 2026-07-21 基线 |
|---|---|
| Python | 3.11.7 |
| ETABS | 22.7.0 / 23.3.1 x64 |
| 设计规范 | ACI 318-14 |
| 单元/默认测试 | 105 passed、1 skipped |
| 覆盖率 | 78.65%（干净 Git 快照） |
| 单元测试模块 | 20 个 |
| ETABS 集成测试 | 1 个，需 `ETABS_INTEGRATION=1` 和已打开模型 |
| 最新分跨只读重算 | 当前 v6.4.3：3,020 跨，29 个 `REQUIRES_SPLIT` Object，拓扑 BLOCKED=0 |
| Git | `master`，23 个提交；当前存在未提交开发修改和生成物 |

测试命令：

```powershell
python -m pytest -q

$env:ETABS_INTEGRATION = "1"
python -m pytest tests/integration -v
```

默认跳过 ETABS 集成测试是预期行为。涉及模型写入的验证必须额外记录 EDB 副本、源文件哈希、API 实例/模型路径、单位、锁定状态、写后 GUID/连接关系以及重新分析设计结果。

## 5. 配置与运行契约

`src/config.py` 读取显式配置文件；未指定时使用 `config.yaml`，不存在则回退到 `config.example.yaml`。当前生产入口使用的核心配置包括：

- `etabs.dll_path` 和连接模式；
- 坐标容差、源 Object/Span 重叠容差、梁线归并角度；
- 报告、日志目录和文件名；
- `all` / `selected` 梁选择模式；
- 固定导出单位 `N_mm_C`。

当前运行环境契约：

1. ETABS、Python 和终端均为 x64，并使用相同 Windows 用户和权限级别；
2. 多实例时必须读取并核对实际附着的模型路径，API/PID 只是辅助信息；
3. 写操作仅针对工作副本，先生成可审计计划；
4. `ChangeConnectivity` 用于交换 I/J，`SetLocalAxes` 不能反转局部 1 轴；
5. Unique Name 使用“旧名 → 临时唯一名 → 最终名”两阶段改名；
6. 写回后以 GUID 为稳定身份复核名称、连接、截面和对象数量。

## 6. 文件命名和生命周期

### 生产代码与测试

| 类型 | 规则 | 示例 |
|---|---|---|
| Python 模块 | `snake_case.py` | `beam_line_span_analyzer.py` |
| 类 | `PascalCase` | `BeamDetailingEngine` |
| 函数/方法 | `snake_case()` | `plan_orientation()` |
| 常量 | `UPPER_SNAKE_CASE` | `PROJECT_ROOT` |
| 单元测试 | `tests/unit/test_<module>.py` | `test_model_writeback.py` |
| ETABS 集成测试 | `tests/integration/`，显式 opt-in | `test_workflow.py` |

纯 Python 拓扑、映射、配筋和 Excel/DXF 逻辑必须有单元测试。ETABS API 调用签名、版本差异、权限条件和验证证据必须同步至 [`docs/ETABS_OAPI_VALIDATION.md`](docs/ETABS_OAPI_VALIDATION.md)。

### 生成物

| 文件 | 建议位置 | 是否入库 | 当前问题 |
|---|---|---|---|
| EDB 工作副本 | `build/output/model_writeback/` | 否 | 应由 manifest 关联源哈希 |
| DXF/CSV | `build/output/topology_*/` | 否 | 按模型哈希/算法版本由 manifest 管理 |
| 审计 JSON/日志 | `build/output/reports/`、`logs/` | 否 | 运行脚本与生成物混放 |
| 三表 Excel | 受控交付目录 | 视交付策略 | 当前位于未忽略 `outputs/` |
| SQLite 快照 | `build/output/model_archives/` | 否 | 尚未实现 |
| 本机配置 | `config.yaml` / `config.local.yaml` | 否 | 已忽略 |
| 配置示例 | `config.example.yaml` | 是 | 与当前 ETABS 22/23、ACI 318-14 对齐 |

当前 `.gitignore` 的 `build/` 条目优先于后面的细分规则，因此 `build/` 下所有脚本和输出都会被忽略。后续应把生产脚本迁到 `src/`，再将 `build/` 明确定义为纯生成目录；根目录 `outputs/` 也要明确“交付物入库”或“生成物忽略”二选一，避免大型检查文件进入 Git。

## 7. Git 与交付基线

Git 仓库已初始化，当前分支 `master`，截至 2026-07-18 已有 23 个提交。`src/utils/io.py`、`src/utils/logger.py`、文档体系、`pyproject.toml` 和 opt-in 集成测试均已存在，不再属于旧文档所称的待创建内容。

当前工作树包含正在开发的修改和未跟踪文件，其中 `model_writeback.py`、`detailing.py` 及相应测试尚待形成新的受控提交。不得使用 `git reset --hard`、清理整个输出目录或覆盖用户成果的方式建立基线。

提交继续采用 Conventional Commits：

```text
feat(span): classify primary spans by column support
feat(writeback): verify GUID after two-phase rename
feat(detailing): add ACI 318-14 beam reinforcement policy
test(span): keep PB unbroken at non-column beam intersection
docs(status): align productionization backlog
```

不提交原始/工作 EDB、视频、大型检查数据和临时工作簿。若将来需要集成测试模型，应使用小型脱敏 EDB 并单独制定版本管理策略。

## 8. 后续结构调整计划

### P0：形成可重复交付链路

1. [已完成] 把 `build/` 中必要的分析、写回、提取、DXF 和三表 Excel 编排迁入正式 `src/`；
2. [已完成] 建立成果 manifest，记录 EDB 源/副本哈希、ETABS/API 版本、规范、单位、Git/算法版本、异常统计和输出哈希，并按确定性指纹使旧成果失效；
3. 写回事务默认在存在阻塞项时终止；部分写回必须显式授权并在审计中标记；
4. rigid 刚域支承传递、Z 高程相交、梁 Object 内柱点、PB/SB 固定点和拓扑门禁已完成；后续在 manifest 中冻结模型/算法版本；
5. 修正依赖/环境契约：Python 3.11、ETABS 22/23、ACI 318-14，以及有效的 `openpyxl` 版本范围；
6. 使用最新算法从原始 v6.4.1 模型重生成 EDB、DXF、设计结果和 Excel。

### P1：规则和交付收口

1. 统一内部/外部编号策略，使 Excel、DXF、JSON 和模型写回统一采用无 `S` 的跨编号；
2. 用 GUID/空间指纹保存稳定梁号，避免新增构件导致整层编号漂移；
3. 区分纵筋保护层和箍筋外缘净保护层，解决窄梁 `NO_ALLOWED_SPACING`；
4. 混合截面、混合 Frame Type 和 `Program Determined` 抗震等级采用 fail-closed；
5. 将相邻跨配筋 option 1/2 接入整条 Beam Line 联合协调；
6. CAD 文字方向强制跟随模型 I→J，并完成正式 JSON 中间数据。

### P2：扩展能力

1. ETABS 23 SQLite 本地只读快照；
2. ETABS/CAD 插件化交付；
3. 其他 ACI 版本、GB 规范、SAP2000、GUI 和安装包；
4. CI/CD 与可提交脱敏模型测试。

## 9. 新增文件管理流程

1. 判断文件属于正式生产代码、测试、规则文档、交付物还是本机生成物；
2. 生产逻辑进入 `src/`，不得长期留在 `build/`；
3. 独立纯逻辑同步添加 `tests/unit/test_<module>.py`；
4. ETABS API 变化同步更新实测文档和集成验证；
5. 输出文件进入明确的生成目录并由 manifest 追踪，不以文件名代替来源证据；
6. 功能状态和验收证据同步更新 [`PROJECT_COMPLETION_CHECKLIST.md`](PROJECT_COMPLETION_CHECKLIST.md)；
7. 若目录或入口变化，同步更新本文件和 [`README.md`](README.md)。

## 10. 结构核对清单

- [x] Python 3.11 / ETABS 22/23 / ACI 318-14 当前基线已明确
- [x] 梁几何、设计读取、Beam Line/Span 和基础 Excel 正式代码已实现
- [x] I/J、Unique Name 写回原型与单元测试已实现
- [x] DXF/CSV 分跨复核已实现
- [x] ACI 318-14 配筋排布引擎和三表 Excel 已接入正式 CLI 并验证
- [x] `utils/io.py`、`utils/logger.py`、开发/用户/API 文档已存在
- [x] Git 已初始化并已有 23 个提交
- [x] 105 passed、1 skipped、干净 Git 快照覆盖率 78.65%
- [x] 完整链路从 `build/` 迁入正式可复现 CLI
- [ ] rigid/Z 高程/剩余跨 Span Object 拓扑收尾
- [x] manifest、成果哈希和跨运行失效注册表
- [ ] 失败回滚和全量写后属性比较
- [ ] 用 3010 跨最新算法重新生成最终验收成果
- [ ] 配筋异常、保护层和相邻跨连续性收尾
- [ ] 输出目录和 `.gitignore` 生命周期收敛
- [ ] 可提交脱敏 EDB、CI/CD、SQLite 和插件交付
