# SB 普通梁与 PB 梁配筋规则对照（ACI 318-14）

**状态**：工程规则评审稿  
**规范基线**：ACI 318-14（SI，最终以项目持有的授权正文及官方勘误为准）  
**关联文档**：`PRIMARY_BEAM_REBAR_DETAILING_ACI318_14_REVIEW.md`

## 1. 已确认的项目边界

1. `SB` 是本项目的普通梁类别，**不采用 ACI 318-14 Chapter 18 的任何要求**。
2. `PB` 是主梁拓扑/图纸类别，**不等于 SMF**。
3. PB 只有在 ETABS Frame Type 或经审核的项目 override 明确为 `Sway Special` 时，才叠加 18.6 SMF 梁规则。
4. **已确认**：PB 非 SMF 时保留现有 PB 项目构造规则，并按 Chapter 18 的 OMF profile 执行；ETABS 原始 Frame Type 仍保留为审计字段，`Program Determined`、未知或混合类型不得静默解析。
5. PB/SB 仍决定梁线识别、分跨、编号和图纸表型；设计 profile 只决定配筋规范策略，二者不得互相推断。

第 1 条是项目工程决策，不是对 ACI Chapter 18 适用范围的一般性解释。本项目程序必须无条件遵循。

## 2. 规则标签

| 标签 | 含义 |
|---|---|
| `CODE-COMMON` | ACI Chapter 9、20～25 中适用于相应普通非预应力梁的通用要求 |
| `SB-ORDINARY` | 本项目 SB 普通梁规则；明确排除 Chapter 18 |
| `PROJECT-SHARED` | 已确认同时用于 PB/SB 的梁表、排布或施工表达规则，不等于 ACI 最低要求 |
| `PB-OMF` | 所有已解析的非 SMF PB；执行现有 PB 项目规则并采用 Chapter 18 OMF profile |
| `PB-SMF-ONLY` | 仅适用于明确分类为 SMF 的 PB，主要来自 18.6 |

## 3. SB 普通梁与现有 PB 规则对照

