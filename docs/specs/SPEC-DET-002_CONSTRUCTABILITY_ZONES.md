---
id: SPEC-DET-002
type: spec
status: review
owner: project
created: 2026-08-03
updated: 2026-09-10
related:
  - SPEC-DET-001_ACI318_14_DETAILING.md
  - SPEC-DESIGN-001_RESULT_SEMANTICS.md
  - ADR-0006
  - ADR-0008_ETABS_CAPACITY_SHEAR_PROCESS_ONLY
  - IP-P1-002
---

# SPEC-DET-002：梁构造区段与 SMF 审计输出

## 目的

配筋选择仍可读取 ETABS 左端、跨中和右端的需求，但这些读取点不等于施工时的箍筋区段。本规范定义第二层“按实际里程定位”的构造结果，避免把面积选择误读为完整构造设计。

## 已实现的领域对象

- `SupportFaceContext`：支承名称、沿梁里程、支承尺寸和来源。SMF 梁必须使用柱/支承的实际表面；SQLite 老快照没有截面尺寸时保持未解析，不猜测。
- `DetailingZone`：普通梁全跨、SMF 两端 `2h`、重叠合并区、可能屈服位置两侧的 `2h` 区和中间非加密区。
- `HoopPosition`：实际每道箍筋里程。首箍按距离支承面 50 mm 布置，区段边界和相邻道距再次检查。
- `RebarLayoutGeometry`：宽度、梁高、有效高度、箍筋直径、保护层和纵筋层数摘要；精确 x/z 坐标留给后续布筋优化。
- `RuleCheck`：规则号、条文、输入、限值、结果和来源。SMF 容量剪力可记录已验证的 ETABS 来源，或按两端 `Mpr` 和重力剪力形成可追溯包络；现有处理后梁表也可在明确采用项目 Mpr 系数（当前自动复核为 1.25）且端区 `Vc=0` 时，由 Provided/Av/s/梁跨反算 Vu；加密区明确采用 `Vc=0`，未完成工程确认时保留 `CAPACITY_SHEAR_SOURCE_UNVERIFIED`。

## 普通梁与 SMF 门禁

普通梁额外输出裂缝间距、抗扭周边布筋、锚固和搭接校核的“已确认/未确认”状态。当前项目阶段不把抗扭周边布筋、锚固和搭接作为梁表红字门禁；这些状态仍写入 JSON 审计，作为超远期工程复核待办，不等同于施工合格确认。

SMF 额外执行净跨/有效高度、梁宽、轴力（若提供）筛查，纵筋至少两根连续、项目最大配筋率 `0.01613` 和正负弯矩强度比例筛查。端部 `2h` 区在 ACI 上限外再叠加项目 `100 mm` 上限。`Sway Special` 标签本身不替代体系确认。项目已确认 ETABS Frame Design 输出的 `Design Force` 已考虑此前提及的抗震来源；因此 `CAPACITY_SHEAR_SOURCE_UNVERIFIED` 不再作为当前梁表红字门禁。相关 SMF 来源/支承面状态仍保留在 JSON 审计，待后续完整工程复核，不在本阶段阻断梁表生成。

### 当前项目阶段的颜色门禁与超远期待办

梁表只将当前阶段仍需处理的计算、拓扑/协调、结果状态和排布问题显示为红字。以下状态保留原始值和审计上下文，但不触发当前阶段红字：

- 超远期工程待办：`ANCHORAGE_UNVERIFIED`、`SPLICE_UNVERIFIED`、`TORSION_PERIMETER_LAYOUT_UNVERIFIED`。
- 已确认或相关审计项：`CAPACITY_SHEAR_SOURCE_UNVERIFIED`、`SMF_DESIGN_SOURCE_UNVERIFIED`、`SMF_SUPPORT_FACE_UNRESOLVED`。其中 ETABS Frame Design 设计力来源的确认是本项目既有确认结论；后两项不代表本阶段已经完成全部 SMF 构造签认，只是不作为当前梁表颜色门禁。

这是一项交付范围决策，不删除原始检查字段，也不把黑字解释为完整施工审查通过。详细记录见 [VAL-20260910_REBAR_REVIEW_SCOPE](../state/validation/VAL-20260910_REBAR_REVIEW_SCOPE.md)。

## 交付字段

三张配筋工作表的原始、复核和整理层均保留以下字段：构造状态、阻断状态、区段 JSON、箍筋里程 JSON、规则校核 JSON、纵筋布置几何 JSON、左右支承面、左右首箍、`2h` 边界、各区段间距和道数。配筋运行同时生成 `beam_detailing_zones.dxf`，绘制支承面、`2h` 边界和首箍位置。

## 待工程师签认

项目仍固定使用 ACI 318-14。条文编号、`Mpr` 取值、锚固/搭接长度、裂缝控制参数和体系级节点校核必须由项目持有的正式规范正文及勘误逐条签认；本文件描述软件门禁和追溯方式，不替代施工图审查。

项目已按《纳西里耶医疗城项目 SMF 主梁+次梁措施-1版 20260824》冻结 `0.01613` 和端区 `100 mm` 两项参数；其余钢筋层数、节点排布、箍筋形式、接头和拥挤节点混凝土措施仍见 [IP-P1-002](../delivery/plans/active/IP-P1-002_SMF_REBAR_CONSTRUCTABILITY_MEASURES.md)。读取 ETABS 需求/已提供面积、规范安全与施工便利性发生冲突时的拟议裁决顺序见 [ADR-0006](../decisions/ADR-0006_REBAR_CONFLICT_RESOLUTION_ORDER.md)。
