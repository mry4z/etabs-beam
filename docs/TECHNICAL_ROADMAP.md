# ETABS Beam Generator — 技术路径与实现方法规划

## Context（背景）

本项目为课题申报（`README.md` 标注目标：*基于视频素材进行 ETABS 梁结构建模与分析*）。素材 `beam gennerator.mp4` 演示了一款商用工具 **Quick Beam Design (P&T Group)** 的完整工作流：连接 ETABS → 选梁 → 提取几何/设计结果 → 连续梁链分析 → 剪扭配筋计算 → 导出 Excel 梁表。

`extracted_frames/` 下已沉淀四份分析资料（**本规划的输入依据，不含图片**）：
- `BEAM_GENERATOR_FEATURE_DOCUMENTATION.md` — 功能拆解（四阶段工作流、命名规范、输出格式）
- `ETABS_Beam_Generator_Code.py` — 从代码帧复现的**草图级**脚本
- `CODE_ANALYSIS_AND_RECONSTRUCTION.md` — 模块/算法/设计模式解析
- `QUICK_REFERENCE_AND_EXAMPLES.md` — API 用法与场景示例

**本规划要解决的问题**：把这些"逆向理解"转化为一条**工程可落地、技术可行**的实现路线，供课题申报与后续开发遵循。本次仅交付路线图文档，不编写实现代码。

### 关键环境事实（已核实）
| 项 | 状态 |
|----|------|
| ETABS 22 OAPI | ✅ `E:\Program Files\Computers and Structures\ETABS 22\ETABSv1.dll` + `CSI API ETABS v1.chm` 存在 |
| Python | ✅ 3.11.7；pywin32 / pandas / openpyxl 已装；`comtypes` 未装 |
| 复现脚本可运行性 | ❌ 草图级：`sys`/`math`/`n_legs`/`beams_by_story` 未定义，COM ProgID 写错（`CSI.ETABS.API.x64` 非法），仅表意不可执行 |
| 版本一致性 | ⚠️ README 路径 `F:\2026\课题申报` 与实际 `F:\2026\Research Study` 不符，需同步 |

### 已确认的方向决策
- **交付目标**：仅技术路线图文档（本文件）
- **API 绑定**：`pythonnet`（`clr` 加载 `ETABSv1.dll`）——CSI 官方 v22 推荐、类型安全、CHM 文档齐全
- **设计计算**：**优先读取 ETABS 自带混凝土梁设计结果**导出；自研 ACI 318-14/318-19 配筋工具作为**远景**，仅预留扩展接口

---

## 技术架构总览

采用**分层 + 策略预留**架构，将"读结果"与"自研算法"解耦，使远景 ACI 工具可无侵入接入。

```
┌──────────────────────────────────────────────┐
│  表现层 (可选远景)  CLI / Tkinter GUI          │
├──────────────────────────────────────────────┤
│  编排层  BeamWorkflow                          │
│    连接→选梁→提取→(链分析)→设计→导出            │
├───────────────┬──────────────────┬────────────┤
│ 连接层        │ 提取层            │ 导出层      │
│ EtabsConnector│ GeometryExtractor │ ExcelExport │
│ (pythonnet)   │ DesignResultReader│             │
├───────────────┴──────────────────┴────────────┤
│  设计策略层 (Strategy, 预留)                    │
│    IDesignProvider ← 接口                       │
│      ├─ EtabsResultProvider  ← 阶段1 落地       │
│      └─ ACI318Provider       ← 远景扩展         │
└──────────────────────────────────────────────┘
```

**对应现有资料**：编排层≈复现脚本 `__main__`；连接层≈`ETABSBeamAutomation.connect_to_etabs`；提取层≈`get_frame_points`/`get_point_coord`；链分析≈`build_chain_top_story`；导出层≈`export_beam_schedule`。规划在此基础上**修正 API 细节 + 抽出设计策略接口**。

---

## 分阶段技术路径

