---
id: ADR-0007
type: decision
status: superseded
owner: project
created: 2026-08-03
updated: 2026-08-10
decision_date: 2026-08-03
related:
  - SPEC-DESIGN-001
  - SPEC-DET-002
  - ARCH-003
  - ADR-0004
  - ADR-0006
  - IP-P1-001
  - TASK-REGISTER-001
supersedes: []
superseded_by:
  - ADR-0008
---

# ADR-0007：容量剪力由后台引擎计算，Excel 负责可审计复核

> 已由 [ADR-0008](ADR-0008_ETABS_CAPACITY_SHEAR_PROCESS_ONLY.md) 替代。保留本文件作为历史决策记录；当前实现以 ADR-0008 为准，容量剪力数值和公式不再进入交付 Excel。

## Status

Accepted — 2026-08-03。

## Context

对于已确认采用 SMF 容量设计的梁，ETABS 输出的设计剪力 `Vu_ETABS` 已按 ETABS 计算的纵向钢筋和可能弯矩强度取得。它是横向钢筋设计的首要需求来源，不应被插件以未经验证的简化公式替换。

但插件最终采用的钢筋是按实际可供应直径、根数、层数、净距和节点门禁选出的布置。若最终布置的纵筋使实际 `Mpr` 高于 ETABS 用于容量设计的纵筋对应值，按实际 `Mpr` 得到的容量剪力可能超过 `Vu_ETABS`。此风险不能只在梁表中提示；最终箍筋必须按控制剪力选择和复核。

每跨的截面强度、容量剪力和横向钢筋校核可以独立计算，规模增大后不宜把复杂迭代、错误状态和选筋决策塞入工作簿。工作簿仍须让结构工程师能看清输入、控制来源和复核结论。

## Decision Drivers

- 不低估最终实际配筋导致的 SMF 容量剪力；
- 保留 ETABS 设计结果的版本、方向、组合、站点和确认状态；
- 让复杂的截面平衡、`Mpr` 和选箍决策有单一可测试实现；
- 保持交付物为无需启用宏的 `.xlsx`，并可离线打开、筛选和审计；
- 允许纯计算按跨并行，同时禁止并发访问 ETABS COM/OAPI；
- 防止 Excel 和后台各自重算一套不同规则而给出矛盾结论。

## Options Considered

### Option A：后台 Python 计算引擎 + Excel 公式复核

后台引擎读取经确认的 ETABS 结果、基于最终钢筋布置复算 `Mpr` 与容量剪力，并据此选择和复核箍筋。Excel 只消费版本化结果，在既有“分跨、配筋、整理”三表结构中显示输入、辅助列、短公式和结论。

### Option B：Excel 公式作为主计算引擎

可直接让使用者看到全部计算，但多层纵筋截面平衡、不同方向容量剪力、候选选筋和失败状态会形成难以审查和测试的长公式；也难以可靠地驱动重新选箍。

### Option C：VBA 作为主计算引擎

VBA 能访问桌面 Excel，但正式成果需改为 `.xlsm`，受宏安全策略、签名与企业 Trust Center 影响；VBA 循环也不是此类逐跨 CPU 计算的稳健并行方案。

### Option D：Python in Excel 或 Office Scripts 作为主计算引擎

Python in Excel 依赖合格的 Microsoft 365 许可、联网和 Microsoft Cloud 运行环境，不能访问本机 ETABS 或本地文件；Office Scripts 面向云端工作簿自动化，不能承担 ETABS 桌面集成。两者可用于个人探索，不是项目正式交付依赖。

## Decision

采用 **Option A：后台 Python 计算引擎 + Excel 公式复核**。

### 1. 容量剪力来源与最终控制值

