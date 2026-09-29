# 可重现情景与并行实验

实验计划是不可变输入，包含完整源版本、共享 RNG namespace、控制组、各分支的情景以及相对回合数。
它保存在 SQLite 的 `simulation_experiments` 扩展表中，带内容 hash；进度使用普通 commands/commits，不再复制世界快照。
每个分支从同一个源版本 Copy-on-Write 分叉，使用独立 stage 实例；实验不会推进源 timeline。

## 最小计划

以下源版本应替换为已经存在的快照。版本 1 的每个 forcing entry 只设置一个相对回合的输入，可在同一个 entry 中设置多个参数。
第 1 回合表示源快照之后的第一个模拟回合，不是世界绝对 turn 1。

```json
{
  "version": 1,
  "id": "warming-trial",
  "name": "对照与升温",
  "source": {"world_id": "demo", "timeline_id": "main", "generation": 0, "revision": 0},
  "turns": 20,
  "rng_namespace": "paired-warming-trial",
  "control": "control",
  "branches": [
    {
      "id": "control",
      "name": "对照",
      "scenario": {"version": 1, "id": "baseline", "name": "原始环境", "forcing": []}
    },
    {
      "id": "warm",
      "name": "升温实验",
      "scenario": {
        "version": 1,
        "id": "warm-four",
        "name": "温度forcing增加4°C",
        "forcing": [{"turn": 1, "warming_offset": 4.0}]
      }
    }
  ]
}
```

`warming_offset` 是相对模型 baseline 的**绝对 forcing 设置**。如果源快照已经有 2°C offset，再填 4 表示增加 2；要在原基础增加 4，应填 6。
CO₂ 与 warming 设置保存在环境状态中，后续省略表示继承。
`disaster_severity` 和 `disease_pressure` 是每回合压力，省略时显式归零；持续事件需要为每个受影响相对回合填写记录。
不支持任意 Python 表达式、人口覆盖、动态 trait 修改或 LLM 决定事件结果。

计划版本、唯一 ID、有限参数范围、重复 JSON key、重复相对回合、未知字段、NaN/Inf、控制组非空 forcing 都会验证。
边界是 2–8 个分支、1–1000 回合、1–4 workers；这限制单次批次大小，不保证所有最大容量地图都能快速完成。

## HTTP 与恢复

- `POST /api/v2/experiments/run?workers=2`：请求体为完整计划；有界批次执行完后返回各分支状态、真实观测与对照差值。
- `GET /api/v2/worlds/{world}/experiments`：分页列出持久计划。
- `GET /api/v2/worlds/{world}/experiments/{id}`：只读核验当前已提交前缀，返回 pending/partial/completed/conflict 与各回合观测。

HTTP 批次在工作线程中执行，一个应用实例同一时间只运行一个实验批次，每批至多四个分支 worker；SSE/只读请求仍可服务。
这是有界请求，不是脱离应用生命周期的隐形后台任务。大批次可能超过代理超时；再次提交**原计划**会校验既有提交并从未完成处继续。
单个分支失败会保留其他分支的已完成结果，不能把部分完成当作完整对照结论。

同一 experiment ID 不能绑定不同 source、forcing、期限或 RNG 输入。workers 数只是执行并发度，改变 workers 不改变计划身份。
每回合 key 由 manifest、branch、相对回合确定；恢复核对 command 输入 hash、expected version、父提交、源快照和 RNG。
有人在实验分支上手动推进、回溯或替换世界后，恢复返回 conflict，不会覆盖这些操作。

## 结果解释

对照与实验采用共同随机输入以降低配对差异中的随机噪声。分化改变实体集合时，对应随机流仍按实体和目的隔离，不意味着不同世界必然逐事件配对。
结果提供已提交 metrics、人口/结构生物量的营养角色汇总、性状统计和活跃食物边；这些都是参考模型的描述性结果。
单次配对差异不能证明现实因果效应，也不能证明模型已经产生长期稳定的生态多样性。
实验没有调用真实 AI；数值结果不依赖注释完成时间。
