# Resources / ecology 精确迁移映射（只读）

日期 2026-09-29；仓库 `/home/yuyuan/Clade`，检查时 HEAD `298e2f3125f4d9e44f1b4e3552f724ce380d3785`。已读 `docs/simulation-v2/CURRENT_PIPELINE.md` 与 `ARCHITECTURE_PROBLEMS.md`，下面仅补充本批边界。所有 `backend/...:line` 相对仓库根。本报告未改 repo、未跑生产回合、未连接 DB/AI/GPU。

**结论：可以等价提取 Miami NPP、显式资源状态转移、给定图的捕食压力公式；现有系统没有可直接迁走的闭合能量账本或 Holling 通量。** 资源 `current_npp` 混用通量与库存语义；tensor K 与 CPU K 没有统一单位或唯一权威。新模型不能用“旧函数迁移后全数值相等”作为总验收。

## 1. 接线：定义、被调用、权威写入分开

| 功能 | 当前接线与状态写入 | 迁移影响 |
|---|---|---|
| ResourceManager | provider 从 UIConfig.resource_system 创建 cached service：`backend/app/core/providers/simulation_services.py:113`；注入 engine：`:177`。持有 `_tile_states`、`_event_pulses`、`_snapshot`、`_last_turn`：`backend/app/services/ecology/resource_manager.py:107`。 | 服务内存是跨回合状态，不是 MapTile 字段；本地检索 legacy SaveManager/snapshot 未发现保存这些字段的接线。必须显式纳入快照或有明确新模型初始状态。 |
| 独立资源 Stage | 实际类名 **ResourceCalcStage**，order 32：`backend/app/simulation/stages.py:730`。计算消费→update→ctx.resource_snapshot。`backend/app/simulation/stage_config.py:882` 起注册表、当前 YAML 都没有它。且其 all_tiles 前置通常由 order40 的 Tiering 填充（`stages.py:968`）。 | 不能通过注册它直接声称保持等价：会提前/重复更新，而且32早于其数据来源40。 |
| 当前实际资源更新 | PopulationUpdate 最后调用 `_update_resource_dynamics`：`stages.py:1370`；重新读 DB tiles：`:1385`，按 `species.habitats` 算需求后调 service：`:1430`。 | 在 tensor ecology、CPU reproduction **之后**更新，CPU K 读到的是上次资源状态或 fallback；不要称同回合 NPP 已驱动所有生态。 |
| 初始化/事件 | TectonicMovementStage 的 `_apply_resource_event_pulses`：`stages.py:595`；每次有 map_tiles 即 initialize：`:612`，追加火山/洪水/干旱脉冲。 | 注释“尚未初始化”没有条件判定，initialize 会重置现有 current_npp（`resource_manager.py:606`）；full 接线要避免把状态重置当等价。 |
| 食物网 | FoodWebStage order35：`stages.py:843`；maintain_food_web 修改 prey_species/preferences 并 upsert：`backend/app/services/species/food_web_manager.py:294`。stage 保存 previous_species_codes、更新 ctx.food_web_analysis/trophic_interactions，并可能重读 species：`stages.py:855`、`:868`、`:887`、`:895`。 | 关系推断、持久写入与数值捕食应拆开；previous_codes 也是影响下一步的状态。 |
| tensor ecology | 49 构造 tensor，51 调 process_ecology：`backend/app/simulation/tensor_stages.py:472`；输入有环境、trait、营养级、pressure、cooldown、bonus，**没有 ResourceSnapshot、捕食矩阵或 prey_species**。 | 图变化不会直接成为这里的捕食通量；food-web 的 per-species mortality/migration 字符串信号也未在 TensorEcologyStage 消费。 |
| PopulationUpdate | 再做 kin competition：`stages.py:1184`；重置 species.population 到 initial：`:1248`；CPU reproduction：`:1252`；另算总量/K：`:1261`；重分配 tensor：`:1325`；upsert：`:1343`。 | tensor 的繁殖/竞争最终总量被 CPU 结果重标定，主要留下空间分布；不能把 tensor end population 当唯一 oracle。 |
| PredationService 压力 | `calculate_predation_pressure` 当前被 get_species_food_chain 展示读取（`backend/app/services/species/predation.py:951`）。全文引用检索 `compute_predation_pressure_matrix` 只有定义 `:1046`；`calculate_starvation_pressure` 也只有定义。 | 这些是可提取的旧算法，不代表已在 standard 执行，不能将新接线称原行为等价。 |
| Resource pressure/K API | `get_trophic_capacity` 仅被 ResourceManager 自己的 calculate_resource_pressure 调用；后者没有外部调用；initialize_tiles 的显式外部调用如上。 | CPU reproduction 真正读的是 `get_tile_state(...).t1_capacity_kg`，没有用逐营养级 API。 |

