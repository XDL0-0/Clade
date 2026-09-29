# Reference ecosystem numerical contract

本模型 ID 为 `ecology-reference-v1`。它是用于验证因果链、守恒和长期运行的 CPU 参考模型，不是旧 GPU pipeline 的等价重命名，也尚未替换默认游戏入口。旧公式的兼容迁移分别使用 `LegacyClimatePressureStage` 与 `stages/resources/legacy.py`。

## 时间、空间与身份

- 一个生态时间步默认 1/12 年；当前季节周期固定 12 turn。地质时间步单独保存，默认 1000 年，避免板块速度混用生态时间。
- tile 索引为 `y * width + x`。统一 `odd-q-cylinder-v1` 奇列向下 hex 邻接；横向环绕要求偶数宽度，南北不环绕。地表各 tile 面积在本参考模型中视为相同。
- `world.py` 的 genesis 使用独立 SeedManager 命名空间，不读取 Python/NumPy 全局 RNG；物种按 ID 排序分配 slot，初始物种输入顺序不影响世界。
- species 元数据包含 `slot`；数值矩阵预留固定容量，未用行人口与能量必须为零。slot 不存入不可变 model manifest。后续物种创建使用保留行；容量耗尽必须明确拒绝 proposal，不能静默生成、截断或改变数组轴。
- manifest 固定 model、stage versions、RNG。相同模型、输入、依赖版本与 RNG 命名空间可复算；分支默认独立 RNG 命名空间，配对实验可显式共享命名空间。

## 单位和库存

| 字段 | 单位/语义 |
|---|---|
| population | int64 个体数，species × tile |
| body_mass | 碳质量单位/个体；当前不是克或千克标定 |
| energy_reserve | 碳质量库存，species × tile；能量以碳当量表示 |
| plant_biomass | 可食叶片碳库存，与 producer 个体结构碳分开 |
| detritus | 死有机质碳库存 |
| npp | 本 turn 新固定碳通量，不能再当库存累计消费 |
| nutrients | 游离养分库存；每单位有机碳固定携带 0.02 单位养分 |
| soil_water / surface_water | 每 tile 毫米水深库存 |
| rainfall / river_flux | 本 turn 毫米水深输入/流经量 |
| elevation / sea_level | 米；海平面是简化全球气候反馈 |
| temperature | 摄氏度 |
| humidity / soil_quality / volcanic_stress | [0,1] 无量纲指标 |
| plate vx / vy | 网格单位/地质年 |

初始库存是 genesis 的显式条件：每 tile 叶碳 200、死有机碳 20、游离养分 20、土壤水 100；个体初始储备为结构碳的 10%。这是待实验校准的参数，不声称代表现实生物群落。初始海洋和陆地消费者分别分布，producer 与 decomposer 使用结构化角色；名称、AI 描述不参与数值判定。

## 已实现的环境与资源链

`Climate → Geology → Hydrology → Biome → Primary Productivity → Resource Regeneration`

气候目标为 `baseline + 3 log2(CO2/280) + warming_offset`，每 turn 向目标调整差值的 10%；地块温度还受纬度、季节、海拔影响。叶片库存通过饱和反馈影响降雨；地表温度将使用下一步生态反馈，不能把叙事文字当植被类型。

板块中心、速度和所属 tile 均在快照内。周期 Voronoi 归属、边界相对速度和保守相邻侵蚀决定地形变化，火山由作用域 RNG 触发。此处尚非真实板块力学、地幔或完整河道地貌模型。

水文按地形降序汇流，包含土壤吸收、蒸发、地表水与低地蓄水；外流进入海洋视为系统输出。必须满足：

`old soil + old surface + rainfall = next soil + next surface + evaporation + ocean export`

biome 综合高程、水、温度、湿度、土壤和叶碳决定。编码为海洋0、湖1、苔原2、沙漠3、草原4、森林5、高山6。变化产生有原因和位置的事件。

初级生产力由活 producer 的结构碳决定冠层覆盖，再乘太阳入射、温度、水和养分限制。无活 producer 则 NPP 为零。每单位 NPP 扣 0.02 游离养分，陆地另扣 0.15 mm 土壤水；水生生产使用模型外部水库。固定的碳来自外部无机碳库，模型当前不做大气碳质量闭合。

叶片凋落转入 detritus；分解速度受温湿度和 decomposer 生物量影响。存在分解者时，被处理碳的 60% 进入其储备，40% 呼吸；没有分解者时基础分解全部呼吸。只有呼吸部分的养分返回矿质池，储备中仍绑定养分。

资源阶段分别检查逐 tile：

`Δ(leaf + detritus + reserves) = NPP - respiration`

`Δ(mineral nutrients + 0.02 × organic carbon) = 0`

结构碳在上述两个 Stage 内不改变，因此账本中可省略；完整生态回合必须把 `sum(population × body_mass)` 纳入。metrics 仅保存总量和最大绝对残差，测试直接检查逐 tile 数组，禁止把完整数组重复展开进 JSON metrics。

## 后续阶段必须遵守的接口

- ecology 写适宜度、动态 K、竞争和摄食压力；population 唯一常规更新者为 demography 的 PopulationUpdate。
- 摄食只转移库存。捕食先预留 `predation_deaths` 并转移死者碳；迁移只能移动尚活个体，人口最终统一扣减，不能在 mortality 再次生成同一尸体碳。
- migration_in/out 为整数流量，迁移按比例搬运储备，两个总量均守恒。同步流量从阶段开始状态求出，不允许遍历顺序造成同一阶段多次移动。
- 出生从储备扣除相应结构碳；非捕食死亡的结构碳和储备进入 detritus；维持代谢呼吸释放绑定养分。任何 trait 改变体重都须有额外质量转换合同，当前先不开放该自由度。
- traits 共用预算，禁止无代价全面增强。selection、mutation、gene flow、drift、isolation history 必须由数值状态驱动；AI 只能解释已经提交的事实。
- 已灭绝物种保留历史元数据，不重用它的 ID；复活、引入物种与 axis 扩容必须另有明确 Command。

## 验收边界

环境/资源的公式单测、性质测试和短链 durable replay 是当前证据；尚不能证明生态稳定性、物种涌现或协同演化。待消费者、迁移、人口和演化阶段接入后，必须另跑 100/500/1000 turn 多 seed 的生态长跑、参数扰动与配对对照。已有 storage benchmark 只能证明其固定负载下的存储行为。

参考模型当前不承诺 CPU/GPU 逐 bit 一致、不宣称物理气候/水文/生态真实性；所有新增机制必须保留公式、事件原因、单位和定量限制，便于之后校准和替换。
