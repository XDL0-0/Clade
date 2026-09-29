# AI Job Architecture（设计，尚未实现）

目标：LLM 只补名称、描述、器官文字、解释和事件摘要。LLM 不决定死亡、繁殖、胜负、人口、trait 数值、地图、分化是否发生。将现有 ModelRouter 保留为 provider adapter，移出世界写入路径；不能只在现有 AI 回调外套一个 version 判断就宣称完成解耦。

## 1. 与回合的时序

~~~text
Deterministic simulation → finalized Proposal/Event + fallback text
 → validate + commit world revision N、AIJob(expected=N)、outbox
 → worker receives frozen job input → provider → schema validation
 → NarrativeCommitService 比较 world/timeline/generation/revision N
 → apply narrative annotation 或 STALE（无世界/展示写入）
~~~

按用户总流程，AI Enrichment 在回合里承担任务准备、结构验证和模板填充，网络 enrich 在提交后异步执行。没有“未命名物种尚不存在”的中间态；确定性 ID、临时名称与数值状态已经可用。AI 慢、断线、失败均不阻塞下一回合。

严守 current world version != expected → STALE。连续快进可能使许多叙事任务过期，这是正确行为；通过合并重大事件批次、只 enrich 稳定 head 或有界交互等待减少浪费，不通过放宽 stale 检查解决。旧任务不可改当前物种，也不可在另一个活跃存档上写名字。

如果未来支持给固定历史事件补注释，必须另建以 immutable snapshot/event 为对象、用户可区分的工作流；本阶段不借此绕过严格 stale 条件。

## 2. AIJob 数据与状态机

| 字段 | 含义 |
|---|---|
| job_id | 稳定身份，重试不换 ID |
| world_id / timeline_id / turn_id | 固定目标，不查询 session 的当前存档来猜 |
| expected_world_version | generation + revision 的完整值 |
| proposal_id / event_ids / snapshot_id | 引用已冻结事实 |
| job_type / schema_version / prompt_version | 任务与验证契约 |
| model/provider configuration hash | 用于审计；不保存 API key |
| idempotency_key / input_hash | scoped unique；同键不同内容冲突 |
| status / attempt / max_attempts | durable state |
| created_at / started_at / completed_at | 运维时间，不参与生态 hash |
| lease_owner / lease_until / fencing_token | worker crash 可恢复；旧 lease 结果不可提交 |
| result / error / validation_errors | 结构化结果或有上限的诊断 |
| token_usage / latency / fallback_used | 成本与质量观测 |

状态转换：QUEUED → RUNNING → VALIDATING → READY → APPLIED；可进入 RETRY_WAIT → QUEUED、FAILED、CANCELLED、STALE。FAILED/CANCELLED/STALE/APPLIED 为终态。READY 只表示结构有效，并不表示可以写世界。

## 3. 原子性与幂等

- 创建任务与对应 turn commit/outbox 同一 SQLite 事务；回合回滚不能留下可执行的孤儿任务。
- UNIQUE(world,timeline,generation,idempotency_key)。key 包含 kind/proposal/event group/expected revision/prompt+schema version；输入 hash 比较防同键不同请求。
- worker 原子领取 lease，超时后可重领；以递增 fencing token 处理迟到 worker。外部模型请求可能重复收费，但本地 apply 必须至多一次；不承诺外部 exactly-once。
- callback 只交付 result envelope 给协调器，无 repository、mutable Species、map manager 或 DB session 引用。
- NarrativeCommitService 在一个事务内先读取job、核对scope与input identity。若已 APPLIED，幂等返回既有提交结果，不写任何新数据；若 CANCELLED/FAILED/STALE，保持终态并拒绝。已完成任务不会因后续head推进而改写为STALE。
- 对尚未应用的结果，读取该job所属timeline head；核对 world/timeline/generation/revision、lease token、proposal identity和可应用状态。
- 若 head 不匹配：记录 STALE 和原因，result 可保留为受限诊断；**不产生 annotation、不更新物种、不发 SpeciesUpdated、不改变世界 head**。
- 全部符合条件后，仅写 annotation、更新 narrative_revision、标记 APPLIED、写 NarrativeReady/outbox。
- simulation revision 不因文案改变；所有影响模拟的命令必须递增它。annotation 也需唯一 job/target/version 约束，防重复 append 历史。

读取 head 后再普通 upsert 不足以避免 TOCTOU；版本检查与写入必须同事务，使用 row count/CAS 和唯一约束，而不是 Python 内存字典独自保证。

## 4. Schema 与处理层

采用 Pydantic strict 配置、extra='forbid'、受限字符串/列表长度、Literal job kind、明确必填 ID；生成 JSON Schema 给支持结构化输出的 provider。其他 provider 返回 JSON 后同样本地校验。

