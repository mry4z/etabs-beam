---
id: IP-P1-003
type: implementation-plan
status: approved
owner: project
created: 2026-09-10
updated: 2026-09-11
related:
  - ARCH-003
  - REV-20260910-ROW-REBAR
  - SPEC-DET-001
  - MS-P1-001
---

# IP-P1-003：逐排纵筋协调与 0.4.2 发布

## 实施合同

执行任务为 `01a0430e-74f1-7881-b812-640aff575621`，工作区只用 `F:/2026/Research Study/etabs beam-v2`。按 [ARCH-003](../../../architecture/ARCH-003_ROW_REBAR_COORDINATION.md) 实现完整 rows 计算链及跨内/跨间协调，不改原工作区和原样本，不连接 ETABS、不保存/分析/设计/写回模型，不提交或推送。

1. 修改现有纵筋表示和候选生成、共同支座算法、完整物理排的面积/几何/最终横筋反馈；废弃新策略中同排混径候选。保持现有有效输入及已确认阶段门禁。
2. 更新 SPEC-DET-001 冲突的 SB 架立、同排混径、跨内上筋和公共支座说明，明确新规则为用户项目构造策略；同步 schema/policy 元数据、ARCH-001、索引、任务和当前基线。不得把 proposed ADR-0006 自动变 accepted 或引入其未确认 provided 保留门禁。
3. 新增可揭示真实缺陷的 rows、全梁线、混 profile、闭环反馈及 renderer 定向测试。只运行受影响测试；此次触及跨模块配筋计算，需覆盖 detailing、constructability、rebar_workbook、PB/SB偏好、Python/Node渲染语义，不以全仓覆盖率代替功能证明。
4. 从原样本 raw_rows 离线重算，输出到新的 `build/output/validation/20260910-v042-row-rebar/`；保留 PB/SB JSON、静态 XLSX、逐行旧新状态与失败原因。报告16个历史阻断跨的实际结果及剩余失败，不强制“全绿”。发布前绘表抽样检查逐排 notation、保留工程异常。
5. 插件 Version=0.4.2、FileVersion/AssemblyVersion=0.4.2.0，运行器/UI/发布说明与策略版本同步。使用现有官方公开 ETABS DLL 离线构建流程，生成完整插件+新便携引擎 ZIP；禁止复用旧0.4.1引擎。执行插件契约、自检及该包离线样本重放，证据区分源码测试、便携运行及实模未验收。
6. 形成独立 VAL 记录，列出代码身份、命令、准确通过数、原样本状态迁移、已知限制和包路径。发布状态只在上述验收完成后记录；留待后续的正式工程签认不得假装完成。

## 默认决策

- 保留既有 PB Option1/Option2 和 SB 公共支座行为；同一跨 L/M/R 的第一排根数独立。公共支座按物理排对齐对应层数和直径，第一排根数可因两侧需求不同而调整；两侧分别满足本侧箍筋架立、净距、面积和最终几何校核。Option2 仍可在需求大侧增加根数或附加排。
- 已解析混 profile 两侧分别校核；未知 profile、未知设计结果等原门禁不变。
- T25/4根示例是有足够截面空间的强制验收案例，不是所有梁的全局固定配筋。
- 不扩大到模型截断、I/J改写、CAD正式交换或重新恢复已延期门禁。
