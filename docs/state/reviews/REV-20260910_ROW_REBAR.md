---
id: REV-20260910-ROW-REBAR
type: review
status: final
owner: project
created: 2026-09-10
updated: 2026-09-10
related:
  - ARCH-003
  - IP-P1-003
  - SPEC-DET-001
---

# 2026-09-10 纵筋协调审查

输入实际路径为 `F:/2026/Research Study/test temp/run_20260910_153410/rebar_workbook_payload_20260910_153416.json`，不是嵌套 `run/_20260910/_153410`。本次只读读取 payload 及当前源码，没有操作 ETABS。

## 已确认发现

1. 64 跨（32 梁线）中 48 行 engine_status=OK，16 行为 `BEAM_LINE_COORDINATION_BLOCKED: 相邻跨配筋 profile 不一致，不能自动协调。`；本样本没有 `ADJACENT_SPAN_COORDINATION_NOT_CONVERGED`。16 行位于 T1-LMR-PB1（5跨）、PB2（4跨）、PB3（3跨）、PB11（4跨），存在 NON_SWAY/SMF 相接。
2. `coordinate_adjacent_spans` 对不同 profile 无条件抛错；`_coordinate_results_by_beam_line` 把一对失败传播至整个 beam_line，并把全部结果置空。失败信息不代表这些跨单独没有可放置钢筋。
3. `_select_longitudinal`、`_mixed_candidate`、`_minimum_credit_hanger_candidates`、`_base_diameter_design` 支持同排异径；`_coordinate_pb_top` 对两端不同直径尝试跨中混径 overlap；SB 顶筋未建立同跨统一第一排。样本 PB4、SB3、SB14 等 OK 行仍含 `2T25+2T16`、`2T20+1T16`、`1T20+3T16` 等组合，不能当作新同排同径策略合格。
4. LongitudinalDesign 以 BarSpec 直径组描述钢筋，缺少可靠物理排身份；RebarLayoutGeometry 构建读取 bars[0].layers，不能证明异径多排的完整几何。仅改 notation 不足以实现新规则。
5. 本次实际 cover 为 ETABS 重心 50 mm、显式箍外净 cover=None、骨料25 mm。复算不得擅自切回40 mm 项目净保护层。现有回读候选还可出现目录外 T18/T8，仅表示 ETABS 候选，不得转为新规则的自动合格配筋。

## 整改裁决

用户确认的新构造规则进入 [ARCH-003](../../architecture/ARCH-003_ROW_REBAR_COORDINATION.md)。不同已解析 profile 应求两侧共同可行布局并分别复核，不得改写 FrameType；不能简单关闭错误颜色、取消几何/强度检查或增加旧循环次数。现阶段已明确暂缓的锚固、搭接等复核继续依照 VAL-20260910-REBAR-REVIEW-SCOPE 管理。

本审查不是规范正文复核或实模验收。后续实现、重放和发布证据由 [IP-P1-003](../../delivery/plans/active/IP-P1-003_ROW_REBAR_V042.md) 记录。
