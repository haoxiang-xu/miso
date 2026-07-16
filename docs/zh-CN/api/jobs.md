# 耐久任务 API 参考

`unchain.jobs` 是 D4.1 的本机后台任务耐久层。真正的子进程由一个独立的
detached worker 持有；agent runtime 只保存任务状态、日志和游标。因此即使重建
toolkit 或 `ProcessJobSupervisor`，正在运行的任务也不会丢失。

## 保证与边界

- `(execution_id, idempotency_key)` 会得到一个稳定的 `job_...` 标识。
- 不可变启动 spec 会把 store identity、进程参数、超时和 environment profile
  digest 绑定到该标识；同一个 key 配不同意图会抛出
  `DurableJobConflictError`。
- 每个文件 store 都在 `store.json` 中保存随机且持久的 `store_id`。每个 job spec
  都携带该 ID，每次操作也会确认当前 manifest 仍属于同一 store。删除后在同一路径
  重建会产生新的逻辑 store。job 数据在物理上按 `stores/<store_id>/jobs` 隔离，因此
  已经在途的 stale generation 最多重新产生一个旧 namespace orphan，不可能写入当前
  generation。
- 即使多个 runtime 同时启动同一任务，跨进程 claim 也只允许一个 worker 启动
  用户命令。
- 查询、日志和取消都按 `execution_id` 隔离；其他 execution 只能看到“未找到”。
- `ProcessJobSupervisor` 在构造时捕获一份不可变 `JobEnvironmentProfile`。规范化后的
  完整 mapping 会原样传给 detached worker 和用户子进程；耐久状态只保存其 SHA-256
  digest。构造完成后的 ambient environment 变化不会影响它。
- `close()` 只断开 supervisor；只有 `cancel()` 才请求终止任务。
- worker lease 第一次被观察为 stale 时不会立刻进入终态。supervisor 会先记录一份
  进程内 suspicion，并让任务保持 `queued`、`starting` 或 `running`；只有同一份
  lease evidence 在完整的单调时钟 `suspect_grace_ms` 窗口内都没有变化，才能通过
  compare-and-set 进入耐久终态 `outcome_unknown`。
- `outcome_unknown` 是单调终态：supervisor 不会伪造成功，也不会擅自重跑。它只是
  orchestration 结论，不能证明失联的外部进程已经停止。
- stdout/stderr、终态以及 UTF-8 字节游标都能跨新 runtime 保留。
- 耐久工具审批会绑定 Jobs handler、持久 `store_id`、解析后的 store 路径、规范化
  shell intent 和 environment profile digest；冷恢复时移除 Jobs、替换或移动/复制
  store、改变解析后的 cwd，或者重建出不同环境 profile，都会在命令执行前 fail
  closed。

这个 adapter 保证同一台主机、同一个文件系统上的进程重启耐久性。它不是分布式
调度器，也不承诺跨机器的 exactly-once。D4.1 的 `wait()` 仍是有上限的阻塞等待；
持久化 `WAITING_JOB`、释放执行 worker、自动唤醒以及先写 tool journal 再恢复模型，
属于 D4.2。

environment digest 绑定的是字符串，而不是这些字符串所指向的 executable 或文件
内容。D4.1 不会冻结文件系统、cwd 内容、PATH target、container image 或 host；若这些
身份也必须可验证，应使用外部不可变执行环境。

终态任务目录不会自动 GC；应用需要按每路日志上限规划容量，并自行设置 retention。
orphan store-generation 目录同样不会自动清理。任务完成语义跟踪的是启动的 shell
leader；如果 detached descendant 比 leader 活得更久，任务进入终态后不会继续管理
这些 descendant。

## Store identity

`JsonFileJobStore(base_dir)` 会把 `base_dir` 解析成绝对路径，并创建或校验根目录下的
`store.json` manifest。多个进程首次同时打开时，会在锁内共享一次 manifest 创建。
manifest 保存 `STORE_MANIFEST_SCHEMA_VERSION`、随机 `store_id` 和创建时间，并把当前
物理 namespace 指向 `base_dir/stores/<store_id>/jobs/...`。

identity 规则全部 fail closed：