1. 仅当 ETABS 产品/版本、模型身份、设计规范、Frame Type、设计完成状态、组合/方向以及“容量设计已包含”的设置均已确认时，`Vu_ETABS` 才可标记为 `ETABS_CAPACITY_INCLUDED`。
2. 后台引擎以最终采用的纵筋布置计算 `Mpr_actual`。`Mn`、`φMn` 与 `Mpr` 必须分别保存，不能混用。
3. 对每个地震方向和相应正负弯矩情况，后台引擎以实际 `Mpr`、净跨和适用重力剪力计算 `Vcap_actual`，再与同方向的 `Vu_ETABS` 形成控制包络。
4. `Vu_final` 是箍筋选择和最终校核的需求。不得把不同方向或不同控制组合的绝对值简单做单个 `MAX()` 后标记为一个虚构的控制组合；结果必须保留控制方向、来源和输入身份。
5. SMF `2h` 加密区的适用 `Vc=0` 规则进入后台横筋计算。若 `Vu_final`、`Mpr_actual`、重力剪力或来源确认缺失，输出可追溯的阻断/复核状态，不给出“施工图可用”结论。

当最终实际纵筋未提高控制容量剪力时，已验证的 ETABS `Vu` 继续控制；当 `Vcap_actual` 更大时，必须重新选择或重新校核箍筋。这个复核不是对 ETABS 整体分析的替代，而是对最终钢筋离散化和构造选型的闭环。

### 2. 计算、并行与 ETABS 边界

- ETABS OAPI/COM 读取保持单线程、只读和宿主线程亲和；不得由多个 worker 同时请求模型或设计结果。
- 读取完成后，形成不可变的逐跨 `CapacityShearInput` 快照；截面强度、容量剪力、区段和候选箍筋计算作为纯领域计算执行。
- 先对代表性模型做性能基准。仅当纯计算成为瓶颈时，按批次使用受限进程池并行；worker 数、批次、超时和失败结果必须写入 manifest。
- 并行结果按稳定的梁线、梁、跨、方向和区段键排序；串行与并行运行同一输入必须产生相同的结果、控制来源和状态。

### 3. Excel 交付与实际配筋复核边界

- 成果继续使用普通 `.xlsx`，不依赖 VBA、Python in Excel、Office Scripts、Microsoft 365 特定许可或网络。
- “分跨、配筋、整理”三表结构保持不变。`配筋`表承载容量剪力复核区/表：ETABS 容量剪力、最终钢筋摘要、引擎计算的 `Mpr_actual`、`Vcap_actual`、`Vu_final`、`Vc`、所需及实际 `Av/s`、间距限值、控制来源和结论。
- Excel 公式只承担清晰、短小的二次核对和展示，例如容量剪力输入组合、`Av/s` 比较和通过/阻断标记；复杂截面平衡、钢筋候选生成、规则裁决和最终选箍只由后台引擎完成。
- 公式必须拆成可见的辅助列或具名计算，不以单条超长公式隐藏逻辑。Excel 公式计算的结果不是发布 verdict 的唯一来源。
- 使用者若要调整实际钢筋，必须通过版本化的“配筋调整输入”表或后续插件的“按实际配筋复核”动作提交。不得把任意修改展示单元格解释为新的设计输入。每次复核生成新的 run ID、manifest 和结果身份。

## Rationale

