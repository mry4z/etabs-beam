---
id: MS-P1-001
type: milestone-spec
status: accepted
owner: project
created: 2026-07-25
updated: 2026-07-30
related:
  - SPEC-DET-001
  - IP-P1-001
  - BASELINE-001
supersedes: []
superseded_by:
---

# MS-P1-001：ACI 318-14 配筋工程闭环

## 1. 阶段目标

在不削弱 P0 fail-closed 拓扑与模型安全边界的前提下，把当前配筋原型收敛为可追溯、可实模重放、可由结构工程师审查的工程流程。

## 2. 进入条件

- P0 历史全链路已有带模型和成果哈希的 manifest。
- 当前工作树默认离线测试通过。
- 梁线、分跨、方向、编号和写回安全约束已有 Accepted Spec/ADR。
- 配筋规则中的项目决策与 ACI 通用规则已经分标签记录。

进入条件不代表当前工作树已通过实模验收，详见 [Current Baseline](../../state/CURRENT_BASELINE.md)。

## 3. 本阶段范围

1. 收敛保护层、骨料、截面和 ETABS cover 的字段语义；当前项目采用 ETABS
   `CoverTop/CoverBot` 纵筋重心口径，最大骨料默认 25 mm，显式配置或后续插件选择可带来源覆盖。
2. 完成普通/高剪/抗扭/SMF 箍筋最小量与间距控制来源。
3. 用最终实配纵筋直径闭环 SMF `6db` 检查。
4. 对混合截面、未知/混合 Frame Type、空设计数组和 ETABS error/warning 保持 fail-closed。
5. 完成 PB option 2 和 PB/SB 相邻跨连续性实模复核。
6. 验证 SB 左/中/右使用同一箍筋直径、间距和 legs 的整跨包络，不仅合并 Excel 文本。
7. 用当前代码、同一模型和同一次 run 生成 manifest、基础 Excel、三表工作簿和异常清单。
8. 对 ACI 条文、官方勘误和项目规则进行工程师签署。

## 4. 非目标

- 不在本阶段实现 SAP2000、GUI、正式 ETABS/CAD 插件或 SQLite 数据源。
- 不把配筋引擎作为 ETABS 设计或注册结构工程师施工图审查的替代品。
- 不通过放宽间距、默认保护层或选择首个截面消除阻塞项。

## 5. 退出门禁

| Gate | 退出条件 | 所需证据 |
|---|---|---|
| P1-G01 | Spec 中所有 `DET-*` 要求有测试或人工签署映射 | Requirement-to-test/sign-off 矩阵 |
| P1-G02 | ETABS 重心 cover 与可选箍筋外缘净保护层分义；最大骨料缺省解析为有来源的 25 mm，显式覆盖必须携带来源；缺少当前保护层口径必需值时返回输入错误 | schema、单元测试、实模样本 |
| P1-G03 | 箍筋每个位置逐项满足强度、最小量和最大间距 | 逐区控制原因和测试 |
| P1-G04 | PB/SB profile 无静默推断 | 未知/混合类型阻塞测试 |
| P1-G05 | 相邻跨协调收敛，无法收敛时阻断 | 单元测试和实模差异报告 |
| P1-G06 | 当前工作树完成同次实模全链路 | commit/diff、EDB、配置、run ID、manifest、成果哈希 |
| P1-G07 | 所有异常有明确状态和处置，不以删除行销项 | 异常清单与人工决定 |
| P1-G08 | ACI 条款和项目规则完成工程师审查 | 冻结 Review/签署记录 |

## 6. 延后范围

稳定编号、CAD 正式交换、人工 UI 和 SQLite 按 [Roadmap](../../delivery/ROADMAP.md) 后续里程碑推进；它们不得阻止本阶段关闭已经明确隔离的配筋内核，但正式 CAD 交付仍需后续门禁。
