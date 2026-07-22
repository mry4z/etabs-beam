# 主梁配筋排布规则评审稿（ACI 318-14）

> 状态：第一版配筋排布引擎已接入真实 ETABS 23 模型的独立 Excel 评审链路；当前算法能力有效，但既有 3,070/3,074 跨工作簿使用了已修正的旧分跨拓扑，必须重算后才能作为最终验收件
> 当前规范：ACI 318-14（采用 SI 单位，并应用官方勘误）
> 目的：把 ETABS 左/中/右设计需求转换为主梁梁表配筋，同时保留以后扩展其他规范版本的接口。

## 1. 输入字段必须先统一语义

以下定义是本模块的唯一内部语义；ETABS 适配器必须证明源字段与之对应：

| 内部字段 | 单位 | 定义 |
|---|---:|---|
| `As_etabs_top` | mm² | ETABS `AsTop`：不含受扭纵筋，但已受最小配筋率控制；仅保留作审计，不作为本项目受弯选筋需求 |
| `As_etabs_bottom` | mm² | ETABS `AsBot`：不含受扭纵筋，但已受最小配筋率控制；仅保留作审计，不作为本项目受弯选筋需求 |
| `As_total_top` | mm² | ETABS `AsTopTotal`：共同考虑受弯、受扭及适用时剪力后的顶部总纵筋需求 |
| `As_total_bottom` | mm² | ETABS `AsBotTotal`：共同考虑受弯、受扭及适用时剪力后的底部总纵筋需求 |
| `As_flex_top` | mm² | 项目反算的顶部真实受弯需求：`max(0, As_total_top-Al/2)` |
| `As_flex_bottom` | mm² | 项目反算的底部真实受弯需求：`max(0, As_total_bottom-Al/2)` |
| `As_design_top` | mm² | 程序选筋后顶部实际提供的钢筋面积，不是 ETABS 需求值 |
| `As_design_bottom` | mm² | 程序选筋后底部实际提供的钢筋面积，不是 ETABS 需求值 |
| `As_provided_top` | mm² | ETABS `TopAreaProvided`：模型中用于设计校核的顶部实配面积；未设置时可为 `-1` |
| `As_provided_bottom` | mm² | ETABS `BotAreaProvided`：模型中用于设计校核的底部实配面积；未设置时可为 `-1` |
| `Av_over_s` | mm²/mm | 仅抗剪所需的全部有效抗剪腿面积/间距 |
| `At_over_s` | mm²/mm | 抗扭闭合外箍单腿面积/间距 |
| `Al_torsion` | mm² | 截面抗扭纵筋总面积，不是每面面积 |

必须保留原始 ETABS 字段名、设计组合、station 和结果状态。若 ETABS 的 `Total Longitudinal Rebar` 已经包含抗扭纵筋，不得再次叠加；若横向钢筋输出已经是剪扭组合量，也不得重复计算。

左、右区仍按约定取梁端 `2D` 范围内的最大需求，中区取跨中区域最大需求，其中 `D` 为梁总高。对于 SMF，ACI 的 `2h` 是从支承/节点面起算，而 ETABS 梁线端常在柱中心；实现时必须根据柱面、端部偏移或刚域换算，不能一律从 Object 端点直接量 `2D`。

## 2. ACI 318-14 构造与校核规则

### 2.1 纵向钢筋净距、层数与保护层

- 9.7.2.1、25.2.1：同层平行非预应力钢筋的净距不得小于 `max(25 mm, db, 4dagg/3)`。
- 25.2.2：两层及以上钢筋应上下对齐，层间净距不得小于 25 mm。
- ACI 318-14 **没有规定梁纵筋最多只能排两层**。“主筋最多两层；超过两层就把抗扭纵筋转为 face rebar”是用户已确认、模块必须遵循的项目工程策略。
- 20.6.1.3.1：保护层取值与暴露条件、施工方式有关。模块必须读取项目保护层，不能把 40 mm 写死；通常保护层量至最外侧箍筋，排筋净宽还要扣除箍筋直径和纵筋半径。
- **输入语义约束**：ETABS 导出的纵筋 `Top/Bottom Cover`、项目定义的“混凝土表面至箍筋外缘净保护层”和最大骨料粒径是三个不同字段。适配器必须保留原字段名、单位和定义，不能把 ETABS 纵筋 cover 或骨料尺寸直接当作箍筋外缘保护层。用于排筋净宽/有效高度的值必须来自已确认的 `clear_cover_to_stirrup_outer`；来源未确认时应要求项目覆盖值或返回输入错误，不能用默认 50 mm 静默继续。
- 24.3.2 的裂缝控制钢筋中心距上限按 SI 可写为：

  `smax = min[380(280/fs) - 2.5cc, 300(280/fs)] mm`

  其中 `fs` 可按 24.3.2.1 取 `(2/3)fy`，`cc` 为受拉面至最近钢筋表面的净距。

