# ETABS Beam Generator 用户指南

> 当前状态：P0 生产候选。只读拓扑、模型副本写回、ETABS 分析设计、三表配筋 Excel、成果 manifest、全属性回滚和幂等验证均已接入正式 CLI；ETABS 内人工分跨 UI 属于后续插件阶段。

## 1. 环境与模型确认

当前正式支持 ETABS 22.7.0 和 23.3.1、x64 Python 3.11 及公开 `ETABSv1` OAPI。DLL 必须来自实际宿主安装目录。ETABS 23 示例：

```yaml
etabs:
  dll_path: "E:\\Program Files\\Computers and Structures\\ETABS 23\\ETABSv1.dll"
```

运行前必须完成以下检查：

1. 打开目标 ETABS 模型；只读导出前模型应已完成分析和混凝土设计。
2. ETABS 与 Python 使用相同 Windows 权限级别。ETABS 以管理员权限启动时，终端也必须提升权限。
3. 记录 ETABS 进程 ID（PID），并核对 API 返回的完整模型路径。PID 只说明进程身份，模型路径才是本次操作对象的最终确认。
4. 同时开启多个 ETABS 实例时，不得只根据“成功连接”继续操作；应关闭非目标实例，或使用当前模型专用 PID 验证脚本明确连接对象。
5. 写回前确认当前打开的是带时间戳的工作副本，而不是原始 EDB。

可在 PowerShell 查看 ETABS PID：

```powershell
Get-Process ETABS | Select-Object Id, ProcessName, StartTime, Path
```

正式 CLI 启动后会打印 `GetModelFilename()` 返回的路径。路径与目标模型不完全一致时，应立即停止。

## 2. 正式只读导出

复制 `config.example.yaml` 为 `config.yaml`，至少把 DLL 路径改为当前 ETABS 22/23 宿主的安装路径，然后运行：

```powershell
# 读取模型中全部符合条件的混凝土梁
python -m src.workflow --config config.yaml --selection all

# 仅处理 ETABS 当前选择集
python -m src.workflow --config config.yaml --selection selected
```

该命令的边界是：

- 会读取几何、截面、材料、柱支承和混凝土设计结果；
- 会识别 Beam Line/Span，并整理左 `2D`、跨中、右 `2D` 关键位置；
- 会导出 Excel 和异常记录；
- 会记录原显示单位，临时切换为 `N_mm_C`，最后恢复原单位；
- 不会改变 I/J、Unique Name、截面、荷载，也不会自动运行分析或设计。

如果运行中发生异常，单位恢复仍由上下文管理器执行；但用户仍应在 ETABS 界面确认显示单位是否与运行前一致。

## 3. 梁筛选和分跨规则

只有用于混凝土梁设计的 Frame Object 进入本工作流：

- 设计方向为 Beam；
- 具有有效混凝土截面和梁宽、梁高；
- `None` 截面、非混凝土梁以及截面名含 `rigid` 的辅助杆件被排除。

`rigid` 杆件不进入梁表；只有与真实柱锚定的刚域链才把柱支承传播到真实梁端。支承名称和三维坐标通过批量 Frame 快照读取，长 Beam Object 内部的柱/等效柱也会参与分跨。

结构语义规则如下：

- 同楼层、端点相接且局部切线折角不超过 15° 的连续段可组成一根 Beam Line；截面变化本身不拆分梁号；
- 主梁 PB 按柱支承分跨；普通梁交点不应把 PB 再拆跨；
- 次梁 SB 按主梁 PB 支承分跨；SB 与 SB 相交不增加跨数；
- 悬挑段单独作为一跨；
- 弧形主梁单独编号，路径方向沿整条 Beam Line 连续；
- 一个 ETABS Object 同时贡献多个结构 Span 时标记 `REQUIRES_SPLIT`，不得静默取其中一个编号。

最新 v6.4.3 只读基线是 3,020 个 Span，29 个 `REQUIRES_SPLIT` Object 进入专项审计；源 Object 覆盖完整，拓扑 BLOCKED 为 0。不同 EDB/算法版本的数量不得直接混用。详见 [BEAM_LINE_SPAN_RULES.md](BEAM_LINE_SPAN_RULES.md)。

