# ETABS Beam Generator

面向 ETABS 混凝土梁的几何整理、结构分跨、编号写回、设计结果读取、配筋排布和 CAD/Excel 交付工具。

> **当前阶段（2026-07-22）**：P0 梁线/分跨、可复现工作流、成果 manifest、写回事务和 v6.4.3 全链路基线均已关闭。`src.cli` 已串联模型副本写回、分析/设计、DXF/CSV/JSON、基础/三表 Excel，并完成保存重开、全属性、需求等价和幂等性验证。下一阶段进入 P1 配筋工程闭环、稳定编号和 CAD 中间数据。

## 快速导航

| 目的 | 文档 |
|---|---|
| 当前进度、阻塞项和下一步 | [`PROJECT_COMPLETION_CHECKLIST.md`](PROJECT_COMPLETION_CHECKLIST.md) |
| 当前目录与模块职责 | [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md) |
| 梁线、主次梁和分跨规则 | [`BEAM_LINE_SPAN_RULES.md`](docs/BEAM_LINE_SPAN_RULES.md) |
| ETABS API 实测与环境排查 | [`ETABS_OAPI_VALIDATION.md`](docs/ETABS_OAPI_VALIDATION.md) |
| 编号、I/J 方向与 CAD 交换 | [`DRAWING_NUMBERING_INTERCHANGE.md`](docs/DRAWING_NUMBERING_INTERCHANGE.md) |
| ACI 318-14 配筋规则评审 | [`PRIMARY_BEAM_REBAR_DETAILING_ACI318_14_REVIEW.md`](docs/PRIMARY_BEAM_REBAR_DETAILING_ACI318_14_REVIEW.md) |
| ETABS 23 SQLite 技术路线 | [`ETABS_SQLITE_SNAPSHOT_ROUTE.md`](docs/ETABS_SQLITE_SNAPSHOT_ROUTE.md) |
| 开发指南与技术路线 | [`DEVELOPMENT.md`](docs/DEVELOPMENT.md) · [`TECHNICAL_ROADMAP.md`](docs/TECHNICAL_ROADMAP.md) |

## 已实现能力

| 能力 | 当前状态 |
|---|---|
| ETABS 连接、模型路径确认、临时切换 `N_mm_C` 并恢复原单位 | 已实现并实测 |
| 混凝土梁筛选、几何/截面/柱支承与设计站点读取 | 已实现；Frame 几何使用 `GetAllFrames` 批量快照 |
| Beam Line/Span、PB/SB、悬挑、弧形梁和多 Object 一跨识别 | P0-1 已冻结；真实复杂模型 BLOCKED 拓扑诊断为 0 |
| 左/中/右关键位置（端部 `2D`、跨中区域、端部 `2D`）设计结果 | 已实现 |
| DXF/CSV 图形化分跨复核 | 已实现；最新成果位于 `build/output/topology_p01_20260721_final3/` |
| I/J 方向整理、两阶段 Unique Name 改名、全属性/回滚/幂等验证 | P0 已在模型副本关闭 |
| ETABS 22/23 分析、ACI 318-14 混凝土设计和 `_2` 结果字段读取 | 两个主版本均已在真实模型验证 |
| ACI 318-14 + 项目策略配筋排布 | `src/design/detailing.py` 第一版已实现并有单元测试 |
| 原始分跨数据、配筋调整复核、整理梁表三表 Excel | 正式负载、离线 redetail、artifact-tool 生成器和 CLI 均已纳入 `src/` |
| 成果 manifest 与失效注册表 | 成功/失败运行均记录；输入或算法变化时旧成果自动标记 `SUPERSEDED` |
| SQLite 本地快照 | 技术路线已记录，尚未实现 |

最新 v6.4.3 只读重放读取 **4,110** 个混凝土梁 Object，形成 **1,215** 条 Beam Line、**3,020** 个结构跨（PB 1,573、SB 1,447），29 个跨 Span Object 进入 `REQUIRES_SPLIT`；4,110/4,110 源 Object 被覆盖，拓扑 BLOCKED 为 0。此前的 3,070/3,074 跨 EDB、DXF 和 Excel 属于旧算法成果；不同 EDB 哈希的统计也不得直接互相替代。

## 已验证环境

| 组件 | 当前基线 |
|---|---|
| 操作系统 | Windows x64 |
| Python | **3.11.7** |
| ETABS | 正式支持 **22.7.0 / 23.3.1**；必须加载与宿主主版本一致的 `ETABSv1.dll` |
| 设计规范 | **ACI 318-14** |
| ETABS API | `ETABSv1.dll`，优先使用 pythonnet |
| Excel | `openpyxl` 用于基础导出；三表评审使用已跟踪的 artifact-tool 生成器，Node 运行时可显式配置 |

`requirements.txt` 已把 Excel 依赖约束修正为 `openpyxl>=3.1.0,<4`。

## 当前运行方式

### 只读正式入口

确保 ETABS 已打开目标模型，并让 ETABS、Python 与终端保持相同权限级别：

```powershell
python -m src.workflow --selection all
```