补充：食物网生成 signals 的代码是 `food_web_manager.py:899`；消费者缺猎物比例→t2/t3/t4_scarcity，另有 food_web_mortality/migration 字符串 key。旧 PreliminaryMortalityStage 会重置 `ctx.trophic_interactions`（`stages.py:985`），当前 standard 则走 tensor；本报告不把旧 mortality 解释成当前主链。

## 2. 数值单位 / shape / 读写合同

### NPP 与资源状态

- `MapTile` 明确 `temperature=°C`、`humidity=0–1`、`elevation=m`、`resources=1–1000 资源丰富度`：`backend/app/models/environment.py:37`。**没有 habitat_type，只有 biome**（`:45`）。NPP 却用 `getattr(tile,'habitat_type','terrestrial')`（`resource_manager.py:212`），所以普通 ORM tile 走 terrestrial 倍率；fixture 的 tropical 分支是人工输入覆盖，不代表现有所有森林能走到。
- `calculate_npp`（`:131`）：标称 kg biomass/tile/turn。实现：`rainfall=humidity*3500`，`miami=min(3000/(1+exp(1.315-.119*T)),3000*(1-exp(-.000664*rainfall)))`；硬编码乘30（`:191`），没有按 tile 实际面积与 turn_years 转换。因此“kg/tile/turn”是游戏缩放单位，并非严格 g/m²/year 积分。
- 温度 `<-30` / `>55` 或湿度 `<.05` 时0；y 用 `LOGIC_RES_Y=40`（`backend/app/simulation/constants.py:11`），光照 `1-.8*clip(abs(y-20)/20,0,1)^2.5`；肥力 `.5+resources/1000`；再乘 habitat/event，clip `[0,max_npp]`（`resource_manager.py:158`、`:193`、`:216`）。没有显式随机。
- `ResourceSystemConfig` 标明 NPP kg/tile/turn，K 个体数，效率无量纲（`backend/app/models/config.py:844`）。`enable_climate_npp`、`resource_to_npp_factor`、旧 optimal_temp/humidity 修正配置存在，但 calculate_npp 没用它们；兼容函数 `_calculate_temp_modifier/_calculate_humidity_modifier` 也未被调用（`resource_manager.py:232`）。不要在等价提取中顺手接通这些开关。
- `TileResourceState`（`:40`）每 tile：base_npp/current_npp、event_multiplier、last_consumption_ratio、overgrazing_penalty、t1_capacity_kg、trophic_capacities。实际循环写 base/current/ratio/penalty/t1_capacity；`trophic_capacities` dict 没填；`event_multiplier` 只是事件时更新的展示性副本，NPP 实际读 `_event_pulses`。
- `ResourceSnapshot`（`:67`）是 `dict[tile_id, state]` 与两个 `(T,)` float64 numpy vectors；vector 按排序 tile_ids（`:578`），没有单独 axis IDs 数组。`tile_states=self._tile_states.copy()`（`:584`）是浅复制，state 对象仍会被下一轮更改，不是不可变世界快照。

### 再生、需求与所谓“消费”

源 `resource_manager.py:287`，每 tile 显式等价式（默认 f=.7, r=.3, cap_mult=1.2, penalty_coef=.15, threshold=1）：

```
supply = (old_current if old_current > 0 else base_npp) * harvestable_fraction
ratio = demand/supply if supply>0 else 0
penalty = min(.5, (ratio-threshold)*penalty_coef) if ratio>threshold else max(0,old_penalty-.05)
n = base_npp if old_current == 0 else old_current
k = base_npp * capacity_multiplier
growth = r*n*(1-n/k) if k>0 else 0
current_next = max(.1*base_npp, n+growth-penalty*n)
current_next *= 1 + amplitude*sin(turn_index*.5)
t1_capacity_kg = current_next*npp_to_capacity_factor
```

