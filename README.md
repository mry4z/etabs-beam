---
id: REF-PROJECT-README
type: reference
status: current
owner: project
created: 2026-07-10
updated: 2026-07-25
related:
  - REF-DOCS-INDEX
  - SPEC-002
supersedes: []
superseded_by:
---

# ETABS Beam Generator

面向 ETABS 混凝土梁的几何整理、结构分跨、编号写回、设计结果读取、配筋排布及 CAD/Excel 交付工具。

## 最短快速开始

在 Windows、Python 3.11 和已安装 ETABS 22/23 的环境中：

    python -m pip install -r requirements.txt
    Copy-Item config.example.yaml config.yaml
    python -m src.workflow --config config.yaml --selection all

运行前须在 config.yaml 中配置与 ETABS 主版本一致的 ETABSv1.dll。

## 文档

全部规范、架构、决策、当前基线、任务、指南和验证记录统一从
[开发文档索引](docs/INDEX.md)进入。

当前能力和限制以 [Current Baseline](docs/state/CURRENT_BASELINE.md) 为准，不在 README 重复维护。

## 安全边界

模型操作必须遵守[工程不变量与安全边界](docs/specs/SPEC-002_ENGINEERING_INVARIANTS.md)：
默认只读，写回仅作用于已校验的工作副本，连接时核对 PID 与模型绝对路径，任何不明确输入均 fail-closed。
实际运行步骤见[用户指南](docs/guides/USER_GUIDE.md)。
