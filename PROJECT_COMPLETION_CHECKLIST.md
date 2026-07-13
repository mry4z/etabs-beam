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
| `build/output/reports/beam_schedule_20260713_00136.xlsx` | 复杂模型试验：1,754 根逻辑梁、50,925 个设计站点；非混凝土截面跳过设计统计，异常记录保留 803 条合并映射 |
| `build/output/reports/beam_schedule_20260713_131722.xlsx` | 15°归并验证：4,166 根混凝土梁、1,651 根逻辑梁；梁表含设计结果状态 |
| `build/output/reports/merge_angle_3_vs_15_20260713_131921.csv` | 15°相对3°新增的51个逻辑合并组，供人工抽查 |
| `build/output/drawing_data/merge_angle_3_vs_15_20260713_150825.dxf` + `.csv` | 51个差异组的全量 CAD 图形复核及坐标映射：红=15°新增连接、蓝=3°原分组、黄/青=中点映射 |
| `build/output/drawing_data/beam_groups_full_15deg_20260713_160709.dxf` + `.csv` | 当前 ETABS v6.3 模型的全部 1,652 个逻辑梁多段线；黄色主梁760、绿色次梁892，编号与坐标映射同名 CSV |
| `build/output/drawing_data/beam_spans_structural_20260713_194933.dxf` + `.csv` | API ID 278156、v6.2 模型按楼层/支承生成的结构分跨复核图：3,023 跨、3,023 条多段线，主梁1,568跨、次梁1,455跨 |
| `build/output/drawing_data/beam_spans_structural_stitched_20260713_211158.dxf` + `.csv` + `.records.txt` | 第 7 项修正后的跨截面 Beam Line 串接验证：2,976 跨、每跨一条多段线；4,166 个源 Object 全部覆盖（38 个跨支承 Object 按一对多关系列示），GF/T1 目标梁组已分别收敛为 22 跨/6 跨连续梁线 |
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

> **2026-07-13 真实模型图形复核新增发现**：API ID 278156 的 `EMHS-0606-P4700-GF.v6.2.EDB` 已完成首轮结构分跨 DXF/CSV 复核。实模证明当前程序错误地将“同截面”作为 Beam Line 身份条件，导致连续梁线在截面变化处被拆成多个 PB/SB 编号；该问题及 DXF 标注中点问题已提升为下方第 7 项高优先级缺陷。

### 🔴 重要且紧急 — 现在就做，按顺序推进