- 重新打开同一个完整目录会保留 `store_id`；
- 删除后在同一路径重建会产生新的 `store_id`；
- 如果 manifest 被底层替换，之前已经打开的 store 对象会拒绝新的后续操作；
- 如果某个 stale critical section 在 unlink/recreate 竞态前已经通过 identity check，它
  仍固定写向旧 generation path。旧 lock file 与替换后的 lock file 属于不同 inode，可能
  已无法互相协调，但 stale write 最多只会在旧 `store_id` 下形成 orphan，不能修改当前
  generation；
- 已存在 job 数据却缺失 manifest 属于 corruption，不能自动生成替代身份；
- 每份不可变 job spec 都保存 `store_id`，并拒绝来自其他逻辑 store 的数据。

完整复制整个目录会保留逻辑 `store_id`，但耐久审批还会绑定解析后的 base path。因此，
在原路径暂停的审批不能从副本路径继续。备份或迁移时必须一起处理 manifest 和它选中的
generation，不能单独重新生成 `store.json`。只有在确认没有 stale worker 还会写入后，
才能清理 orphan generation。

## Environment profile

`JobEnvironmentProfile.capture(environment=None)` 会校验并冻结一份 string-to-string
环境 mapping。省略 `environment` 时，它会在 supervisor 构造时捕获 `os.environ`。
`PYTHONPATH` 各项会解析成绝对路径，并把当前运行的 Unchain source root 放在最前面，
保证 detached worker 加载同一份代码。冻结宿主不会把临时 bundle 解压根目录注入
`PYTHONPATH`：可信宿主 launcher 已经提供内嵌 worker，而把易变的解压路径写进 profile
会导致新 worker 无法验证 digest。得到的完整 mapping 会同时传给 worker 和 child。

profile entries 不进入 `repr`，也不会写入 job store 或耐久审批 journal。持久化的只有
`environment_digest`，并会出现在 `DurableJobSnapshot` 和 `DurableJobHandle` 上。即使
如此，应用仍应把活跃的 profile 对象视为含有 secret 的内存。

持久化 digest 是无盐、确定性的 SHA-256，其目标是完整性绑定和 environment drift
检测，而不是保护 secret 的机密性。能够读取 store 的攻击者，理论上可以离线尝试猜测
低熵环境值。高敏场景应使用高熵 token 和外部 secret provider；未来可通过
profile-id/secret-provider 集成保留这层绑定，但不能把当前 digest 当作 secret vault。

一个 supervisor 的所有任务共用一份 frozen profile。如果新 supervisor 遇到一项尚未
被 claim 的 `queued` job，而其 digest 与当前 profile 不同，当前 caller 会在启动任何
wrapper 或用户命令之前收到 `DurableJobConflictError`。它不会把共享 job 变成终态，也
不会修改它；job 仍保持 `queued`，因此 profile 相同的 supervisor 仍能恢复。detached
wrapper 还会在取得 claim 前独立复核继承到的 profile digest。如果审批暂停发生在 job
reserve 之前，profile drift 会更早以 interaction integrity failure 被拒绝。

## Lease reconciliation 与 `outcome_unknown`

worker 取得 launch claim 后会立刻启动独立 heartbeat lease。heartbeat 会持续覆盖命令
收尾、日志 drain/fsync 以及耐久终态写入，避免正常 finalization 被误判成 worker 失联。

超过 launch grace 后如果 heartbeat 缺失或 stale，reconciliation 分两步：

1. 第一次观察保留原 public status，并在进程内记录一份 suspicion signature，其中包括
   state revision、worker identity、heartbeat sequence/timestamp，以及适用时的 claim
   generation。
2. 只有该 signature 在 `suspect_grace_ms` 内始终不变，后续 reconcile 才可能写入
   `outcome_unknown`。最终 store transition 还会在 job lock 内重新 compare state
   revision/status 和 heartbeat sequence；queued pre-launch suspicion 还会 compare claim
   id。任何被观察到的进展都会重置 grace。

suspicion 属于当前 supervisor，并由观察驱动。`inspect()`、`poll()`、`wait()`、
`cancel()` 或 `reattach()` 进行 reconcile 时才会推进；没有独立后台 timer 自动把它
变成终态，新建 supervisor 也会重新开始 suspicion window。结构无效的 claim evidence
属于 integrity failure，不享受 stale-lease grace。

