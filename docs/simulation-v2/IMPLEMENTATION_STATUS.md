# 实施与逐步验收记录

审计基线：48dfb31。以下状态描述实际实现，设计文档中的目标不视为已完成。每批独立检查、记录、提交，随后继续下一批。

| 步骤 | 状态 | 验收边界 |
|---|---|---|
| Audit / Design | 已交付 | 七份文档、执行链映射、499文件清单与失败基线 |
| B0 后端测试入口/依赖 | 已通过 | 全量收集、隔离DB/网络、65包hash锁与干净环境安装 |
| B1 前端基线 | 已通过 | 14 tests、lint无错误且无新增warning、typecheck、build |
| B2 Stage配置合同 | 已通过 | 明确模式优先级、稳定ID、错误依赖拒绝、四种清单 |
| B3 回归捕获/旧档fixture | 工具已通过，生产长跑oracle待建立 | 逐回合捕获、结构差异失败、JSON/gzip旧档roundtrip |
| Foundation | 已通过 | TurnContext / StageResult / WorldEvent / WorldVersion / SeedManager |
| Async Safety | 新路径合同/持久任务已通过，旧AI写者逐Stage迁移 | 运行协调、AIJob、幂等、版本/lease、stale拒绝 |
| Persistence | 核心存储与旧档导入已通过 | checkpoint/delta、原子提交、timeline、replay、旧档导入 |
| Environment / Resources | CPU参考模型通过，旧气候/资源oracle通过 | 气候/地质/水文/biome、NPP与资源再生；不是旧GPU整链数值等价 |
| Ecology / Movement / Population | CPU参考模型通过 | 动态K、竞争/捕食、守恒迁移、死亡与繁殖ledger；疾病仅有密度压力接口 |
| Evolution / Speciation | 26stage参考配方通过 | 压力/梯度/代价/变异/漂变/基因流、条件分化与灭绝；默认长测未自然分化 |
| Explainability / Emergence | Trace/28stage生态反馈已通过 | 数值原因与化石已实现；协同演化机制不等于已证明长期军备竞赛 |
| Player Experience | 实验室接入与逐项验收中 | 事件驱动数据层、时间机、分支对比；批量实验/情景编辑继续推进 |
| Ecosystem / Legacy removal | 待实施 | scenario/mod/stage SDK、权限与兼容、最后移除legacy |
| Long-run acceptance | 17/26/28stage均完成1000turn | mock AI 100/500/1000 turn；单seed证据不等于生态标定或所有规模稳定 |

## B1 — 前端合同与检查基线

- Changed files：frontend/package.json；GlobalTrendsPanel/{GlobalTrendsPanelNew.tsx,hooks/useTrendsData.ts,types.ts}；SpeciesPanel/components/SpeciesListHeader.tsx；queries/{useFoodWebData.test.tsx,useTrendsData.test.ts}。
- New files / Removed legacy code：无。没有替换活动页面或删除兼容入口。
- 修复：FoodWeb fixture按真实nodes/links；具名test wrapper；分化使用new_lineage/parent_lineage；移除不存在的Recharts导出；nullable select规范；缺失湿度使用null/暂无数据，不生成零湿度曲线。
- Tests added：在既有测试中增加食物网方向/边权、筛选后无悬空边和缺失湿度null断言。
- Tests passed：npm run test:run，14/14；npm run typecheck；npm run build；npm run lint为0 errors/174 warnings，相比原176没有新增warning。主代理逐文件审阅diff与后端schema。
- Known issues：旧lint warnings与SettingsDrawer混合静态/动态导入的build提示仍在；未做浏览器E2E。
- Performance impact：只改最小展示/类型合同；未做运行性能压测。
- Save compatibility：后端存档路径未改；另批次新增旧档回归测试。
- Next：完成B0/B2/B3验收后建立Foundation。

## B0 / B2 / B3 — 后端基线、配置与旧档回归

