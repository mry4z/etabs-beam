---
id: GUIDE-001
type: guide
status: current
owner: project
created: 2026-07-25
updated: 2026-07-25
related:
  - SPEC-002
  - SPEC-TOPO-001
  - SPEC-DRAW-001
  - ADR-0002
  - BASELINE-001
  - REF-API-001
  - REF-OAPI-001
  - REF-SCHEMA-001
supersedes:
  - LEGACY-USER-GUIDE
superseded_by:
---

# ETABS Beam Generator 用户指南

本指南说明仓库中可直接运行的命令。能力是否已被当前工作树和实模验证，以
[Current Baseline](../state/CURRENT_BASELINE.md) 为准；工程要求和安全边界分别见
[工程不变量](../specs/SPEC-002_ENGINEERING_INVARIANTS.md)、
[梁线与结构分跨](../specs/SPEC-TOPO-001_BEAM_LINE_SPAN.md)和
[工作副本写回决策](../decisions/ADR-0002_SAFE_WORKING_COPY_WRITEBACK.md)。

## 1. 运行前准备

1. 使用 x64 Python 3.11，并安装 [requirements.txt](../../requirements.txt) 中的依赖。
2. 打开目标 ETABS 22 或 23 模型；Python、ETABS 与终端使用相同 Windows 权限级别。
3. 从 [config.example.yaml](../../config.example.yaml) 创建 `config.yaml`，把
   `etabs.dll_path` 改为与已打开宿主主版本一致的 `ETABSv1.dll`。
4. 需要设计结果的 Excel 或工作簿导出前，在 ETABS 中完成分析和混凝土设计，并确认设计规范符合
   [SPEC-DESIGN-001](../specs/SPEC-DESIGN-001_RESULT_SEMANTICS.md)。
5. 写回前自行创建、打开并核对一个不覆盖原文件的 EDB 工作副本。`src.cli` 不负责创建副本；
   `--source-model` 只把未修改源模型的身份和哈希写入 manifest。

查看 ETABS PID：

```powershell
Get-Process ETABS | Select-Object Id, ProcessName, StartTime, Path
```

`--expected-model` 在参数解析层不是必填项，但受治理的运行必须提供它。CLI 会同时核对 PID 和
`GetModelFilename(True)`；ETABS 报告同目录、同 stem 的 `.$et` 工作文件时视为同一模型，其他路径均拒绝。

## 2. 正式只读拓扑审计

```powershell
python -m src.cli `
  --pid <PID> `
  --expected-model "<MODEL.EDB>" `
  --config config.yaml `
  --read-only `
  --selection all `
  --output-dir "<OUTPUT_DIR>"
```

`--read-only` 是推荐的显式写法；未选择其他互斥模式时源码也按只读执行。`--selection` 仅接受
`all` 或 `selected`。后者读取 ETABS 当前选择集中的 Frame Object，再筛选合格混凝土梁。

每次运行都会生成：

- `topology_spans.csv/.dxf`；
- `requires_split_audit.json/.csv/.dxf`；
- `multi_section_span_review.json/.csv`；
- `topology_diagnostics.json`；
- `topology_summary.json` 和唯一 `run_manifest_<run-id>.json`。

这些文件是审计输出，不等于正式 CAD 交换包。字段边界见
[Interchange Schemas](../reference/schemas/INTERCHANGE_SCHEMAS.md)。

## 3. 设计结果与工作簿导出

在只读审计命令后追加 `--export-excel`，生成基础梁表：

```powershell
python -m src.cli `
  --pid <PID> `
  --expected-model "<MODEL.EDB>" `
  --config config.yaml `
  --read-only `
  --export-excel `
  --output-dir "<OUTPUT_DIR>"
```

导出配筋评审工作簿和独立次梁表时，先在仓库根目录安装 Node 依赖，再运行：

```powershell
npm install

python -m src.cli `
  --pid <PID> `
  --expected-model "<MODEL.EDB>" `
  --config config.yaml `
  --read-only `
  --export-rebar-workbook `
  --artifact-node node `
  --artifact-node-modules ".\node_modules" `
  --output-dir "<OUTPUT_DIR>"
```

Node 默认最大堆为 8192 MB，可用 `--artifact-node-max-old-space-mb` 调整。两类设计导出都要求
`DesignConcrete.GetResultsAvailable()` 为真；CLI 使用 `GetSummaryResultsBeam_2` 读取正式 Total/Min/Provided
字段，语义见 [Design Results Reference](../reference/api/design-results.md)。