- **没有 `-demand` / 实际摄食量 / 摄食成功率 / 共享限额 / 同化流入。** 需求低于阈值时消费0和消费正数得同结果；current_npp 被当 logistic stock，base_npp 标称却是 production flux。t1 factor默认50（`models/config.py:853`）暗含 residence-time 类比例但未定义物理时间单位。
- 非幂等：`_last_turn` 只写入，update 不拒绝重复回合；同回合重复调用会再次再生/惩罚和衰减事件（`resource_manager.py:357`）。 `enable_resource_dynamics=False` 直接返回，不推进 event/last_turn。
- base_npp=0 且 old_current>0 时 k=0/growth=0；零需求零旧惩罚会保留旧资源，极端气候不会自动使旧 current_npp 清零。
- PopulationUpdate 两套需求：生产者 `hab.population*(body_weight_g/1000)*.1`，消费者 `hab.population*(body_weight_g/1000)*(metabolic_rate/10)`（`stages.py:1392`、`:1410`）。**更关键：Species ORM 根本没有 habitats 字段/relationship**（`backend/app/models/species.py:10` 至`:128`），全文查找生产代码也未发现赋值挂载；因此正常 ORM 对象在这两处 getattr 回退为空，消费字典保持空。若fixture/扩展对象显式挂载habitats，两类需求才施加到同一个tile资源压力；且用该habitats.population而非tensor迁移后的分布，没有图上边实际生物量移除。此为源码+schema探针结论，未跑liveDB主链。
- 未注册 ResourceCalcStage 的需求不同：**所有 alive 物种** `population/len(habitats)*.01*body_weight_kg^.75`（`stages.py:803`）；body_weight_kg 从顶层属性读，缺省1，不是 morphology.body_weight_g。不能任选一个作等价全管线 oracle。
- event 队列 tuple `(event_type,multiplier,decay,remaining)`（`resource_manager.py:114`），multiplier相乘；每次 update 后 `new_mult=1+(mult-1)*(1-decay)`、remaining减1（`:363`）。默认 flood 初值.7、decay=-.4 导致 `.7→.58→.412→1`，与“先损失后肥力提升”的注释不同。

### K 并不是单一公式

| 分支 | 数值与单位 / shape | 当前读写 |
|---|---|---|
| ResourceManager K | `(T,)` t1_capacity_kg=current_npp*50；T1→2→3→4→5效率 .12/.10/.10/.08；`get_trophic_capacity` 再除 avg_body_weight_kg 得个体数（`:382`）。只接受 integer range 逐级循环；float营养级不能直接用。 | API 不写 population，不按 species 共享；不应误用作同级每种各得完整 K。 |
| CPU regional K | `dict[(tile_id,species_id),float]` 注释 kg（`backend/app/services/species/reproduction.py:580`）。T1基础取ResourceManager.t1_capacity_kg，否则 resources*100000（`:646`）；每营养组按 habitat.suitability 分配，再乘另一套 species suitability、body-size modifier，最终每物种最低1000（`:673`、`:749`）。 | 读取上一轮资源/DB habitats；真正调用处 global temp_change/sea_change 都0 TODO（`:293`）。floor与体型倍率可使共享后总和超过基础K。 |
| CPU half-level cascade | `reproduction.py:699`：1.5=.4*T1，2.0=.12*T1.5，即 **.048*T1**；2.5=.12*T2，即 .00576*T1；3.0=.10*T2.5，即 .000576*T1。并非注释的T2=.12、T3=.012。 | 没有可捕食营养级时再*.05；只检查存在，不按实际猎物存量或 prey_species 边限供给（`:722`）。 |
| HabitatManager 独立 K | resources*100000 *环境修正*suitability*体型(.5/2)，floor1000kg（`backend/app/services/species/habitat_manager.py:983`）。 | 与 reproduction 中另写的计算重叠，非 tensor K。 |
| PopulationUpdate 最终硬 K | stored morphology.carrying_capacity，否则 PopulationCalculator.calculate_reasonable_population 最大值（`stages.py:1275`）。 | `PopulationCalculator` 明确输出 **kg生物量**（`backend/app/services/species/population_calculator.py:29`），用 log10(body_weight_kg) 缩放 base1e7、乘3得到上界；PopulationUpdate 却直接对所谓人口数 clamp。这一单位歧义不能靠改变量名解决。 |
| tensor 繁殖 K | `(H,W)` capacity = env[3]*100*era_multiplier（`backend/app/tensor/ecology.py:1389`），与全部物种 pop.sum(axis=0)比较。 | env[3]是 resources/100，故基本量级K≈原始resources；无 NPP、体重kg、生态效率。 |
| tensor trait mortality K | `(S,H,W)` 概念值：env[3]*100/(body_size_trait*.1+.3)，和全物种总 pop 比较（`backend/app/tensor/taichi_hybrid_kernels.py:1678`）。 | 又一套K，过饱和引入死亡压力，上限.45；不同物种以自身body trait分母衡量相同tile总量。 |