该边界把真正影响安全的计算留在已可版本化、单元测试和失败封闭的领域引擎中，同时让 Excel 保有工程人员需要的透明度。Python in Excel 的计算在 Microsoft Cloud 中运行，需要联网，且不具备访问用户电脑、设备或本地 ETABS 的能力；因此不满足本项目可离线交付、桌面插件和可复现实模复核的边界。[Microsoft Python in Excel 介绍](https://support.microsoft.com/en-US/Excel/python/introduction-to-python-in-excel) [数据安全与边界说明](https://support.microsoft.com/en-US/Excel/python/data-security-and-python-in-excel)

VBA 虽可用于桌面自动化，但宏的启用和签名受安全策略约束；Office Scripts 则以云端、跨平台工作簿自动化为目标，并不能替代本地 ETABS 集成。[Microsoft 对 VBA 与 Office Scripts 的对比](https://learn.microsoft.com/en-us/office/dev/scripts/resources/vba-differences) [VBA 宏安全说明](https://learn.microsoft.com/en-us/office/vba/language/reference/user-interface-help/security-dialog-box)

## Consequences

- 配筋引擎需新增或整理纯容量剪力复核组件、版本化输入/输出 schema 及按方向保存的控制来源。
- ETABS 容量设计来源确认、实际纵筋 `Mpr` 复核和 `Vu_final` 将成为最终箍筋选择的必经门禁。
- 工作簿生成器只能生成复核表与公式，不能从 Excel 文本反推或覆盖配筋决策。
- 正式工作簿保持 `.xlsx`；本决定不交付或要求 VBA 宏、Python in Excel、Office Scripts。
- 并行仅适用于 ETABS 读取后的纯计算；是否启用由性能基准决定，不能以并行改变工程结果。
- 本 ADR 只确定软件计算边界，不替代 ACI 318-14 条文签认，也不放宽 [ADR-0006](ADR-0006_REBAR_CONFLICT_RESOLUTION_ORDER.md) 的读取值、安全与施工便利性裁决顺序。

## Verification

### Current implementation evidence (2026-08-03)

本决定的第一阶段已经落到以下代码路径：

- `src/design/constructability.py` 的 `calculate_smf_capacity_shear()` 接受已归一化的
  `Vu_ETABS` 或显式 `Mpr`/重力剪力输入；当处理后的梁表同时有 ETABS `Provided` 纵筋、
  `VMajorArea (Av/s)`、显式 Mpr 系数并声明端区 `Vc=0` 时，可透明地把 Av/s 换算为 ETABS
  Vu、扣除 ETABS Mpr 包络得到重力剪力残值，再替换为自动配筋的实际 Mpr。它不会把
  `VMajorArea` 直接当作力值。
- `BeamDetailingEngine` 在最终纵筋布置后计算 `Vcap_actual`，以
  `Vu_final=max(Vu_ETABS,Vcap_actual)` 换算端部 `Vc=0` 的所需 `Av/s`，再进入箍筋候选选择，
  并在最终化阶段保留同一 demand 来源。
- `src/rebar_workbook.py`、两个三表渲染器和 payload `2.1` 增加 Mpr、容量剪力、所需
  `Av/s`、控制来源、`Vc=0` 和机器可读复核状态；Excel 公式只计算差值和显示复核，不改变引擎选筋。
- 旧 payload `2.0` 仍可显式读取，新的运行统一输出 `2.1`；`VMajorArea` 在三表中标为
  `Av/s`，不会被误标成 `Vu`。

本阶段的验证为 62 项定向 Python 断言、portable renderer 测试和 Node 语法检查通过。当前
ETABS 适配层仍没有从 `GetSummaryResultsBeam_2` 单独取得带完整来源确认的力值 `Vu`、逐方向
Mpr 和重力剪力；新增加的梁表反算路径依赖 `Provided + Av/s + Vc=0 + Mpr` 系数，因此没有
明确 ETABS 设计设置确认时仍保持 `CAPACITY_SHEAR_SOURCE_UNVERIFIED`，不得发布为可直接
用于施工图的结果。该边界与本 ADR 的 fail-closed 决定一致。

1. 已确认容量设计来源的 ETABS 结果，最终 `Vu_final` 保留正确的方向、组合和 `ETABS_CAPACITY_INCLUDED` 标记。
2. 最终钢筋与 ETABS 设计钢筋等效、实际 `Mpr` 增大、来源未确认和重力剪力缺失四类案例分别产生预期的控制值或阻断状态。
3. 当 `Vcap_actual > Vu_ETABS` 时，箍筋选择按 `Vu_final` 更新，且端部 `Vc=0` 校核仍通过。
4. 保护层、层数、实际钢筋直径或相邻跨协调改变最终纵筋后，相关跨重新计算 `Mpr_actual`、`Vcap_actual` 和箍筋。
5. 相同输入下，串行与并行纯计算的结果、状态、排序和 manifest 哈希一致；ETABS OAPI 调用只发生在主读取阶段。
6. 生成的 `.xlsx` 不含宏依赖；复核区公式引用正确、无错误值，且能清楚追溯到引擎输出与 run ID。

## Reconsideration Triggers

- 项目明确要求在无后台运行器的隔离环境中完成正式再设计，且接受受签名宏或其他受控本地计算部署；
- ETABS 可提供经过验证、可读取且与最终实际钢筋一一对应的容量设计中间结果，使重复 `Mpr` 复核不再必要；
- 项目规范、适用软件版本或数据保密要求改变，导致当前 ETABS/Python/Excel 边界不再适用。