1. [x] **连续梁分组在分支拓扑下静默丢梁并导致导出崩溃**：已在 `_ordered()` 前通过 `_is_simple_path()` 显式检测分支和环，并将非简单拓扑降级为独立梁组、记录 warning；`test_complex_collinear_branch_preserves_every_beam_as_singletons` 已验证所有梁保留且不再触发 `KeyError`。
2. [x] **配置提交身份**：已仅对本仓库设置 `ETABS Beam Generator <etabs-beam@local>`，不影响其他项目。
3. [x] **建立首个基线提交**：已创建 `cd285b4 feat: establish validated ETABS beam export MVP`；仅包含源码、测试、主文档与配置示例，历史资料和生成物均未纳入。
4. [x] **连续性验收样本**：已覆盖两跨、三跨、孤立梁、不同楼层、分叉节点和超限斜梁共 7 类离线场景。*注："分叉节点"场景经复核为 T 型直角分支（度数 2 的简单路径变形），未覆盖上方第 1 项的度数 ≥3 分支拓扑，两者不冲突。*
5. [x] **设计结果验收**：用户已完成模型人工抽查；自动化集成测试另对首/中/末梁的首/中/末站点逐项比较 API 与 Excel 的位置、顶筋、底筋和剪力筋值。
6. [x] **设计结果为空但未被检测异常的情况**：已建立“有设计结果 / 无设计结果 / 不纳入混凝土设计 / 读取失败 / 部分梁段缺设计结果”五种状态；混凝土梁 `count == 0` 时写入异常记录，梁表新增“设计结果状态”和“设计结果说明”列，新增单元测试覆盖合并梁的部分缺失状态。
7. [ ] **实模 Beam Line 被截面变化错误拆号，且 DXF 梁号未位于跨长中点**（高 severity，阻塞正式梁号、跨号、悬挑判断及 DXF 验收）：
   - **实模证据**：`GF-GRD-F-SB17`～`GF-GRD-F-SB24` 的路径共线且首尾连续，连接误差约 `0.025 mm`，但截面在 `B 300X600`、`B 300X700`、`B 300X800` 之间变化；`T1-F2-SB65`、`SB70`、`SB75`、`SB85`、`SB88` 同样连续，连接误差小于 `0.1 mm`，截面依次为 `B 300X700 -P`、`B 300X700`、`B 300X600`、`B 300X800`、`B 300X700 -P`。这些对象应分别属于同一根 Beam Line 的不同跨，不应因截面变化获得新的 SB 编号；主梁同类情况遵循相同原则。
   - **首轮修正实测**：新增跨截面串接及源 Object 分配修正后，API ID 278156 实模重新输出 `build/output/drawing_data/beam_spans_structural_stitched_20260713_211158.dxf/.csv` 及 `.records.txt`；结果为 4,166 根混凝土梁、1,173 条 Beam Line、2,976 跨（PB 519 条/1,556 跨，SB 654 条/1,420 跨），无警告或错误记录。4,166 个源 Object 全部被覆盖；其中 38 个 Object 实际跨越结构支承，因此按一对多关系列入相邻 Span，未再出现空的源对象字段。上述 GF 梁组已收敛为同一根 SB（新编号 `GF-GRD-F-SB13`、22 跨），T1 梁组已收敛为同一根 SB（新编号 `T1-F2-SB49`、6 跨）。该输出仍待用户 CAD 全量复核，因此本项暂不勾选完成。
   - **已定位根因**：`BeamMerger` 先按 `(story, section)` 建立候选组，`beam_line_span_analyzer` 随后只在单个候选组内部按支承切割，缺少跨截面候选组的二次拓扑串接；每个断开的候选组最终取得新的 PB/SB 编号并从 `S1` 重新计数。被截断的局部梁组还会因看不到完整支承关系而产生内部梁段误判为悬挑的连带错误。坐标容差和 15° 归并角不是上述样例的主因。
   - **修改原则一——先定梁线、后定分跨**：新增独立的 Beam Line 拓扑追踪阶段，按同楼层、端点连接、方向/切线连续及结构支承关系识别整根梁；截面名称、截面尺寸变化只作为源 Object/Span 属性保存，不再作为 Beam Line 身份或编号边界。若同一结构跨内部发生截面变化，应记录专项异常供人工核对，但不得静默生成新的 Beam Line 编号。
   - **修改原则二——支承边界保持既定业务规则**：完整主梁仅在柱或等效柱支承处增加跨，完整次梁仅在主梁支承处增加跨，次梁—次梁相交不增加跨；悬挑端单独成跨；弧形主梁与相邻直线主梁的独立编号应在完整梁线层级判断，不能由单个短梁段的曲直布尔值决定。
   - **修改原则三——统一编号与派生判断**：梁线和全部支承边界确定后，再沿稳定的 I→J 方向一次性生成 `[楼层]-[PB/SB编号]-S1...Sn`；悬挑、左右方向、文字方向和配筋左/中/右映射均基于该完整拓扑结果计算。合并错误梁组后，同楼层后续 PB/SB 序号允许整体顺移。
   - **DXF 标注修正**：当前实现取路径点数组的中间顶点，不是梁跨沿程长度中点。应按多段线累计长度求 `50%` 位置并在线段内插值，文字插入点使用该真实跨长中点，文字角度使用中点所在梁段的局部切线；颜色继续跟随相应主梁黄色/次梁绿色。
   - **验收条件**：上述 GF 与 T1 样例分别归为一根连续 Beam Line 并连续编号 `S1...Sn`；不再出现由候选组截断造成的假悬挑；每个结构跨恰有一条 DXF 多段线和一个位于跨长中点的同色编号；源 Object 不遗漏，跨越结构支承的 Object 允许按实际贡献跨一对多列示；新增跨截面、主梁过柱、次梁过主梁、次梁交叉、悬挑和折/弧线回归测试，并重新输出全模型 DXF/CSV 供 CAD 图形复核。

### 🟡 重要不紧急 — 核心验收通过后按顺序推进

8. [x] **配置项 `etabs.timeout_sec` 被解析但未使用**：已删除该死配置及相关说明；当前连接 API 不承诺不存在的超时行为。

9. [x] **梁表汇总缺少扭转纵筋峰值**：已新增 `max_torsion_longitudinal_area` 和“最大受扭纵筋面积（仅受扭需求）”列。