- Changed files：pyproject.toml、StageLoader/YAML与相关测试、ConfigService及API合同测试、插件fixture/ancestry_embedding、3个带旧时间戳的models、regression_test.py、niche浮点断言与GPU测试标记。
- New files：requirements-dev.lock、TESTING.md、顶层conftest、安全测试、插件/物种测试包入口、Stage配置测试、check_v2.py、逐回合捕获测试、legacy_world_v2.json及JSON/gzip roundtrip测试。
- Removed legacy code：无。旧引擎/存档仍保留。
- Tests added：配置四模式12/22/31/13精确清单，非法mode/未知或重复stage/依赖拒绝；默认配置缓存与后来出现的配置文件；逐回合捕获/缺失报告/结构差异；旧JSON/gzip；测试环境DB/网络隔离。
- Tests passed：共享工作区全量427 passed、0 skip、9 warnings；88项Stage定向、55项API/niche、9项捕获/旧档测试包含在全量中，不能重复累加。65包hash锁在干净Python3.12/Linux环境安装、pip check与收集通过。
- 审查修复：SQLModel新版本默认UTC字段拒绝旧naive时间戳，显式SQLAlchemy DateTime保留旧存储语义；Ancestry在向量填充前过滤空向量导致全部跳过，改为收集候选后批量embedding；niche断言按float32一ULP检查，未放宽生态阈值。
- Known issues：旧tensor包导入仍初始化GPU，-m not-gpu不等于无GPU可收集；deprecated warnings保留。B3目前修复捕获工具并建立旧档fixture，尚未证明整个旧GPU pipeline跨进程确定性；后续迁移的数值fixture与100/500/1000长跑分别验收。
- Performance impact：未做运行压测；默认standard仍22stage；显式full现在可调度31stage，增加的运行成本尚未测。
- Save compatibility：原version 2.0普通JSON与gzip fixture的物种人口、食物网、地图、habitat与turn读取/保存/重载通过，源fixture保持不变；不是所有玩家档的覆盖证明。
- Next：纯Foundation接口、深immutable/Seed/StageDelta验收，再进入AI任务与持久化。

## Step 3 — Foundation 验收

- Changed files：simulation/__init__.py 改为惰性兼容导出；pyproject Ruff目标保持项目Python 3.11最低语法。
- New files：simulation/v2/{context,contracts,events,version,seed,values,reducer,pipeline,__init__}.py；tests/v2/{test_foundation,test_pipeline}.py。
- Removed legacy code：无。现有运行引擎仍默认启用，新接口暂未切换生产路径。
- 实现：深不可变快照/bytes数组、稳定内容哈希、完整world/timeline/generation/revision、无全局状态的按实体counter RNG、显式读写合同、稳定DAG、旧值哈希补丁、候选状态归约与fail-closed执行。
- Tests added / passed：129项纯Foundation测试；后端整体验收566 passed、0 skipped、9既有warnings（含另批运行令牌10项）；新11个源/测试文件Ruff与strict mypy通过。
- 独立审查修复：前stage结果/计时/metrics绕过声明读范围；字段带点造成授权歧义；数组替换擅自改变shape/dtype。相应最小复现全部转为回归测试。
- Known issues：Python插件不是安全沙箱；第三方Stage仍需要后续注册/权限策略。当前数组扩容必须另建显式axis迁移合同。未接入durable Commit，不能把candidate当已发布世界。
- Performance impact：129项纯接口测试约0.5秒；copy/freeze/hash会复制数据，生产tile规模待长跑测量；不宣称GPU与CPU逐bit相同。
- Save compatibility：未改旧save reader/writer；JSON/gzip旧档测试继续通过。
- Next：持久AI任务/原子版本校验与checkpoint+delta落盘。

## Step 4a — 旧运行入口最小并发补丁