也可使用 `--selection selected` 仅处理 ETABS 当前选择集。该入口目前执行：

1. 附着已运行 ETABS，并输出实际模型路径；
2. 记录原始显示单位，临时切换至 `N_mm_C`；
3. 读取梁几何、柱支承和混凝土设计结果；
4. 计算 Beam Line/Span 并建立源 Object 映射；
5. 导出基础 Excel 梁表；
6. 恢复原始显示单位。

### 写回与完整交付链路

写回必须只针对模型工作副本，不能直接改原始 EDB。目前已验证的完整链路是：

```text
原始 EDB → 工作副本 → 分跨/编号计划 → I/J 反向 → 两阶段改名
         → 写后验证 → ETABS 分析设计 → DXF/CSV → 配筋引擎 → 三表 Excel
```

该链路已经迁入正式源码；`build/output/reports/` 和 `build/spreadsheet_work/` 仅保留历史验证证据，不再是运行依赖。失败回滚、需求等价、全属性和保存重开幂等性检查均已完成。

## 项目结构摘要

```text
src/
├── core/                 # ETABS 连接、几何、归并、Beam Line/Span、模型写回
├── design/               # ETABS 结果、关键位置、ACI 318-14 配筋排布
├── drawing/              # DXF 输出
├── utils/                # 几何、IO、防覆盖路径与日志工具
├── excel_export.py       # 基础梁表导出
├── excel_span_adapter.py # 分跨到 Excel 的稳定适配层
├── rebar_workbook.py     # 三表负载、redetail 和生成器编排
├── run_manifest.py       # 运行身份、成果哈希和 SUPERSEDED/INVALID 注册表
├── spreadsheet/          # artifact-tool 三表生成器
└── cli.py                # P0 正式全链路 CLI

tests/
├── unit/                 # 20 个单元测试模块
└── integration/          # 1 个显式 opt-in ETABS 集成测试

docs/                     # 规则、API、路线和评审文档
build/                    # 被 Git 忽略的本机脚本与生成物
outputs/                  # 当前三表 Excel 评审成果；尚需收敛生命周期和忽略规则
```

详见 [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md)。

## 测试基线

2026-07-22 在 Python 3.11.7 执行：

```powershell
python -m pytest -q
```

结果为 **105 passed、1 skipped，干净 Git 快照覆盖率 78.65%**。跳过项是需要已打开 ETABS 且显式启用的集成测试。

```powershell
$env:ETABS_INTEGRATION = "1"
python -m pytest tests/integration -v
```

真实模型操作仍需单独验证；单元测试通过不能替代 EDB 副本、API 返回值、写后 GUID/连接关系以及 DXF/Excel 的工程复核。

## 近期工作重点

1. 强化写回事务的全属性比较、幂等、失败回滚和跨 Span Object 安全处置；
2. 用冻结算法重放最新 v6.4.3 配筋基线；
3. 收尾保护层语义和相邻跨 option 1/2；
4. 冻结 CAD 文字方向必须与模型 I→J 一致的交换契约，再推进 SQLite 与 ETABS/CAD 插件。

完整优先级和验收条件以 [`PROJECT_COMPLETION_CHECKLIST.md`](PROJECT_COMPLETION_CHECKLIST.md) 为准。

## 开发与版本状态

- Git 仓库已初始化，当前分支为 `master`，截至 2026-07-18 已有 **23 个提交**；
- 当前工作树包含正在推进的代码/文档修改及未跟踪生成物，整理基线前不得用清理命令覆盖用户成果；
- `src/utils/io.py`、`src/utils/logger.py`、文档体系及 ETABS opt-in 集成测试均已实现，不再属于“尚未创建”项目；
- 新增生产逻辑应进入 `src/`，纯 Python 逻辑必须有单元测试，ETABS 写操作必须使用工作副本和可审计计划。

## 常见问题

### 为什么有些 Object 没有改名？

一个 ETABS Object 只能有一个 Unique Name。若它被算法判定同时贡献多个结构跨，写回计划会标记 `REQUIRES_SPLIT`，而不是静默选择某一跨。应先确认是否为分跨误判；确实跨支承的对象再进入分割/映射专项处理。

### 为什么普通 Python 有时连接不到 ETABS？

Windows COM Running Object Table 受进程权限级别和会话隔离影响。ETABS 与 Python/终端应使用相同用户、相同权限级别及相同位数。未来从 ETABS 内部加载插件可减少这类权限错配。

### ETABS 设计结果与配筋引擎如何分工？

ETABS 提供 `AsTotal`、`Al`、`Av/s`、`At/s` 等设计需求；项目配筋引擎负责按 ACI 318-14 和工程策略选择钢筋直径、根数、排数、箍筋间距及 face rebar。当前受弯需求采用 `AsFlex = AsTotal - Al/2`，并保留规范版本与用户钢筋目录接口。

## 项目信息

- **状态**：可验证工程原型，正在生产化和规则收尾
- **主要验证模型**：`EMHS-GF.v6.4.1.EDB` 及其受控工作副本
- **当前规范**：ACI 318-14
- **最后更新**：2026-07-18
- **许可证**：待根据课题交付要求确定
