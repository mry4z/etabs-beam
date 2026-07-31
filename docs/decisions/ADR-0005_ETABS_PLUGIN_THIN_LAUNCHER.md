---
id: ADR-0005
type: decision
status: accepted
owner: project
created: 2026-07-30
updated: 2026-07-30
related:
  - SPEC-PLUGIN-001
  - ARCH-001
  - ADR-0002
supersedes: []
superseded_by:
---

# ADR-0005：ETABS 插件采用 .NET 8 薄交互层

## Context

当前生产算法、OAPI 兼容处理、manifest 和工作副本门禁均在 Python 中。ETABS 22 的官方插件合同及
.NET 8 官方示例要求程序集在与程序集名一致的命名空间中包含 `cPlugin`；本项目的完整入口名为
`BeamGenerator.EtabsPlugin.cPlugin`。该类实现 `ETABSv1.cPluginContract`，并在结束时调用
`cPluginCallback.Finish`。若把现有算法重新实现于插件 DLL，会立即形成两套设计语义和安全门禁。

## Decision

采用 x64 `.NET 8` Windows Forms 插件作为薄交互层：

- 进程内 OAPI 只读取宿主身份、模型摘要和当前选择集；
- 版本化选择清单由插件直接导出；
- 完整业务流程由随发布包提供的 PyInstaller 目录式后台运行器执行；该运行器封装 Python、项目模块和
  第三方依赖，目标机器不需要本机 Python 或仓库源码；
- 后台运行器没有独立 UI，由插件用结构化参数和 `CreateNoWindow=true` 启动；插件按当前宿主传入
  ETABS PID、durable EDB、`ETABSv1.dll` 绝对路径、配置和非覆盖输出目录；
- 插件发布包不复制 `ETABSv1.dll`，运行时使用 ETABS 安装目录已加载的 API 程序集；
- 构建门禁必须按 `<AssemblyName>.cPlugin` 解析并实际实例化入口，不再只搜索短类名；
- 初始 UI 不暴露任何 mutation、保存、分析或设计操作。

插件和后台运行器之间只共享显式命令参数、配置路径和版本化输出，不共享内存状态或未经版本化的隐式文件。

## Consequences

优点：

- 最短路径验证 ETABS 内加载和交互，同时复用已经验证的 Python 领域链；
- 插件自身保持只读，模型风险小；
- Python/.NET 运行依赖与 ETABS 进程隔离，发布目录可整体迁移；
- 后续可以在不改变领域算法的情况下增加进度、异常审查和 override UI。

代价：

- 发布包体积增加到约百兆，必须保留 `workflow/` 目录结构；
- 便携运行器和插件需要作为同一版本整体升级、回退和签名；
- 插件进程与外部 OAPI 客户端并行工作的 ETABS 版本差异需要真实宿主验证；
- 正式安装器、签名、自动升级和 ETABS 23 矩阵尚未解决。

## Rejected Alternatives

- **在 C# 中重写全部领域算法**：会复制规则、扩大工程验证面。
- **插件直接嵌入 Python 运行时**：首版打包、ABI 和依赖隔离成本过高。
- **用 COM 注册插件**：需要管理员权限；官方 .NET DLL 浏览加载流程更简单。
