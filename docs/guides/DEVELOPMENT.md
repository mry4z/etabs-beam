---
id: GUIDE-DEV-001
type: guide
status: current
owner: project
created: 2026-07-25
updated: 2026-07-30
related:
  - GOV-001
  - ARCH-001
  - SPEC-002
  - REF-API-001
  - REF-OAPI-001
  - REF-REPO-001
supersedes:
  - LEGACY-DEVELOPMENT-GUIDE
superseded_by:
---

# 开发指南

本指南约束仓库内源码、测试和受治理文档的日常开发。系统职责见
[ARCH-001](../architecture/ARCH-001_SYSTEM_ARCHITECTURE.md)，目录归属见
[Repository Layout](../reference/REPOSITORY_LAYOUT.md)。

## 1. 安装开发环境

使用 x64 Python 3.11：

```powershell
python -m pip install -r requirements.txt
```

需要生成配筋工作簿时安装 Node 依赖：

```powershell
npm install
```

从 `config.example.yaml` 创建本机 `config.yaml`。配置加载器只接受 `N_mm_C` 和 `ACI 318-14`；正式 CLI
的 `--selection` 只接受 `all`、`selected`。运行入口均附着已打开的 ETABS，不创建新宿主进程。

ETABS 插件构建需要 PowerShell 7 和隔离的 PyInstaller venv，详见
[ETABS 插件指南](ETABS_PLUGIN.md)及其 [v0.2.0 构建 Validation](../state/validation/VAL-20260731_P2_ETABS_PLUGIN_V02.md)；
base anaconda 的 `python -m PyInstaller` 会因过时 `pathlib` 冲突而失败，且 Windows PowerShell 5.1
缺少 `Get-FileHash` 等构建脚本所需 cmdlet。

## 2. 本地验证

单元测试：

```powershell
python -m pytest tests/unit -q
```

默认测试集；集成测试未显式 opt-in 时会跳过：

```powershell
python -m pytest tests -q
```

ETABS 集成测试要求已打开并完成设计的目标模型、匹配的 DLL 和显式环境变量：

```powershell
$env:ETABS_INTEGRATION = "1"
python -m pytest tests/integration -q
Remove-Item Env:ETABS_INTEGRATION
```

静态检查：

```powershell
python -m compileall -q src
python -m black --check src tests
python -m flake8 src tests
```

`pyproject.toml` 定义 120 字符 Black 行宽、严格 pytest marker、`src` coverage 和最低覆盖率门槛；
`.flake8` 使用相同行宽并排除生成目录。一次运行的通过数和覆盖率只能写入 Validation Record，不能写入本指南。

## 3. 代码边界

| 位置 | 责任 |
|---|---|
| `src/core/` | ETABS 适配、几何、拓扑、人工 override、审查和写回事务。 |
| `src/design/` | 设计结果领域模型、OAPI 映射和配筋策略。 |
| `src/drawing/` | 消费有向拓扑并生成 DXF/CSV；不访问 ETABS。 |
| `src/spreadsheet/` | 从版本化 payload 生成工作簿。 |
| `src/excel_export.py` | 从领域对象生成基础 Excel。 |
| `src/excel_span_adapter.py` | 把 Object 设计站点映射到结构 Span。 |
| `src/rebar_workbook.py` | payload、离线 redetail 和 Node 工作簿编排。 |
| `src/run_manifest.py` | run 身份、输入指纹、哈希和注册表。 |
| `src/cli.py` | PID 定位、门禁、读写事务、分析设计、审计和导出编排。 |
| `src/workflow.py` | 简化活动实例基础 Excel 入口；不承载安全写回。 |

领域算法只接受 Python 数据对象；COM/OAPI 细节集中在适配层。导出器不得自行重新读取 ETABS，编号规则
不得在 Excel、DXF、JSON 和写回模块各自拼接。

## 4. 增加或修改 OAPI 调用

1. 使用 `src.core.etabs_connector.api_values()` 检查返回码和拆出 `out/ref` 值。
2. 按 pythonnet 3.x 实际签名传入占位值；枚举 `out/ref` 参数使用对应 .NET 枚举，不用裸整数代替。
3. 几何优先复用 `FrameObj.GetAllFrames` 批量快照，不在循环中重复读取可批量取得的字段。
4. 有单位语义的调用置于 `temporary_present_units(eUnits.N_mm_C)` 中，并覆盖异常恢复测试。
5. 非零 API 返回码、空数组和未知字段必须显式失败或形成诊断，不能变成零值。
6. 写入方法保留 `allow_mutation=True` 门禁，并同时实现验证和失败回滚。