- Changed files：core/session.py、api/simulation.py、api/analytics.py。
- New files：tests/test_running_guard.py；Removed legacy code：无。
- 实现：acquire/release owner令牌在锁内原子操作；被拒绝的并发请求不会清除首请求运行状态或安排autosave；旧令牌不能释放新lease；finally释放；abort正确await HTTP client reset。
- Tests added：10项并发/异常/取消/跨线程/ABA/abort测试。定向49通过，包含在全量566项中；新测试Ruff/strict mypy通过，主代理审阅全部diff。
- Known issues：取消route不等于同步worker停止；此补丁不是world fencing。成功请求的旧autosave仍读取live state，后续迁移到immutable commit。abort接口现在明确表示仅重置AI HTTP连接。
- Performance impact：每次run增加两次短锁；未改数值计算；无性能压测。
- Save compatibility：旧保存格式与读取保持；Next：新的持久AIJob和版本事务。

## Step 4b — AI合同、worker与provider适配器

- New files：app/ai/jobs/{models,schemas,worker,provider,__init__}.py；tests/ai/jobs/{test_contracts,test_worker,test_provider}.py（文件列表以提交为准）。
- Changed files / Removed legacy code：未修改ModelRouter等旧实现，未删除legacy。适配器通过注入已有acall_capability复用配置。
- 实现：4类Pydantic strict/extra-forbid叙事schema；冻结目标/器官/事件验证；完整WorldVersion、scoped幂等identity；一次repair、timeout、有界attempt/fallback、取消传播；结构化JSON provider适配与安全错误分类。
- Tests passed：合同/worker46项+provider57项，共103项离线测试；Ruff、format、strict mypy通过。无真实LLM调用、无外部费用。
- Known issues：重试worker由调用方调度，尚未把旧speciation/hybridization等AI写者改接；不能据此宣布旧活动世界已与LLM完全解耦。当前返回fallback记录，未来UI按事件提供默认模板。
- Performance impact：provider异步调用不参与数值hash；无真实token/延迟基准；新输入/输出均限制64KiB。
- Save compatibility：旧档不存这些job，不伪造恢复；新任务与下述SQLite提交一同持久化。
- Next：逐领域替换旧writer，优先迁移可等价提取的环境kernel。

## Step 5 — Persistence / durable AI transaction 验收

- New files：app/storage/{database,codec,history,store,objects,jobs,observations,legacy,__init__}.py；tests/storage/{test_objects,test_world_store,test_jobs,test_command_inputs,test_legacy_import}.py；scripts/benchmark_v2_storage.py；evidence/storage-stress-1000.json。
- Changed files：v2/pipeline.py将active_events固定为输入，输出事件只归StageResult，以免输入身份混入输出；对应独立测试调整并保留隔离断言。
- Removed legacy code：无。旧JSON/gzip reader/writer继续可用，新格式magic为clade.checkpoint-delta，不混淆旧version=2.0。
- 实现：SQLite WAL/FULL/外键事务；command完整输入与hash；head CAS；周期checkpoint+实体delta+NPZ分块内容寻址；完整commit元数据checksum；fork共享祖先/对象；generation rewind；固定Turn.end_version；只读replay；独立cursor outbox；同revision事件/metrics/Stage profiler。
- AI事务：world commit同事务入队，lease/fencing重领，finish同事务schema+scope+version校验，仅写annotation/独立narrative revision；stale不写展示或世界；APPLIED重投保留终态；取消与完成并发只有一个结果。
- 旧档导入：独立临时目录校验/replay后发布，源JSON/gzip不修改；人口/栖息地/食物网/地图严格校验；旧名称/历史完整归档一次；缺状态明示warnings，不伪造历史；原子不覆盖已有目标。
- Tests passed：当前后端全量864 passed、0 skipped、9既有warnings；随后新增1项profile/event/metric一致性定向测试通过（后续全量再合并计数）。storage195项包含对象89、world29、jobs30、command3、legacy44；新增profile测试后196。迁移33文件Ruff/format/strict mypy通过；benchmark脚本另行检查。
- 独立审查修复：active_events/jobs未纳入命令身份、turn元数据损坏、损坏payload异常不一致、历史祖先循环；追加固定turn边界和完整command输入，避免replay与resimulation混淆。
- Performance impact：16×512整数数组、1000次事务、每25回合mock叙事提交；两次运行100/500/1000检查点hash完全相同。验证轮存档415675/1589298/2866920 bytes，末100回合p50=45.06ms、p95=77.11ms（含tracemalloc与读取校验）；当前RSS约51.7/52.8/53.5MB，不能把这一个固定规模实验当作已排除所有泄漏。512种循环数组状态被去重。
- Known issues：这是存储+Mock AI压力测试，不是完整生态长跑；尚未完成100/500/1000 turn多营养级模拟验收。对象层当前需POSIX，导入无覆盖发布需Linux renameat2，不支持的平台明确失败，未声称Windows可用。未提供自动GC/retention；对象崩溃可产生安全的未引用块。新版尚未接管默认UI/旧全局引擎。
- Save compatibility：JSON/gzip旧fixture读写测试持续通过；新导入从保存turn开始可回放，缺失过去世界无法恢复；新模型继续运行必须显式校验model/stage版本。
- Next：独立SimulationEngineV2协调器，global climate pressure公式+旧wrapper语义精确对照，再迁移地质closure和空间拓扑。

