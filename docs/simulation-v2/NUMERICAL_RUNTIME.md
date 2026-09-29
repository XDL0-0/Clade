# 宿主浮点状态隔离

确定性不能只固定 RNG。旧 Taichi 的 `ti.init(arch=ti.cpu)` 在本机改变线程 MXCSR 的 FTZ 位，使 subnormal 浮点结果被清零。DAZ 还会让输入 subnormal 被视为零：`json.dumps(5e-324)` 可以变成 `0.0`，破坏存档 hash。单独参考模型测试未触发此问题，完整旧/新系统混合回归发现了它。

## 同步边界

`numeric_environment()` 保存调用线程的完整浮点环境，切到默认 IEEE round-to-nearest、gradual underflow，执行同步工作后在 `finally` 恢复原环境。嵌套调用复用外层边界，线程之间互不共享模式。

已覆盖 pipeline.execute、engine.run_turn、reference genesis、FrozenArray 构建、canonical JSON 编码、存储 JSON 解码和 snapshot invariant 验证。Scalar freeze 用位模式识别正/负零，避免 DAZ 将真实 subnormal 错当作零。

Niche 算子另用整数位检查不可表示的微量储备/支出，保留原 reserve bytes，且不产生付费环境收益。其他算子绕过 pipeline 直接执行时，调用者应自行进入数值环境。

这不是进程级永久设置，不改变 NumPy seterr 配置。不得在 scope 中 await，也不得装饰异步函数后误以为 coroutine 内部受保护。AI 网络 worker 不持有该 scope；V2 不永久改变旧 Taichi 的模式。

## 平台和重演范围

当前验证实现使用 Linux/glibc x86-64 的 fegetenv/fesetenv，依据本机 bits/fenv.h 的 FE_DFL_ENV=-1。未验证平台明确失败，不能退回依赖未知宿主模式的运行。支持其他 ABI 需增加实现及模式切换回归。

完整实验室运行当前要求 Linux/glibc x86-64，比仅可读取 SQLite/NPZ 文件更严格。此修复保持已验收配方的默认 IEEE 数值含义，没有修改生态参数。

重演证据基于锁定 Python/NumPy 依赖和模型配方；不承诺未测试 CPU、libm 或依赖升级后逐比特相同。跨运行时比较仍应记录版本并保留 golden fixtures。

## 检查

主动切换 FTZ、DAZ 和舍入控制，覆盖异常恢复、线程隔离、嵌套 scope、不同字节序及 float16/32/64 subnormal、canonical JSON、真实 SQLite/NPZ 回放。旧26stage的100turn golden保持；28stage在100/500/1000关键点比较此前持久化长跑hash。完整回归保留旧JSON/gzip与GPU测试，不通过删除旧测试隐藏宿主污染。