`_calculate_trophic_biomass_pools` / `_calculate_available_prey_biomass`（`reproduction.py:831`、`:873`）全文检索只有定义，无当前调用。前者用全局 population * weight_g^.75 * suitability，尽管收 tile_id/habitats 却没用局部人口；后者*.15，不能视为已接通的真实kg能量模型。

### Tensor shape、适宜度与竞争

- `env float32 (7,H,W)=[T/50,H,elevation/1000,resources/100,land,sea,coast]`，`pop float32 (S,H,W)`，`tile_ids int32 (H,W)`，species_map 给固定S轴（`tensor_stages.py:160`、`:189`、`:208`）。resources原值1–1000，env[3]可至10，而kernel注释[0,1]（`taichi_hybrid_kernels.py:355`）；真实MapState无height/width时 stage 取64×128（`tensor_stages.py:147`），不是NPP使用40纬度高度。
- TensorStateInit 临时建 `(S,4)` params（`tensor_stages.py:300`），实际 TensorEcologyStage 重提取 `(S,8)` params、`(S,7)` prefs、`(S,14)` traits、`(S,)` trophic（`:396`）。params中代际时间天、体长cm，其余有归一化trait（`ecology.py:1526`）。主链**总传traits**，应冻结 trait branch，不能拿简化pref branch代替。
- trait suitability（`taichi_hybrid_kernels.py:308`）返回 `(S,H,W)` `[0,1]`。T/H tolerances来自trait；盐度proxy用sea+0.3coast，光照proxy=1-.7sea，未读 MapTile 真 salinity或NPP；权重 `[.30,.18,.18,.12,.12,.10]`，habitat硬屏蔽，specialization再调节（`:433`）。简化pref kernel（`:253`）权重与屏蔽不同，是另一个版本。
- local fitness（`:463`）=(.40*suit+.25*repro_score+.15*size_eff+.20*mobility)*age_bonus。其 age>30 分支在 age>20 的 elif之后，不可达（`:512`）。这应作为 legacy fixture行为保留，改正另列bugfix。
- trait overlap（`:529`）输出 `(S,S)`，六维归一化距离，加权 `[.15,.12,.12,.08,.13,.40]`，exp(-8*dist_sq)。不读 embedding。这与 CPU NicheAnalyzer/亲缘竞争读的 scalar niche.overlap 不是同一个量。
- trait competition（`:613`）输入 `(S,H,W)` pop/fitness、`(S,S)` overlap；overlap<.3跳过，fitness_diff>.05受压，<-.05负压力增益，否则双方耗损；loss=min(.5,pressure/(my_pop+100))，**没有下限0**，优势者可直接增加人口，未有食物流入。输出新pop，不返回死亡/出生ledger。
- 第二次亲缘竞争 `backend/app/tensor/competition.py:63` 输入S行物种属性、谱系、niche scalar overlap；输出 `(S,) mortality_mod/repro_mod/fitness`，另构造 `(S,S)` kinship/overlap/trophic mask。PopulationUpdate 只消费 mortality_mod（`stages.py:1207`），repro_mod没用于繁殖。另一个竞争层已在tensor process step6运行。
- 基础 trait mortality 只用营养级范围 `[trophic-1.5,trophic)` 累计 **population** 比率（`taichi_hybrid_kernels.py:1694`），不是body-weighted prey biomass，也不是food-web边。猎物少只增消费者死亡，没有相应从猎物减掉被吃数量。
- process顺序 suit→mortality→death→dispersal→migration→repro→competition→每格net-change clamp（`ecology.py:421`、`:524`、`:543`、`:556`）。death_counts 在第一轮mortality后计算（`:448`），后续竞争/最终clamp不修正它。sync用非零死亡格子的算术平均（`tensor_stages.py:600`），不是人口加权率；随后CPU再由initial*rate算survivors（`stages.py:1269`），不能期望death账本恒等。