## Step 6a — 气候标量等价迁移与新引擎协调器

- New files：v2/engine.py、v2/stages/environment/{climate,__init__}.py、v2/stages/__init__.py；tests/v2/{test_engine,test_climate_stage}.py。
- Removed legacy code：无。LegacyClimatePressureStage只迁移旧global temperature/sea level切片，默认旧引擎和tile温度计算均未改。
- 回归：保留旧公式的算术顺序、极冷无上限项、温室cap，以及wrapper空modifier no-op与0.01阈值。kernel可直接调用的空输入行为和stage行为分别测试；没有偷渡全图温度广播。
- 引擎：TurnCommand完整版本/幂等输入；历史snapshot+持久seed→pure pipeline→冻结叙事job计划→原子commit；model/stage/RNG manifest不匹配拒绝；planner看不到非确定性耗时。
- Tests passed：气候129项（含78组旧函数精确对照）+引擎17项；后端全量1011 passed、0 skipped、9既有warnings。迁移39文件strict gate通过。包含此前追加的profile一致性1项，数量不能与历史总数叠加。
- Known issues：这不包含tectonic阶段机/地块气候/水循环/biome全链。旧hydrology是view-only且阈值阻止正常累积；新水循环和统一拓扑会使用独立reference模型版本，详见ENVIRONMENT_MIGRATION_MAP。
- Performance impact：单kernel纯算术；未测完整环境负载；引擎每次重试可纯重算固定旧snapshot，由store保证幂等发布。
- Save compatibility：旧读写/新导入全通过；未知model/stage版本不会静默继续运行。
- Next：明确新参考生态单位与拓扑，迁移环境闭包；资源旧公式oracle与新闭合能量模型分开验收。

## Step 6b — 统一拓扑、环境闭包与资源层

- Changed files：pyproject.toml / requirements-dev.lock 增加 hash 锁定的 Hypothesis 及 sortedcontainers，其余已锁版本保留。
- New files：reference/{common,topology,world,environment,hydrology,resources,__init__}.py；stages/resources/{legacy,__init__}.py；6 组对应测试；RESOURCE_ECOLOGY_MIGRATION_MAP.md、REFERENCE_MODEL.md。
- Removed legacy code：无。旧 NPP、资源更新和捕食压力按原算术顺序纯提取；保留旧单位/库存缺陷作为 oracle，不把新参考生态伪装成旧数值等价实现。
- 新模型：统一奇列六边形圆柱地图；可复现 genesis 与预留 species slots；气候→持久板块/地形→守恒水文→多因素 biome→活生产者 NPP→凋落/分解/养分回收。定义生态/地质两个时间尺度、碳当量和水深单位，详细限制见 REFERENCE_MODEL。
- Tests added/passed：拓扑50、环境61、资源52、genesis/整链replay21、legacy oracle201，共385项；Hypothesis覆盖随机小图水收支、资源C/N/水收支。共享工作区全量1442 passed / 9既有warnings（其中已包含尚待独立验收的下一批ecology32、demography14，不能重复累加）；61个迁移源/测试文件Ruff/format/strict mypy通过。
- 独立查看：季节南北相位错误、显式未知topology被接受、大库存吞掉NPP但仍扣养分/水三项均有反例并修复，再次独立复验184项通过；账本按各库存差求和，超出基于通量的数值容差立即拒绝candidate。调整测试中的错误季节预期，增加精度损失与未知拓扑回归。
- Performance impact：上述385项定向测试约2秒；审核阶段曾对固定population环境资源链跑3 seed×1000 turn，残差小，但这些长跑在最后三项修复前运行，只作为探索证据，不当最终完整生态验收。资源metrics仅标量总量/最大误差，不存tile数组副本。
- Known issues：尚无完整生态长跑；水文无湖盆溢流，封闭洼地水可持续积累；生态年当前固定12 turn季节；基础分解/外部无机碳和水生水库是明确简化；默认旧游戏入口仍未切换。预留slot容量、CPU开销需要完整模型验收后评估。
- Save compatibility：旧JSON/gzip读写/导入全量回归仍通过。新参考genesis显式新model；6stage历史可重建，分支共享输入RNG时同数值结果，+4°C分支不污染control。
- Next：审查适宜度/动态K/竞争/Holling摄食，接入守恒迁移和统一人口账本，再进入selection与演化。

