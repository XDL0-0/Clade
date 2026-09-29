# 演化沙盒运行说明

玩家入口与操作见 [游玩指南](PLAY_GUIDE.md)。`/play` 和 `/lab` 均可进入。
2026-09-30 起按用户最新要求停止后续测试与验证，开发重点转为可玩性；下文保留后端接口说明。

`/lab` 是新的 CPU Simulation V2 入口。经典页面与旧存档继续使用原有路径。
实验室世界采用独立 SQLite/NPZ 存储，不与旧全局物种数据库混用。
当前实现与限制以 [实施记录](IMPLEMENTATION_STATUS.md) 为准，最初的目标文档不是完成清单。

## 启动

当前完整数值运行时已验证 Linux/glibc x86-64；其他 ABI 明确拒绝执行。原因与浮点隔离边界见 [数值运行时](NUMERICAL_RUNTIME.md)。

在已按 `backend/TESTING.md` 安装依赖的 Python 3.12 环境中：

```bash
cd backend
python -m app.api.v2 --root /absolute/path/to/lab-worlds --port 8022
```

另一个终端启动前端：

```bash
cd frontend
npm ci
BACKEND_PORT=8022 npm run dev
```

在 Vite 输出地址后加 `/lab`。CLI 默认只监听 `127.0.0.1`。
也可使用原应用启动方式：原应用 lifespan 会挂载 `/api/v2`，存储位于配置 `data_dir/simulation-v2`。
独立 CPU CLI 不导入旧 GPU/ORM 容器；原应用仍会初始化其旧运行时。
这里没有多用户鉴权，部署到公网前需要单独完成访问控制。

## 操作语义

1. 创建世界时固定 seed、地图大小和物种容量。相同模型配方、seed、命令与 RNG namespace 才构成可重演输入。
2. 推进提交完整 world/timeline/generation/revision 和稳定幂等键。网络结果不明时重试原请求，不能用新键猜测推进。
3. 历史滑块只读 `checkpoint + delta`，不会重新跑模拟或调用 AI。地图、物种、trace、profile 都按选择的历史快照读取。
4. 从历史分叉共享存储对象。配对升温实验从同一快照创建 control/warm 两条线，以同一 RNG namespace 推进；增加的是温室 forcing，温度按模型的弛豫过程响应，不会立即跳高 4°C。
5. 回溯替换当前 timeline 会增加 generation。旧历史仍保留；旧未完成 AI 任务无法写入新 generation。
6. SSE 从持久 outbox 按客户端独立 cursor 补发，React Query 按事件失效。事件日志面板显示选中 timeline 的日志，历史叙事则只沿所选快照的语义祖先读取。

每个数值世界保存 model/stage/RNG manifest。已知旧参考配方按原版本续算；未知配方明确拒绝，不能用新参数静默修改既有世界。
旧 JSON/gzip 可由独立导入器读取，但缺失的过去历史不能恢复；导入旧世界不代表已可在新生态模型继续运行。

## AI 叙事

显著适应和成功分化事件产生有上限的持久任务，默认每回合最多四个。
Job planner 不发网络请求，任务与世界同事务提交。叙事完成只写 annotation，数值 hash 不变。

测试或离线使用可执行有界模板批次：

```bash
cd backend
python -m scripts.run_narrative_jobs --root /absolute/path/to/lab-worlds --limit 4
```

这条命令不调用真实 LLM。API 将它标记为 `offline_template`，不冒充模型输出。
真实 provider 需通过已测试的 `ModelRouterNarrativeProvider` 和 `NarrativeWorker` 显式注入；当前启动命令不会自动启动付费请求。
连续快进会让旧任务进入 STALE；先在某个 head 完成的叙事可继续在其后代历史中读取，已被回溯丢弃的未来不会混入历史。
`fallback_used=false` 本身不证明使用了 LLM；来源未标记时显示 `unspecified_provider`。

## 观测范围

Stage profile 为所选提交的实际运行时长；duration 不进入确定性结果。
CPU RSS 属于当前服务进程，存档大小是整个 store 目录的近似值，不能解释成某一条 timeline 独占空间。
GPU 显存和未报告的 token usage 为 null。物种数、栖息记录数、AI 任务状态使用所选完整版本。
形态示意由现有 traits 派生，仅帮助阅读模型数值，不是经过验证的生物解剖结构。

## 开发状态

历史测试和长跑记录保留在实施记录中。它们不代表后续玩法代码已经验证；本轮按用户要求不运行新验证。
