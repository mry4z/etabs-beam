---
id: VAL-20260730-P1-ETABS-COVER-25MM-CURRENT
type: validation
status: partial
owner: project
created: 2026-07-30
updated: 2026-07-30
run_id: 20260730T103252Z-d96440dc
git_commit: 7d3862752041fe18eb0201b21671f3575d70b52b
dirty: true
related:
  - MS-P1-001
  - IP-P1-001
  - REV-20260730-P1-REQUIREMENT-MAPPING
  - VAL-20260730-P1-V645-CURRENT-FULL-CHAIN
  - VAL-20260730-P2-OAPI-SQLITE-WORKFLOW-PARITY
  - BASELINE-001
supersedes:
  - VAL-20260730-P1-P2-OFFLINE-CURRENT
superseded_by:
---

# VAL-20260730：ETABS 设计保护层与 25 mm 骨料 P1 当前结果

## 1. Verdict

`PARTIAL`。当前项目已经按用户确认输入使用 ETABS `PropFrame.GetRebarBeam`
`CoverTop/CoverBot`，并按 25 mm 最大骨料执行配筋；实时 ETABS 22.7 只读 run 为
`SUCCESS`，主/SB 工作簿公式错误扫描均为 0，3,018 个 Span 中 2,610 个得到自动配筋。

P1 仍不能关闭：408 个 Span 保持工程阻断或复核状态，ACI 条款和项目规则尚无结构工程师冻结签署；
当前代码身份也没有重新执行写回、分析、设计和全属性审计。历史 writeback run 继续证明该事务链，
本记录只证明当前保护层、骨料、配筋和只读交付链。

## 2. 当前输入语义

- ETABS `CoverTop/CoverBot` 是混凝土表面至顶部/底部纵向钢筋重心的距离；逐截面取控制最大值。
- 有效高度按 `d = h - c_etabs`；纵筋候选排布按
  `c_clear = c_etabs - d_stirrup - d_bar / 2` 反算，不把 ETABS 值误作箍筋外缘净保护层。
- 最大骨料粒径为 25 mm，来源为 `User-confirmed project input, 2026-07-30`；该值控制同层纵筋净距。
- config、payload 和工作簿均记录 `ETABS_LONGITUDINAL_CENTROID` 口径与来源。

## 3. 运行身份

| 项目 | 值 |
|---|---|
| Run ID / 状态 | `20260730T103252Z-d96440dc` / `SUCCESS` |
| 开始 / 结束 | 2026-07-30 18:32:53 / 18:54:21 +08:00 |
| 模式 | ETABS OAPI `read-only`，PID `60856` |
| ETABS / API | ETABS `22.7.0`；`ETABSv1.dll 2.10.0.0` |
| Git / 工作树 | `7d3862752041fe18eb0201b21671f3575d70b52b` / `dirty: true` |
| tracked diff SHA-256 | `6167eb029d2272a9cc88e8e9624c667b61361b12ed5c5d7fc2f7aa54ebe1ac5a` |
| 生产代码 content SHA-256 | `2f9990f0b475023a248939b2dfd524f33573dd20fbf5d4ab68125a246a70d8fc` |
| EDB SHA-256 | `20347ccc0e3960ef10c91b107776b19dc1676648c8b621b79f8925202aff9c41` |
| 单位 / 规范 | `N_mm_C` / `ACI 318-14` |
| payload | `2.0` |

原始 manifest：
[run_manifest_20260730T103252Z-d96440dc.json](../../../build/output/p1_v645_etabs_cover_25mm_20260730/live_readonly_retry_accepted/run_manifest_20260730T103252Z-d96440dc.json)，
SHA-256 `9153866a3b18d25e2b7f7d0c434ac5d9a465f347d41fbf71d489078ecc987fcd`。

manifest 在新增本 Validation 和后续治理 Markdown 前生成；后续仅更新证据文档，不扩大本记录的代码证明范围。

## 4. 拓扑与配筋结果

| 检查 | 结果 |
|---|---:|
| Beam Object / Line / Span | 4,094 / 1,215 / 3,018 |
| PB / SB Span | 1,574 / 1,444 |
| `REQUIRES_SPLIT` | 30 Object / 60 actions |
| 多截面 Span | 28，全部保留复核 |
| 阻断级拓扑诊断 | 0 |
| 自动配筋 `OK` | 2,610（PB 1,208；SB 1,402） |
| 稳定迭代 | 2,480 |
| 保守循环解 | 130，状态 `STABLE_CONSERVATIVE_CYCLE_BREAK` |

408 个非 `OK` Span 的规范化状态：

