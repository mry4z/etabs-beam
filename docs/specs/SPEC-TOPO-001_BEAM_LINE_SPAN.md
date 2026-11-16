---
id: SPEC-TOPO-001
type: spec
status: accepted
owner: project
created: 2026-07-25
updated: 2026-07-25
related:
  - ARCH-001
  - SPEC-DRAW-001
  - SPEC-TOPO-002
supersedes:
  - LEGACY-BEAM-LINE-SPAN
superseded_by:
---

# SPEC-TOPO-001：梁线识别与结构分跨

## 1. 目的

把 ETABS Frame Object 转换为稳定的 Beam Line 和结构 Span，使编号、模型 I→J、梁表左/中/右、DXF/CAD 和配筋工作流共享同一结构语义。

## 2. 概念

| 层级 | 定义 |
|---|---|
| Beam Object | ETABS 原始 Frame Object，以 GUID 识别，可因建模需要被切成多段。 |
| Beam Line | 同楼层连续的有向逻辑梁路径，可跨多个 Object 和截面。 |
| Span | Beam Line 上由真实结构支承边界切分的结构跨。 |

三个层级不得合并。Object 分段、截面变化和普通梁交点不自动等于结构跨边界。

## 3. 最小输入

- Object GUID、当前名称、楼层、I/J 点和三维坐标。
- 设计方向、截面、材料和有效梁宽/梁高。
- 真实柱支承及由锚定 `rigid` 链传播的等效柱支承。
- 几何容差、Z 相交容差、源 Object 端部重叠容差和候选折角上限。
- 有效人工 override 和已确认多截面 Span。

## 4. 处理顺序

1. 筛选合格混凝土 Beam Object，并按楼层隔离。
2. 建立共点/相交候选和三维 Z 校核。
3. 生成连续候选路径；跨截面 stitch 恢复完整 Beam Line。
4. 先识别 PB 核心和柱支承边界，再迭代 PB/SB 角色到固定点。
5. PB 按柱/等效柱/确认支承分跨；SB 按稳定 PB 支承分跨。
6. 应用有效人工 override，生成最终有向路径。
7. 按 I→J 生成稳定 Beam Line/Span 编号和源 Object 映射。
8. 运行不变量和阻塞诊断，再允许下游导出或写回。

顺序不得颠倒；尤其不能先把每个合并组直接当成一个单跨梁。

## 5. 规范要求

| ID | 要求 |
|---|---|
| TOPO-R001 | 所有分析按楼层隔离；同平面投影不证明三维相交。 |
| TOPO-R002 | 相邻候选可在端点相接且局部切线折角不超过配置上限时进入同一 Beam Line；多候选、分叉或闭环不得静默选路。 |
| TOPO-R003 | 截面变化不自动拆 Beam Line 或 Span；多截面 Span 进入 `REQUIRES_SECTION_REVIEW`，确认前阻止方向和编号写回。 |
| TOPO-R004 | PB 只在真实柱、锚定 `rigid` 等效柱或明确人工支承处分跨；PB/SB 或普通梁交点不增加 PB 跨数。 |
| TOPO-R005 | SB 只在 PB 或明确人工支承处分跨；SB-SB 交点不增加跨数。 |
| TOPO-R006 | 无共点的 XY 交点必须沿两条三维杆件分别插值 Z；高差超容差时不建立连接。 |
| TOPO-R007 | 角色传播迭代到固定点；循环或规定轮数内不收敛时 `BLOCKED`。 |
| TOPO-R008 | 弧形/折线 Beam Line 沿累计路径确定方向和中点；弧形主梁不得继承相邻直梁编号。 |
| TOPO-R009 | 柱到自由端或支承外延伸段形成独立悬挑 Span。 |
| TOPO-R010 | 支承距 Object 端点不超过有效重叠容差时忽略微小碎片；两侧均有实质重叠时 Object 一对多映射并进入 `REQUIRES_SPLIT`。 |
| TOPO-R011 | 每个合格源 Object 至少映射到一个 Span；不得为适配表格丢弃 Object 或设计站点。 |
| TOPO-R012 | 一条 Beam Line 只有一个确定性 I→J；Object 顺序、跨号、模型方向、梁表和图纸继承该方向。 |
| TOPO-R013 | 最终结果必须记录诊断严重度、源 Object、候选、支承和控制原因。 |

## 6. 必须检查的不变量

1. 每个 Span 至少两个不同路径点，长度大于容差。
2. 同一 Beam Line 的 Span 不重叠且按 I→J 连续排序。
3. PB 内部边界均为柱/等效柱/确认支承。
4. SB 内部边界均为 PB/确认支承。
5. SB-SB 交点不出现在 Span 边界集合。
6. 源 Object 覆盖完整；一对多映射有 `REQUIRES_SPLIT`。
7. DXF 一父结构跨一条 polyline 且恰有一个同色父跨编号文字；有多个连续截面段时额外标注沿 I→J 排序的 `.n` 子编号；连续相同截面的 Object 合并为一段，A-B-A 为三段，源 Object 身份保留审计；payload 2.3 采用此语义，旧 2.0/2.1/2.2 payload 不自动重号，但不增加结构跨数或 polyline 数。
8. 相同输入重复运行的路径、角色、方向和编号一致。

## 7. 最小验收场景

- 三柱主梁、柱间被次梁切段的主梁、穿越两根主梁的次梁。
- SB-SB 相交、内部柱点、不同 Z 的平面交叉。
- 跨截面连续梁、step beam、弧形梁、折线梁和悬挑。
- 分叉、闭环、多候选和缺失源 Object 的 fail-closed。
- Object 302 回归、PB/SB 角色固定点和源 Object I/J/名称不变性。

当前实现状态不写入本 Spec，见 [Current Baseline](../state/CURRENT_BASELINE.md)。
