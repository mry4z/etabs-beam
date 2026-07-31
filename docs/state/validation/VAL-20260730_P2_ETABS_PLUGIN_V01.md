---
id: VAL-20260730-P2-ETABS-PLUGIN-V01
type: validation
status: partial
owner: project
created: 2026-07-30
updated: 2026-07-30
related:
  - SPEC-PLUGIN-001
  - ADR-0005
  - IP-P2-003
  - TASK-P2-PLUGIN-001
supersedes: []
superseded_by:
---

# VAL-20260730-P2-ETABS-PLUGIN-V01

## 1. Verdict

`partial`。ETABS Beam Generator v0.1.0 的外部 smoke 通过，但用户从 ETABS Tools 菜单实际运行时返回
`Cannot create plugin` / `Value cannot be null (Parameter 'type')`。该失败已精确复现：旧程序集仅导出
全局短名 `cPlugin`，ETABS 按 `<AssemblyName>.cPlugin` 查找
`BeamGenerator.EtabsPlugin.cPlugin` 时得到 `null`。

v0.1.1 已把入口移入与程序集同名的命名空间，并把 Validator 和 SmokeHost 改为按 ETABS 的完整类型名
解析和实例化。修复后的程序集合同、真实 ETABS 22 `SapModel` 窗口、选择 JSON 和完成 callback 均通过；
v0.1.0 已验证的 Python 全模型只读 runner 逻辑未改变。

本记录不升级为 `passed`，因为 v0.1.1 修复包仍待用户从 `Tools > Add/Show Plugins` 删除旧条目、完全
重启 ETABS 后重新添加确认；ETABS 23 和非空选择集也未重跑。

## 2. 代码与环境身份

| 项目 | 值 |
|---|---|
| Git HEAD | `7d3862752041fe18eb0201b21671f3575d70b52b` |
| 工作树 | `dirty: true`；保留了本任务开始前的大量 P1/P2 未提交修改 |
| tracked diff SHA-256 | `d27ef5ec5a22543e3dd65d7e5f189f1421efa566891c608ffe2630ad844d0f00` |
| tracked diff + untracked content SHA-256 | `9e91cbd52257286fe2702d330323db396bf665138313c602194031812def67f5` |
| ETABS / API | ETABS `22.7.0`；`ETABSv1.dll 2.10.0.0` |
| .NET | SDK `8.0.423`；目标 `.NETCoreApp,Version=v8.0` / `win-x64` |
| Python | `3.11.7` x64 |
| 插件 | `BeamGenerator.EtabsPlugin 0.1.1.0` |

content identity 在插件源码、构建/校验/smoke 工具和测试最终完成后生成；随后只追加本 Validation 及关联
Architecture/Task/Baseline 治理 Markdown。

## 3. Release 包与合同校验

构建命令：

```powershell
.\tools\build_etabs_plugin.ps1 `
  -EtabsInstallDirectory "E:\Program Files\Computers and Structures\ETABS 22"
