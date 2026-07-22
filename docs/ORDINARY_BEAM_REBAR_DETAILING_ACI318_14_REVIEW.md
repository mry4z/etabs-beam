# SB 普通梁与 PB 梁配筋规则对照（ACI 318-14）

**状态**：工程规则评审稿  
**规范基线**：ACI 318-14（SI，最终以项目持有的授权正文及官方勘误为准）  
**关联文档**：`PRIMARY_BEAM_REBAR_DETAILING_ACI318_14_REVIEW.md`

## 1. 已确认的项目边界

1. `SB` 是本项目的普通梁类别，**不采用 ACI 318-14 Chapter 18 的任何要求**。
2. `PB` 是主梁拓扑/图纸类别，**不等于 SMF**。
3. PB 只有在 ETABS Frame Type 或经审核的项目 override 明确为 `Sway Special` 时，才叠加 18.6 SMF 梁规则。
4. PB 的 `Sway Ordinary`、`Sway Intermediate`、`Sway Special`、`Non-Sway` 和 `Program Determined` 必须分开处理；不能只用一个 `is_smf` 布尔值概括。
5. PB/SB 仍决定梁线识别、分跨、编号和图纸表型；设计 profile 只决定配筋规范策略，二者不得互相推断。

第 1 条是项目工程决策，不是对 ACI Chapter 18 适用范围的一般性解释。本项目程序必须无条件遵循。

## 2. 规则标签

| 标签 | 含义 |
|---|---|
| `CODE-COMMON` | ACI Chapter 9、20～25 中适用于相应普通非预应力梁的通用要求 |
| `SB-ORDINARY` | 本项目 SB 普通梁规则；明确排除 Chapter 18 |
| `PB-PROJECT` | 已确认用于 PB 梁表、排布或施工表达的项目规则，不等于 ACI 最低要求 |
| `PB-SMF-ONLY` | 仅适用于明确分类为 SMF 的 PB，主要来自 18.6 |
| `DECISION-REQUIRED` | 先前只针对 PB 定义，是否扩展到 SB 尚需工程师确认 |

## 3. SB 普通梁与现有 PB 规则对照

