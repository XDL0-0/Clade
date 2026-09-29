# Simulation Pipeline V2 — 目标架构（设计，尚未实现）

基线：4d1045bfecd412aa26dd55e506c8416b83ac984b。先读 [CURRENT_PIPELINE.md](CURRENT_PIPELINE.md) 与 [ARCHITECTURE_PROBLEMS.md](ARCHITECTURE_PROBLEMS.md)。本文件不代表现有引擎已经具备这些保证。

## 1. 要保留与要改变的边界

保留已有 FastAPI、SQLModel/SQLite、React Query、Taichi 内核、物种模型与旧存档读取能力。现有 SimulationContext / StageResult 是迁移起点，但它们目前传递 mutable ORM 对象和服务访问权，不能直接宣称为不可变世界快照或事务 delta。已有 NPP、代价计算、食物网、漂变和生态拟真逻辑需要逐项确定接线与数值语义，不再各写一套。

V2 规则：**Simulation decides WHAT happened. AI explains WHAT it means.** 名称、描述、叙事和器官文字与数值 trait、population、地理状态分开。任何文本 embedding 若参与生态计算，必须作为带模型版本与内容哈希的固定输入显式导入；AI 改名不能通过重新计算 embedding 偷渡成生态变化。

## 2. 回合与提交

~~~text
Command（持久化、有幂等键与顺序）
 → TurnContext + immutable start snapshot + config/plugin/backend manifest
 → deterministic stages → candidate state + proposals + domain events
 → AI enrichment preparation（固定模板、可选严格校验的注释）
 → validation → compare-and-swap atomic commit
 → state delta + event log + job/outbox + metrics（同一事务）
 → checkpoint materialization / SSE publish（事务后，可重试）
~~~

重要调整：AI 网络等待不持有 DB 事务，不决定回合是否成功。模拟先产生稳定的新物种 ID、trait、人口分配与模板名称；AI enrich 阶段准备任务和默认叙事。异步结果通过独立的 NarrativeAnnotation 命令校验后提交展示数据。需要回合内完整报告的交互模式可配置有界等待；超时仍使用模板。两种模式得到同一个数值世界哈希。详见 [AI_JOB_ARCHITECTURE.md](AI_JOB_ARCHITECTURE.md)。

TurnContext 只持有值与只读数组，不持有 repository、engine、LLM client、event callback。Stage 返回 StageResult；调度器用声明式 reducer 将 delta 应用到私有 candidate view，供后续 stage 读取。**只有 CommitService 可以改变已发布世界**。失败丢弃 candidate，不推进 turn/version；SSE progress 不能被当成已提交 WorldEvent。

所有 API 操作（玩家造物、压力、基因编辑、配置变化、加载分支）也必须穿过命令边界。只修 turn loop 会留下绕过版本检查的入口。运行中的用户操作排队到下一回合边界，按 sequence 排序；物种生成 AI 只生成建议文本，数值参数由规则校验的玩家 Command 确定。

## 3. Stage contract 与状态所有权

每个 stage 声明 stable name、version、dependencies、deterministic、parallelizable、reads、writes、side_effects；执行 validate_inputs → execute → validate_outputs。数据 schema 版本、量纲、tensor axis、空间拓扑也是 contract。显示名称与 stable ID 分开。

- simulation stage：side_effects 为空；不得 I/O、访问系统时钟、全局 RNG 或当前活跃世界；只返回 delta/events/metrics/proposals。
- orchestration service：允许明确声明 DB、object store、任务队列与 SSE 副作用，不伪装成纯数值 stage。
- 必需输入缺失、依赖缺失、重复注册、写入冲突、NaN/Inf 立即失败；禁止用空列表默认值掩盖缺失生产者。
- 阶段按 DAG 和稳定次序执行。parallelizable 仅表示可以并行，还必须通过读写冲突检查；求和归约次序固定。物种 ID 排序与独立 RNG stream 保证任务完成顺序不影响输出。
- 超时协作取消后等待结束；不可协作的 GPU/同步数值工作在可终止 worker process 中运行。停止等待不等于停止写入。

## 4. 新旧映射与阶段输入输出

“复用”指提取算法并建立 fixture 对照，绝不把旧 service 的 DB 写入包一层便称纯函数。

