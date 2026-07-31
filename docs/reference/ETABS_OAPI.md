---
id: REF-OAPI-001
type: reference
status: current
owner: project
created: 2026-07-25
updated: 2026-07-30
related:
  - SPEC-002
  - ARCH-001
  - ADR-0002
  - ADR-0003
  - REF-API-001
  - REF-API-DESIGN-RESULTS
supersedes:
  - LEGACY-ETABS-OAPI-VALIDATION
superseded_by:
---

# ETABS OAPI 调用参考

本页记录当前源码依赖的稳定调用约定。历史模型、PID、数量、耗时和运行结论属于 Validation Record，
不属于 OAPI Reference。当前证据边界见 [Current Baseline](../state/CURRENT_BASELINE.md)。

## 1. 宿主与程序集

- Python、ETABS 和 `ETABSv1.dll` 使用 x64。
- 连接适配器接受 ETABS 主版本 22、23；DLL 主版本必须与已打开宿主一致。
- `clr.AddReference()` 使用配置中的 DLL 绝对路径，再从 `ETABSv1` 导入 `Helper`、`cHelper` 和枚举。
- ETABS 与 Python 进程权限级别不同可能导致附着返回 `None`。

## 2. 两种附着方式

完整 CLI 按 PID 附着：

```python
helper = cHelper(Helper())
app = helper.GetObjectProcess("CSI.ETABS.API.ETABSObject", int(pid))
sap = app.SapModel
model_path = Path(str(sap.GetModelFilename(True))).resolve()
```

`src.cli` 随后把 `model_path` 与 `--expected-model` 比较。ETABS 保存期间可能返回同目录、同 stem 的
`model.$et`；CLI 只把这种路径与 `model.EDB` 视为同一对象。

`EtabsConnector.attach()` 使用：

```python
app = helper.GetObject("CSI.ETABS.API.ETABSObject")
```

该调用只能取得注册的活动实例，不能按 PID 或模型文件选择。因此它只适合 `src.workflow` 的简化入口或
调用方已经独占 ETABS 实例的场景。

`GetModelFilename(True)` 直接返回字符串，不按 `(ret, value)` 拆包。

## 3. 版本兼容

`etabs_version_info()` 先调用 `SapModel.GetVersion()`；遇 `TypeError` 时改用：

```python
SapModel.GetVersion("", 0.0)
```

这是 ETABS 22 的 pythonnet ref 参数兼容路径，也可兼容需要相同签名的安装。返回对象记录 raw 值、解析出的
主版本以及是否属于 22/23。

## 4. pythonnet `out/ref` 规则

多数 OAPI 方法返回“整数状态码 + out 值”。项目统一使用：

```python
values = api_values(oapi_call(...), "CallName")
```

`api_values()` 要求首值为整数且等于 `0`；否则抛出 `EtabsApiError`。调用者不得只取数组而忽略状态码。

常用签名：

```python
# ret, count, names
sap.FrameObj.GetNameList(0, None)

# ret, count, object_types, object_names
sap.SelectObj.GetSelected(0, None, None)

# ret, orientation；枚举 out 参数使用对应 .NET 枚举
sap.FrameObj.GetDesignOrientation(name, eFrameDesignOrientation.Beam)

# ret, label, story
sap.FrameObj.GetLabelFromName(name, None, None)

# ret, point_i, point_j
sap.FrameObj.GetPoints(name, None, None)

# ret, x, y, z
sap.PointObj.GetCoordCartesian(point_name, 0.0, 0.0, 0.0, "Global")
```

省略 pythonnet 要求的占位参数可能得到“没有匹配的方法”；枚举 ref/out 位置不能用裸整数代替。

## 5. 批量 Frame 快照

`GeometryExtractor` 优先调用：

```python
sap.FrameObj.GetAllFrames(*([0] + [None] * 19 + ["Global"]))
```

去除状态码后应得到 20 个值，其中首值是数量，随后数组包含名称、截面、楼层、I/J 点名和两端全局坐标。
数组长度与数量不一致时适配器失败。Label 通过 `GetLabelNameList(0, None, None, None)` 建立名称映射。

当测试替身或兼容 API 没有 `GetAllFrames` 时，源码回退到逐对象 `GetNameList`、`GetPoints`、`GetSection`
和 `GetCoordCartesian`。业务模块不应绕过 `GeometryExtractor` 重复实现这些调用。

## 6. 选择和筛选

ETABS Frame 的对象类型值为 `2`，Beam 设计方向值为 `2`。`selected` 模式先读取选择集，再保留 Frame 且
通过混凝土梁筛选的对象。截面为空、`None`、非混凝土或名称含 `rigid` 的辅助 Frame 不作为梁输出；
锚定 `rigid` 只参与柱支承传播。

## 7. 单位上下文

OAPI 几何、设计结果和写回计算在 `eUnits.N_mm_C` 下执行：

```python
with connector.temporary_present_units(eUnits.N_mm_C):
    ...
```

上下文记录 `GetPresentUnits()`，调用 `SetPresentUnits()` 并检查返回码，最后在 `finally` 恢复。`src.cli`
在退出单位上下文后才保存 EDB，避免把临时显示单位写入模型。

## 8. 混凝土梁设计结果

旧接口使用：

```python
sap.DesignConcrete.GetSummaryResultsBeam(
    name, 0, *([None] * 14), eItemType.Objects
)
```

Total/Min/Provided 接口使用：

```python
sap.DesignConcrete.GetSummaryResultsBeam_2(
    name, 0, *([None] * 23), eItemType.Objects
)
```

前者经 `api_values()` 后应有 15 个值，后者应有 24 个值。`ret=0` 只表示调用成功；设计 error/warning
属于工程结果，必须保留。字段映射见 [Design Results Reference](api/design-results.md)。

## 9. 写回调用

方向反转使用：

```python
sap.EditFrame.ChangeConnectivity(object_name, new_point_i, new_point_j)
```

这会改变 Object 的 I/J 连接顺序；`SetLocalAxes` 不能替代该事务。名称使用
`FrameObj.ChangeName(old, new)`，并由 `EtabsWriteback` 先改为 GUID 派生的临时唯一名，再改为最终名。

写回快照还使用 `GetGUID`、`GetTransformationMatrix`、截面/材料、释放、端偏移、Group、铰、弹簧、荷载和
混凝土设计覆盖项。可选赋值在“未设置”返回码上保存为 `None`；必需读取失败保留为显式错误，使比较 fail-closed。

所有修改调用都在已审查计划之后执行，并要求 `allow_mutation=True`。写后按当前名称和 GUID 复核；改名后
方向验证必须传名称 resolver。

## 10. 数据源边界

领域层不直接依赖 COM 数组。OAPI 适配器先产生 `BeamGeometry`、`DesignStation`、支承和写回快照，再交给
拓扑与导出层。数据库快照路线的取舍见
[ADR-0003](../decisions/ADR-0003_PUBLIC_OAPI_SQLITE_SNAPSHOT.md)。当前技术 spike 只使用公开
`DatabaseTables.GetAvailableTables` 和 `GetTableForDisplayArray`，把显式请求的原始表写入项目自建
SQLite；Reader 及矩形混凝土 `BeamGeometry` 适配入口见 [Python API Reference](API.md)。

该 spike 不读取 ETABS 私有 SQLite/EDB 内部结构，不调用 `SetTableForEditingArray` 或
`ApplyEditedTables`，也尚未替代支承、设计结果和写回适配器。未知字段、缺表和未支持的混凝土 shape
必须阻断，不能把 ETABS 私有内部文件当成受支持接口。
