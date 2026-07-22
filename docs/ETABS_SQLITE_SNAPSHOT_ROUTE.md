# ETABS SQLite 数据快照技术路线

## 1. 决策与适用范围

**结论：采用“公开 OAPI 批量表格读取 → 项目自建 SQLite 只读快照”的混合路线。**

SQLite 快照是梁线/分跨、编号、Excel、DXF 和后续 CAD 中间数据的稳定数据源；它不替代 ETABS 模型文件，也不承担模型修改。模型局部坐标反转、Object Unique Name 回写、分析和设计仍必须通过公开 OAPI 完成。

本路线的目标是减少跨进程 OAPI 调用、使后处理可脱离 ETABS 重复执行，并为按楼层并行计算提供只读数据基础。它不承诺缩短 ETABS 本身的分析或设计时间。

## 2. 已核实的 ETABS 23 能力与边界

本机目标安装：

| 项目 | 已核实值 |
| --- | --- |
| ETABS 根目录 | `E:\Program Files\Computers and Structures\ETABS 23` |
| 程序版本 | `ETABS_v23.3.1` |
| 公开 API DLL | `E:\Program Files\Computers and Structures\ETABS 23\ETABSv1.dll` |
| API DLL 文件版本 | `2.16.0.0` |
| API 帮助 | `E:\Program Files\Computers and Structures\ETABS 23\CSI API ETABS v1.chm` |

