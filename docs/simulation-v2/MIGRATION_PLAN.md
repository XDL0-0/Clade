# Migration Plan — 增量迁移与验收

基线 4d1045bfecd412aa26dd55e506c8416b83ac984b；本次只交付 Audit + Design。没有替换运行引擎，也未宣称完成 P0。问题证据见 [ARCHITECTURE_PROBLEMS.md](ARCHITECTURE_PROBLEMS.md)，实测基线见 [AUDIT_BASELINE.md](AUDIT_BASELINE.md)。

## 1. 先解除基线阻塞

现有全量测试/lint/typecheck 不是绿色。必须先做独立 baseline 修复批次，再开始大规模迁移，禁止把已有失败静默归为“与本次无关”。

| 批次 | 内容 | 验收 |
|---|---|---|
| B0 测试入口 | 修 pytest package/import、顶层插件声明、补 dev 依赖与版本锁定；明确 GPU 标记与无 GPU 参考测试 | 全测试可收集；环境版本可复建；GPU-only 标记不掩盖普通单测失败 |
| B1 前端合同 | FoodWeb fixture 对齐 nodes/links；修缺失 eslint rule、8 个 TS errors | npm ci、test:run、lint、tsc/build 全绿；不改生态模型 |
| B2 Stage contract 基线 | 修模式入参被 YAML 覆盖、英文 stable ID/中文 display name 混用；同步相应过期测试 | 四种模式有明确冻结清单；错误 stage/dependency fail fast；记录行为变化 |
| B3 回归地基 | 建立固定小世界、mock AI/embedding、固定 config、单回合与 save/load fixtures | current production pipeline 可重复运行/对照；发现随机差异必须记录，不放宽阈值掩盖 |

不要用 LegacyTurnRunner 作 old oracle：该文件只返回空报告。Engine 的 use_pipeline=False 已被忽略，不能作为独立旧路径。现有 regression_test.run_engine_with_snapshots 在所有回合完成后逐个报告读取同一当前 DB，也不是逐回合状态采集。old 指此 commit 的实际默认 tensor pipeline 或提取前的数值函数，捕获点必须移至每次提交边界。

## 2. 分阶段交付

| 阶段/优先级 | 最小改动单元 | 必须完成的 gate | 退出后下一步 |
|---|---|---|---|
| Audit / Design（本次） | 七份要求文档 + 基线/文件清单 | 入口、配置、算法接线、副作用有源码证据；缺口不包装成已实现 | B0–B3 |
| Foundation / P0 | simulation/v2：TurnContext、StageResult、WorldEvent、WorldVersion、SeedManager；纯 reducer | 稳定序列化/hash；深只读；跨进程/不同 PYTHONHASHSEED；非法 delta 拒绝；无 GPU/AI import | 接入 wrapper shadow |
| Async Safety / P0 | world-scoped coordinator/锁、generation、durable AIJob、幂等/CAS/fencing；旧 AI 回写转 adapter | running flag race、重试/取消/load/迟到、job crash恢复；AI有无不改数值结果 | 存储 |
| Persistence / P0 | SQLite提交/outbox + NPZ checkpoint/delta + timeline/replay + legacy importer | crash injection、旧档只读导入、分支隔离、随机turn replay hash相等、损坏失败 | shadow 每回合捕获 |
| Stage Migration / P0→P1 | 环境→资源→生态→移动→人口→演化→分化/灭绝；一次一个 stage | 每次旧/新相同 fixture；对应 unit/invariant；不得双写活跃 DB | 逐领域切换 writer |
| Explainability / P1 | mortality ledger、selection vector、EvolutionTrace、灭绝/分化原因、historical event API | 因果引用可追踪、死亡归因总和正确、无 AI 编造数值依据 | UI 展示 |
| Emergence / P2 | NPP闭环/dynamic K、niche construction、coevolution、deme drift/founder/bottleneck | 反馈开/关、多seed对照、敏感性分析、能量与人口不变量、长跑 | 玩家实验 |
| Player / P3 | 时间机、分支对比、解释UI、历史地图、形态视图、scenario editor | read-only replay 不碰 live；query keys隔离；方案输入schema/version | SDK |
| Ecosystem / P4 | scenario/mod/stage manifest、版本注册、验证、沙盒worker | 拒绝未声明写入/依赖、稳定插件顺序、禁网络/全局状态越界、兼容测试 | 移除legacy |

Foundation 与安全/存储先做窄接口，不提前实现全部生态算法。用户优先级与实施依赖不同：世界版本与 snapshot closure 是 stale job 和存档正确性的共同先决条件。

## 3. 新旧并行策略

1. 冻结 start snapshot、commands、RNG streams、config、embedding vectors、backend；真实 provider 全部替换成 deterministic fake。
2. Old adapter 与新 stage 在两个独立 workspace/process 中运行，不能依次复用同一个 mutable ctx 或 global DB。
3. 先建立输入→结果 capture，旧数据库写入导向隔离事务/临时库；审计结果以 stdout/文件返回，不能发布 SSE 或自动存档到用户世界。
4. 比較 named outputs、人口/质量 ledger、事件类型/原因、状态/trait，不只比较最终 population 或报告文字。保存第一个差异 stage 与输入 hash。
5. 行为保持的纯提取要求 reference 数值精确一致；GPU 对 reference 使用预先声明且有物理意义的容差，同时做整数守恒和边界不变量。不能套用现有 5% 总体差异阈值当所有验收。
6. 改变模型（例如资源闭环、灭绝生命周期）单独增加 model_version、变更说明和 approved fixture；比较方向/不变量与实验结果，不声称新模型必须逐位等于旧模型。
7. 功能开关决定唯一 authoritative writer。shadow 只能观测，不改世界、不触发 AI、不得多扣能量或重复分化。
8. 切换通过后保存对照 fixtures，再删除该旧 stage 的写路径；legacy routes/兼容 API 最后按调用图与历史档 gate 移除。