| 主题 | SB 普通梁基线 | 现有 PB 普通/项目基线 | PB-SMF 附加要求 | 分类/依据 |
|---|---|---|---|---|
| 受弯强度 | 按 ETABS 正确普通梁设计结果配筋，并满足 `φMn≥Mu` | 相同 | 相同，并控制塑性铰及相关容量设计 | `CODE-COMMON`；18.6 为 `PB-SMF-ONLY` |
| 最小受弯钢筋 | `As,min=max[0.25√fc'/fy·bw·d, 1.4/fy·bw·d]`；应保留 ETABS Req/Min/Provided 可追溯字段 | 相同 | 同时满足普通最小量 | 9.6.1 `CODE-COMMON` |
| 最大纵筋率 | 不套用 SMF 的 `ρ≤0.025`；仍须满足强度、应变、延性和可施工性 | PB 非 SMF 同 SB | `ρ≤0.025` | 18.6.3 `PB-SMF-ONLY` |
| 顶、底最少连续筋 | 不套用 SMF“顶底各至少两根通长”规则；仍须满足 9.7.3 的延伸/截断和 9.7.7 适用时的结构完整性连续筋 | PB 非 SMF 同 SB；PB 梁表可另定施工通长筋 | 顶部、底部至少各两根连续钢筋 | 普通条文需按授权正文核对；18.6.3 `PB-SMF-ONLY` |
| 正负弯矩强度比例 | 无 18.6.3.2 的 1/2、1/4 比例要求 | PB 非 SMF同 SB | 柱面正弯矩强度及沿跨正负弯矩强度须满足 18.6.3.2，并按 `Mn` 而非面积比较 | `PB-SMF-ONLY` |
| 锚固、截断和搭接 | 按 9.7.3、25.4、25.5；理论截断点外延伸、支座锚固及结构完整性要求均不得由三位置面积包络替代 | 相同 | 搭接位置、类型及约束须满足 18.6.3 的附加限制；不得把一般截断规则误写成 SMF 专属比例 | `CODE-COMMON` / `PB-SMF-ONLY` |
| 剪力设计基础 | 使用普通梁正确 Frame Type 下的 ETABS `Av/s` 及对应 Req/Min/Provided；不采用 18.6.5 `Mpr` 容量剪力 | PB 非 SMF同 SB | 采用 18.6.5 容量剪力；用户已确认 SMF 的 ETABS `VMajorArea` 包含该影响 | 22.5 / 9.6、9.7；18.6.5 `PB-SMF-ONLY` |
| 最小抗剪配筋 | 规范要求配置剪力筋时，显式校核 `(Av/s)min=max[0.062√fc'·bw/fyt, 0.35bw/fyt]`（mm²/mm），并保留例外条件与控制来源 | 相同 | 不因 SMF 间距要求而省略最小量校核 | 9.6.3 `CODE-COMMON`，精确适用条件按授权正文 |
| 普通剪力筋间距 | 低剪力：`s≤min(d/2,600)`；高剪力：`s≤min(d/4,300)`。高/低剪力状态必须来自可追溯输入 | 相同 | 梁端或预期屈服区另受 18.6.4 控制 | 9.7.6 `CODE-COMMON` |
| 抗扭箍筋间距 | 仅有抗扭需求时叠加 `s≤min(ph/8,300)`；必须采用适用的闭合箍构造 | 相同 | 与 SMF 限值共同取严 | 9.7.6 `CODE-COMMON` |
| 端部加密区 | SB 没有 Chapter 18 的 `2h` hoop 区；左/右 `2D` 只作为结果包络区 | PB 非 SMF 的 `2D` 同样只是项目输出区 | 柱面起 `2h` 及其他预期屈服区设 hoops；首道及间距按 18.6.4 | `SB-ORDINARY` / `PB-PROJECT` / `PB-SMF-ONLY` |
| SMF 端部间距 | 不适用 | PB 非 SMF不适用 | `s≤min(d/4,6db,150)`；`db` 必须取最终实配主纵筋中的规范控制直径 | 18.6.4 `PB-SMF-ONLY` |
| 箍筋 legs | 由剪力/抗扭面积需求和普通构造确定；多腿箍不应自动强迫顶底受弯筋根数等于 legs | PB 梁表当前采用一跨统一 Legs，并先箍后纵 | 还须按最终纵筋坐标校核角筋和交替周边筋的横向支承；“端部至少 4 legs”只是项目初选策略 | `SB-ORDINARY` / `PB-PROJECT` / `PB-SMF-ONLY` |
| 受扭纵筋 | `Al` 位于闭合箍内、四角有筋、周边间距满足要求；条文不要求按各边长度均匀分配，但布置应关于截面协调 | 相同 | 无独立 SMF 抗扭面积公式；与 SMF 构造共同满足 | 9.7.5 `CODE-COMMON` |
| 受扭纵筋直径 | 纵向抗扭筋直径不小于 `max(0.042s,9.5 mm)`；该式不是箍筋直径规则 | 相同 | 相同 | 9.7.5 `CODE-COMMON` |
| Total/AsFlex | 使用 ETABS `AsTopTotal/AsBotTotal`；`AsFlex=AsTotal-Al/2` 的字段逻辑可共用 | 相同 | 相同 | 已验证 ETABS 字段契约；排布策略另见下项 |
| 超过两排时转 face | ACI 没有“两排封顶”；是否把超出两排的抗扭纵筋转到两侧 face bars 尚未对 SB 确认 | PB 已确认：任一位置触发后整跨转 face 模式，受弯筋仍允许三排及以上 | SMF 不改变该项目策略，但须继续满足横向支承 | `DECISION-REQUIRED` / `PB-PROJECT` |
| 普通 side-face/skin | ACI 门槛按既有评审为 `h>900 mm`，并按裂缝控制布置；SB 是否采用更严格门槛尚未确认 | PB 项目规则为 `h≥800 mm` 时默认单侧 T10、最大间距 250；不足 800 时无扭填 `N.A.` | 无单独 SMF 门槛 | 9.7.2/24.3 `CODE-COMMON`；PB 为 `PB-PROJECT` |
| 保护层和净距 | 保护层须明确为箍筋外缘净保护层；同排净距 `max(25,db,4dagg/3)`，层间净距至少 25 mm | 相同 | 相同 | 20.6、25.2 `CODE-COMMON` |
| 左/中/右输出 | 可继续按左 `2D`、中区、右 `2D` 取站点包络，以保持 Excel 接口一致；这不是 Chapter 18 加密区 | PB 已确认采用该三位置梁表 | SMF 施工加密范围必须另按柱面和 `2h` 计算，不能用 Object 端点的 `2D` 代替 | `PROJECT POLICY` |
| 相邻跨协调 | 应按普通梁受力、锚固、截断和施工连续性设计；PB 的 option 1/2 不自动套用 | PB 已确认提供严格相同/只增减根数两种方案 | 还须满足 18.6.3 强度连续性 | `DECISION-REQUIRED` / `PB-PROJECT` |

