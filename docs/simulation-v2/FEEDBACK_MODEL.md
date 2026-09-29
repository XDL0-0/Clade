# 生态工程与相互选择：28 Stage 配方

新配方 `feedback_pipeline()` 保留 26 Stage 的人口、资源和遗传账本，并增加两个阶段：
`Climate → Microclimate → Geology`，以及 `Speciation → NicheConstruction → Extinction`。
捕食版本升为 3，fitness 版本升为 2。26 Stage 配方保留原结果，并以固定 100 回合 hash 回归保护。

## 有成本的环境反馈

| 路径 | 实际算子 | 时间与限制 |
|---|---|---|
| 活生产者 → 叶生物量 → 局部温度 | 陆地降温 `2 × leaf/(100+leaf)` °C | 每回合从 Climate 重建温度后减去，不能逐回合累积无限降温 |
| 叶生物量 → 降水 | 既有气候公式按植被覆盖调节降水 | 属于简化局部反馈，不是全球水汽输运模型 |
| 生物工程 → 土壤质量 → 保水 | 储备支付工程工作，改善有界土壤质量；下回合 Hydrology 读取它 | 不直接赠送水或养分，不改变人口 |
| 水生工程 → 栖息结构 → 捕食 | 有界结构指数将局部 encounter 乘以 `1 − 0.5 × structure` | 只在水生 biome 生效，不把所有水生工程生物命名为珊瑚 |
| 储备支出 → 呼吸 → 养分回收 | 工作来自真实 reserve，C 作为呼吸离开有机池，N 以 `0.02 × C` 回到无机池 | 与其他 C/N 账本一起检查；工程指数不是新碳库存 |

每个 cohort 的请求工作量为 `min(reserve, 0.02 × dt × body_mass × population × engineering)`。
陆地土壤自然衰减率为 0.005/year，水生结构为 0.05/year；付费工作以饱和响应提高剩余空间。
所有角色可具有抽象 engineering trait，模型没有按物种名称触发脚本。零储备、零人口或零工程性状不能获得付费收益。
被动衰减仍可发生；工程者死亡不会使环境立即复原。

小通量计算采用逐 cohort/逐 tile 的局部舍入界；若极微量工作无法同时登记碳支出和养分回收，则撤回该处支出及对应环境收益。
超过声明的局部误差界立即拒绝整个 candidate。具体边界与长跑修复证据记录在实施验收中，不能按全世界库存放大容差。

## 相互选择进入实际捕食

原有 Holling II encounter 包含捕食者 attack、双方 speed、猎物 armor、toxin 与捕食者 detox。
新版再将群体因子写为：

```text
(1 + predator.cooperation × log1p(predator_abundance))
────────────────────────────────────────────────────
(1 + 0.3 × prey.cooperation × log1p(prey_abundance))
```

同一函数用于实际整数捕杀与 fitness counterfactual。没有“第 N 回合获得合作”或“猎物升级后强制捕食者升级”的触发器。
双方改变性状后，后续回合实际捕杀风险及选择梯度随之改变；trait 仍受总预算、装甲/速度权衡和代谢支出约束。
没有相关捕食边时，cooperation 只有维护成本，不能凭空获得正选择奖励。

这建立了协同演化所需的选择反馈机制，**不等于已经观察到长期军备竞赛**。
有限资源、随机灭绝、性状代价和相互作用消失都可能终止反馈。默认生态长测仍须诚实报告多样性下降。

## 温度与水分的适应依据

将已有 Suitability 中的 thermal/water 响应提取为共享纯函数，保留旧算术顺序。
新版 fitness 在固定当前环境下数值扰动 armor 和 engineering，评估 Mortality 已使用的年化 hazard：
`2 × thermal_pressure + 0.5 × water_pressure`，并扣除实际可支付的工程工作率。

只有真正进入这些方程的两轴获得该项梯度。食物边梯度仍是固定密度/适宜度下的稀疏代表性代理；
没有把压力数值直接当作某个性状的奖励，也没有将未来全体共享的环境收益虚构为当前个体收益。
这是瞬时模型 proxy，非个体终生繁殖成功的严格导数。`EvolutionTrace.fitness_gain` 应按此解释。

## 旧实现映射与验收边界

旧 `TieringAndNiche` 和 `PostMigrationNiche` 分析生态位/embedding，并不等价于这里的资源支付和环境反馈。
没有把名称或 LLM 描述的变化当成生态工程输入，也没有删除仍运行旧游戏的服务。

验收分别覆盖真实整数捕杀统计对照、陆水隔离、无捕食时的成本、共享生理公式、土壤对真实水文的下回合影响、
储备/C/N 守恒、两次同 seed 的逐回合 hash、旧 26 Stage 的固定 hash，以及真实 checkpoint/delta 长跑。
单次长测通过只能证明该输入下的工程和运行性质，不构成现实生态标定。
