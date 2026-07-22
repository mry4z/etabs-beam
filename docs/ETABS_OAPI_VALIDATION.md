# ETABS OAPI 实测用法与验证记录

**首次记录日期**：2026-07-12；**最近更新**：2026-07-20
**适用实现**：当前 `src/` 梁线/分跨、ETABS 22/23 设计结果、配筋评审及模型副本写回事务
**验证方式**：本文按日期保留从只读 OAPI 到工作副本写回的演进证据；早期章节的“只读/未修改”只描述当次试验，不代表当前能力仍停留在只读阶段

---

## 1. 目的与结论

本记录固化开发中已验证的 ETABS OAPI 调用方式，特别说明 `pythonnet 3.x` 下与一般 Python 调用不同的 `out/ref` 参数、活动实例附着规则、ETABS 22/23 设计结果以及模型副本写回事务。

已在 `E:\051 SAP_ETABS MODEL\beam\bm.EDB` 上完成闭环验证：连接、梁识别、几何提取、连续梁分组、混凝土梁设计摘要读取与 Excel 导出均成功。

此后又在 ETABS 23.3.1 工作副本上完成 I/J 反向、Unique Name 两阶段改名、GUID/连接点重开校验、重新分析设计及 DXF/Excel 输出。因此“通过 API 修改 ETABS 模型”目前是**已验证工程原型**，不再是“尚未开发”；但生产使用仍受拓扑阻塞对象、附属属性等价校验和回滚验收约束。

---

## 2. 已验证环境

| 项目 | 实测值 |
|---|---|
| Python | 3.11.7 x64 |
| pythonnet | 3.1.0 |
| ETABS OAPI DLL | 初始验证：`E:\Program Files\Computers and Structures\ETABS 22\ETABSv1.dll`；当前 ETABS 23 主路径：`E:\Program Files\Computers and Structures\ETABS 23\ETABSv1.dll` |
| 初始最小验证模型 | `E:\051 SAP_ETABS MODEL\beam\bm.EDB` |
| 当前复杂模型验证 | `EMHS-GF.v6.4.1.EDB`、`EMHS-GF.v6.4.3.EDB` 及带时间戳工作副本 |
| 模型当前单位 | `N_mm_C`（`SapModel.GetPresentUnits()` 返回） |
| 导出单位策略 | 记录原单位 → 临时固定 `N_mm_C` → 导出后恢复原单位 |
| 连接方式 | 基础入口使用 `cHelper.GetObject(...)`；多实例验证脚本使用 `GetObjectProcess(ProgID, PID)` 并校验模型绝对路径 |

> 应让 Python、ETABS 与 OAPI DLL 均使用 x64；DLL 版本应与实际宿主版本匹配。机器上安装多版 ETABS 时，公共 ProgID 的注册通常指向最后注册的版本，必须以实际连接验证为准。

---

## 3. 连接活动 ETABS 实例

```python
import clr

clr.AddReference(r"E:\Program Files\Computers and Structures\ETABS 22\ETABSv1.dll")
from ETABSv1 import Helper, cHelper

helper = cHelper(Helper())
app = helper.GetObject("CSI.ETABS.API.ETABSObject")
sap = app.SapModel

model_path = sap.GetModelFilename(True)
```

### 非常规点 1：`GetModelFilename` 不返回状态元组

多数 OAPI 方法经 pythonnet 调用后返回 `(ret, out_1, out_2, ...)`，但 `GetModelFilename(True)` 直接返回字符串。不要将其按带 `ret` 的方法拆包。

### 非常规点 2：多实例时附着对象不可指定

`GetObject` 只能获取当前注册的活动实例，不能按模型文件名选择实例。

本次验证初期，API 曾附着到另一份已打开模型：

```text
E:\051 SAP_ETABS MODEL\IRAQ-HOSPITAL\20260606\EMHS-0606-P4700-GF.v6.3.EDB
```

关闭该实例、仅保留 `bm.EDB` 后，才稳定附着到目标模型。因此，开始批量导出前应总是打印并人工确认 `model_path`。

### 非常规点 3：ETABS 与 Python 的权限级别必须一致

初次用普通权限 Python 进程附着时，`GetObject` 返回 `None`；以与 ETABS 相同的提升权限运行后附着成功。若再次发生，可按以下顺序处理：

1. 确保 ETABS 已打开并加载模型；
2. 关闭不需要的 ETABS 实例；
3. 让 ETABS 与运行脚本的终端保持同一权限级别；
4. 检查 `dll_path`、Python 与 ETABS 是否均为 x64。

---

## 4. pythonnet 3.x 的 `out/ref` 参数规则

ETABS 的 C# 接口大量使用 `ref` / `out` 参数。`pythonnet 3.x` 不会替所有参数自动补位；必须为这些参数提供初始值或 `None`。以下写法均已实测。

