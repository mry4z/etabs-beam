---
id: VAL-20260730-P2-OAPI-SQLITE-WORKFLOW-PARITY
type: validation
status: passed
owner: project
created: 2026-07-30
updated: 2026-07-30
run_id: 20260730T095035Z-d1abb748;20260730T103252Z-d96440dc
git_commit: 7d3862752041fe18eb0201b21671f3575d70b52b
dirty: true
related:
  - ADR-0003
  - IP-P2-002
  - TASK-P2-SQLITE-DOMAIN-001
  - TASK-P2-SQLITE-001
  - VAL-20260730-P1-ETABS-COVER-25MM-CURRENT
  - BASELINE-001
supersedes:
  - VAL-20260730-P2-SQLITE-DOMAIN-CURRENT
superseded_by:
---

# VAL-20260730：P2 OAPI / SQLite 只读工作流等价

## 1. Verdict

`PASSED`，范围为 ETABS 22.7 / ACI 318-14 矩形混凝土梁只读 profile。项目 SQLite v5 使用
`.17g` 全精度公共 OAPI 几何、支承和设计结果表，通过与实时 OAPI 相同的 `ReadModelSource`
协议完成拓扑、基础 Excel、主配筋 payload/工作簿和独立 SB 工作簿。

实时 OAPI 与 SQLite 的 3,018 个 raw/review/final 配筋行逐字段完全一致；六个权威拓扑
JSON/CSV 成果逐字节一致。该结论不扩展到非矩形截面、ETABS 23、CAD 正式 schema 或 bundle 生命周期。

## 2. 快照身份

| 项目 | 值 |
|---|---|
| SQLite | `etabs22_v645_domain_v5.sqlite` |
| 大小 | `254,771,200` bytes |
| SHA-256 | `270f30cf6bc564d5dc268d2674f08f72e5d255d9bb2cdece7c28a2bb11306a12` |
| profile | `etabs-aci31814-rectangular-concrete-beam 1.1` |
| snapshot / raw / domain schema | `1.0` / `raw-oapi-table/1.0` / `beam-domain/2.0` |
| 源 EDB SHA-256 | `20347ccc0e3960ef10c91b107776b19dc1676648c8b621b79f8925202aff9c41` |
| ETABS / API | `22.7.0` / `2.10.0.0` |

[快照](<F:/2026/Research Study/etabs beam/build/output/p2_sqlite_domain_20260730/etabs22_v645_domain_v5.sqlite>)（原工作区历史证据）；
[manifest](<F:/2026/Research Study/etabs beam/build/output/p2_sqlite_domain_20260730/etabs22_v645_domain_v5.sqlite.manifest.json>)（原工作区历史证据），
manifest SHA-256 `9b2191a0ebb51fdf41a1fe73de7cdd5b57293da13bbfb482f62bf35376658170`。

v5 含 15 张表：4,094 条精确梁几何、1,441 个精确支承、4,094 个 FrameState、
44 个截面信息和 65,881 个设计 station；原始 `DatabaseTables` 行继续保留作来源审计。

## 3. 两次工作流

| 项目 | SQLite 离线 | 实时 OAPI |
|---|---|---|
| Run ID | `20260730T095035Z-d1abb748` | `20260730T103252Z-d96440dc` |
| 状态 | `SUCCESS` | `SUCCESS` |
| 模式 | `read-only-snapshot` | `read-only`，PID `60856` |
| Beam / Line / Span | 4,094 / 1,215 / 3,018 | 4,094 / 1,215 / 3,018 |
| PB / SB | 1,574 / 1,444 | 1,574 / 1,444 |
| 阻断级拓扑诊断 | 0 | 0 |
| 主/SB 公式错误扫描 | 0 / 0 | 0 / 0 |

- [SQLite run manifest](<F:/2026/Research Study/etabs beam/build/output/p2_sqlite_workflow_20260730/offline_v5_accepted/run_manifest_20260730T095035Z-d1abb748.json>)（原工作区历史证据），
  SHA-256 `8f7e8479175c4ab5f1656f2911572985300f0cbabbbe827f45325d9c055ae2cf`。
- [OAPI run manifest](<F:/2026/Research Study/etabs beam/build/output/p1_v645_etabs_cover_25mm_20260730/live_readonly_retry_accepted/run_manifest_20260730T103252Z-d96440dc.json>)（原工作区历史证据），
  SHA-256 `9153866a3b18d25e2b7f7d0c434ac5d9a465f347d41fbf71d489078ecc987fcd`。

离线 run 后仅增加了预览瞬时原生崩溃重试；领域读取、拓扑、payload 和工作簿内容逻辑未变。当前代码身份
由 OAPI run 的 content SHA-256
`2f9990f0b475023a248939b2dfd524f33573dd20fbf5d4ab68125a246a70d8fc` 与 208 项离线门禁证明。

## 4. 精确等价

以下成果的 SHA-256 在 OAPI / SQLite 之间完全相同：

| 成果 | SHA-256 |
|---|---|
| `topology_spans.csv` | `5f9d4ede5bd4b12bd7aff6b0dcb3a1ba924f3c904de2ad2a962ad133a30aef15` |
| `topology_diagnostics.json` | `973e8f82040c8fbc0ea977695f48f3d57001eeee2ac79362832a1aea2d53b1ab` |
| `requires_split_audit.csv` | `0b26bab1fd56048a862c1d933cdc064a13ee426722e87625a9e76ef499851a72` |
| `requires_split_audit.json` | `71631f904ec84db5178bfff0c20191c41b6e85cc3b72872960521f9f1b8d01ca` |
| `multi_section_span_review.csv` | `978d0ad8b1f24c2983cfcbb334664a099d42516e72988707a9693a32f2684596` |
| `multi_section_span_review.json` | `b03032174ac5b06cb23eabc4812a74f17972524cff485ab103015b120d3b6e05` |

主 payload 的 `raw_rows`、`review_rows`、`final_rows` 分别逐字段一致；SB payload 的
`schedule_rows`、`exception_rows` 逐字段一致。两套 payload 唯一 metadata 差异是模型标签：
实时宿主返回打开的 `.$et`，SQLite 记录 durable `.EDB`；两者由同一个 EDB 路径和 SHA-256 绑定。

两个 DXF 的几何和图元一致，文本图例只在上述 `MODEL ... .$et` / `MODEL ... .EDB` 标签处不同；
这不是领域数据差异。

## 5. 安全边界

- SQLite 路径不附着 ETABS，不保存、解锁、分析、设计或写回模型。
- OAPI 对照 run 为只读；结束后 ETABS 仍响应、`.$et` 锁文件仍存在，EDB SHA-256 未变。
- Reader 在开始、工作簿前后检查 snapshot 自身和 durable EDB 身份。
- v5 精确表保留公共 `GetAllFrames` 输入顺序，因此编号、分组和 payload 顺序与实时 OAPI 一致。

## 6. 剩余 P2 Gate

1. 获得 Concrete Tee / General 等真实非矩形梁样本并冻结公开字段映射。
2. 在 ETABS 23 当前版本重跑相同 profile 和全工作流等价门禁。
3. 等待 `TASK-CAD-001` 冻结正式 CAD schema 后建立共享版本与兼容测试。
4. 完成约 255 MB bundle 的命名、归档、清理、主动撤销和升级/回退策略。

因此 `TASK-P2-SQLITE-001` 保持 `in_progress`；本记录只关闭 ETABS 22 矩形梁只读工作流切换 Gate。