### 2.2 抗弯纵筋与 SMF 连续性

- 9.6.1.2：每个受弯截面的纵向受拉钢筋不得小于规范最小值。SI 常用形式为：

  `As,min = max[0.25sqrt(fc')/fy * bw*d, 1.4/fy * bw*d]`

- 18.6.3.1：SMF 梁顶部、底部至少各有两根连续钢筋，且纵向配筋率不得大于 0.025。
- 18.6.3.2：柱面处正弯矩强度不得小于负弯矩强度的一半；沿梁任一截面的正、负弯矩强度均不得小于两端柱面最大弯矩强度的四分之一。该项必须按 `Mn` 校核，不能只比较钢筋面积。
- SMF 的钢筋强度、锚固、搭接位置也应由规范策略校核；本阶段的梁表选筋不能替代这些校核。

### 2.3 抗扭纵筋与 face/skin rebar

- 9.5.4.3：抗扭纵筋、横筋应与同一截面、同一作用组合下的弯矩、剪力及轴力需求共同考虑，不能盲目叠加各自不同时出现的包络最大值。
- 9.7.5.1：`Al_torsion` 是沿闭合箍周边配置的总面积；四角至少各有一根纵筋，周边纵筋间距不得大于约 300 mm，并位于闭合箍内。条文没有要求按各边长度比例分配，也没有要求各边面积均匀。
- 9.7.5.2：用于抗扭的纵筋直径不得小于 `max(0.042s, 9.5 mm)`，其中 `s` 为横向抗扭箍筋沿梁长方向的间距。
- 本项目不再使用受最小配筋率阈值抬高的 ETABS `AsTop/AsBot` 设计受弯筋，而从 Total 反算 `AsFlexTop=max(0,AsTopTotal-Al/2)`、`AsFlexBot=max(0,AsBotTotal-Al/2)`。正常模式仍按 Total 完成顶、底组合纵筋排布；只有组合排布超过两层时才把抗扭部分转移到 face rebar，受弯筋继续按 AsFlex 排布且不受两层上限限制。用于计入 `Al` 的钢筋面积应关于截面对称，其分布形心与闭合箍/截面中心重合。无需按闭合箍各边长度比例分配；几何校核仍保留四角有筋、钢筋位于闭合箍内、周边相邻筋间距不大于约 300 mm。
- 9.7.2.3：只有梁总高 `h > 900 mm` 时，ACI 318-14 的 SI 条文才强制在两侧、受拉缘附近 `h/2` 范围设置 skin reinforcement，并按 24.3.2 控制间距。项目对 `h >= 800 mm` 的梁默认采用 `T10@250`，在 800～900 mm 区间属于更严格的项目规则，而不是 ACI 的统一最低要求。
- 当 face rebar 承担抗扭时，应标记为 `TORSION_AND_SKIN`；梁表填写单侧数量/面积，截面两侧总量为表中数值的两倍。

### 2.4 剪扭组合箍筋

设候选箍筋单腿面积为 `Ab`、间距为 `s`、有效总腿数为 `n`，必须同时满足：

1. 外闭合箍单腿抗扭：`Ab/s >= At_over_s`；
2. 剪扭组合总量：`n*Ab/s >= Av_over_s + 2*At_over_s`。

故强度所需初始腿数为：

`nraw = max(2, ceil([(Av/s) + 2(At/s)] * s / Ab))`

随后还要按对称布置和 SMF 横向支承要求增加腿数。不能简单取 `max(Av/s, 2At/s)`；外闭合箍两腿既参与抗剪，也形成抗扭闭合流，组合式已正确处理其共同贡献。

其他限制：

- 9.7.6.3：有抗扭需求时必须采用闭合箍/hoop；抗扭箍间距不得大于 `min(ph/8, 300 mm)`，`ph` 为最外层闭合箍中心线周长。
- 9.6.4.2：有抗扭时还应满足最小组合横筋：

  `(Av + 2At)/s >= max[0.062sqrt(fc')*bw/fyt, 0.35bw/fyt]`

- 9.7.6.2.2：普通剪力条件下，低剪力区 `s <= min(d/2, 600 mm)`；高剪力区 `s <= min(d/4, 300 mm)`。截面剪扭上限不满足时必须报错，不能靠继续加钢筋消除。