## 3. 捕食 / Holling：现有的是什么

源码范围 `backend` 全文（不区分大小写）查找 `holling|functional.response|handling.time|attack.rate|处理时间` 无结果。只有 `docs/simulation-v2/MIGRATION_PLAN.md:51` 提到“Holling分量”。可饱和的 sigmoid 压力并不等同于带攻击率/处理时间的 Holling Type II/III 摄食通量。

现有三个近似层：

1. **Species图与需求压力**：`predation.py:1006` 构造 `(S,S)` dense float32 A，A[predator,prey]=preference（缺省.5），不保证每行归一化。Biomass `B=N*body_weight_g` 为g，需求=.1B注释“每天”，但是调用没有 dt（`:1095`）。available=A@B，不扣其它捕食者共享的猎物；starvation=.5*max(0,(.1B-available)/(.1B))^1.5，仅available>0才计算，**完全无猎物时反而0**（`:1103`）。被捕食压力=.3*(2/(1+exp(-(A.T@(.1B))/B))-1)；最终压力 `[0,1]`（`:1119`）。输出是无量纲死亡压力，既没有摄食数量也无同化/维持。
2. **scalar graph pressure**：`predation.py:651` 用2.718幂近似exp，没有matrix版*.3；preference缺省1/prey_count而非.5；无猎物生物量时return1。不是matrix版的等价scalar oracle。
3. **EcologicalRealism spatial coefficient**：`backend/app/services/ecology/ecological_realism.py:380` 用tile overlap与embedding策略对抗，返回无量纲[0,1]；`:452` 从饮食/恒温语义相似度返回同化效率.05–.35。它们没有明确摄食ledger，stage当前未注册；ResourceManager.get_species_assimilation_efficiency（`:440`）仅读可选plugin字典，get_trophic_capacity没用该效率。

因此新模型若加入 `attack_rate`、`handling_time`、逐边请求、共享猎物限额、同化/呼吸/碎屑账本，是**新增模型**。可以复用图/偏好与部分trait变换，但不能宣称在旧Holling核基础上改名。

## 4. 随机、重复计算与状态闭包

- 本批 NPP / resource transition / graph-pressure 原函数都无 random/NumPy RNG。季节波动是 turn_index*sin 的确定性周期。
- tensor“随机逃逸/长跳”实际用 `sin(i*13+j*17+s*19)` 等位置与**S轴索引**构造伪噪声（`taichi_hybrid_kernels.py:1149`、`:1220`、`:3045`）。不是seed RNG；species重排会改变其迁徙行为，GPU sin还有浮点容差。等价迁移先固定axis IDs/order；换成SeedManager属于有意数值变化。
- Kin ranking 用 np.argsort（`tensor/competition.py:186`）；相同值仍产生顺序相关不同rank。FoodWebManager构造set/current_codes（`stages.py:868`），图推断的稳定遍历应单独检查；本次未执行关系维护。
- PopulationCalculator.get_initial_population 有 global random.random（`population_calculator.py:154`）；纯 calculate_reasonable_population 没有。勿把初始化过程误当纯K oracle。
- 旧service闭包至少为：tile resource states、event pulses、resource config、turn、当前tiles；food-web previous_codes与图；population/tile distribution；species trait/体重/代际时间/年龄；生态/繁殖配置、environment modifier/resource boost；迁徙cooldown/decline state；轴顺序、embedding hotspot显式输入。仅存NPP vectors不足以resume等价。
- 重复来源：两套resource消费估算；独立资源stage+PopulationUpdate可双update；Tectonic初始化可清资源记忆；tensor与CPU重算繁殖/K/竞争；trait suitability与CPU habitat suitability重复且定义不同。不要在 extraction 阶段去重并仍宣称结果相同。

