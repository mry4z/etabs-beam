---
id: VAL-20260731-P2-ETABS-PLUGIN-V02
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
  - GUIDE-PLUGIN-001
supersedes: []
superseded_by:
---

# VAL-20260731-P2-ETABS-PLUGIN-V02

## 1. Verdict

`partial`。v0.2.0 正式发布封装构建全部通过：插件 DLL、便携 workflow 运行时（`BeamGenerator.Workflow.exe`
+ `_internal/` 配套 DLL + `config.yaml`）、合同校验、package manifest 和 ZIP 均生成并验证。v0.1.1
正式包缺少 `workflow/` 便携运行时，而 v0.2.0 包同时包含插件 DLL 与便携计算引擎。

仍未确认真实 ETABS 22 `Tools` 菜单加载（需用户操作）、ETABS 23 和非空选择集，因此 verdict 不升级为
`passed`。本记录只证明发布封装与离线构建/合同/自检证据，不声称已通过菜单加载验收。

## 2. 代码与环境身份

| 项目 | 值 |
|---|---|
| Git HEAD | `7d3862752041fe18eb0201b21671f3575d70b52b` |
| 工作树 | `dirty: true`；保留了本任务开始前的大量 P1/P2 未提交修改 |
| tracked diff SHA-256 | `78188f69ca3f250f87dba999353b20cc747948a5502bde940ecf3ba3e7b8169c` |
| tracked diff + untracked content SHA-256 | `7e5351801a7e775cf93151138f63446a891c773dafd1781dcf27a63ec8428716` |
| ETABS / API | ETABS `22.7.0.4095`；`ETABSv1.dll 2.10.0.0` |
| .NET SDK | `8.0.423`；目标 `.NETCoreApp,Version=v8.0` / `win-x64` |
| PowerShell | `pwsh 7.6.4`（构建必须；见 §4 环境问题） |
| Python / PyInstaller | `3.11.7` x64 / `6.12.0`（`build/portable-build-env` 隔离 venv） |
| 插件 | `BeamGenerator.EtabsPlugin 0.2.0.0` |

content identity 在本次构建、脚本修复与治理 Markdown 写入前生成；随后只追加本 Validation 及关联
Guide/Task/Baseline 治理文档。

## 3. 发布封装产物

构建命令（PowerShell 7）：

```powershell
.\tools\build_etabs_plugin.ps1 `
  -EtabsInstallDirectory "E:\Program Files\Computers and Structures\ETABS 22" `
  -PythonExecutable ".\build\portable-build-env\Scripts\python.exe"
```

| Artifact | 身份 |
|---|---|
| 发布目录 | `build/etabs-plugin/v0.2.0-etabs22-20260731-223330/`；912 files |
| ZIP | `v0.2.0-etabs22-20260731-223330.zip`；75,833,420 bytes；SHA-256 `7b00628d919175ab2dbc76a71d1365fff542f2fedde3c38c37fbec566fd743c9` |
| 插件 DLL | `BeamGenerator.EtabsPlugin.dll`；58,368 bytes；SHA-256 `6e81bcace399c68648be44133433908e27a7cdc0d69b0c0955f5f57aec764a8c` |
| workflow EXE | `BeamGenerator.Workflow.exe`；11,468,232 bytes；SHA-256 `a0f4fbf5bf25fc7694df03b7696f7599c8a0a3811f13e53986e6e1d01a1136a2` |
| package-manifest.json | 199,786 bytes；SHA-256 `7a705ce52a7e8d33318b177d4cfdf4c0e69e94ffba4d134062b636c11304dfbf` |
| contract-validation.json | 642 bytes；SHA-256 `6f136cd7a6c3fa25da2cc0a6b89547ab610e94c7ab8ef063247d5930cc36197c` |

### 3.1 package manifest 关键字段

- `schema=etabs-beam-generator/plugin-package@1`；`plugin_version=0.2.0`；`target=net8.0-windows/x64`
- `portable_workflow=workflow/BeamGenerator.Workflow/BeamGenerator.Workflow.exe`
- `local_python_required=false`；`repository_source_required=false`；`etabsv1_in_package=false`
- `etabs_host_version=22.7.0.4095`；`etabs_api_file_version=2.10.0.0`
- `contract_validation=passed`；artifacts 共 912 条（153 个 DLL）

### 3.2 合同校验结论

