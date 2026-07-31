---
id: VAL-20260731-P2-ETABS-PLUGIN-V03
type: validation
status: partial
owner: project
created: 2026-07-31
updated: 2026-07-31
related:
  - SPEC-PLUGIN-001
  - ADR-0005
  - IP-P2-003
  - TASK-P2-PLUGIN-001
  - VAL-20260730-P2-ETABS-PLUGIN-V01
  - VAL-20260731-P2-ETABS-PLUGIN-V02
  - GUIDE-PLUGIN-001
supersedes: []
superseded_by:
---

# VAL-20260731-P2-ETABS-PLUGIN-V03

## 1. Verdict

`partial`。v0.3.0 交付两项新能力并通过离线/便携验证：ETABS API DLL 可定位（插件 UI 可覆盖 + CLI
自动搜索），以及便携工作流在无 node 的目标机器上按交付形态生成带公式的三表配筋 xlsx 与 SB 梁表。
仍未确认真实 ETABS 22/23 `Tools` 菜单加载（需用户操作）和非空选择集，因此 verdict 不升级为 `passed`。

## 2. 代码与环境身份

| 项目 | 值 |
|---|---|
| Git HEAD | `503195bcc272349b88022baa984e075d4b416f79` |
| 工作树 | `dirty: true`；本次改动包含新的便携渲染器、DLL 定位模块、插件 UI 与测试 |
| tracked diff SHA-256 | `bb0f10c140e7a075159cf4956a1eb19142e0d865f78362a60d01d46e9bb18636` |
| tracked diff + untracked content SHA-256 | `477bb677d1a703d3e657b6870b73f1716761db0349d4954b8e0e44b94559158f` |
| ETABS / API | ETABS `22.7.0`（构建目标）；自动搜索本机另发现 ETABS `23.x`（`ETABSv1.dll 2.16.0.0`） |
| .NET SDK | `8.0.423`；目标 `.NETCoreApp,Version=v8.0` / `win-x64` |
| Python / PyInstaller | `3.11.7` x64 / `6.12.0`（`build/portable-build-env` venv） |
| 插件 | `BeamGenerator.EtabsPlugin 0.3.0.0` |

content identity 在 v0.3.0 代码、测试与重打包完成后、本 Validation 及关联治理 Markdown 写入前生成。

## 3. 新增能力

### 3.1 ETABS API DLL 定位（问题 1）

- 插件 `MainForm`「运行只读工作流」页新增 **ETABS API DLL** 行：默认自动填入当前宿主程序集路径
  （`typeof(cSapModel).Assembly.Location`），可点击“浏览…”选择其它机器的 `ETABSv1.dll`；选择持久化到
  `PluginSettings.EtabsApiPath`（`plugin-settings.json`）。运行工作流时以用户指定路径优先于宿主路径。
- 便携 CLI 新增 `src/etabs_locate.py`：`search_etabs_dll_path()` 枚举各盘符
  `Program Files / Program Files (x86)/Computers and Structures/ETABS 23|22/ETABSv1.dll`（固定 2 级目录，
  无递归，优先 23）。`src/cli.py::_ensure_etabs_dll` 优先级：
  `--etabs-api` 参数 > `ETABS_API_PATH` 环境变量 > `config.yaml` 有效路径 > 自动搜索 > `FileNotFoundError`。
- `--runtime-self-test` 不再强制要求 `--etabs-api`（缺失时自动搜索）。

### 3.2 便携带公式配筋 xlsx（问题 2）

- 新增 `src/spreadsheet/render_rebar_workbook_xlsx.py`（三表“原始分跨数据 / 配筋调整复核 / 整理梁表”，
  openpyxl）与 `src/spreadsheet/render_secondary_beam_xlsx.py`（SB 梁表 + 复核异常），移植原
  `build_rebar_workbook.mjs` / `build_secondary_beam_workbook.mjs` 的列结构、公式（面积解析、箍筋 At/s、
  OK/NG/ENGINE 状态、跨表引用、QC）、表格、条件格式、冻结窗格与列宽。
- `src/rebar_workbook.py::run_workbook_builder` / `run_secondary_workbook_builder` 新增 `builder` 参数：
  默认 `"python"`（openpyxl，无需 node，跳过 PNG 预览）；`"node"` 保留为开发机对照基线
  （`_run_artifact_builder` / `_run_rebar_preview_builder` 未删改）。