| 新阶段（依赖） | 输入 → 输出 / 写权限 | 现有来源与迁移动作 |
|---|---|---|
| TurnInitialization | 命令、head、manifest → Context、seed stream、外压 | InitStage、session、TurnCommand；拆离 callback 与全局查询 |
| Climate | 上回合气候、季节、外压 → 气候 delta | EnvironmentSystem、MapEvolution；固定参数与单位 |
| Geology（Climate） | 板块、地形、地质时钟 → 高程/海陆/火山 delta | tectonic、geo/map_evolution；消除两套地形写者 |
| Hydrology（Geology） | 高程、降水、储水 → 径流/河湖/水分 | geo/hydrology；提取只读地形输入 |
| Biome（Hydrology） | 温湿、土壤、水、上一轮植被 → biome delta | map_manager/vegetation_cover；避免更新后又被旧张量覆盖 |
| PrimaryProductivity（Biome） | 光照、气候、水、养分 → NPP flux | 复用 ResourceManager 的 Miami 模型，统一 kg/tile/生态时间单位 |
| ResourceRegeneration | NPP、plant/detritus/nutrient stocks → 可消费资源预算 | 接入现有再生/过采积累，补养分与碎屑显式状态 |
| HabitatSuitability | species traits × tile environment → suitability | 统一 geo/tensor 两条适宜度路径；只算一次同版本输入 |
| CarryingCapacity | 资源、体重、代谢、suitability → K | 将 ResourceManager 与 tensor K 统一；不固定人口保底 |
| Competition | 密度、niche overlap → intra/inter demand | tensor/competition、kin_competition、plant_competition；保留解释分量 |
| Predation | food web、相遇概率、双方 trait/丰度 → 捕食通量、猎物死亡请求 | 复用饱和捕食相关算法；对多捕食者共同请求限额 |
| Disease | 密度、免疫、宿主/寄生关系 → disease hazard | ecological_realism 已有原型；初期可禁用，不能隐式读取描述 |
| Dispersal | 种群、邻接图 → 保守迁移流 | 拆出 tensor diffusion；验证六边格/边界语义 |
| Migration | 食物、气候、捕食、拥挤压力 → 有向迁移流 | tensor/ecology、migration、dispersal_engine；不直接减人口 |
| Connectivity | 海陆、障碍、迁移通量 → 连通分量与基因流网络 | speciation 中地理聚类提取；地形版本缓存 |
| Mortality | 外压及各 hazard、迁移后局部密度 → MortalityBreakdown | 拆 tensor/ecology；有明确竞争风险归因，禁止重复死亡 |
| Reproduction | 存活者、资源预算、密度、life history → births | 拆 tensor ecology 与 reproduction；灭绝物种恒零 |
| PopulationUpdate | N、births、deaths、migration ledger → 唯一人口 delta | 取代多个 ORM/tensor 同步写者；新物种分流也进此 ledger |
| SelectionPressure | mortality/birth/resource ledger → pressure vector | 保留现有 pressure/fitness 计算并补来源引用 |
| FitnessGradient | 同一冻结环境的 trait 微扰 → 数值梯度 | 使用模拟器反事实评估，不能让 LLM 给分 |
| Mutation / Drift | 局部 deme、Ne、专属 RNG → mutation proposals | gene_diversity/genetic_evolution 逻辑拆分；记录中性/有害变异 |
| Adaptation + Tradeoff | 梯度、变异、预算 → 受约束 trait delta | 复用 tradeoff/trait_config，先检查可支付成本再接受 |
| GeneFlow | connectivity、deme 频率、迁移 → 遗传混合 | 复用 gene_flow；质量守恒，抑制隔离分化 |
| Speciation | 隔离时长、生态/遗传差异、gene flow、Ne → Proposal | SpeciationMonitor + speciation_rules；先打分，再确定物种/人口分流 |
| Hybridization | 接触、相容性、gene flow → proposal/trait/population transfer | tensor hybrid 与 service 分离；同一出生不得计入两个物种 |
| Extinction | 趋势、可繁殖个体、分布、原因 → lifecycle transition | extinction_checker + history；保留化石记录，不删除身份 |
| NicheConstruction | plant/reef/burrowing/decomposer traits 与生物量 → 下一步环境反馈 | 复用 vegetation 原型，补真实资源与土壤反馈 |
| Observability | committed-candidate ledgers/events → metrics + trace | 统一 TurnReport、tensor metrics、健康指数口径 |
| Enrichment / Validate / Commit | proposals、candidate、head → narratives/jobs / durable turn | 用新模块替换 stage 直接 repository/save/SSE 调用 |

Connectivity有两个明确时点：地形变化后建立本回合通行图；迁移后更新realized gene-flow/isolation状态。避免把迁移依赖的连接图放到迁移完成之后才算。GeneFlow与Drift的顺序作为模型版本的一部分，不能靠文件加载顺序决定。后置分化/杂交的人口transfer仍由同一PopulationReducer消费新增账项；Extinction与Observability读取全部transfer应用后的candidate，不能重复应用之前的出生/死亡。

## 5. 生态模型约束

### 时间与单位