## 4. DXF 和梁编号中间数据

当前 DXF 导出能力已经在 `src/drawing/dxf_export.py` 实现，但尚未并入 `python -m src.workflow` 的统一命令。现阶段 DXF 由 `build/output/reports/` 下的模型专用验证脚本生成。

DXF 约定：

- 一跨一条多段线；
- PB 梁线及编号为黄色，SB 梁线及编号为绿色；
- 编号放在相应跨的路径中点；
- 编号格式为 `楼层-PB/SB梁号-跨号`，例如 `F1-PB5-3`；
- 当前 CSV 保留源 Object、路径坐标和 Span 编号；带 GUID、I→J 和 schema 版本的正式 JSON 属于阶段 10 数据契约。

DXF 当前用于几何审核，不能单独作为模型写回依据。CAD 正式模块必须保证文字方向严格等于模型 I→J，不能为了屏幕可读性把文字旋转 180°；否则梁表左/中/右钢筋会与图纸方向错配。

## 5. Excel 输出

### 5.1 正式 CLI 标准 Excel

`python -m src.workflow` 输出至配置中的 `output.reports_dir`，当前包含：

- `梁表`：Object/逻辑梁摘要和左、中、右关键设计值；
- `设计站点`：ETABS Station、组合、纵筋、剪力筋和扭转筋；
- `梁分跨`：Beam Line/Span 及源 Object 映射；
- `导出说明`：单位、字段来源和约定；
- `异常记录`：缺失设计结果、拓扑和筛选异常。

### 5.2 三表配筋复核 Excel

已验证的工程配筋工作簿包含：

1. `原始分跨数据`：按结构 Span 整理的 ETABS 原始计算值；
2. `配筋调整复核`：配筋文本、实配面积、计算需求和人工调整区；
3. `整理后的梁表`：用于后续 CAD 梁表流程的排版数据。

工作簿中的配筋文本公式（例如 `8T25 6/2`）只用于计算实配面积并与需求值对比，不替代配筋引擎。正式入口为：

```powershell
python -m src.cli --pid <PID> --expected-model <MODEL.EDB> `
  --read-only --export-rebar-workbook --output-dir <OUTPUT_DIR> `
  --artifact-node <NODE.EXE> --artifact-node-modules <NODE_MODULES>
```

大型模型默认给 Node 8192 MB 堆空间，可用 `--artifact-node-max-old-space-mb` 调整。写回后导出三表时必须同时启用 `--run-analysis --run-design`；程序会在分析设计后重新读取当前名称和结果。混合截面、混合 Frame Type 或无法确定抗震 Frame Type 的跨不会静默套用首个截面，而会输出阻塞状态供人工审查。

### 5.3 成果 manifest 与失效状态

每次正式 CLI 运行都会在本次输出目录生成唯一 `run_manifest_<run-id>.json`，并在输出目录同级的 `manifests/` 保存跨运行注册副本。manifest 记录模型和成果 SHA-256、实际模型路径、ETABS/API、PID、单位、规范、Git/算法、配置、返回码和计数。

对工作副本运行时应增加：

```powershell
--source-model <UNMODIFIED_SOURCE.EDB>
```

需要把多个输出根目录归入同一项目注册表时，使用 `--manifest-registry <DIR>`。`CURRENT` 表示与当前输入指纹一致的成功成果，`SUPERSEDED` 表示已被不同模型/配置/算法输入替代，`INVALID` 表示该次运行失败。不得仅按文件时间选择成果。

## 6. 模型写回：仅限工作副本和显式事务

当前 `src/core/model_writeback.py` 已提供正式的底层计划和执行组件，`python -m src.cli` 已接入统一写回门禁。写回必须遵循：

```text
原始 EDB 哈希
  → 创建带时间戳的工作副本
  → 只读生成方向计划和改名计划
  → 检查 BLOCKED / REQUIRES_SPLIT / REQUIRES_SECTION_REVIEW
  → 显式执行 I/J 事务并验证
  → 显式执行两阶段改名并验证
  → 保存副本
  → 重新分析、混凝土设计
  → 重新导出 DXF/Excel 并比较
```

