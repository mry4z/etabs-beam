# 项目完成与开发状态清单

**更新日期**：2026-07-12  
**项目路径**：`F:\2026\Research Study\etabs beam\`  
**当前阶段**：MVP 核心链路已实现并在真实 ETABS 模型上验证

> 本文件反映当前实际状态，替代 2026-07-10 的“项目整理完成”占位符清单。规划、已实现和远景能力在下文明确分开标注。

---

## 当前结论

项目已从“文档与空代码骨架”进入“可运行 MVP”阶段。当前工具可附着到已打开的 ETABS、读取模型梁和混凝土设计摘要、识别连续梁组，并导出 Excel 梁表。

本次真实模型验证：

- [x] 成功附着 `E:\051 SAP_ETABS MODEL\beam\bm.EDB`
- [x] 记录原始当前单位，在导出期间固定 `N_mm_C`，并在结束后恢复原单位
- [x] 读取 96 根梁的楼层、截面、端点坐标和长度
- [x] 识别 32 个连续梁组
- [x] 读取 5,568 条 ETABS 混凝土梁设计站点结果
- [x] 导出完整 Excel 梁表和运行日志
- [x] 单元测试 19 / 19 通过；默认测试套件覆盖率 65.83%，达到 60% 基线门槛
- [x] 复杂模型试验：4,217 根原始梁合并为 1,754 根逻辑梁，导出 50,925 条设计站点，并生成异常记录页

详细 API 用法、环境限制和验证证据见 [ETABS_OAPI_VALIDATION.md](docs/ETABS_OAPI_VALIDATION.md)。

> **状态注记**：`PROCESS_CONTROL_REQUIREMENTS.md` 第 8 节曾判断"统一数据模型（`BeamGeometry`/`ContinuityGroup`/`BeamDesignResult`/导出字段）缺失"为悬空项；该判断基于 2026-07-10 的状态，已被本次 MVP 实现覆盖——四类数据模型均已在 `src/core/geometry_extractor.py`、`src/core/continuity_analyzer.py`、`src/design/provider.py`、`src/excel_export.py` 中落地并通过真实模型验证，不再是待办项。

---

## 已完成

### 1. MVP 源代码

- [x] `src/config.py`：YAML 配置加载、默认路径解析和数据类配置
- [x] `src/core/etabs_connector.py`：pythonnet 加载 DLL、附着活动 ETABS、获取模型路径、统一 API 错误处理和临时单位恢复
- [x] `src/core/geometry_extractor.py`：梁过滤、选择集读取、楼层/截面/端点/坐标提取
- [x] `src/core/beam_merger.py`：同截面过度分割梁段合并、折线梁路径和异常记录
- [x] `src/core/continuity_analyzer.py`：按楼层、端点容差和平面夹角识别连续梁组
- [x] `src/design/provider.py`：`BeamDesignResult`、`DesignStation` 与设计策略协议
- [x] `src/design/etabs_result.py`：读取 `DesignConcrete.GetSummaryResultsBeam`
- [x] `src/excel_export.py`：导出“梁表”和“设计站点”工作表，并设置表头样式、筛选和冻结窗格
- [x] `src/workflow.py`：连接 → 提取 → 连续性分析 → 读设计结果 → Excel 导出的命令行工作流
- [x] `src/utils/logger.py`、`src/utils/io.py`：共享日志配置与防覆盖输出路径
- [x] `src/design/aci318.py`：远景接口占位，明确不在当前 MVP 范围

### 2. 测试与实测

- [x] 几何工具单元测试：距离、共线反向梁夹角
- [x] 连续梁单元测试：共线相连与直角相接场景
- [x] Excel 导出单元测试：工作簿、工作表和数据行可读性
- [x] 单位恢复单元测试：正常路径和导出异常路径均恢复原单位
- [x] 真实 ETABS 手工集成验证：连接、96 根梁、32 组连续梁、5,568 个设计站点、Excel 文件复核
- [x] 可选自动化 ETABS 集成测试：读取、固定单位导出、Excel 行数核对；仅在显式设置 `ETABS_INTEGRATION=1` 时运行
- [x] 代表性设计站点比对：集成测试对首/中/末梁的首/中/末站点，逐项比较位置、顶筋、底筋和剪力筋导出值与 API 值

### 3. 文档与配置

- [x] `README.md`：项目入口与总体路线
- [x] `PROJECT_STRUCTURE.md`：按实际目录和进度修订（本次同步更新）
- [x] `docs/TECHNICAL_ROADMAP.md`：原始技术路线
- [x] [ETABS_OAPI_VALIDATION.md](docs/ETABS_OAPI_VALIDATION.md)：实测 API 用法、非常规 pythonnet 参数、双实例/权限排查及验证证据
- [x] `requirements.txt`：声明核心运行、导出和测试依赖
- [x] `config.example.yaml`：ETABS DLL、几何容差、输出目录和日志配置样例

---

## 已生成的验证交付物

| 文件 | 说明 |
|---|---|
| `build/output/reports/beam_schedule_20260712_232350.xlsx` | 固定 `N_mm_C` 导出：96 根梁、5,568 个设计站点，梁表含左中右关键配筋字段 |
| `build/output/reports/beam_schedule_20260713_000839.xlsx` | 复杂模型试验：1,754 根逻辑梁、50,925 个设计站点，含原始梁映射和异常记录 |
| `build/output/logs/etabs_beam_20260712_232349.log` | 对应工作流日志（记录原始、导出与恢复单位） |
| `build/output/reports/integration_geometry_20260712_030650.xlsx` | 早期三根梁几何联调结果（来自另一份模型，仅作连接调试留档） |

生成文件按 `.gitignore` 规则不进入版本库。

---

## 当前目录状态

```text
src/
├── config.py                         已实现
├── core/
│   ├── etabs_connector.py            已实现
│   ├── geometry_extractor.py         已实现
│   └── continuity_analyzer.py        已实现
├── design/
│   ├── provider.py                   已实现
│   ├── etabs_result.py               已实现
│   └── aci318.py                     远景占位
├── utils/geometry.py                 已实现
├── utils/io.py、logger.py            已实现：防覆盖输出路径与日志配置
├── excel_export.py                   已实现
└── workflow.py                       已实现

