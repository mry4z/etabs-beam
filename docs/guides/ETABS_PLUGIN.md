---
id: GUIDE-PLUGIN-001
type: guide
status: current
owner: project
created: 2026-07-30
updated: 2026-07-30
related:
  - SPEC-PLUGIN-001
  - ADR-0005
  - IP-P2-003
  - REF-OAPI-001
supersedes: []
superseded_by:
---

# ETABS Beam Generator 插件 v0.2.0

本指南适用于 x64 `.NET 8` 便携插件。交互界面全部位于 ETABS 插件 DLL 内；发布包同时携带一个无窗口
后台计算引擎，已经封装 Python 3.11、项目算法和运行依赖。目标机器只需要 ETABS 22/23，不需要安装
Python、克隆仓库或配置 `src/cli.py`。当前插件没有写回、保存、解锁、分析、设计或修改选择集的功能。

## 1. 构建

要求：

- Windows x64；
- .NET 8 SDK；
- ETABS 22 或 23 x64；
- 对应安装目录中的 `ETABSv1.dll`。
- 仅构建机需要 Python 3.11；目标用户机器不需要 Python。
- PowerShell 7 (`pwsh`)。构建脚本依赖 PowerShell Core API；若在 Windows PowerShell 5.1 下运行，脚本会
  自动检测并重启到 `pwsh`。

首次构建先创建隔离的封装环境（base anaconda 的 `python -m PyInstaller` 会因过时 `pathlib` 后向兼容包
报错，因此 PyInstaller 必须运行在下列 venv 中）：

```powershell
.\tools\prepare_portable_build_env.ps1 `
  -BasePython "D:\ProgramData\anaconda3\python.exe"
```

在仓库根目录运行：

```powershell
.\tools\build_etabs_plugin.ps1 `
  -EtabsInstallDirectory "E:\Program Files\Computers and Structures\ETABS 22" `
  -PythonExecutable ".\build\portable-build-env\Scripts\python.exe"
```

### 1.1 构建环境与已知问题

以下问题在 v0.2.0 正式封装时实测并已解决，结论证据见
[VAL-20260731-P2-ETABS-PLUGIN-V02](../state/validation/VAL-20260731_P2_ETABS_PLUGIN_V02.md)：

| 现象 | 原因 | 处置 |
|---|---|---|
| 构建在最后 manifest 阶段失败：`Get-FileHash` / `GetRelativePath` 无法识别 | Windows PowerShell 5.1 缺少这些 .NET Core / 精简版 cmdlet | 脚本已改兼容写法并自动重启到 `pwsh`；构建请使用 PowerShell 7。 |
| `python -m PyInstaller` 报 `The 'pathlib' package is an obsolete backport ... incompatible with PyInstaller` | base anaconda 的 `pathlib` 后向兼容包与 PyInstaller 冲突 | 必须用 `build/portable-build-env` venv 的 `Scripts\python.exe` 作为 `-PythonExecutable`。 |
| 包看起来"只有 EXE 和 config、没有 DLL" | 只解压了便携运行器目录，或使用了缺少 `workflow/` 的 v0.1.x 包 | v0.2.0 完整包同时含插件 DLL 与 `workflow/`；整个目录整体解压、整体部署，不能只复制单个文件。 |

脚本在 `build/etabs-plugin/` 下创建一个不覆盖既有目录的版本化包和 ZIP，并自动验证：

- ETABS 加载器要求的公共入口类型 `BeamGenerator.EtabsPlugin.cPlugin`
  （即 `<AssemblyName>.cPlugin`）可被解析和实例化；
- `ETABSv1.cPluginContract`、`Info` 和 `Main` 的精确签名；
- `.NET 8` 目标；
- 发布目录没有携带 `ETABSv1.dll`；
- `BeamGenerator.Workflow.exe` 是 64 位 frozen runtime；
- 便携运行器可以导入 pandas、openpyxl、PyYAML、pythonnet，并装载构建时指定的 ETABS API；
- package manifest 明确记录 `local_python_required=false` 和 `repository_source_required=false`。

不能把任意版本的 `ETABSv1.dll` 复制进插件目录。ETABS 会提供其安装目录中已经加载的 API 程序集。

## 2. 在 ETABS 中添加

如果已经添加过 v0.1.x，先在 `Tools > Add/Show Plugins` 删除旧条目并完全退出所有加载过旧 DLL 的
ETABS 进程。把 v0.2.0 ZIP **完整解压到新目录**后再重新启动 ETABS。不能只复制 DLL；`workflow/`
目录是发布包的一部分。

1. 打开目标 ETABS 22/23 模型。
2. 选择 `Tools > Add/Show Plugins`。
3. 浏览并选择 **v0.2.0 完整发布目录**中的 `BeamGenerator.EtabsPlugin.dll`。
4. 点击 `Add`；确认 Status 表明插件已加载。
5. 从 `Tools` 菜单点击新增的插件项。

首次添加时，ETABS 会调用插件 `Info` 显示用途和版本。插件窗口关闭时会调用
`cPluginCallback.Finish`，把控制权交还 ETABS。

## 3. 模型与选择集

“模型与选择集”页显示：

- ETABS 宿主/API 版本和 PID；
- 当前模型路径、显示单位及锁定状态；
- Frame 总数、选择对象数和选择 Frame 数；
- 当前选中 Frame 的名称、GUID、Label、Story、Section、设计方向和读取诊断。

插件只读取当前选择，不会替用户改变选择。点击“导出选择清单 JSON”可生成
`etabs-beam-generator/plugin-selection@1` 文档。任何字段读取失败都会保留为诊断，`complete` 为 `false`。

## 4. 启动便携只读工作流

“运行只读工作流”页要求显式确认：

- “内置计算引擎”为“已就绪”；
- 随包提供或用户覆盖的 `config.yaml`；
- 输出根目录；
- `selected` 或 `all`；
- 是否附加 `--export-excel`。

ETABS 仍只加载插件 DLL。`BeamGenerator.Workflow.exe` 不提供第二套 UI，也不需要用户手动运行或注册；
插件使用 `CreateNoWindow=true` 在后台调用它。该进程隔离避免 Python/.NET 依赖进入 ETABS 宿主进程。

插件只会生成以下类别的后台命令：

```text
BeamGenerator.Workflow.exe --pid <ETABS_PID> --expected-model <EDB> \
  --etabs-api <HOST_ETABSV1_DLL> --config <CONFIG> --read-only \
  --selection <all|selected> --output-dir <NEW_DIR>