| Schema | 允许的结果字段 | 不允许 |
|---|---|---|
| SpeciesNarrativeResult | species_id、common_name、latin_name、description、organ_descriptions、source_event_ids | population/status/traits、器官数值能力 |
| SpeciationNarrativeResult | proposal_id、各已确定 child_id 的名称/描述、分化解释、source_event_ids | 子种数量、亲子身份、人口拆分、speciation_score |
| AdaptationNarrativeResult | proposal_id、species_id、summary、tradeoff_explanation、source_event_ids | trait delta、预算、fitness 数值 |
| HybridizationNarrativeResult | proposal_id、确定 child_id 的名称/描述、parent narrative、source_event_ids | 是否杂交成功、遗传比例、fertility |

校验分两层：结构字段合法；目标 ID 与 job frozen input 完全一致，source_event_ids 是输入子集、数量正确、无重复未知子种。表述的证据只来自 trace/events；不让模型虚构确定原因。无法验证的生物学解释标明“叙事性解释”，数值因果展示直接读 trace。

有限重试策略：可重试网络/限流按配置 backoff，结构失败允许一次受限 repair，所有尝试保留同一 job id。最大尝试数/总 deadline/token budget 明确。无 schema 支持也不能落回 regex 提取任意 Python/JSON 子串后直接修改物种。最终使用固定模板，任务 FAILED/fallback 不使模拟失败。

模板名称基于稳定 species ID，不用时间或全局 random。AI completion、provider 温度、请求时间都不进入确定性模拟输入或 RNG stream。

## 5. 生命周期和 SSE

替换当前运行世界的load/new/rewind/stop经过session coordinator：停止接收旧scope命令 → 标记其未完成job CANCELLED或版本失效 → 发取消信号 → 等待可取消子任务结束 → 切换新的world/timeline/generation。Python thread/GPU不可强制取消，必须无写能力或隔离为worker process。

单纯创建fork不改变parent head，也不把parent job复制或重新指向child。版本比较针对job所属timeline的head，而不是浏览器当前选择的世界；被允许继续运行的parent任务只能给parent写注释。若fork后同时停止parent，则另发scope明确的cancel命令。这样既拒绝向child写旧结果，也不破坏并行实验。

服务 shutdown 等待/释放 tracked tasks；进程重启从持久队列恢复，而非假设内存 create_task 仍在。客户端断开不等于取消模拟；任务是否取消由明确命令决定。

SSE progress 是短期观测，domain event 来自提交后的 outbox。每个客户端有独立 cursor，不能从共享队列 destructive-pop 导致另一个窗口漏事件。失败重发按 event_id 去重；没有 subscriber 时 DB 仍是事实源。job_queued/job_stale/job_failed/NarrativeReady 分开表示。

## 6. 现有接缝与迁移顺序

1. ModelRouter 的 invoke/stream 接口适配成 provider，保留现有连接配置；解析、repair、schema 校验移到 validators/schemas。先用 fake provider 做契约测试。
2. 拆开 speciation.process_async、_call_batch_ai、_queue_deferred_request 中的数值决策与文字生成；文字请求变成 durable job，不携带可变 Species 引用。当前 _call_batch_ai 的 batch/内共生任务有 gather 等待，仍需为取消、重试和世界切换提供持久身份。AI产物只填 presentation，不再把trait_changes/形态回写生态。
3. 将 hybridization、organ evolution、description enhancer、造物路由中的 AI 后写入逐一分类：模拟提案移到确定性规则；文字注释走 NarrativeCommitService。
4. 初期可以在 old API 的异步入口加入 generation/CAS 防护，但这仅是安全补丁，不算已完成 Stage 解耦。全局 DB 写者未收敛前，禁用同进程多世界实验。
5. 分化、叙事和报告保留独立预算：只对重大事件/新物种排队，按队列积压限流；不每物种每回合调用 LLM。

## 7. 必须先有的测试

| 场景 | 断言 |
|---|---|
| 两个 worker 同时完成同 job | annotation/outbox 各一条 |
| job已APPLIED，head推进后重复投递 | 返回原提交结果；保持APPLIED；无新写入 |
| 相同幂等键、不同 input hash | conflict；不覆写原任务 |
| AI 完成前推进一回合 | STALE；生态与注释 hash 均不变 |
| AI 完成前替换load/new/rewind，turn 恰好相同 | 身份/generation 拒绝，不写替换后的世界 |
| AI 完成前fork并切换UI到child | job只验证parent scope；不复制任务、不写child |
| 完成与取消/commit 并发 | transaction/fencing 保证只有一个终态 |
| lease 过期后旧 worker 返回 | 旧 token 不能覆盖新 attempt |
| schema 多余 population/trait/status 字段 | 拒绝或有限 repair，不 apply |
| child ID 指向别的世界/多返回物种 | 拒绝 |
| provider 超时/无效 JSON/重启 | fallback 可用、job可恢复、模拟相同 |
| 相同输入，AI 关闭/极快/极慢/不同文本 | 数值 state hash 完全一致 |
| 重复/丢失/断线后 SSE | cursor 补发、幂等 invalidation |

上述测试不使用真实 API key、不依赖 sleep 竞态“碰巧发生”；使用 barrier/latch 和 fake clock 精确控制顺序。
