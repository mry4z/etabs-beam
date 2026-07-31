---
id: VAL-20260730-P1-V645-CURRENT-FULL-CHAIN
type: validation
status: partial
owner: project
created: 2026-07-30
updated: 2026-07-30
run_id: 20260730T072626Z-5101501d
git_commit: 7d3862752041fe18eb0201b21671f3575d70b52b
dirty: true
related:
  - MS-P1-001
  - IP-P1-001
  - TASK-REGISTER-001
  - REV-20260730-P1-REQUIREMENT-MAPPING
  - BASELINE-001
supersedes:
  - VAL-20260722-083439-V643-FULL-CHAIN
superseded_by:
  - VAL-20260730-P1-ETABS-COVER-25MM-CURRENT
---

# VAL-20260730：v6.4.5 当前工作树 P1 全链路

## 1. Verdict

`PARTIAL`。run 本身为 `SUCCESS`：同一次运行内完成写回、分析、混凝土设计、保存、4,094 对象
全属性审计、需求等价校核、基础 Excel、配筋 payload 2.0、主工作簿、SB 工作簿和 manifest。

P1 仍不能关闭：项目尚未提供箍筋外缘净保护层、最大骨料尺寸及其来源；另有设计 partial、未知/混合
Frame Type 和未确认多截面跨；ACI 条款与项目规则也没有结构工程师冻结签署。工作簿因此正确保留异常，
未生成虚假的零值配筋。

## 2. 运行身份

| 项目 | 值 |
|---|---|
| Run ID | `20260730T072626Z-5101501d` |
| Run 状态 | `SUCCESS` |
| 开始/结束 | 2026-07-30 15:26:27 / 15:41:53 +08:00 |
| Git | `7d3862752041fe18eb0201b21671f3575d70b52b`, `dirty: true` |
| tracked diff SHA-256 | `2019c1f4f5f03b3452b6039951c6e6ac38c917912bcfe1f53e9e829c3748fdd4` |
| content SHA-256 | `8f4dbca345bdd7505dc3e364a189d4f46ee67067b924110276ab80a35cb09dec` |
| ETABS / API | ETABS `22.7.0`；`ETABSv1.dll 2.10.0.0` |
| API SHA-256 | `7cfbc7f317ffe7e88a930a04de4e8b2a5d8534e5b0e0411db0ab95a8d694ab6d` |
| 单位 / 规范 | `N_mm_C` / `ACI 318-14` |
| 算法 | topology `2026.07.22-p01`；numbering `2026.07.22-no-span-s`；detailing `aci318-14-si-errata+project-2026-07` |
| payload | `2.0` |

原始 manifest：
[run_manifest_20260730T072626Z-5101501d.json](../../../build/output/p1_v645_final_20260730/final_full_chain_verified/run_manifest_20260730T072626Z-5101501d.json)，
SHA-256 `abb9fbccccd95963e95e63bdd50452fa8098d9b841432981fea0f5171f4e9f39`。

manifest 的 `validity.state=CURRENT` 只表示其输入指纹未被后续注册 run 取代，不代表 P1 工程门禁已关闭。

## 3. 模型身份

| 角色 | 路径 | SHA-256 |
|---|---|---|
| 冻结源 | `build/output/p1_v645_final_20260730/frozen_source/EMHS-GF.v6.4.5.CRS_p1_frozen_20260730_1505.EDB` | `bf2683e0aa2447463459c06a5abd28038ead2f8c09085b2e5822f32b7dfaeb51` |
| 工作副本输入 | `build/output/p1_v645_working_20260730/EMHS-GF.v6.4.5.CRS_p1_working.EDB` | `bf2683e0aa2447463459c06a5abd28038ead2f8c09085b2e5822f32b7dfaeb51` |
| 工作副本保存后 | 同上 | `20347ccc0e3960ef10c91b107776b19dc1676648c8b621b79f8925202aff9c41` |

只写受控工作副本；未操作或覆盖外部原始工程 EDB。

## 4. 拓扑、写回与等价

| 检查 | 结果 |
|---|---:|
| 混凝土 Beam Object | 4,094 |
| Beam Line | 1,215 |
| Span | 3,018（PB 1,574；SB 1,444） |
| `REQUIRES_SPLIT` | 30 Object / 60 actions |
| 多截面 Span | 28，全部未确认 |
| 阻断级拓扑诊断 | 0 |
| 信息级拓扑诊断 | 66（53 `STITCH_LONG_CHAIN`；13 `ROLE_REFINEMENT_CONVERGED`） |
| 分析 / 设计 / 保存返回码 | 0 / 0 / 0 |
| 全属性审计 | 4,094 checked；0 failure |
| 需求等价 | 4,094 checked；0 failure |

