# ETABS Beam Generator 开发指南

## 本地环境

```powershell
python -m pip install -r requirements.txt
python -m pytest tests/unit -q
```

ETABS 集成测试不会默认运行。打开目标模型后，以与 ETABS 相同权限级别执行：

```powershell
$env:ETABS_INTEGRATION='1'
python -m pytest tests/integration -q
```

## 代码边界

- `core/`：仅负责 ETABS 连接、几何提取和连续性分析；
- `design/`：将 ETABS 原始设计数组映射为稳定数据模型；
- `excel_export.py`：只消费数据模型，不直接访问 ETABS；
- `workflow.py`：编排流程，不堆积业务算法；
- `utils/`：可复用的日志、文件路径和纯计算工具。

## 单位约束

当前 MVP 固定在 `N_mm_C` 下导出。工作流使用 `temporary_present_units()` 在 `try/finally` 中恢复 ETABS 原单位。任何新增 OAPI 读取方法都必须在这一上下文内执行，或明确说明其单位来源。

## 验证要求

新增代码至少应包含以下一项：

- 不依赖 ETABS 的单元测试；
- 需 ETABS 的 opt-in 集成测试；
- 对 `docs/ETABS_OAPI_VALIDATION.md` 的调用签名和实测记录更新。

提交前运行：

```powershell
python -m pytest tests -q
python -m compileall -q src
python -m black --check src tests
python -m flake8 src tests
```

当前离线覆盖率门槛为 60%（首个可执行基线）；后续应在补足 ETABS 适配层的 mock 测试后逐步提高。

## Git

历史视频、帧图、逆向资料和生成报告均被忽略。首个基线提交只应包含源码、测试、主文档、`config.example.yaml`、`requirements.txt` 与 `.gitignore`。