## Step 6c — 生态、迁移、人口与端到端长跑

- New files：reference/{ecology,feeding,movement,demography_inputs,mortality,reproduction,demography,metrics,model}.py；tests/v2/test_reference_{ecology,movement,demography,metrics,integration}.py；scripts/benchmark_reference_ecology.py；两份1000turn证据JSON。
- Changed files：world.py 初始化紧凑genesis观测baseline，预留人口/生态scratch；Removed legacy code：无。纯CPU新路径仍显式opt-in，未让旧GPU runtime运行新模型。
- 实现：动态K、niche竞争、资源扣减、Holling共享猎物整数死亡；守恒扩散/压力迁移/地理连通；八原因排他死亡、储备支付出生、单一常规人口更新；紧凑metrics、17stage组装、真实存储/replay/branch验收。
- Tests added/passed：生态37、移动48、人口20、metrics9、整链durable1；最终共享全量1625 passed/9既有warnings（已包含下一批selection43/lifecycle71，之后新增20stage集成2项也定向通过）；72源/测试文件strict gate通过。所有新增业务文件均<500行。
- 每步查看修复：feeding/movement/mortality/reproduction大库存吞通量；死亡原因int64累计溢出；极小K与体重无意义除法溢出；metrics空baseline/首turn灭绝漏记/错误species axis。均保留反例回归。独立只读gate113项通过；增加捕食v2后另跑38项生态+整链回归。
- 参数修正：初版Holling处理时间使默认carnivore饱和摄入仍不足维持代谢。明确升FeedingStage version2，保留初版长跑作对照，新增默认捕食者高猎物密度可维持测试；并非人为确保物种存活。
- Performance impact：8×4、7初始物种、16slots、17stage、seed37、checkpoint25、每50turn mock AI，v2第100/500/1000回合人口13106/10004/6397，richness5/3/3；累计238次捕食死亡、444534个迁移步。C最大误差5.18e-12、N最大误差4.55e-13；各窗口重演/replay/reopen均通过。RSS54.5/55.1/55.2MB；存档6.35/28.99/56.92MB；末100turn median317.7ms（包含持久化、profile读取和校验，不是纯数值耗时）。初版完整长跑、另seed8的100turn也留有本地报告；不把单seed1000turn当全面稳定性证明。
- Known issues：多样性仍下降，不能据此宣称长期营养结构或涌现已经验收。此次完整长跑未含适应/物种分化/生态工程；存档随事件/差量历史线性增长，无GC策略；磁盘校验占据明显成本。最后增加的统计定义文字/有界timing deque不改变数值模型。参考模型不是现实生态标定。
- Save compatibility：旧JSON/gzip读写/导入全量回归保持；新参数用stage版本隔离，旧参考世界可replay，不能静默按新manifest续算。前端默认流程未变。
- Next：20stage解释层验收；mutation/drift/gene flow/adaptation/speciation，再开放新API/前端访问。