```python
from ETABSv1 import eFrameDesignOrientation, eItemType

# (ret, number_names, names)
ret, count, names = sap.FrameObj.GetNameList(0, None)

# (ret, number_selected, object_types, object_names)
ret, count, object_types, object_names = sap.SelectObj.GetSelected(0, None, None)

# (ret, orientation)
# 枚举 out 参数必须传对应 Enum，不能传整数 0。
ret, orientation = sap.FrameObj.GetDesignOrientation(
    frame_name, eFrameDesignOrientation.Beam
)

# (ret, label, story)
ret, label, story = sap.FrameObj.GetLabelFromName(frame_name, None, None)

# (ret, point_i, point_j)
ret, point_i, point_j = sap.FrameObj.GetPoints(frame_name, None, None)

# (ret, x, y, z)
ret, x, y, z = sap.PointObj.GetCoordCartesian(
    point_name, 0.0, 0.0, 0.0, "Global"
)

# (ret, property_name, auto_select_list)
ret, section, _ = sap.FrameObj.GetSection(frame_name, None, None)
```

### 为什么不能省略参数

例如，`sap.FrameObj.GetNameList()` 和 `sap.SelectObj.GetSelected()` 在当前 pythonnet 版本会报“没有匹配的方法”。`GetDesignOrientation(frame_name, 0)` 也会因 `int` 不能隐式转换为 .NET 枚举而失败。

当前实现通过 [geometry_extractor.py](../src/core/geometry_extractor.py) 统一封装上述调用；不应在其他模块重复简化写法。

---

## 5. 混凝土梁设计摘要

本节记录 2026-07-12 早期 MVP：当时只读取 ETABS 自带混凝土设计结果，不执行工程配筋排布。当前项目已经增加 ETABS 23 `_2` 字段适配和 ACI 318-14 配筋引擎，见后续实测章节；旧接口仍用于兼容基础梁表。

```python
from ETABSv1 import eItemType

result = sap.DesignConcrete.GetSummaryResultsBeam(
    frame_name,
    0,
    *([None] * 14),
    eItemType.Objects,
)
```

返回结果为：

```text
(ret, number_items, frame_name[], location[], top_combo[], top_area[],
 bot_combo[], bot_area[], vmajor_combo[], vmajor_area[], tl_combo[],
 tl_area[], tt_combo[], tt_area[], error_summary[], warning_summary[])
```

调用前提：模型应已完成分析和混凝土设计。当前实现将其标准化为 `BeamDesignResult` / `DesignStation`，从而令导出层不依赖 ETABS 原始数组。

### 错误码与设计提示的区分

- `ret == 0`：调用成功；
- 非零 `ret`：API 调用未取得可用数据，应记录上下文并跳过该梁；
- `error_summary[]` 有文字：API 调用成功，但这是 ETABS 给出的设计校核结果，不是程序异常，应原样写入 Excel。

例如，目标模型中首根梁的某站点有如下 ETABS 设计提示：

```text
Shear stress due to shear force and torsion together exceeds maximum allowed.
```

该提示已导出到 `设计站点` 工作表的“错误”列。

### 固定导出单位与恢复原单位

工作流不假定打开模型已经是 `N_mm_C`。在连接后，它会记录当前 `eUnits`，在导出范围内临时设置：

```python
with connector.temporary_present_units(eUnits.N_mm_C):
    # 提取几何、设计结果并导出 Excel
    ...
```

`temporary_present_units()` 通过 `try/finally` 恢复原始单位；即使提取或写入 Excel 失败，也会尝试恢复。该操作只切换 ETABS 当前显示/输入输出单位，不重新分析或重新设计模型。

真实会话已完成往返验证：

```text
before = N_mm_C
during = kN_m_C
after  = N_mm_C
```

---

## 6. 本次端到端验证证据

| 检查项 | 结果 |
|---|---:|
| 当前模型 | `bm.EDB` |
| 识别梁数 | 96 |
| 连续梁组数 | 32 |
| 导出设计站点数 | 5,568 |
| 首根梁 | 对象 `65`，标签 `B1`，楼层 `Story4`，截面 `ConcBm1` |
| 首根梁长度 | 18,000 mm |
| 首根梁设计站点 | 58 |
| 单元测试 | 17 / 17 通过，默认覆盖率 65.86%（60% 基线） |
| 自动化集成测试 | 1 / 1 通过；含代表性梁站点 API/Excel 数值比对 |

已生成验证文件：

- `build/output/reports/beam_schedule_20260712_223402.xlsx`
- `build/output/logs/etabs_beam_20260712_223401.log`

工作簿复核结果：`梁表` 96 行、`设计站点` 5,568 行，首根梁的设计站点数与 ETABS API 返回一致。

### 复杂模型逻辑梁合并试验（2026-07-13）

本节是 `EMHS-0606-P4700-GF.v6.2.EDB` 的早期 `3°`、同截面候选合并试验，结果仅用于说明算法演进；后续已经采用 `15°` 候选连接和跨截面 Beam Line 串接，且 Object `302` 修正再次改变拓扑，因此下表数量不得作为当前基线：

| 检查项 | 结果 |
|---|---:|
| 原始梁对象 | 4,217 |
| 合并后逻辑梁 | 1,754 |
| 设计站点 | 50,925 |
| 梁段合并记录 | 803 |
| 非混凝土设计跳过（唯一对象） | 51 |
| 混凝土设计结果异常记录 | 0 |
| 输出工作表 | `梁表`、`设计站点`、`导出说明`、`异常记录` |

