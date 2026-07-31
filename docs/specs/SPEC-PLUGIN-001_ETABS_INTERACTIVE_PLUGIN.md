---
id: SPEC-PLUGIN-001
type: spec
status: accepted
owner: project
created: 2026-07-30
updated: 2026-07-30
related:
  - SPEC-001
  - SPEC-002
  - ADR-0005
  - TASK-P2-PLUGIN-001
supersedes: []
superseded_by:
---

# SPEC-PLUGIN-001：ETABS 交互插件初始边界

## 1. 目的

为 ETABS Beam Generator 建立一个能由 ETABS 22/23 x64 直接加载的进程内交互入口。初始版本用于验证插件
合同、宿主身份、选择集交互和现有 Python 只读工作流的启动闭环，不在 .NET 插件中复制已经存在的拓扑、
设计结果或配筋算法。

## 2. 要求

| ID | 要求 |
|---|---|
| `PLUG-R001` | 程序集包含 ETABS 可按 `<AssemblyName>.cPlugin` 解析和实例化的公共类；本程序集入口为 `BeamGenerator.EtabsPlugin.cPlugin`，实现 `ETABSv1.cPluginContract` 的 `Info` 和 `Main`，并在窗口关闭时恰好一次调用 `cPluginCallback.Finish`。 |
| `PLUG-R002` | 初始宿主范围为 ETABS 22/23 x64；插件目标为 x64 .NET 8，构建时引用所选 ETABS 安装目录的 `ETABSv1.dll`，发布包不得携带该 DLL。 |
| `PLUG-R003` | 插件显示 PID、宿主/API 版本、当前模型路径、显示单位、模型锁定状态、Frame 总数和当前选择集。非零 OAPI 返回码必须带调用上下文显示为错误。 |
| `PLUG-R004` | 选择集导出为版本化 JSON，至少保留模型身份、宿主/API、生成时间、Object 名称、GUID、Label、Story、Section、设计方向和逐项诊断。 |
| `PLUG-R005` | 插件只可启动随包提供的 frozen workflow `--read-only`；目标机器不得依赖本机 Python、仓库或 `src/cli.py`。配置、输出目录和选择范围由用户在界面中确认；初始版本不得提供写回、保存、解锁、分析或设计操作。 |
| `PLUG-R006` | 后台计算进程没有独立 UI；插件以无窗口、非 shell、结构化参数启动，显式传入当前宿主 API DLL。运行输出和退出码在插件中可见，运行期间关闭窗口必须显式确认。 |
| `PLUG-R007` | 插件不得改变 ETABS 当前单位、选择集、对象、Group 或模型文件；任何加载、配置、宿主版本或模型身份不明确均 fail-closed。 |
| `PLUG-R008` | 提供可复现的构建、frozen runtime 自检、合同校验、安装、卸载和回退说明，并分别记录离线构建证据、脱离仓库路径证据与真实 ETABS 加载证据。 |
| `PLUG-R009` | 模型摘要页在 100%/高 DPI 和长路径下不得重叠；运行页只显示内置引擎状态，不要求普通用户选择或手动运行 EXE。 |

## 3. 初始交互

1. 用户从 ETABS `Tools > Add/Show Plugins` 浏览并添加插件 DLL。
2. 插件读取由 ETABS 传入的 `SapModel`，显示当前宿主和模型摘要。
3. 用户可刷新当前选择集并导出只读 JSON。
4. 用户确认内置计算引擎、`config.yaml`、输出根目录和 `all/selected`，再由插件后台启动便携只读
   拓扑工作流。
5. 用户关闭插件窗口；插件完成回调并把控制权交还 ETABS。

## 4. 非目标

- 初始版本不在插件进程内移植 Python 领域算法。
- 初始版本不执行模型写回、分析、设计、保存、创建 Group 或修改选择集。
- 初始版本不构成 `TASK-P2-PLUGIN-001` 的正式关闭；CAD 插件、稳定编号 UI、安装器、签名和 ETABS 23
  实模矩阵仍属于后续交付。

## 5. 验收

- Release x64 构建成功，发布目录不包含 `ETABSv1.dll`。
- 从不含仓库源码且未配置 Python 的隔离目录运行 frozen runtime 自检，并完成一次真实 ETABS 只读 run。
- 自动合同校验按 ETABS 的 `<AssemblyName>.cPlugin` 规则解析并实例化入口，再确认接口、方法签名、目标框架和构造函数。
- 两个页签均生成渲染截图，摘要字段无重叠，普通界面不显示运行器 EXE 路径。
- ETABS 22 实际可添加并打开窗口；刷新、JSON 导出、关闭回调至少完成一次。
- 若环境允许，插件启动一次 Python 只读 `selected` 工作流并记录退出码；否则明确记录为未验证 Gate。
