# 实施与逐步验收记录

审计基线：48dfb31。以下状态描述实际实现，设计文档中的目标不视为已完成。每批独立检查、记录、提交，随后继续下一批。

| 步骤 | 状态 | 验收边界 |
|---|---|---|
| Audit / Design | 已交付 | 七份文档、执行链映射、499文件清单与失败基线 |
| B0 后端测试入口/依赖 | 进行中 | 全量收集、隔离DB/网络、依赖锁、迁移代码lint/typecheck |
| B1 前端基线 | 已通过 | 14 tests、lint无错误且无新增warning、typecheck、build |
| B2 Stage配置合同 | 进行中 | 明确模式优先级、稳定ID、错误依赖拒绝、四种清单 |
| B3 回归捕获/旧档fixture | 进行中 | 逐回合捕获、结构差异失败、JSON/gzip旧档roundtrip |
| Foundation | 待实施 | TurnContext / StageResult / WorldEvent / WorldVersion / SeedManager |
| Async Safety | 待实施 | 运行协调、AIJob、幂等、版本/lease、stale拒绝 |
| Persistence | 待实施 | checkpoint/delta、原子提交、timeline、replay、旧档导入 |
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