调用模式、版本差异和已封装入口见 [ETABS OAPI Reference](../reference/ETABS_OAPI.md)。实模结果必须新建
Validation Record，不把运行数字追加到 Reference。

## 5. 修改 CLI 或配置

- 参数在 `src.cli._parser()` 定义，执行前置条件集中在 `_run_impl()` 的入口门禁。
- 写回相关参数不得在只读模式下被静默忽略；组合无效时应在连接 ETABS 前失败。
- `--expected-model` 的目标身份校验、单位恢复、保存时机和 manifest 生成不得绕过。
- 新配置项必须由源码读取并有测试；未被执行路径消费的键不能在 Guide 中宣称有效。
- 新成果进入 `summary["outputs"]`，由 `RunManifest` 记录路径和哈希。
- schema 改动同时更新序列化测试、[Interchange Schemas](../reference/schemas/INTERCHANGE_SCHEMAS.md)及关联 Spec。

## 6. 测试策略

- 纯几何、拓扑、配筋、schema、事务门禁和回滚使用不依赖 ETABS 的单元测试。
- OAPI 签名、权限、宿主版本、真实模型结果和保存重开使用 opt-in 集成或精确实模 Validation。
- 每个错误分支至少验证：异常可见、没有静默丢失对象、模型或输出不会被标记为完整成功。
- 参数变化需比较同一输入下的结构差异；不同模型、配置或 dirty worktree 的结果不得合并。
- 生成物存在不证明测试通过；验收结论引用同一版本的日志、manifest 和哈希。

## 7. 文档与生成物

按 [文档治理纲领](../governance/DOCUMENTATION_GOVERNANCE.md)更新文档：要求变化进入 Spec，结构变化进入
Architecture/ADR，执行工作进入 Plan/Task，运行结果进入 Validation，再由 Current Baseline 汇总。

`build/`、`outputs/`、`.pytest_cache/`、媒体、帧图和本机工具缓存不是生产源码或当前文档权威。生产实现必须
位于受跟踪的 `src/`/`tools/`，测试位于 `tests/`；大成果保留在生成目录，由小型 Validation Record 引用。

## 8. Agent 模型分配建议

在允许使用多个 Agent 且任务可以安全拆分时，按任务的歧义、风险、影响范围和可验证性选择模型，不固定绑定
某个型号；模型目录变化时使用当前能力相当的非 fast 模型。选择依据依次为：**质量与正确性第一，token 与费用
第二，完成速度第三**。

| 任务特征 | 建议模型档位与用法 |
|---|---|
| 机械性修改、重复整理、格式转换、明确模式下的批量操作，且结果可由脚本或测试完整验证 | 使用较低或经济档模型执行；限定输入、输出和验收条件。 |
| 边界清楚的常规实现、测试补充、局部重构和资料归纳 | 使用中等能力模型执行；失败、范围扩大或需求出现歧义时升级。 |
| 架构与接口决策、跨模块改动、复杂缺陷诊断、陌生 API、工程计算、ETABS 模型写回及其他高影响操作 | 使用高推理档模型负责分析和实现，不因节省 token 或追求速度下调。 |
| 中高风险成果的验证与校核 | 由未执行原任务的 Agent 独立复核，使用至少同等、关键场景更高的推理档；核对需求、假设、差异、测试和失败路径。 |

出现需求不明确、证据相互矛盾、连续尝试失败、改动超出原边界、测试无法覆盖关键结论或涉及不可逆操作时，
应立即升级模型或增加高推理档复核。低档模型的产出不得仅凭表面完整即被接受；确定性脚本和测试可以承担
机械校验，但不能替代对工程语义、安全边界和关键取舍的高质量判断。

运行 ETABS、工程模型或其他长时任务时，应预期执行可能持续较长时间。建议将轮询或状态检查间隔设为
3～5 分钟；若状态没有变化，检查时无需向用户重复汇报模型进度。仅在任务进入新阶段、发生错误、完成，或
需要用户介入时再汇报，以减少无效状态消息并保留关键节点的可见性。

## 9. 提交前检查

1. 检查 `git status --short`，区分本次改动与已有用户改动。
2. 运行与影响范围匹配的测试、compileall、Black 和 Flake8。
3. 核对配置、CLI help、Guide、Reference 和 schema 是否与源码一致。
4. 对受治理 Markdown 运行链接、ID、类型和状态审计。
5. 若改变当前能力，先生成证据，再更新 [Current Baseline](../state/CURRENT_BASELINE.md)。