现有配置默认 500,000 年/回合。不能把季节、繁殖率、捕食率都直接乘此数字。分离 geological_years、ecological_dt、evolutionary_generations；通过固定数量或确定性自适应 substeps 得到生态稳态近似，然后推进宏观演化时钟。阶段与存档必须携带单位表、转换公式和模型版本。早期先冻结旧时间语义做对照，再单独更改模型。

NPP 是流量，plant biomass 是存量；K 是资源可支持个体数。消费质量经 assimilation 转入消费者，剩余进入 detritus/respiration；必须显式记录光合输入与系统输出，不声称封闭能量守恒。捕食转移和疾病/灾害死亡不可重复记入资源损耗。

### 人口与归因

唯一更新式 N_next = N_start + births − deaths + inflow − outflow。出生、分化、杂交和再引入均为 typed ledger entry。迁移在闭合世界全局净和为零；开放边界的损失是具名事件。非负约束通过通量限额和保守舍入保证，不能末尾 clamp 掩盖凭空造人口。

多个死亡风险按 competing hazards 或固定、有版本的分配顺序归因；每个原因 deaths 非负，合计等于实际 deaths。先选明确方案再冻结 fixture，不能把多个独立概率直接相加超过 1。

EvolutionTrace 记录 baseline/candidate fitness、扰动大小、压力分量、预算、拒绝原因、成本与随机来源。fitness_gain 是模型内预测，并非证明现实生物必然适应。中性/有害漂变允许存在；“适应”与“随机变异”分开标记。

### 真正的反馈

协同演化来自 predator/prey 或 toxin/detox trait 改变同一捕食/生存函数，反过来改变下一回合的选择梯度；没有专用“军备竞赛事件脚本”。Niche construction 输出下一生态子步/回合的环境 delta，避免同一步无穷循环。用关闭反馈的对照实验验证机制，并报告参数敏感性，而非把一次有趣轨迹称为涌现证据。

## 6. 分辨率、并行与性能

- Critical / Focus / Background 已有分层服务，但目标需要新增数值分辨率契约，而不仅是 AI 关注度。Critical tile，Focus region，Background 固定低频统计；每个模式保存最后更新时间与待积累通量。
- 升降级依据确定性规则与显式 player-focus Command；上下阈值与最短驻留时间防抖。聚合/细化保持质量和遗传统计，不凭空补密度。
- 环境、人口、trait、food-web 各有版本，派生 cache key 包括 world/timeline/version/model/config。只在其输入变化时重算。
- 并行实验使用独立 worker process + 独立 timeline state/DB transaction；一个 Taichi runtime 不承担多个可写世界共享场。GPU 初期串行调度，CPU reference 支持独立实验。
- profiler 保存每 stage duration、等待/计算/I/O、CPU RSS、GPU allocator 使用量、物种/占据格数、AI token、实际字节与 checkpoint 时间。duration 和墙上时间不进入 deterministic hash。
- 延迟目标必须在固定硬件、相同 S/T/占据率下测量；此审计阶段没有编造毫秒数或加速倍数。

## 7. 前端与历史

继续使用已安装 React Query。统一 query key 为 world/timeline/view_turn/resource/filters；live 与 replay 不共用 cache。服务端发布 durable event_id、commit_version、changed_domains；前端按 cursor 去重并批量 invalidate。断线通过 Last-Event-ID 补发；cursor 过期做带版本的全量重新同步。

时间轴读取 checkpoint+delta 重建的只读历史 view；禁止把历史数据载入当前活跃世界。分支创建独立 timeline，比较相同时间基准下的 richness/extinction/Shannon/biomass/trophic/trait 分布。名称来自对应 narrative revision，缺少 AI 结果时模板可用。

## 8. 模块布局与接受边界

先新增 simulation/v2/{context,contracts,seed,events,version,pipeline,commit}.py，避免覆盖现有同名 context/stages 模块；适配器放 simulation/adapters。后续逐阶段迁往 simulation/stages/，在旧 stages.py 改包之前先解决所有导入。

evolution/{selection,mutation,adaptation,drift,gene_flow,tradeoff,speciation,trace}；ai/{jobs,providers,schemas,prompts,validators}；storage/{checkpoints,deltas,events,replay,timelines}。文件目标 <500 行，核心 <800 行；把数据表/配置与算法分开，但不为凑行数拆碎逻辑。

P0 接受：失败回合无写入、同种子稳定、stale AI 无副作用、重试幂等、旧存档可读、checkpoint 恢复一致、分支隔离、长跑可观察。P1/P2 改模型在这些基础上推进；P3 时间机与解释 UI 使用同一数据源；P4 插件注册需 manifest、schema、读写集、固定顺序及权限隔离。
