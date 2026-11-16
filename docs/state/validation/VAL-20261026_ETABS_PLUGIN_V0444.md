---
id: VAL-20261026-ETABS-PLUGIN-V0444
type: validation
status: partial
owner: project
created: 2026-10-26
updated: 2026-10-26
related:
  - GUIDE-PLUGIN-001
  - SPEC-PLUGIN-001
---

# VAL-20261026 · ETABS 插件 v0.4.4.4 · 最终 auto 包

## 1. 发布结论

最终 r5 完整包的构建/契约/frozen 自检及默认 `auto` 数值验收通过。代表样本请求7、实际6个进程；L2请求7、实际7个进程。原始冻结 R4 输入显式生成 R7，15行和746行三张表及完整逻辑预算均与此前已验收 R7 gold exact。

用户最新已接受此前包内 301.817 秒，并要求默认按 CPU 逻辑单元数减一浮动。**最终包 L2 单次完整生成 268.441 秒**；时间如实列为观测信息，严格300秒不再是本次拒绝发布条件。没有通过改预算、丢失败行或反复择快来满足耗时。

`partial` 只保留实模边界：本轮未安装/加载到 ETABS 菜单，未连接、保存、分析、设计、关闭、写回用户模型，未改个人settings，未自动commit。真实菜单及可处置模型副本成果仍待验证。

## 2. 最终交付

| 项目 | 值 |
|---|---|
| Version / FileVersion / AssemblyVersion | `0.4.4.4` |
| ZIP | `build/etabs-plugin/v0.4.4.4-v2-etabs22-r7-20261026-r5.zip` |
| ZIP大小 / entries | **42,405,249 bytes** / 911 entries |
| DLL | `build/etabs-plugin/v0.4.4.4-v2-etabs22-r7-20261026-r5/BeamGenerator.EtabsPlugin.dll`，95,744 bytes |
| Manifest | `build/etabs-plugin/v0.4.4.4-v2-etabs22-r7-20261026-r5/package-manifest.json` |
| 构建基准 | ETABS22 `22.7.0.4095` / API `2.10.0.0` |
| 目标 / runtime | `net8.0-windows/x64`，64bit Python `3.11.7` frozen |
| Fresh策略 | `drawing_20260924_v1` / `drawing_20261025_etabs_only_independent_skin_v1` |
| 规则资源 | 412 frozen / 380 eligible；包内 `_internal/src/design/policies/drawing_20260924_v1.json` |
| 源码 | commit `3ac02a7d32e27f34de1cfef5ac2770d8e8fa2064`，dirty=`true`，82 source records |
| `ETABSv1.dll` | 不随包复制；完整 `workflow/` 已包含 |

发布manifest保留逐文件hash和构建前后源码一致性门禁，包含policy JSON。最终源码记录锁定后仅写外部验证报告，没有改包文件或SourceRecords。

## 3. 自动并行契约

- portable与example均默认 `rebar_workers: auto`；旧配置缺少worker项也按auto。
- 配置加载解析 `max(1, (os.cpu_count() or 1) - 1)`，存入 `DesignSettings.rebar_workers` 的仍是整数；本机8个逻辑CPU解析为requested7。
- 实际值按CPU cap、可计算梁串jobs和Windows ProcessPoolExecutor的61进程runtime上限取最小值；自动解析值本身不受手填64上限约束。
- 手动整数1..64、CLI `--rebar-workers auto|整数`、CLI覆盖优先级保留；核心函数及DesignSettings直接构造默认仍为1。
- PB/SB共用原池，梁串内部串行；只记录resolved requested/effective整数，不增加数值policy或设置模式对象。

26个不同窄case通过：CPU None/1/2/8/128、缺项/auto、128→127、手动1/4/64与CLI覆盖、非法输入、两个CLI解析类型、核心默认1、Windows effective61。高核数case使用fake executor，没有启动61个进程。

发布中的R7统计微调仅去除重复membership probe，改用已有geometry_hits前后变化。R6、缓存数学/完整键/LRU4096、候选排序、预算及错误不变；PB28和真实5000案例before/after三表**不屏蔽任何字段**exact，全部物理计数也exact，4类窄检查通过。

## 4. 最终包实测

命令使用随包config默认auto，没有workers覆盖；外部TEMP cwd；同一原始冻结R4输入通过显式freshR7及既有API的 `--allow-policy-revision-change` opt-in。

