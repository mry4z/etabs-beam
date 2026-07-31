---
id: SPEC-001
type: spec
status: accepted
owner: project
created: 2026-07-25
updated: 2026-07-25
related:
  - ARCH-001
  - SPEC-002
supersedes:
  - LEGACY-PROCESS-CONTROL
superseded_by:
---

# SPEC-001：产品范围

## 1. 问题与理由

项目需要从大型 ETABS 模型中稳定提取混凝土梁，建立 Beam Line/Span，统一方向和编号，读取设计结果，生成可审查的 Excel/DXF/中间数据，并在受控工作副本上执行必要的方向和名称写回。

## 2. 目标

| ID | 要求 |
|---|---|
| PROD-R001 | 当前产品形态为运行于 ETABS 进程外的 Python 自动化工具，通过公开 OAPI 连接已经打开的 ETABS。 |
| PROD-R002 | 当前正式宿主范围为 ETABS 22.7 和 23.3 x64；加载的 `ETABSv1.dll` 主版本必须与宿主一致。 |
| PROD-R003 | 工具必须支持只读拓扑/设计结果导出，以及在工作副本上的显式写回事务。 |
| PROD-R004 | 核心交付包括 Beam Line/Span、稳定编号映射、DXF/CSV/JSON、基础 Excel、三表配筋工作簿、诊断和 run manifest。 |
| PROD-R005 | 当前配筋策略只支持 ACI 318-14；其他规范必须由独立策略接入，不能复用当前常数。 |
| PROD-R006 | 自动流程必须优先保证模型安全、可追溯和 fail-closed；无法唯一判断时宁可阻塞，不得猜测。 |
| PROD-R007 | 用户必须能从输出追溯到源 EDB、Object GUID、模型/成果哈希、ETABS/API、配置、算法和代码状态。 |

## 3. 非目标

- 当前阶段不支持 SAP2000。
- GUI、ETABS 进程内插件、CAD 插件、CI/CD 和打包属于后续里程碑。
- 项目不替代 ETABS 结构分析/设计，也不替代注册结构工程师对最终施工图的审查。
- SQLite 快照是计划中的离线数据源，不替代 EDB，不承担模型修改。

## 4. 用户工作流

1. 用户打开并确认目标 ETABS 模型。
2. 工具核对 PID、绝对模型路径、宿主/API 和单位。
3. 默认只读生成拓扑、诊断和导出结果。
4. 如需写回，先创建不可覆盖的工作副本并生成计划。
5. 用户明确允许后执行方向/两阶段改名、保存、重开、分析设计和验证。
6. 运行生成 manifest；Current Baseline 只引用与声明版本一致的 Validation Record。

## 5. 验收标准

- 只读模式不会改变模型对象或保存 EDB。
- 写回只在已确认的工作副本上发生，并有回滚、重开和属性审计。
- 每个合格源 Object 被覆盖；无法安全命名的对象出现在明确阻塞清单。
- 输出共享同一 Beam Line/Span、方向、编号和设计结果语义。
- 每次正式运行产生带哈希和限制的 manifest。

相关安全要求见 [SPEC-002](SPEC-002_ENGINEERING_INVARIANTS.md)，当前事实见 [Current Baseline](../state/CURRENT_BASELINE.md)。
