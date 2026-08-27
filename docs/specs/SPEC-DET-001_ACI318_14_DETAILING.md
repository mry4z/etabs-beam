---
id: SPEC-DET-001
type: spec
status: review
owner: project
created: 2026-07-25
updated: 2026-08-27
related:
  - SPEC-DESIGN-001
  - ADR-0004
  - MS-P1-001
supersedes:
  - LEGACY-PB-DETAILING-REVIEW
  - LEGACY-SB-DETAILING-REVIEW
superseded_by:
---

# SPEC-DET-001：ACI 318-14 梁配筋排布

> 状态为 `review`：项目规则已由用户确认并有当前单元测试，但条文、例外条件和官方勘误仍需按项目持有的授权 ACI 318-14 正文完成工程师签署。本 Spec 不替代施工图审查。

本次项目参数以《纳西里耶医疗城项目 SMF 主梁+次梁措施-1版 20260824》为依据；代码中的项目策略版本为
`nasiriyah-smf-sb-2026-08-24-r1`。

## 1. Profile 边界

| ID | 要求 |
|---|---|
| DET-R001 | PB/SB 仅表示梁用途；明确的 ETABS FrameType 对两类梁均有效，不能被 PB/SB 覆盖。 |
| DET-R002 | `Non Sway`→`NON_SWAY`，`Sway Ordinary`→`OMF`，`Sway Intermediate`→`IMF`，`Sway Special`→`SMF`。 |
| DET-R003 | 只有 `Program Determined` 使用项目默认 `SMF`；缺失、未知或混合类型保持 `UNRESOLVED`。 |
| DET-R004 | 每根梁同时输出 ETABS 原始类型、ETABS 解析类型、插件采用类型和判定来源。 |

## 2. 输入语义

- 设计需求遵循 [SPEC-DESIGN-001](SPEC-DESIGN-001_RESULT_SEMANTICS.md)。
- `etabs_longitudinal_cover`、`clear_cover_to_stirrup_outer` 和 `max_aggregate_size` 是不同字段。
- ETABS OAPI 将 `CoverTop/CoverBot` 定义为混凝土顶/底面至相应纵筋重心的距离，不得把该值直接标成
  箍筋外缘净保护层。
- 当前项目运行配置按梁措施第 4 节单排容量表采用 `clear_cover_to_stirrup_outer=40 mm`，并记录措施名称、
  版次和日期；有效高度取 `d=h-c_clear-ds-db/2`。
- 只有显式清空该项目值和来源后，才允许回退到各截面 ETABS `CoverTop/CoverBot` 纵筋重心 cover：
  `d=h-c_etabs`，排布时按候选 `db`、`ds` 反算 `c_clear=c_etabs-ds-db/2`。两种口径不得静默替换。
- 当前项目最大骨料粒径默认取 25 mm，并记录项目默认来源；配置显式覆盖或后续插件菜单选择其他
  数值时，必须同时记录选择来源。解析后的数值进入 `4dagg/3` 净间距控制。
- 截面、材料、Frame Type 和左/中/右需求保留来源 Object、station、组合和单位。
- 混合截面或混合 Frame Type 在人工确认前 fail-closed。

## 3. ACI 通用校核

以下为工程转述，最终以授权正文和勘误为准：

1. 同层净距不小于 `max(25 mm, db, 4dagg/3)`；层间净距不小于 25 mm，且多层钢筋上下对齐。
2. 受弯最小量按 `max[0.25sqrt(fc')/fy*bw*d, 1.4/fy*bw*d]` 校核。
3. 有抗扭时，抗扭纵筋位于闭合箍内、四角有筋、周边间距满足要求；纵向抗扭筋直径不小于 `max(0.042s, 9.5 mm)`。
4. 剪扭横筋同时满足外闭合箍单腿 `Ab/s >= At/s` 和总腿 `n*Ab/s >= Av/s + 2At/s`。
5. 有抗扭时叠加 `s <= min(ph/8, 300 mm)`；普通低剪、高剪和最小横筋按其适用条件分别校核。
6. SMF 端部 hoop 区、首道距离、`min(d/4, 6db, 150)`、横向支承和 18.6.5 容量剪力仅对最终 `SMF` 生效；本项目端区再叠加 `s<=100 mm`，Program Determined 也属于 SMF。

## 4. 项目排布策略