| 主题 | SB 普通梁基线 | 现有 PB 普通/项目基线 | PB-SMF 附加要求 | 分类/依据 |
|---|---|---|---|---|
| 受弯强度 | 按 ETABS 正确普通梁设计结果配筋，并满足 `φMn≥Mu` | 相同 | 相同，并控制塑性铰及相关容量设计 | `CODE-COMMON`；18.6 为 `PB-SMF-ONLY` |
| 最小受弯钢筋 | `As,min=max[0.25√fc'/fy·bw·d, 1.4/fy·bw·d]`；应保留 ETABS Req/Min/Provided 可追溯字段 | 相同 | 同时满足普通最小量 | 9.6.1 `CODE-COMMON` |
| 最大纵筋率 | 不套用 SMF 的 `ρ≤0.025`；仍须满足强度、应变、延性和可施工性 | PB 非 SMF 同 SB | `ρ≤0.025` | 18.6.3 `PB-SMF-ONLY` |
| 顶、底最少筋与架立筋 | 顶、底受弯主筋各至少 2 根且主筋直径不小于 T16。箍筋固定点不足时以 T12 补足：括号形式如 `2T16+(2T12)` 不计受弯面积；AsMin 控制时不加括号，如 `2T16+1T12`，T12 面积计入该位置纵筋实配面积 | PB 仍保留“箍筋 legs 控制主筋最少根数”的既有项目策略；受弯主筋同样不小于 T16 | 顶部、底部至少各两根连续钢筋，并满足 18.6.3 的连续性要求 | 已确认项目规则；PB-OMF/SMF 分别叠加对应 Chapter 18 要求 |
| 正负弯矩强度比例 | 无 18.6.3.2 的 1/2、1/4 比例要求 | PB 非 SMF 同 SB | 柱面正弯矩强度及沿跨正负弯矩强度须满足 18.6.3.2，并按 `Mn` 而非面积比较 | `PB-SMF-ONLY` |
| 锚固、截断和搭接 | 按 9.7.3、25.4、25.5；理论截断点外延伸、支座锚固及结构完整性要求均不得由三位置面积包络替代 | 相同 | 搭接位置、类型及约束须满足 18.6.3 的附加限制；不得把一般截断规则误写成 SMF 专属比例 | `CODE-COMMON` / `PB-SMF-ONLY` |
| 剪力设计基础 | 使用普通梁正确 Frame Type 下的 ETABS `Av/s` 及对应 Req/Min/Provided；不采用 18.6.5 `Mpr` 容量剪力 | PB 非 SMF 同 SB | 采用 18.6.5 容量剪力；用户已确认 SMF 的 ETABS `VMajorArea` 包含该影响 | 22.5 / 9.6、9.7；18.6.5 `PB-SMF-ONLY` |
| 最小抗剪配筋 | 规范要求配置剪力筋时，显式校核 `(Av/s)min=max[0.062√fc'·bw/fyt, 0.35bw/fyt]`（mm²/mm），并保留例外条件与控制来源 | 相同 | 不因 SMF 间距要求而省略最小量校核 | 9.6.3 `CODE-COMMON`，精确适用条件按授权正文 |
| 普通剪力筋间距 | 低剪力：`s≤min(d/2,600)`；高剪力：`s≤min(d/4,300)`。高/低剪力状态必须来自可追溯输入 | 相同 | 梁端或预期屈服区另受 18.6.4 控制 | 9.7.6 `CODE-COMMON` |
| 抗扭箍筋间距 | 仅有抗扭需求时叠加 `s≤min(ph/8,300)`；必须采用适用的闭合箍构造 | 相同 | 与 SMF 限值共同取严 | 9.7.6 `CODE-COMMON` |
| 端部加密区 | SB 没有 Chapter 18 的 `2h` hoop 区；左/右 `2D` 只作为结果包络区 | PB 非 SMF 的 `2D` 同样只是项目输出区 | 柱面起 `2h` 及其他预期屈服区设 hoops；首道及间距按 18.6.4 | `SB-ORDINARY` / `PROJECT-SHARED` / `PB-SMF-ONLY` |
| SMF 端部间距 | 不适用 | PB 非 SMF 不适用 | `s≤min(d/4,6db,150)`；`db` 必须取最终实配主纵筋中的规范控制直径 | 18.6.4 `PB-SMF-ONLY` |
| 箍筋 legs | 由剪力/抗扭面积需求和普通构造确定；左/中/右统一采用全跨最大 legs；legs 不强迫增加 SB 受弯主筋，缺少的固定点由 T12 架立筋补足。梁宽不大于 300 mm 时优先比较满足要求的 2-leg 候选 | 同样采用全跨最大 Legs；PB 继续执行先箍后纵及主筋最少根数策略 | 还须按最终纵筋坐标校核角筋和交替周边筋的横向支承；“端部至少 4 legs”只是项目初选策略 | `PROJECT-SHARED` / `PB-SMF-ONLY` |
| 受扭纵筋 | `Al` 位于闭合箍内、四角有筋、周边间距满足要求；条文不要求按各边长度均匀分配，但布置应关于截面协调 | 相同 | 无独立 SMF 抗扭面积公式；与 SMF 构造共同满足 | 9.7.5 `CODE-COMMON` |
| 受扭纵筋直径 | 纵向抗扭筋直径不小于 `max(0.042s,9.5 mm)`；该式不是箍筋直径规则 | 相同 | 相同 | 9.7.5 `CODE-COMMON` |
| Total/AsFlex | 使用 ETABS `AsTopTotal/AsBotTotal`；`AsFlex=AsTotal-Al/2` 的字段逻辑可共用 | 相同 | 相同 | 已验证 ETABS 字段契约；排布策略另见下项 |
| 超过两排时转 face | 与 PB 相同：任一位置的 Total 组合排布超过两层后，整跨转 face 模式；受弯筋仍允许三排及以上 | 相同 | SMF 不改变该项目策略，但须继续满足横向支承 | `PROJECT-SHARED` |
| 普通 side-face/skin | 与 PB 相同：`h≥800 mm` 时默认单侧 T10、最大间距 250；不足 800 且无抗扭 face 时填 `N.A.` | 相同 | 无单独 SMF 门槛 | `PROJECT-SHARED`；同时满足适用的 9.7.2/24.3 通用要求 |
| 保护层和净距 | 保护层须明确为箍筋外缘净保护层；同排净距 `max(25,db,4dagg/3)`，层间净距至少 25 mm | 相同 | 相同 | 20.6、25.2 `CODE-COMMON` |
| 左/中/右输出 | 可继续按左 `2D`、中区、右 `2D` 取站点包络，以保持 Excel 接口一致；这不是 Chapter 18 加密区 | PB 已确认采用该三位置梁表 | SMF 施工加密范围必须另按柱面和 `2h` 计算，不能用 Object 端点的 `2D` 代替 | `PROJECT-SHARED` |
| 单跨搭接与相邻跨协调 | SB 上部允许左/中/右两次变化；公共支座两侧上筋规格、根数和分层相同。SB 下部采用一个通长基准直径，跨中不得减小，仅支座处可减筋；相邻跨公共支座的下筋直径相同，但根数可按各侧需求不同 | PB 上部一跨最多一次直径转换；PB 下部通长基准直径且跨中不减；公共支座按 option 1/2 协调 | SMF 还须满足 18.6.3 强度连续性 | 已确认项目规则 |

