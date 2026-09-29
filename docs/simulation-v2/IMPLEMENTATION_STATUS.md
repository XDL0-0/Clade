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
| Async Safety | 运行令牌补丁已通过，持久Job实施中 | 运行协调、AIJob、幂等、版本/lease、stale拒绝 |
| Persistence | 实施中 | checkpoint/delta、原子提交、timeline、replay、旧档导入 |
| Environment / Resources | 待迁移 | 气候/地质/水文/biome、NPP与资源再生 |
| Ecology / Movement / Population | 待迁移 | 动态K、竞争/捕食/疾病、守恒迁移、死亡与繁殖ledger |
| Evolution / Speciation | 待迁移 | 压力/梯度/代价/变异/漂变/基因流、分化与灭绝生命周期 |
| Explainability / Emergence | 待实施 | EvolutionTrace、因果历史、niche construction、coevolution |
| Player Experience | 待实施 | 事件驱动数据层、时间机、并行实验、历史地图/形态/情景编辑 |
| Ecosystem / Legacy removal | 待实施 | scenario/mod/stage SDK、权限与兼容、最后移除legacy |
| Long-run acceptance | 待执行 | mock AI 100/500/1000 turn、性能/内存/save增长、replay一致 |

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
