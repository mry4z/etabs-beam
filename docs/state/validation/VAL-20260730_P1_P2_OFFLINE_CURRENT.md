---
id: VAL-20260730-P1-P2-OFFLINE-CURRENT
type: validation
status: passed
owner: project
created: 2026-07-30
updated: 2026-07-30
run_id: local-pytest-20260730-1542
git_commit: 7d3862752041fe18eb0201b21671f3575d70b52b
dirty: true
related:
  - MS-P1-001
  - IP-P1-001
  - IP-P2-001
  - BASELINE-001
supersedes:
  - VAL-20260725-OFFLINE-TESTS
superseded_by:
  - VAL-20260730-P1-ETABS-COVER-25MM-CURRENT
---

# VAL-20260730：P1/P2 当前工作树离线门禁

## 1. Verdict

`PASSED`。当前生产源码和测试通过默认离线门禁；需要已打开 ETABS 且显式 opt-in 的集成测试按配置跳过。
本记录不替代实模 Validation，也不构成 ACI 条文或项目规则的结构工程师签署。

## 2. 身份

| 项目 | 值 |
|---|---|
| 执行时间 | 2026-07-30 15:42 +08:00 |
| Git HEAD | `7d3862752041fe18eb0201b21671f3575d70b52b` |
| 工作树 | `dirty: true` |
| Python | 3.11.7，Windows |
| 同代码实模 content fingerprint | `8f4dbca345bdd7505dc3e364a189d4f46ee67067b924110276ab80a35cb09dec` |
| 关联实模 run | `20260730T072626Z-5101501d` |

实模 manifest 在本次测试前已记录所有 tracked diff 和每个未跟踪文件的内容哈希；其后至本次测试之间
未修改生产源码。随后新增或更新的受治理 Markdown 只记录证据，不扩大本 Validation 的代码证明范围。

## 3. 结果

| 检查 | 结果 |
|---|---:|
| pytest 收集 | 189 |
| 通过 | 188 |
| 跳过 | 1 |
| 失败 | 0 |
| coverage | 80.12% |
| coverage 门槛 | 60% |
| `compileall` | 通过 |
| Black | 通过，60 files unchanged |
| Flake8 | 通过 |
| 两个生产 `.mjs` 的 `node --check` | 通过 |

默认跳过项为需要 `ETABS_INTEGRATION=1`、已打开 ETABS 和匹配 DLL 的集成测试；当前 ETABS 22.7
行为由独立实模 Validation 记录。

## 4. 原始证据

- [pytest.xml](<F:/2026/Research Study/etabs beam/build/output/validation/20260730-p1-p2-final-current/pytest.xml>)（原工作区历史证据），SHA-256
  `063d086ce5f71310d4c1b87494782c99abde36ae2dfedb696bbc6a0a13c61547`
- [coverage.xml](<F:/2026/Research Study/etabs beam/build/output/validation/20260730-p1-p2-final-current/coverage.xml>)（原工作区历史证据），SHA-256
  `63bba69adc17369f9301305f3263b7710a8007406c134994419b90269ceecb91`

## 5. 覆盖边界

- P1：cover/骨料语义、设计结果状态与来源、逐区剪扭控制、PB/SB profile、最终 `db`、相邻跨协调、
  SB 全跨统一箍筋、payload 2.0、工作簿 fail-closed 和 manifest 内容指纹。
- P2 spike：公开 OAPI 表解析、SQLite 原子发布、严格 Reader、字段漂移、缺表和矩形梁领域重建。
- 不包含：结构工程签署、未确认项目输入、CAD/插件/GUI、完整 SQLite 生产数据源和发布验收。