```

最终修复交付：

| Artifact | 身份 |
|---|---|
| 发布目录 | `build/etabs-plugin/v0.1.1-etabs22-20260730-224522/`；6 files；64,815 bytes |
| ZIP | `build/etabs-plugin/v0.1.1-etabs22-20260730-224522.zip`；30,776 bytes；SHA-256 `c138157535eba80a0bcc978dcf7d0ea74323e1efd14e918dc46c836107aa3652` |
| DLL | `BeamGenerator.EtabsPlugin.dll`；56,320 bytes；SHA-256 `18f2b0f7c7c107b299f072165cdea84a78baa58c49e4c4817683772e2478aea9` |

`contract-validation.json` 结论：

- `verdict=passed`；
- PE machine 为 `Amd64`；
- `etabs_loader_type_name=BeamGenerator.EtabsPlugin.cPlugin`；
- 按 `<AssemblyName>.cPlugin` 得到非空公共类型且 `Activator.CreateInstance` 成功；
- 入口实现 `ETABSv1.cPluginContract`；
- `Info(ref string) -> int` 和 `Main(ref cSapModel, ref cPluginCallback) -> void` 精确匹配；
- 发布目录不包含 `ETABSv1.dll`。

### 3.1 失败复现与回归门禁

对 v0.1.0 执行 ETABS 风格反射：

| 项目 | v0.1.0 | v0.1.1 |
|---|---|---|
| 程序集名 | `BeamGenerator.EtabsPlugin` | `BeamGenerator.EtabsPlugin` |
| ETABS 查找名 | `BeamGenerator.EtabsPlugin.cPlugin` | `BeamGenerator.EtabsPlugin.cPlugin` |
| `Assembly.GetType` | `null` | `BeamGenerator.EtabsPlugin.cPlugin` |
| `Activator.CreateInstance` | `Value cannot be null. (Parameter 'type')` | 成功 |
| Validator | 拒绝 | passed |

该复现与用户截图逐字一致，根因不是模型、ETABS OAPI 返回码或 Python 环境。

## 4. 自动门禁

| Gate | Result |
|---|---|
| 插件、SmokeHost、Validator Release build | 三个项目均 `0 warnings, 0 errors` |
| ETABS 入口回归 | v0.1.0 被新 Validator 拒绝；v0.1.1 完整类型解析、实例化和合同校验 passed |
| v0.1.1 真实 ETABS 22 smoke | PID `57192`；`7,075` Frame；窗口可见；`Finish(0)` 恰好一次 |
| Python compileall | passed |
| Python tests | `212 passed, 1 skipped` |
| Python coverage | `79.54%` |
| Black | `65 files would be left unchanged` |
| Flake8 | passed，无输出 |
| 受治理文档 ID/front matter/local links | `58 files / 58 unique IDs`，passed |

## 5. 真实 ETABS 22 smoke

### 5.1 插件合同与窗口

v0.1.1 的 `tools/EtabsPluginSmokeHost` 先按
`BeamGenerator.EtabsPlugin.cPlugin` 完整类型名解析/实例化，再通过 CSI `GetObjectProcess` 附着 PID
`57192`，取得真实 `SapModel` 并调用精确 `cPlugin.Main` 合同：

- 宿主 `22.7.0`，受支持；
- 真实模型路径：
  `build/output/p1_v645_working_20260730/EMHS-GF.v6.4.5.CRS_p1_working.EDB`；
- 插件 WinForms 窗口 `Visible=true`；
- Frame 总数 `7,075`；
- 当前选择对象 `0`；
- 窗口关闭后 `cPluginCallback.Finish` 调用次数 `1`，error flag `0`。

该 smoke 证明真实 OAPI 对象、窗口生命周期和 callback 合同可运行，但它由外部测试宿主调用，不等同于
ETABS `Tools` 菜单自己的程序集发现/加载证据。

### 5.2 选择 JSON

`plugin-selection-v011-smoke.json`：

- schema `etabs-beam-generator/plugin-selection@1`；
- `complete=true`；
- PID `57192`；
- ETABS/API `22.7.0 / 2.10.0.0`；
- Frame `7,075`，选择对象 `0`；
- SHA-256 `21f25192406b63bdba49df42b17c277540734935f1274cda5c504f093982acf7`。

非空选择集的逐 Frame GUID/Label/Story/Section/方向读取留待菜单实测或后续集成样本。

### 5.3 插件内 Python runner

v0.1.0 的插件窗口和 callback 保持活动时，由相同 UI 逻辑启动：

```text
python -m src.cli --pid 38200 --expected-model <EDB> \
  --config <ETABS22_CONFIG> --read-only --selection all --output-dir <NEW_DIR>
```

run `20260730T131855Z-53226a75`：

| 项目 | Result |
|---|---|
| manifest status / validity | `SUCCESS / CURRENT` |
| 梁 / Beam Line / Span | `4,094 / 1,215 / 3,018` |
| PB / SB Span | `1,574 / 1,444` |
| blocking topology diagnostic | `0` |
| 只读门禁 | `read_only=true`, `writeback=false` |
| EDB SHA-256（preflight / manifest / run 后复核） | `bf2683e0aa2447463459c06a5abd28038ead2f8c09085b2e5822f32b7dfaeb51` |
| manifest SHA-256 | `0abf20f11db9df584466688978b7bd4d6e5dfd119da4f9c42a3ac8510ed2e7e9` |
| 输出目录 | 10 files；1,803,841 bytes；SHA-256 `6324930c830f0e09d7f97d6923d0a7e721e0d47d65c5e2ed2a0c7249f78386ce` |

第一次 smoke preflight 使用外部测试宿主自身 PID，Python 正确以 `ETABS PID unavailable` fail-closed，
生成 `FAILED/INVALID` manifest，未继续 OAPI 操作。随后 smoke host 使用只在非 ETABS 测试进程生效的显式
PID 注入重跑成功；真实进程内插件始终使用 `Environment.ProcessId`。v0.1.1 只改入口完整类型名和对应
验证门禁，没有改动 `MainForm`、`PythonWorkflowRunner` 或 Python CLI；本次修复 smoke 未重复该长链 run。

## 6. 未完成 Gate

1. 完全退出加载过 v0.1.0 的 ETABS；重启 ETABS 22 后，从 `Tools > Add/Show Plugins` 删除旧条目、浏览
   v0.1.1 最终 DLL、确认 Status，再从 Tools 菜单打开并截取/记录加载证据。
2. 选择至少一个 Frame，复核导出中的 GUID、Label、Story、Section 和设计方向。
3. 使用 ETABS 23 匹配 API 路径重新构建，并重复合同、菜单加载、选择集和 Python runner。
4. 正式安装器、签名、升级/回退矩阵、稳定编号/override UI 和 CAD 插件不在本 v0.1 证据范围。
