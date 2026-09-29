# 演化迁移：明确旧入口、新数值模型和验收边界

## 新旧对应

| 原入口 | 新参考阶段 | 行为变化与验收 |
|---|---|---|
| stages.GeneDiversityStage / GeneActivationStage | SelectionPressure / TraitFitnessGradient / Metrics(genetics=True) | 不把embedding或器官文本当可遗传数值。七个定量trait与实际摄食/维护代价连接；局部群体均值方差与个体等位基因多样性明确区分。 |
| stages.GeneticDriftStage | reference_mutation → reference_drift | 世界/时间线/回合/阶段/物种/trait独立随机流；漂变标准差随sqrt(dt/N)，瓶颈增大方差。局部人口清零后的重新定居是founder近似，不追踪单个迁入基因。 |
| stages.GeneFlowStage | reference_gene_flow | 同物种、同可通行地理分量内按人口加权混合；不跨不连通岛屿。不把“两个占据区中间没有个体”直接当作地理隔离。 |
| SpeciationStage + services/species/speciation.py | reference_adaptation → reference_speciation_proposal → reference_speciation | 先小步改变局部trait，再由数值证据提出分化；LLM不决定后代数量、traits、种群转移或地图。实际转移及事件提交后才允许计划命名任务。 |
| 旧同步/异步AI结果回写 | 独立JobSpec/validator/SQLite annotation事务 | 已迁移的新模型只有展示字段可由AI补充；默认旧游戏的AI writer尚未整体退役。 |
| 删除/按单一计数灭绝 | reference_extinction | 五状态生命周期与最终分布、原因、祖先后代保留；新物种占新slot，化石slot不回收。 |

这些不是与旧AI输出逐位相等的数值重命名。旧模型依赖外部服务/缓存/随机状态、存在多处人口writer；新参考模型采用明确单位和独立manifest。旧global climate及资源公式已有冻结oracle；新的遗传/分化规则以固定输入、守恒、不变量和复算验收，不能声称“旧完整世界继续运行结果相同”。

## 26阶段执行顺序

Climate → Geology → Hydrology → Biome → PrimaryProductivity → ResourceRegeneration → HabitatSuitability → CarryingCapacity → Competition → Feeding → Dispersal → Migration → Connectivity → Mortality → Reproduction → PopulationUpdate → SelectionPressure → TraitFitnessGradient → Mutation → GeneticDrift → GeneFlow → Adaptation → SpeciationProposal → SpeciationCommit → Extinction → Metrics。

`ecological_pipeline()`的17阶段、`explainable_pipeline()`的20阶段保持独立配方；`evolution_pipeline()`显式增加新数组和stage版本。后续更改模型不能悄悄给旧世界换配方。所有阶段仅返回StageResult，最终WorldStore才原子发布；SpeciationCommit是候选状态内部的一次身份转移，不是中途DB commit。

## 可解释的数值约束

- 七个trait各在[0,1]，总预算不超过3。Armor每增加1最多支付0.25 speed；speed不足时限制armor增加。所有trait通过下一回合实际维护支出支付0.05×trait总和×体重/年。
- 定向步使用真实摄食关系的有限差分proxy。预算投影后的定向步若降低该线性proxy则拒绝；随机变异/漂变造成的负变化允许存在。trace的fitness_gain不是实际存活率提升。
- 同一species当前仍使用全物种加权trait执行生态。每个deme的不同trait用于遗传分化，尚未逐tile求独立适应梯度。不能把这一近似宣称为个体遗传或局部最优。
- 分化至少有两个实际占据的地理分量，每组和剩余群体各≥20、隔离≥12回合；评分0.30地理+0.25生态+0.20遗传+0.15隔离时间+0.10低基因流，阈值0.70。纯地理分离最高0.55，不能单独分化。
- 转入子物种的每格人口、reserve、deme、proposal原样复制，父行清零，体重精确继承。复制使用逐项精确检查；父物种的trait重算，并排除身份转移造成的假衰退。
- 固定容量满时发SpeciationDeferred，保留全部数值与化石，不扩数组、不创建虚拟AI后代。
- EvolutionTrace来自数值事件：trait变化、七轴压力、线性proxy收益、代价、mutation/drift/gene flow/founder/bottleneck原因。历史从checkpoint+delta和事件读取，不累积species×tile×turn JSON。

## 已知限制

局部trait是均值而非基因型；基因流是连通分量混合而非逐迁徙边输送；温度/水压力目前缺对应可变生理trait。生态工程、完整协同演化评估、杂交、分辨率调度、可编辑scenario/Mod接口仍需后续逐步接入。独立阶段与完整回归不保证真实生态标定或长期多样性稳定。