| 状态 | Span |
|---|---:|
| 相邻跨 profile 不一致，禁止自动协调 | 224 |
| `NO_ALLOWED_SPACING` | 104 |
| `MIXED_SECTION_REVIEW_REQUIRED` | 28 |
| `DESIGN_RESULTS_PARTIAL` | 22 |
| `LONGITUDINAL_FIXED_DIAMETER_LAYOUT_FAILED` | 10 |
| `FRAME_TYPE_UNRESOLVED` | 8 |
| `SB_BOTTOM_THROUGH_BAR_LAYOUT_FAILED` | 8 |
| `MIXED_FRAME_TYPE_REVIEW_REQUIRED` | 4 |

其中 `NO_ALLOWED_SPACING` 表示项目候选间距 `100/150/200 mm` 中没有满足当前控制上限的值；
系统没有静默增加 75 mm 等未冻结项目规格。

## 5. 工作簿与视觉 QA

- [主配筋 payload](../../../build/output/p1_v645_etabs_cover_25mm_20260730/live_readonly_retry_accepted/rebar_workbook_payload_20260730_183432.json)，
  SHA-256 `3c281af18251b8b2245bfee63613200a3980afcd8db6c8679e0787c6ba9fa51e`。
- [主配筋工作簿](../../../build/output/p1_v645_etabs_cover_25mm_20260730/live_readonly_retry_accepted/beam_rebar_review_20260730_183432.xlsx)，
  SHA-256 `4bac3bfb736c3f0ef49bb0086502a4c8190e9c450ea2b241eb3d75fce24aeba9`。
- [SB 工作簿](../../../build/output/p1_v645_etabs_cover_25mm_20260730/live_readonly_retry_accepted/secondary_beam_schedule_20260730_183432.xlsx)，
  SHA-256 `1e2df95ef18d630f16b5ed915c613e9697ae1650f02c1e37e20b9d3187328db8`。
- [基础梁表](../../../build/output/p1_v645_etabs_cover_25mm_20260730/live_readonly_retry_accepted/beam_schedule_20260730_183412.xlsx)，
  SHA-256 `afafafce2fc52449050a489233adfd15bfbbbe5125d718b0a89cdc99a02f2a83`。

主工作簿以独立 Node 进程逐张生成 7 张预览；SB 工作簿生成 2 张预览。视觉 QA 已覆盖原始输入、
设计需求、来源追踪、可编辑配筋、逐区控制、最终梁表和 SB 异常；两个工作簿的公式错误扫描均匹配 0 项。
SB 独立梁表含 1,402 个可交付自动行，42 个异常行保留在“复核异常”。

## 6. 自动门禁

| 检查 | 结果 |
|---|---:|
| pytest | `208 passed, 1 skipped` |
| coverage | `79.19%`，门槛 `60%` |
| Black | 65 files unchanged |
| Flake8 / compileall / `git diff --check` | 通过 |
| 三个生产 `.mjs` 的 bundled Node `--check` | 通过 |
| 工作簿公式错误扫描 | 主表 0；SB 表 0 |

- [pytest.xml](../../../build/output/validation/20260730-cover-aggregate-p1-p2-current-final/pytest.xml)，
  SHA-256 `0c9e926ad9d3caed09d117e69098f29b05a0dab12319af25cf5d1f2df7740f73`。
- [coverage.xml](../../../build/output/validation/20260730-cover-aggregate-p1-p2-current-final/coverage.xml)，
  SHA-256 `6325d04010a0249719f5f82e4a5d07c72016fffd4bf8a0ee0ddd3a2ae86bcbbb`。

## 7. P1 Gate

| Gate | 当前状态 | 依据 |
|---|---|---|
| P1-G01 | `VERIFIED` | `DET-R001` 至 `DET-R018` 已映射 |
| P1-G02 | `VERIFIED` | ETABS 重心 cover 与 25 mm 骨料输入、来源和实模样本已冻结 |
| P1-G03 | `PARTIAL` | 2,610 行通过；104 行无项目允许间距，其他布局/设计异常未处置 |
| P1-G04 | `VERIFIED` | PB/SB profile 与 unknown/mixed 状态可见 |
| P1-G05 | `PARTIAL` | 自动协调与保守循环解通过；224 行跨 profile 边界需工程裁决 |
| P1-G06 | `PARTIAL` | 当前代码完成实模只读链；写回/分析/设计链由较早代码身份证明 |
| P1-G07 | `PARTIAL` | 408 个异常均有机器状态，但尚无全部工程处置 |
| P1-G08 | `BLOCKED` | 尚无结构工程师冻结 Review/签署 |

## 8. 关闭 P1 仍需

1. 结构工程师裁决 224 个跨 profile 协调边界。
2. 冻结是否允许小于 100 mm 的项目箍筋间距，或逐项处置 104 个 `NO_ALLOWED_SPACING`。
3. 处置 22 个设计 partial、12 个未知/混合 FrameType、28 个多截面 Span 和 18 个纵筋/SB 布置失败。
4. 由有权限的结构工程师签署 requirement Review；需要严格满足 P1-G06 时，再用当前代码身份执行受控
   writeback / analysis / design / full-audit run。