### 阶段 0 — 环境与项目基线（0.5 天）
- `pip install pythonnet comtypes`（comtypes 仅作降级备选）
- 初始化 git 仓库（当前目录非 repo），建立 `src/`、`docs/`、`tests/` 结构
- 修正 `README.md` 路径不一致（`课题申报`→`Research Study`）
- 建 `config.py`：集中管理 DLL 路径 `ETABS_DLL = r"E:\Program Files\Computers and Structures\ETABS 22\ETABSv1.dll"`

### 阶段 1 — ETABS 连接层（pythonnet）★核心可行性
**目标**：稳定 attach 到运行中的 ETABS 22 实例并取得 `SapModel`。
**技术方法**（替换复现脚本中错误的 `win32.GetActiveObject("CSI.ETABS.API.x64")`）：
```
import clr
clr.AddReference(ETABS_DLL)          # 加载 ETABSv1.dll
from ETABSv1 import cOAPI, cSapModel, cHelper, Helper
helper = cHelper(Helper())
etabs  = helper.GetObject("CSI.ETABS.API.ETABSObject")  # 正确 ProgID
sap    = etabs.SapModel
```
**验证点**：打印 `sap.GetModelFilename()`、`sap.GetPresentUnits()` 确认 attach 成功。
**风险**：pythonnet 与 .NET Framework/Core 运行时匹配；attach 失败时回退 `helper.CreateObject` 或 comtypes。

### 阶段 2 — 选择与几何提取层
**沿用**复现脚本中已基本正确的 OAPI 调用族，修正签名（OAPI 返回 `(ret, ...)` 元组）：
- 取当前选择：`sap.SelectObj.GetSelected()` → 过滤 `ObjectType==2`(Frame)
- 楼层标签：`sap.FrameObj.GetLabelFromName(name)` → `(label, story, ret)`
- 端点：`sap.FrameObj.GetPoints(name)` → `(p1, p2, ret)`
- 坐标：`sap.PointObj.GetCoordCartesian(pt)` → `(x, y, z, ret)`（注意：复现里的 `Point.GetCoordinates` 名称不准）
**复用**：`unique_keep_order()`、按 story 分组字典结构——现有资料中这两处逻辑正确，可直接采纳。

### 阶段 3 — 连续梁链分析
**采纳**现有 `build_chain_top_story` 的算法骨架（processed 集合 + 前向扩展），**补全**被留空的 `find_continuous_frame`：
- 判据（现有资料已列出，正确）：端点共点(容差~1mm) + 共线(斜率≤`plan_slope_limit`) + 同 story
- 复用现有 `is_beams_continuous` 片段的距离容差思路
- 产出 `build_neighbor_continuity_map_for_frames` 的左右邻接映射（数据结构已在资料中定义好）

### 阶段 4 — 设计结果读取层（★阶段1配筋主路径）
**技术方法**：调用 ETABS 混凝土框架设计结果 API，而非自研公式：
- `sap.DesignConcrete.GetSummaryResultsBeam(name, ...)` → 取 top/bot 纵筋面积、箍筋 Av/s、剪力等
- 需先确保模型已 `RunAnalysis` + `StartDesign`；否则提示用户先在 ETABS 内完成设计
**这一步直接替代**复现脚本第 5 节 `ConcreteBeamDesign` 的手算逻辑（其 `calculate_req`/`combine_shear_torsion_leg_areas_per_station` 保留为远景 ACI 策略的雏形，不在阶段1启用）。

### 阶段 5 — 设计策略接口（为远景预留）
定义抽象接口，使读结果与自研算法可切换：
```
class IDesignProvider(Protocol):
    def get_beam_design(self, sap, beam_name) -> BeamDesignResult: ...

class EtabsResultProvider(IDesignProvider):   # 阶段1落地
    # 内部调用 DesignConcrete.GetSummaryResultsBeam

class ACI318Provider(IDesignProvider):        # 远景，仅留空壳+TODO
    # 迁移 ConcreteBeamDesign 手算逻辑；支持 318-14 / 318-19 切换
```
`BeamDesignResult` 为统一 dataclass（纵筋、箍筋、剪扭、跨度、混凝土等级），下游导出层只认它，与来源无关。