## Step 6d — 选择压力、fitness梯度与灭绝化石记录

- New files：reference/{selection,fitness,extinction}.py；tests/v2/{test_reference_selection,test_reference_extinction,test_explainable_pipeline}.py。
- Changed files：world.py 按显式manifest初始化选择信号和genesis压缩分布；model.py新增explainable_pipeline，保留17stage ecology基线；Removed legacy code：无。
- Tests added/passed：选择/梯度43、灭绝71、真实20stage集成2项。独立灭绝review又跑正常/灾害两组3turn并重复重演，无重复终态事件；主代理20turn重复执行state/event一致、durable灾害化石回放通过。
- 修复：fitness删掉混用当回合/年度单位且不影响梯度的baseline；逐边差分+独立精确维护梯度避免数值抵消；灭绝跨观测缺口不延续下降计数、同turn幂等、首turn初始分布、历史字段边界和点号ID保留。
- Known issues：fitness是固定密度/适宜度下的稀疏食物边代理，不是完整生命过程的导数；这一层不执行trait mutation，也没有fossil API/UI，后续阶段继续。温度/水压力目前可解释，不代表已有相应热适应性状。
- Performance impact：信号准备O(food edges×tiles)，梯度按关联边而非全species对；未单独标定大图成本。压缩分布只保留当前及最终一次，历史由delta承担。
- Save compatibility：新增stage由manifest显式区分；旧17stage世界继续由旧配方读取，不添加缺失genes假装续算。
- Next：真实遗传过程、预算约束、EvolutionTrace、条件化SpeciationProposal与守恒commit；同步准备隔离V2 API。

## Step 6e — 局部遗传、渐进适应与数值分化

- New files：reference/{evolution_contracts,genetics,adaptation,speciation,speciation_commit,diversity}.py；tests/v2/{test_reference_adaptation,test_reference_speciation,test_evolution_pipeline}.py；EVOLUTION_MIGRATION_MAP.md、evidence/evolution-1000-seed37.json。
- Changed files：world.py按manifest增加固定轴deme/proposal/gene population/connectivity/age；model.py新增26stage配方；metrics.py新增version2群体均值方差；benchmark_reference_ecology支持显式--evolution；Removed legacy code：无。
- Tests added/passed：adaptation44（含40个Hypothesis世界）、speciation76、持久化全链2；定向133 passed，包含原metrics/lifecycle集成。共享后端全量1799 passed/9既有warnings（其中还包含新API48+mount2；不要与这些计数再相加）；93个迁移源/测试文件strict lint/format/mypy通过。
- 每步查看：修正漂变方差应随1/N；约束投影后的定向步不得降低线性proxy；armor不足speed不能免费增加；分化父traits按剩余个体重算；身份转出不伪造Declining；大碳池旁极小reserve使用逐项精确转移校验；两套旧配方仍保持可独立执行。
- 独立验收：seed8/91各100turn，seed8完整重复100turn逐turn hash相同；C最大残差3.24e-12/N2.27e-13；每turn global traits等于deme加权均值。构造双岛隔离fixture，完整26stage在turn25达到score0.8635分化，26/27继续运行并保留食物网与谱系，无重复split；不是仅对standalone stage断言。
- Performance impact：seed37、8×4、16slots、checkpoint25、每50turn模板AI，100/500/1000turn回放及检查点纯复算通过；人口13119/9988/6342、richness5/3/3；C最大残差4.07e-12、N4.55e-13。RSS58.6/57.5/58.7MB，存档11.69/50.15/96.49MB，末100turn median439.2ms（包含持久化/读取/校验），受同期测试负载影响，不能作为纯算子benchmark。
- Known issues：默认1000turn未自然分化，仍发生多样性下降；不得声称物种涌现/长期营养级平衡已经完成。Deme均值不是个体基因型，基因流与fitness是已注明的近似；新遗传数组增加每turn差量体积；固定slot满时延期分化。真实LLM接线、生态工程、适应性精度与Mod/Scenario仍待后续。
- Save compatibility：所有旧JSON/gzip兼容回归仍通过；已存17/20stage历史可重建；新26stage world明确新manifest，不把缺基因的旧档静默升级。AI模板完成不会改数值hash。
- Next：V2 API与实验室界面验收；事件驱动叙事job接线；再逐项加入生态反馈/协同演化和实验调度，不删除仍承担默认旧游戏的legacy路径。

