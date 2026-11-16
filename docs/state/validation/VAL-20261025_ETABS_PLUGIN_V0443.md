---
id: VAL-20261025-ETABS-PLUGIN-V0443
type: validation
status: partial
owner: project
created: 2026-10-25
updated: 2026-10-25
related:
  - GUIDE-PLUGIN-001
  - SPEC-PLUGIN-001
---

# VAL-20261025 · ETABS 插件 v0.4.4.3

## 1. Verdict

`partial`。v0.4.4.3 完整发布包已构建；契约验证、frozen runtime 自检、包外帮助检查和 R4/legacy 策略运行烟测均通过。R4 策略回放结论按其覆盖范围记录，不能解释为全面规范符合或整体钢筋节省。ETABS 菜单加载及模型副本成果验收仍待用户完成；本次没有连接、分析、设计、保存、写回或关闭任何 ETABS 实际模型。

## 2. 插件策略选择

界面默认选择“新配筋规则（图纸风格）”，稳定 ID 为 `drawing_20260924_v1`；用户也可选“旧配筋规则”，ID 为 `legacy`。选择保存在用户设置中，缺失或非法值回退新规则。普通生成显式传 `--detailing-policy`，使所选策略覆盖 config 中的策略值；Tower 检查不带该参数。

新规则使用 revision `drawing_20261025_uniform_hoop_no_4_3_v1`。它保留冻结的 412 规则和兼容 396 规则。中间顶筋需求范围按母跨 `3L/8..5L/8` 协调并保留重复原始站位；纵筋最小净距不少于 `max(25 mm, db)`；只删除扭转 `ph/8` 上限。箍筋间距目录为 `100/110/120/130/150/160/180/200 mm`，实体 `4/3` 纵筋排布被禁，合法几何适用时选择实体 `5/2`，且不得只改标注。每跨统一箍筋直径、区域间距可变；排序依次为非箍筋风格、等效钢筋量、箍筋风格。PB 类型保持原值；新策略下普通 SB 使用确认的 `Non Sway`，保留原始 frame type 来源。

## 3. R4 回放证据与限制

完整 R4 回放覆盖 1,307 条线，每个场景 3,118 行。C 引擎 OK 2,888 行，230 行非 OK；C OK 行中实体 `4/3` 为 0、混合箍筋直径为 0。可共同计算诊断的样本为 2,870 行，C/B 为 `−0.363%`、C/A 为 `+0.625%`。严格 production-common gate 为 0。

230 行非 OK 和严格 gate 0 必须继续保留为非 OK/blocked 证据，不把它们折算成通过。结果只描述上述回放样本，不代表全面规范符合，也不支持整体钢筋节省结论。

## 4. 源码检查

| Gate | Result |
|---|---|
| `dotnet run --project tests/plugin/PluginOfflineTests.csproj` | 通过；普通请求传递策略 ID，Tower 请求不传策略参数；设置默认、缺失、非法回退及双策略持久化均有断言；ETABS API 未连接 |
| `dotnet build plugins/etabs/BeamGenerator.EtabsPlugin/BeamGenerator.EtabsPlugin.csproj -p:EtabsApiPath="E:\Program Files\Computers and Structures\ETABS 22\ETABSv1.dll"` | 成功；0 warning、0 error；ETABS 22 API DLL 存在 |

## 5. 发布产物与验证

| 项目 | 值 |
|---|---|
| 插件版本（Version/FileVersion/AssemblyVersion） | `0.4.4.3` |
| ETABS 目标 / API | ETABS 22 `22.7.0.4095` / `2.10.0.0` |
| 发布目录 | `build/etabs-plugin/v0.4.4.3-v2-etabs22-drawing-style-20261025-r1/` |
| 发布目录文件数 / 总大小 | `902` / `102,925,428` bytes |
| ZIP | `build/etabs-plugin/v0.4.4.3-v2-etabs22-drawing-style-20261025-r1.zip` |
| ZIP 大小 | `42,338,506` bytes |
| 插件 DLL | `BeamGenerator.EtabsPlugin.dll`，`95,744` bytes |

该目录和 ZIP 均为新建，未覆盖旧包。`package-manifest.json` 记录 dirty worktree 状态及逐文件哈希；发布目录中 `ETABSv1.dll` 为 0 个。

| Gate | Result |
|---|---|
| `tools/build_etabs_plugin.ps1` | 成功；构建时源码一致性与依赖门禁通过 |
| `contract-validation.json` | `verdict=passed`，plugin version `0.4.4.3`，`.NETCoreApp,Version=v8.0`，`Amd64`，ETABS contract 入口正确，未复制 `ETABSv1.dll` |
| 内置 frozen runtime self-test | `status=OK`，64-bit，Python `3.11.7`，ETABS API 类型加载成功；R4 revision `drawing_20261025_uniform_hoop_no_4_3_v1`，eligible rules `396` |
| 外部 cwd `--help` | 在 `F:\ZCI8\AppData\Local\Temp` 执行成功；同时列出 `legacy` 和 `drawing_20260924_v1` |
| 外部 cwd drawing-policy self-test | `status=OK`；R4 resource 从发布包 `_internal/src/design/policies/drawing_20260924_v1.json` 加载，revision 和 396 eligible rules 均符合预期 |
| 外部 cwd legacy self-test | `status=OK`，`detailing_policy_id=legacy` |

源码编译和发布自检未连接或写回 ETABS 模型。ETABS 22/23 菜单加载及模型副本成果验收仍由用户完成；离线检查不替代实模验收。