### 阶段 6 — Excel 导出层
**采纳**现有 `export_beam_schedule` 结构（pandas + openpyxl），改进：
- 输出文件名对齐视频：`Concrete_Beam_Design_ACI318_19.xlsx`（现有资料中误写为 `AC319`，需修正）
- 增列：连续性(左/右邻接)、跨度、支座反力
- 文件被占用时回退加时间戳文件名（现有资料已有此容错思路，采纳）

---

## 关键文件规划（未来实现时）

| 文件 | 职责 | 来源/复用 |
|------|------|-----------|
| `src/config.py` | DLL 路径、单位、规范开关 | 新增 |
| `src/etabs_connector.py` | pythonnet attach（阶段1） | 修正复现 `connect_to_etabs` |
| `src/geometry_extractor.py` | 选择/楼层/端点/坐标 | 复用阶段2 OAPI 族 |
| `src/continuity.py` | 梁链+邻接映射 | 复用 `build_chain_top_story` 骨架 |
| `src/design/provider.py` | `IDesignProvider` 接口 + 两实现 | 阶段5新增 |
| `src/excel_export.py` | 梁表导出 | 复用 `export_beam_schedule` |
| `src/workflow.py` | 编排 | 复用 `__main__` 流程 |

---

## 验证方法（端到端）

1. **连接冒烟测试**：ETABS 22 打开任一含梁模型 → 运行连接层 → 断言取到 `SapModel` 且 `GetModelFilename()` 非空。
2. **提取正确性**：人工在 ETABS 选 3 根梁 → 脚本读到的 story/端点/坐标与 ETABS 界面逐一核对。
3. **链分析**：构造一排 3 跨连续梁 → 断言输出为单链 `[B-1,B-2,B-3]`；构造孤立梁 → 断言单元素链。
4. **设计结果**：模型完成设计后读取某梁 → 与 ETABS `Display > Design results` 数值比对。
5. **导出**：生成 `.xlsx` → 打开确认列齐全、编码正常（中文/单位符号）。
6. **回归**：无 ETABS 运行时，连接层应给出清晰错误提示而非崩溃。

---

## 范围与风险

**本次交付**：仅本路线图文档。**不含**任何实现代码编写、依赖安装或文件改动。

**主要风险**：
- pythonnet 运行时匹配问题 → 阶段1优先验证，备选 comtypes。
- 设计结果 API 要求模型预先完成分析+设计 → 编排层需前置检查并提示。
- CHM 中 v22 方法签名可能与旧版差异 → 以 `CSI API ETABS v1.chm` 为准，不照搬网络旧例。

**远景扩展**：ACI 318-14/318-19 自研配筋（`ACI318Provider`），届时迁移现有 `ConcreteBeamDesign` 手算逻辑并补全规范系数。

**图纸协同远景**：先将逻辑梁编号、源 Object、路径坐标、中点和局部 1 轴方向输出为 CAD 无关的 JSON/CSV 中间数据，再由 CAD 插件负责文字放置。逻辑编号采用 `[楼层]-[PB/SB]-[跨编号]`，跨编号为不带 `S` 前缀的纯数字；局部坐标整理仍是模型侧前置步骤，必须使 ETABS I→J、梁表左/中/右和图纸文字方向统一。具体规则见 `DRAWING_NUMBERING_INTERCHANGE.md`。

另预留 **ETABS SQLite 数据本地归档**：待目标 ETABS 版本提供稳定、可授权使用的 SQLite 数据访问或导出能力后，导出流程可将模型对应的 SQLite 数据副本保存至本地，并记录模型路径、软件版本、时间、单位和校验值。该能力只读归档，不替代 OAPI 主读取路径，也不得写入或改变正在打开的 ETABS 模型。
