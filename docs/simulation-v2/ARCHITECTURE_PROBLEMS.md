# Architecture Problems — 风险与证据

源码基线：4d1045bfecd412aa26dd55e506c8416b83ac984b。日期：2026-09-29。本文描述当前代码，不把设计建议写成现有能力。运行顺序与逐 Stage 输入/输出/副作用见 [CURRENT_PIPELINE.md](CURRENT_PIPELINE.md)；实测命令、日志与限制见 [AUDIT_BASELINE.md](AUDIT_BASELINE.md)。路径均相对仓库根，行号对应基线。

证据等级：**实测**表示受控探针或测试复现；**源码**表示直接可见的调用/数据流；**推演**表示由这些路径产生的调度或规模风险，未在真实玩家世界复现。没有执行真实 LLM、真实世界长跑或破坏性读档测试。

## 1. 优先级与阻塞项

| 优先级 / 问题 | 证据与实际影响 | 第一条修复边界 |
|---|---|---|
| P0 运行闸门失效 | 实测：第二个 /turns/run 请求被400拒绝，却在异常处理清掉第一个请求的 running 标志；第一个仍pending。simulation.py:304–308,411–413 | 用拥有者明确的原子 acquire/release；所有世界写入口共享 coordinator |
| P0 没有回合原子性 | 源码：各repository单独commit，Pipeline continue_on_error，失败仍递增turn；局部成功已经外泄 | candidate + validation + 单一提交边界；不能仅补一个总try/except |
| P0 世界身份/版本缺失 | 源码：当前表无world/timeline/generation；load/new先清库再恢复，admin reset也可改当前库 | world-scoped version/CAS，离线导入后切换head，旧worker结果fence |
| P0 张量空间契约不一致 | 源码：PressureTensor缺地图时64×64，TensorStateInit在MapState存在时64×128；真实地图逻辑128×40，MapState没有width/height | 统一grid schema、axis/topology与shape validation；实际kernel失败/压力错位后果待fixture复现 |
| P0 可复现闭包不完整 | 源码：global random/NumPy、进程hash、时钟seed、服务缓存与不完整save | SeedManager + manifest + 显式状态；保存所有影响下一回合的状态 |
| P0 AI可改变模拟字段 | 源码：分化/造物/杂交接受AI trait、habitat、形态等并upsert；不是纯叙事 | 数值Proposal先确定；AI schema仅允许presentation；job CAS/幂等 |
| P0 基线gate不绿 | 实测：后端collect失败；frontend tests/lint/typecheck失败；legacy runner不是真旧引擎 | B0–B3先修测试入口、冻结真实oracle，不能大重构后再补回归 |
| P1 多个人口权威源 | 源码：tensor ecology→CPU population update→tensor rescale→DB sync；报告在最终同步前生成 | 单一人口ledger/reducer；报告/metrics读同一candidate revision |
| P1 模式和stage声明失真 | 实测：四模式均22阶段；源码：YAML order未用于实例，speciation实际125而非120 | 明确config优先级与stable IDs，冻结各模式stage/dependency清单 |
| P1 存档与事件不可回放 | 源码：多事务读、多文件覆盖、内存破坏性SSE队列；无checkpoint/delta | immutable chunks + commit ledger/outbox + 独立subscriber cursor |
| P1 UI存在双重状态系统 | 源码：QueryProvider与手动state/fetch/缓存混用，成功后遗漏失效 | world/timeline/version query keys，提交事件统一invalidate |

P0标记表示开始长跑/并行世界前必须解决，不表示本轮已经修复。并发、回合提交和AI边界优先于新增生态功能。

full模式还有两处不能按名字推定功能完整的接线：SubspeciesPromotionStage仅统计可能晋升数量，没有创建/更新物种；EmbeddingPluginsStage实际order166（YAML写165），且查找engine.embedding_service，而当前engine只声明self.embeddings。即使修复模式选择，也需要逐项验证这些阶段真的生效。

## 2. 不合理耦合