- `src/cli.py` 新增 `--workbook-builder {python,node}`（默认 `python`）；`--export-rebar-workbook` 分支与
  `src/snapshot/workflow.py` 均改用 `builder=getattr(args, "workbook_builder", "python")`。
- payload schema（2.0）不变；便携端不生成 PNG 预览（QA 用，非交付）。

## 4. 发布封装产物（v0.3.0）

构建命令与 v0.2.0 相同（pwsh + `build/portable-build-env`）。

| Artifact | 身份 |
|---|---|
| 发布目录 | `build/etabs-plugin/v0.3.0-etabs22-20260731-234613/`；912 files |
| ZIP | `v0.3.0-etabs22-20260731-234613.zip`；75,949,999 bytes；SHA-256 `e210b6543c68d5042ec061b998ff51dcb36d96118d4e2552f34a8298b1dd6a3a` |
| 插件 DLL | `BeamGenerator.EtabsPlugin.dll`；59,904 bytes；SHA-256 `cb4f27ad888662957049cd557a0a274d5435b8ef3a9af0043156bc815b8320ad`；版本 `0.3.0.0` |
| workflow EXE | `BeamGenerator.Workflow.exe`；11,496,533 bytes；SHA-256 `d69bf3ef9ea4019dc06e91ef637ca2fe062bbf92e4841d8372774d25974fe0f6` |
| package-manifest.json | 199,787 bytes；SHA-256 `7e7a352b034824b90307841df99b950a5e2ee1b288fa44a554ca8c101679742d`；`plugin_version=0.3.0`、`contract_validation=passed`、`local_python_required=false`、`etabsv1_in_package=false` |
| contract-validation.json | 642 bytes；SHA-256 `1de06f61d462534af5c72d895b0c72f56f6977fc6b6213cddd36db3de5176d32`；`verdict=passed` |

## 5. 自动门禁与便携验证

| Gate | Result |
|---|---|
| Python tests | `239 passed`（新增 27 项：渲染器 18、SB 8、etabs_locate 3、cli 4）；coverage `80.52%` |
| 插件 Release build | `0 warnings, 0 errors` |
| 便携 `--runtime-self-test`（无 `--etabs-api`） | `status=OK`；自动搜索定位到本机
  `E:\Program Files\Computers and Structures\ETABS 23\ETABSv1.dll`（2.16.0.0） |
| 便携 snapshot rebar 导出（无 node） | `--snapshot-input etabs22_v645_domain_v5.sqlite --export-rebar-workbook`，
  run `20260731T155128Z-277376f1`；`spans=3018`、`blocking_topology_diagnostics=0`；生成
  `beam_rebar_review_*.xlsx`（7.5 MB，3023 行三表）与 `secondary_beam_schedule_*.xlsx` |
| 便携 xlsx 公式扫描 | 两个文件 `_scan_formula_errors` 均 `NONE`；抽查末行面积/状态/跨表/QC 公式正确 |

## 6. 未完成 Gate

1. 完全退出加载过 v0.1.x/v0.2.x 的 ETABS；重启后从 `Tools > Add/Show Plugins` 删除旧条目、加载 v0.3.0
   完整解压目录、确认 Status 与 Tools 菜单，并验证新「ETABS API DLL」行与「导出配筋工作簿」复选框。
2. 选择至少一个 Frame，复核导出中的 GUID、Label、Story、Section 和设计方向。
3. 使用 ETABS 23 匹配 API 路径重新构建，并重复合同、菜单加载、选择集和 Python runner 验证。
4. 正式安装器、签名、升级/回退矩阵、稳定编号/override UI 和 CAD 插件不在本 v0.3 证据范围。

## 7. 与 V01/V02 的关系

[V01](VAL-20260730_P2_ETABS_PLUGIN_V01.md) 记录 v0.1.0 入口缺陷与 v0.1.1 修复；
[V02](VAL-20260731_P2_ETABS_PLUGIN_V02.md) 记录 v0.2.0 正式封装构建与环境问题；
本记录覆盖 v0.3.0 的 DLL 定位与便携配筋工作簿能力。三者均为 `partial`，直到 ETABS 菜单加载证据补齐。