## 5. 已运行的 3 个 CPU fixture / 精确值

脚本 `/tmp/clade-resource-ecology-fixture.py`，完整输出 `/tmp/clade-resource-ecology-fixture.json`。命令：

```
/tmp/clade-audit-venv/bin/python /tmp/clade-resource-ecology-fixture.py
```

exit=0。调用原 `ResourceManager` 类与原 PredationService 源文件（独立importlib装载该文件，避开species package初始化）；没有重写公式。Resource transition fixture 仅将上游 calculate_npp 替成常数100来隔离状态核，其 update/get_trophic_capacity仍是原方法。额外读取ORM model_fields验证Species没有habitats/body_weight_kg、MapTile没有habitat_type（三项false）。进程设置 sqlite memory URL，并将 sqlite3.connect、SQLAlchemy Engine.connect、socket.connect 全部替换为报错，禁止DB/网络连接；断言 taichi 未导入。

### F1：Miami NPP scalar

固定 tile `{id:1,T:20,H:.5,y:20,resources:100,habitat_type:'terrestrial'}`，默认ResourceConfig：

| 变体 | 原始输出 |
|---|---:|
| 赤道默认 | 37105.56890592682 |
| y=0极地 | 7421.113781185361 |
| T=56致死cutoff | 0.0 |
| H=.049致死cutoff | 0.0 |
| H=.05边界 | 5923.954112698963 |
| habitat_type=tropical | 55658.35335889022 |
| enable_climate_npp=False且resource_to_npp_factor=999 | 37105.56890592682 |

最后一项验证这两个配置字段不影响当前 calculate_npp。

### F2：resource state transition + trophic conversion

固定 base_npp=100、旧current=50、旧penalty=0、turn=0、季节幅度=0，其余默认。

| demand | ratio | penalty | current_next | t1_capacity_kg |
|---:|---:|---:|---:|---:|
| 0 | 0 | 0 | 58.75 | 2937.5 |
| 35 | 1 | 0 | 58.75 | 2937.5 |
| 140 | 4 | 0.44999999999999996 | 36.25 | 1812.5 |

第一行按avg_body_weight_kg=2，T1–T5个体K=`[1468.75,176.25,17.625,1.7625000000000002,0.14100000000000001]`。同一turn重复update得到current=**67.74609375**。旧current=0/base100初始即105；base0/oldcurrent50仍50。默认flood event衰减=`[.7,.58,.41200000000000003,1]`。

### F3：graph predation pressure

species固定顺序 `[plant,herb,wolf]`，N=`[100,10,2]`，weight_g=`[10,100,1000]`，trophic=`[1,2,3]`，A=`[[0,0,0],[1,0,0],[0,1,0]]`。

- matrix pressure=`[0.014987512487364006,0.02990039838748677,0.0]`。
- scalar plant predation pressure=`0.049953203681951885`，与matrix版不相等，符合不同公式。
- 新service、wolf无猎物：`[0.014987512487364006,0.0,0.0]`；完全没猎物wolf的starvation仍0。
- **复用旧service且只删除wolf猎物边，物种数没变**：仍`[0.014987512487364006,0.02990039838748677,0.0]`，复现cache仅按species_count失效（`predation.py:1071`）。
- wolf人口改200：`[0.014987512487364006,0.2999999987633078,0.46297273137842576]`。

## 6. 哪些可以用旧 oracle，哪些不可以