本次输出文件（已按确认结果本地清理非混凝土设计异常）为 `build/output/reports/beam_schedule_20260713_00136.xlsx`。`梁表`保留 `原始梁对象` 映射列；钢截面/刚性板截面不进入混凝土设计统计，`异常记录`仅保留 803 条梁段合并映射。此次操作仅读取 ETABS 并切换/恢复当前显示单位，没有修改模型对象。

### 15°归并夹角验证（2026-07-13）

在相同模型、相同混凝土梁筛选规则下，`15°` 导出读取 4,166 根混凝土梁，得到 1,651 根逻辑梁，报告为 `build/output/reports/beam_schedule_20260713_131722.xlsx`。同时生成 `build/output/reports/merge_angle_3_vs_15_20260713_131921.csv`：其中列出相对 `3°` 新增的 51 个合并组。用户已确认 15° 可作为当前候选连接限值；生产冻结前仍需对长链、分叉和多个近似共线候选输出歧义诊断，防止参数本身掩盖误并。

为支持全量图形核对，新增无第三方 CAD 依赖的 ASCII DXF 导出器 `src/drawing/dxf_export.py`。它直接使用每个梁对象的 ETABS 点坐标：红色图层 `MERGE15_NEW` 绘制仅在 15° 下连接的梁段，蓝色图层 `MERGE3_GROUP` 绘制对应的 3° 原逻辑梁组，黄色/青色图层标注两级逻辑梁中点和映射编号。当前模型已生成 `build/output/drawing_data/merge_angle_3_vs_15_20260713_150825.dxf`（514 条线段、169 个文字/标记实体）及同名 `.csv` 坐标映射文件，可直接载入 AutoCAD/BricsCAD 进行全面复核；DXF 使用毫米平面坐标，不修改模型。

### 全部逻辑梁 CAD 复核（2026-07-13）

新增 `export_beam_groups_dxf`：对当前 `15°` 逻辑梁结果逐梁输出一个经典 `POLYLINE`，不再只显示夹角差异。主梁判定为源 Beam Object 的 I/J 点与柱对象共享节点，图层 `BEAM_PRIMARY` 使用黄色；未触及柱节点的逻辑梁判为次梁，图层 `BEAM_SECONDARY` 使用绿色。该历史阶段曾采用 `楼层-PB/SB序号-S1` 标注，`S` 前缀现已废止；当前统一格式为 `楼层-PB/SB序号-1`，例如 `F1-PB5-3`。文字颜色与梁线一致；同名 CSV 保存源对象和路径点坐标，便于 CAD 全量核对。

当前连接到的模型为 `E:\051 SAP_ETABS MODEL\IRAQ-HOSPITAL\20260606\EMHS-0606-P4700-GF.v6.3.edb`，读取 4,165 根混凝土梁并归并为 1,652 根逻辑梁，其中主梁 760、次梁 892；输出为 `build/output/drawing_data/beam_groups_full_15deg_20260713_160709.dxf` 及同名 `.csv`。此前 `beam` 目录下的 v6.2 模型与本次当前 ETABS 窗口路径不同，审核时应以 DXF 文件记录的 MODEL 图例为准。

### 支承定义结构分跨验证（2026-07-13）

API ID `278156` 当前附着模型为 `E:\051 SAP_ETABS MODEL\beam\EMHS-0606-P4700-GF.v6.2.EDB`。按楼层隔离后读取 4,166 根混凝土梁、1,421 个柱支承点，识别 1,705 条 Beam Line 和 3,023 个 Span：主梁 810 条/1,568 跨，次梁 895 条/1,455 跨，弧形梁线 35 条，悬挑跨 669 个。DXF 中 `POLYLINE` 数量为 3,023，每跨一条多段线；CSV 同步记录 `梁编号、楼层、主次梁、弧形、悬挑、源对象、路径点坐标`。

输出文件：`build/output/drawing_data/beam_spans_structural_20260713_194933.dxf` 和同名 `.csv`。该文件使用黄色 `BEAM_PRIMARY`、绿色 `BEAM_SECONDARY`，标注文字使用 `ARIAL` 且颜色跟随梁线。结构分跨分析已按楼层隔离；`story_workers` 可在不访问 ETABS 的纯几何阶段启用楼层并行，串行/并行单元测试结果一致。分析过程中产生 813 条合并/异常记录，目前尚未写入独立审核文件，需结合 CAD 图形审核后再接入正式 Excel。

### 跨截面 Beam Line 修正验证（2026-07-13）

首轮 CAD 复核发现，连续且共线的梁段会因截面变化被 `BeamMerger` 分成多个候选组，进而重新编号并造成假悬挑。修正后的 `beam_line_span_analyzer` 增加跨截面端点拓扑串接：截面仅保留为源 Object/Span 属性，梁线确定后再按柱或主梁支承分跨；同时 DXF 标注改为沿多段路径累计长度的真实中点，并采用中点所在梁段切线方向。

使用 API ID `278156`、模型 `E:\051 SAP_ETABS MODEL\beam\EMHS-0606-P4700-GF.v6.2.EDB` 重新验证：读取 4,166 根混凝土梁，识别 1,173 条 Beam Line、2,976 跨（PB 519 条/1,556 跨，SB 654 条/1,420 跨），records 中无警告或错误。源 Object 全部被覆盖；其中 38 个 Object 的实体几何跨越结构支承，因此在相邻 Span 中按实际贡献一对多列示。原 `GF-GRD-F-SB17`～`SB24` 已合并为一根 22 跨梁线，原 `T1-F2-SB65`、`SB70`、`SB75`、`SB85`、`SB88` 已合并为一根 6 跨梁线。输出文件为：