## 4. 当前代码审计

| 位置 | 当前行为 | 对 SB/PB 的问题 | 建议 |
|---|---|---|---|
| `rebar_workbook.build_payload_from_schedule()` | 对所有梁用 `FrameType` 是否含 `Sway Special` 设置 `is_smf` | **违反已确认规则**：SB 仍可能进入 SMF 分支 | `beam_type==SB` 时强制解析为 `SB_ORDINARY_NO_CH18`；只有 PB 解析 Frame Type |
| 同上 | `Program Determined`、混合 Frame Type 已设置 `blocked_reason` | 该 fail-closed 行为正确，应保留；但 IMF/OMF 单一类型目前仍落入普通布尔分支 | PB 使用显式 profile enum；未实现 profile 阻断而非降级 |
| `ACI318_14_DetailingPolicy.maximum_stirrup_spacing()` | 普通上限无条件取 `min(d/2,600,300,ph/8)` | 无扭低剪梁被过度限制；高剪梁又缺少明确 `d/4` 分支，某些截面可能不安全 | 输入 `shear_regime` 和 `has_torsion`，按条件组合上限 |
| `BeamDetailingEngine._select_stirrup()` | 只消费一个 `Av/s`，未显式记录规范最小量和高剪阈值控制来源 | 无法证明普通 SB 已完成全部最低量/间距校核 | 传入 ETABS Req/Min 或等价可追溯量，输出 controlling reason |
| `BeamDetailingEngine.design()` | 箍筋初选使用启用目录最大纵筋直径 T25 | 若最终为 T20，SMF `6db` 真值为 120 mm，按 T25 得 150 mm，属于**潜在不安全** | 纵筋确定后用最终控制 `db` 复核并迭代 |
| `minimum_legs()` + span-wide Legs | SMF 左右先取 4 legs，随后全跨采用最大 legs | 对 PB 是已确认表型；实际 SMF 横向支承仍未按坐标验证 | 保留 PB 初选策略，增加最终坐标校核；SB 采用独立普通策略 |
| `_select_longitudinal()` | 顶、底最少根数直接取 stirrup legs | PB“先箍后纵”策略泄漏到 SB；普通 SB 的多腿抗剪箍会无依据地增加受弯主筋 | SB 将受弯主筋最少根数与 legs 解耦；必要时另设构造/架立筋 |
| `_select_face_rebar()` | 所有梁统一执行 `h≥800` 的 PB face bar 规则 | 未经确认把 PB 项目规则应用到 SB | 根据 beam policy 分支；SB 暂按普通规范门槛，待用户确认项目加严值 |
| 正常/face 模式选择 | 所有梁统一执行“两排后抗扭转 face” | 该规则此前只针对 PB 确认，当前 SB 输出依据不足 | SB 单独配置 `torsion_face_strategy`，未确认前 fail-closed 或保留审核状态 |
| `coordinate_adjacent_spans()` | option 1 与 option 2 当前都把两侧增至同一最大根数 | option 2 尚未实现“共同基线+需求大侧只增加根数” | 在 PB 流程中另行完成；SB 是否启用先由工程师决定 |

历史 147 个 `NO_ALLOWED_SPACING` 可能同时受保护层语义、候选间距、无条件 `ph/8` 等因素影响；未用当前模型重放前，不把任何单项认定为主因，也不预测修正数量。

## 5. 建议的稳定接口

```python
class BeamDetailingProfile(Enum):
    SB_ORDINARY_NO_CH18 = "SB_ORDINARY_NO_CH18"
    PB_ORDINARY = "PB_ORDINARY"
    PB_OMF = "PB_OMF"
    PB_IMF = "PB_IMF"
    PB_SMF = "PB_SMF"
    PB_CH18_14 = "PB_CH18_14"          # 仅项目明确要求时
    PB_REVIEW_REQUIRED = "PB_REVIEW_REQUIRED"

def resolve_profile(beam_type, frame_type, override=None):
    if beam_type == "SB":
        return BeamDetailingProfile.SB_ORDINARY_NO_CH18
    return resolve_pb_profile(frame_type, override)  # 未知/Program Determined 时阻断
```

