---
id: VAL-20260725-OFFLINE-TESTS
type: validation
status: passed
owner: project
created: 2026-07-25
updated: 2026-07-25
run_id: local-pytest-20260725-1138
git_commit: 7d3862752041fe18eb0201b21671f3575d70b52b
dirty: true
related:
  - BASELINE-001
  - MS-P1-001
supersedes: []
superseded_by:
---

# VAL-20260725：当前工作树离线测试

## 1. 身份

- 执行时间：2026-07-25 11:38 +08:00。
- 命令：`python -B -m pytest -q --junitxml=... --cov-report=term --cov-report=xml:...`。
- Git HEAD：`7d3862752041fe18eb0201b21671f3575d70b52b`。
- 工作树：`dirty: true`。
- Python：3.11.7，Windows。

测试时生产代码与测试 tracked diff SHA-256：`83226c9b75d84f4735223db90fefbe56a300c1d0541ab27d5058c876cb6261d1`。

未跟踪生产文件 `src/spreadsheet/build_secondary_beam_workbook.mjs` SHA-256：`effc5f84b21b35eebcfb2bb25cc3076aba5c02a0f3d6096cfdaf7a203f3ce621`。

## 2. 结果

| 项目 | 结果 |
|---|---:|
| 收集测试 | 163 |
| 通过 | 162 |
| 跳过 | 1 |
| 失败 | 0 |
| 覆盖率 | 78.89% |
| 门槛 | 60% |

默认跳过项是需要已打开 ETABS 且显式设置 `ETABS_INTEGRATION=1` 的集成测试。

## 3. 原始证据

- [pytest.xml](<F:/2026/Research Study/etabs beam/build/output/validation/20260725-doc-restructure/pytest.xml>)（原工作区历史证据），SHA-256 `fa58a453af5b2c68d9ac4da2d83158d9c8776c6374088f46fa6be6b538d8d851`
- [coverage.xml](<F:/2026/Research Study/etabs beam/build/output/validation/20260725-doc-restructure/coverage.xml>)（原工作区历史证据），SHA-256 `080fe94b5a18a11d0ff1576f04f2d9f88a5a7faa250ad2cf09786d3bf2bcf348`

## 4. 限制

- 本记录不包含 ETABS 实例、模型、OAPI、分析、设计、写回或工作簿实模验证。
- 工作树包含未提交代码；只有上述 diff 和未跟踪文件哈希能标识本次被测代码。
- 测试后新增的纯文档和备份文件不改变代码证明范围；生产代码发生任何变化时本记录失效。

## 5. Verdict

`PASSED`，范围仅为当前被标识代码的默认离线测试和覆盖率门槛。
