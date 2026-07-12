# ETABS Beam Generator 用户指南

## 使用前准备

1. 使用 x64 Python、x64 ETABS，并确保两者以相同权限级别运行。
2. 在 ETABS 中打开模型；若同时开了多个实例，只保留目标模型。
3. 模型应已完成分析与混凝土设计。
4. 按需复制 `config.example.yaml` 为本机 `config.yaml`，设置正确的 `etabs.dll_path`。

## 导出梁表

```powershell
# 导出所有设计方向为 Beam 的框架对象
python -m src.workflow --selection all

# 仅导出 ETABS 当前选择集中的梁
python -m src.workflow --selection selected
```

运行时会打印附着的模型路径。若路径不是目标模型，关闭其他 ETABS 实例后重试。

## 单位行为

工具会记录 ETABS 原始当前单位，临时切换为 `N_mm_C` 后读取和导出，结束时自动恢复原单位。它不重新分析、设计或修改构件物理数据。

## 输出文件

报告输出至 `build/output/reports/`，包含：

- `梁表`：梁几何、截面、连续性和设计结果摘要；
- `设计站点`：每个站点的设计组合、纵筋、剪力筋、扭转筋及 ETABS 错误/警告；
- `梁表`：顶部弯矩纵筋、底部弯矩纵筋和抗剪筋字段分别展开为左端 `2D`、跨中、右端 `2D` 三列；`D` 为梁高；
- `导出说明`：单位、数据来源和字段解释。

日志输出至 `build/output/logs/`。

## 常见问题

| 现象 | 处理 |
|---|---|
| 未找到 ETABS 实例 | 确认 ETABS 已打开；检查 Python 与 ETABS 权限级别一致。 |
| 附着到错误模型 | 关闭其他实例，核对控制台中的模型路径。 |
| 没有设计结果 | 在 ETABS 内完成分析和混凝土设计，再重新导出。 |
| 当前选择没有梁 | 改用 `--selection all`，或在 ETABS 中仅选择梁。 |

API 兼容性与验证详情见 [ETABS_OAPI_VALIDATION.md](ETABS_OAPI_VALIDATION.md)。