`outcome_unknown` 会让 orchestration 看到 `completed=True`，但日志不会被视为 final，
因为外部进程可能仍在写入；后续 poll 仍可能读到新字节。它已经是终态，因此
`cancel()` 不会再写取消 marker，需要 operator 或 adapter-specific reconciliation 处理。

## 日志 cursor 语义

`poll()`、`wait()` 和 `cancel()` 都会消费同一个 job 级持久 cursor；`inspect()` 和
`reattach()` 不消费日志。stdout 和 stderr 使用独立的 UTF-8 byte offset，但会在同一个
job lock 内原子更新。多个 consumer 会瓜分一条流，而不是各自收到一份副本。

public result 字段含义如下：

| 字段 | 含义 |
| --- | --- |
| `stdout_offset`、`stderr_offset` | 本次 result 开始读取时的 byte offset。 |
| `next_stdout_offset`、`next_stderr_offset` | 已为共享 consumer 持久化的下一 byte offset。 |
| `stdout_available`、`stderr_available` | 生成 result 时观察到的 stream 大小。 |
| `offset_unit` | 固定为 `"utf8_bytes"`。 |

`max_output_chars` 会分别作用于每一路 stream。末尾不完整的 UTF-8 code point 会留给下次
读取；已经确认 final 的 stream 可以把不完整的最终序列 flush 成 Unicode replacement
character。

cursor 会先耐久推进，然后 result 才返回调用方；对于 Agent tool call，它也早于 result
写入 transcript。如果在这个间隙崩溃，这些字节可能不会再次交给模型。因此当前是
“日志存储耐久”，还不是“结果交付可崩溃重放”。`truncated` 既可能表示仍有字节可供
下次 cursor 读取，也可能表示 producer 已达到日志 cap；后者应通过
`stdout_truncated` 和 `stderr_truncated` 判断。

## 公共接口

| 名称 | 作用 |
| --- | --- |
| `JsonFileJobStore` | 原子保存本机任务 spec、状态、claim、heartbeat、取消标记、日志、游标、持久 `store_id` 和 generation-isolated job path。 |
| `ProcessJobSupervisor` | 编排 `start`、`inspect`、`poll`、`wait`、`cancel` 和 `reattach`。 |
| `JobEnvironmentProfile` | 在构造时冻结的规范化环境；entries 只留在内存，持久化的只有 digest。 |
| `DurableJobHandle` | 稳定的外部任务身份。 |
| `DurableJobSnapshot` | 不可变的耐久状态视图。 |
| `DurableJobResult` | 可直接 JSON 化、同时支持属性访问的 poll/wait/cancel 结果。 |
| `DurableShellJobPlugin` | 把后台 `CoreToolkit.shell` 调用路由给 supervisor。 |
| `JobsModule` | 在 `AgentBuilder` 上注册 shell plugin；从 `unchain.agent` 导出。 |

该包还导出 `DurableJobError`，以及未找到、ownership、冲突和存储损坏等类型化错误，
同时导出 job、environment profile 和 store manifest 的 schema 常量。

## ProcessJobSupervisor

```python
ProcessJobSupervisor(
    store,
    *,
    python_executable=None,
    worker_command_prefix=None,
    worker_environment_overlay=None,
    heartbeat_stale_ms=None,
    launch_grace_ms=None,
    poll_interval_s=None,
    default_max_log_bytes=50 * 1024 * 1024,
    poll_interval_ms=None,
    heartbeat_interval_ms=None,
    heartbeat_stale_after_ms=None,
    cancel_grace_ms=1000,
    startup_timeout_ms=None,
    suspect_grace_ms=None,
    monotonic_clock=None,
    environment=None,
)
```

解析后的默认值是：heartbeat stale 5,000 ms、launch grace 2,000 ms、wait-loop poll
50 ms，以及一个 stale-heartbeat window 长度的 suspicion grace。
`heartbeat_stale_after_ms`、`startup_timeout_ms` 和 `poll_interval_ms` 是对应主参数的
兼容 alias，不能与主参数同时提供。`environment` 接受 mapping 或已有的
`JobEnvironmentProfile`，并且只在构造时捕获一次；单个 `start()` 不能换另一份 profile。

