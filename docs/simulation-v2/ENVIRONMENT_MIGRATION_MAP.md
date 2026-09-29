# Step 6 环境逐阶段迁移边界（只读源码映射）

2026-09-29；仓库 `/home/yuyuan/Clade`。参考 `docs/simulation-v2/CURRENT_PIPELINE.md` 和 `TARGET_PIPELINE.md`。本次未改任何 repo 文件、未调用 live DB/AI。以下行号是本次工作树读取结果，不把目标文档当现有实现。直接数值调用在 `/tmp/clade-audit-venv/bin/python` 运行通过。

## 决策：首批只提取现有全局 climate pressure kernel

最小、安全的首个等价边界是 `backend/app/services/geo/map_evolution.py:213` 的 `MapEvolutionService.calculate_climate_changes`。它不读取 `self`、不访问仓库、不抽随机数；只读 `pressure_modifiers` 字典和 `current_state.global_avg_temperature`，返回两个 Python float：`(delta_temperature_C, delta_sea_level_m)`。可提取成显式标量输入函数，然后在 V2 reducer 应用两项 delta；还不能称完整 Climate→Geology→Hydrology→Biome 全链迁移。

现有计算顺序必须保留：`0.3*temperature - 0.2*volcanic - 0.4*impact + 0.05*humidity + 0.1*drought`；`delta_sea = 2.5*delta_temperature + 2*flood`；新全球温度小于 5°C 再减 `(5-new_temp)/5*50 m`；大于 25°C 再加 `min(1,(new_temp-25)/10)*30 m`。`tectonic` 键读出但无作用（:264）；冷端因子实际上无 cap（:277），不是注释所称保证 0–1。

调用边界不能丢掉旧 wrapper 语义：

- `simulation/stages.py:456` 先 `advance`，它递增/切换地质叙事阶段；`map_evolution.py:159` 算气候仅用于叙事，不应用温度增量。
- `stages.py:460` 仅在 modifiers 非空时再算 kernel；`:468` 仅当任一 delta 绝对值 >0.01 才更新状态；`:482` 仅 `abs(sea_delta)>0.5` 才重分类地形。空字典时旧 stage 不执行 kernel，尤其冷/热基线下直接调用空字典可能产生海平面额外变化；不可无意改成每回合无条件调用。
- 当前 wrapper 只改 `MapState.global_avg_temperature/sea_level`，没有把同样的 delta 加到每个 `MapTile.temperature`。Tensor 构建读 tile.temperature。把全球温度广播到所有 tile 是接线/模型改变，不能计入首批 parity。
- 比较数值时不要为了调用纯方法实例化服务：构造器 `map_evolution.py:109` 会抽 `random.randint`；用下方 unbound method 保留旧算法，避免无关 RNG 干扰。

### 已验证的精确 fixtures（不是新实现）

| 起始全球温度 °C | modifiers | 精确返回 `(°C,m)` | 覆盖 |
|---:|---|---|---|
| 15.0 | temperature=5, volcanic=2.5, impact=1.25, humidity=10, drought=5, flood=2, tectonic=99 | `(1.5, 7.75)` | 所有有效线性项、tectonic 无效 |
| 4.0 | temperature=-5 | `(-1.5, -28.75)` | 新温度 2.5°C 的冰期分支 |
| 34.0 | temperature=5 | `(1.5, 33.75)` | 新温度 35.5°C 的温室 cap |

以下可从 repo 根目录直接运行，测试输入状态与 Python RNG 不变；未调用仓库：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/clade-audit-venv/bin/python - <<'PY'
from types import SimpleNamespace
import random
from backend.app.services.geo.map_evolution import MapEvolutionService
before = random.getstate()
fixtures = [
    (15.0, {'temperature': 5.0, 'volcanic': 2.5, 'impact': 1.25,
            'humidity': 10.0, 'drought': 5.0, 'flood': 2.0,
            'tectonic': 99.0}, (1.5, 7.75)),
    (4.0, {'temperature': -5.0}, (-1.5, -28.75)),
    (34.0, {'temperature': 5.0}, (1.5, 33.75)),
]
for temp, modifiers, expected in fixtures:
    state = SimpleNamespace(global_avg_temperature=temp)
    assert MapEvolutionService.calculate_climate_changes(None, modifiers, state) == expected
    assert state.global_avg_temperature == temp