## Step 7a — 生态工程与真实相互选择反馈

- New files：reference/{physiology,feedback,niche}.py；tests/v2/{test_reference_feedback,test_reference_niche,test_evolution_golden}.py；FEEDBACK_MODEL.md与两份反馈证据JSON。
- Changed files：ecology/feeding/fitness提取共享响应并保留旧默认参数；model/world显式28stage配方及habitat_complexity；benchmark支持--feedback；整链tests参数化旧26/新28。Removed legacy code：无。
- 实现：冠层局部降温、储备支付土壤/水生栖息结构工程、养分回收、结构影响真实捕食；猎物群体防御与捕食者合作进入同一实际捕食/梯度函数；温湿度死亡hazard和工程代价进入fitness。不是预设回合触发升级。
- Tests added/passed：反馈机制10、生态工程56（含7项精度修复回归）、旧配方100turn golden1、26/28整链3；独立检查两次28stage seed8 100turn逐turn hash一致，旧26stage seed8此前100个hash全保留，另256seed实际捕杀统计和独立七轴差分通过。单独定向与整链结果见本批命令记录；下一批共享全量汇总避免重复计数。
- 每步查看：第一次1000turn在turn574拒绝0.465 ULP的养分舍入，保留失败前checkpoint并精确复现。采用局部ULP与2^-40单位上界，微量无法成套登记时撤回支出及环境收益；仍拒绝大库存吞有效flux。574–650完整续算与重演通过，再从genesis重跑1000turn通过。
- Performance impact：seed37、8×4/16slots、checkpoint25，100/500/1000turn人口12950/9944/6321，richness5/3/3；最大人口13630，累计捕杀240、迁移440180；最大C残差4.58e-12、N漂移4.55e-13。RSS58.89/59.06/59.19MB，存档12.10/52.87/101.59MB，末100turn median467.2ms（含存储、profile/校验及同期测试负载）。工程额外内存为tile结构数组，事件仅在显著变化时保留最多8个tile摘要。
- Known issues：默认1000turn仍未观察自然分化，营养结构/长期多样性并未达到目标。区域公共环境收益不伪装成即时个体收益；fitness仍是固定密度代理。双向选择机制通过不代表自然军备竞赛已经被证实；无湖泊溢流、无存档GC等既有限制保留。
- Save compatibility：新28stage manifest与17/20/26区分；旧26固定hash回归保持。旧JSON/gzip读写/导入的全量回归持续执行；未删除旧默认游戏路径。
- Next（当时计划）：实验室HTTP/React Query/SSE、历史叙事与形态展示；情景计划与并行世界。后续执行方式以下文最新要求为准。

## Step 7b — 可玩入口、世界分支与场景

用户于2026-09-30明确要求停止验证，减少学术化，优先可玩性。已停止后续测试、lint、typecheck、build和长跑任务；以下是实现记录，不是验收结论。