项目候选间距为 `{100, 150, 200}` mm。算法从满足全部规则的最大间距开始选；若三者均不满足，应返回 `NO_ALLOWED_SPACING`，不得静默采用 100 mm。

### 2.5 SMF 箍筋

- 18.6.4.1：从柱面向跨内 `2h`，以及其他预计发生弯曲屈服截面两侧各 `2h` 范围采用 hoops。
- 18.6.4.4：首道 hoop 距柱面不大于 50 mm；上述区域的间距不得大于 `min(d/4, 6db, 150 mm)`，`db` 为最小主抗弯纵筋直径，不含构造 skin bars。
- 18.6.4.6：其他区域仍须采用两端带 seismic hook 的 stirrups，间距不得大于 `d/2`，并同时满足普通剪力和抗扭限制。
- 18.6.4.2、25.7.2：每个角筋和交替的周边纵筋应获得箍角或拉筋的横向支承；未直接支承的纵筋在箍边任一侧距已支承纵筋的净距不得大于约 150 mm。腿数必须根据实际纵筋坐标校核，不能只按梁宽估算。
- 18.6.5：SMF 设计剪力应由两端相反符号的 probable moment strength 与重力共同确定；满足条文条件时端部 `2h` 区取 `Vc=0`。只有确认 ETABS 输出已经采用正确 SMF 设计类型与 capacity shear 后，才可直接消费其 `Av/s`。

“主梁 PB”不自动等于“SMF 梁”。`is_smf` 必须来自 ETABS 设计类型或明确项目配置。

## 3. 项目排筋策略

以下内容不是 ACI 的通用条文，而是用户已经确认、模块必须执行的项目规则：

1. 顶、底纵筋分别按左/中/右三个区域输出；箍筋按左端 `2D`、跨中、右端 `2D` 三个区域计算，但梁表仍可把两端统一为控制值 `S2`、跨中为 `S1`。
2. **已确认**：每个位置先反算 `AsFlexTop=max(0,AsTopTotal-Al/2)`、`AsFlexBot=max(0,AsBotTotal-Al/2)`，用 AsFlex 设计真正的受弯钢筋；ETABS `AsTop/AsBot` 只作对照。AsFlex 允许小于 `AsMin`，因为正常模式下顶、底各计入 `Al/2` 后的 Total 仍满足最小纵筋要求。
3. 正常模式直接按 `AsTopTotal/AsBotTotal` 选取顶、底组合纵筋。如果该组合排布在净距、箍筋 legs 和保护层约束下不超过两层，则不启用抗扭 face 转移。
4. **已确认**：两层上限只适用于“受扭纵筋继续放在顶、底”的组合排布，不适用于受弯筋。若 Total 组合排布需要超过两层，则切换为 `FACE_TORSION_MODE`：顶、底改按 AsFlex 单独选筋；AsFlex 本身需要三层或更多层时仍照常排布，不能削减、截断或让 face rebar 代替受弯筋。
5. face 模式下，抗扭纵筋与受弯筋完全分路：两侧 face rebar 各按 `Al/2` 设计，不再计算 `AlCredit/AlSup`，也不以受弯选筋取整余量抵扣 `Al`。程序必须复核 `AsFlexDesignTop/Bottom >= AsFlexTop/Bottom`；同时若抗扭从顶、底移走后导致受拉面实际纵筋低于必须单独满足的 `AsMin`，应返回 `MIN_FLEXURE_CONFLICT`，不得由侧面 face rebar 补足。本项目预期该冲突不会出现在触发两层转移的截面，但实现仍须防守。
6. 任一左/中/右区域触发 face 模式后，整跨统一采用 `FACE_TORSION_MODE` 和同一种 face bar 规格，并由左/中/右最大 `Al/2` 控制。
7. 梁高 `h >= 800 mm` 时才添加默认构造 face rebar，采用 T10、最大间距 250 mm；单侧中间筋根数按 `max(0, ceil(Lside/250)-1)` 计算。`h < 800 mm` 且未触发抗扭 face 模式时，梁表 face rebar 填写 `N.A.`（Not Applicable），不得填 0 或留空。抗扭 face 模式不受 800 mm 门槛限制。
8. **先设计箍筋，再设计纵筋**：先由剪扭强度、规范最小量、间距和 SMF 要求确定箍筋直径与 legs；随后顶部、底部纵筋根数均不得少于 legs，使每一箍腿均有对应的纵筋构造位置。初选纵筋坐标若反过来要求增加 SMF 横向支承腿数，则重新选纵筋，迭代至 legs 和纵筋根数稳定。
9. **多排纵筋每排至少 2 根**：不得输出仅 1 根的末排。若贪心排布形成 `n/1`，应在钢筋总根数不变时从前一排移 1 根到末排，例如 `6T25 5/1` 调整为 `6T25 4/2`、`4T25 3/1` 调整为 `4T25 2/2`。若截面每排容量小于 2 或无法通过重新分配/最小增配形成每排至少 2 根，则该候选方案判为不可排布，不得偏置放置单根钢筋。
10. 箍筋间距只从 100、150、200 mm 中选择；优先最大合规间距，再选直径与腿数。腿数最终取强度、最小配筋及 SMF 实际横向支承三者的控制值。
11. **已确认钢筋目录分层**：模块级完整备选直径为 `{10, 12, 14, 16, 18, 20, 22, 25, 28, 30, 32, 36, 40}` mm；本次项目启用直径仅为 `{10, 12, 16, 20, 25}` mm。选筋引擎不得输出未在项目启用目录中的直径，但应能在其他项目配置中直接启用完整目录，无需修改算法。
12. 本项目箍筋优先直径为 `[10, 12]` mm，纵向主筋优先直径为 `[20, 25]` mm；完整项目启用目录作为扩展备选。优先目录通过用户接口勾选，不写死在计算器中。默认 face bar 仍为 T10；承担抗扭时从允许目录中按需求选取。
13. 同一 Beam Line 的相邻跨在公共支座处，顶部和底部钢筋分别进行连续性协调，并保证对应连续钢筋直径一致。用户接口提供两种方案：
    - `OPTION_1_STRICT_EQUAL`：公共支座两侧采用完全相同的钢筋根数和直径组合，以两侧较大需求控制。
    - `OPTION_2_COUNT_ADJUSTMENT`：两侧需求差异较大时，保持共同钢筋的直径、位置和排列基线一致，只通过增减根数满足较大一侧。例：需求 4400/2900 mm² 时采用 9T25（6/3）与 6T25，而不是把 9T25 排成 7/2；6 根为贯通基线，较大侧增加第二排 3 根。需求 1600/2500 mm² 时可采用 2T25+2T20 与 4T25+2T20，公共的 2T25+2T20 保持连续，较大侧只增加 2T25。
