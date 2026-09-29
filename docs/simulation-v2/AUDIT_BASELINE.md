# Audit Baseline — 实测、范围与交付状态

审计日期：2026-09-29（Europe/Brussels）。源仓库 Pocketfans/Clade，fork XDL0-0/Clade。clone 的 origin 指向用户 fork；upstream 指向源仓库。源码基线：4d1045bfecd412aa26dd55e506c8416b83ac984b。

## 本次范围

本批只新增审计与设计文档。没有修改后端、前端、配置、旧存档或数据库 schema，没有移除 legacy。全仓 499 个 tracked 文件已盘点，227 个 Python 文件语法树解析成功，Python/TS/TSX 合计 380 个文件。清单见 [REPOSITORY_INVENTORY.tsv](REPOSITORY_INVENTORY.tsv)。此清单表示文件盘点，不把它冒充逐行行为验证。

重点沿 main/router → session/container → engine/stage_config → stages/tensor → repositories/save → async AI/SSE → frontend consumer 跟踪实际执行链，并对算法服务、缓存、配置、测试和旧文档交叉检索。对发现的关键控制流进行源码复核和受控探针；未连接真实 LLM、未运行真实玩家世界。静态阅读不等于证明全部生态模型正确，特别是 GPU 长跑与生物学参数仍未验证。

分工补读还覆盖前端叶子组件（造物、基因编辑/基因库、历史、谱系、预测、设置）、CSS、根/模块API文档以及start/stop/diagnose/optimize脚本。测试正文、部分独立渲染组件与资源只做盘点/针对性检索，未对499个文件作逐行语义证明；没有像素级UI验证或执行有清理副作用的运维脚本。主要执行链与配置事实已经复核，不能把全仓文件清单等同于每个UI分支/算法都已运行验证。

## 环境

- frontend：Node 24.19.0，npm 11.17.0，使用仓库 frontend/package-lock.json，npm ci --ignore-scripts --no-audit --no-fund 成功。
- backend：独立 /tmp/clade-audit-venv，Python 3.12.13；安装项目 .[dev]，额外补 pytest-asyncio 和 PyYAML。仓库未声明这两个直接使用的 dev/runtime 依赖；未修改 pyproject。
- 解析版本：pytest 9.1.1、pytest-asyncio 1.4.0、PyYAML 6.0.3、NumPy 2.5.3、Taichi 1.7.4、Pydantic 2.13.5、SQLModel 0.0.47、FastAPI 0.142.0、Starlette 1.7.0、httpx 0.28.1、SciPy 1.18.1。
- 所有 backend 探针设置 DATABASE_URL 指向 /tmp/clade-audit-*.db，LOG_TO_FILE=false。import 张量模块仍会初始化 Taichi CUDA，这是代码现有导入副作用；本轮没有进行真实世界 GPU 长跑或性能压测。
- backend 没有依赖 lock、项目 lint/typecheck 脚本或自有 CI workflow。以下是该环境下的结果，不将依赖浮动导致的问题推定为所有历史安装的行为。

## 实际结果

| 检查 | 结果 | 解释 |
|---|---|---|
| 全部 227 Python 文件 AST parse | 通过 | 仅语法，不等于导入/运行/typecheck |
| frontend npm ci | 成功 | 未更改锁文件 |
| frontend npm run test:run | 11 passed / 3 failed，共14项 | FoodWeb mock仍用species/relationships，当前hook读nodes/links |
| frontend npm run lint | 1 error / 176 warnings | test中引用未配置的react/display-name规则 |
| frontend tsc --noEmit | 8 errors | Sparklines导出、旧报告字段、chart类型、nullable select |
| backend pytest --collect-only -q app | 281 collected / 2 collection errors | species test相对导入；非顶层pytest_plugins |
| 六组 tensor 定向测试 | 91 passed | tradeoff、pressure bridge、tensor state、speciation monitor、metrics、integration |
| simulation 单独 rootdir 测试 | 73 passed / 2 failed | 过期阶段名称/注册期望；44 warnings |
| StageLoader 四模式探针 | 四种均加载同一22阶段standard | YAML顶层mode覆盖传入mode |
| /turns/run 并发标志探针 | 复现运行中标志被拒绝请求清除 | fake engine用asyncio.Event阻塞，未执行世界 |
| 全后端 lint/typecheck | 未运行：项目没有配置该gate | 不宣称通过 |
| 100/500/1000 turn、save增长与内存曲线 | 未运行 | 后续P0 gate，不能用局部单测替代 |
| 旧档 round-trip / checkpoint replay | 未运行 | 本轮未实现存档V2，也未获实际旧档fixture |

现有测试源码有 24 个 test_*.py，另有 simulation/regression_test.py 回归工具；365 个 test_ 函数定义不等于当前成功收集或执行的测试数量。没有测量覆盖率百分比。

## 可复现命令

在 frontend：

~~~bash
npm ci --ignore-scripts --no-audit --no-fund
npm run test:run
npm run lint
./node_modules/.bin/tsc --noEmit
~~~

在 backend，解释器使用独立 Python 3.12 venv；先安装 .[dev]、pytest-asyncio、PyYAML，并设置上述临时 DB 与禁文件日志环境变量：

~~~bash
python -m pytest --collect-only -q app
python -m pytest -q app/tensor/tests/test_tradeoff.py app/tensor/tests/test_pressure_bridge.py app/tensor/tests/test_tensor_state.py app/tensor/tests/test_speciation_monitor.py app/tensor/tests/test_metrics.py app/tensor/tests/test_integration.py
python -m pytest -q --rootdir=app/simulation/tests app/simulation/tests
~~~

模式探针：依次调用 StageLoader().load_stages_for_mode(mode, validate=True)，mode 为 minimal/standard/full/debug，输出 name/order 清单。注意此操作的导入链会初始化 GPU。

并发探针：mock engine.run_turns_async 在 Event 等待；第一请求进入后发第二请求，第二请求400，第一仍pending时检查 session.is_running 为 False。只有 await Event 的 fake engine，没有 repository save 或 autosave 执行。

完整失败日志与探针输出归档在 [evidence](evidence/)；归档仅清理行尾空白与末尾空行。日志包含审计机路径和运行时长，仅用于复核，不能解释为产品性能基准。

文档另经独立源码抽查与契约复核。已修正指标按revision唯一、AI已应用任务的幂等优先级、完整父版本引用、turn历史选择及后置分化transfer归约时点。文档内部链接/代码块与499项清单检查通过；这不代替尚未实现的V2测试。

## 本批阶段报告

| 用户要求项 | 本批结果 |
|---|---|
| Changed files | 无既有 tracked 文件改动 |
| New files | docs/simulation-v2 中七份要求文档、本基线、文件清单和证据日志 |
| Removed legacy code | 无 |
| Tests added | 无产品测试；使用临时审计探针，不把文档阶段变成业务重构 |
| Tests passed | 91 tensor + 73 simulation + 11 frontend；同时存在上述失败，非全量绿 |
| Known issues | 见 ARCHITECTURE_PROBLEMS；尚无全局版本/CAS、durable AI jobs、完整replay闭包 |
| Performance impact | 运行时代码未改；未做运行性能结论 |
| Save compatibility | 旧读取器与格式均未改；V2只完成设计，旧档未来导入需fixtures验证 |
| Next migration step | B0–B3修复测试/配置基线与回归oracle，再实现纯Foundation接口 |
