---
id: VAL-20260908-DRAWING-EXPORT-REVIEW
type: validation
status: partial
owner: project
created: 2026-09-08
updated: 2026-09-09
related:
  - SPEC-DRAW-001
  - ARCH-001
  - TASK-DRAW-001
  - TASK-CAD-001
  - BASELINE-001
supersedes: []
superseded_by:
evidence:
  - "src/drawing/dxf_export.py"
  - "tests/unit/test_dxf_export.py"
  - "tests/unit/test_beam_line_span_analyzer.py"
  - "docs/specs/SPEC-DRAW-001_NUMBERING_INTERCHANGE.md"
---

# VAL-20260908：梁图导出顺序、编号与文字可读性审查

## 身份与范围

- 工作目录：`F:\2026\Research Study\etabs beam-v2`，分支 `codex/clean-start`。
- HEAD：`59a40b26f99b5dac35fdc7159a01c164e3c3f075`；工作树 dirty，前序并行修改保留。
- 本记录只覆盖 `src/drawing/dxf_export.py`、受影响单元测试和文档治理同步；未连接 ETABS、未读取或写回模型、未截断模型、未重新打包。

## 修改与结果

1. `export_beam_groups_dxf` 与 `export_beam_groups_csv` 现在共享同一分类、既有 DXF 空间排序和 `STORY-PB/SB<n>-S1` 编号结果；输入乱序、对象名称排序与空间排序相反时，编号、源对象和路径坐标仍一一对应。
2. `export_structural_spans_dxf/csv` 无自定义 resolver 时调用现有规范编号函数，默认交付编号不含 `S`；`BeamSpan.span_id` 保留为内部标识，自定义 resolver 继续优先。
3. DXF 文字角度只用于显示可读性；向西、向下和折线路径的实际导出测试解析 `TEXT` 角度与 `POLYLINE/VERTEX` 顺序，确认 180° 可读性翻转不改变输入路径顶点顺序。
4. `SPEC-DRAW-001` 已将文字显示角度与结构 I→J 分离；模型截断、I/J 协调、持久稳定编号、正式 CAD JSON 和完整出图门禁保留到 `TASK-CAD-001`。

## 验证命令与结果

```text
python -m pytest -q --no-cov -p no:cacheprovider tests/unit/test_dxf_export.py tests/unit/test_beam_line_span_analyzer.py
```

结果：`28 passed`。

测试覆盖：

- DXF/CSV 共享空间排序、编号、源对象和坐标；乱序输入结果稳定；
- 向西、向下、折线文字的实际 DXF 角度及 `VERTEX` 顺序；
- 结构跨默认交付编号无 `S`，自定义 resolver 对 DXF/CSV 均生效；
- 原有 merge comparison、单 polyline、PB 颜色和结构跨导出回归。

## 文档链接审计

治理审计原先报告的 31 个本地缺失链接全部定位到原工作区已有的历史成果目录，包括 `manifests`、`validation`、
`p0_complete_v643_20260722_final3`、`p1_v645_etabs_cover_25mm_20260730`、`p1_v645_final_20260730`、
`p2_sqlite_domain_20260730` 和相关快照目录。链接已改为指向
`F:/2026/Research Study/etabs beam/build/output/...` 的原工作区路径，并在历史 Validation 文档中明确标注；
没有复制、修改旧目录，也没有把这些历史文件解释为当前 v2 验收证据。

审计命令：

```text
python C:\Users\zci8\.codex\skills\govern-development-docs\scripts\audit_docs.py . --format markdown --check
```

结果：81 个 Markdown、0 个 broken local links、0 个 errors；剩余 8 个 warnings 和 72 个 advisories 为既有治理提示，
不属于本轮新增失效链接。

## 2026-09-09 文档复核收尾

- 复核并同步 ARCH-001 的 PB/SB 单表静态 Excel + JSON 审计数据流、当前 renderer 投影职责和插件受控状态职责。
- 复核并同步 SPEC-DRAW-001 的正式出图阻断条件，以及“文字正向可读、顶点顺序保持”的验收表述。
- 复核并隔离 Current Baseline、INDEX 和 TASK_REGISTER 中的历史三表/v0.3 插件证据；当前插件能力改以 v2 源码表述，已有 v0.4.1 包记录但本轮不重建、不进行实模验收。
- 前序 `28 passed` 定向测试结果保持有效，本轮未重复运行测试；未连接 ETABS、未修改旧工作区、未打包、未提交。
- 本轮唯一最终文档审计命令及结果记录如下：

  ```text
  python C:\Users\zci8\.codex\skills\govern-development-docs\scripts\audit_docs.py . --format markdown --check
  ```

  结果：81 个 Markdown、76 个带 frontmatter；0 个 open checklist items、0 个 broken local links、0 个 errors；8 个 warnings、72 个 advisories。
  warnings/advisories 为既有治理提示；本记录不把文档审计替代代码或实模验收。

## 排除项与限制

- 本记录不证明模型截断、I/J 方向协调、持久稳定编号映射、正式 CAD JSON schema、CAD 消费兼容或完整施工图门禁。
- 本记录不证明 ETABS 实模、菜单加载、选择集恢复或 PB/SB 工作簿工程签发；这些事实分别由 Current Baseline 和对应 Validation 记录管理。
- 未运行全仓测试；本轮按受影响导出路径执行定向测试。

## Verdict

`partial`：本轮梁图导出整改及定向离线测试通过；正式 CAD 交换和实模/工程门禁仍待后续任务。