- `build/output/drawing_data/beam_spans_structural_stitched_20260713_211158.dxf`
- `build/output/drawing_data/beam_spans_structural_stitched_20260713_211158.csv`
- `build/output/drawing_data/beam_spans_structural_stitched_20260713_211158.records.txt`

离线回归测试为 `36 passed, 1 skipped`，覆盖率 `81.93%`。上述输出尚待用户进行 CAD 全量复核，复核通过后再接入正式 Excel 和模型方向整理流程。

### PB/SB 混合连续路径修正验证（2026-07-14）

针对 `GF-GRD-F-PB17` 的复核发现，单个连续候选路径可能同时包含柱支承的 PB 核心段和越过主梁交点后的 SB 延伸段。修正后的规则以路径累计位置计算首末柱支承核心区：核心区及其外侧至最近主梁交点保留 PB，越过该交点且无柱支承的区间转为 SB；角色边界不再依赖 ETABS 源 Object 的分段边界。该规则同时覆盖多柱连续路径、单柱连接路径和次梁只在主梁交点处分跨的场景。

使用 API ID `526520` 重新附着同一 v6.2 模型，原始/导出/恢复单位均为 `N_mm_C`。本次读取 4,166 根混凝土梁、1,421 个柱支承点，识别 1,203 条 Beam Line、3,068 个 Span：PB 1,616 跨、SB 1,452 跨；CSV 中 4,166 个源 Object 全部覆盖，records 共 1,124 条且无警告/错误。`GF-GRD-F-PB17` 的 PB 跨从 `(15272.900,58563.400)` 主梁交点开始，左侧至 `(-23727.100,58563.400)` 的区间转入 `GF-GRD-F-SB18`，与示意规则一致。

输出文件为：

- `build/output/drawing_data/beam_spans_structural_roles_20260714_221827.dxf`
- `build/output/drawing_data/beam_spans_structural_roles_20260714_221827.csv`
- `build/output/drawing_data/beam_spans_structural_roles_20260714_221827.records.txt`

离线回归测试为 `38 passed`，覆盖率 `80.47%`；DXF 仍待用户进行 CAD 全量复核，复核通过后再接入正式 Excel 和模型方向整理流程。

### 结构分跨接入 Excel 工作流（2026-07-16）

已将 `BeamLineSpanResult` 接入正式工作流。`excel_span_adapter.py` 负责把源 Object 的设计站点按完整梁线位置裁剪到每个 Span，Excel 导出器仅通过可选 `SpanScheduleMapping` 消费结果，因此后续编号原则变化不需要改动既有梁表列定义。原 `梁表` 新增“结构梁线编号”和“结构跨号”映射列，并新增“梁分跨”工作表；原 `设计站点` 工作表保持兼容。

使用 API ID `526520` 运行 `python -m src.workflow --selection all`，模型为 `E:\051 SAP_ETABS MODEL\beam\EMHS-0606-P4700-GF.v6.2.EDB`，原始和导出单位均为 `N_mm_C`。工作流读取 4,166 根混凝土梁和设计站点，结构分跨识别 1,203 条 Beam Line、3,068 跨（PB 1,616，SB 1,452），并输出 1,651 行既有梁表、50,720 行设计站点和 3,068 行梁分跨记录。实模 Excel 为：

- `build/output/reports/beam_schedule_20260716_000814.xlsx`

`GF-GRD-F-PB17`/`GF-GRD-F-SB18` 的 PB/SB 边界及左、中、右关键配筋字段已写入“梁分跨”表，后续可直接按跨核查。离线回归测试为 `39 passed`，覆盖率 `80.98%`。

### 模型写回工作副本验证（2026-07-16）

API ID `9948` 附着 ETABS 23.3.1，当前原始模型为 `E:\051 SAP_ETABS MODEL\beam\EMHS-0606-P4700-GF.v6.2.EDB`，当前单位为 `N_mm_C`。新增 `src/core/model_writeback.py`，将方向整理和改名拆成独立的 plan/apply/verify 事务，并以 GUID 建立稳定身份；跨 Span Object 自动标记 `REQUIRES_SPLIT`，不静默改名。

本次先创建工作副本，不直接修改原始 EDB：

- 工作副本：`build/output/model_writeback/EMHS-0606-P4700-GF.v6.2_writeback_20260716_130453.edb`
- 全量只读预览：`build/output/model_writeback/writeback_plan_599276.json`
- 样本验证记录：`build/output/model_writeback/writeback_sample_599276.json`

预览读取 4,166 个梁 Object、1,203 条梁线和 3,068 跨；141 个 Object 需要反向，4,084 个单 Span Object 可改名，166 个跨 Span 改名动作被阻塞。工作副本上执行 3 个 `EditFrame.ChangeConnectivity` 反向和 3 个“旧名→临时名→最终名”改名，保存并重开后连接点、最终名称和 GUID 均验证通过；改名后的三个对象仍返回 21/23/23 个混凝土设计站点。随后活动 ETABS 恢复到原始 EDB，原始文件 SHA-256 仍为 `596c11670081be0d61c601b7653bca173a13fc65d113c39b30b12b5430450876`。