14. 已由用户确认：ETABS `VMajorArea`（`Av/s`）已经包含 ACI 318-14 18.6.5 capacity shear；程序保留该来源标记，不再另行叠加一次容量设计剪力。

算法顺序为：读取并归一化 `AsETABS/AsTotal/Al` → 反算 `AsFlex=AsTotal-Al/2` → 选择各区箍筋与控制 legs → 先尝试按 Total 生成不超过两层的组合纵筋候选 → 任一区失败则整跨进入 face 模式，顶/底改按 AsFlex 生成不限两层的受弯筋，两侧各按最大 `Al/2` 生成 face bars → 校核 AsFlex、AsMin、净距和 SMF 横向支承 → 按所选 option 协调相邻跨公共支座钢筋并迭代至稳定 → 输出梁表及控制原因。

## 4. 算例一：500×700 SMF 主梁，普通剪扭组合

### 4.1 条件与 ETABS 假定需求

- `b x h = 500 x 700 mm`，保护层 40 mm（至箍筋外缘）；
- `fc' = 35 MPa`，`fy = fyt = 420 MPa`，最大骨料 20 mm；
- 主筋 T25，箍筋 T10；`d = 700-40-10-12.5 = 637.5 mm`；
- 钢筋面积：T10 = 78.5 mm²，T25 = 490.9 mm²。

| 位置 | AsTop | AsBot | AsTopTotal | AsBotTotal | Av/s | At/s | Al |
|---|---:|---:|---:|---:|---:|---:|---:|
| 左 | 2400 | 1500 | 2750 | 1800 | 1.20 | 0.40 | 800 |
| 中 | 1300 | 2200 | 1300 | 2200 | 0.75 | 0 | 0 |
| 右 | 2500 | 1500 | 2850 | 1800 | 1.10 | 0.35 | 800 |

这里的 Total 是 ETABS 输入字段，不是由 `As+Al/2` 计算得到。

### 4.2 按 Total 选筋的纵筋结果

| 区域 | 需求计算 | 顶筋结果 | 需求计算 | 底筋结果 |
|---|---:|---|---:|---|
| 左 ET1/EB1 | 2750 | 6T25=2945 | 1800 | 4T25=1963 |
| 中 T1/B1 | 1300（受4 legs控制） | 4T25=1963 | 2200 | 5T25=2454 |
| 右 ET2/EB2 | 2850 | 6T25=2945 | 1800 | 4T25=1963 |

