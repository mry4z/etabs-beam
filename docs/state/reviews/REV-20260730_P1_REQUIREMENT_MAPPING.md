---
id: REV-20260730-P1-REQUIREMENT-MAPPING
type: review
status: draft
owner: project
created: 2026-07-30
updated: 2026-07-30
related:
  - SPEC-DET-001
  - SPEC-DESIGN-001
  - MS-P1-001
  - IP-P1-001
  - TASK-REGISTER-001
  - VAL-20260730-P1-ETABS-COVER-25MM-CURRENT
  - BASELINE-001
supersedes: []
superseded_by:
---

# REV-20260730：P1 requirement-to-test/sign-off 映射

## 1. 审查边界

本矩阵映射当前 `SPEC-DET-001` 的 `DET-R001` 至 `DET-R018` 及 P1 退出门禁。自动测试证明被标识
代码的确定性行为；条文转述、项目规则和真实配筋成果仍需要结构工程师签署。本 Review 在签署完成前保持
`draft`，不得作为 P1-G08 关闭证据。

## 2. Detailing 要求映射

| Requirement | 自动证据 | 仍需人工/实模 | 当前判断 |
|---|---|---|---|
| `DET-R001` | `test_profile_resolver_never_promotes_sb_to_chapter_18`；`test_sb_sway_special_frame_type_stays_ordinary_without_chapter_18` | 抽查 SB 实模 profile | 自动通过 |
| `DET-R002` | `test_profile_resolver_treats_every_non_smf_pb_as_omf`；`test_profile_resolver_maps_sway_special_pb_to_smf` | 抽查 PB Frame Type/profile | 自动通过 |
| `DET-R003` | `test_profile_resolver_requires_review_for_unresolved_pb`；`test_program_determined_pb_stays_blocked_after_offline_redetail`；`test_mixed_pb_frame_type_is_fail_closed_with_spec_status` | 处置实模未知/混合类型 | 自动通过 |
| `DET-R004` | SB Sway Special 不升级测试；PB/SB 分别解析测试 | 确认项目分类规则 | 自动通过 |
| `DET-R005` | `test_station_envelope_can_override_asflex_from_same_station_calculation`；ETABS `_2` 适配 station 测试 | 实模 payload station/组合抽查 | 自动通过 |
| `DET-R006` | `test_case_two_triggers_span_wide_torsion_face_mode` | 结构工程师确认项目“两层后转 face”策略 | 自动通过 |
| `DET-R007` | face-mode 测试逐区断言 AsFlex，并按整跨最大 `Al/2` 选择 face | 条文/项目策略签署 | 自动通过 |
| `DET-R008` | AsMin 强制计入所有区域；`test_face_mode_that_cannot_place_asmin_fails_with_spec_status` | AsMin、四角及周边规则签署 | 自动通过 |
| `DET-R009` | `test_case_one_selects_stirrups_first_and_keeps_shallow_face_na`；`test_deep_non_torsion_beam_gets_default_t10_skin_bars` | 项目构造筋规则签署 | 自动通过 |
| `DET-R010` | `test_stirrup_records_minimum_quantity_and_final_db_control`；迭代稳定/不收敛状态断言 | 实模 iteration 字段抽查 | 自动通过 |
| `DET-R011` | `test_multilayer_longitudinal_layout_never_leaves_one_bar_in_last_layer` | 工作簿抽查 | 自动通过 |
| `DET-R012` | `test_no_project_spacing_within_code_limit_fails_closed`；普通/高剪/抗扭间距分支测试 | 箍筋间距目录签署 | 自动通过 |
| `DET-R013` | `test_project_policy_rejects_diameter_outside_full_catalog`；Project Policy 目录构造验证 | 项目启用目录签署 | 自动通过 |
| `DET-R014` | `test_main_bar_selection_never_uses_diameter_below_t16`；两项 SB T12 架立/AsMin credit 测试 | T12 表达与计入规则签署 | 自动通过 |
| `DET-R015` | `test_300_wide_beam_prefers_valid_two_leg_stirrup_candidate` | 项目优选策略签署 | 自动通过 |
| `DET-R016` | payload 测试断言 `VMajorArea` 原值及 `PB_SMF capacity shear is not added again` 来源标记 | ETABS 设计含义及 ACI 18.6.5 工程签署 | 自动通过 |
| `DET-R017` | `test_etabs_centroid_cover_controls_effective_depth_and_layout`；payload 在净保护层留空、ETABS cover 有来源时成功并记录 `ETABS_LONGITUDINAL_CENTROID` | 抽查 ETABS `CoverTop/CoverBot`、候选钢筋反算净宽和最终工作簿 | 自动通过；工程签署待完成 |
| `DET-R018` | 25 mm 骨料配置来源校核；ETABS-cover payload 测试；ACI 净距函数测试 | 确认项目骨料输入和实模成功行 | 自动通过；用户已确认输入 |