assert random.getstate() == before
print('3 exact fixtures passed; random state unchanged')
PY
```

后续迁移测试还需 wrapper 空字典/no-op、5/25°C 分界、commit failure 无发布、同输入重放、delta apply 只一次；这些是接受项，本次没有编写实现/测试。

## 可提取函数清单与真实 contract

路径均相对 repo 根；`T=H*W`，tile 对象必须有稳定 id 和明确 `(y,x)` 映射。模型声称的量纲与实际运算不同处以下单列。

| 域、来源 | 实际输入 → 输出/shape/单位 | RNG / global / mutation；迁移意义 |
|---|---|---|
| 压力汇总 `simulation/environment.py:315` `apply_pressures` | ParsedPressure 序列的 kind/intensity + 可选配置 → dict[str,float] 的无量纲基础 modifier | 无 RNG，不依赖 self；读取 `PRESSURE_TO_MODIFIER_MAP` 和 `constants.py` tier/阈值（:333）。显式冻结配置即可提取。当前 `stages.py:423` 未传可选配置，使用默认倍率，勿顺带改接线。 |
| 全局气候 `services/geo/map_evolution.py:213` | dict modifier + 全球 °C → `(delta °C,delta m)` 标量 | 上述首个 kernel，纯算术；无地质时钟、季节、降水模型。 |
| 地块初始温度 `services/geo/map_manager.py:2629` `_temperature` | lat/lon ∈[0,1]、elevation m → °C 标量 | 数学函数+坐标 hash 扰动，无随机调用、无 self 状态；初始化 `_generate_grid:1250` 调用。陆地 lapse -0.0065°C/m（:2689）；深海调节、简化洋流、大陆项；不是每回合动态 Climate。 |
| 地块初始湿度 `map_manager.py:2726` `_humidity` | lat/lon∈[0,1]、elevation m → [0.05,0.95] 相对湿度标量 | math.sin、坐标 hash，非随机；参数 tiles/x/y 实际未使用，未实现注释声称的距海/真实雨影；初始化 :1251 调用。没有 mm/yr 降水量。 |
| 初始高程映射 `map_manager.py:1764` `_rank_to_elevation` | rank∈[0,1]、sea_threshold 通常0.7 → elevation m | 无 RNG/全局/self 状态；只是 percentile→分段高程，初始化 :1247 调用，不是时间演化。 |
| 地图初始资源 `map_manager.py:2823` `_resources` | °C、elevation m、humidity∈[0,1]、latitude∈[0,1] → richness [1,1000] 标量 | 无 RNG/隐藏状态；不是 kg biomass 或 NPP flux。迁移时只能保留 legacy richness 单位。 |
| 地质阶段转换 `map_evolution.py:122`, `:198` | `current_stage_name/progress/duration` +事件+turn → 更新阶段状态与 MapChange | `random.random/randint` + 模块 `STAGE_POOL/TRANSITIONS/DURATION_RANGE`；`advance` 原地改 self 和 MapState。不是高程运动。需把随机 stream 和阶段状态显式化才能提取。 |
| 边界分类 `services/tectonic/motion_engine.py:256` `_classify_boundary` | 两个 Plate 的格/回合速度、格坐标旋转中心、plate_type；width格 → BoundaryType | 无 RNG；使用 self.width 的 X 最短周期差；±0.01 dot 阈值有 legacy 格²/回合语义，不是物理标准。小型 geology 纯核候选。 |
| 高程演化 `motion_engine.py:324` `_compute_terrain_changes` | T 个 SimpleTile 的 id/x/y/elevation/temperature/boundary_type/distance_to_boundary，modifiers；参数 plates/plate_map/boundary_info 在此体内未使用 → TerrainChange 列表（m/°C）并原地改 tiles | 本函数无 RNG，但依赖 self.terrain_config、全局 TECTONIC_CONFIG 的 pressure_effects/features、self.width/height 邻接。距离高斯衰减、每回合 cap、固定次数邻居平滑，温度 lapse 默认 -0.6°C/100m（:452）。先边界/距离计算，后脱副作用提取；不可把一整个 TectonicSystem.step 当纯函数。 |
| 地幔点速度 `services/tectonic/mantle_dynamics.py:55` `ConvectionCell.get_velocity_at` | cell center/radius 格、strength 无量纲、direction + 查询 x/y 格 + width → `(vx,vy)` 简化驱动强度 | 无 RNG，读取 dataclass cell；X最短周期距离，中心<0.1返回0，radius外0。不是新增 mantle 模型，现有功能可独立 extraction。 |
| 地幔/板块主推进 `tectonic_system.py:95`，`mantle_dynamics.py:253`，`motion_engine.py:38` | 板块/plate_map/内部 tiles/mantle 状态 +压力 → 新速度/高程/事件/反馈/turn | 原地写复杂状态；多处全局 random。TectonicIntegration :76 先按坐标同步主 tile 高程/温度，不同步所有其他环境值。需先完整 closure。 |
| 河流网络 `services/geo/hydrology.py:9` `calculate_flow` | T 个 {id,x,y,elevation m,humidity∈[0,1],is_lake} + H/W → 稀疏 dict[id,{target_id,flux}] | 无 RNG/DB、不改输入；只 self.width/height。flux 是“humidity×单位面积”伪通量，无 m³/s 或 mm/yr 单位。严格下降、邻居枚举先者赢 tie。**当前不是 stage，只在 `map_manager.py:1090` get_overview 内用于 RiverSegment 渲染。** |
| 初始化生物群系 `map_manager.py:2908` `_infer_biome` | temperature°C/humidity/elevation m → biome 字符串 | 无 RNG/self，初始化 sea_level=0；同样分类表在 reclassify :101–131 内 inline 用 relative elevation。可合并前先冻结 exact strict inequalities。 |
| 重分类地形 `map_manager.py:88` | 当前 tiles、sea_level m → 更新 biome/cover，并再次水体分类 | wrapper 读写 repository，不是纯核；主干可抽 DTO→delta。更新 cover 用坐标 XOR伪随机 :148，未传 primordial flag，因此与 primordial 初始化不同。 |
| 水体分类 `map_manager.py:2398`, `_is_landlocked:2453` | 全 T 个高程/湿度/坐标 + H/W → 原地 biome/cover/is_lake/salinity‰ | 无 RNG；六邻接 BFS。实际水域测试用固定 `elevation<0`，不是 sea_level-relative，可能覆写前一个海平面重分类；消除这种不一致是模型修正，不能隐藏在 parity。 |
| 纯 cover 推断 `map_manager.py:2945`、`:3030` | biome + noise[0,1] + primordial +可选 °C/humidity/m → cover类别 | 无 RNG；noise来自上游显式值/坐标 hash。不模拟植被存量。 |
| 实际植被统计 `services/geo/vegetation_cover.py:251` | tile_id + habitats(population) + species_map → TileVegetationStats（各植物类型 count/total/density/type） | 会通过 `classify_plant_type:153` 读写 `_plant_type_cache`；`is_plant_species:237` 调 PlantTraitConfig。无 RNG。density实际为 `sum(habitat.population)/1e12` (:302)，虽然常量注释写kg，但没有乘个体质量，不能称真实 biomass density。 |
| 覆盖分类 `vegetation_cover.py:333` + `:446/:480/:532/:566` | density/dominant_type + tile °C/humidity/elevation/biome → cover类别 | 只读 class constants（阈值），无 RNG/DB；适合在明确 plant-type/density 输入后提取。`:610 update_vegetation_cover` 原地改 cover；水体直接 skip :642。当前默认 stage 不调用，full声明 VegetationCover :2743 调用。 |

地质生成 `_generate_grid`、`_generate_earth_like_height_map`、岛屿/海岸修改是初始化模型，不是逐回合环境阶段。生成器 `map_manager.py:1218` 可读墙钟，`:1221` 和 `:1222` reseed 全局 Python/NumPy；噪声 helper :1308/:1737 也 reseed。这些不能在 stage parity 中隐式重跑。

## Hydrology 的关键接受边界

`hydrology.py:41` 给每格初始 flux=humidity，`:55` **仅 current_flux>2 且有下游时才给下游累加**（:62）。因此当所有 humidity 合法处在 [0,1] 时，没有首个格子满足传播条件，整个结果恒空；这不仅是河流显示阈值。直接验证四格单行、id=1..4、elevation=3,2,1,0、humidity=1，输出 `{}`。

这不妨碍“原样提取”的 parity，但把累加移出显示阈值、接入降水/储水/蒸散/土壤水、输出真实径流或更新 has_river 都是**修复/新增模型**。当前 `map_manager.py:3108` 的 has_river 还是独立的坐标三角函数，不能声称与 HydrologyService 河网一致。

## 空间拓扑：视觉六边形，运算没有统一六边图

不能简单声明 tile 是方形，也不能声称所有算法都使用一致六边邻接。矩形 tensor shape 只是存储；实际函数有不同 stencil：

| 使用者 | 精确证据/语义 |
|---|---|
| UI | `frontend/src/components/CanvasMapPanel.tsx:202` 画六角形；`:98` x=col*spacing，`:99` y=row*spacing+(col%2?offset:0)，列奇偶错位；世界有横向复制（:232）。 |
| 初始化 q/r | `map_manager.py:1263` q=x-(y-(y&1))//2,r=y，是行奇偶转换，与 UI 列奇偶不同。 |
| 水体 BFS | `map_manager.py:2488` 注释称 odd-q，实际分支 `y&1`，六邻接；X modulo width，Y越界丢弃 :2504。`:2468` 任一连到南/北极水格就判海洋，横边不算出口。 |
| legacy habitats 与 hydrology | `map_manager.py:3298` 与 `hydrology.py:67` 按 `x&1` 选择各6偏移，X wrap、Y剪裁。前者 `if neighbor_id` 会丢 id=0（:3325），后者不丢。这个列表和水体 BFS 不同，不能只复用其“六边”标签。 |
| tectonic motion | `motion_engine.py:689` 按 x 奇偶，offset表又与 legacy `_neighbor_ids` 不同；X wrap、Y剪裁。平滑与边界距离依赖此表。 |
| 当前 tensor dispersal | `tensor/ecology.py:857` 调 `kernel_trait_diffusion_v2`；`:950` 调 advanced版本。`tensor/taichi_hybrid_kernels.py:2946/:2798` 都是方格4邻接，上下左右，`:2955/:2807` 两轴仅范围检查，没有 X wrap。 |
| 基础 tensor diffusion | `taichi_hybrid_kernels.py:103`、`taichi_kernels.py:116` 也是四邻；边界缺邻居但仍扣完整rate，边缘会损失population。NumPy fallback `taichi_kernels.py:295` 同4邻卷积，`:304` constant零填充。 |

因此 parity 阶段应该保存“每个算法的旧拓扑版本”及确定的 tile id→index 排序；选定统一六边图/周期闭合边界并修复人口守恒是另外的模型版本。首个 climate 标量核不受此冲突阻挡。空间下一批之前至少用 x/y 奇偶格、东西 seam、南北边界和 id=0 fixture 锁定选择。

## Tensor 环境桥的单位与保留信息

`simulation/tensor_stages.py:160–200` 构建 float32 `(7,H,W)`，下标 `[channel,y,x]`：`[temperature_C/50, humidity, elevation_m/1000, resources/100, land, sea, coast]`；tile ids 是 int32 `(H,W)`，缺格=-1。没有 salinity、soil、water storage、vegetation stock、river flux、geology state channel。`models/environment.py:46–52` 定义实体单位为 m、°C、humidity0–1、richness1–1000、salinity‰。不能只保存 env tensor 作为环境闭包。

`tensor_stages.py:168–183` biome substring 分类是另一份规则：freshwater和sea都归 sea；海岸归 land+coast，包含“海”的海岸词又可命中 sea，因此 flags 并非严格互斥。MapState 模型（`models/environment.py:69`）本身无 width/height 字段，而 tensor stage 对有 MapState 的 H/W用 getattr默认 H64/W128（:148）；真实地图尺寸应从显式 world metadata 固定，避免默认静默扩容。统一这些规则仍要作为独立有版本变更。

## 必须保存的 state closure（按“影响下一步”而非“序列化过”判断）

1. **基础环境状态/manifest**：稳定 tile id、x/y/q/r 与确切 H/W/topology版本；每格原始高程、温度、湿度、biome/cover、richness、lake/salinity/has_river、plate_id/地壳字段/地质风险（`models/environment.py:37`）。全球 sea_level m、global_avg_temperature°C、turn_index。冻结常量/配置及 float32/64 转换、legacy per-turn 时间语义，不能靠 tensor env 反推所有字段。
2. **旧 MapEvolution 内部阶段机**：`current_stage_name`、`stage_progress`、`stage_duration`（`:102–109`）；恢复后必须与 MapState.stage_*绑定同一权威值，不能重新构造抽duration。`advance`读取的是 service self；仅恢复 DB MapState 不足以决定下一阶段。保存 stage 转换/持续期表版本和其 RNG stream 状态/确定计数。
3. **压力累积**：`services/system/pressure.py:30–34` 的 window/threshold/cooldown_default/history[turn,intensity,kinds]/cooldown。`:40` 滑窗、`:48` 冷却会影响下次 major event。`EnvironmentSystem` 本身只有 width/height，apply_pressures无需额外气候累积状态；pressure config/map表要固定。
4. **启用真实 TectonicIntegration 后的内部世界**：`tectonic_system.py:55–71` width/height/seed/turn_index、全 plates、plate_map `(H,W)` int32、内部 SimpleTile 有序集合，及 main tile↔内部 tile的坐标/id映射。Plate包括velocity格/回合、angular_velocity rad/回合、center格坐标、type/density/thickness/age/motion_phase/target（`tectonic/models.py:48`），不等同于 SQLModel Plate 的 angular_velocity rad/年注释（`models/environment.py:32`）。plate_map和tiles双表示恢复一致。
5. **地幔动态**：`mantle_dynamics.py:87` 的 wilson_phase/progress/duration/total_cycles/mantle_activity/continental_aggregation，及全部 convection_cells{id,center_x/y,radius,strength,direction}；这些每步读写，不能用原seed再生成。配置含 phase序列/时长、速度/驱动力系数也应版本化。
6. **地质特征和触发历史**：`geological_features.py:47–53/:85` volcanoes/hotspots/trenches/ridges/rift_lakes/used_names；每个 GeologicalFeature 的位置、tile/plate、intensity、boundary、last_eruption_turn、dormant（`models.py:123`）。纯数值 closure至少保留参与后续 eruption 的集合/属性；若“used_names”以后仅展示，则可分离，但不能让命名抽随机数改变数值 stream。
7. **地质物种连通历史（若启用该接口）**：`species_tracker.py:123–141` current distributions/current connectivity、`_isolation_history`、`_last_plate_distances`、`_last_connectivity`，影响新增隔离/接触事件（:246–326/:349–362）。当每次都从同一 snapshot确定重建，当前分布/当前派生矩阵可归为 cache；跨回合历史不可丢。
8. **RNG/global**：旧 map生成、PlateGenerator、Mantle initialize、feature initialize 都可能重置进程全局 random/np.random；每步 mantle cell演化/phase时长（`mantle_dynamics.py:318/:415`）、运动速度扰动与灾害抽签（`motion_engine.py:174/:527/:555`）消耗全局random。Legacy重放需完整 Python/NumPy RNG状态和调用次序；V2改独立SeedStream是必需工程改造，但应以固定输入/预先抽样fixture隔离“随机序列切换”与“公式改变”。
9. **植被隐式分类状态**：`vegetation_cover.py:151` 的 `_plant_type_cache` 按species.id缓存；fallback读取 name/latin_name/description（:198），因此只改叙事文字可能改变下次未缓存classification，却不改变已缓存classification。parity要记录解析后的 plant category及来源版本/哈希或显式保留cache；改为只用growth_form/数值trait是目标合规性改动，不是无条件等价提取。常量虽称kg，当前density使用population计数，须保留legacy单位直至单独模型修订。

现有 `TectonicSystem.to_dict`（`:406–418`）只写部分外层状态，未包含 mantle、species_tracker、完整feature集合、RNG。`load`（`:430–457`）先调用构造器重新随机初始化，再只还原turn、plates、plate_map；没有还原已写出的 tiles/volcanoes/hotspots。这不是完整checkpoint实现，不能据其“save/load存在”认定闭包已覆盖。

## 明确属于新增/修正模型的目标

- 季节驱动、真实降水/储水/蒸散/土壤水/河湖质量守恒、土壤养分与植被stock，现有上述模块无这些状态方程。
- 将 currently view-only hydrology 改为回合生产者；修复 flux阈值传播；把 has_river与实际网络同步。
- 将全球 climate增量传播到tile、统一0m水体判定为动态sea_level、统一邻接与边界、选择“正确六边图”，都可能改变现有结果。
- 真实 biomass→cover、NPP flux的 kg/tile/ecological_dt量纲，以及soil/vegetation反馈；legacy resources richness不能只重命名为这些量。
- 将 legacy per-turn geological运动改为geological_years/ecological_dt/generations分离；现有Plate velocity单位明确格/回合。
- 禁止叙事参与植物类别/生态量，需用固定结构化输入迁移；不得为了parity把AI改名影响模拟的路径继续藏在stage里。

建议下一批接受声明写为“global climate pressure formula 提取 + 显式输入/输出 + parity fixtures + candidate-only reducer”，其后才是“地质stage状态closure和单一写者”；Hydrology与Biome按旧功能提取和模型修正分别验收。