普通剪扭间距策略应接收显式输入，而不是从梁号猜测：

```text
limit = min(d/2, 600)
if high_shear:  limit = min(limit, d/4, 300)
if has_torsion: limit = min(limit, ph/8, 300)
if profile == PB_SMF and in_smf_end_zone:
    limit = min(limit, d/4, 6*selected_db, 150)
```

Excel 三表结构不需要改变。只需在中间数据增加 `detailing_profile`、`profile_source`、`shear_regime`、`has_torsion`、`spacing_control`，现有梁表继续消费最终配筋结果。

## 6. 可立即实现与仍需确认

### 6.1 可立即实现

1. 强制 `SB → SB_ORDINARY_NO_CH18`，禁止读取 Frame Type 后升级为 SMF。
2. PB 用显式 profile 替代 `is_smf`；`Program Determined` 和未实现的 profile 继续 fail-closed。
3. 把普通、高剪、抗扭和 PB-SMF 间距限值拆成条件策略。
4. 用最终实配 `db` 复核所有依赖纵筋直径的间距。
5. SB 的受弯主筋最少根数与箍筋 legs 解耦。
6. 把 PB 专属的 face、两排转 face、相邻跨协调策略从通用引擎参数中显式分离。

其中第 3 项只有在 ETABS Req/Min 或等价高剪判定输入可追溯后才可输出 `OK`；输入缺失时必须阻断，不能猜测。

### 6.2 需要工程师确认

1. SB 顶、底施工最少根数是否统一采用各 2 根；若多腿箍需固定点，是否另设架立筋而不增加受弯主筋。
2. SB 是否沿用 PB 的“超过两排后把抗扭纵筋转移到两侧 face”策略。
3. SB side-face 是否仅执行 ACI `h>900 mm`，还是采用项目加严的 `h≥800 mm、T10@250`。
4. SB 箍筋 legs 是否允许左/中/右不同，或为梁表简化采用全跨最大 legs。
5. SB 相邻跨是否设置独立连续性 option；不得直接复用当前 PB option 1/2。
6. PB 非 SMF 是否全部保留现有 PB 项目规则，仅删除 18.6 附加项。

## 7. 验收测试矩阵

| 场景 | 预期 |
|---|---|
| SB 源 Object 的 ETABS Frame Type 为 `Sway Special` | 仍解析为 `SB_ORDINARY_NO_CH18`，不出现 2h、6db、Mpr 或 SMF 4-leg 初选 |
| SB、低剪、无扭 | 间距上限为 `min(d/2,600)`，不受 `ph/8` 或固定 300 mm 控制 |
| SB、高剪、无扭 | 间距上限为 `min(d/4,300)` |
| SB、有扭 | 叠加 `min(ph/8,300)` 并完成闭合箍和 Al 构造校核 |
| SB 由剪力需要 4/6 legs | 不自动把顶部、底部受弯主筋最少根数提高到 4/6 |
| SB、h=800、无扭 | 在 SB face 策略确认前标记审核；不得静默继承 PB T10@250 |
| PB + Non-Sway | `PB_ORDINARY`，不应用 18.6 |
| PB + Sway Special | `PB_SMF`，应用 18.6 并用最终实配 db 复核 |
| PB + Sway Intermediate/Ordinary | 进入对应显式 profile；未实现时阻断 |
| PB + Program Determined | `PB_REVIEW_REQUIRED`，不得输出默认普通或 SMF 配筋 |
| 相邻 PB option 1/2 | 两方案产生不同且符合定义的结果；SB 未确认时不调用 |

## 8. 参考依据

- [ACI 318-14 官方版本页](https://www.concrete.org/store/productdetail.aspx?ItemID=318U14&Language=English&Units=US_Units)
- [ACI 318-14 官方预览与目录](https://www.concrete.org/Portals/0/Files/PDF/Previews/318-14_preview.pdf)
- [ACI 官方勘误入口](https://www.concrete.org/publications/documenterrata.aspx)
- [NIST：ACI 318-14 钢筋混凝土 SMF 设计指南](https://nvlpubs.nist.gov/nistpubs/gcr/2016/NIST.GCR.16-917-40.pdf)

官方免费预览只证明版本、章节结构和适用主题，不包含可替代授权规范正文的完整条文。本文采用工程转述，不复制长段标准原文；具体条号、例外条件、锚固/截断比例和构造措辞在编码前必须再对照项目持有的授权 ACI 318-14 及勘误。
