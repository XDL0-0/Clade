# Save Format V2 — Checkpoint + Delta（设计，尚未实现）

## 1. 格式身份与旧档

当前 SaveManager 已在 gzip JSON 中写 version="2.0"。因此不能用这个数字识别本设计。新格式必须有独立 magic：format="clade.checkpoint-delta"、schema_version=2、最低 reader version；旧 JSON/gzip 继续按 legacy importer 读取，绝不将其误当新格式。

旧档导入只读源文件，在新目录/世界建立 genesis checkpoint；校验完成后才切换活跃世界。不得先清空当前 DB 再试图解析。旧档缺少 RNG、隔离时长、资源缓存等时，记录 migration_warnings 与确定的默认初始化策略；只保证导入后的可复现，不承诺恢复旧程序未保存的历史或随机轨迹。

## 2. 初始实现选择

~~~text
save-directory/
  metadata.json                  # 小型索引，可从 SQLite 重建
  world.sqlite                   # WAL，提交/事件/job/metric/manifests
  objects/ab/<content-hash>.npz   # immutable typed array chunks
  staging/                       # 未发布文件，恢复时清理
~~~

P0 采用 SQLite + NumPy NPZ；不同时引入 Zstd、MessagePack 等多个必需 codec。codec 名称/版本显式记录，之后可增加 NPY+Zstd，旧 reader 明确拒绝不支持的 codec。小型 schema payload 可用紧凑 JSON 存 SQLite；禁止将 species×tile tensor 展成 JSON list。

NPZ 必须 allow_pickle=False，拒绝 object dtype。hash 对规范 header（array name/dtype/shape/axis/endian）与原始数组字节计算，不对带 ZIP 元数据的容器时间戳计算。NaN/Inf 拒绝；浮点 canonicalization 与负零规则固定。不同 codec 编码的相同逻辑块可以共享逻辑 hash。

## 3. SQLite 数据关系

| 表 | 主键 / 重要约束 | 内容 |
|---|---|---|
| worlds | world_id | seed、schema/model/format manifest |
| timelines | world_id,timeline_id | parent/fork reference、generation、head_revision、head_turn |
| commands | world,timeline,generation,idempotency_key UNIQUE | input hash、sequence、status、result revision |
| commits | world,timeline,generation,revision UNIQUE | parent revision/hash、turn、command、manifest、state hash |
| checkpoints | checkpoint_id | 精确 revision/turn、entity+array manifest hash、complete 标记 |
| deltas | commit_id UNIQUE | typed patches、changed chunk refs、entity tombstones、base/result hashes |
| objects | content_hash | relative path、codec、dtype/shape、size、checksum |
| events | event_id UNIQUE；timeline/revision/ordinal 索引 | WorldEvent、cause、payload、delta ref |
| ai_jobs / annotations | job_id；scoped idempotency UNIQUE | 见 AI_JOB_ARCHITECTURE；不嵌入世界 tensor |
| outbox | message_id UNIQUE | 待发布 SSE/job 事件和投递状态 |
| turn_metrics | world,timeline,generation,revision,schema UNIQUE | turn为查询索引；紧凑标量时序，支持同turn多个数值提交 |

严格 timeline 外键/查询范围；禁止默认读取“最新全局记录”。初期一 world 一个 SQLite 文件，可有多个 timeline。SQLite 单 writer 足以先保正确性；多实验跨进程可分库，immutable objects 可以共享。不要伪称 SQLite 多个 writer 能无限并发。

## 4. Commit 与 crash consistency

1. 读取明确 head version 的一致 snapshot，计算 candidate；此时不持有长写事务。
2. 校验并写 immutable changed chunks 到 staging；flush/fsync，内容校验后 atomic rename 到 objects，同步目录。完成文件先于 DB 引用发布。
3. BEGIN IMMEDIATE；先按scope+幂等键查command：同输入且已提交则返回原commit，绝不再写或因head已推进而误报stale；同键不同输入冲突。仅对新/未提交command核对expected world/timeline/generation/revision，冲突立即回滚。
4. 在一个事务中写 commit、delta、events、metrics、AI job/outbox 和 head 更新。head 使用 compare-and-swap，受影响行数必须为 1。
5. COMMIT 后更新进程只读 head 引用；再投递 outbox / SSE。metadata.json 临时写入后 replace，属于可重建索引，不是权威 head。
6. checkpoint worker 从已提交的固定 revision 构建完整 manifest；所有对象持久化后再事务性发布 checkpoint。失败不使原 delta 链失效。

文件系统与 SQLite 没有跨介质原子事务。以上顺序保证最多出现未引用的 orphan blob，而不出现指向尚未持久化文件的已提交 head。事务前 crash：旧 head 有效；事务后 publish 前 crash：outbox 重发；checkpoint 中断：继续用旧 checkpoint+delta。恢复必须验证引用完整性、hash 链和 schema，不悄悄跳过损坏 delta。