关键安全规则：

- 禁止在原始 EDB 上测试写回；禁止覆盖既有副本。
- `plan_orientation()` 和 `plan_rename()` 只生成计划，不修改 ETABS。
- 执行方法必须显式传入 `allow_mutation=True`。
- I/J 通过 `EditFrame.ChangeConnectivity` 调整；`SetLocalAxes` 不能交换局部 1 轴方向。
- 改名通过“旧名→临时唯一名→最终名”执行，以避免名称占用。
- 内部身份使用 GUID，写后重新读取 GUID、I/J、名称和对象数量验证。
- 当前模型专用脚本曾过滤阻塞项后部分写回；这只是验证行为。正式工作流将默认存在任何阻塞项就拒绝执行，除非用户显式允许部分写回。
- 同一 Span 含多个截面时，方向与编号同时暂停。只读运行会生成 `multi_section_span_review.json/.csv`；可显式使用 `--prepare-section-review-groups` 创建彩色 `CODX_MS_*` Group，再用 `--select-section-review-group` 高亮。确认 step beam 后通过 `--confirmed-multi-section-spans` 重算计划；判断为误归并则建立人工拓扑 override。

当前写回验证脚本位于 `build/output/reports/`，文件名中包含模型版本。它们不是通用工具：可能写死模型路径、PID、输出名或分析顺序，不能直接换模型运行。

## 7. 分析、设计和耗时

只读 CLI 不自动修改模型，也不自动运行分析/设计。对于写回副本：

- 修改连接或名称后，应重新创建分析模型、运行分析并执行混凝土设计；
- 大模型创建分析模型可能长时间没有明显进度，这是已观察到的正常耗时；
- 必须以 ETABS 分析日志中的 `ANALYSIS COMPLETE`、OAPI 返回码和设计结果重新可读三项共同判断完成；
- 中途不得通过反复点击或启动另一个写回脚本干扰同一实例。

## 8. 成果版本与失效提示

拓扑规则变化后，旧的 EDB、DXF、CSV/JSON 和 Excel 必须成套失效并重新生成。目前：

- v6.4.1 的 3070 跨成果基于旧分跨算法；
- v6.4.3 的 3074 跨配筋成果也基于旧分跨算法；
- Object `302` 修复后的最新只读基线为 3010 跨。

因此旧文件只能用于历史对比，不能作为最终验收成果。正式重算应等待剩余 24 个阻塞 Object 和 CAD 全量复核完成。

## 9. 常见问题

| 现象 | 原因与处理 |
|---|---|
| 未找到 ETABS 实例 | 检查 ETABS 是否已打开、x64 位数、DLL 版本，以及 Python/ETABS 权限级别是否一致。 |
| 连接成功但模型不对 | 核对 PID 和 `GetModelFilename()`；关闭其他 ETABS 实例后重试。 |
| 没有设计结果 | 在 ETABS 内完成分析与混凝土设计；同时检查 Frame 是否属于混凝土梁设计。 |
| 某些梁没有写回编号 | 查 `BLOCKED/REQUIRES_SPLIT/REQUIRES_SECTION_REVIEW`；跨 Span Object 或尚未确认的多截面 Span 会被安全阻止。 |
| DXF 分跨与模型不同 | 先查支承类型、Z 高程、刚域和算法版本；旧 DXF 可能已因拓扑更新失效。 |
| Excel 出现 `NO_ALLOWED_SPACING` | 当前需复核保护层语义、梁宽和可用直径；不得直接删除异常记录。 |
| ETABS 单位被切换 | 正常导出会自动恢复；异常退出后在 ETABS 界面核对原单位并查看日志。 |

API 细节见 [ETABS_OAPI_VALIDATION.md](ETABS_OAPI_VALIDATION.md)，写回和 CAD 编号约束见 [DRAWING_NUMBERING_INTERCHANGE.md](DRAWING_NUMBERING_INTERCHANGE.md)，SQLite 路线见 [ETABS_SQLITE_SNAPSHOT_ROUTE.md](ETABS_SQLITE_SNAPSHOT_ROUTE.md)。
