# Data Model V2（设计，尚未实现）

基线与问题见 [CURRENT_PIPELINE.md](CURRENT_PIPELINE.md)。迁移期保留现有 SQLModel 表，通过 adapter 转为以下值对象；不要把 ORM Session 放进 TurnContext。

## 1. 身份、时间和版本

| 对象 | 必需字段 | 约束 |
|---|---|---|
| World | world_id、world_seed、created_at、format/schema/model versions | world_id 不由可改的存档名充当 |
| Timeline | timeline_id、world_id、parent_version、fork_turn、head_version、rng_namespace | parent_version含完整world/timeline/generation/revision；分支head独立 |
| WorldVersion | world_id、timeline_id、generation、revision | generation 在破坏性替换/恢复时更新；revision 单调递增，不能倒退复用 |
| Turn | turn_id、turn_number、start_version、end_version、start_snapshot_id、command_hash、state_hash | turn_number表示已完成回合；新世界创世turn=0；旧档导入保留原turn并标记最早可回放边界；非回合命令可推进revision |
| Command | command_id、idempotency_key、expected_version、sequence、kind、payload/schema、input_hash | 同键不同输入返回冲突；顺序持久化；禁止隐含当前配置 |
| SimulationManifest | code revision、stage IDs/versions、config hash、trait/array schema、RNG version、backend/precision、plugin hashes | 恢复时先校验；未支持的未来版本明确拒绝 |

turn_number、revision、墙上时间互不替代。同一timeline的revision跨generation仍单调递增，不复用；新timeline可从自身初始revision开始。历史浏览不改变head；“回到turn 80继续”默认创建子timeline；覆盖导入需要新generation，同时检查完整身份，避免turn相同或兼容导入时错误匹配旧任务。generation是运行实例的额外隔离栅栏，不代替revision或timeline身份。

### SeedManager

使用 BLAKE2b/SHA-256 对带长度/类型的规范编码做稳定派生：world_seed、rng_namespace（默认 timeline_id）、turn_number、stage stable ID/version、entity/deme/tile ID、purpose、draw counter。算法与编码版本入 manifest。禁止 Python 内置 hash()，禁止全局 random.seed/np.random.seed；一个 stage 不能消耗另一个 stage 的随机序列。

reference 提供局部 Generator(Philox) 或固定版本的等价计数器 RNG；GPU 从同一 key/counter 产生样本或接收预生成数组。具体浮点转换、采样算法、排序和舍入均冻结；不把相同整数 seed 当作不同库天然产生相同输出的保证。可变抽样次数需要每个 entity/purpose 独立 stream，新增一个物种不能扰动所有现存物种的随机数。

确定性保证分级：固定 reference backend/version 提供精确 state hash；固定 GPU backend 用重复运行测试验证，若有非确定归约则不能标 deterministic=true；跨硬件/不同精度只可声明已测的容差，不承诺逐位相同。精确历史回放由保存的 delta 保证，不依赖重跑 GPU 得到相同数字。

## 2. TurnContext 与只读快照

TurnContext 字段：turn_id / world_id / timeline_id / seed、world_version、start_snapshot_id、simulation_manifest、command、environment_state、species_state、population_state、habitat_state、food_web_state、gene_state、active_events、external_pressures、stage_results、evolution_proposals、ai_jobs、metrics、warnings、errors。

设计语义：

- 输入快照 immutable；NumPy array 禁止写入且不能暴露可变别名。frozen dataclass 不能单独保证深层 immutable，adapter 必须复制或交付受控只读 buffer。
- stage_results / proposals 等不是允许任意 stage 修改的共享列表。调度器创建下一份 context view，append 通过 reducer 完成。
- Stage 只能看到声明 reads 的视图与局部 SeedStream；测试中放置读写审计代理。进程隔离是运行期约束，类型提示本身不是安全边界。
- Context 中的 errors 用可序列化 Diagnostic(code,stage,path,message,details)，不能保存 Python Exception 或 DB 对象。

## 3. StageResult 与 delta

StageResult = stage_name + stage_version + status + duration_ms + state_delta + events + metrics + warnings + errors + random_seed + input_hash + output_hash。

state_delta 是带 schema 的 typed patch，不是任意 dict/update callback：

| Patch | 内容 | 校验 |
|---|---|---|
| ArrayPatch | array_id、axis_version、chunk/indices、expected_chunk_hash、new_values | 唯一位置、固定 dtype、shape/单位一致、值有限 |
| EntityPatch | operation(create/update/tombstone)、entity_id、expected_entity_version、明确字段/完整新实体 | create断言不存在；update/tombstone断言版本匹配；allowlist、外键、species ID唯一；灭绝不删除身份 |
| PopulationLedger | species/deme/tile、births、deaths by cause、transfer entries | 每条非负；迁移入出对应；分化/杂交转移不重复 |
| TraitProposal | source traits、delta、cost、fitness counterfactual、pressure refs | 总预算、边界、阶段权限 |
| EnvironmentFeedback | tile、变量、source species、flux、effective_substep | 反馈延迟显式，不让同一步反馈循环读自己输出 |

仅 PopulationReducer 有 population 写权限。其他stage提供流量：人口阶段先消费demography/movement ledger；后置speciation/hybridization提交带唯一entry_id的parent→child transfer，再由同一reducer消费新增账项，禁止重放先前出生/死亡。Observability与最终校验在全部transfer应用后运行。统一写者不意味着所有StageResult同时叠加：使用manifest固定依赖顺序和field reducer，冲突拒绝而不是last-write-wins。