```

当前源码不生成 `--writeback`、`--run-analysis`、`--run-design` 或其他 mutation 参数。输出目录按运行时间
新建，不覆盖既有 run。运行日志和退出码显示在窗口下方。

设置保存在：

```text
%LOCALAPPDATA%\ETABS Beam Generator\plugin-settings.json
```

该文件只保存配置、输出目录和 UI 选项，不保存内置计算引擎路径、模型对象或未版本化的领域数据。计算
引擎始终相对于当前插件 DLL 自动发现，因此整个发布目录移动到另一台机器或另一路径后不会沿用旧路径。

## 5. 卸载与回退

1. 关闭插件窗口，确认没有便携工作流仍在运行。
2. 在 ETABS `Tools > Add/Show Plugins` 中删除对应插件项。
3. 确认 Tools 菜单不再出现该项。
4. 删除发布目录或保留 ZIP 作为回退包。
5. 如需清除个人设置，删除上述 `%LOCALAPPDATA%` 设置文件。

该 .NET 插件不执行 COM 注册，因此不需要 `regasm` 或管理员权限。

## 6. 故障排查

| 现象 | 检查 |
|---|---|
| Add 后状态失败 | 确认使用 x64 ETABS 22/23、.NET 8 插件、包内没有 `ETABSv1.dll`，并从完整发布目录加载。 |
| `Cannot create plugin` / `Value cannot be null (Parameter 'type')` | 这是 v0.1.0 的入口命名缺陷；删除旧插件、完全重启 ETABS，并加载 v0.2.0。验证 `contract-validation.json` 中 `etabs_loader_type_name` 为 `BeamGenerator.EtabsPlugin.cPlugin`。 |
| 提示宿主版本不支持 | 当前正式版本矩阵只接受 22/23；不要绕过版本门禁。 |
| 当前模型路径为空 | 先把模型保存为 durable `.EDB`；插件不会代替用户保存。 |
| `selected` 被拒绝 | 先在 ETABS 选择至少一个 Frame Object，再点击刷新。 |
| “内置计算引擎”显示缺失 | 重新完整解压 ZIP；确认 `workflow/BeamGenerator.Workflow/BeamGenerator.Workflow.exe` 和 `_internal/` 都存在。不要单独移动 DLL 或 EXE。 |
| 找不到 config | 确认同一计算引擎目录存在 `config.yaml`；DLL 路径由插件根据当前 ETABS 宿主自动覆盖，不需要手工改盘符。 |
| 便携工作流退出码非零 | 查看插件日志和新输出目录；错误不会被转换为成功，也不会触发写回。 |
| 关闭窗口被阻止 | 后台计算进程仍在运行；等待完成或确认终止由插件启动的进程。 |

## 7. 初始版本限制

- ETABS 22 的真实菜单加载证据与 ETABS 23 兼容证据分别由 Validation Record 裁定。
- 这不是正式安装器或签名发布包。
- ZIP 当前约百兆，采用目录式自包含运行时；必须保持目录结构完整。
- 运行器尚未进行代码签名，企业终端可能需要将发布目录加入允许列表。
- CAD 插件、稳定编号/override UI、写回事务 UI、升级机制和企业部署仍未交付。