## 4. 多截面 Span 复核

只读审计会列出多截面 Span。创建 ETABS 审查 Group 会修改 Group 元数据并保存当前模型，因此只能对已核对的
工作副本执行：

```powershell
python -m src.cli `
  --pid <PID> `
  --expected-model "<WORKING_COPY.EDB>" `
  --config config.yaml `
  --prepare-section-review-groups `
  --output-dir "<OUTPUT_DIR>"
```

高亮一个已经创建的 Group：

```powershell
python -m src.cli `
  --pid <PID> `
  --expected-model "<WORKING_COPY.EDB>" `
  --config config.yaml `
  --select-section-review-group "CODX_MS_<SpanID>" `
  --output-dir "<OUTPUT_DIR>"
```

确认文件可为字符串数组，或包含 `confirmed_span_ids` 的对象：

```json
{
  "confirmed_span_ids": ["F1-PB5-3"]
}
```

把该文件通过 `--confirmed-multi-section-spans <FILE.json>` 输入后重新生成计划。确认只解除多截面门禁，
不覆盖其他拓扑、设计或写回阻塞项。

## 5. 写回工作副本

写回前确认 ETABS 当前打开路径就是工作副本，并保留未修改源文件。默认门禁会在存在拓扑 `BLOCKED`、
`REQUIRES_SPLIT` 或未确认多截面动作时拒绝执行。

```powershell
python -m src.cli `
  --pid <PID> `
  --expected-model "<WORKING_COPY.EDB>" `
  --source-model "<UNMODIFIED_SOURCE.EDB>" `
  --config config.yaml `
  --writeback `
  --run-analysis `
  --run-design `
  --verify-full-writeback `
  --verify-demand-equivalence `
  --export-excel `
  --output-dir "<OUTPUT_DIR>"
```

`--verify-demand-equivalence` 要求写回前已有设计结果，并强制同时指定 `--run-analysis` 和
`--run-design`。写回后导出任一设计工作簿时，CLI 同样强制重新分析和设计，再重新读取当前名称、几何、
拓扑和设计结果。

仅在审查者明确接受部分运行边界时才使用 `--allow-partial-writeback`。该开关只执行计划中 `READY` 的动作，
不会把被阻塞对象变成成功。专项禁止写回对象通过以下格式传给 `--exclude-writeback-objects`：

```json
{
  "excluded_object_names": ["52", "56"]
}
```

方向事务使用 `EditFrame.ChangeConnectivity`，改名使用“当前名 -> 临时唯一名 -> 最终名”。任一执行、验证或
保存步骤失败时，CLI 尝试回滚；回滚失败时必须放弃该工作副本。

## 6. Manifest 与成果选择

`--manifest-registry <DIR>` 可把不同输出目录的 manifest 注册到同一目录；未指定时使用输出目录同级的
`manifests/`。不得按文件时间挑选成果，也不得把注册表的 `CURRENT` 输入指纹状态解释为项目已经验收。
证据有效性按 [文档治理纲领](../governance/DOCUMENTATION_GOVERNANCE.md)和
[Current Baseline](../state/CURRENT_BASELINE.md)判断。

## 7. 常见故障

| 现象 | 检查 |
|---|---|
| 未找到 PID | 确认 ETABS 已打开，PID 未变化，终端权限与 ETABS 一致。 |
| 模型路径不匹配 | 核对 `--expected-model`；关闭非目标实例，不要仅凭附着成功继续。 |
| 没有可导出的梁 | 检查选择集、Frame 设计方向、混凝土截面和 `rigid`/空截面筛选。 |
| 没有设计结果 | 在当前模型状态上完成分析和混凝土设计，检查设计规范与 ETABS error/warning。 |
| 写回门禁拒绝 | 查看拓扑 diagnostics、`requires_split` 和多截面审查文件，不要删除异常记录。 |
| 单位显示变化 | 停止后核对 ETABS 当前单位；正常上下文退出会恢复原单位，写回保存发生在恢复之后。 |

`python -m src.workflow` 是简化的活动实例 Excel 入口：它不接收 PID/期望模型，不提供安全写回或 manifest
编排。需要目标模型身份、拓扑审计、工作簿或写回时使用 `python -m src.cli`。