tests/unit/                           4 个测试文件，15 项测试
tests/integration/test_workflow.py    已实现：显式 opt-in 的 ETABS 端到端测试
docs/ETABS_OAPI_VALIDATION.md         本次新增的实测记录
```

---

## 后续待办（重要-紧急矩阵）

已完成事项（单位策略、集成测试自动化、初始化 Git 仓库、排除历史资料）不再占用矩阵，详见上文"当前结论"与"已完成"章节。以下按**重要且紧急 → 重要不紧急 → 紧急不重要 → 不重要不紧急**排列；每个象限内部按开发逻辑顺序排列（有依赖关系的前序任务排在前面），随开发进度推进后应及时勾选并把象限内顺序前移到下一未完成项。

> **2026-07-12 代码审查新增发现**：对 `cd285b4`/`fc8cbf1` 两次提交做了针对性代码审查（非泛读），用最小复现脚本验证了 1 个高严重度缺陷，并核实了 2 个中低严重度缺口，见下方新增条目 1、5、6。

### 🔴 重要且紧急 — 现在就做，按顺序推进

1. [x] **连续梁分组在分支拓扑下静默丢梁并导致导出崩溃**：已将非简单路径（度数 ≥3 的分支或环）显式降级为独立梁组并记录 warning，确保每根梁仍被导出；新增 4 根共线分支复现测试，确认 4 根梁全部保留且不再触发 `KeyError`。
2. [x] **配置提交身份**：已仅对本仓库设置 `ETABS Beam Generator <etabs-beam@local>`，不影响其他项目。
3. [x] **建立首个基线提交**：已创建 `cd285b4 feat: establish validated ETABS beam export MVP`；仅包含源码、测试、主文档与配置示例，历史资料和生成物均未纳入。
4. [x] **连续性验收样本**：已覆盖两跨、三跨、孤立梁、不同楼层、分叉节点和超限斜梁共 7 类离线场景。*注："分叉节点"场景经复核为 T 型直角分支（度数 2 的简单路径变形），未覆盖上方第 1 项的度数 ≥3 分支拓扑，两者不冲突。*
5. [x] **设计结果验收**：用户已完成模型人工抽查；自动化集成测试另对首/中/末梁的首/中/末站点逐项比较 API 与 Excel 的位置、顶筋、底筋和剪力筋值。

### 🟡 重要不紧急 — 核心验收通过后按顺序推进

6. [x] **`etabs.timeout_sec` 配置项未生效**：已从数据类和配置示例移除该死配置；当前连接 API 不再承诺不存在的超时行为。
7. [x] **梁表汇总缺少扭转纵筋峰值**：已新增 `max_torsion_longitudinal_area` 与“最大受扭纵筋面积（仅受扭需求）”列；最新报告中首根梁 `B1` 的峰值为 `5419.99336505491`。
8. [x] **明确并重命名弯矩纵筋与扭转纵筋的工程语义**：CSI 官方 `GetSummaryResultsBeam` 文档确认：`TopArea` / `BotArea` 为弯矩所需总纵筋且不含受扭钢筋；`TLArea` 为仅受扭所需纵筋总面积，不是总纵筋。代码、梁表与站点表已改用“顶部/底部弯矩纵筋（不含受扭纵筋）”和“受扭纵筋（仅受扭需求）”名称。
9. [x] **输出梁左—中—右关键位置的弯矩筋与抗剪筋**：不再单独建表；梁表中顶部弯矩纵筋、底部弯矩纵筋和抗剪筋字段各拆为左端 `2D`、跨中、右端 `2D` 三列，仅输出控制面积。`D` 为梁截面总高；左/右区为距 I/J 端 `2D`，中区为两端区之间的控制最大值；若两端区相交则取最接近跨中的站点。
10. [x] **复杂模型梁段合并与异常记录**：对当前模型按同楼层、同截面、端点相接、夹角≤3°合并简单路径梁段；4,217 根原始梁合并为 1,754 根逻辑梁，结果写入 `原始梁对象` 列，特殊情况写入 `异常记录` 页。
11. [x] **配置收敛**：导出单位配置已规范为唯一允许值 `N_mm_C`；其余远景字段保留注释说明，不参与当前工作流。
12. [x] **日志与 I/O 模块**：日志配置已下沉至 `src/utils/logger.py`；输出路径防覆盖已下沉至 `src/utils/io.py`。
13. [x] **开发者与用户文档**：已补齐 `docs/DEVELOPMENT.md`、`docs/USER_GUIDE.md`、`docs/API.md` 与 `docs/api/design_results.md`。
14. [x] **代码质量门槛**：已配置 Black、Flake8、严格 pytest marker 与 60% 覆盖率基线；完整 CI/CD 仍属远景。
15. [ ] **梁局部坐标与图纸文字方向整理（远期模型功能）**：建立可预览、可确认的梁方向整理工具，使局部 1 轴统一为 I→J；优先令 I 端位于全局 X/Y 的负向、J 端位于正向（对不满足单调关系的斜梁需定义稳定的 X 优先、Y 次级判据）。梁编号写入图纸/导出定位时，文字方向必须与局部 1 轴一致。该功能涉及修改 ETABS 模型对象方向，实施前必须处理荷载、释放、局部轴、连接关系和可逆备份，当前不自动执行。
16. [ ] **ETABS SQLite 数据本地归档（版本能力就绪后）**：待目标 ETABS 版本正式提供可用的 SQLite 数据访问或导出能力后，在每次梁表导出时将对应 SQLite 数据副本存放到本地归档目录，并写入模型路径、ETABS 版本、导出时间、单位和文件校验值。归档必须只读、不得替代当前 OAPI 读取主路径，也不得修改正在打开的模型。

### 🟢 紧急不重要

当前清单中没有这类事项——所有待办均源自内部工程质量把控，没有外部截止时间驱动的非核心任务。

### ⚪ 不重要不紧急 — 远景，明确不占用主线

17. [ ] ACI 318 自研配筋引擎
18. [ ] GB50010 或其他规范支持
19. [ ] SAP2000 双宿主支持
20. [ ] GUI、安装包和完整 CI/CD

---

## 运行与复核命令

```bash
# 离线单元测试
python -m pytest tests/unit -q

# 先打开目标 ETABS 模型；若 ETABS 以管理员身份运行，终端也需同权限
python -m src.workflow --selection all

# 仅导出 ETABS 当前选中的梁
python -m src.workflow --selection selected
```

运行前后应检查控制台或日志中输出的模型路径，避免多实例时附着到错误模型。

---

**状态维护规则**：每完成一项待办，应先在"重要-紧急矩阵"中勾选并更新复核证据，再更新技术路线图或用户文档；新增待办应先判断象限和开发依赖顺序，再插入对应位置。