| 输入 | 行数 | requested / effective | 观测fork工作PID数 | 输入读取s | 完整生成s | 导出s | 三表/逻辑预算 |
|---|---:|---:|---:|---:|---:|---:|---|
| representative | 15 | 7 / 6 | 6 | 0.024 | **6.845** | 0.108 | exact PASS |
| L2 | 746 | 7 / 7 | 7 | 0.400 | **268.441** | 1.519 | exact PASS |

生成时钟从JSON读取完成后至引擎初始化、spawn、配筋/协调及redetail返回；输入和导出另计。L2只测一次。`within_300_seconds=true`仅是信息列，`timing_is_release_gate=false`。L2 peak tree RSS `1.015 GiB`、最低可用 `16.515 GiB`，满足<10 / >=4 GiB；原始输入未变。730 OK和16个未OK/待复核状态完整保留。

通过明确 `--multiprocessing-fork` 标记识别实际工作进程，conhost等辅助进程另列；不能把全部direct children当workers。

## 5. 数值、资源和兼容证据

R7最多3排、SMF最大配筋率2.0%、ETABS-only需求/抗剪控制、独立构造侧筋且strength credit=false均保留。geometry4096 LRU及经济纯查询/规则摘要缓存保留，不读历史选筋作缓存。

比较只允许原7项物理counter在明确candidate-search字典内变化、原parallelism四个执行字段及独立rebar_performance；另单列 `metadata.detailing_policy_rules_file` 的源绝对路径→本包资源路径，并由release manifest源/资源相同hash证明内容一致。未整段屏蔽coordination/policy/candidate audit；所有配筋、需求、坐标、状态、错误及budget limit/basis/remaining、search_complete、raw/unique/base/economic候选计数等仍exact。

最终r5自动22契约、自检、源码/资源门禁通过，R7/380/64bit/Python3.11.7。C# DLL与r3的发布manifest hash相同；未变模块沿用此前23契约/API类型装载及legacy自检证据，不重复运行，也不把它称为本轮实模验证。原9项revision/replay边界tests已通过；新flag默认false、仅offline且显式policy有效，跨策略快照gate不变。

## 6. 历史记录与既有边界

此前固定6包r3的单次L2完整生成 **301.817秒**、旧严格300指标false仍按原值保留；用户后来接受该耗时，历史报告没有改成PASS。本轮auto实测单列，不把不同构建归因成隔离的性能对照。

此前源码的六层3,118行/3023 OK/95未OK、L2六进程首次295.294秒和复测298.906秒均为**此前源码验收记录**，不冒充当前包或其他模型/硬件保证。包内本轮仅覆盖代表样本和L2。

直接对已生成R7结果二次重放的旧审计/协调列残留诊断继续保留，原gold FAIL没有改记PASS；源码与binary对同一结果输入可复现。不能把旧4T16审计签名直接解释为当前实际b1变化；本发布不修该投影、不改gold或扩大工程白名单，验证使用原始冻结R4需求输入。

## 7. 证据与升级

- 当前机器报告：`outputs/plugin_release_validation_20261026_v0444_r5/release_validation_report.json`。
- 当前代表样本：`outputs/plugin_release_validation_20261026_v0444_r5/representative_original_R4_to_R7_packaged_auto/report.json`。
- 当前L2：`outputs/plugin_release_validation_20261026_v0444_r5/L2_original_R4_to_R7_packaged_auto/report.json`；stdout/monitor/RSS采样/完整payload同目录保留。
- r3机器报告：`outputs/plugin_release_validation_20261026_v0444_r3/release_validation_report.json`。
- r3正式记录原文：`outputs/plugin_release_validation_20261026_v0444_r3/VAL-20261026_ETABS_PLUGIN_V0444_r3_original.md`。
- 原始失败诊断与r4中间包/窄测试证据保留，没有覆盖。

升级时完整解压最终r5 ZIP并保留workflow结构，在用户自行处理旧DLL加载状态后选择新包 `workflow/BeamGenerator.Workflow/config.yaml`。旧配置明确填写的整数仍尊重，缺worker项则auto；不自动改个人settings。真实ETABS菜单、可处置模型副本工作簿/DXF和环境状态保持仍需实模验收。