副本分析输出文件记录 `ANALYSIS COMPLETE`，首次中断恢复时 `RunAnalysis()` OAPI 返回码为 `1`，`DesignConcrete.StartDesign()` 在等待窗口内未返回；但改名后的三个对象仍可读取设计结果。随后使用 API ID `9948` 完整复现“切换工作副本→运行分析→启动混凝土设计→保存副本→恢复原始模型”流程，分析和设计均返回码 `0`，副本保存成功，活动模型恢复为原始 EDB，自动化日志未捕获错误提示。完整复现耗时约 8 分 23 秒。离线回归测试为 `45 passed, 1 skipped`。下一步仍需记录 ETABS 前台提示截图，并在全量写回前完成属性校验。

### 全量模型副本写回验证（2026-07-16，API ID 31080）

本次从未修改的原始 EDB 新建工作副本 `build/output/model_writeback/EMHS-0606-P4700-GF.v6.2_writeback_20260716_155529.edb`，并在副本上执行全量方向/编号写回。扫描得到 4,166 个梁 Object、1,203 条梁线和 3,068 跨；146 个 Object 需要反向，4,084 个单 Span Object 完成两阶段改名，166 个动作（82 个 Object）因一个 Object 贡献多个 Span 被保留并记录为 `REQUIRES_SPLIT`。方向首轮连接校验 4,166/4,166 通过，改名重开后的 GUID 校验 4,084/4,084 通过。

原始 JSON `build/output/model_writeback/writeback_full_20260716_155721.json` 中的 `orientation_reopen_verify.ok=false` 不表示写回失败：该次校验在改名后仍用旧的数字 Unique Name 调用 `GetPoints`，ETABS 返回“对象不存在”（错误码 1）。已将 `EtabsWriteback.verify_orientation` 增加可选 `name_resolver`，用 GUID 对应的最终名称查询，并用单元测试固定该行为。工作副本当前保持打开，原始 EDB SHA-256 未改变；全量附属属性等价校验和 82 个跨 Span Object 的分割仍是完成该待办前的必要工作。

随后使用临时只读验证副本，将每个当前 Object 的 `GetPoints()` 端点投影到其重建的逻辑梁线路径，比较 I→J 的累计路径位置与图纸文字方向。4,166 个梁 Object 共形成 4,250 个 Object→梁线映射，4,250/4,250 沿路径正向，反向数为 0，脱离路径数为 0；结果见 `build/output/model_writeback/orientation_vs_text_direction_20260716_164057.json`。因此当前写回副本的 ETABS 局部 1 轴方向与预设文字方向一致。跨多个 Span 的 Object 仅按梁线方向校验，仍保留 `REQUIRES_SPLIT` 约束。

### ETABS 23 写回副本重新打开与 AsFlex 验证（2026-07-17）

使用 ETABS 23.3.1 打开并按 PID 50772 附加以下副本：

`F:\2026\Research Study\etabs beam\build\output\model_writeback\EMHS-0606-P4700-GF.v6.2_writeback_20260716_155529.EDB`

只读确认结果：模型路径完全匹配，当前单位为 `N_mm_C`，`DesignConcrete.GetResultsAvailable=True`。四个已回写 Unique Name 均能通过 `GetSummaryResultsBeam_2` 返回设计站点，说明改名后设计结果仍可读取：

| 回写 Unique Name | 站点数 | 最大 AsTotal 顶/底 mm² | 最大 Al mm² | 最大 AsFlex 顶/底 mm²（按站点计算） |
|---|---:|---:|---:|---:|
| `T2-F1-PB19-2.1`（原 149） | 25 | 6852.922 / 4821.440 | 1382.572 | 6161.636 / 4142.434 |
| `T1-F1-PB21-1.1`（原 2084） | 15 | 6011.634 / 795.178 | 1011.849 | 5505.709 / 289.254 |
| `T2-F1-PB21-1.1`（原 273） | 25 | 4919.555 / 2622.561 | 1212.032 | 4313.539 / 2016.545 |
| `T2-F1-PB21-2.1`（原 150） | 27 | 5962.478 / 3204.849 | 1268.933 | 5328.012 / 2607.879 |

`AsFlex` 按每个 Station 计算：`AsTotal - Al/2`，没有将不同 Station 的最大值拼接。前三个离线梁的高配筋位置均可在两层 T25 容量内完成；本次样本未触发抗扭纵筋向 face bar 转移。验证脚本为 `build/output/reports/verify_writeback_design_etabs23.py`，未修改模型、未运行分析或设计。

### ETABS 23 v6.4.3 模型基准验证（2026-07-17）

使用 ETABS 23.3.1（PID `37800`）打开并附加以下模型，验证脚本为
`build/output/reports/validate_v643_etabs23.py`：

`F:\2026\Research Study\etabs beam\build\output\model_writeback\EMHS-GF.v6.4.3.EDB`

本次仅执行只读 API 调用，没有运行分析/设计，也没有保存或修改模型。

