# 当前 API 参考

本页描述项目对外稳定的数据入口；ETABS 的实测调用签名见 [ETABS_OAPI_VALIDATION.md](ETABS_OAPI_VALIDATION.md)。

| 组件 | 主要入口 | 作用 |
|---|---|---|
| `EtabsConnector` | `attach()` | 附着到活动 ETABS 并返回 `SapModel` |
| `EtabsConnector` | `temporary_present_units()` | 临时切换显示单位并在结束时恢复 |
| `GeometryExtractor` | `extract("all" | "selected")` | 返回 `BeamGeometry` 列表 |
| `analyze_beam_lines` | `analyze_beam_lines(beams, column_point_names)` | 按柱/主梁支承识别 `BeamLine`、`BeamSpan` 和异常记录 |
| `ContinuityAnalyzer` | `analyze(beams)` | 返回 `ContinuityGroup` 列表 |
| `EtabsResultProvider` | `get_beam_design(name)` | 返回 `BeamDesignResult` |
| `export_beam_schedule()` | 几何、连续性、设计结果 | 写入 Excel 文件 |
| `workflow.run()` | 配置、可选选择模式 | 执行完整导出流程 |

## 数据模型

- `BeamGeometry`：对象名、标签、楼层、节点、坐标、截面、长度。
- `ContinuityGroup`：组编号、楼层和有序梁对象名。
- `DesignStation`：位置、控制组合、钢筋需求和 ETABS 错误/警告。
- `BeamDesignResult`：某根梁的全部设计站点与最大值摘要。

当前 API 仅保证 `N_mm_C` 导出语义。