| 候选迁移 | Oracle评价 | 合适的验收 |
|---|---|---|
| Miami scalar | **可用**。原方法无DB/GPU，固定tile attrs/config/event倍率，double精度。 | explicit NPP params→scalar；边界-30/55/.05、y边界、cap、habitat/default分支。保留游戏系数30与当前忽略字段；物理时间/面积转换另版。 |
| Resource transition | **可用作legacy纯状态转移**。显式old_state+demand+base+config+turn能复现。 | 数值与原update一致；状态深拷贝、event递减先后、同turn语义单列。新reducer的幂等防重复可独立验证，不能把额外拒绝说成旧数值核行为。 |
| Graph-pressure | **可用作旧压力核**，输入显式A/B/trophic跳过缓存。 | matrix pressure与原新service相等；孤立消费者0压力作为已知旧行为fixture。改变为正常饥饿、新通量需新的模型版本，不能以旧bug为正确性标准。 |
| PopulationCalculator合理K | **可用作旧启发式标量**；函数已纯，但输出kg和调用端人口歧义。 | freeze公式仅证明代码等价；不证明它能作为新population_count K。 |
| KinCompetitionCalculator CPU 对照 tensor kin | **不可靠**。CPU fitness权重.40/.30/.20/.10且age>10/20惩罚（`kin_competition.py:232`）；GPU .25/.35/.25/.15、age<=2有1.25（`taichi_hybrid_kernels.py:2086`）。CPU共存/势均力敌不乘gen_speed，而GPU乘（CPU`:367`,`:432` vs GPU`:2183`,`:2224`）。CPU parent_code路径与GPU纯谱系解析也不同。 | 不可拿已有CPU类作GPU等价golden；逐核按实际GPU源建立reference，再有GPU环境验证float32容差；本次没执行GPU。 |
| Scalar predation对matrix pressure | **不可靠**。尺度*.3、2.718近似、preference缺省、zero分支不同。 | 每分支自己的旧oracle，禁止混比。 |
| CPU reproduction整函数 | **不宜直接纯核oracle**。内部DB/config/global ResourceManager（`reproduction.py:280`,`:350`,`:646`,`:1065`）；公式在`:1332`是分段增长/超K衰减，非docstring闭式logistic。 | 先显式化配置和输入；可以提取最后分段算式，不能导入函数就声称独立闭包。 |
| CPU suitability/HabitatManager 对 tensor trait suit | **不可靠**。不同环境通道、trait映射与权重，CPU还含独立服务/cache路径。 | 当前trait kernel float32逐分支数值reference，不能用最终人口或只测0–1范围替代。 |
| legacy runner / regression终局DB快照 | **不可靠全链**，原审计已说明占位/重复终态问题，本次未扩展重复审计。 | 新资源/生态用局部纯fixtures+state闭包+不变量；最终仍需真实旧主链逐turn冻结才能宣称全回归。 |

现有 `backend/app/tensor/tests/test_hybrid.py:97` 繁殖只断言总量不降，`:110` 竞争只断言不增；它们不是traits生态/资源闭合golden。`backend/app/simulation/tests/test_ecology_stages.py:23` 是旧stage+mock检查，不代表主tensor ecology执行。没有在本任务运行它们或宣称GPU覆盖。

## 7. 给新参考模型的明确边界

本批建议拆成两个独立验收范围，不为主代理重复实现：

1. **legacy extraction**：以显式float参数提取F1、显式状态/事件转移提取F2、显式矩阵压力提取F3；只保留原结果、单位注明 `legacy_game_biomass_per_turn` / `legacy_pressure`，不把current_npp偷换为真实资源stock。适宜度/竞争能提取，但需要按当前trait分支另建CPU reference，尚没有本次运行的GPU oracle。
2. **新生态模型**：明确 `population_count[S,T]`、`body_mass_kg[S]`、`living_biomass_kg[S,T]`（派生或单一权威）、`resource_stock_kg[T,R]`、`production_flux_kg_per_year[T,R]` 与 `dt_years`，不要把库存与NPP复用一格；逐边摄食请求同单位、共同 prey/resource 限额、实际摄食→prey损失/consumer同化/维持respiration/未同化detritus；K由可用能量与维持需求得到只读诊断或增长约束，不能再与多个population clamp并行权威。

新模型至少应过：消费/死亡请求不超供给、无未记账人口/生物量增加、零供给不会产生无限K、无捕食者/无猎物/多捕食者共享/两个生产者分资源、单位缩放与dt一致、species/tile轴置换一致、save-resume资源/脉冲闭包相同。Holling若加入要显式定义attack/handling的量纲与多猎物分母，标注新增模型；本报告没有为其捏造旧实现。