| 检查项 | 结果 |
|---|---:|
| 模型路径匹配 | 通过 |
| 当前单位 | `N_mm_C` |
| 模型锁定 | 否（`GetModelIsLocked=False`） |
| 混凝土设计规范 | `ACI 318-14` |
| 混凝土设计结果 | 不可用（`GetResultsAvailable=False`） |
| Frame Object 总数 | 7,149 |
| 梁设计方向对象 | 4,217 |
| 柱设计方向对象 | 1,082 |
| 其他方向对象 | 1,850 |
| I/J 连接点读取 | 7,149 / 7,149 |
| GUID 读取 | 7,149 / 7,149 |
| 逻辑编号格式对象 | 0 |
| 数字 Unique Name 对象 | 7,149 |

代表性对象仍使用数字 Unique Name（如 `1`、`2`），梁 Label/Story 和截面可正常读取；因此该文件不能作为“逻辑梁编号写回已完成”或设计结果字段（`AsTotal`、`AsProvided`、`TLArea` 等）的验收样本。下一步如需验证配筋 API，必须在副本上完成分析与 ACI 318-14 混凝土设计后重新执行；不得把 `GetResultsAvailable=False` 解释为设计 API 字段为空或零。

### v6.4.3 副本分析、设计与结果持久化验证（2026-07-17）

为完成设计字段验证，将上述 EDB 复制为：

`build/output/model_writeback/EMHS-GF.v6.4.3_validation_20260717_161023.EDB`

原文件未写入（原文件 SHA-256：`2EA9CD0CB3C14F246C6CF37D859ED8A7DDEC144C1858A5855E3BA74FA0F71A0E`）。在 ETABS 23.3.1 副本（PID `28116`）上执行 `RunAnalysis()` 和 `DesignConcrete.StartDesign()`，返回码均为 `0`；保存并重开副本后路径仍匹配、单位为 `N_mm_C`、`GetResultsAvailable=True`。

分析日志显示，`BEGIN ANALYSIS` 为 `16:16:07`，`ANALYSIS COMPLETE` 为 `16:20:40`，实际求解耗时约 4 分 33 秒；脚本开始调用 `RunAnalysis()` 为 `16:13:40`，因此约 2 分 27 秒属于分析模型创建/准备阶段。后续性能优化应分别统计这两个阶段，不能把全部 OAPI 阻塞时间归因于求解器。

`GetSummaryResultsBeam_2` 全量扫描 4,217 个梁对象，API 状态 `0` 的对象为 4,217 个，其中 4,167 个返回至少一个设计站点。按 `AsFlex=AsTotal-Al/2` 计算，Flex 控制值最大的三个对象及其控制组合/站点为：

| Object | 站点数 | Top AsTotal / 组合 / 站点 | Bot AsTotal / 组合 / 站点 | TLArea 峰值 | TTArea 峰值 | VMajorArea 峰值 | 最大 AsFlex |
|---|---:|---|---|---:|---:|---:|---:|
| `149` | 25 | 6900.226 / `U5 SPEC YX` / 8300.0 | 4819.839 / `T1-1` / 4833.333 | 1382.538 | 0.543 | 3.531 | 6208.957（顶，8300.0） |
| `2084` | 15 | 6015.063 / `T1` / 2813.4 | 798.622 / `T1-1` / 2813.4 | 1011.855 | 0.495 | 1.777 | 5509.135（顶，2813.4） |
| `150` | 27 | 6001.963 / `U5 SPEC YX` / 8300.0 | 3209.804 / `U5 SPEC YX` / 8300.0 | 1272.451 | 0.567 | 2.941 | 5365.738（顶，8300.0） |

本次同时确认 `TopAreaProvided/BotAreaProvided` 在这些控制站点返回 `-1`（ETABS 未提供实配面积，不应当当作零）；`VMajorArea`、`TLArea` 和 `TTArea` 均返回非零数值。详细扫描脚本为 `build/output/reports/inspect_v643_design_results_etabs23.py`，分析/设计脚本为 `build/output/reports/run_design_copy_etabs23.py`。

### v6.4.1 全链路原型及 Object 302 拓扑纠正（2026-07-18）

以未修改原模型 `build/output/model_writeback/EMHS-GF.v6.4.1.EDB` 创建工作副本并完成分析、ACI 318-14 混凝土设计、I/J 整理、Unique Name 写回、重开验证、DXF 和三表 Excel。旧算法当次读取 4,166 根混凝土梁，形成 1,205 条 Beam Line、3,070 个 Span；146 个 Object 反向，4,086 个 Object 改名成功，80 个源 Object（162 个改名动作）因一对多 Span 映射被阻塞。分析和设计 API 均返回 `0`，设计结果可重新读取。这证明完整写回流程可以运行，但不证明当次拓扑划分正确。

用户随后在 API ID `54204` 的模型中复核 Object `302`：其真实端点 `698→696` 均为柱点，7800 mm 杆件中间点 `2160` 仅连接两根梁，没有柱。旧算法把任意梁交点错误地当作 PB 支承，因而把 Object `302` 同时映射到 `T1-F1-PB9-3` 和下一跨。规则修正为：

- PB 只在真实柱、经 `rigid` 传播的等效柱或人工确认竖向支承处分跨；PB/SB 梁交点不增加 PB 跨数；
- SB 只在 PB 支承处分跨；SB—SB 相交不增加跨数；
- 任何无共点的 XY 交点还必须沿两条三维杆件分别插值 Z，只有高差在容差内才建立连接。

