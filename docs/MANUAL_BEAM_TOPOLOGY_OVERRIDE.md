# 人工梁线/梁跨分组 Override 规范

**状态**：设计冻结稿；核心数据契约列入 P0-1，ETABS 交互界面列入 P2 插件。

## 1. 目的

自动算法继续处理大多数直梁和规则折线梁。对于弧形梁、连续小直线段形成的曲线、复杂转角及多个近似候选路径，允许用户在 ETABS 中选择 Beam Object，并明确指定这些 Object 属于同一逻辑 Beam Line 或同一逻辑 Span。

人工分组只改变本项目的梁线/分跨、编号、DXF、Excel 和写回映射，不合并、删除或重建 ETABS Frame Object，也不改变结构分析模型。

## 2. 规定的两轮编号/写回交互流

正式流程固定为：

```text
自动拓扑分析与编号
    → 第一次 Unique Name 写回（安全对象）
    → 用户在 ETABS 中按当前可见梁号选择 Beam Object
    → 创建人工 Beam Line/Span Group 并交互确认 I→J
    → 程序重新分配/局部调整梁线号和跨号
    → 显示旧编号→新编号差异并再次确认
    → 第二次 Unique Name 两阶段写回
    → 重读 GUID/I→J/名称并重新生成 DXF、JSON 和 Excel
```

第一次写回的作用是给用户提供可见、可选择和可讨论的自动编号基线。存在 `REQUIRES_SPLIT` 的 Object 可以暂时保留旧 Unique Name，但必须显示在审计图层中；人工分组始终用 GUID 识别对象，因此不依赖第一次写回是否已经改名。

第二次写回只允许作用于工作副本，并在写回前另存检查点。用户取消任一确认时，不修改模型。

建议状态机：

```text
AUTO_PLANNED → AUTO_WRITTEN → MANUAL_DRAFT → MANUAL_CONFIRMED
             → RENUMBER_PLANNED → RENUMBER_WRITTEN → VERIFIED
```

## 3. 两类人工分组

### 3.1 `MANUAL_LINE`

选中的 Object 强制属于同一 Beam Line。组内仍按柱、等效柱和 PB 支承执行正常分跨。

适用场景：一条弧形或折线梁跨越多个真实结构支承，需要先锁定连续路径，但仍应按支承分成多跨。

### 3.2 `MANUAL_SPAN`

选中的 Object 强制组成一个逻辑 Span，并提供明确的 I→J 路径方向。

适用场景：两个真实支承之间由多个短直线段拼成的弧形梁或转角梁，自动角度/路径判断不可靠，但用户已确认其属于同一跨。

发现内部真实柱、等效柱或应当形成 SB 边界的 PB 支承时，必须输出 `MANUAL_SPAN_SUPPORT_CONFLICT`。普通确认不能跳过该警告；如工程师仍决定合并，必须进入二级 `force_support_override` 确认，填写原因并保存操作者、时间和冲突支承清单。自动批处理不得生成这种强制确认。

## 4. 数据权威与 ETABS Group

外部版本化 JSON 是人工 override 的权威数据；ETABS 原生 Group 只镜像成员，便于用户在模型中选择、着色和复核。

原因：ETABS Group 名称和 Object Unique Name 都可能改变，无法独立保存路径顺序、I 端、创建原因和失效状态。Object GUID 才是稳定身份。

建议的 ETABS Group 名称：

```text
ETB_MANUAL_LINE_<group_id>
ETB_MANUAL_SPAN_<group_id>
```

自动检测到“同一 Span 内含多个截面”时，先使用临时审查 Group：

```text
CODX_MS_<SpanID>
```

该 Group 不是人工 override 的权威记录，只用于在 ETABS 中按组着色、选择和高亮。用户判断结果必须进入外部 JSON：确认属于同一 step beam 时记录 `confirmed_span_ids`；判断自动归并错误时创建正式 `MANUAL_LINE/MANUAL_SPAN` override。未作决定前，相关 Object 的 I/J 与 Unique Name 均保持不变。