## 4. 当前代码审计

| 位置 | 当前行为 | 对 SB/PB 的问题 | 建议 |
|---|---|---|---|
| `rebar_workbook.build_payload_from_schedule()` | 已使用显式 profile；SB 永久解析为 `SB_ORDINARY_NO_CH18`，PB `Sway Special` 为 `PB_SMF`，其余已解析 PB 为 `PB_OMF` | 已按本次决定修正；同时保留 `frame_type/profile_source` | `Program Determined`、未知和混合 PB 继续 fail-closed；SB 的特殊 FrameType 只作来源警告，不能启用 Chapter 18 构造分支 |
| `ACI318_14_DetailingPolicy.maximum_stirrup_spacing()` | 普通上限无条件取 `min(d/2,600,300,ph/8)` | 无扭低剪梁被过度限制；高剪梁又缺少明确 `d/4` 分支，某些截面可能不安全 | 输入 `shear_regime` 和 `has_torsion`，按条件组合上限 |
| `BeamDetailingEngine._select_stirrup()` | 只消费一个 `Av/s`，未显式记录规范最小量和高剪阈值控制来源 | 无法证明普通 SB 已完成全部最低量/间距校核 | 传入 ETABS Req/Min 或等价可追溯量，输出 controlling reason |
| `BeamDetailingEngine.design()` | 箍筋初选使用启用目录最大纵筋直径 T25 | 若最终为 T20，SMF `6db` 真值为 120 mm，按 T25 得 150 mm，属于**潜在不安全** | 纵筋确定后用最终控制 `db` 复核并迭代 |
| `minimum_legs()` + span-wide Legs | 左/中/右已统一为全跨最大 legs | PB/SB 表型已确认；实际 SMF 横向支承仍未按坐标验证 | 保留全跨统一并增加最终坐标校核 |
| `_select_longitudinal()` | PB 继续以 legs 控制主筋最少根数；SB 已改为顶、底受弯筋至少 2 根，缺少固定点时输出括号 T12 架立筋 | 已按本次决定修正 | 继续补充窄梁无法容纳全部固定点的 fail-closed 测试和 Excel 公式解析 |
| `_select_face_rebar()`、正常/face 模式 | PB/SB 共用 `h≥800` T10@250 和“两排后整跨转 face” | 已由用户确认 | 保留共用项目策略；不得误写为 ACI 两排上限 |
| 单跨/Beam Line 协调器 | 已实现 PB 顶部一次转换、PB/SB 底部基准直径与跨中不减；payload 按 Beam Line/跨号迭代公共支座，上筋匹配，SB 下筋同直径但允许不同根数 | 基础协调已接入 Excel 工作流；PB option 2 的共同基线+较大侧增筋仍未完整实现 | 保留当前 fail-closed/不收敛阻断，并继续完成 option 2 和真实模型重放 |

历史 147 个 `NO_ALLOWED_SPACING` 可能同时受保护层语义、候选间距、无条件 `ph/8` 等因素影响；未用当前模型重放前，不把任何单项认定为主因，也不预测修正数量。

## 5. 建议的稳定接口

