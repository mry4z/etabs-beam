---
id: VAL-20260827-TOWER-SELECTION-RECOVERY
type: validation
status: partial
owner: project
created: 2026-08-27
updated: 2026-08-28
related: [SPEC-PLUGIN-001, VAL-20260827-TOWER-MIGRATION]
supersedes: []
superseded_by:
---

# VAL-20260827：分塔选择集恢复与连续运行

## 身份与范围

- 工作目录：`F:\2026\Research Study\etabs beam-v2`，分支 `codex/clean-start`。
- 按用户要求先完成迁移提交 `59a40b26f99b5dac35fdc7159a01c164e3c3f075`（`feat(plugin): migrate tower preflight into v2`），再实施本记录对应的修复。
- 当前修复保留在未提交工作区；前序次梁选筋/PB38 诊断相关修改仍保留，未混入迁移提交。完整离线测试覆盖整个当前工作区，不能描述成迁移提交自身的结果。
- 旧目录只作为迁移来源，没有修改其源码和历史成果。未连接/修改/保存 ETABS 实际模型，未执行分析设计、发布打包或远端推送。
- 使用本机 ETABS 22/23 安装目录的公开 `ETABSv1.dll` 做签名反射核对与 .NET 编译；没有把程序集文件版本误当成产品主版本。

## 根因和修复

旧实现逐塔执行 `Tower.SetActiveTower`，结束时再逐对象 `SetSelected` 恢复；3919 个对象会产生线性 API 往返，且选择历史可能在多次切塔中被覆盖。公共 `Story Definitions` 表已经包含 Tower/Story 映射时，切塔本身也是不必要的状态变更。

1. Python `EtabsSelectionSnapshot` 保存完整 `(ObjectType, UniqueName)` 集合，不限于梁；验证返回码、数组长度、名称及可恢复类型。ETABS 22/23 公开 `cSapModel` 仅对 Point/Frame/Tendon/Area/Link 提供可用对象入口，Cable/Solid 等无法安全回退的类型在切塔前明确阻断。
2. 恢复优先调用公开 `SelectObj.PreviousSelection()`，严格校验其返回码，并再次 `GetSelected` 比较完整集合。返回非零、返回成功但集合不符或接口不可用时，才清空并逐对象回退；回退也逐项校验，结果带策略、返回码、完整集合校验和调用次数审计。空选择及已无需恢复的选择均有独立路径。
3. `EtabsTowerReader` 仅在公共 `Story Definitions`、Point 和 Beam Connectivity 表字段/数据完整，且每一行 Story 与相关点都有有限绝对标高、同名跨塔标高可区分时走 `TOWER_TABLE_ONLY_PATH`，不调用 `SetActiveTower`；`Height` 单独存在、空值、部分目录、点 Z 缺失或同标高歧义会明确拒绝快路径并回退到按塔读取的公共 Story API。API 目录在同名跨塔且标高不完整时也会 `BLOCKED`，不会用当前 Tower 拓扑投票自证。
4. Python 在选择恢复入口、`PreviousSelection`、清空、每次对象写入及最终完整 `GetSelected` 读取前后复核模型身份；身份变化/身份读取失败立即退出，不进入 fallback，也不返回 `verified=true`。报告保留 `selected_objects_before`、`selected_objects_after`、`selection_restore_verified`；恢复不完整或模型身份改变时为 `BLOCKED`，不能用“确认继续”绕过。
5. GUI 宿主侧 `UiSessionCheckpoint` 同样先尝试 `PreviousSelection`，只在完整集合不符时逐对象回退；子进程正常结束且留下 `strategy=object_fallback` 审计时，宿主只读核验，失败保持 `UI_RESTORE_SELECTION_HANDOFF_BLOCKED`，不重复第二个对象循环。取消/硬终止或审计缺失没有可信完成责任交接，仍允许宿主承担一次受控恢复；活动塔、单位和模型身份守卫保留。
6. 计时拆分为 `TOWER_READ_TIMING_MS`（表读、Tower 目录、报告构建）和端到端 `TOWER_TIMING_MS`（`selection_capture`、`tower_read`、`selection_restore`、`read_report`、恢复策略）；GUI 另记录 `[ui recovery] host_restore_ms`。
7. Tower 人工确认继续绑定 PID、模型路径、已保存文件身份、处理模式和选中梁 Name/GUID。范围变化后清除旧确认；成果运行重新检查，不能将旧 Group 计划用于新增成员。
8. 没有改变本轮之外的配筋规则，也没有新增上部 1.2 倍放大；未连接、操作或保存用户指定的真实 ETABS 模型。旧发布 ZIP 尚未更新。