只读重算后得到 3,010 个 Span，Object `302` 只贡献 `T1-F1-PB9-3`，`REQUIRES_SPLIT` 从 80 个源 Object 降至 24 个。离线回归测试为 `57 passed, 1 skipped`，覆盖率 `80.51%`。

**成果失效声明**：旧 3,070 跨 v6.4.1 写回副本/DXF/Excel，以及旧 3,074 跨 v6.4.3 配筋 Excel，均基于修正前拓扑，只能保留为 API、配筋引擎或文件格式的历史验证证据，不得作为梁编号、分跨或最终配筋验收件。后续必须从未修改原模型按 3,010 跨新结果重跑完整链路。

### v6.4.1 P0-1/P0-2 正式 CLI 重放（2026-07-20）

从未修改的 `build/output/model_writeback/EMHS-GF.v6.4.1.EDB` 创建全新副本
`EMHS-GF.v6.4.1_writeback_20260720_001559.edb`。源文件和副本创建时 SHA-256 均为
`8e18212e0b9595f87a7cdffe5552832dbecb5860423627eeaccf3b7a6381437e`。正式入口为
`python -m src.cli`，通过 `GetObjectProcess` 按 PID 附着，并以绝对路径校验模型；ETABS 保存后可能将
`GetModelFilename(True)` 报告为同目录同 stem 的 `.$et` sidecar，CLI 只接受这种等价路径，不接受其他模型。

只读基准读取 4,166 个梁 Object、1,202 条 Beam Line 和 3,009 个 Span（PB 1,568、SB 1,441），
24 个 Object/48 个动作进入 `REQUIRES_SPLIT`。未指定 `--allow-partial-writeback` 时，写回门禁按预期拒绝。
显式允许安全对象后，方向校验、两阶段改名、改名后方向校验均通过；`RunAnalysis()`、
`StartDesign()`、`File.Save()` 返回码均为 0。分析日志显示负特征值为 0，并出现 `ANALYSIS COMPLETE`。

保存后只读重读仍获得相同 Object/梁线/Span 数量，已写回名称可被重新提取；保存时单位已在临时
`N_mm_C` 上下文退出后执行，因此副本和活动 ETABS 单位仍为原始 `lb_in_F`。随后 `--export-excel`
通过 `GetSummaryResultsBeam_2` 生成 `beam_schedule_20260720_003447.xlsx`，包含梁表 4,166 行、
设计站点 66,180 行和梁分跨 3,009 行。

证据目录：`build/output/topology_p0_20260720_baseline/`、
`build/output/topology_p0_20260720_writeback_design/`、
`build/output/topology_p0_20260720_postverify/`、
`build/output/topology_p0_20260720_excel/`。Object `302` 在本次 CSV 中只贡献
`T1-F1-PB9-3`；24 个跨 Span Object 仍被阻塞，没有自动重建或拆分 Frame。

### v6.4.3 内部柱支承与拓扑 fail-closed 门禁（2026-07-21）

只读连接 PID `40352` 当前模型
`E:\051 SAP_ETABS MODEL\IRAQ-HOSPITAL\20260606\EMHS-GF.v6.4.3.EDB`。该文件与 2026-07-17 的本地验证副本哈希不同，不能直接比较旧 `1,209/3,074` 统计。当前原始单位为 `N_mm_C`；全部测试运行后仍为 `N_mm_C`，未保存或修改模型。

ETABS 23 `FrameObj.GetAllFrames` 一次返回 7,091 个 Frame 的 Unique Name、截面、楼层、I/J 点名和两端全局坐标，实测单次约 `0.085 s`。`GeometryExtractor` 现在复用该快照并缓存设计方向、截面材料和梁高；4,110 根混凝土梁及 1,441 个真实柱/`rigid` 等效支承完整读取约 `4.6 s`。相比逐 Object 重复调用 `GetPoints/GetSection/GetCoordCartesian`，消除了数万次 COM 往返。写回状态也复用该几何快照，仅逐 Object 补读无法批量取得的 GUID。

最终只读 CLI 结果：

| 项目 | 结果 |
|---|---:|
| 混凝土 Beam Object | 4,110 |
| Beam Line | 1,215 |
| Span | 3,020（PB 1,573；SB 1,447） |
| 源 Object 覆盖 | 4,110 / 4,110 |
| `REQUIRES_SPLIT` | 29 个 Object / 58 个动作 |
| 15° 长链 INFO | 54 |
| PB/SB 固定点收敛 INFO | 13 |
| BLOCKED 拓扑诊断 | 0 |
| 完整 CLI 耗时 | 67.5 s |

新增门禁包括：内部柱点可落在长 Beam Object 中部；PB 内部边界必须是柱/等效柱；SB 内部边界必须落在当前稳定 PB 路径；多候选/分叉/闭环及源 Object 丢失均 BLOCKED。PB/SB 支承角色迭代至固定点，循环或 6 轮未收敛即阻塞。实模中 `T1-L1-SB18` 原先保留了已经消失的临时 PB 交点 `(3572.9,58563.4,5050)`；修正后该点仅作为路径折点，不再分跨，SB18 从 4 跨变为 3 跨。