建议 JSON 结构：

```json
{
  "schema_version": "1.0",
  "model_fingerprint": "<source-edb-sha256-or-project-id>",
  "groups": [
    {
      "group_id": "MG-0001",
      "kind": "MANUAL_SPAN",
      "base_run_id": "AUTO-20260721-001",
      "story": "T2-F2",
      "object_guids_in_i_to_j_order": ["guid-1", "guid-2", "guid-3"],
      "object_names_at_creation": ["4412", "4413", "4414"],
      "automatic_span_ids_before_override": ["T2-F2-PB5-3", "T2-F2-PB6-1"],
      "i_end_point": {
        "point_name_at_creation": "P101",
        "coordinate_mm": [15272.9, 58563.4, 5050.0]
      },
      "reason": "弧形梁由短直线段拼接，人工确认属于同一跨",
      "force_support_override": false,
      "support_conflicts": [],
      "created_by": "user",
      "created_at": "2026-07-21T10:00:00+08:00"
    }
  ]
}
```

`object_names_at_creation` 仅供阅读和审计；重新加载时必须用 GUID 解析当前 Object 名称。

## 5. 创建时验证规则

插件读取 ETABS 当前选择集后，按以下顺序验证：

1. 只保留设计方向为 Beam 的 Frame Object；非梁对象必须报告并拒绝混入。
2. 所有成员必须位于同一楼层；跨楼层选择拒绝。
3. Object GUID 必须唯一且可读取。
4. 端点图必须形成一条连通链；断开、分叉或重复边拒绝。
5. 普通开链必须恰有两个端点；闭合环必须先由用户指定断点，第一版直接拒绝。
6. 允许相邻短段具有大于 15° 的转角，因为人工选择本身就是显式 override；但相邻端点间隙仍必须在坐标容差内。
7. `MANUAL_SPAN` 内部若存在真实柱、`rigid` 传播等效柱或 PB 支承，普通确认拒绝创建；只有二级 `force_support_override` 和书面原因可继续。
8. 同一 Object 不得属于两个不同 `MANUAL_SPAN`；人工组之间重叠时输出冲突并拒绝。
9. 混合截面可以保存为拓扑组，但必须记录 `MIXED_SECTION`；配筋模块仍按其独立 fail-closed 规则处理。
10. 插件必须显示排序后的路径和 I→J 箭头，用户确认后才能写入 override。

ETABS 的选择顺序不作为路径顺序。路径顺序必须由端点连通图计算；用户只负责选择 I 端或反转预览方向。

## 6. 第二轮拓扑与编号调整规则

人工确认后，程序重新运行拓扑，但必须以第一次自动结果作为编号基线，控制编号漂移：

1. 人工组成员替换其原自动 Beam Line/Span 归属。
2. 未受影响的楼层、Beam Line 和 Span 保留原编号。
3. 同一人工组涉及多个自动 Beam Line 时，默认保留沿 I→J 最先出现的既有梁线号；若发生类型冲突或编号占用，必须在预览中列出候选并由用户确认。
4. 受影响 Beam Line 内的跨号从 I→J 连续重排；只允许该梁线内必要的后续跨号变化。
5. 人工 Span 内 Object 按 I→J 顺序命名为 `<Span编号>.1`、`.2`、`.3`。
6. 程序生成逐 GUID 差异表：当前 Unique Name、原自动梁线/跨、人工组、新梁线/跨、拟写入 Unique Name、方向是否反转。
7. 第二次写回前必须在 ETABS/DXF 中高亮受影响对象；用户确认差异后才执行。
8. 第二次写回仍使用“当前名称→临时唯一名称→最终名称”，避免名称占用。
9. 写后按 GUID 重读名称和 I/J；再次运行同一 override 时应得到零变化或可解释最小变化。

## 7. 最终拓扑优先级

拓扑分析按以下优先级执行：