## 4. 规范数值表示

| 数据 | 表示与单位 | 保留/派生 |
|---|---|---|
| 环境 | named channels；temperature °C、elevation m、precipitation mm/生态时间、水/土壤单位有定义 | 气候、地形、水、养分是状态；suitability 可重建 |
| 人口 | reference 使用 int64 个体；物种×tile 稀疏 chunk，稳定 species axis | 栖息分布为唯一真源；species total 是归约，不能双向互相覆盖 |
| 数值模型通量 | float64 reference；确定性保守舍入到整数 | 舍入残量如影响下一步，必须持久化 |
| GPU 人口/通量 | 显式声明精度，不默认 float32 能精确表示大种群 | float32 超过 2^24 不能逐个体精确计数；迁移需精度/守恒测试 |
| Trait/Gene | trait registry/version、单位/范围、deme allele frequencies、effective population | 数值 genotype/phenotype 与文字描述分离 |
| 资源 | NPP 流量、plant/detritus/consumer biomass kg、nutrient/water stocks | 缓存不能替代需跨回合保存的 stocks |
| 食物网 | 有方向的稳定 species ID 边及强度、模型版本 | 不由可变物种名称推断；缓存与规范关系分开 |
| 空间 | tile_id→坐标、六边格拓扑/边界、region/deme ID | 不能默认为规则四邻格；geometry checksum 入 manifest |

不强制物种多时一直分配 dense S×H×W。稀疏存储与密集 GPU working buffer 分离；转换需保留 axis manifest 和 tile mapping。背景物种使用低频/区域统计时，也要能解释其总量和延迟通量。

## 5. 演化与灭绝

- SelectionPressureVector：temperature/water/food/predation/competition/mobility/disease，归一化方式、正负含义、source event/ledger references。
- MortalityBreakdown：temperature/starvation/predation/competition/disease/disaster/old_age/other 的 hazard 和实际死亡数；合计与 ledger 一致。
- EvolutionBudget：available、spent、maintenance_cost、tradeoffs、约束版本；拒绝全 trait 无成本增强。
- EvolutionProposal：proposal_id、kind、parents/demes、确定的 trait 与人口转移、score、threshold、cause、random_stream_ref；不含“等待 LLM 决定是否发生”。
- SpeciationState：隔离开始时间/时长、连通性、生态分歧、遗传距离、gene flow、population size。用户示例的 0.30/0.25/0.20/0.15/0.10 是待标定参数，不当成生物学定律。
- Lifecycle：Healthy → Declining → Critical → FunctionallyExtinct → Extinct；前四态按规则允许恢复，Extinct 不繁殖。重新引入必须有显式 Command/Event 和新人口来源。
- FossilRecord：species_id、ancestor/descendants、last_population、last_habitat_snapshot_ref、extinction_cause、last_seen_turn；分布引用历史 delta，不每回合复制完整分布。
- EvolutionTrace：species/proposal/turn、before/after traits、pressure vector、fitness_before/after、tradeoffs、accepted/rejected reasons、evidence event IDs、stage/model/RNG versions。

## 6. WorldEvent 与展示数据

WorldEvent 字段：event_id、schema_version、world_id、timeline_id、generation、turn、revision、ordinal、type、actor、target、location、cause、payload、state_delta_ref。event_id 从 timeline/generation/command/stage/ordinal 稳定生成；created_at 只做运维元数据。

事件类型至少覆盖 SpeciesCreated / SpeciesExtinct / SpeciesAdapted / SpeciationOccurred / MigrationOccurred / MassExtinction / ClimateShift / Volcano / MeteorImpact / BiomeChanged / FoodWebChanged，以及 TurnCommitted、NarrativeReady、TimelineForked。

因果关系使用 event/proposal/command ID，而不是自由文本“原因”。展示时间线、报告、AI 输入共享 domain events；每个数组元素变化写 delta，不生成每格每物种一个巨大叙事事件。事件流不能凭空替代重建世界需要的完整数值 delta。

NarrativeAnnotation 单独存储 object/event/proposal IDs、base_world_version、narrative_revision、language、名称/描述/摘要、provenance(job_id/model/prompt/schema/fallback)。AI 不写 SpeciesSimulationState。改文案不触发重新训练/重新 embedding 后改变生态。

## 7. Metrics 与持久状态边界

每次提交计算 richness、Shannon、Simpson、total_biomass、population-weighted mean trophic level、food-web connectivity、extinction/speciation/migration rates、genetic_diversity、NPP、stability。公式、空世界的 0/undefined 表达及 rate 分母写入 metrics schema；Simpson 采用 1−Σp²，不能不同 UI 各用一个定义。

生态稳定性先明确窗口与归一化波动指标；没有足够窗口时为 null 加 sample_count，而非 NaN。performance metrics 和生态 metrics 分开；数值世界哈希排除 duration、RSS、token 使用与 wall-clock。

任何会影响下一回合的状态都属于 snapshot closure：resource stocks/event pulses、isolation duration、migration cooldown、decline streak、gene flow、deme/diversity state、tectonic fields、pressure queue/escalation、RNG counters、resolution schedule、player/scenario commands、config/plugin manifest。FAISS index、UI cache、tensor 工作空间只是可重建缓存，不能有独占的演化记忆。