worker command prefix 默认是
`(python_executable or sys.executable, "-m", "unchain.jobs._worker")`。
可信宿主 adapter 可通过 `worker_command_prefix` 提供不可变的等价前缀；例如冻结应用
可以传 `(sys.executable, "--durable-job-worker")`，再由自身分发这个私有入口。它与
`python_executable` 互斥。该前缀属于 orchestration 配置，绝不能来自模型或 tool
arguments；改变它不会改变公开 job identity 或进程 intent。宿主必须确保该入口实际
分发内嵌的 `unchain.jobs._worker`，并在 worker 校验 job 前重建与捕获时完全一致的环境。

`worker_environment_overlay` 是不可变的 string mapping，只会叠加到可信 wrapper 的
`Popen` 环境。它明确不进入 canonical `JobEnvironmentProfile`、环境 digest、耐久进程
intent 或用户 child 环境。它用于 PyInstaller 的
`PYINSTALLER_RESET_ENVIRONMENT=1` 这类宿主 bootloader 控制。自定义 wrapper 必须在
进入 `unchain.jobs._worker` 前移除这份临时 overlay，并重建完全一致的 canonical
profile；否则 worker 的 digest 校验会拒绝本次启动。

主要方法：

- `start(..., execution_id, idempotency_key, argv, cwd, timeout_ms)` 先保留稳定身份，
  再启动独立 worker。`intent_digest` 可省略，此时 supervisor 会根据包含 frozen
  environment digest 的进程意图计算。
- `inspect(job_id, execution_id=...)` 返回 `DurableJobSnapshot`，并核对 worker
  存活状态。它不是 pure read：可能安全恢复尚未 claim 的 queued launch，或者推进
  lease suspicion。queued environment mismatch 会抛出 `DurableJobConflictError`，
  但不会修改共享 job。
- `poll(...)` 消费下一段持久化日志并推进共享 cursor。
- `wait(..., timeout_ms=...)` 只等待调用方给出的时长；它还不会把 execution 变成
  可长期休眠的耐久 checkpoint，并会在返回前消费一段日志。
- `cancel(...)` 先持久化取消标记，再等待实际持有子进程的 worker 写入终态；
  supervisor 不会向无法验证身份的 PID 发信号，其 result 也会消费一段日志。
- `reattach(execution_id)` 会 reconcile 该 execution 的任务，并安全恢复 environment
  digest 与当前 supervisor 一致的 queued launch。profile 不匹配时当前 caller 收到
  `DurableJobConflictError`，job 则保持 queued，留给匹配的 supervisor 恢复。
- `close()` 只释放当前 supervisor 对象。

## 接入 Agent 的 shell

```python
from pathlib import Path

from unchain import Agent
from unchain.agent import JobsModule, ToolsModule
from unchain.jobs import JsonFileJobStore, ProcessJobSupervisor
from unchain.toolkits import CoreToolkit

workspace = Path.cwd()
supervisor = ProcessJobSupervisor(
    JsonFileJobStore(Path.home() / ".unchain" / "job-store" / "my-app")
)

agent = Agent(
    name="durable-worker",
    modules=(
        ToolsModule(tools=(CoreToolkit(workspace_root=workspace),)),
        JobsModule(supervisor=supervisor),
    ),
)
```

`JobsModule` 不会替换 shell schema。前台命令和旧的进程内 task id 仍走原路径；
只有 `run_in_background=True`，以及 `task_id` 以 `job_` 开头的生命周期调用会被
拦截。原有的确认、拒绝和修改参数语义都会保留。耐久后台启动必须有非空
`session_id`；安装 `JobsModule` 后，如果省略它会 fail closed，不会悄悄退回进程内
任务。即使 `ToolOptimizerModule` 把 shell 放进 deferred executor，它也会重新经过
同一个 Jobs handler，而不会退回旧的内存 task runtime。

请使用按应用隔离、持久、私有并且位于 tool 可写 workspace 之外的 store 目录；
`session_id` 是该 store 内的 execution identity。日志可能包含命令输出，不可变 spec
会保存命令参数和 environment digest。原始环境 entries 只存在于活跃的
`JobEnvironmentProfile`，不会进入 store JSON。在 POSIX 上，文件存储会创建仅 owner
可访问的目录和文件。如果需要更强的对抗性隔离，必须使用独立服务或 OS identity；
同一用户身份运行的本机 shell 不是安全沙箱。