## 自动化验证

### Python

```text
$env:COVERAGE_FILE = "tmp/.coverage-tower-selection-fix-final4"
python -m pytest -q --junitxml=tmp/pytest-tower-selection-fix-final4.xml --cov=src --cov-report=xml:tmp/coverage-tower-selection-fix-final4.xml --cov-report=term
```

结果：**367 passed、1 skipped，覆盖率 80.88%**。跳过项为需显式开启的 ETABS 集成测试；这是包含并发工作区当前全部改动的离线结果，不能描述成迁移提交自身的结果。此前迁移中间态的 12 项 Tower 失败已由后续 356 passed 基线闭合；原失败证据未覆盖。

定向选择/Tower 测试：**46 passed**。覆盖空选择、混合选择、无恢复、PreviousSelection 非零、返回成功但完整集合不符、原历史被覆盖、多塔、读取/切塔失败、非零返回码、静默未选中、恢复中/最终读取时模型改变、验证读取失败、同名 Story 标高快慢路径一致性、缺失/空值/部分目录、连续 selected 检查、虚拟时钟计时，以及大集合正常批量路径的调用次数不随对象数线性增加。未放宽原有 PB/SB 配筋校核。

### 插件

```text
dotnet run --project tests/plugin/PluginOfflineTests.csproj -c Release
dotnet build plugins/etabs/BeamGenerator.EtabsPlugin/BeamGenerator.EtabsPlugin.csproj -c Release -p:EtabsApiPath="E:\Program Files\Computers and Structures\ETABS 23\ETABSv1.dll" --nologo
```

- 4 组请求参数场景通过。
- 20 组宿主恢复场景通过，包括批量恢复、批量结果不符回退、子进程已完成失败对象回退后的责任交接（宿主不重复循环）、模型身份在 PreviousSelection/对象写入/最终读取时改变，以及实际启动并强制终止测试子进程；测试使用假的会话接口，子进程不装载 ETABS。
- 8 组确认范围检查通过，含相同数量但不同梁、GUID 改变、PID/文件变化和 `all` 范围。
- 正式插件以本机 ETABS 22 和 23 的 `ETABSv1.dll` 分别 Release 编译：均 0 warning、0 error；只做程序集签名/编译核对，没有连接宿主。
- `git diff --check` 通过。Black 对包含前序并发改动的 `etabs_tower.py` 和测试文件仍提示可格式化，本次没有格式化整个文件/仓库。
- 文档检查仍报告 31 个既有历史输出链接缺失（与迁移前相同），没有新增失效链接；因此全仓文档检查不标为通过。

## 证据与边界

- [本机测试 JUnit](../../../tmp/pytest-tower-selection-fix-final4.xml)
- [本机覆盖率](../../../tmp/coverage-tower-selection-fix-final4.xml)
- 本次已生成 ETABS 23 可加载完整包：[解压目录](../../../build/etabs-plugin/v0.4.1-v2-etabs23-loadable-20260828)；[ZIP](../../../build/etabs-plugin/v0.4.1-v2-etabs23-loadable-20260828.zip)。包校验通过，包含插件 DLL、便携 workflow 和 manifest；未复制 `ETABSv1.dll`，尚未安装到 ETABS 或连接实模。
- 上述 XML 在本机 `tmp/` 中，使用了独立覆盖率文件；跨机器验证须重新运行。

结论为 **Partial：代码修复和离线回归通过，实模 GUI 尚未验收**。独立迁移证据见[迁移记录](VAL-20260827_TOWER_MIGRATION.md)。

仍需在用户指定的可处置模型副本中验收：选中多根梁并混选点/板，检查前后比较对象身份；随后直接生成梁表；分别测试正常结束、取消和关闭窗口；确认活动塔与单位保持原状。ETABS 22/23 的菜单实际加载和可见界面行为不能用编译或 fake 测试代替。

不在运行期间人工更换模型或选中对象。ETABS 崩溃、宿主被操作系统强制终止、恢复 API 自身失效等情形仍需人工核对；代码只会明确报错，不能保证这些情况下自动恢复。旧发布 ZIP 尚未更新。