```python
class BeamDetailingProfile(Enum):
    SB_ORDINARY_NO_CH18 = "SB_ORDINARY_NO_CH18"
    PB_OMF = "PB_OMF"
    PB_SMF = "PB_SMF"
    PB_REVIEW_REQUIRED = "PB_REVIEW_REQUIRED"

def resolve_profile(beam_type, frame_type, override=None):
    if beam_type == "SB":
        return BeamDetailingProfile.SB_ORDINARY_NO_CH18
    if frame_type == "Sway Special":
        return BeamDetailingProfile.PB_SMF
    if frame_type in {"Unknown", "Program Determined", "Mixed"}:
        return BeamDetailingProfile.PB_REVIEW_REQUIRED
    return BeamDetailingProfile.PB_OMF
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
5. SB 的受弯主筋最少根数与箍筋 legs 解耦，缺少的箍筋固定点以 T12 架立筋补足，并用括号表达。
6. PB/SB 共用 face、两排转 face 和全跨最大 Legs；相邻跨策略按梁类别分派。

其中第 3 项只有在 ETABS Req/Min 或等价高剪判定输入可追溯后才可输出 `OK`；输入缺失时必须阻断，不能猜测。

### 6.2 本轮已确认决定

1. SB 顶、底受弯筋各至少 2 根；多腿箍缺少固定点时另设 T12 架立筋，文本用括号表达且不计入受弯实配面积。
2. SB 与 PB 共用“两排后转 face”、`h≥800 mm` T10@250 和全跨最大 Legs。
3. SB 公共支座左右的上部钢筋必须完全相同；下部钢筋根数不要求相同，但基准直径必须一致，并向各自跨内传播。
4. PB 非 SMF 保留现有 PB 项目规则，并按 Chapter 18 OMF profile 执行。
5. PB 上部按算例允许一次直径转换并在跨中形成唯一搭接区；PB 底部跨中不减。SB 上部可两次变化；SB 底部采用通长基准直径，跨中不减、支座可减。
6. 受弯主筋最小 T16；T12 只作架立用途，括号形式不计面积，AsMin 控制时不加括号并计入纵筋实配面积。

## 7. 验收测试矩阵

| 场景 | 预期 |
|---|---|
| SB 源 Object 的 ETABS Frame Type 为 `Sway Special` | 仍解析为 `SB_ORDINARY_NO_CH18`，不出现 2h、6db、Mpr 或 SMF 4-leg 初选 |
| SB、低剪、无扭 | 间距上限为 `min(d/2,600)`，不受 `ph/8` 或固定 300 mm 控制 |
| SB、高剪、无扭 | 间距上限为 `min(d/4,300)` |
| SB、有扭 | 叠加 `min(ph/8,300)` 并完成闭合箍和 Al 构造校核 |
| SB 由剪力需要 4/6 legs | 顶、底受弯主筋仍至少 2 根；不足固定点以 `(nT12)` 补足，例如 `2T16+(2T12)` |
| SB、h=800、无扭 | 输出项目构造 face bar T10@250 选筋结果 |
| 相邻 SB 跨 | 公共支座上筋文本相同；下筋直径相同但根数可不同，并把支座基准直径传播至各自跨内 |
| PB + Non-Sway/Sway Ordinary/Sway Intermediate | `PB_OMF`，执行现有 PB 项目规则和 OMF profile |
| PB + Sway Special | `PB_SMF`，应用 18.6 并用最终实配 db 复核 |
| PB + Program Determined | `PB_REVIEW_REQUIRED`，不得输出默认普通或 SMF 配筋 |
| 相邻 PB option 1/2 | 两方案产生不同且符合定义的结果；上、下筋均按 PB 规则协调 |

## 8. 参考依据

- [ACI 318-14 官方版本页](https://www.concrete.org/store/productdetail.aspx?ItemID=318U14&Language=English&Units=US_Units)
- [ACI 318-14 官方预览与目录](https://www.concrete.org/Portals/0/Files/PDF/Previews/318-14_preview.pdf)
- [ACI 官方勘误入口](https://www.concrete.org/publications/documenterrata.aspx)
- [NIST：ACI 318-14 钢筋混凝土 SMF 设计指南](https://nvlpubs.nist.gov/nistpubs/gcr/2016/NIST.GCR.16-917-40.pdf)

官方免费预览只证明版本、章节结构和适用主题，不包含可替代授权规范正文的完整条文。本文采用工程转述，不复制长段标准原文；具体条号、例外条件、锚固/截断比例和构造措辞在编码前必须再对照项目持有的授权 ACI 318-14 及勘误。