P0 优先 SQLite journal_mode=WAL、foreign_keys=ON、明确 busy timeout 与 durability 设置；实际 fsync/文件锁/备份语义要在支持的平台验证。备份运行中数据库使用 SQLite backup API，不能只复制 world.sqlite 丢掉 WAL。

## 5. Delta 粒度与大小控制

- 环境按 tile chunk，人口按 species block×tile chunk；记录 changed chunk replacement 和稳定 axis manifest。物种增加采用追加 ID/新轴版本，删除使用 tombstone，避免所有旧 row 重编号。
- 稀疏变化可用 sorted index/value patch；密集变化改写该 chunk，不生成每元素操作 JSON。
- 单个 revision 的 delta 含所有影响未来的实体、数组、命令/配置更改；报告、照片、embedding 不重复内嵌。
- 默认 checkpoint_interval=50 是待 benchmark 的初值，同时按累计 delta bytes 和 replay budget 触发；先检查用户配置再执行。
- 完整 checkpoint 是逻辑 manifest，未变 chunk 引用旧对象；不会每 50 turn 复制整个世界所有字节。
- 不能承诺任意历史精确回放且永远固定空间。若每格每回合都变化，下界仍接近 O(changed_cells×turns)。通过压缩、chunk 去重、归档控制；删除历史必须显式 retention policy，告知 replay 范围。
- metrics 与必要 domain events 可长存；进度/heartbeat、debug 大对象和原始 AI completion 有独立大小/保留上限，不进入 authoritative world snapshot。

## 6. Replay 与历史读取

resolve(timeline,revision) → 查目标 ancestry → 最近可达 checkpoint → 顺序 apply deltas → 每步核对 base/result hash → 验证模型无关结构约束 → read-only WorldSnapshot。

**Replay 不执行 RNG、Stage 或 LLM。** Replay 是还原记录状态；resimulation 是用固定输入/模型重算并与 state hash 比较，二者分别测试。历史 API 输入 turn 时默认解析到该 Turn.end_version，即回合提交边界；随后同turn的玩家编辑必须按显式revision查询，不能随最新head漂移。响应总是返回resolved_version。同 turn 的 annotation 有独立展示 revision。

新 reader 可回放旧数值模型产生的已记录 delta；继续演化需有兼容模型版本或显式 model-upgrade Command。版本升级不能静默改变同存档结果。

缓存使用 timeline/generation/revision/checkpoint hash；历史浏览不修改 active head、资源 manager、embedding store、session callback。API 建议 GET /worlds/{world}/timelines/{timeline}/state?turn=N 与 /events?after=cursor，返回实际 revision/available-range。

## 7. Timeline 与 Copy-on-Write

fork(parent,version) 在短事务中记录完整 parent_world/timeline/generation/revision 与 fork_hash；共享祖先 checkpoint/delta/object references，不复制实体/数组全集。子分支首次更改才写私有 delta/chunk。不得从fork_revision反查父分支当前generation。

祖先查询以固定 fork revision 截断，不能读取 parent 最新 head；并行 branch 写入按 timeline 限定。创建两个分支后父世界继续演化不能改变子分支基线。parent history 删除受 descendant 引用约束；GC 以可达性 mark-and-sweep + grace period/锁执行，不能仅按文件年龄删。

默认 SeedManager 包含 timeline_id，分支自然拥有独立随机轨迹。控制实验可显式记录 common-random-numbers 模式：两个分支共享 rng_namespace 并按 species/事件 key 抽样，其他身份/存储仍隔离。该选项必须进 experiment manifest，不能把换种子造成的差异误当 +4°C 的因果效应。

## 8. 导入、兼容与恢复测试

| 输入/故障 | 预期 |
|---|---|
| legacy game_state.json / .json.gz，version 2.0 | 格式探测→新目录导入；源文件 hash 不变 |
| legacy food web / seed / resource state 缺失 | 报明确 warning，确定性初始化；不伪造历史 |
| 同一档反复导入 | 有 import identity，可选新世界或幂等返回，不能重复加人口 |
| 导入中断/验证失败 | 当前活跃世界不变；临时目标不可发布 |
| delta 缺页、chunk hash 不符、未来 schema | 明确错误；不返回看似完整的世界 |
| turn commit 各 crash point | 只能旧 head 或完整新 head；无半回合 |
| checkpoint 周期边界与随机 turn | replay state hash 等于当时保存的 hash |
| 相同 branch fork 后并发更改 | 父/兄弟不变；共享对象仍可读 |
| pending AI job + 替换load/rewind | version/generation 检查拒绝旧结果 |
| pending parent AI job + fork | job仍绑定parent且不复制给child；结果不可能写child |
| 100/500/1000 turn | 记录 bytes/changed chunk、replay latency、RSS 与 checkpoint cost；不以“文件能打开”代替验证 |

旧存档写出器在迁移期可保留为手工兼容导出，但不承担新时间机与分支语义。当前旧存档没有保存的历史状态无法通过 V2 恢复；UI 应显示 earliest_replayable_turn。
