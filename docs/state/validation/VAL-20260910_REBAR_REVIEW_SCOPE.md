---
id: VAL-20260910-REBAR-REVIEW-SCOPE
type: validation
status: partial
owner: project
created: 2026-09-10
updated: 2026-09-10
as_of: 2026-09-10
related:
  - SPEC-DET-002
  - ADR-0008
  - BASELINE-001
---

# 2026-09-10 配筋梁表当前复核范围记录

## 结论

本项目现阶段不把以下三类状态作为梁表生成或 Excel 红字门禁：

- `ANCHORAGE_UNVERIFIED`（锚固）；
- `SPLICE_UNVERIFIED`（搭接）；
- `TORSION_PERIMETER_LAYOUT_UNVERIFIED`（抗扭周边布置）。

三类状态仍保留在 JSON 计算/审计结果中，归入超远期工程复核待办。梁表中的黑字只表示该行没有命中当前阶段颜色门禁，不表示上述构造项目已完成施工审查。

项目既有确认结论：ETABS Frame Design 输出的 `Design Force` 已考虑此前提及的抗震来源。因此，`CAPACITY_SHEAR_SOURCE_UNVERIFIED` 不再作为当前梁表红字原因。与 SMF 来源和支承面相关的 `SMF_DESIGN_SOURCE_UNVERIFIED`、`SMF_SUPPORT_FACE_UNRESOLVED` 继续保留在 JSON 审计，作为后续完整工程复核信息，但按本阶段范围不阻断梁表生成或染红。

## 实施边界

当前颜色策略只在渲染层过滤上述非当前门禁状态；没有删除、改写或伪造 ETABS 设计结果，也没有从原始 payload 删除构造检查。Python 静态渲染器和便携 Node 渲染器使用同一组状态码策略，避免默认运行路径与备用运行路径产生不同颜色结果。

仍会触发红字的当前阶段问题包括实际计算失败、结果缺失、梁线/拓扑协调阻断、当前配筋排布策略不一致及其他未列入非当前范围的审查状态。

## 证据与限制

- 文档记录依据项目此前对 ETABS Frame Design `Design Force` 抗震来源的确认；本次变更未重新连接或操作 ETABS 实模。
- 回归测试覆盖“仅含非当前状态时保持黑字”和“仍含当前协调阻断时保持红字”。
- JSON 审计字段和历史验证记录不被回写；历史记录仍按其原代码身份和当时范围解释。

本次离线检查：

- `python -m pytest -q --no-cov -p no:cacheprovider tests/unit/test_render_rebar_workbook_xlsx.py tests/unit/test_render_secondary_beam_xlsx.py`：`17 passed`。
- `node --check src/spreadsheet/build_rebar_workbook.mjs`：通过。
- 文档治理审计：`0 errors`、`0 broken local links`；既有 8 条缺少 frontmatter 的警告仍存在。
- 对现有 `run_20260910_134428` payload 重新应用当前颜色策略：PB `231` 行中 `42` 行红字、`189` 行黑字；SB `352` 行中 `78` 行红字、`274` 行黑字。该统计是离线重算，不是新的 ETABS 实模验收。
- 已重建可加载包 `build/etabs-plugin/v0.4.1-v2-etabs23-loadable-20260910-r6d.zip`：ETABS 23 插件契约校验 `passed`，便携运行时自检 `OK`，包内未嵌入 `ETABSv1.dll`。该包仍未经过 ETABS 22/23 实模菜单加载验收。

## 超远期 TODO

在正式工程签认阶段重新明确并验证锚固长度、搭接长度、抗扭周边布置、完整 SMF 支承面/来源以及相应规范条文；届时再决定是否恢复为当前梁表门禁。
