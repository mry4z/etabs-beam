# ETABS Beam Generator

**A comprehensive Python tool for automated ETABS beam design, analysis, and reporting.**

## 快速导航

| 角色 | 查看文档 |
|------|---------|
| 🎓 课题申报 / 初了解 | [`TECHNICAL_ROADMAP.md`](docs/TECHNICAL_ROADMAP.md) — 技术方案与实现路线 |
| 👨‍💻 开发者 / 贡献代码 | [`DEVELOPMENT.md`](docs/DEVELOPMENT.md) + [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md) |
| 📚 API 参考 / 环境排查 | [`API.md`](docs/API.md) + [`ETABS_OAPI_VALIDATION.md`](docs/ETABS_OAPI_VALIDATION.md) |
| 👤 最终用户 / 使用工具 | [`USER_GUIDE.md`](docs/USER_GUIDE.md) |
| 📊 视频分析（本地研究参考）| [`extracted_frames/`](extracted_frames/) — 历史逆向资料，不纳入当前 Git 仓库 |

---

## 项目概述

本项目是对商业工具 **Quick Beam Design (P&T Group)** 的技术复现与开源实现。基于 ETABS 22 OAPI，提供：

✅ **自动梁选择与几何提取** — 从 ETABS 模型读取梁的坐标、楼层、端点  
✅ **连续梁链识别** — 按共点、共线、同楼层判据自动分组连续梁  
✅ **设计结果读取** — 调用 ETABS 内置混凝土设计 API，无须自研配筋公式  
✅ **详细梁表导出** — 生成标准化 Excel 梁表，并可附带按 Beam Line/Span 编号的“梁分跨”工作表（几何、连续性、纵筋、剪力筋、扭转筋及 ETABS 校核信息）
✅ **远景扩展** — 预留 ACI 318-14/318-19 自研配筋接口（后续落地）

**适用场景**：多层建筑梁系统快速设计、自动化报告生成、设计参数批量提取。

---

## 技术栈

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | 3.7+ | 核心语言 |
| pythonnet | 最新 | 加载 ETABS v22 DLL（推荐）/ comtypes 作备选 |
| ETABS 22 | 22.x | `ETABSv1.dll` + `CSI API ETABS v1.chm` |
| pandas | 1.0+ | 数据处理、Excel 导出 |
| openpyxl | 3.0+ | Excel 工作簿操作 |

## 系统要求

- **OS**：Windows 7 或更高版本
- **Python**：3.7+（推荐 3.10+）
- **ETABS**：v22 及以上（必须 x64）
- **内存**：≥4GB
- **磁盘**：≥1GB（含 DLL）

---

## 快速开始

### 1. 克隆或下载项目
```bash
git clone <repository-url>
cd "etabs beam"
```

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 配置环境
```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml，确保 ETABS DLL 路径正确
# 默认: E:\Program Files\Computers and Structures\ETABS 22\ETABSv1.dll
```

### 4. 运行工作流
```bash
python -m src.workflow --config config.yaml
```

也可显式指定梁范围：

```bash
# 导出模型中所有设计方向为 Beam 的对象
python -m src.workflow --selection all

# 仅导出当前 ETABS 选择集中的梁
python -m src.workflow --selection selected
```

**预期结果**：
- ETABS 窗口打开并 attach
- 控制台与日志输出实际附着的模型路径
- 自动读取所有梁，或当前选择集中的梁
- 导出期间固定使用 `N_mm_C`，完成后恢复 ETABS 原始当前单位
- 生成梁表 Excel 文件：`build/output/reports/beam_schedule_[时间戳].xlsx`

**注意**：ETABS、Python 与运行终端须为 x64，且应以相同权限级别运行。多实例时，工具只能附着到当前注册的活动实例；运行前务必确认控制台输出的模型路径。详见 [`ETABS_OAPI_VALIDATION.md`](docs/ETABS_OAPI_VALIDATION.md)。

---

## 项目结构

详见 [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md)。简述：

```
src/
├── core/                    # 连接、提取、链分析（阶段1-3）
├── design/                  # 设计策略接口与两实现（阶段4-5）
├── utils/                   # 工具函数
└── workflow.py + excel_export.py  # 编排与输出（阶段6）

tests/                        # 单元测试；可选 ETABS 集成测试
docs/                         # 技术路线与 OAPI 实测记录
build/output/                 # 生成的梁表、日志
extracted_frames/             # 视频分析资料（研究阶段）
```

---

## 实现路线

遵循 6 个开发阶段（详见 [`TECHNICAL_ROADMAP.md`](docs/TECHNICAL_ROADMAP.md)）：