同层最小净距为 `max(25,25,4x20/3)=26.7 mm`。该截面单层最多可排 8T25，以上均为单层。`As,min` 约为 1122 mm²，最大配筋率约 0.0092，小于 0.025。

本梁 `h=700<800 mm`，按 Total 选筋后没有超过两层，不触发抗扭 face 模式，因此 face rebar 输出 `N.A.`。

### 4.3 箍筋结果

- 左：组合需求 `1.20+2x0.40=2.00 mm²/mm`。T10@100 的外腿 `78.5/100=0.785 >= 0.40`；强度初算需 3 腿，按对称及横向支承取 4 腿。
- 中：需求 `0.75 mm²/mm`，4 腿 T10@200 提供 `4x78.5/200=1.57 mm²/mm`。
- 右：组合需求 `1.10+2x0.35=1.80 mm²/mm`。4 腿 T10@100 提供 `3.14 mm²/mm`，外腿抗扭也满足。
- SMF 端部间距上限为 `min(637.5/4, 6x25, 150)=150 mm`，故端部 100 mm 合规；跨中 200 mm 小于 `d/2`。

梁表暂定结果：

| ET1 | T1 | ET2 | EB1 | B1 | EB2 | S2 | S1 | Legs | Face/side |
|---|---|---|---|---|---|---|---|---:|---|
| 6T25 | 4T25 | 6T25 | 4T25 | 5T25 | 4T25 | T10@100 | T10@200 | 4 | N.A. |

首道 hoop 还应标记为距柱面不大于 50 mm；端部加密长度为 `2h=1400 mm`。

## 5. 算例二：450×1000 SMF 主梁，强扭触发 face 模式

### 5.1 条件与 ETABS 假定需求

- `b x h = 450 x 1000 mm`，保护层 40 mm；
- `fc' = 40 MPa`，`fy = fyt = 420 MPa`，最大骨料 20 mm；
- 主筋 T25，闭合箍 T12，face bar 候选 T20；
- `d = 1000-40-12-12.5 = 935.5 mm`；
- T12 = 113.1 mm²，T20 = 314.2 mm²，T25 = 490.9 mm²。

| 位置 | AsTop | AsBot | AsTopTotal | AsBotTotal | Av/s | At/s | Al |
|---|---:|---:|---:|---:|---:|---:|---:|
| 左 | 5500 | 4300 | 7500 | 6000 | 2.30 | 1.00 | 4000 |
| 中 | 3600 | 4800 | 4800 | 6000 | 1.00 | 0.55 | 2600 |
| 右 | 5100 | 4400 | 7000 | 5600 | 2.10 | 0.90 | 3300 |

箍筋内净宽约 346 mm；按 26.7 mm 最小净距，每层最多排 7T25、两层最多 14T25。左端顶部 `AsTopTotal=7500 mm²` 需要 16T25，超过两层容量，因此触发 `FACE_TORSION_MODE`；按已确认规则，任一区域触发后整跨统一采用 face 模式。

### 5.2 纵筋和 face rebar 结果

触发后，顶、底抗扭纵筋全部转移到两侧 face bars；顶部、底部只按 `AsFlex=AsTotal-Al/2` 设计受弯筋，不再按 Total 补筋，也不再使用受最小配筋率抬高的 `AsTop/AsBot`。本算例各区域需求和结果为：

| 区域 | AsFlexTop | 顶筋 | 排列 | AsFlexBot | 底筋 | 排列 |
|---|---:|---|---|---:|---|---|
| 左 | `7500-4000/2=5500` | 12T25=5891 | 7/5 | `6000-4000/2=4000` | 9T25=4418 | 5/4 |
| 中 | `4800-2600/2=3500` | 8T25=3927 | 4/4 | `6000-2600/2=4700` | 10T25=4909 | 5/5 |
| 右 | `7000-3300/2=5350` | 11T25=5400 | 6/5 | `5600-3300/2=3950` | 9T25=4418 | 5/4 |

两排净竖距暂取 30 mm，大于 25 mm；每排至少两根，且所有实配面积均不小于对应 AsFlex。若某一 AsFlex 需要三层或更多层，应继续排布受弯筋，不能由 face rebar 代替。