```text
有效 MANUAL_SPAN
    > 有效 MANUAL_LINE + 自动支承分跨
    > 自动 Beam Line/Span 算法
```

第二轮处理流程：

1. 从第一次写回后的 ETABS 副本读取当前名称、几何、GUID、柱点和 `rigid` 等效支承。
2. 加载人工 override，按 GUID 解析当前 Object，并关联第一次自动编号。
3. 验证楼层、几何指纹、连通性、路径顺序和支承冲突。
4. 先从自动分析候选集中占用有效人工组成员。
5. 对剩余 Object 执行自动 15° 梁线/分跨算法。
6. 合并人工与自动结果后，按第 6 节执行局部编号调整。
7. 生成编号差异预览并取得用户确认。
8. DXF、Excel、JSON 和第二次模型写回只消费确认后的同一结果。

人工组只锁定拓扑关系，不直接指定最终 `PB/SB` 序号。最终编号仍由统一编号策略产生；以后如需锁定图纸编号，应另设编号 override，不能复用拓扑分组字段。

## 8. I→J、写回与图纸方向

人工组必须保存唯一 I 端。所有组内 Object 按路径从 I→J 排序：

```text
<Span编号>.1, <Span编号>.2, <Span编号>.3, ...
```

第二次模型写回阶段使各 Object 局部 1 轴沿组路径连续；DXF/CAD 文字方向必须与人工组 I→J 一致。插件预览必须同时显示路径、I 端、箭头以及旧/新编号，避免梁表左/中/右与图纸方向相反。

## 9. 失效与诊断

每次运行都重新校验 override。出现以下情况时，不回退为静默自动判断，而是将组标记为 `STALE/BLOCKED`：

- 任一 Object GUID 不存在；
- Object 楼层、端点或截面几何指纹发生不可解释变化；
- 成员不再连通或形成分叉；
- 新增内部结构支承；
- 与其他人工组发生重叠；
- 模型指纹不匹配且无法证明是同一模型的受控副本。

诊断输出至少包含 group ID、GUID、当前 Object 名、坐标、失效原因和建议操作。DXF 使用独立醒目图层 `AUDIT_MANUAL_OVERRIDE`。

## 10. ETABS 插件交互

建议命令：

1. `Create Beam Line Group from Selection`
2. `Create Beam Span Group from Selection`
3. `Set/Reverse I End`
4. `Preview Selected Group`
5. `Edit Group Members`
6. `Delete Group`
7. `Revalidate All Overrides`
8. `Highlight Invalid Groups`

创建流程：程序第一次自动编号并写回 → 用户在 ETABS 中选择 Object → 插件读取选择集 → 验证并排序 → 显示 I→J 箭头、内部支承和当前自动编号 → 用户确认人工组 → 保存 JSON并镜像到 ETABS Group → 程序局部重算编号 → 显示逐 Object 旧名/新名差异 → 用户第二次确认 → 两阶段改名写回 → GUID/I→J/名称重读验证。

## 11. 第一版验收样例

- 多个小直线段组成的弧形梁，人工 `MANUAL_SPAN` 后只生成一跨。
- 同一弧形 Beam Line 中存在柱支承，使用 `MANUAL_LINE` 后仍按柱分跨。
- 选中断开的两组梁时拒绝创建。
- 选中分叉链、闭合环或跨楼层对象时拒绝创建。
- `MANUAL_SPAN` 内出现真实柱/PB 支承时拒绝创建。
- Object 改名后 GUID 仍能恢复分组、顺序和 I→J。
- 第一次自动写回后建立人工组，第二次仅调整受影响梁线的编号，其他楼层/梁线名称不变。
- 第二次写回前可审查逐 GUID 的旧编号→新编号映射，取消确认时模型保持第一次写回状态。
- 同一 override 重放时第二次写回计划为零变化或可解释最小变化。
- 删除成员或改变几何后 override 标记为 `STALE/BLOCKED`。
- Excel 左/中/右、DXF/CAD 文字方向和模型局部 1 轴保持一致。
