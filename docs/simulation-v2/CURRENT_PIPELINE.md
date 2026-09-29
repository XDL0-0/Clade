# Current Simulation Pipeline — 源码审计

基线：4d1045bfecd412aa26dd55e506c8416b83ac984b，2026-09-29。源仓库 Pocketfans/Clade → 用户 fork [XDL0-0/Clade](https://github.com/XDL0-0/Clade)。本地 clone 的 origin 是 fork，upstream 是源仓库。

本文件记录**当前运行行为**；目标架构与映射在 [TARGET_PIPELINE.md](TARGET_PIPELINE.md)，风险分类在 [ARCHITECTURE_PROBLEMS.md](ARCHITECTURE_PROBLEMS.md)，测试与审计限制在 [AUDIT_BASELINE.md](AUDIT_BASELINE.md)。源码定位以此 commit 为准，行号不是未来修改后的稳定 API。

## 1. 实际入口与生命周期

1. backend/app/main.py:136 的 lifespan 创建 SimulationSessionManager 与 ServiceContainer，初始化全局 DB。main.py:214 注册 api/router.py 聚合路由，另注册 admin/embedding；默认不注册 api/routes.py。
2. api/simulation.py:288 的 POST /api/turns/run 检查 session.is_running、出队压力、扣能量、设置 engine._event_callback，直接 await engine.run_turns_async。HTTP 等多回合完成后返回报告数组；SSE 是另一个连接。
3. simulation/engine.py:316 逐回合调用 run_turn_with_pipeline；默认初始化 standard Pipeline。:285 创建新的 SimulationContext，引用共享服务、ORM数据与 callback；没有 world_id/timeline_id/world_version 或稳定 stage seed。
4. StageLoader 读 YAML、建 stage并按 order 排序；Pipeline 顺序 await stage.execute(ctx,engine)。StageResult 由 executor 包装成功/异常/耗时，不是 stage 返回的状态补丁。
5. 各 stage 分别更新数据库、缓存、张量、内存服务，并直接 emit_event。report在后置tensor同步之前生成；历史和存档没有覆盖整个回合的统一事务。
6. engine.py:312 即使 Pipeline 有失败也递增 turn_counter；API发送 complete/turn_complete、清 is_running，再通过 FastAPI BackgroundTasks 自动保存（api/simulation.py:380）。

## 2. 默认的22阶段顺序

~~~text
init(0) → parse_pressures(10) → pressure_tensor(11) → map_evolution(20)
→ fetch_species(30) → food_web(35) → tiering_and_niche(40)
→ tensor_state_init(49) → tensor_ecology(51)
→ speciation_data_transfer(86) → population_update(90)
→ gene_diversity(93) → gene_activation(95) → speciation(125)
→ background_management(130) → tensor_metrics(139) → build_report(140)
→ save_map_snapshot(150) → tensor_state_sync(159)
→ save_population_snapshot(160) → save_history(170) → finalize(180)
~~~

StageLoader 实例化类后按实例 order 排序，没有把 YAML order 赋回实例（stage_config.py:714）。speciation enum/实际 order 为125，当前 YAML 写120但未生效。YAML声明不能直接当作实际执行顺序。

**模式覆盖已实测**：stage_config.py:414 使用 config_data.get("mode", mode)，仓库 YAML 顶层固定 standard。所以传入 minimal / standard / full / debug 均加载上述22阶段。原始输出见 [stage-modes.txt](evidence/stage-modes.txt)。声明的模式清单与运行结果要分开：

| YAML声明 | 声明阶段数 | 与standard不同 |
|---|---:|---|
| minimal | 12 | 去掉地图演化、food web、基因/分化、历史等 |
| standard | 22 | 当前实际路径 |
| full | 31 | 加tectonic、gene flow/drift/hybridization/promotion、vegetation、embedding integration/hooks、export |
| debug | 13 | minimal加population snapshot |

ResourceCalculationStage、EcologicalRealismStage、独立mortality/migration/dispersal等代码存在，但不在当前 standard 的显式stage列表。不能把“已定义”写成“每回合运行”。完整资源/生态效果还需沿底层函数判断。

## 3. 每阶段输入、输出和副作用

下表为代码实际读写摘要。除 executor 最后包装的 StageResult 外，execute 返回 None；“输出”通常是对 ctx/对象的原地修改。reads/writes 的声明只覆盖部分字段，不是强制隔离。常用输入是 previous ctx + engine服务，非纯快照。

| order / stage / 位置 | 输入与前置 | 输出 | 副作用 |
|---|---|---|---|
| 0 Init；stages.py:346 | command/turn、engine服务 | 清理状态，尝试plugin start | 清 speciation/migration/mortality cache；插件可持状态 |
| 10 ParsePressures；:403 | command、turn | pressures/modifiers/major_events | EnvironmentSystem、escalation累计状态；SSE |
| 11 PressureTensor；tensor_stages.py:37 | modifiers/pressures、当前地图尺寸（此时通常未装载） | pressure_overlay | 全局 pressure bridge；缺少地图时默认64×64，非真实128×40 |
| 20 MapEvolution；stages.py:429 | modifiers/events、地图服务状态 | current_map_state、map_changes、温度/海面变化 | 地图/状态repository写入、海陆重分类与栖息地搬移、资源事件、SSE |
| 30 FetchSpecies；:651 | species repository、当前环境 | all_species、alive batch、extinct codes | 清/更新species/embedding缓存，气候栖息调整与压力记录 |
| 35 FoodWeb；:843 | species、现有关系 | food_web_analysis、trophic signals、重新读取species | maintain_food_web更新关系/物种；日志/SSE |
| 40 TieringAndNiche；:951 | species、watchlist、DB habitats/tiles | tiered、niche_metrics、all_habitats/all_tiles | repository读、niche缓存；SSE |
| 49 TensorStateInit；tensor_stages.py:123 | species、tiles、每物种最新DB habitats | env/pop tensor、species map、tile grid | 再读栖息地；缺分布时生成位置；重标定总量 |
| 51 TensorEcology；:341 | tensor、trait/preference、pressure、global config、cooldown | 新tensor population、死亡统计、migration count、衰退计数 | GPU/全局compute、habitat_manager cooldown；ctx动态字段；SSE |
| 86 SpeciationDataTransfer；stages.py:1477 | combined results、modifiers、tensor | candidates/trigger codes、分化参考 | 把候选和tensor挂入长期持有的speciation service |
| 90 PopulationUpdate；:1123 | mortality、niche、resource/环境、species | new_populations、births、final_population，再重分配tensor | 第二套繁殖/竞争/K计算、species upsert、extinction处理、全局container配置；末尾更新ResourceManager |
| 93 GeneDiversity；:1661 | species、环境/压力 | 多样性与漂变统计 | service修改species基因状态并upsert；缓存 |
| 95 GeneActivation；:1624 | species、modifiers | activation_events | batch_check激活基因，upsert |
| 125 Speciation；:2209 | species、死亡/压力、tensor候选、embedding hints | branching_events、新子种 | process_async创建物种/栖息地/更改trait，并发AI/内共生任务；SSE |
| 130 BackgroundManagement；:2452 | tiered/background种群 | summary、mass_extinction、reemergence | background/promote/reemergence修改对象或持久状态 |
| 139 TensorMetrics；tensor_stages.py:618 | 全局tensor collector与当前数据 | tensor_metrics | end_turn/滚动监控历史，不是完整生态时序 |
| 140 BuildReport；stages.py:2485 | species、结果、events、traits、环境 | TurnReport | DB读取、LLM报告调用/stream与SSE；读取发生在159同步前 |
| 150 SaveMapSnapshot；:2706 | 当前species、旧tile mortality survivors、新pop | habitats snapshot | 调用 map_manager.snapshot_habitats写DB；并非完整历史地图checkpoint |
| 159 TensorStateSync；tensor_stages.py:673 | tensor + new_populations + species/tiles | species总量与逐tile habitats | 再次upsert species、write_habitats；部分异常被记录后继续 |
| 160 SavePopulationSnapshot；stages.py:2781 | 重新查询species | 近期人口历史 | PopulationSnapshotService写模块级cache，每species最多100项 |
| 170 SaveHistory；:2999 | ctx.report | TurnLog.record_data | history_repository逐记录提交；内容是报告JSON |
| 180 Finalize；:3065 | turn、地图状态 | next turn状态、完成事件 | environment state保存；无统一commit |

TensorEcology 并不是单一生态公式。tensor/ecology.py 的 process_ecology 内部依次提取世代/时代缩放，计算 suitability、mortality，扣除死亡，执行 dispersal、pressure-driven migration、reproduction，并进行竞争/密度/K限制与统计。之后 PopulationUpdate 又从 initial_population 重算繁殖和总量、重分配 tensor，最后 TensorStateSync 再落 DB。需要把“有这些算法”与“只有一个权威结果”区别开。

## 4. 非默认与 legacy 路径

| 现有功能 | 接线与意义 |
|---|---|
| ResourceCalculation（stages.py:730） | 有Miami NPP、资源动态、过采/承载力服务；独立stage未注册/未列YAML，但PopulationUpdate:1370末尾实际调用资源动态。GPU早先读取tile.resources，未接通current_npp的统一能量闭环 |
| EcologicalRealismStage | 有Allee、疾病、适应滞后、空间捕食、互利共生等原型；当前默认未调度，接口保留 |
| TectonicMovement / VegetationCover | full声明有，但当前mode覆盖使普通full请求仍跑standard；MapEvolution仍有自身地形逻辑，不可误称“完全无地质” |
| GeneFlow / GeneticDrift / AutoHybridization / SubspeciesPromotion | full声明有；gene diversity内部也有漂变相关逻辑；玩家API还可触发杂交 |
| 旧mortality/migration/post-migration stages | 源码保留且测试仍引用部分名字；默认已由tensor路径取代，不应误当当前主链 |
| EmbeddingIntegration / hooks / plugins | 有独立stage和全局注册表；部分入口在FetchSpecies，部分只在full/default factory；并非纯离线注释 |
| api/routes.py | 4745行旧单体，main未挂载，但活动analytics.py:294/302的配置更新仍动态导入旧routes并调用旧全局服务。不能当纯死代码直接删 |
| simulation/legacy_engine.py:24 | LegacyTurnRunner.run_turn只生成空species的“遗留模式运行”报告，没有旧模拟实现 |
| simulation/snapshot.py | 另有全量JSON快照/恢复工具、Python random状态等；不是当前save的checkpoint+delta实现 |
| simulation/regression_test.py:250 | 先跑完全部回合，再为各report读取当前DB；无法得到真实逐回合物种状态；capture_callback也未被实际使用 |

full声明额外阶段的读写如下。修复模式选择后，这些副作用会重新可达，不能把模式修复当作完全不改变行为的小配置修复。位置均为simulation/stages.py。

| order / stage | 读取 | 产出与副作用 |
|---|---|---|
| 25 TectonicMovement；:490 | 地图/压力、species/habitats、tectonic服务状态 | tectonic_result、terrain/pressure反馈；upsert tiles、海陆重分类、强制迁移、资源脉冲、SSE |
| 100 GeneFlow；:1713 | species_batch、genus分组与repository | gene_flow_count；改物种基因状态并upsert、SSE |
| 105 GeneticDrift；:1765 | 小种群population与hidden_traits | 随机选择/高斯修改trait；drift_count、species upsert；全局random |
| 110 AutoHybridization；:1810 | species、habitat/traits、杂交配置、tensor候选 | 随机成功/杂交创建、auto_hybrids与新species；AI/规则service、仓储和事件副作用 |
| 115 SubspeciesPromotion；:2167 | alive species.subspecies的created_turn与genetic_distance | 仅计算promotion_count并记录日志；没有实际晋升/新物种创建或upsert |
| 155 VegetationCover；:2743 | DB tiles/latest habitats/species | 植被覆盖更新、upsert tiles、SSE；此时报告已经生成 |
| 164 EmbeddingIntegration；:2806 | combined_results、species_batch、embedding开关 | extinction/turn-end hooks、embedding_turn_data；向量/分类索引与服务状态 |
| 166 EmbeddingPlugins；:2856 | engine.embedding_service、ctx、插件配置 | tensor bridge重置/同步、插件on_turn_end、各插件索引；条件不满足会直接跳过。YAML embedding_hooks写165，但类order实际166 |
| 175 ExportData；:3042 | report、species_batch | exporter.export_turn写文件；SSE；不是V2 delta |

独立ResourceCalculation读取tile气候/资源与species消费，写ctx.resource_snapshot及ResourceManager状态；EcologicalRealism读取species/habitats/压力，生成疾病/Allee/适应滞后/互利等修正并更新其服务状态。二者当前不在标准调度链；未来接入需先解决与现有生态/资源调用重复的问题。

## 5. 前端、保存与异步链

当前活跃前端是 main → QueryProvider → App → Session/UI/GameProvider。GameProvider本地state/手动fetch管理地图、报告和species；React Query只覆盖部分模块。活动 App 导入同名旧 SpeciesPanel.tsx，并未自动使用目录里的拆分新组件。

回合按钮：App.tsx:225 await POST /turns/run → addReports → refresh map/species/queue → 清独立lineage cache。SSE主要由 TurnProgressOverlay.tsx:252 消费以显示进度/token，turn_complete只清理进度UI，不统一invalidate domain queries。创建物种成功回调只刷新map/queue，漏掉species。

保存：SaveManager获取全部species/tiles、最新habitats、最多1000份TurnLog、genus，完整序列化到game_state.json.gz（或JSON）；另外写embedding/taxonomy/events/energy/progression文件。不是每turn必定全量autosave，但每次save都是完整当前世界与部分历史导出；运行中habitat历史与每turn报告另会增长。读取清空并逐段恢复当前DB，尚无新旧世界原子切换。

事件：当前SSE消息是session内存队列和callback，不是持久化的WorldEvent log；NarrativeEngine另有自己的events列表，TurnLog又保存报告。三者没有统一的event identity、version、因果引用或replay cursor。

异步：speciation create_task、model_router的并行任务、streaming heartbeat、BackgroundTasks autosave各有生命周期，尚无统一world/timeline/version/幂等键。跨回合/读档风险及AI数值写入位置详见问题文档。

## 6. 后续设计文档

- [TARGET_PIPELINE.md](TARGET_PIPELINE.md)：新旧Stage映射、数据所有权、模拟顺序与生态约束。
- [MIGRATION_PLAN.md](MIGRATION_PLAN.md)：baseline修复、Foundation→Safety→Persistence→逐Stage迁移、每批gate。
- [DATA_MODEL_V2.md](DATA_MODEL_V2.md)：Context/Result/Event/Version/Seed、数值状态与trace。
- [SAVE_FORMAT_V2.md](SAVE_FORMAT_V2.md)：独立格式标识、checkpoint/delta、原子发布、timeline与replay。
- [AI_JOB_ARCHITECTURE.md](AI_JOB_ARCHITECTURE.md)：任务状态机、strict schema、CAS/幂等与stale拒绝。