| 耦合 | 具体位置 / 后果 | 目标归属 |
|---|---|---|
| stage→engine→服务→global repository | simulation/stages.py、tensor_stages.py直接读写数据库/服务状态；StageResult只是成功/异常/耗时包装 | stage仅读Context并返回delta；CommitService独占发布 |
| 模拟与前端进度 | SimulationContext持有event callback；多个stage在中间写入前后emit_event | progress独立观测；已发生事件来自事务outbox |
| 模拟字段与语言模型 | services/species/speciation.py:1478、2727；species_generator.py:223；hybridization.py:277 | 核心物种数值与NarrativeAnnotation分开 |
| 文本语义与数值适宜度/生态位 | suitability、niche和embedding服务参与数值计算；名称/描述更新可能触发向量变化 | 固定版本、内容hash的显式输入；AI文案不得暗改生态 |
| 全局环境与地理/资源/栖息地 | MapEvolution同时改地图、状态、海陆与habitat；PopulationUpdate末尾再更新资源 | environment/resource/population各有唯一写权限和时间点 |
| 注入容器与兼容单例 | main.py创建app.state.container；stages仍get_container；SaveManager直接import模块repo | world-scoped adapter，最后移除兼容全局入口 |
| 配置API与legacy引擎 | api/analytics.py:294,302动态import api/routes.py，:310调用其simulation_engine.reload_configs | 配置命令交由当前coordinator；不能直接删除旧文件 |
| world运行与save UI名称 | autosave后台任务读取当时session save_name/engine turn，未捕获不可变commit | 保存指定commit identity，不根据完成时的活跃页面猜世界 |

api/routes.py默认未挂载，但上述配置路径仍会初始化其全局服务。simulation/legacy_engine.py只返回占位报告；二者都不能凭“legacy”名字假定为独立、可靠的旧模型。

## 3. 隐式全局状态与恢复缺口

