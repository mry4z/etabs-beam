---
id: VAL-20260827-REBAR-SINGLE-SHEET-STATIC
type: validation
status: passed
owner: project
created: 2026-08-27
updated: 2026-08-27
related:
  - SPEC-DET-001
  - SPEC-PLUGIN-001
  - ARCH-002
  - TASK-P1-011
supersedes: []
superseded_by:
evidence:
  - "src/design/detailing.py"
  - "src/rebar_workbook.py"
  - "src/spreadsheet/render_rebar_workbook_xlsx.py"
  - "src/spreadsheet/render_secondary_beam_xlsx.py"
  - "src/spreadsheet/build_rebar_workbook.mjs"
  - "plugins/etabs/BeamGenerator.EtabsPlugin/MainForm.cs"
  - "tests/unit/test_detailing.py"
  - "tests/unit/test_rebar_workbook.py"
  - "tests/unit/test_render_rebar_workbook_xlsx.py"
  - "tests/unit/test_render_secondary_beam_xlsx.py"
---

# VAL-20260827：PB/SB 静态单页成果表与全跨底筋策略验证

## 范围与结论

本记录验证本轮离线实现：PB、SB 分别投影为只有一张 `整理梁表` 的静态 Excel；后台 JSON
继续保留 raw/review/final 对齐行和计算审计；每个梁跨的底筋由左/中/右需求包络一次选定并在
全跨一致；相邻跨底筋不再传播，上部钢筋协调规则保持不变。

本记录不包含 ETABS 连接、模型写回、模型保存、施工图工程签发或发布包验收。

## 自动化结果

| Gate | Evidence | Result |
|---|---|---|
| 全跨底筋引擎 | `tests/unit/test_detailing.py` | PB/SB 三个设计站点的底筋直径、根数、分层和挂筋内容统一；跨间底筋可独立；上部支座协调回归通过。 |
| 最终实际钢筋复核 | `src/design/detailing.py`、SMF capacity-shear test | 统一底筋在 Mpr、capacity shear、箍筋最终控制和构造检查前完成；测试覆盖 capacity-shear review 与最终实际布局。 |
| Payload 兼容与投影 | `src/rebar_workbook.py`、`tests/unit/test_rebar_workbook.py` | schema `2.1` 继续读取 `2.0`；新 payload 标记 `single-sheet-static-v1`、`FULL_SPAN_UNIFORM_PER_SPAN`、`INDEPENDENT_PER_SPAN`；旧的三点底筋结果不能只删列伪装为新成果，必须重算或作为候选留审。 |
| PB portable renderer | `src/spreadsheet/render_rebar_workbook_xlsx.py` | 12 列、实际楼层分段、自然楼层/梁线/数值跨序、A 列同楼层同梁线合并、B 列不合并；静态值、异常行保留并红色/批注。 |
| SB portable renderer | `src/spreadsheet/render_secondary_beam_xlsx.py` | 11 列、SB 过滤、全跨统一箍筋；旧 schedule-only 输入保留候选并标记需重算。 |
| Node artifact-tool path | `src/spreadsheet/build_rebar_workbook.mjs` | PB/SB 共用同一静态布局；Node 直接从 live artifact workbook 渲染 `schedule.png`，再导出带最终合并记录的 XLSX。 |
| 插件 Release 编译 | `plugins/etabs/BeamGenerator.EtabsPlugin/BeamGenerator.EtabsPlugin.csproj` | ETABS 22 `ETABSv1.dll` 与 ETABS 23 `ETABSv1.dll` 两次 x64 Release build 均为 0 warnings、0 errors。 |
| 定向测试 | `python -m pytest -q tests/unit/test_detailing.py tests/unit/test_rebar_workbook.py tests/unit/test_render_rebar_workbook_xlsx.py tests/unit/test_render_secondary_beam_xlsx.py --no-cov` | `73 passed`。 |
| 全量离线测试 | `python -m pytest -q` | `340 passed, 1 skipped`；coverage `77.06%`。 |
| Node 语法门禁 | `node --check` on the three tracked spreadsheet `.mjs` files | 通过。 |

## 历史 payload 重放

输入为只读复制的历史过程文件：
`feedback/生成编号不连续问题-20260824/4#5#_beam_output/_history/20260818T080419Z-d546cf99/intermediate/rebar_workbook_payload_20260818_160503.json`。
没有覆盖该历史文件；重算结果写入 `tmp/single_sheet_static/replay_20260827_rebar_payload.json`。

- 对齐 payload 共 968 行：PB 481、SB 487；metadata 已写入新的布局和底筋策略。
- 721 行成功结果满足 `bottom_uniform_regions_match=true`，247 行因无可直接交付结果保留候选/复核状态，
  没有发现 `false` 的静默不一致行。
- 对成功结果，`eb1/b1/eb2/bottom_uniform_text` 逐行一致；同一梁线内有 118 组出现跨间底筋差异，
  证明相邻跨不再被错误传播为同一套底筋。
- Python 与 Node 两条路径均生成完整 PB/SB 文件：PB 12 列、SB 11 列；每个文件只有 `整理梁表`，
  3 个实际楼层段；零公式、零 Excel Table、零隐藏行/列、零外部链接。异常候选保留批注和红色行。

## 视觉验收

使用 Node artifact-tool 对历史 PB/SB 样本分别渲染 `A1:L35` / `A1:K35`：标题、模型元数据、实际
楼层、分组英文表头、PB/SB 专属列、数值尺寸/肢数、多层钢筋文本和红色候选行均可见，列宽未截断。
最终 XLSX 的 A 列纵向合并记录由 openpyxl/Node 导出后复核；B 列没有数据纵向合并。多层钢筋样本和
3 个实际楼层段均在历史重放文件中保留。

## 未覆盖边界

1. 本轮没有自动连接、操作或保存 ETABS，也没有进行最新代码身份的 ETABS 22/23 实模重跑。
2. 408 个历史工程异常、锚固/搭接、节点拥挤和施工图人工复核仍是工程审查事项；红色候选不是合格成果。
3. 本记录没有创建、提交、推送或发布插件压缩包；发布包需在本轮代码和文档评审后单独按插件发布门禁构建。

## Verdict

`passed` for the offline engine/payload/static-workbook implementation and visual contract. Real-model
validation and engineering sign-off remain outside this record and therefore remain open in the Current Baseline.