CSI 在 ETABS 23.3.0/23.3.1 的正式更新中新增了“数据库表导出 SQLite 格式”能力；见 [CSI 官方更新说明](https://www.csiamerica.com/products/etabs/enhancements/20-20.0.0)。

但对上述 `ETABSv1.dll` 的公开接口反射核查结果如下：

- `cDatabaseTables` 提供 `GetTableForDisplayArray`、CSV、XML、Excel 等批量表格读取能力，**没有**公开 SQLite 导出方法；
- `cFile.ExportFile` 的 `eFileTypeIO` 只有 Text、Excel、Access、Text Tables、XML，**没有** SQLite 枚举；
- 安装目录的内部程序集确有 SQLite 组件和导出代码，但该实现不是公开 OAPI 合同的一部分。

因此，不得通过反射调用内部 `CSIDatabase` SQLite 类作为生产方案。此做法会把项目绑定到未承诺的内部实现，ETABS 补丁升级后可能失效。

## 3. 推荐的数据路径

```text
ETABS OAPI（连接、状态、单位、批量表格、模型写入）
                         |
                         v
项目 SQLite 快照（只读、可校验、可复现）
                         |
                         v
梁筛选 -> 梁线/支承分跨 -> 编号 -> Excel / DXF / JSON-CAD
```

### 3.1 快照导出规则

1. 通过 OAPI 读取并保存原始显示单位，切换到 `N_mm_C`；所有表格读取完成后，无论成功或失败均恢复原单位。
2. 用 `DatabaseTables.GetAvailableTables` 查询当前模型实际可用的表，不假设某一设计规范表必定存在。
3. 用 `GetTableForDisplayArray` 批量读取所选表；禁止为每根梁循环调用几何、截面、点坐标或设计结果 API。
4. 先写入 `*.sqlite.partial`，完成数据写入、索引创建和 `PRAGMA integrity_check` 后再原子改名为 `*.sqlite`。
5. 快照文件完成后以只读方式打开；后续梁线、分跨、Excel 与 DXF 不再连接 ETABS。
6. ETABS 的模型写回是独立步骤。任何写回成功后，旧快照立即标记过期并重新导出。

建议归档位置：

```text
build/output/model_archives/<模型文件名>_<YYYYMMDD_HHMMSS>.sqlite
```

该目录和 SQLite 文件均不纳入 Git。

### 3.2 首批读取表

下列键名已在 ETABS 23 `Table and Field Keys.xml` 中核实。实际导出仍以 `GetAvailableTables` 返回值为准。

| 表键 | 用途 |
| --- | --- |
| `Beam Object Connectivity` | 梁 Object、楼层、I/J 点、长度、曲线类型 |
| `Column Object Connectivity` | 柱支承识别 |
| `Point Object Connectivity` | 点的 X/Y/Z 坐标、楼层、特殊点属性 |
| `Frame Assignments - Section Properties` | 截面、梁/刚域/非混凝土筛选的基础信息 |
| 当前规范的 `Concrete Beam Design Summary - <Code>` | Station、AsTop、AsBot、VRebar、TLngRebar、TTrnRebar、状态及消息 |
| `Story Definitions` | 楼层排序、标高和按楼层并行的边界 |

设计汇总表名称随设计规范变化，例如当前模型可能使用 `Concrete Beam Design Summary - Chinese 2010`。导出器必须按可用表动态选择，并将实际表键写入元数据；不得将某一规范名称硬编码为唯一选择。

### 3.3 快照最小结构

快照既保存规范化查询表，也保留来源信息，避免 ETABS 版本或字段名称变化时失去可追溯性。

| SQLite 表/信息 | 最低内容 |
| --- | --- |
| `snapshot_metadata` | 模型绝对路径、文件大小/修改时间/哈希、ETABS 与 API 版本、原/导出单位、导出时间、设计规范、数据源模式 |
| `source_tables` | ETABS 表键、字段顺序、字段名、行数、导出时选择的荷载输出条件 |
| `beam_objects`、`column_objects`、`points`、`sections`、`stories` | 用于梁线、支承和筛选的规范化字段 |
| `beam_design_results` | Object、Station、弯曲/剪力/扭转字段、状态、组合名和异常消息 |
| `raw_<table>`（可选） | 原始字段值，供版本差异诊断与重新映射 |

`snapshot_metadata` 必须包含快照格式版本和字段映射版本。读取器发现版本未知、模型校验值不一致、单位不为 `N_mm_C`、或设计状态不完整时，应拒绝作为正式导出数据源。

## 4. 与现有工作流的接口

新增数据源层，保持现有 `BeamGeometry`、设计结果对象、`BeamLineSpanResult` 和 `excel_span_adapter.py` 的输入输出不变：

```text
EtabsDataSource
  |- ObjectApiSource             # 现有逐对象 OAPI，保留作回退与对照
  |- TableSnapshotExporter       # OAPI 批量表格 -> SQLite
  |- SqliteSnapshotSource        # SQLite -> 既有领域对象
  `- NativeEtabsSqliteSource     # 未来：仅在 CSI 提供公开自动化接口后实现
```

这使将来的梁编号、局部坐标或表格字段调整集中在数据源/映射层；Excel 导出模块和分跨适配层不需要因数据存储方式变化而改动。

ETABS 界面手动导出的 SQLite 可作为**人工导入样本**，经模式检查和字段映射后读取；在公开 OAPI 提供自动导出前，不把它列为无人值守工作流的依赖。

## 5. 后续任务与验收顺序

### 阶段 A：快照契约与最小导出器

1. 增加 `src/data_sources/`，定义只读 `EtabsDataSource` 协议和快照元数据数据类。
2. 实现 `TableSnapshotExporter`：按第 3.2 节读取首批表、固定 `N_mm_C`、写入 `*.partial`、完整性检查、恢复原单位。
3. 增加 CLI，例如 `python -m src.workflow snapshot --selection all`；默认不覆盖已有快照。

验收：快照可用 SQLite 客户端只读打开；`integrity_check` 为 `ok`；元数据完整；导出前后 ETABS 当前单位与模型状态完全一致。

### 阶段 B：等价读取与字段验证

1. 实现 `SqliteSnapshotSource`，将快照还原为当前几何和设计结果领域对象。
2. 用同一模型分别运行 `ObjectApiSource` 与 SQLite 路线，比较对象数、楼层、截面、I/J 坐标、长度、混凝土筛选结果和设计站点。
3. 对每个 Station 比较 `AsTop`、`AsBot`、`VRebar`、`TLngRebar`、`TTrnRebar`、状态和组合名称；明确弯曲配筋与扭转纵筋的字段语义，不允许静默替换。

验收：对象覆盖率 100%；数值比较采用配置化容差；缺失表、空设计数组、未知字段映射均生成可审计异常记录并阻止正式 Excel 导出。

### 阶段 C：正式工作流与性能基准

1. 将 SQLite 源作为 Excel/DXF 工作流的可选输入，不改变现有默认 OAPI 路径。
2. 对 4,166 根混凝土梁的真实模型连续运行三次，分别记录冷启动快照时间、SQLite 离线梁线/Excel/DXF 时间、总时间、文件大小和内存占用。
3. 对按楼层的只读分析实施并行实验；SQLite 写入保持单写者，快照生成完成后才开始并行读取。

验收：SQLite 输出与已复核的梁表、梁分跨记录一致；重复离线运行不附着 ETABS；性能报告只报告实测值，不以预估值替代结论。

### 阶段 D：原生 SQLite 导出兼容性（可选）

当 CSI 发布公开 OAPI SQLite 导出方法或文档化的命令行/自动化入口后，新增 `NativeEtabsSqliteSource` 并与项目自建快照进行字段、行数和性能对比。在此之前，不调用内部程序集，不将界面自动化作为正式依赖。

## 6. 风险控制

- **快照过期**：以模型路径、时间、尺寸与哈希校验；模型发生写回后强制重建。
- **设计规范/字段变化**：表键和字段映射版本化，按当前可用表动态发现。
- **单位污染**：单位切换使用 `try/finally` 恢复，元数据记录原单位和导出单位。
- **模型安全**：快照路线仅读取 OAPI，不调用 `SetTableForEditing*`、`ApplyEditedTables` 或模型编辑 API。
- **性能误判**：区分“ETABS 到快照”的冷启动时间与“快照后的离线处理”时间；不以单次运行下结论。

## 7. 参考

- [CSI：ETABS 23.3.0 / 23.3.1 增强功能](https://www.csiamerica.com/products/etabs/enhancements/20-20.0.0)
- [CSI：ETABS Database Tables OAPI 更新说明](https://installs.csiamerica.com/software/ETABS/18/ReleaseNotesETABSv1800.pdf)
- 本机 API 帮助：`E:\Program Files\Computers and Structures\ETABS 23\CSI API ETABS v1.chm`