face 模式不再计算 `AlCredit/AlSup`。整跨由左端 `Al=4000 mm²` 控制，两侧各承担 `Al/2=2000 mm²`；6T20=1885 mm² 不足，7T20=2199 mm² 满足，因此两侧最终各取 7T20。默认构造 skin 的 3T20 数量低于抗扭需求，不控制。两侧钢筋关于截面中心对称，顶部、底部主筋分别满足 AsFlex 和适用的最小配筋要求；同时复核闭合箍内周边相邻纵筋间距不大于约 300 mm。

### 5.3 箍筋结果

| 区域 | 选配 | 外腿 At/s 提供值 | 总腿面积/s | 剪扭组合需求 |
|---|---|---:|---:|---:|
| 左 | 4腿T12@100 | 1.131 | 4.524 | 4.30 |
| 中 | 4腿T12@200 | 0.565 | 2.262 | 2.10 |
| 右 | 4腿T12@100 | 1.131 | 4.524 | 3.90 |

最外闭合箍中心线周长约 `ph=2532 mm`，抗扭间距上限为 `min(ph/8,300)=300 mm`。SMF 端部上限为 `min(935.5/4,6x25,150)=150 mm`，故 100 mm 合规；跨中 200 mm 同时满足抗扭和 `d/2` 限制。4 腿仍须通过实际纵筋坐标的横向支承检查；若不满足，应增加 legs，而不是改变已满足强度的判断。

梁表暂定结果：

| ET1 | T1 | ET2 | EB1 | B1 | EB2 | S2 | S1 | Legs | Face/side |
|---|---|---|---|---|---|---|---|---:|---|
| 12T25 7/5 | 8T25 4/4 | 11T25 6/5 | 9T25 5/4 | 10T25 5/5 | 9T25 5/4 | T12@100 | T12@200 | 4 | 7T20 |

## 6. 规范版本接口

配筋引擎不得直接散落 ACI 318-14 常数。建议依赖两个独立策略：

```text
BeamDetailingEngine
  ├─ CodeDetailingPolicy
  │    ├─ ACI318_14_DetailingPolicy   # 当前启用
  │    ├─ ACI318_19_DetailingPolicy   # 后续扩展
  │    └─ ...
  └─ ProjectDetailingPolicy
       ├─ allowed_spacings = [100, 150, 200]
       ├─ max_main_bar_layers = 2
       ├─ default_face_bar = T10@250
       ├─ default_face_bar_min_depth_mm = 800
       ├─ enabled_bar_diameters = [10, 12, 16, 20, 25]
       ├─ preferred_stirrup_diameters = [10, 12]
       ├─ preferred_longitudinal_diameters = [20, 25]
       ├─ adjacent_span_option = OPTION_1 | OPTION_2
       └─ tie_breaking_rules
```

模块另设不可变的 `BarCatalog`，完整支持直径 `[10, 12, 14, 16, 18, 20, 22, 25, 28, 30, 32, 36, 40]` mm，并由直径自动计算公称面积。`ProjectDetailingPolicy` 只引用其中本项目允许的 `[10, 12, 16, 20, 25]` mm；配置载入时必须验证启用值属于完整目录、顺序唯一且默认 face bar 直径可用。

`CodeDetailingPolicy` 负责规范限值、SMF、抗扭纵筋的四角/闭合箍内/周边间距要求、最小配筋与错误状态；`ProjectDetailingPolicy` 负责项目启用/优选直径、标准间距、两层抗扭转移阈值、先箍后纵、`AsFlex=AsTotal-Al/2`、face 模式每侧 `Al/2`、800 mm face 门槛、整跨 face 模式和相邻跨连续性 option。ETABS 读取及 Excel 输出只依赖统一结果对象，不感知具体规范版本或完整钢筋目录，从而使以后换版、增加直径或切换 option 时尽量不改 Excel 工作流。

每个输出结果应同时记录：

- `code_edition`、`code_policy_version`、`project_policy_version`；
- `strength_required`、`code_minimum`、`project_rule`；
- `governing_reason` 与所有失败/警告状态；
- 原始 ETABS 字段、设计组合、station 和单位。

## 7. 编码前需要确认的事项

1. `is_smf` 的可靠来源。ETABS `Av/s` 已由用户确认包含 18.6.5 capacity shear。

## 8. 第一版引擎实现与离线测试（2026-07-17）

已新增 `src/design/detailing.py`，将规范规则与项目选项分离：