附加 PB/SB 表规则由以下测试覆盖：PB 单次直径转换、底筋通长基线、Option 1/2、相邻跨底筋传播；
SB 全跨统一箍筋、左右/中逐区强度、公共支座上筋相同和下筋基准直径传播；payload 级 Beam Line 协调、
混合截面/类型阻塞、旧 schema 拒绝和 partial 设计结果传播。

## 3. 输入和设计结果语义

| 范围 | 自动证据 | 当前判断 |
|---|---|---|
| 净保护层、ETABS cover、骨料分义 | config source 必填测试；ETABS 重心 cover 的有效高度/排布反算测试；payload 缺失返回 `COVER_SEMANTICS_UNRESOLVED`；旧 `1.2` 被 `2.0` Reader 拒绝 | 当前项目采用 ETABS `CoverTop/CoverBot` 控制值；最大骨料 25 mm，来源为用户 2026-07-30 确认 |
| Provided `-1` 与有效零 | `_2` 适配测试分别断言 raw sentinel、归一化空值和有效 `0` | 自动通过 |
| API error/partial/warning | 适配器 partial 测试；payload 保留并阻断 partial 结果 | 自动通过 |
| 单位/规范 | 配置只接受 `N_mm_C` / `ACI 318-14`；CLI 临时单位上下文恢复 | 自动通过；实模由 run 复核 |

## 4. P1 Gate 状态

| Gate | 当前证据 | 状态 |
|---|---|---|
| P1-G01 | 本矩阵及当前完整离线测试 | `VERIFIED`：`DET-R001` 至 `DET-R018` 均已映射；冻结签署由 P1-G08 单独控制 |
| P1-G02 | schema/config/payload 自动测试、ETABS OAPI 定义及用户 2026-07-30 输入确认 | `VERIFIED`：两种保护层口径分义；当前项目使用 ETABS 纵筋重心 cover 和 25 mm 骨料，没有把 50 mm 静默当作箍筋外缘净保护层 |
| P1-G03 | 普通/高剪/抗扭/最小横筋逐区测试及控制字段 | `PARTIAL`：当前实模已有 2,610 个成功行；104 个 `NO_ALLOWED_SPACING` 及其他布局/设计异常仍需工程处置 |
| P1-G04 | PB/SB/Unknown/Mixed profile 测试 | `VERIFIED`（自动范围） |
| P1-G05 | Option 1/2、PB/SB 相邻跨和 payload Beam Line 协调测试 | `PARTIAL`：实现/自动测试通过；实模在协调前被工程输入阻断 |
| P1-G06 | 当前 ETABS 22.7 工作副本最终 run | `VERIFIED`：run `20260730T072626Z-5101501d` 为 `SUCCESS` |
| P1-G07 | fail-closed 状态、拓扑/多截面/设计异常输出 | `VERIFIED`：3,018 行全部保留，66 个拓扑信息项和所有工程异常均有状态 |
| P1-G08 | 无结构工程师冻结签署 | `BLOCKED` |

当前实模结果见
[VAL-20260730-P1-ETABS-COVER-25MM-CURRENT](../validation/VAL-20260730_P1_ETABS_COVER_25MM_CURRENT.md)：
3,018 个 Span 中 2,610 个自动配筋成功，408 个保持工程阻断或复核状态；其中 224 个为
跨 profile 协调边界、104 个无允许箍筋间距，其余为混合截面、设计结果 partial、未知/混合
Frame Type 及纵筋/SB 布置异常。

## 5. 签署区

以下内容必须由有权限的结构工程师基于项目持有的授权 ACI 318-14 正文、勘误、项目总说明和实际材料
输入完成，Codex 不得代签：

- ACI 最小纵筋、剪扭最小横筋、普通/高剪/抗扭间距和 SMF 条款映射；
- ETABS `VMajorArea` 与容量剪力的项目适用语义；
- PB/SB profile、face、T12、Option 2、300 mm 两腿优选等项目规则；
- 设计 partial、未知/混合 Frame Type、多截面 Span、104 个无允许间距、224 个跨 profile 协调边界、
  18 个纵筋/SB 布置失败的工程处置。
- ETABS cover/25 mm 骨料的最终计算样本和工作簿已由
  [当前实模 Validation](../validation/VAL-20260730_P1_ETABS_COVER_25MM_CURRENT.md)完成。

签署人、资格、日期、审查文本版本和结论尚未提供，因此本 Review 不能改为 `final`。
