# 项目完成与开发状态清单

**更新日期**：2026-07-22
**项目路径**：`F:\2026\Research Study\etabs beam\`  
**当前阶段**：P0 已关闭；下一项为 P1-1 配筋引擎工程闭环

> 本文件是项目当前状态和 TODO 的唯一主清单。专题规则见 `docs/`；较早模型和旧算法成果仅作为回归证据，不代表当前可验收交付物。

---

## 1. 当前结论

项目已完成从“只读 MVP”到“可验证工程原型”的跨越：能够读取真实 ETABS 模型、识别 Beam Line/Span、整理 Object I→J、在模型副本上回写逻辑编号、运行分析与混凝土设计、生成 DXF/CSV 和三表配筋 Excel。

P0 已形成可重复的生产候选基线。后续主要风险为：

1. 当前模型仍有 29 个真实跨 Span Object、38 个未确认多截面 Span 和 6 个需求复核 Object；它们继续 fail-closed，不自动重建或强行改名；
2. 配筋引擎的保护层语义、相邻跨协调、SMF 最终实配复核和人工 override 尚未闭环；
3. 编号持久映射、CAD 中间 JSON 和 ETABS 内人工跨组 UI 属于 P1/P2；
4. 历史旧拓扑 EDB/DXF/Excel 只用于回归，最终 v6.4.3 基线以 run `20260722T083439Z-862d6985` 为准。

### 当前技术基线

| 项目 | 当前基线 |
|---|---|
| Python | 3.11 |
| ETABS | 22.7.0 / 23.3.1 均为正式支持宿主；DLL 主版本必须匹配 |
| 设计规范 | ACI 318-14 |
| ETABS 设计数据 | `GetSummaryResultsBeam_2` 为配筋引擎正式目标字段 |
| 固定导出单位 | `N_mm_C`；操作结束后恢复模型原始当前单位 |
| 梁编号格式 | `F1-PB5-3`、`RF-SB10-1`；跨号不含 `S` |
| Object 名称格式 | `F1-PB5-3.1`、`F1-PB5-3.2`；按逻辑跨内 Object 顺序编号 |
| 最近离线测试 | 140 passed、1 skipped；覆盖率 78.17% |
| 代码质量 | `flake8 src tests`：0 |
| Git | 已有 23 个提交；当前工作区仍有修改和未跟踪核心文件，尚未形成最新基线提交 |

---

## 2. 已完成或已有可用原型

### 2.1 ETABS 读取与基础工作流

- [x] 通过 pythonnet 加载 `ETABSv1.dll` 并连接 ETABS；开发验证脚本支持按 PID 连接。
- [x] 读取模型路径、当前单位、梁/柱设计方向、截面、材料、端点和全局坐标。
- [x] 导出期间临时切换为 `N_mm_C`，正常或异常退出均恢复原始单位。
- [x] 排除非混凝土、无截面及名称含 `rigid` 的辅助梁，不进入混凝土配筋表。
- [x] 区分“有结果、无结果、不纳入设计、API 失败、部分结果”五类设计状态。
- [x] ETABS 23 `GetSummaryResultsBeam_2` 已验证 `AsTotal/AsMin/AsProvided/VMajorArea/TLArea/TTArea` 字段。

### 2.2 Beam Line、Span 与图形复核

- [x] 梁 Object 先形成完整 Beam Line，再按结构支承分 Span。
- [x] 主梁按柱/等效柱分跨；次梁按主梁分跨；次梁—次梁交点不增加跨数。
- [x] 同一 Beam Line 可跨截面变化，截面变化不再自动拆成新 PB/SB 编号。
- [x] Step beam 自动归一：同层、共点、15° 内连续且连接点无结构支承时，不同截面 Object 保持同一跨；已加入 `T3-F1-PB15/PB16` 等价回归。
- [x] 多截面 Span 写回 fail-closed：生成 JSON/CSV 清单和 `CODX_MS_*` ETABS 彩色审查 Group；未确认前 I/J 与 Unique Name 均标记 `REQUIRES_SECTION_REVIEW`，确认文件与选中高亮入口已接入 CLI。
- [x] 同一几何路径允许 PB→SB 角色转换；弧形主梁独立编号；悬挑单独成跨。
- [x] 支持楼层隔离和楼层间纯几何并行分析。
- [x] DXF 一跨一条多段线；PB 黄色、SB 绿色；编号位于沿程长度中点。
- [x] Object `302` 回归：两端均为柱，PB 内部无柱梁交点不再新增跨；只读重算仅贡献 `T1-F1-PB9-3`。

详细规则见 [BEAM_LINE_SPAN_RULES.md](docs/BEAM_LINE_SPAN_RULES.md)。

### 2.3 模型方向与编号写回原型

- [x] `src/core/model_writeback.py` 已实现方向计划、改名计划、I/J 反向、两阶段改名及写后验证原型。
- [x] 局部 1 轴通过 `EditFrame.ChangeConnectivity` 交换 I/J，不使用 `SetLocalAxes` 冒充反向。
- [x] 内部身份使用 Object GUID，不依赖可变 Unique Name。
- [x] v6.2 与 v6.4.1 模型副本完成过全量方向和可安全 Object 改名测试。
- [x] 已验证 ETABS I→J、梁表左/中/右和预设图纸文字方向可保持一致。
- [x] 两阶段改名规避目标名称占用；跨多个 Span 的 Object 使用 `REQUIRES_SPLIT` 阻塞，不静默选取一个编号。
- [x] 同一 Span 存在多个截面时不静默使用首截面或直接写名；须先确认 step beam，或转入人工 Line/Span override。

当前仅是经过实模验证的原型；正式事务、全属性等价比较和失败回滚仍属 P0。

### 2.4 ACI 318-14 配筋排布原型

- [x] `src/design/detailing.py` 已隔离规范策略与项目选筋策略。
- [x] 采用每个 station 的 `AsFlex=max(0, AsTotal-Al/2)` 计算真实受弯需求。
- [x] 先设计箍筋，再按 legs 确定纵筋最小根数；多排纵筋每排至少 2 根。
- [x] Total 排布超过两层时整跨进入 face 模式；受弯筋自身允许三层及以上。
- [x] 梁高 `h>=800 mm` 才默认设置 skin/face rebar；较浅梁未触发抗扭 face 模式时输出 `N.A.`。
- [x] 本项目纵筋优选 `[20,25]`、箍筋优选 `[10,12]`，完整可选直径目录已预留。
- [x] 三表 Excel 已实现：原始分跨数据、配筋调整复核、整理梁表；配筋文本公式支持面积复核。
- [x] 已消除 `n/1` 的单根末排排布。

详细规则和算例见 [PRIMARY_BEAM_REBAR_DETAILING_ACI318_14_REVIEW.md](docs/PRIMARY_BEAM_REBAR_DETAILING_ACI318_14_REVIEW.md)。

---

## 3. 当前成果有效性

| 成果 | 状态 | 说明 |
|---|---|---|
| `EMHS-GF.v6.4.1_writeback_20260718_004759.EDB` | **已失效为最终交付** | 基于旧 3,070 跨算法；仅保留写回、分析和设计回归证据 |
| `beam_spans_structural_v641_20260718_004759.dxf/.csv` | **已失效为最终交付** | 需按最新拓扑重新生成 |
| `ETABS_beam_rebar_review_v641_writeback_20260718_004759.xlsx` | **已失效为最终交付** | 需按最新拓扑重新生成 |
| v6.4.3 的 3,074 跨配筋 Excel | **回归参考** | 配筋规则证据有效，但拓扑编号需重新评估 |
| `rebar_v643_20260718_practical_layers` | **旧算法下较可靠的配筋基线** | 已消除单根末排；不能作为最终梁号交付 |
| `topology_p0_20260720_*` | **当前 v6.4.1 P0-1/P0-2 验证成果** | 只读拓扑、写回分析设计、保存重读及 `_2` Excel 均已验证；24 个跨 Span Object 仍阻塞 |
| v6.4.1/v6.4.3 分析日志 | **有效验证证据** | 未发现负特征值；分析/设计返回码和持久化验证有效 |

最新修正后的 v6.2 基线为：4,166 个混凝土梁 Object、约 1,205 条 Beam Line、3,010 个 Span、24 个 `REQUIRES_SPLIT` Object；本次 v6.4.1 副本实测为 1,202 条 Beam Line、3,009 个 Span。不同模型版本的数量差异必须记录在 manifest，不以时间戳判断“最新”。

---

## 4. 后续 TODO（按依赖和优先级排序）

### P0 — 当前阶段必须完成

#### P0-1 冻结梁线、支承与分跨拓扑

**状态：算法与只读实模门禁已完成（2026-07-21）；ETABS 内交互选择界面留在 P2 插件阶段。**

- [x] 实现名称含 `rigid` 的柱端刚域将真实柱支承传递至与主梁连接的另一端；`rigid` 自身仍不进入梁表。当前为只读传播原型，并已覆盖锚定/未锚定链测试。
- [x] 用真实 ETABS 23.3.1 模型核对刚域传播后的梁支承链；无法安全改名的对象均进入机器可读审核清单，不静默判为 SB。基准输出见 `build/output/topology_p0_20260720_baseline/requires_split_audit.{json,csv,dxf}`。
- [x] 梁线交点必须校验 Z 高程，平面投影相交但高程不同不得形成支承。
- [x] 支持梁 Object 内部落有真实柱/等效柱点，不再只从梁 Object 两端反推柱支承；`GetAllFrames` 批量读取坐标并按楼层 Z 包络过滤。
- [x] 为 15° 折线连接输出歧义诊断；多候选、分叉或闭环 fail-closed，长链保留完整 INFO 审计；存在 BLOCKED 时 CLI 拒绝写回。
- [x] 建立两轮人工拓扑 override 的稳定核心契约：按 Object GUID 保存 Line/Span Group、路径顺序、I→J、第一次自动编号和内部支承冲突；支持改名后重载、失效检测、二级强制确认和 JSON 往返。ETABS 内选择、差异预览、局部重排及第二次写回属于 P2 插件交互层，见 [MANUAL_BEAM_TOPOLOGY_OVERRIDE.md](docs/MANUAL_BEAM_TOPOLOGY_OVERRIDE.md)。
- [x] 固化 Object `302`、GF-GRD-F-PB17、GF/T1 连续次梁组、悬挑、弧形梁、次梁—次梁交点回归样例。
- [x] 对剩余 24 个 `REQUIRES_SPLIT` Object 生成 JSON/CSV 和醒目诊断 DXF，记录 GUID、端点、坐标、支承、所属跨及阻塞原因；当前 48 个动作已按 Object 去重为 24 行审核记录。

**验收**：全模型无未解释的梁线/Span 变化；每个源 Object 都被覆盖；PB 内部边界均为柱/等效柱；SB 内部边界均为 PB；不同 Z 交点不误分跨；所有阻塞项可机器复核。

#### P0-2 建立受 Git 跟踪的正式统一工作流

**状态：已完成（2026-07-22）。**

- [x] 将 `build/output/reports/*.py` 和 `build/spreadsheet_work/.../*.mjs` 中必要逻辑迁入正式源码；`build/` 只保存生成物。ETABS→Span→配筋负载及离线 redetail 位于 `src/rebar_workbook.py`，artifact-tool 三表生成器位于 `src/spreadsheet/build_rebar_workbook.mjs`。
- [x] 正式 CLI 支持 `--pid`、`--expected-model`、`--selection`、`--read-only`、`--writeback`、`--allow-partial-writeback` 和明确的输出目录，并增加 `--run-analysis`、`--run-design`、`--export-excel`。
- [x] CLI 增加多截面审查入口：`--prepare-section-review-groups`、`--select-section-review-group`、`--confirmed-multi-section-spans`；审查决定与 Excel 输出模块解耦。
- [x] 副本检查点、分析、设计、分跨、方向、改名、DXF、中间数据和 Excel 已在真实副本串联验证；默认阻塞门禁和失败后只读重放可从安全检查点重新开始。副本创建仍由 `create_working_copy`/准备脚本作为独立检查点调用。
- [x] CLI 移除模型路径和输出文件名硬编码；Excel 只消费 `BeamLineSpanResult`、`BeamDesignResult` 和 `SpanScheduleMapping` 统一数据契约。
- [x] 正式 ETABS 23 Excel 检查点使用 `GetSummaryResultsBeam_2`；当前配置仍由加载器拒绝非 ACI 318-14 规范。
- [x] CLI 增加 `--export-rebar-workbook`，正式生成“原始分跨数据—配筋调整复核—整理梁表”；Node/artifact-tool 路径和大模型堆内存均由参数传入，无需修改脚本。
- [x] 写回模式导出 Excel 时强制同时运行分析、设计，并在写回/设计后重新读取当前 Object 名和设计结果，禁止输出写回前旧结果。
- [x] 正式负载对多截面、混合 FrameType、`Program Determined` 采用 fail-closed，不再继承旧脚本的 `MIXED_SECTION_USE_FIRST` 行为。

**验收**：在新的干净 checkout 中，仅使用已跟踪源码、配置和指定模型即可复现完整成果，不需要修改脚本源代码。

### P0-2 三表迁移验收（2026-07-22）

使用历史 v6.4.1 的 3,070 跨 JSON 负载仅验证正式生成器，不把旧拓扑重新认定为最终成果。正式生成器成功输出：

- `outputs/p0_close_20260722/ETABS_beam_rebar_review_p02_formal.xlsx`，大小约 6.28 MB；
- 三张工作表及五个关键预览区域均通过视觉检查；
- artifact-tool 公式错误扫描为 0；
- 大模型运行显式采用可配置的 Node 堆上限，默认 8192 MB；
- 大体积 `.inspect.ndjson` 只作为瞬时 QA 文件，导出后自动删除；
- Python 全套测试 96 passed、1 skipped，覆盖率 78.12%；`flake8 src tests`、JavaScript 语法和 `git diff --check` 均通过。
- 已从 Git 索引树 `6de65a26d34ed87a7ce2b817feec63fc5fb37319` 创建隔离快照；快照不含 `build/`，仍通过 96 passed、1 skipped、`flake8` 和 JavaScript 语法检查，满足“仅使用已跟踪源码”的退出条件。

### P0-1/P0-2 实模验收记录（2026-07-20）

使用源模型 `build/output/model_writeback/EMHS-GF.v6.4.1.EDB` 创建全新副本，源 SHA-256 为
`8e18212e0b9595f87a7cdffe5552832dbecb5860423627eeaccf3b7a6381437e`；副本为
`EMHS-GF.v6.4.1_writeback_20260720_001559.edb`，创建时哈希一致，未覆盖源模型。

| 检查点 | 结果 |
|---|---|
| 只读拓扑 | 4,166 Beam Object、1,202 Beam Line、3,009 Span（PB 1,568、SB 1,441） |
| 阻塞审计 | 24 个 Object / 48 个动作；JSON、CSV、醒目 DXF 均已生成 |
| 默认写回门禁 | 未显式 `--allow-partial-writeback` 时拒绝写回；副本未被修改 |
| 方向与编号写回 | 显式允许安全对象部分写回；方向校验、两阶段改名、改名后方向校验均为 `true` |
| 分析与设计 | `RunAnalysis=0`、`StartDesign=0`；日志显示负特征值 `0`，`ANALYSIS COMPLETE` |
| 保存与重读 | `File.Save=0`；重读接受 ETABS 保存后的 `.$et` sidecar，原始单位仍为 `lb_in_F`，导出上下文为 `N_mm_C` |
| Excel | `beam_schedule_20260720_003447.xlsx`：统一梁表 4,166 行、设计站点 66,180 行、梁分跨 3,009 行；使用 `_2` 字段。配筋引擎三表格式已于 2026-07-22 迁入正式入口并用历史负载回归验证 |

本次证据目录：

- `build/output/topology_p0_20260720_baseline/`
- `build/output/topology_p0_20260720_writeback_design/`
- `build/output/topology_p0_20260720_postverify/`
- `build/output/topology_p0_20260720_excel/`

Object `302` 在本次新拓扑 CSV 中只贡献 `T1-F1-PB9-3`；跨 Span Object 仍按约束阻塞，未自动重建 Frame。

### P0-1 最终实模门禁（2026-07-21）

只读连接当前 ETABS 模型
`E:\051 SAP_ETABS MODEL\IRAQ-HOSPITAL\20260606\EMHS-GF.v6.4.3.EDB`，原始/导出/恢复单位均为 `N_mm_C`；未调用模型保存或写回。

| 检查点 | 结果 |
|---|---|
| 批量读取性能 | `GetAllFrames` 单次约 0.085 s；梁和柱/刚域等效支承完整读取约 4.6 s；最终全流程 67.5 s |
| 拓扑 | 4,110 个混凝土 Beam Object、1,215 条 Beam Line、3,020 个 Span（PB 1,573、SB 1,447） |
| 源 Object 覆盖 | 4,110/4,110；缺失 0、额外 0；缺失时生成 `SOURCE_OBJECT_UNCOVERED/BLOCKED` |
| 支承边界 | PB 内部边界仅允许柱/`rigid` 等效柱；SB 内部边界仅允许当前稳定 PB 路径；违反时 BLOCKED |
| 角色收敛 | 13 个楼层经过 PB/SB 固定点细化后稳定；循环或 6 轮内不收敛时 BLOCKED |
| 15° 诊断 | 54 条 `STITCH_LONG_CHAIN/INFO`；多候选/分叉/闭环 BLOCKED 为 0 |
| 跨 Span Object | 29 个 Object / 58 个动作进入 `REQUIRES_SPLIT` 审计；未静默改名 |
| T1-L1-SB18 修正 | 失效的临时 PB 标记 `(3572.9, 58563.4, 5050)` 不再形成跨；由 4 跨收敛为 3 跨 |
| 写回门禁 | `blocking_topology_diagnostics=0`；本次仍为只读，未执行写回 |

最终成果目录：`build/output/topology_p01_20260721_final3/`。其中包含完整梁跨 DXF/CSV、跨 Span Object 审计 JSON/CSV/DXF、拓扑诊断 JSON 和摘要。该当前模型与 2026-07-17 的本地验证副本 SHA-256 不同，因此不直接以旧 `1,209/3,074` 统计判定编号漂移；以本次模型路径、当前算法和诊断门禁作为新基线。

### P0-1/P0-2 收口状态

| 未完成项 | 当前原因 | 关闭条件 |
|---|---|---|
| 梁 Object 内部柱/等效柱点 | 已完成：所有柱/刚域等效点由批量 Frame 快照取得三维坐标，投影到梁内部并按楼层 Z 过滤。 | 真实 v6.4.3 重放无 PB 非柱内部边界。 |
| 15° 歧义诊断 | 已完成：多候选/分叉/闭环 BLOCKED，长链 INFO；写回门禁消费同一诊断数据。 | 最终重放 54 条长链 INFO、0 条拓扑 BLOCKED。 |
| Object 302、PB17、连续 SB、悬挑/弧形回归样例 | 已完成：具名纯几何 fixture 已进入默认测试。 | 后续算法变更由自动测试阻止规则回退。 |
| 人工 Line/Span override | GUID 数据契约、开链排序、I→J、内部支承二级确认、改名后重载和失效检测已完成。 | ETABS 内选择及两轮写回 UI 依计划在 P2 插件阶段实现，不再阻塞 P0-1 只读拓扑冻结。 |
| Step beam / 多截面 Span | 自动连续性判据、审查清单、写回门禁、ETABS Group 创建/选中 API 和确认文件已完成。 | P2 插件将把组列表、按组着色、确认 step beam/改建人工 override 做成同一交互面板。 |
| `build` 配筋细节三表迁移 | 已完成：负载适配、离线 redetail、Excel 公式/格式/渲染生成器和 CLI 均位于正式源码；历史 `build` 脚本不再是运行依赖。 | 已由受控历史负载重建 6.28 MB 三表工作簿并通过公式、渲染和全套测试。 |

#### P0-3 建立成果 manifest 和失效机制

**状态：已完成（2026-07-22）。**

- [x] 每次运行生成 UTC 时间加随机后缀的唯一 run ID；成功与失败运行均写入本地 manifest 和跨运行注册表。
- [x] 记录源模型/副本 SHA-256、实际模型路径、ETABS/API 版本、PID、单位、设计规范、Git commit/dirty 内容指纹、算法及 schema 版本、配置快照。
- [x] 记录分析/设计/保存返回码、Object/梁线/Span/异常计数，以及本次生成的 EDB、JSON、CSV、DXF、Excel 路径、大小和 SHA-256；目录成果采用按相对路径排序的聚合哈希。
- [x] 输入模型、配置、Git/算法、规范、ETABS/API 环境或关键选项变化时，通过确定性 input fingerprint 将注册表中的旧成功成果标为 `SUPERSEDED`；失败运行标为 `INVALID`，不使既有成功成果失效。

### P0-3 实模验收（2026-07-22）

- 当前唯一实例为 ETABS 22.7.0、PID 40352；本次只读验证按实际 DLL 记录 API file/product version `2.10.0.0` 和 DLL SHA-256，不把默认 ETABS 23 配置写入实测结果。
- 首次用工作区同名模型作为 `--expected-model` 时，API 实际路径位于 `E:\051 SAP_ETABS MODEL\IRAQ-HOSPITAL\20260606\`；路径门禁拒绝运行并生成 `INVALID` manifest，证明不会凭文件名误认模型。
- 按 API 实际路径重放得到 run `20260722T052708Z-9df0177f`：模型 SHA-256 `f2a7b2f8925eef19c86cc61dca8141b1233fdc0db4b05b9d94a2d77b8a1eca3a`，4,110 Beam Object、1,215 Beam Line、3,020 Span、29 个 `REQUIRES_SPLIT` Object、38 个未确认多截面 Span、拓扑 BLOCKED=0。
- 注册表验证状态为一条 `INVALID`、上一成功运行 `SUPERSEDED`、最新成功运行 `CURRENT`；旧运行由新 run ID 和 `INPUT_OR_ALGORITHM_FINGERPRINT_CHANGED` 原因显式关联。
- manifest 正式实现位于 `src/run_manifest.py`；CLI 新增 `--source-model` 和 `--manifest-registry`，模型副本运行时应显式提供未修改源 EDB。
- 从 Git 索引树 `a87b05bd365303cd62da565b43f85c1af7ec70ab` 创建不含 `build/` 的隔离快照，并在该快照目录通过 105 passed、1 skipped、覆盖率 78.65%、`flake8 src tests` 和 JavaScript 语法检查；`git diff --check` 同时通过。

#### P0-4 强化模型写回事务

- [x] 默认存在任何阻塞项时停止改名；只有显式 `allow_partial_writeback=true` 才允许安全对象部分写回。
- [x] 写前/写后比较 GUID、对象数量、I/J、变换矩阵、截面、材料、释放、端部偏移、荷载、组、铰、弹簧和混凝土设计覆盖项。
- [x] 增加幂等性：对已正确回写的副本再次执行，计划应为零变更或可解释最小变更。
- [x] 验证方向反转后重新分析设计的需求包络保持等价，左/右仅按预期交换。
- [x] 覆盖临时改名失败、最终改名失败、方向失败、保存失败和回滚失败路径。
- [x] 真实跨 Span Object 在安全分割与属性迁移实现前继续阻塞，不自动重建 Frame。
- [x] 未确认的多截面 Span 同时阻止方向与编号动作；显式确认后才按跨内 I→J 顺序生成 `.1/.2/...` 名称。

**验收证据（2026-07-22）**：ETABS 22.7.0 工作副本先完成 138 个方向反转的需求等价检查；6 个 `VMajorArea` 同时超过绝对/相对容差的 Object 被 fail-closed，方向和名称均不写入。最终纠正 run `20260722T083439Z-862d6985` 完成分析、设计、保存及 4,110 Object 全属性比较；保存重开后再次比较 4,110/4,110 零差异，重复构造计划得到方向动作 0、改名动作 0。曲线梁固定点中“本梁细分子段被当成外部 PB 交点”的方向依赖已修复并加入回归测试。临时/最终改名、方向、保存及回滚失败路径均有单元测试。

#### P0-5 用冻结算法重放真实模型全链路

- [x] 从未修改的 `EMHS-GF.v6.4.1.EDB` 创建全新副本，不在旧写回副本上叠加改名。
- [x] 执行方向整理、编号写回、分析、混凝土设计、保存重开和全属性验证。
- [x] 重新生成按楼层 DXF、CSV/JSON 中间数据和统一梁表 Excel；配筋引擎三表评审格式已在 P0-2 迁入正式入口。
- [x] 固定 Object `302` 只贡献一跨；记录最终 Span 和 24 个阻塞 Object。
- [x] 评估并按同一拓扑版本重做 v6.4.3 配筋基线。

**最终 v6.4.3 基线**：未修改源模型 `E:\051 SAP_ETABS MODEL\IRAQ-HOSPITAL\20260606\EMHS-GF.v6.4.3.EDB` SHA-256 保持 `6be0a975...a83c9fc`；最终工作副本 SHA-256 为 `29c8d2e8...5bf160d`。结果为 4,110 Beam Object、1,215 Beam Line、3,020 Span，29 个跨 Span Object、38 个未确认多截面 Span 和 6 个需求复核 Object 继续阻塞；BLOCKED 拓扑诊断为 0。最终 DXF、CSV/JSON、基础梁表和三表配筋工作簿位于 `build/output/p0_complete_v643_20260722_final3/`。

#### P0-6 收敛依赖、配置和质量基线

- [x] 修正 `requirements.txt` 中不存在的 `openpyxl>=3.7.0` 版本要求，改为受支持的 `<4` 版本范围。
- [x] README、配置和代码统一为 Python 3.11、ETABS 22/23、ACI 318-14 当前主路径，并集中处理版本调用差异。
- [x] 删除 `config.example.yaml` 中未被代码读取的字段，并由加载器校验当前仅支持 ACI 318-14。
- [x] 清除全部 Flake8 错误并通过 Black、pytest 和覆盖率门槛。
- [x] 把 `model_writeback.py`、`detailing.py`、测试和最新文档形成可复现基线提交。

**质量基线（2026-07-22）**：`black --check src tests tools`、`flake8 src tests tools`、正式工作簿生成器 JavaScript 语法和 `git diff --check` 均通过；pytest 为 122 passed、1 skipped，总覆盖率 78.01%。本次 P0 基线提交包含写回事务、配筋引擎、ETABS 22/23 兼容边界、真实模型验证工具、测试和同步文档。

### P1 — P0 完成后推进

#### P1-1 完成配筋引擎工程闭环

- [x] 建立 ACI 318-14 单排纵筋最大根数的 720 组合字典，并让配筋引擎统一调用；键使用工程尺寸，保护层明确为混凝土表面至箍筋外缘，非标准组合按同一公式后备计算。720 项逐项公式对照、窄梁边界和引擎接入测试均通过。
- [x] 固定配筋类别边界并形成 `docs/ORDINARY_BEAM_REBAR_DETAILING_ACI318_14_REVIEW.md`：SB 永久采用普通梁且不使用 Chapter 18；显式 `Sway Special` PB 使用 SMF，其余已解析 PB 保留现有项目规则并按 OMF profile 执行。
- [x] 重构设计 profile 解析：`beam_type=SB` 强制进入 `SB_ORDINARY_NO_CH18`；PB 分为 `PB_OMF`、`PB_SMF`、`PB_REVIEW_REQUIRED`，中间 payload schema 升至 `1.1` 并保留 `detailing_profile/profile_source/frame_type`；`Program Determined`、未知或混合 PB 继续 fail-closed，旧 payload 可由 `beam_type/is_smf` 兼容重算。
- [ ] 对照项目持有的 ACI 318-14 授权正文，将 `PB_OMF` 的 Chapter 18 OMF 条文逐项实现为可追溯校核；当前已完成 profile 路由、顶底最少筋和现有 PB 项目构造策略，但不得据此声称 OMF 全条文闭环。
- [ ] 修正普通梁剪扭间距：低剪、 高剪和抗扭条件分别启用 `d/2`、`d/4`、`ph/8` 限值；显式消费可追溯的最小 `Av/s` 和剪力 regime，缺输入时 fail-closed。
- [x] 落实 SB/PB 构造策略边界：两类梁共用“两排后整跨转 face”、`h>=800` T10@250 和全跨最大 Legs；SB 顶、底受弯筋各至少 2 根，箍筋固定点不足时输出 `(nT12)` 架立筋且不计入受弯实配面积；SB 相邻跨只统一公共支座上筋，下筋独立。

**P1-1 增量验证（2026-07-22）**：profile 路由、SB 括号 T12 架立筋、SB 支座仅协调上筋、旧 payload 兼容重算和 Review Required 离线重算阻断均已加入回归测试；全量 pytest 为 150 passed、1 skipped，覆盖率 78.45%，Flake8、Black、工作簿生成器 JavaScript 语法及 `git diff --check` 均通过。
- [ ] 明确区分 ETABS `GetRebarBeam` 的纵筋设计 cover 与施工构造使用的箍筋外缘净保护层，不共用含义不清的单一 `cover_mm`。
- [ ] 复核旧结果中 147 个 `B 200X400`、`NO_ALLOWED_SPACING` 梁跨；确认净保护层后，明确采用 75 mm 间距、调整截面/保护层或人工 override 的项目策略。
- [ ] 新拓扑重放后确认旧 5 个 127 mm 假碎跨和 `NO_RESULTS` 是否归零。
- [ ] 36 个混合截面 Span 必须 fail-closed、拆分或采用经确认的控制截面，禁止静默使用首截面。（正式负载适配器已 fail-closed；尚需最新模型重跑和逐 Span 人工决定。）
- [ ] 57 个 `Program Determined` 和 7 个混合 Frame Type 必须解析或要求项目显式指定，禁止默认按非 SMF。
- [ ] SB 若源 ETABS Frame Type 为 `Sway Ordinary/Intermediate/Special`，程序虽强制使用 `SB_ORDINARY_NO_CH18` 构造 profile，但 ETABS 原始 `Av/s` 等需求仍可能已包含 Sway/Chapter 18 设计影响；当前输出 `SB_ETABS_SWAY_DESIGN_RESULT_REVIEW`，后续需通过设计 overwrite/普通梁重设计取得严格的普通梁需求，不能假定 profile 路由能够反算并去除 capacity shear。
- [ ] PB 相邻跨 option 1/2 必须在整根 Beam Line 上联合协调，并覆盖混合直径、分层、对称和迭代稳定性；SB 已完成“公共支座上筋相同、下筋独立”的局部纯函数和回归测试，仍需接入整线迭代工作流。
- [ ] SMF 箍筋间距使用最终实配纵筋直径复核，而不是预设最大启用直径。
- [ ] Excel 公式继续校核面积，并增加直径、分层根数和、末排至少两根、箍筋间距和 legs 合法性。
- [ ] `FLEXURAL_DEMAND_BELOW_AS_MIN` 按已确认的 AsFlex/AsTotal 工程策略降为有意义的说明，避免形成大量无效警告。

#### P1-2 统一编号策略并保证编号稳定

- [ ] 建立唯一的编号 formatter/policy，由 Excel、DXF、JSON 和模型写回共同调用；正式编号禁止 `S` 前缀。
- [ ] 建立 `Object GUID/空间指纹 → 既有 Beam Line/Span 编号` 的持久映射。
- [ ] 新增、删除或局部修改梁时输出 added/removed/renumbered 差异，避免同楼层后续编号无提示整体漂移。
- [ ] 提供人工编号 override，并校验重复、冲突和失效映射。

#### P1-3 建立 CAD 无关中间数据与正式图纸方向契约

- [ ] 输出版本化 JSON；至少包含 GUID、旧/新 Unique Name、楼层、Beam Line/Span、路径、I→J 单位向量、文字锚点/角度、截面和源 Object 映射。
- [ ] 正式 CAD 文字方向严格等于 I→J；禁止为了可读性独立反转 180°。
- [ ] DXF 作为 JSON 的可视化派生物，并按楼层分别输出；PB 黄、SB 绿、异常独立图层。
- [ ] CAD 插件读取中间数据后，把文字置于跨中；后续再实现文字向梁边线中上方对齐及碰撞调整。

#### P1-4 输出目录、版本管理和文档收敛

- [ ] 统一 `build/output/` 与根目录 `outputs/` 的职责；生成物默认不进入 Git。
- [ ] 不保留大体积 `.inspect.ndjson` 作为交付物，仅保存精简摘要、错误清单和必要预览。
- [ ] 更新 `PROJECT_STRUCTURE.md`、README、用户指南和 API 文档；历史路线明确标记为历史，不再指导当前开发。
- [ ] 最新 P0/P1 成果完成后创建干净基线提交。

### P2 — 远期，不阻塞当前主线

- [ ] ETABS 表格 API → 项目自建 SQLite 只读快照；重点是可复现、可审计和离线处理，不承诺缩短 ETABS 分析时间。
- [ ] CAD 插件：读取中间数据、放置梁号、识别梁线图层并调整文字位置。
- [ ] ETABS 进程内插件：收敛 COM/UAC 权限和部署边界，仍保留副本、显式事务与回滚；自动编号第一次写回后，增加“使用当前选择创建 Beam Line/Span Group”的交互功能，可预览当前编号、路径顺序、内部支承和 I→J；确认后局部重排编号，显示逐 Object 旧名→新名差异，再执行第二次写回。ETABS 原生 Group 镜像成员用于图形检查，外部 GUID 数据为权威记录。
- [ ] GUI、安装包、CI/CD、脱敏集成测试模型。
- [ ] ACI 318 其他版本、GB50010、SAP2000 等扩展。

---

## 5. 不可破坏的工程约束

1. 原始 EDB 永不直接试验；所有模型修改只在带时间戳副本上执行。
2. 每次连接都核对 PID 和实际模型绝对路径；多实例时不得依赖“当前活动实例”猜测。
3. 记录原始当前单位；导出/计算固定 `N_mm_C`；结束后恢复原单位。
4. 主梁按柱/等效柱分跨，次梁按主梁分跨，次梁—次梁交点不增加跨，悬挑单独成跨。
5. ETABS Object 只有一个 Unique Name；一个 Object 对应多个 Span 时必须阻塞或安全分割，不能静默选一个编号。
6. 模型 I→J、梁表左/中/右、JSON/DXF/CAD 文字方向必须一致。
7. 正式跨号不含 `S`；内部临时标识也应逐步收敛到同一编号策略。
8. 设计结果、规范、SMF、截面或构造输入不明确时 fail-closed；不得输出看似完整但实际为空或采用默认猜测的配筋。
9. 所有 API 返回码、异常、人工 override 和旧成果失效状态必须可追溯。

---

## 6. 关键真实模型验证时间线

- **2026-07-12，`bm.EDB`**：96 根梁、32 个连续组、5,568 个设计站点，验证最小只读 Excel 链路。
- **2026-07-13～14，v6.2 真实复杂模型**：4,166 根混凝土梁；完成 15° 折线归并、跨截面 Beam Line 串接、PB/SB 角色转换、结构分跨和完整 DXF 图形复核迭代。
- **2026-07-16，v6.2 写回副本**：完成方向反向、两阶段改名、GUID/方向验证；确认跨 Span Object 必须阻塞。
- **2026-07-17，v6.4.3 验证副本**：ETABS 23 分析、混凝土设计、保存重开和 `_2` 字段验证；4,167 个梁 Object 有设计站点。
- **2026-07-17～18，配筋评审**：生成 3,074 跨三表 Excel，完成 AsFlex、face 模式、箍筋优先、纵筋分层和单根末排修正。
- **2026-07-18，v6.4.1 全链路副本**：旧算法下完成 3,070 跨写回、分析设计、DXF 和 Excel；随后确认该成果因 PB 内部梁交点误分跨而失效为最终交付。
- **2026-07-18，Object `302` 修正**：API ID 54204 只读重算为 3,010 跨、24 个阻塞 Object；完整重放尚未执行。
- **2026-07-20，v6.4.1 P0-1/P0-2 重放**：从未修改源 EDB 创建 `EMHS-GF.v6.4.1_writeback_20260720_001559.edb`，按当前算法读取 4,166 个 Object、1,202 条梁线和 3,009 跨；24 个跨 Span Object 阻塞。安全对象方向/编号写回、分析、混凝土设计、保存重读及 `_2` Excel 检查点全部返回成功；源 EDB 哈希未改变。不同版本模型的 Span 数量差异记录在本次输出目录，不以时间戳替代算法/模型版本。

API 签名、权限问题和验证细节见 [ETABS_OAPI_VALIDATION.md](docs/ETABS_OAPI_VALIDATION.md)。

---

## 7. 状态维护规则

1. 新任务先判断依赖和 P0/P1/P2，再插入本清单；不继续向已完成段落追加无限长日志。
2. 每项完成必须同时给出测试、真实模型或人工复核证据，并记录对应 run manifest。
3. 算法变化必须明确标记受影响的旧 EDB、DXF、JSON、CSV 和 Excel 是否失效。
4. 专题文档保存规则与推导；本清单只保存当前状态、验收条件和关键证据。
5. P0 全部关闭后，再把当前阶段改为“生产候选”，并开始 CAD/SQLite/插件阶段。
