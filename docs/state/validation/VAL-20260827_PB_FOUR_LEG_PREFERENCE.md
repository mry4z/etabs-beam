---
id: VAL-20260827-PB-FOUR-LEG-PREFERENCE
type: validation
status: partial
owner: project
created: 2026-08-27
updated: 2026-08-27
related: [SPEC-DET-001, VAL-20260827-SB-TOP-PB38]
supersedes: []
superseded_by: []
---

# 主梁四肢箍优选与 PB38-1 重算

## 范围与用户决定

用户针对“六肢箍导致跨中至少六根上筋”的诊断，确认按“尽量四肢箍，不够先增加箍径，减少不必要纵筋”的原则修复。
本次在 `F:\2026\Research Study\etabs beam-v2` 实施，项目策略升级为 `nasiriyah-smf-sb-2026-08-27-r3`。

本轮只修改配筋候选优选及其测试/元数据，不修改或保存 ETABS 模型，不运行分析设计，不发布插件或推送远端。
插件选择集/分塔性能修复已按用户要求交给任务 `01a0430e-74f1-7881-b812-640aff575621`；其文件与本轮配筋文件分开负责。
工作区的前序未提交修改保留，没有重置、覆盖或将其合并提交。

## 实现与未改变的规则

1. PB 宽度不大于 300 mm 继续采用既有两肢优选；其余 PB 候选先比较超过优选肢数的数量，默认优选四肢以内。
2. 同属优选档时，依次优先较大的合法间距、配置中的箍径顺序、较少肢数。因此先在 T10/T12/T16/T20/T25 启用目录内增大箍径，而不是因偏好 T10 就选六肢或更多。
3. 四肢不是新增最低肢数或强制上限：已可满足的两肢保留，所有四肢以内候选不足时仍可选择六肢及以上。
4. 剪扭总量、外箍单腿抗扭量、高剪/有效高度/最终纵筋直径控制间距均先筛选；无可行候选仍失败，不降低需求。
5. PB 全跨肢数统一，纵筋根数仍不小于相应肢数；上部支座—跨中直径转换及相邻跨支座协调不变。
6. 选中的实际箍径与肢数进入原有纵筋—容量剪力迭代。最后继续复核几何净距、层数、配筋率、强度比例及容量剪力。
7. SB 上筋 T20 及以下优选/双筋同径、SB 箍筋排序、同跨统一底筋及跨间底筋独立、上部 1.2 倍人工考虑均不变。
8. 重算后写入 `project_policy_version`、`pb_preferred_max_stirrup_legs` 和 `pb_stirrup_selection_strategy`；仅投影旧数据不会伪造新策略证明。

该策略不是全梁钢筋重量最优化。既有纵筋直径/层数偏好可能使某一面面积稍增，不能宣称每一面或每一跨都节省重量。
搭接排布相容仍不代表搭接长度、禁区、支承及节点拥挤已验收。

## PB38-1 对照

按同一历史需求、相同纵筋策略，仅比较旧“箍径优先”与新排序：

| 项目 | 旧排序重算 | 新排序重算 |
|---|---|---|
| 梁跨 | T2-Story2-PB38-1 | 不变 |
| 截面 / mm | 400×800 | 不变 |
| ET1 | 6T25 4/2 | 6T25 4/2 |
| T1 | 6T25 4/2 | 4T25 |
| ET2 | 6T25 4/2 | 6T25 4/2 |
| 全跨 B1 | 6T20 | 4T25 |
| 近支座 S2 | T10@100 | T10@100 |
| 跨中 S1 | T10@150 | T16@200 |
| 全跨箍筋肢数 | 6 | 4 |

跨中原始上筋需求仍为 1129.5091989569646 mm²，并未复制支座需求或人为降低需求。
历史重放沿用其 ETABS 纵筋重心 cover=60 mm；单元测试另覆盖当前项目显式箍筋外缘净保护层 40 mm，两种口径不能混用。
原有 `CAPACITY_SHEAR_SOURCE_UNVERIFIED`、`SMF_SUPPORT_FACE_UNRESOLVED` 等提示保留，因此表中结果只是离线候选，不是施工图验收结论。

## 验证

针对性测试：

```text
python -m pytest tests/unit/test_detailing.py tests/unit/test_sb_top_preferences.py tests/unit/test_pb_stirrup_preferences.py tests/unit/test_rebar_workbook.py -q --no-cov
```

结果：95 passed。覆盖增箍径、更多肢数回退、禁用直径、单腿抗扭不足、高剪间距、两肢不被强制提升、PB/SB 窄梁策略、
SB 跨宽度/Profile 不变、实际配筋变化后的 Mpr/容量剪力重算与原有上筋协调。新增测试和离线验证脚本通过 Black 检查；
既有大文件仍有原有全文件格式差异，未为格式整理扩大修改范围。

历史对照脚本（输出目录必须新建，拒绝覆盖）：

```text
python tools/validate_pb_stirrup_preference.py --source "<历史 rebar_workbook_payload.json>" --output-dir "outputs/validation/pb-four-leg-20260827/replay-r01"
```

- 原始数据 SHA-256：`9004f44722e68ed22cb8564814956dec9eeb8676b09d27ecea8079d6683f30be`。
- 共 968 跨：PB 481、SB 487；三组后台行逐行对应、原始需求不变，原文件未修改。
- 242 跨的汇总结果变化，其中 147 跨降低箍筋肢数，没有增加肢数的跨。
- SB 的 raw/review/final 三组记录逐字段不变。
- 新旧均为 721 跨 `engine_status=OK`、247 跨非 OK；无新增引擎失败，SMF 校核状态未改变。
- 247 跨中包含 151 跨梁线 Profile 不一致、91 跨支座协调/迭代阻断及 5 跨数据不全/混合截面或类型，均未借此次优化绕过。
- `engine_status=OK` 仅表示生成了候选，不等于全部构造和工程核验通过。
- 比较脚本冻结旧排序以隔离本次改变，两组重算均使用当前的底筋/SB 等其他策略；不是把未重算的旧 Excel 当成基准。

本机证据：

- [对照摘要和代码 SHA-256](../../../outputs/validation/pb-four-leg-20260827/replay-r01/comparison.json)
- [完整重算后台数据](../../../outputs/validation/pb-four-leg-20260827/replay-r01/redetailed_payload.json)
- [95 项配筋相关测试](../../../outputs/validation/pb-four-leg-20260827/detailing-pytest.xml)
- [全仓测试首次记录](../../../outputs/validation/pb-four-leg-20260827/pytest.xml)

全仓测试首次运行处于另一任务迁移选择 API 的中间状态：334 passed、12 failed、1 skipped；12 项失败均位于该任务负责的
`test_etabs_tower.py`，没有修改其断言或用排除文件的结果冒充全仓通过。失败信息已交接，最终联合门禁须在插件任务稳定后重跑。
跳过项为需要显式开启的 ETABS 集成测试。

## 结论

本轮配筋修改及针对性离线重放通过，仍为 Partial：尚未完成插件联合回归、实模及图形工程师验收。
当前加载的已发布插件不会因源码修改自动更新；本轮没有生成新发布包。