- `ACI318_14_DetailingPolicy`：AsMin、净距、有效高度、箍筋间距、抗扭闭合箍周长和 SMF 端部限值；
- `ProjectDetailingPolicy`：完整钢筋目录、项目启用直径、箍筋/纵筋优选直径、100/150/200 间距、800 mm face 门槛和连续性方案；
- `BeamDetailingEngine`：先箍筋、后纵筋；`AsFlex=AsTotal-Al/2`；整跨 face 模式；默认 skin/抗扭 face；左/中/右结果；
- `coordinate_adjacent_spans()`：预留公共支座连续性 option 1/option 2，纯函数方式不修改 ETABS；
- `EtabsResultProvider.get_beam_design_total()`：读取 ETABS 23 `GetSummaryResultsBeam_2` 的 AsTotal、AsMin、AsProvided、VMajorArea、TLArea 和 TTArea，并保留旧字段兼容性。

离线算例与测试见 `tests/unit/test_detailing.py`：

1. 500×700 mm SMF 梁：T10 箍筋、全跨 4 legs、正常纵筋模式，face 输出 `N.A.`；
2. 450×1000 mm 强扭梁：Total 纵筋超过两层，整跨进入 `FACE_TORSION_MODE`，单侧 face 输出 7T20；
3. 1000 mm 非扭转深梁：默认构造 face 输出 3T10；
4. 相邻跨公共支座连续性与 ETABS 23 `_2` 字段适配器。

配筋原型形成时的完整测试为 `56 passed`、覆盖率 `80.47%`；加入 Object `302` 回归后的项目最新结果为 `57 passed, 1 skipped`、覆盖率 `80.51%`。已使用设计完成的 v6.4.3 副本固定真实模型输入适配：SMF 读取 `Concrete Beam Overwrites - ACI 318-14` 的 `FrameType`，截面、保护层、混凝土/钢筋材料和左/中/右 station 均由 ETABS 读取；`AsFlex` 在每个 station 先按 `AsTotal-Al/2` 反算，再取区域最大值。独立评审工作簿已生成，尚待工程师复核后接入 `src/workflow.py` 正式入口。

历史真实模型工作簿：`outputs/rebar_v643_20260718_practical_layers/ETABS_beam_rebar_review_v643_practical_layers.xlsx`。其三张表分别为“原始分跨数据”“配筋调整复核”“整理梁表”；黄色输入单元格支持 `8T25 6/2`、`2T25+2T20`、`N.A.`，公式计算实配面积并与模型计算面积比较。v6.4.3 旧拓扑共整理 3,074 跨，2,918 跨生成配筋，14 跨进入 face 模式；151 跨为 `NO_ALLOWED_SPACING`，5 跨无设计站点。2026-07-18 已加入“多排纵筋每排至少 2 根”规则，全量复算后原有 2,932 处单根末排文本全部消除，`配筋调整复核!U9` 由 `6T25 5/1` 调整为 `6T25 4/2`，成功配筋跨数保持不变。

v6.4.1 的旧 3,070 跨工作簿统计为 2,918 跨成功、147 跨 `NO_ALLOWED_SPACING`、5 跨无设计站点。Object `302` 的 PB 内部梁交点误分跨修正后，当前拓扑为 3,010 跨；因此上述 v6.4.1 3,070 跨和 v6.4.3 3,074 跨工作簿都只保留为配筋引擎/表格交互的历史证据，不能作为最终梁编号、跨映射或配筋成果。必须用新拓扑重新提取站点并生成 Excel，不能只删除旧表中的 60/64 行。

### 8.1 正式接入前新增的 fail-closed 审计

1. **147 个 `NO_ALLOWED_SPACING`**：优先核实保护层语义。当前疑点是 ETABS 纵筋 cover 被直接用于箍筋外缘净保护层，可能压缩了 `B 200X400` 等窄梁的可用排筋宽度。重算前必须把 `etabs_longitudinal_cover`、`clear_cover_to_stirrup_outer` 和 `max_aggregate_size` 分字段保存；不得通过强制采用 100 mm 或扩大允许间距来掩盖输入问题。
2. **36 个混合截面 Span**：不得静默采用第一个源 Object 的截面。若同一 Span 内截面确实变化，配筋引擎应按分段截面分别校核并明确控制截面；第一版无法处理时返回 `MIXED_SECTION_REVIEW_REQUIRED`。
3. **57 个 `Program Determined`**：`is_smf` 无法仅由 PB/SB 推断。ETABS Frame Type 仍为 Program Determined 且不能从设计结果/项目配置唯一解析时，返回 `FRAME_TYPE_UNRESOLVED`，不能默认按非 SMF 继续。
4. **7 个混合 Frame Type Span**：同一 Span 的源 Object 若具有不同抗震设计类型，必须进入 `MIXED_FRAME_TYPE_REVIEW_REQUIRED`；不能选择首段或多数值。完成工程确认后才允许由明确覆盖表统一。
5. 上述数量来自旧拓扑审计。按 3,010 跨重新生成后必须重新统计，并在工作簿 manifest 中记录模型哈希、ETABS/规范版本、拓扑版本、保护层来源及所有异常数量。