`contract-validation.json`：`verdict=passed`；PE machine `Amd64`；
`etabs_loader_type_name=BeamGenerator.EtabsPlugin.cPlugin`；`contract=ETABSv1.cPluginContract`；
`copied_etabsv1=false`。入口完整类型名解析/实例化与 `Info`/`Main` 签名由 Validator 确认。

### 3.3 workflow 便携运行时自检

`workflow/BeamGenerator.Workflow/runtime-self-test.json`：`status=OK`；`frozen=true`；`architecture=64bit`；
Python `3.11.7`；pandas `2.2.3`；openpyxl `3.1.5`；pyyaml `6.0.2`；能装载构建时指定的 ETABS 22
`ETABSv1.dll`（2.10.0.0）。该自检在构建脚本内自动执行，失败即中断构建。

## 4. 构建与开发环境问题（本次实测）

本节记录 v0.2.0 封装构建实际遇到并解决的环境配置问题，供可复现构建参考。这些问题的离线结论由本次
构建证据证明；操作指引以 [ETABS 插件指南](../../guides/ETABS_PLUGIN.md) 为准。

### 4.1 Windows PowerShell 5.1 缺失 .NET Core API

- **现象**：构建在最后 manifest 生成阶段失败。第一次 `[System.IO.Path]::GetRelativePath` 无法识别
  （.NET Core API，Windows PowerShell 5.1 无该方法）；改为兼容字符串写法后，第二次 `Get-FileHash`
  无法识别（本机精简版 PS 5.1.19041 缺失该 cmdlet）。
- **影响**：插件 DLL、便携运行器和合同校验在两次失败前均已成功，但缺 manifest/ZIP 使包不完整，可能
  被误认为"只差一步"或直接交付不完整包。
- **处置**：
  1. manifest 相对路径改为纯字符串 `Substring` 兼容写法；
  2. `tools/build_etabs_plugin.ps1` 增加 PowerShell Core guard：非 Core 版本自动定位 `pwsh` 并重启到
     pwsh 执行，避免再次踩坑；
  3. 最终用 `pwsh 7.6.4` 完整构建成功。

### 4.2 Anaconda base Python 与 PyInstaller 的 pathlib 冲突

- **现象**：用 base anaconda 的 `python -m PyInstaller` 报
  `The 'pathlib' package is an obsolete backport ... incompatible with PyInstaller`
  （`D:\ProgramData\anaconda3\Lib\site-packages`）。
- **影响**：便携 workflow 运行器无法直接用 base Python 打包。
- **处置**：必须先用 `tools/prepare_portable_build_env.ps1` 创建隔离 venv
  （`build/portable-build-env`），再以其 `Scripts\python.exe` 作为 `-PythonExecutable` 运行构建；
  base anaconda 的 Python 不用于 PyInstaller。

### 4.3 封装完整性核对

v0.1.1 正式包（`v0.1.1-etabs22-20260730-224522`）只含 6 个文件，缺 `workflow/` 便携运行时；v0.2.0
包同时包含插件 DLL 与 `workflow/BeamGenerator.Workflow/`（EXE + `_internal/` 配套 DLL + `config.yaml`）。
向 ETABS 加载时只能加载插件 DLL，便携运行器由插件在后台调用；两者必须作为同一发布目录整体部署。

## 5. 未完成 Gate

1. 完全退出加载过 v0.1.x 的 ETABS；重启 ETABS 22 后从 `Tools > Add/Show Plugins` 删除旧条目、浏览
   v0.2.0 完整解压目录并添加、确认 Status，再从 Tools 菜单打开并截取/记录加载证据。
2. 选择至少一个 Frame，复核导出中的 GUID、Label、Story、Section 和设计方向。
3. 使用 ETABS 23 匹配 API 路径重新构建，并重复合同、菜单加载、选择集和 Python runner 验证。
4. 正式安装器、签名、升级/回退矩阵、稳定编号/override UI 和 CAD 插件不在本 v0.2 证据范围。

## 6. 与 V01 的关系

[VAL-20260730-P2-ETABS-PLUGIN-V01](VAL-20260730_P2_ETABS_PLUGIN_V01.md) 记录了 v0.1.0 入口缺陷与
v0.1.1 修复；本记录覆盖 v0.2.0 正式发布封装。两者都是 `partial`，直到 ETABS 菜单加载证据补齐。