| 阶段 | 模块 | 交付物 | 验证点 |
|------|------|--------|--------|
| 0 | 环境基线 | 配置管理、依赖 | 已验证 pythonnet + ETABS DLL |
| 1 | EtabsConnector | pythonnet attach | 已附着 `bm.EDB` 并读取模型路径 |
| 2 | GeometryExtractor | 梁选择、坐标提取 | 已读取 96 根梁的几何数据 |
| 3 | ContinuityAnalyzer | 连续梁链识别 | 已识别 32 个连续梁组 |
| 4 | EtabsResultProvider | 设计结果读取 | 已读取 5,568 个设计站点 |
| 5 | IDesignProvider 接口 | 策略可切换架构 | 已实现 ETABS 结果接口；ACI 为远景占位 |
| 6 | ExcelExport + Workflow | 梁表导出、流程编排 | 已导出完整 Excel 梁表 |

本次真实模型验证和 API 调用细节见 [`ETABS_OAPI_VALIDATION.md`](docs/ETABS_OAPI_VALIDATION.md)。

---

## 开发指南

### 本地运行测试
```bash
# 单元测试
pytest tests/unit/ -v

# 运行工作流（需要 ETABS 已打开）
python -m src.workflow --selection all

# 覆盖率报告
pytest --cov=src tests/
```

### 代码规范
- 遵循 [PEP 8](https://pep8.org/)
- 使用类型提示（Python 3.7+ `typing` 模块）
- 必须编写单元测试
- 提交前运行 `black` 格式化、`flake8` 检查

### 提交规范
遵循 [Conventional Commits](https://www.conventionalcommits.org/)：
```bash
git commit -m "feat(connector): implement pythonnet attach to ETABS 22"
```

详见 [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md) 中的"提交规范"章节。

---

## 常见问题

### Q: pythonnet 加载 DLL 失败？
**A**: 
1. 确保已运行 `pip install pythonnet`
2. ETABS 版本必须是 x64
3. DLL 路径正确（`E:\Program Files\Computers and Structures\ETABS 22\ETABSv1.dll`）
4. 若仍失败，检查 .NET Framework 版本（pythonnet 需要 .NET Framework 4.7.2+）

### Q: 工具附着到了错误模型？
**A**: 关闭其他 ETABS 实例，仅保留目标模型；ETABS 与运行 Python 的终端保持相同权限级别。每次运行都核对控制台输出的模型路径。详见 [`ETABS_OAPI_VALIDATION.md`](docs/ETABS_OAPI_VALIDATION.md)。

### Q: 需要自己写配筋公式吗？
**A**: **当前 MVP 不需要**。工具直接读取 ETABS 的设计结果（`DesignConcrete.GetSummaryResultsBeam`）。  
**远景**：可选实现 ACI 318-14/318-19 自研配筋（已预留 `ACI318Provider` 接口），但不是阶段1 范围。

### Q: 如何扩展支持其他设计规范？
**A**: 实现 `IDesignProvider` 接口（见 `src/design/provider.py`），创建新类如 `GB50010Provider`。无须改动其他模块。

---

## 贡献与反馈

欢迎贡献代码、报告 bug、提议功能。流程：

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 编写代码并添加测试
4. 提交 PR，描述变更内容

---

## 许可证

[待定] — 根据课题申报要求确认开源许可

---

## 参考资源

| 资源 | 链接 |
|------|------|
| ETABS 22 OAPI 文档 | `E:\Program Files\Computers and Structures\ETABS 22\CSI API ETABS v1.chm` |
| pythonnet 文档 | https://github.com/pythonnet/pythonnet |
| Pandas 官方文档 | https://pandas.pydata.org/ |
| ACI 318-19 标准 | [American Concrete Institute](https://www.concrete.org/) |

---

## 项目信息

- **当前版本**：0.1-alpha（MVP 已实现，持续完善中）
- **开发阶段**：阶段 0–6 核心链路已实现并完成真实模型验证
- **最新验证**：`bm.EDB`，96 根梁、32 个连续梁组、5,568 个设计站点
- **最后更新**：2026-07-12
- **维护者**：项目核心团队

**[快速链接]**  
📋 [技术路线图](docs/TECHNICAL_ROADMAP.md) | 
🏗️ [项目结构](PROJECT_STRUCTURE.md) | 
✅ [开发进度](PROJECT_COMPLETION_CHECKLIST.md) | 
📚 [OAPI 实测记录](docs/ETABS_OAPI_VALIDATION.md)