本次算例输出：`build/output/reports/primary_beam_detailing_examples_20260717.json`。其中算例一输出正常模式、T10@150/200、全跨 4 legs 和 `N.A.`；算例二输出 `FACE_TORSION_MODE`、单侧 7T20 face bars。算例输出是规则引擎的审查证据，不代表已替代 ETABS 设计或施工图审查。

## 9. ETABS 字段对照样本（API 4572）

只读连接模型：`build/output/model_writeback/EMHS-0606-P4700-GF.v6.2_writeback_20260716_155529.EDB`；当前单位 `N_mm_C`；Object `T3-F1-PB25-2.1` 共返回 22 条设计站点。

| 字段 | 控制值 | Station/mm | LoadCombo | 备注 |
|---|---:|---:|---|---|
| `VMajorArea` | 0.922865 mm²/mm | 7400 | `U7 SPEC YX` | 单一最大值 |
| `TTArea` | 0.468954 mm²/mm | 6825、7400 | `T2` | 两个站点并列最大 |
| `TLArea` | 864.938163 mm² | 6825、7400 | `T2` | 两个站点并列最大 |

完整 22 条记录见 `build/output/reports/T3-F1-PB25-2.1_design_fields_api4572.csv`。用户已完成 ETABS 界面/表格对照并确认最终字段契约：`VMajorArea` 仅为抗剪箍筋 `Av/s`，`TTArea` 为抗扭闭合箍单腿 `At/s`，`TLArea` 为总抗扭纵筋 `Al`。

ETABS 23 另提供 `GetSummaryResultsBeam_2`。对同一 Object 的逐站对照确认：旧接口 `TopArea/BotArea` 对应数据库表 `AsTop/AsBot`，不含抗扭纵筋但已经受 `AsMinTop/AsMinBot` 控制；新接口 `_2.TopArea/_2.BotArea` 对应 `AsTopTotal/AsBotTotal`，是 ETABS 组合受弯、受扭及适用时剪力后的顶部/底部总纵筋。公开字段没有给出应用最小配筋率之前的精确纯受弯面积。完整结果见：

- `build/output/reports/T3-F1-PB25-2.1_longitudinal_api4572.csv`：旧接口逐站结果；
- `build/output/reports/T3-F1-PB25-2.1_longitudinal_detailed_api4572.csv`：ETABS 23 `_2` 接口逐站结果；
- `build/output/reports/T3-F1-PB25-2.1_database_summary_api4572.csv`：含 `AsTopTotal/AsBotTotal` 的数据库表对照。

上述 Total 值不能用 ETABS `AsTop/AsBot + Al/2` 复算，因为 ETABS As 已受最小配筋阈值抬高。项目改为从 Total 反算真实受弯需求：例如 Station 400 mm，`Al/2=428.913`，得到 `AsFlexTop=1872.732-428.913=1443.819 mm²`、`AsFlexBot=1152.271-428.913=723.358 mm²`；后者可小于 ETABS `AsBot=853.637 mm²`，但正常模式下加回 `Al/2` 后的底部 Total 仍大于最小值。只有 Total 组合排布超过两层时才将抗扭纵筋完整转移至两侧 face bars，各按 `Al/2`；AsFlex 受弯筋自身允许继续排至第三层及以上。

## 10. 依据与版本控制

- [ACI 318-14 官方版本页](https://www.concrete.org/store/productdetail.aspx?ItemID=318U14&Language=English&Units=US_Units)
- [ACI 318-14 官方预览与目录](https://www.concrete.org/Portals/0/Files/PDF/Previews/318-14_preview.pdf)
- [ACI 318-14 与上一版条文对照](https://www.concrete.org/Portals/0/Files/PDF/318-14_CrossReference_2014to2011.pdf)
- [ACI 官方勘误入口](https://www.concrete.org/publications/documenterrata.aspx)
- [NIST：基于 ACI 318-14 的钢筋混凝土 SMF 设计指南](https://www.nist.gov/publications/seismic-design-reinforced-concrete-special-moment-frames-guide-practicing-engineers)

工程实现应以项目持有的、包含官方勘误的 ACI 318-14 正文为最终依据。本文件是算法规格与复核记录，不替代注册结构工程师对最终施工图的审查。