- Changed files：`backend/app/main.py` 挂载独立V2服务；`frontend/src/main.tsx` 增加 `/play` 与 `/lab`，经典入口增加沙盒链接；迁移范围脚本增加API目录。
- New files：`backend/app/api/v2/`；`simulation/v2/experiments/`；AI叙事计划器和离线worker命令；`frontend/src/v2/`；`PLAY_GUIDE.md`、`LAB_GUIDE.md`、`SCENARIO_EXPERIMENTS.md`。
- 游玩：新世界、1/10/50回合快进、当前回合结束后暂停、暖化/冰河/单回合灾变、自动保存、上次请求恢复、历史地图、任意历史节点分支、回溯重来。
- 观察：物种星标关注、角色/压力/变化的中文说明、性状外观、地形/物种/温度/植被图层、事件纪事。数值分布、哈希、运行信息移入高级折叠区。
- 平行世界：预设情景从同一历史节点创建原环境与变化分支，推进并比较结果，可进入分支继续游玩。高级设置保留多分支、回合安排、JSON导入导出与可恢复执行。
- 后端：严格命令和完整版本、已提交快照只读回放、事件SSE、React Query失效；持久情景计划、稳定操作标识、有界并行与断点续跑；AI结果仅作为叙事附注。
- 另有已写但未接入默认配方的自适应演化精度模块 `resolution*.py`。不把这个接口当作已上线的“快速模式”。
- Removed legacy code：无；经典模式尚未切换到新核心。
- Tests added：保留用户改变要求前已经写下的后端/前端测试；本轮玩法调整不增加测试。Tests passed：不提供当前代码的通过结论，没有继续运行验证。以前的结果只适用于当时的代码。
- Known issues：真实LLM worker未自动接入；Mod SDK与完整legacy替换尚未实现；玩法平衡仍需后续迭代。当前新数值运行环境限定Linux/glibc x86-64。
- Performance impact：快进按回合顺序保存并更新界面，平行世界有界并发；本轮不做性能测量，未宣称加速。
- Save compatibility：旧JSON/gzip入口保留，新世界使用独立SQLite/NPZ；已知旧V2配方继续按原manifest选择，不静默升级。
- Next migration step：继续围绕物种成长、地图互动和玩家选择打磨游戏体验，按需接入真实叙事提供方；不再默认启动验证流程。

## Step 7c — Mammoet 本地 Qwen 叙事

- 用户指定接入本地Mammoet的Qwen Next。读取本机已有隧道和客户端配置，通过模型目录取得实际ID `qwen3.8-flash-next`，API为本地转发 `127.0.0.1:8084/v1`。
- New files：`ai/jobs/local.py`、`api/v2/narrative_runtime.py`、不含凭据的 `backend/narrative-provider.example.json`。
- Changed files：app/runtime/main生命周期、planner提供方身份、SQLite任务按provider配置筛选、叙事来源投影及前端模型标记。
- 模型负责短中文故事/命名，不获得仓库或世界写入能力；worker复用现有schema、repair、fallback、完整版本检查和SSE。应用关闭时取消并等待worker，不遗留后台任务。
- 本机私有配置保存到被Git忽略的 `backend/data/narrative-provider.json`，凭据未写入提交、界面或日志；后端重启加载配置。
- Removed legacy code：无。Save compatibility：存储表未改，旧任务与注释继续读取；旧离线任务不转为该模型任务。
- Tests added/passed：未新增或运行；未发送试运行生成请求。Performance impact：单个异步worker最多一个推理请求，不阻塞数值回合；未测量模型延迟。
- Known issues：尚未核验模型实际生成结果；快进造成旧版本结果过期时会被丢弃，这是既有规则。模型故障时由有界重试与备用文案处理。

## Step 7d — 经典模式复用本地 Qwen

- 将本机 `data/settings.json` 的默认服务商及九类文本能力指向 Mammoet / `qwen3.8-flash-next`；并发2、超时180秒、回合报告启用。该私有文件不进入Git。
- Changed files：`frontend/vite.config.ts` 增加可选 `V2_BACKEND_PORT`，将 `/api/v2` 与经典 `/api` 分别代理。当前经典服务8025、新沙盒8024，共用手机入口5190。
- New files / Removed legacy code：无。Tests added/passed：未新增或运行，未试调用模型。Performance impact：仅代理分流与本地配置，没有性能测量。
- Save compatibility：经典与新沙盒继续读取各自存储；新沙盒现有地址、数据目录不变。
- Known issues：经典服务首次空库启动日志包含既有 `ServiceContainer.habitat_manager` 缺失警告，异常被旧初始化逻辑捕获，HTTP服务已启动；本次未声称旧模式的完整游戏流程已通过验收。