## 4. 各 Stage 迁移的特殊 gate

| 阶段 | 对照数据与 invariant | 易漏问题 |
|---|---|---|
| environment | 海陆/温湿/高程/水状态；同压力只应用一次 | 多尺度地图、pressure overlay shape、地质状态/随机种子遗漏 |
| resources | NPP、stocks、再生/消费 ledger、零光照/干旱边界 | 独立ResourceCalculation未注册，但PopulationUpdate末尾有资源动态调用；迁移时避免执行两次，不能把flow当stock |
| ecology | suitability、K、competition、predation per species/tile | 语义embedding影响数值、Holling分量、prey共享超额消费 |
| movement | 每个 species Σmigration=0（闭边界），障碍不可穿 | 方格/六边格、GPU随机、float32舍入、冷却/分支隔离 |
| population | N_next 恒等式、deaths sum、灭绝不繁殖 | population_update 与后置 tensor_sync 双写；新物种不在旧tensor axis |
| evolution | 预算守恒、局部gene-flow/Ne、可重复变异 | AI trait_updates回写；全局基因服务与旧缓存 |
| speciation/extinction | parent→child transfer守恒、唯一身份、阈值/原因、化石历史 | 不把地理断开直接当物种；失败不能留下只有一半子物种 |
| observability | 同一revision的报告/metrics/events/snapshot | report在旧DB同步前读取、回合0与next-turn off-by-one |

## 5. 自动验证与 CI

基础 CI：frontend 安装锁文件 → lint → typecheck/build → vitest；backend 固定 Python/lock → 全量 collection → 纯单测 → API/storage tests。GPU kernel job 单独在受控 runner 执行，报告设备、driver、Taichi/NumPy版本。新增 backend v2/ai jobs/storage 的 lint/strict typecheck 必须从第一批启用；旧未类型化区域的覆盖范围明确列出，不把子目录通过写成全后端 typecheck 通过。

新增测试矩阵：

- Unit：mortality、reproduction、competition、predation、migration、selection、adaptation、speciation；空世界、单物种、资源归零、完全隔离等极端案例。
- Determinism：相同快照/seed/input/manifest跨新进程、不同hashseed、不同worker完成顺序；保存后恢复继续与不中断执行一致；同一stage内部独立随机流。
- Invariants：population/biomass非负、无NaN/Inf、ID唯一、闭边界迁移守恒、traits预算、death归因不重复、灭绝无出生、timeline隔离。
- Hypothesis：随机合法小图/种群/流量、不同branch与job调度；失败样本seed与最小fixture入库。
- Persistence：kill点/异常注入覆盖文件落盘、SQL commit、outbox投递；旧JSON/gzip fixtures；跨checkpoint replay；父分支后续写入不影响子分支。
- Frontend contract：TurnCommitted/NarrativeReady/SpeciesCreated失效范围，断线补发、旧世界迟到响应、replay/live缓存隔离；关键玩家路径 E2E 使用 fake backend。

## 6. 100 / 500 / 1000 turn 长跑协议

三档使用同一 headless runner 与 mock AI，不依赖浏览器或真实 API。每次记录 commit/manifest、seed、硬件、物种/tile/占据率、各stage时间、RSS/GPU内存、save bytes/delta bytes、replay校验点。

| 档位 | 运行场景 | Gate |
|---|---|---|
| 100 turn（每次核心迁移） | 小图固定种子、无事件/灾害/灭绝边界 | 每turn invariants；重复run state hash相等；中途save恢复相等 |
| 500 turn（集成/夜间） | 中图多营养级、资源瓶颈、岛屿隔离、多分支 | 无异常/NaN；随机历史点replay相等；并行分支与串行结果一致 |
| 1000 turn（发布前） | 固定规模性能场景 + 动态分化场景，多seed | 无失控队列/对象引用；可解释人口/物种增长；持久化可恢复；性能/空间回归曲线 |

性能 gate 在 B3 测量后冻结：相同固定规模场景用 warmup 后窗口比较 p50/p95 与 RSS 斜率；可先将 >20% regression 设为需调查阈值，不能称已验证 SLA。物种规模增长场景按 occupied cells/edges归一化，不把正常计算增长判成泄漏。

增长控制要能解释：消耗、资源、K、分化预算与 Ne 应约束生态；若为计算预算拒绝分化，产生日志/事件，不能悄悄 clamp population。存档增长按实际变化字节与保留历史解释，不要求全世界不断变化时 delta 大小恒零。

基准 runner 需要资源上限与可恢复中断；本轮未执行这些长跑，也没有用 91 个局部单测冒充长期稳定性结果。

## 7. 每批交付模板

| 项目 | 必须填写 |
|---|---|
| Changed files / New files | 精确列表与用途，标注配置/模型变更 |
| Removed legacy code | 删了哪条可达路径；未删除写“无” |
| Tests added / passed | 命令、环境、数量、失败/skip原因；不得仅写 tests pass |
| Known issues | 尚未解决的基线、模型和并发限制 |
| Performance impact | 同fixture/硬件测量；没有测量明确写未测 |
| Save compatibility | 格式/读写矩阵、旧fixture hash、迁移可逆性与历史范围 |
| Next migration step | 下一条最小迁移边界与阻塞gate |

每批在 fork 的独立分支提交。不要给 routes.py/stages.py/model_router.py/map_manager.py 继续追加大型逻辑；保留薄兼容转发到新模块。首次删除 legacy 前，要求当前调用图无引用、回归oracle已另存、旧存档读档测试绿、公开API迁移记录齐全。