全属性审计 SHA-256
`ee205eeba70ad824eac131623864e7831ce4b30615b0276063c474cca33f5c68`；需求等价证据 SHA-256
`a813135fd971325098d08aa66f7b9703cfb7cbe2f563385a415a8606e0755e37`。

## 5. 配筋结果与 fail-closed 状态

payload 保留 3,018 个 raw/review/final Span 行，没有删除失败行：

| 引擎状态 | 行 |
|---|---:|
| `COVER_SEMANTICS_UNRESOLVED` | 2,956 |
| `DESIGN_RESULTS_PARTIAL` | 22 |
| `FRAME_TYPE_UNRESOLVED` | 8 |
| `MIXED_FRAME_TYPE_REVIEW_REQUIRED` | 4 |
| `MIXED_SECTION_REVIEW_REQUIRED` | 28 |

当前配置的 `clear_cover_to_stirrup_outer_mm/source` 和 `max_aggregate_size_mm/source` 均为空。主工作簿
对阻断行把配筋、Legs、实配面积和差值留空，只显示引擎/复核状态；公式错误扫描匹配 0 项。SB 工作簿
明确显示“无可交付 SB 行”，并把 1,444 行全部保留在“复核异常”表。

## 6. 主要成果

- [基础梁表](../../../build/output/p1_v645_final_20260730/final_full_chain_verified/beam_schedule_20260730_153909.xlsx)，
  SHA-256 `b248a625d746d020cbe69c1f53bbdead384623d5e0da49386baa0d64e3f5036d`
- [主配筋复核工作簿](../../../build/output/p1_v645_final_20260730/final_full_chain_verified/beam_rebar_review_20260730_153932.xlsx)，
  SHA-256 `3da8896d8196145abd3d9fc5d3eb9687885283ba65c51dd724b9a760bfaa43c6`
- [SB 复核工作簿](../../../build/output/p1_v645_final_20260730/final_full_chain_verified/secondary_beam_schedule_20260730_153932.xlsx)，
  SHA-256 `1c353e67c34f48e6de15d2dd58ccd2909065005e25586ed10e31eb6f9f483b00`
- [配筋 payload](../../../build/output/p1_v645_final_20260730/final_full_chain_verified/rebar_workbook_payload_20260730_153932.json)，
  SHA-256 `aabc78b0b388e882a8f88fbc46b28a25f869ae22ea4965d570639224b3459c97`

工作簿已经逐张预览检查；它们是阻断/复核证据，不是可签发的工程配筋成果。

## 7. P1 Gate 判断

| Gate | 状态 | 依据 |
|---|---|---|
| P1-G01 | `VERIFIED` | `DET-R001` 至 `DET-R016` 已有 test/sign-off 映射 |
| P1-G02 | `VERIFIED` | 三类 cover/骨料字段分义；未知实模输入返回明确错误 |
| P1-G03 | `PARTIAL` | 自动逐区控制通过；没有已确认 cover/骨料的实模成功行 |
| P1-G04 | `VERIFIED` | PB/SB/Unknown/Mixed profile 自动测试及实模状态可见 |
| P1-G05 | `PARTIAL` | 协调实现与测试通过；实模配筋在协调前被工程输入阻断 |
| P1-G06 | `VERIFIED` | 本次 `SUCCESS` 全链路及当前 manifest |
| P1-G07 | `VERIFIED` | 所有 3,018 行、拓扑例外和异常均保留并有机器状态 |
| P1-G08 | `BLOCKED` | 无结构工程师冻结 Review/签署 |

## 8. 前置失败保留

新建隐藏 ETABS 实例重开 EDB 后没有写回前混凝土设计结果，run
`20260730T072339Z-6f92885f` 被需求等价前置门禁拒绝，未进入写回；其失败 manifest 保留在
`build/output/p1_v645_final_20260730/final_full_chain/`。最终 run 改用已完成设计且身份匹配的受控
P1 工作实例，没有放宽门禁。

## 9. 关闭 P1 仍需

1. 提供并记录项目采用的箍筋外缘净保护层、最大骨料尺寸及权威来源。
2. 处置 22 个设计 partial、12 个未知/混合 Frame Type 状态和 28 个多截面 Span。
3. 用确认输入重跑，取得逐区剪扭和相邻跨协调成功样本。
4. 由有权限的结构工程师冻结并签署 `REV-20260730-P1-REQUIREMENT-MAPPING`。
