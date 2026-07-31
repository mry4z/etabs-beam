---
id: IP-P2-003
type: implementation-plan
status: in_progress
owner: project
created: 2026-07-30
updated: 2026-07-31
related:
  - SPEC-PLUGIN-001
  - ADR-0005
  - TASK-P2-PLUGIN-001
supersedes: []
superseded_by:
---

# IP-P2-003：ETABS 交互插件 v0.1

## 1. Outcome

交付一个可由 ETABS 22/23 x64 添加的 .NET 8 插件初版，具备宿主/模型摘要、当前 Frame 选择集、JSON
导出和现有 Python 只读工作流启动入口，并提供可复现构建与真实宿主加载证据。

## 2. 范围

- `plugins/etabs/BeamGenerator.EtabsPlugin/`：插件合同、OAPI 只读适配、WinForms UI、设置和 Python runner。
- `tools/EtabsPluginValidator/`：不依赖 ETABS 进程的程序集合同校验。
- `tools/build_etabs_plugin.ps1`：显式 ETABS 安装路径的 Release x64 发布。
- Spec、ADR、Guide、Validation、Architecture、Task 和 Baseline 的对应治理更新。

不修改现有 Python 拓扑、设计、配筋或写回算法。

## 3. 实施步骤

1. 以本机官方 `CSI API ETABS v1.chm` 和 `ETABSv1.dll` 反射结果冻结插件合同。
2. 实现 ETABS 可按 `<AssemblyName>.cPlugin` 解析的入口、一次性 callback session 和只读
   `EtabsModelReader`。
3. 实现模型摘要、选择集表格、JSON 导出及只读 Python 工作流面板。
4. 实现构建脚本和独立合同校验器。
5. 运行 Release 构建、合同校验和 Python 既有门禁。
6. 在当前 ETABS 22 宿主中添加 DLL，验证窗口、刷新、导出和关闭；按可行性验证 Python 启动。
7. 生成 Validation Record，再更新 Current Baseline。

## 4. 安全与回退

- 全部插件内 OAPI 操作为读取；非零返回码抛出带调用上下文的异常。
- 发布目录不得包含 `ETABSv1.dll`。
- Python runner 只生成 `--read-only` 参数；源码不包含写回开关。
- 卸载通过 ETABS `Tools > Add/Show Plugins` 删除菜单项；删除插件发布目录即可回退。
- 任一真实宿主检查失败时保留构建产物，但 Validation verdict 为 `partial` 或 `failed`，不得宣称已加载验证。

## 5. 完成定义

- `SPEC-PLUGIN-001` 的离线构建、合同和 ETABS 22 加载 Gate 有可追溯证据。
- 插件功能保持只读，现有 Python 单元测试没有回归。
- Guide 包含安装、运行、卸载、故障排查和已知限制。
- 本 Plan 转为 `verified`；正式 `TASK-P2-PLUGIN-001` 仍按其 CAD/ETABS 双插件完整门禁管理。

## 6. 当前验证状态

2026-07-30 已完成 v0.1.x 的 Release 包、真实 ETABS 22 `SapModel` 窗口/JSON/callback smoke 和 Python
全模型只读 run，并修复 v0.1.0 入口类型空值（`Value cannot be null (Parameter 'type')`），精确结论见
[VAL-20260730-P2-ETABS-PLUGIN-V01](../../../state/validation/VAL-20260730_P2_ETABS_PLUGIN_V01.md)。

2026-07-31 完成 v0.2.0 正式发布封装：插件 DLL、便携 workflow 运行时（EXE + 153 配套 DLL + config.yaml）、
合同校验（`passed`）、package manifest（912 artifacts）和 ZIP（75.8 MB）全部生成并验证；构建脚本
修复了 Windows PowerShell 5.1 缺失 .NET Core API 的问题并自动重启到 pwsh，且 PyInstaller 必须使用
隔离 venv。精确证据见
[VAL-20260731-P2-ETABS-PLUGIN-V02](../../../state/validation/VAL-20260731_P2_ETABS_PLUGIN_V02.md)。

v0.2.0 仍待用户从 ETABS 22 `Tools > Add/Show Plugins` 删除旧条目、完全重启后加载确认；ETABS 23 也未
重跑。因此 Plan 保持 `in_progress`。