| ID | 要求 |
|---|---|
| DET-R005 | 左/中/右逐 station 反算 `AsFlex=AsTotal-Al/2`；正常模式按 Total 组合排布。 |
| DET-R006 | “两层后转 face”是项目策略，不是 ACI 的纵筋最多两层规定。Total 组合任一区超过两层时，整跨进入 `FACE_TORSION_MODE`。 |
| DET-R007 | face 模式下顶/底只按 AsFlex 设计且允许三层以上；两侧 face 各按整跨最大 `Al/2` 设计，不用取整余量抵扣 Al。 |
| DET-R008 | face 模式必须校核 AsFlex、独立 AsMin、四角/周边和对称；冲突返回 `MIN_FLEXURE_CONFLICT`。 |
| DET-R009 | 梁高 `h>=800 mm` 时默认单侧 T10、最大间距 250；不足 800 且无抗扭 face 时输出 `N.A.`。 |
| DET-R010 | 先设计箍筋再设计纵筋；若最终纵筋坐标要求更多 legs，则迭代到稳定，失败时阻塞。 |
| DET-R011 | 多排纵筋每排至少 2 根；不得留下单根末排。 |
| DET-R012 | 项目间距只允许 100、150、200 mm；无合法候选时返回 `NO_ALLOWED_SPACING`。 |
| DET-R013 | 完整钢筋目录为 T10/T12/T14/T16/T18/T20/T22/T25/T28/T30/T32/T36/T40；本项目启用 T10/T12/T16/T20/T25。 |
| DET-R014 | 纵筋目录限定为 T12/T16/T20/T25；既有排布策略继续采用普通梁受弯主筋不小于 T16、SMF 主要受弯筋不小于 T20。括号 T12 仅作架立且不计受弯面积，AsMin 控制时非括号 T12 可计面积。 |
| DET-R015 | 宽度不大于 300 mm 时，在全部要求满足的候选中优先比较 2-leg 方案。 |
| DET-R016 | ETABS `VMajorArea` 只按 `Av/s` 语义保存；SMF 的 `Vu_ETABS` 首选公共 API 的 `Concrete Beam Shear Envelope - ACI 318-14`。API 不可用时才回退到最终实际配筋的 `Mpr`/重力剪力计算；容量数值仅进入过程 JSONL，不进入交付工作簿。 |
| DET-R017 | 当前项目配置按措施表格采用 40 mm 箍筋外缘净保护层；有效高度、单排容量和箍筋中心线周长均按该口径及候选钢筋重新计算。只有清空显式项目值后才回退到 ETABS 纵筋重心 cover，两个口径不得混用。 |
| DET-R019 | 每个梁跨必须输出非空配筋候选；若无满足全部规则的方案，保留 ETABS 读取需求/Provided 值或面积候选，状态高亮为复核，备注“不得直接用于施工图”，不得以空白代替失败。 |
| DET-R018 | 当前项目最大骨料默认 25 mm；显式配置或后续插件可覆盖，但必须携带来源。最终数值和来源必须进入 payload、工作簿和运行配置并控制同层纵筋净间距。 |
| DET-R020 | SMF 主梁所选截面受拉纵筋率按项目延性限值 `rho<=0.01613` 复核；该值严于 ACI 18.6.3.1 的 `0.025` 上限。 |
| DET-R021 | SMF 端部 `2h` 加密区按 `min(d/4,6db,150 mm,100 mm)`；非加密区优先 200 mm，并继续受 `d/2`、剪力和其他适用上限控制。 |

## 5. PB 与 SB 梁表规则

### PB

- 箍筋允许端区/跨中不同间距，但全跨采用最大 legs。
- 顶部单跨最多一次直径转换；左右端直径不同，跨中是唯一搭接区。
- 底部采用通长基准直径，跨中不得小于两端控制配筋，支座区可减筋。
- 公共支座支持 `OPTION_1_STRICT_EQUAL` 和 `OPTION_2_COUNT_ADJUSTMENT`；Option 2 保持共同直径、位置和排列基线，只在需求大侧增筋。

### SB

- 顶、底受弯主筋各至少 2 根；多腿箍缺少固定点时增加括号 T12 架立筋，不强迫增加受弯主筋。
- 左端 2D、中区、右端 2D 的需求共同选择一个包络整跨的箍筋直径、间距和 legs；三个位置输出同一规格，并逐位置满足 `Av/s + 2At/s`、单腿 `At/s` 和各自最大间距。
- 上部允许左/中/右两次变化；公共支座两侧上筋完全相同。
- 下部使用通长基准直径，跨中不减、支座可减；相邻跨公共支座下筋直径相同，根数可按需求不同。

## 6. Fail-closed 状态

悬挑梁上部支座纵筋按计算值放大 1.2 倍不进入自动配筋引擎，保留为施工图人工复核项。

- `COVER_SEMANTICS_UNRESOLVED`
- `NO_ALLOWED_SPACING`
- `MIXED_SECTION_REVIEW_REQUIRED`
- `FRAME_TYPE_UNRESOLVED`
- `PROGRAM_DEFAULT_MODEL_CONFLICT`
- `FRAME_TYPE_API_CONFLICT`
- `ENGINE_MPR_FALLBACK`
- `MIXED_FRAME_TYPE_REVIEW_REQUIRED`
- `MIN_FLEXURE_CONFLICT`
- `ADJACENT_SPAN_COORDINATION_NOT_CONVERGED`
- `UNSUPPORTED_CODE_EDITION`

## 7. 验收矩阵

至少覆盖：明确 FrameType 不被 PB/SB 覆盖、Program Determined 默认 SMF、普通/高剪/抗扭间距分支、SB 全跨统一箍筋、T12
受弯直径目录与架立表达、SMF 1.613% 配筋率、SMF 端区 100 mm、800 mm face 门槛、PB OMF/SMF/Review Required、最终 db 的 SMF 复核、Total/face
两种模式、多排无单根末排、显式 40 mm 净保护层及 ETABS 重心 cover 回退路径的有效高度与候选排布反算、默认 25 mm 及显式骨料覆盖的净距控制、
PB option 1/2、PB/SB 相邻跨传播、混合截面/类型阻塞，以及当前真实模型的同次 run 重放。

当前实现和未完成项见 [Current Baseline](../state/CURRENT_BASELINE.md)与[P1 实施计划](../delivery/plans/active/IP-P1-001_REBAR_ENGINEERING_CLOSURE.md)。