10. [x] **明确并重命名弯矩纵筋与扭转纵筋的工程语义**：CSI 官方 `GetSummaryResultsBeam` 文档确认：`TopArea` / `BotArea` 为弯矩所需总纵筋且不含受扭钢筋；`TLArea` 为仅受扭所需纵筋总面积，不是总纵筋。代码、梁表与站点表已改用”顶部/底部弯矩纵筋（不含受扭纵筋）”和”受扭纵筋（仅受扭需求）”名称。

11. [x] **输出梁左—中—右关键位置的弯矩筋与抗剪筋**：不再单独建表；梁表中顶部弯矩纵筋、底部弯矩纵筋和抗剪筋字段各拆为左端 `2D`、跨中、右端 `2D` 三列，仅输出控制面积。`D` 为梁截面总高；左/右区为距 I/J 端 `2D`，中区为两端区之间的控制最大值；若两端区相交则取最接近跨中的站点。

12. [ ] **15°归并差异人工审查**（中 severity，技术决策需要确认）：已用同一真实模型完成 `15°` 导出：筛选后 4,166 根混凝土梁归并为 1,651 根逻辑梁；相对 `3°` 新增 51 个逻辑合并组。现已按梁点坐标生成全量 DXF `build/output/drawing_data/merge_angle_3_vs_15_20260713_150825.dxf` 及对应坐标映射 CSV，可在 CAD 中按图层颜色核对全部差异；差异清单仍见 `build/output/reports/merge_angle_3_vs_15_20260713_131921.csv`。其中存在约 91.8m 的长合并链，须确认是否跨柱、跨支座或误并转角梁，并由人工决定保留 `15°` 或回退参数。

13. [x] **混凝土梁导入筛选**：导入前检查梁设计方向、截面材料类型和 `rigid` 命名规则；钢截面、刚域辅助杆件和无截面对象不进入混凝土配筋统计。

14. [x] **配置收敛**：导出单位配置已规范为唯一允许值 `N_mm_C`；其余远景字段保留注释说明，不参与当前工作流。

15. [x] **日志与 I/O 模块**：日志配置已下沉至 `src/utils/logger.py`；输出路径防覆盖已下沉至 `src/utils/io.py`。

16. [x] **开发者与用户文档**：已补齐 `docs/DEVELOPMENT.md`、`docs/USER_GUIDE.md`、`docs/API.md` 与 `docs/api/design_results.md`。

17. [x] **代码质量门槛**：已配置 Black、Flake8、严格 pytest marker 与 60% 覆盖率基线；完整 CI/CD 仍属远景。

18. [ ] **逻辑梁编号、支承分跨、局部坐标与图纸中间数据（远期模型功能）**：业务规则已确认并固化在 `docs/BEAM_LINE_SPAN_RULES.md`：先识别 Beam Line，再按支承分 Span；主梁按柱分跨，次梁按主梁分跨，次梁—次梁相交不增加跨数，弧形主梁独立编号，悬挑段单独成跨。已新增 `src/core/beam_line_span_analyzer.py` 初版及离线测试：覆盖柱分跨、主梁分跨、次梁交点不分跨、弧形主梁独立编号和悬挑识别；DXF 已新增每跨一个多段线、Arial 标注及同色文字输出接口。已用 API ID 278156 的真实模型输出 `beam_spans_structural_20260713_194933.dxf/.csv` 并完成首轮 CAD 图形复核，但因第 7 项跨截面 Beam Line 拆号缺陷，当前实模验收未通过；应先完成第 7 项并重新全模型复核，再接入正式工作流/Excel，随后完善局部坐标整理和 JSON 中间数据。图纸编号采用 `[楼层]-[PB/SB]-[跨编号]`，如 `F1-PB5-S3`；中间数据接口详见 `docs/DRAWING_NUMBERING_INTERCHANGE.md`。模型方向整理必须预览、备份和显式确认，不能随导出自动执行。

19. [ ] **ETABS SQLite 数据本地归档（版本能力就绪后）**：待目标 ETABS 版本正式提供可用的 SQLite 数据访问或导出能力后，在每次梁表导出时将对应 SQLite 数据副本存放到本地归档目录，并写入模型路径、ETABS 版本、导出时间、单位和文件校验值。归档必须只读、不得替代当前 OAPI 读取主路径，也不得修改正在打开的模型。

### 🟢 紧急不重要

当前清单中没有这类事项——所有待办均源自内部工程质量把控，没有外部截止时间驱动的非核心任务。

### ⚪ 不重要不紧急 — 远景，明确不占用主线

20. [ ] ACI 318 自研配筋引擎
21. [ ] GB50010 或其他规范支持
22. [ ] SAP2000 双宿主支持
23. [ ] GUI、安装包和完整 CI/CD

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
