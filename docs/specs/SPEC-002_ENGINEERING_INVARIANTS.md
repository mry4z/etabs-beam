---
id: SPEC-002
type: spec
status: accepted
owner: project
created: 2026-07-25
updated: 2026-07-25
related:
  - SPEC-001
  - ADR-0001
  - ADR-0002
supersedes: []
superseded_by:
---

# SPEC-002：工程不变量与安全边界

## 1. 不变量

| ID | 要求 |
|---|---|
| ENG-R001 | OAPI 几何、设计结果和写回计算统一在 `N_mm_C` 上下文执行；退出时通过 `try/finally` 恢复原当前单位。 |
| ENG-R002 | Python、ETABS 和 OAPI DLL 必须同为 x64；DLL 主版本与当前宿主一致。 |
| ENG-R003 | 连接必须同时核对 PID 和 `GetModelFilename()` 绝对路径；多实例下“连接成功”不足以证明目标正确。 |
| ENG-R004 | Object GUID 是内部稳定身份；Label、Unique Name 和顺序均是可变投影。 |
| ENG-R005 | 原始 EDB 不得直接写回。工作副本创建不得覆盖已有文件，源/副本均记录 SHA-256。 |
| ENG-R006 | 方向整理和改名是独立事务；改名采用当前名到临时唯一名再到最终名的两阶段流程。 |
| ENG-R007 | 分支、环、重合、支承冲突、跨 Span Object、未确认多截面和未知设计类型必须显式诊断并默认阻塞。 |
| ENG-R008 | 写回后按 GUID 复核对象数量、I/J、变换矩阵、截面/材料、覆盖、释放、偏移、荷载、Group、铰、弹簧和设计覆盖项。 |
| ENG-R009 | 拓扑、方向和编号对源 Object I/J 与名称变化保持确定性；相同输入重复运行应产生相同结果。 |
| ENG-R010 | 生产代码和测试进入受跟踪源码目录；`build/`、`outputs/` 和模型产物不得成为唯一生产实现。 |
| ENG-R011 | 当前能力声明必须绑定 commit、dirty state、模型、配置、算法和 evidence；不得按文件时间选择“最新”成果。 |
| ENG-R012 | API error、空设计数组、未知字段、失效 override 和不支持规范不得被转换成零值或默认成功。 |

## 2. 失败策略

严重度统一为：

- `BLOCKED`：不得继续生成权威编号、写回或正式成果。
- `REVIEW_REQUIRED`：需要可追溯人工决定，决定前与 `BLOCKED` 等价。
- `WARNING`：结果可继续，但限制必须进入 Validation/manifest。
- `INFO`：可审计说明，不改变 verdict。

显式 `allow_partial_writeback` 只能执行已经证明安全的动作；它不能改变被阻塞对象的状态，也不能把结果标记为全量成功。

## 3. 证据不变量

1. 单元测试不证明 ETABS 实模行为。
2. 实模 run 不证明不同 commit 或 dirty diff。
3. 不同 run 的写回、需求等价、重开和工作簿不得无说明拼接。
4. manifest 中的排除项和部分运行必须在 Validation verdict 中可见。
5. 模型、算法、配置或代码变化后，旧成果只能作为历史证据。

## 4. 验收

- 安全边界有单元测试和至少一条精确实模 Validation Record。
- 写回失败路径能够回滚；回滚失败使副本标记不可用。
- 当前 Baseline 不引用失效或不同版本证据作为当前证明。
