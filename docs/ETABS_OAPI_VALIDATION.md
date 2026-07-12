# ETABS OAPI 实测用法与验证记录

**记录日期**：2026-07-12  
**适用实现**：当前 `src/` MVP 代码  
**验证方式**：对已打开 ETABS 模型的只读 OAPI 调用；未修改或保存模型

---

## 1. 目的与结论

本记录固化开发中已验证的 ETABS OAPI 调用方式，特别说明 `pythonnet 3.x` 下与一般 Python 调用不同的 `out/ref` 参数、活动实例附着规则及排查方式。

已在 `E:\051 SAP_ETABS MODEL\beam\bm.EDB` 上完成闭环验证：连接、梁识别、几何提取、连续梁分组、混凝土梁设计摘要读取与 Excel 导出均成功。

---

## 2. 已验证环境

| 项目 | 实测值 |
|---|---|
| Python | 3.11.7 x64 |
| pythonnet | 3.1.0 |
| ETABS OAPI DLL | `E:\Program Files\Computers and Structures\ETABS 22\ETABSv1.dll` |
| 当前模型 | `E:\051 SAP_ETABS MODEL\beam\bm.EDB` |
| 模型当前单位 | `N_mm_C`（`SapModel.GetPresentUnits()` 返回） |
| 导出单位策略 | 记录原单位 → 临时固定 `N_mm_C` → 导出后恢复原单位 |
| 连接方式 | `cHelper.GetObject("CSI.ETABS.API.ETABSObject")` |

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

当前 MVP 读取的是 ETABS 自带混凝土设计结果，不执行自研配筋计算。

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

针对当前复杂模型 `EMHS-0606-P4700-GF.v6.2.EDB`，工作流先读取原始 ETABS 梁对象，再按同楼层、同截面、端点相接和夹角不超过 `3°` 的规则合并简单路径梁段；分支或环不会自动合并。结果如下：

| 检查项 | 结果 |
|---|---:|
| 原始梁对象 | 4,217 |
| 合并后逻辑梁 | 1,754 |
| 设计站点 | 50,925 |
| 梁段合并记录 | 803 |
| 设计结果异常记录 | 65 |
| 输出工作表 | `梁表`、`设计站点`、`导出说明`、`异常记录` |

本次输出文件为 `build/output/reports/beam_schedule_20260713_000839.xlsx`。`梁表`保留 `原始梁对象` 映射列；`异常记录`中保留 65 条“ETABS 返回空设计数组”的设计结果异常，供后续确认模型设计结果覆盖情况。此次操作仅读取 ETABS 并切换/恢复当前显示单位，没有修改模型对象。

---

## 7. 当前限制与后续验证

1. 导出期间统一为 `N_mm_C`，结束后恢复 ETABS 原始当前单位；当前不改变模型物理数据，也不支持任意目标单位的可配置换算。
2. 当前“all”模式通过 `eFrameDesignOrientation.Beam` 过滤梁；“selected”模式仅读取 ETABS 当前选中的梁。
3. 连续性使用同楼层、端点距离容差与平面夹角判据；复杂分叉节点的工程规则仍需补充验收样本。
4. ACI 318 自研配筋、SAP2000 支持、GUI、打包和 CI/CD 均未纳入当前 MVP。

---

**维护约定**：每次升级 ETABS、pythonnet 或更换 OAPI 调用方式后，均应重新执行本文件第 3～6 节的冒烟验证并记录结果。