| 状态 | 位置与生命周期 | 问题 |
|---|---|---|
| SQLModel engine/settings、模块repositories | core/database.py:10–40，repositories/*.py | 单一当前数据库；注入多个wrapper不等于多个隔离世界 |
| 兼容_container / _session_manager | core/container.py:157–207；core/session.py:404–454 | 与app.state注入栈并存；同进程初始化顺序可改变行为 |
| engine.turn_counter、_event_callback | simulation/engine.py；api/simulation.py:334起 | 两个任务可互换回合号/事件目的地，非任务局部状态 |
| ResourceManager._tile_states/_snapshot/_last_turn/_event_pulses | services/ecology/resource_manager.py:108起 | 资源再生/过采与事件脉冲未作为完整存档状态闭包导出 |
| 人口历史cache | services/analytics/population_snapshot.py:20–70 | 模块字典，每species最多100项；影响趋势/灭绝判断，重启/加载不能从存档原样恢复 |
| species/niche/embedding/生态计算缓存 | species_cache、suitability/niche、tensor ecology与compute globals | 缺world/version/cache-key；新世界相同lineage code可碰撞 |
| speciation deferred requests/冷却、habitat migration cooldown | 长寿命service字段 | load/reset清理靠多处手工调用，无法证明snapshot闭包 |
| plugin registry/instances、tensor collector | embedding_plugins注册表、tensor metrics/compute | 插件顺序与导入GPU副作用；多实验不能共享可写实例 |
| tensor_decline_streaks | tensor_stages.py:419,512–529动态挂ctx；engine.py:285每回合新建ctx | intended跨回合趋势在新context中丢失，与长期冷却状态寿命不一致 |
| SSE队列、NarrativeEngine events | session内存Queue与叙事服务列表 | 与TurnLog三份历史，无共同identity或完整恢复策略 |

应建立状态清单：凡影响下一回合的量都属于snapshot；纯派生缓存带输入版本可重建；UI临时进度不入数值快照。不要把“缓存”名称当作可以不保存的证明。当前session源码也明确只支持单worker；多worker会形成进程控制状态分裂、数据库仍共享的冲突。

## 4. 跨 Stage 重复计算与写入

1. **人口生命周期重复。** tensor/ecology.py内已有死亡、扩散、迁移、繁殖与K/竞争；stages.py:1123起PopulationUpdate又从initial_population构建结果、执行CPU繁殖/竞争/K，并重分配tensor；tensor_stages.py:673起最终同步再写species/habitats。算法顺序和舍入均可能让报告、tensor总量与DB不一致。
2. **habitat重复查询/持久化。** TieringAndNiche取all_habitats/all_tiles，TensorStateInit再逐物种取最新habitats；SaveMapSnapshot写habitat，TensorStateSync再写。需要按species/tile/turn核对去重与权威来源，而非只是加缓存。
3. **适宜度/承载力多实现。** geo/suitability_service、tensor kernel、ResourceManager、reproduction各自有适宜度/K相关逻辑。不同量纲或时间点不应直接合并，先建立命名输入输出fixture。
4. **资源更新时点过晚。** ResourceCalculationStage存在但未接标准模式；PopulationUpdate:1371确实调用_update_resource_dynamics。tensor生态此前读取tile.resources/100，没有统一消费当轮current_npp。不能简单判断“无NPP”，也不能判断能量闭环已完成。
5. **食物网/traits/species多次刷新。** FoodWeb维护后重读species，Fetch/分化/报告/同步又读改相同对象；新子种可能不在回合开始建立的tensor axis中。需要明确新增实体何时进入数值轴。
6. **历史与解释重复推断。** TurnReport、NarrativeEngine、SSE、前端timeline分别归纳历史；沒有可引用的WorldEvent/causal IDs。不同展示可能在不同revision计算同一指标。

性能收益目前只是假设。先记录每阶段数据库调用数、占据格数、数组分配与profiler，再比较抽离后的数值和耗时。

## 5. AI 直接或间接改变世界的位置

| 路径 | AI允许影响的当前字段 / 落库处 | 审计判断 |
|---|---|---|
| 玩家/初始造物 | species_generator.py:193–270采用habitat、形态、abstract/hidden traits、prey等；api/species.py:315–400调用后upsert | 可达；LLM结果不只命名。generate fallback存在于:338起，但主错误分支raise，未调用它 |
| 回合分化 | speciation.py:1287–1494批量解析、rules.validate_and_fix、_create_species；:2727–3212应用trait_changes、器官/形态、trophic、habitat/diet/prey | 可达；局部clamp/tradeoff不能替代严格schema或模拟先决定 |
| 普通杂交 | hybridization.py:277–387应用AI trait_balance/trophic/habitat并返回物种；divine.py随后upsert | AI路径意图明确，但:426引用未定义sp1/sp2，异常捕获后实际走规则fallback；不能将其当已正常工作的AI链 |
| 强行杂交 | hybridization.py:1033–1178接受trait bonuses/penalties、fertility/stability、habitat等；divine.py:472–545提交 | 仍需移至确定性proposal/纯文字enrichment边界 |
| 描述/embedding增强 | speciation.py:1725–1747增强后upsert描述/向量 | 当前description_enhancer是规则/模板队列，不是LLM；向量若用于生态仍需版本控制 |
| 报告/叙事/focus/critical | BuildReport与analytics服务生成文本/高光 | 主要属于允许的叙事职责，但需job身份、证据来源与成本/超时边界 |

当前死亡和主要繁殖计算仍在数值服务中，没有证据表明ModelRouter直接决定所有死亡。问题是AI改变影响后续生态的数值输入与新Species，而不是整个项目“都用LLM模拟”。

model_router.py接受宽松JSON/文本；streaming_helper可返回partial内容；没有按能力区分的Pydantic严格输出合同。分化已有request_turn检查（speciation.py:1411–1417），因此不是完全没有stale检查；它缺少world/timeline/generation/version，且与跨回合deferred重试相冲突。

## 6. 不可复现的随机与顺序来源

| 来源 | 证据 | 影响/处理 |
|---|---|---|
| Python random全局流 | engine.py:198初始化地质seed；geo/map_evolution.py:109,139,203；gene_diversity多处 | 调用顺序、其他世界初始化可改变下一次抽样 |
| NumPy全局流/重置 | tectonic/plate_generator.py:48–49及多处、mantle_dynamics.py:183、geological_features.py:78 | 隔离任务会互相改变随机流，必须局部Generator |
| 时钟派生seed | geo/map_manager.py:1213–1222缺map_seed时用时间并重置global RNG | 相同输入无固定world seed时地图不同 |
| Python hash进程盐 | species_generator.py:45,158,342,701；speciation.py:1194,1884；gene_activation.py:896；core/seed.py:1244 | 不同PYTHONHASHSEED/进程不稳定；core/seed.py是初始化逻辑，不是合格SeedManager |
| UUID随机实体ID | species/organ_evolution_service.py:460,465 | 器官身份不同会改变序列化/后续索引；模拟实体需确定性ID |
| 预测随机噪声 | analytics/evolution_predictor.py:396附近 | 展示预测也需明确seed/模型，不能当可复现simulation结果 |
| GPU浮点/轴顺序 | tensor/taichi_hybrid_kernels.py等 | 存在按坐标/索引的sin伪噪声；未发现ti.random，不能误称全部GPU噪声为随机抽样。归约、精度与索引变动仍需测定 |
| AI/并发完成顺序/未排序查询 | provider响应、list+分配lineage、模块全局任务 | 文本输出隔离；identity/事件排序由确定性命令与唯一约束负责 |

simulation/snapshot.py保存部分Python random状态，但不是完整NumPy/Taichi/服务/配置闭包。单独调用random.seed(42)不足以证明整个世界确定性。相同backend的重跑与跨硬件容差要分开；replay使用已提交delta，不要求重新问LLM或重跑GPU。

## 7. 跨回合污染的异步路径

| 路径 | 已有行为 | 风险与证据等级 |
|---|---|---|
| /turns/run闸门 | 有running检查，但异常路径无条件释放 | 实测第一任务pending时flag变False；第三个任务/读档可进入的后果为源码推演 |
| 分化batch/内共生 | speciation.py:1952,1991 create_task，:2006 gather等待 | 不是全部fire-and-forget；但仍无世界版本、任务lease、统一取消。heartbeat回调另有未追踪create_task |
| deferred retry | _queue_deferred_request保留request_turn；下一回合先检查旧turn丢弃 | 源码：重试可能在达到fallback阈值前被丢弃；持久job应分清attempt与原proposal identity |
| abort/skip | analytics.py:161–196标志；reset_client异步函数未await；skip无统一消费者 | 源码：UI取消不代表provider/模拟停止，不可据此开放新世界写入 |
| streaming heartbeat | streaming_helper.py后定义的invoke_with_heartbeat覆盖前者；:445 wait_for(to_thread(router.invoke)) | 超时不终止线程。线程本身未直接写DB，不能仅凭其继续就断言必有迟到upsert；返回路径/旧引用仍须fence |
| autosave | simulation.py:385起BackgroundTasks，任务执行时读session/engine | 推演：新回合、load/delete可改变其读取的数据/目录；需固定commit与原子文件发布 |
| 玩家造物/杂交 | list existing codes→await AI→upsert，部分只有非原子的not-running读检查 | 推演：同lineage分配、重复扣能量、世界切换后的旧对象写入；无unique idempotency约束 |
| 前端请求/SSE | load/new、轮询和异步fetch缺world generation响应门禁 | 推演：旧响应覆盖新页面；SSE本身无强制world/job/version envelope |

SSE另有确定的多消费者问题：session.py:265–273破坏性drain同一队列；analytics.py:218–250每个订阅者都消费它。因此多个窗口分走事件，而不是各自收到完整广播。队列>1000时部分事件被丢弃；无durable cursor无法补发。解决方案见 [AI_JOB_ARCHITECTURE.md](AI_JOB_ARCHITECTURE.md)。

## 8. 存档膨胀与一致性

- SaveManager:156–245每次读取完整Species/tiles、**最新**habitats、最多1000份TurnLog、genus，写game_state.json.gz。并非把全部历史habitats都放进该JSON，也并非每turn必定autosave；准确问题是每次save重复全量当前世界与报告窗口。
- embeddings/taxonomy/event_embeddings另用JSON侧文件；宽Species含器官、trait、关系、history等JSON，非空内容每档重复。压缩减少字节，不解决重复语义和随机访问。
- 运行DB的HabitatPopulation/TurnLog持续追加；同回合重复写入缺唯一键会放大记录。SavePopulationSnapshotStage实际只写bounded内存cache，不能误算作每turn DB PopulationSnapshot膨胀。
- save读取各自独立repository事务，再分别覆盖文件，metadata最后写；无checksum/临时文件原子rename/commit manifest。同一档可能混合多个时间点，失败可能留半档。
- load_game:501–571先clear，再逐表恢复；new也先清库再可能调用AI初始化。不是先完整验证后切换世界；错误可能破坏当前可用状态。
- default旧档已经写version:"2.0"（save_manager.py:208）。新checkpoint/delta必须用独立format_id，不能靠同一个2.0数字猜格式。
- _verify_save_integrity:397–401要求game_state.json，但默认写.gz；debug校验会错误报缺文件。load的turn推断会取多个来源，可能掩盖原档不一致。

当前没有真正的checkpoint+delta、CoW timeline、任意turn地图/traits/food-web重建。旧TurnLog与MapHistory的文本变化不足以恢复未保存的历史状态；未来import不得虚构这些历史。详见 [SAVE_FORMAT_V2.md](SAVE_FORMAT_V2.md)。

## 9. 最大 God Files

基线按wc -l计，不含依赖/generated文件；完整清单见 [REPOSITORY_INVENTORY.tsv](REPOSITORY_INVENTORY.tsv)。行数用于定位职责集中，不单独作为删除依据。

| 文件 | 行数 | 拆分方向 |
|---|---:|---|
| backend/app/services/species/speciation.py | 6692 | 候选/隔离、数值proposal、AI jobs、创建提交、叙事 |
| backend/app/api/routes.py | 4745 | 清理活动配置依赖后移除兼容路由栈 |
| backend/app/tensor/taichi_hybrid_kernels.py | 3387 | 按生态算子分组，保留axis/精度合同 |
| backend/app/services/geo/map_manager.py | 3326 | 地形生成、环境演化、分布与持久化 |
| backend/app/simulation/stages.py | 3234 | 每领域stage + adapters，去repository/SSE副作用 |
| frontend/src/components/SpeciesPanel.tsx | 2576 | 活动旧组件；先统一数据层再接拆分组件 |
| frontend/src/components/GenealogyGraphView.tsx | 2535 | 查询/图布局/交互分离 |
| frontend/src/components/HybridizationPanel.tsx | 2117 | 活动旧组件；独立命令状态与展示 |
| frontend/src/components/TurnProgressOverlay.tsx | 2029 | SSE连接/事件路由/进度与叙事展示 |
| backend/app/ai/model_router.py | 1858 | provider、schema/validator、retry与task lifecycle |
| backend/app/tensor/ecology.py | 1710 | 生态flow/reducer；消除与CPU重复生命周期 |
| backend/app/services/system/save_manager.py | 1081 | legacy importer、checkpoint/delta、timeline、metadata |

此外trait_config.py 1894行、models/config.py 1849行、core/seed.py 1624行；配置数据与算法需分开。CSS也有数千行文件，但风险优先级低于事务、并发与模型所有权。

## 10. 前端状态与可解释性缺口

React Query已安装且main.tsx挂QueryProvider；不能把迁移描述为“首次引入”。GameProvider仍持本地map/reports/species并手工fetch，useSpeciesQuery等并非所有活动页面的来源。App同名import命中巨型SpeciesPanel.tsx/HybridizationPanel.tsx，不是目录中拆分的新组件。

- App.tsx:231–241、289–298在完成后手工刷新map/species/queue与lineage cache，未覆盖food web/history等所有关联查询。
- TurnProgressOverlay.tsx:252–566消费SSE，complete分支主要清进度，没有统一领域query invalidation。
- GameProvider中speciesRefreshTrigger定义/导出，但trigger调用未接通；旧SpeciesPanel依赖该数字。ModalsLayer.tsx:318–326创建物种成功只刷新map/queue，遗漏species。
- api/species.ts的_lineageCache独立于Query；history限制窗口、AI timeline era/narrative缓存、轮询各自维护状态。load/new不能只调用几个refresh保证全页属于同一世界。
- 当前历史地图主要展示map_changes文字，没有任意turn tile重建；“为什么进化”需要mortality/selection/trace证据，不能只把LLM说明当因果。
- GeneLibrary/GeneLibraryModal.tsx:424–440用分类、ID hash和索引合成“模拟t-SNE”坐标；:589–597用speciesCount/(speciesCount+10)显示世界覆盖率、以固定25%/50%/25%分布显示等级。它们是展示启发式，不是测得的遗传距离/真实覆盖率；解释UI需标示或替换成同revision的真实统计。
- AIAssistantPanel、SpeciesAITab、NicheCompareView等叶子组件仍直接请求embedding API；GenealogyView节点详情请求没有选中版本检查。前端迁移必须包括这些叶子，不能只改App或QueryProvider。

先统一数据身份与提交事件，再做时间机/并行实验UI。具体query key、cursor和回放隔离在 [TARGET_PIPELINE.md](TARGET_PIPELINE.md)。

补充运维与旧文档：根/后台diagnose_turn.py硬编码8000，而默认服务端口是8022；脚本会POST真实回合，不能作为只读健康检查。optimize_database.py的cleanup/--all会删除历史habitats并压缩旧档，不能用于尚未迁移的replay验收。start/stop.ps1会按端口结束进程，未作为本轮验证入口执行。旧Embedding/React Query/legacy完成清单互相不一致，验收应以调用图、fixture和测试结果为准。

## 11. 当前测试覆盖缺口

已有tensor state、tradeoff、pressure bridge、metrics、speciation monitor、插件/生态拟真等测试；不能称完全没有测试。当前实测91 tensor单测通过，simulation 73通过/2失败，frontend 11通过/3失败；全后端收集另有2错误，frontend lint/typecheck不绿。详见基线和原始日志，不提供未测覆盖率。

| 缺口 | 为什么已有测试不能替代 | 迁移gate |
|---|---|---|
| 全流水线确定性 | 固定random.seed或单kernel相等不包含配置、全局服务、DB与AI | 跨进程/hashseed/任务顺序、save-resume对照 |
| 单一回合原子性 | stage成功测试不覆盖中间upsert后失败 | 每提交/文件写入边界故障注入，head与状态不部分推进 |
| 版本/幂等/异步取消 | 没有world generation/AIJob可测合同 | barriers控制late result、cancel、load、lease重领、重复命令 |
| 人口/能量完整账本 | 局部不变量不覆盖多个stage重算和sync | 全回合N_next恒等式、迁移守恒、死亡归因、分化亲子转移 |
| 旧档兼容/历史恢复 | 当前save utility和报告对比不是所有格式fixture round-trip | plain/gzip旧档导入、损坏拒绝、任意turn replay hash |
| timeline/多实验隔离 | 当前模型无timeline作用域 | 分支CoW、父后续变更不污染子、并行与串行一致 |
| 长期稳定 | 短单测不揭示缓存、队列、save增长、物种爆炸 | mock AI 100/500/1000 turn、RSS/GPU/save与stage曲线 |
| 前端领域一致性 | 只有少量FoodWeb与split-panel hook测试 | App/GameProvider、SSE补发/重复、query失效、load后旧响应、replay/live隔离 |
| 真正回归oracle | legacy_engine占位；regression_test跑完后重复读最终DB作各回合快照 | 对当前生产pipeline逐回合捕获，old/new独立workspace比较 |

库内没有已配置的后端lint/typecheck与自有CI gate；依赖未锁且缺直接使用的PyYAML/pytest-asyncio声明。先修baseline，再新增Foundation的纯单元、属性与契约测试；不能在已有基线失败时宣称“每阶段tests/lint/typecheck均通过”。

下一步按 [MIGRATION_PLAN.md](MIGRATION_PLAN.md) 的B0–B3与Foundation执行。本批未改变任何运行时代码、存档格式或legacy路径，以上风险仍存在。