成果目录：`build/output/topology_p01_20260721_final3/`，包含完整 DXF/CSV、跨 Span Object JSON/CSV/DXF、拓扑诊断 JSON 和摘要。

### ETABS 22/23 兼容边界与 P0 全链路关闭（2026-07-22）

正式支持范围扩大为 ETABS 22.7.0 和 23.3.1。业务层不按版本分叉；版本差异由连接/能力边界处理：

| 项目 | ETABS 22.7 | ETABS 23.3 | 项目处理 |
|---|---|---|---|
| `SapModel.GetVersion` | pythonnet 实测需要 `GetVersion("", 0.0)` 初始化 `ref` 参数 | 部分安装可直接无参，兼容调用仍可使用 ref 形式 | `etabs_version_info()` 先无参、遇 `TypeError` 再使用 ref 形式 |
| `GetSummaryResultsBeam_2` | 已实测可用，返回 Total/Min/Provided 等 24 组输出 | 已实测可用 | 共用 `EtabsResultProvider.get_beam_design_total()` |
| `EditFrame.ChangeConnectivity`、`FrameObj.ChangeName` | 已完成全量写回、回滚、保存重开 | 早期副本验证已通过 | 共用同一事务实现 |
| 显示表字段/设计覆盖表名 | 随设计规范和安装版本可能变化 | 同样可能变化 | 读取前检查字段名；缺字段时 fail-closed，不用主版本猜测 |
| API DLL | `E:\Program Files\Computers and Structures\ETABS 22\ETABSv1.dll` | `E:\Program Files\Computers and Structures\ETABS 23\ETABSv1.dll` | 必须加载与宿主主版本一致的 x64 DLL；manifest 记录 DLL 版本和 SHA-256 |

ETABS 22.7.0、PID `40352` 上，从未修改源模型
`E:\051 SAP_ETABS MODEL\IRAQ-HOSPITAL\20260606\EMHS-GF.v6.4.3.EDB`
创建工作区副本。源文件 SHA-256 在全部验证后仍为
`6be0a9755a4d615d76916daa4537560357fc543a482d2c1c9b0ad0952a83c9fc`。
最终 run `20260722T083439Z-862d6985` 得到 4,110 个混凝土 Beam Object、1,215 条 Beam Line、
3,020 个 Span；分析、混凝土设计和保存返回码均为 0，拓扑 BLOCKED=0。

全属性审计比较 GUID 集合、I/J、变换矩阵、截面/材料、材料覆盖、释放、端部偏移、分组、铰、弹簧、
分布/集中/温度荷载和 ACI 318-14 混凝土设计覆盖项。I/J 交换时端部偏移长度及 I/J 释放按物理端同步交换；
Program Determined 覆盖项只比较“仍为程序确定”的语义，不把分析后重算的数值误报为用户覆盖变化。最终模型保存重开后
4,110/4,110 对象零差异。

方向反转需求等价检查采用按 Object/站点需求包络比较，允许站点顺序反转。6 个 Object（`52`、`56`、`57`、
`111`、`453`、`1178`）的 `VMajorArea` 同时超过 2% 相对容差和 0.05 mm²/mm 绝对容差，已按 fail-closed
同时阻止方向和改名；其余安全对象通过。29 个跨 Span Object 和 38 个未确认多截面 Span 同样继续阻塞。

首次重复计划发现 GF-FG 曲线梁的固定点分类依赖 I/J。根因是 PB 支承迭代未把路径集合完全规范化，且细分后的本梁子段
可能在曲线折点被当成外部 PB 交点。修复后虚拟写回前/后拓扑差异为 0；纠正旧名称并保存重开后，再次构造计划得到
方向动作 0、改名动作 0。联合证据为
`build/output/p0_complete_v643_20260722_final3/writeback_reopen_and_idempotency.json`。

---

## 7. 当前限制与后续验证

1. 导出期间统一为 `N_mm_C`，结束后恢复 ETABS 原始当前单位；当前不改变模型物理数据，也不支持任意目标单位的可配置换算。
2. 当前“all”模式通过 `eFrameDesignOrientation.Beam` 过滤梁；“selected”模式仅读取 ETABS 当前选中的梁。
3. `rigid` 辅助杆件已从混凝土梁表排除，锚定刚域到真实柱支承的传播及三维坐标已接入正式只读提取；未锚定链不产生柱支承。
4. 无共同 ETABS 节点的梁交点必须补齐 Z 高程校验；同一 story 不能替代三维相交判定。
5. `REQUIRES_SPLIT` 数量取决于模型文件和算法版本：v6.4.1 基线为 24 个，2026-07-21 当前 v6.4.3 为 29 个；均逐个输出 GUID、坐标、端点、候选 Span 和诊断 DXF。在安全分割与属性迁移完成前，仍不得强行选取一个 Span 改名。
6. ACI 318-14 配筋引擎及独立三表 Excel 已接入正式 CLI；尚需在 P1 闭环保护层语义、相邻跨协调和人工 override。
7. SAP2000 支持、正式 GUI/CAD 插件、SQLite 快照、打包和 CI/CD 仍未完成。

---

**维护约定**：每次升级 ETABS、pythonnet 或更换 OAPI 调用方式后，均应重新执行本文件第 3～6 节的冒烟验证并记录结果。
