# Runtime API 参考

核心执行类型：kernel loop、provider 抽象（`ModelIO`）、模型 turn 结果、工具调用、token 统计和运行结果。

| 指标 | 值 |
| --- | --- |
| 类数量 | 5 |
| Dataclass | 10 |
| 协议 | 2 |
| 仅内部类型 | 0 |

## 覆盖地图

| 类 | 源码 | 导出 | 类型 |
| --- | --- | --- | --- |
| `ToolCall` | `src/unchain/kernel/types.py` | subpackage | dataclass (frozen) |
| `TokenUsage` | `src/unchain/kernel/types.py` | subpackage | dataclass (frozen) |
| `ModelTurnResult` | `src/unchain/kernel/types.py` | subpackage | dataclass (frozen) |
| `KernelRunResult` | `src/unchain/kernel/types.py` | subpackage | dataclass (frozen) |
| `ModelTurnRequest` | `src/unchain/providers/base.py` | subpackage | dataclass (frozen) |
| `ModelIO` | `src/unchain/providers/base.py` | subpackage | protocol |
| `KernelLoop` | `src/unchain/kernel/loop.py` | subpackage | class |
| `CompletionEvaluation` | `src/unchain/runtime/completion.py` | subpackage | dataclass (frozen) |
| `CompletionPolicy` | `src/unchain/runtime/completion.py` | subpackage | dataclass (frozen) |
| `CompletionPolicyRunner` | `src/unchain/runtime/completion.py` | subpackage | dataclass |
| `ExecutionFence` | `src/unchain/execution.py` | subpackage | dataclass (frozen) |
| `ExecutionLease` | `src/unchain/execution.py` | subpackage | dataclass (frozen) |
| `ExecutionLeaseConfig` | `src/unchain/execution.py` | subpackage | dataclass (frozen) |
| `ExecutionLeaseStore` | `src/unchain/execution.py` | subpackage | protocol |
| `ExecutionRuntime` | `src/unchain/execution.py` | subpackage | class |
| `ExecutionGuard` | `src/unchain/execution.py` | subpackage | class |

### 执行所有权

`ExecutionRuntime` 从 `ExecutionLeaseStore` 获取 `ExecutionGuard`。Guard 持有有时限的 `ExecutionLease`，并把其中的 `ExecutionFence` 传给原子 session 写入。`ExecutionLeaseConfig` 控制 TTL 与 heartbeat 间隔。`build_runtime_loop` 会为 memory-backed 内置 store 自动接线；构造 loop 时也可显式注入 runtime。Lease 冲突和过期 fencing token 会通过导出的 `ExecutionLeaseError` 异常体系 fail closed。

### `src/unchain/kernel/types.py`

跨 kernel、provider 和 agent 层共享的不可变值类型。

## ToolCall

Frozen dataclass，表示模型请求的单次工具调用。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/kernel/types.py` |
| 继承/协议 | `-` |
| 导出状态 | 通过 `unchain.kernel` 导出。 |
| 对象类型 | Dataclass (frozen)。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `call_id` | `str` | 构造时必需。 |
| `name` | `str` | 构造时必需。 |
| `arguments` | `dict[str, Any] \| str \| None` | 构造时必需。 |

### 最小调用示例

```python
ToolCall(call_id="call_abc", name="search_text", arguments={"pattern": "foo"})
```

## TokenUsage

Frozen dataclass，用于单次模型 turn 的 token 统计。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/kernel/types.py` |
| 继承/协议 | `-` |
| 导出状态 | 通过 `unchain.kernel` 导出。 |
| 对象类型 | Dataclass (frozen)。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `consumed_tokens` | `int` | 默认值：`0`。 |
| `input_tokens` | `int` | 默认值：`0`。 |
| `output_tokens` | `int` | 默认值：`0`。 |

## ModelTurnResult

Frozen dataclass，由 `ModelIO.fetch_turn()` 返回，包含模型的 assistant 消息、工具调用和 token 统计。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/kernel/types.py` |
| 继承/协议 | `-` |
| 导出状态 | 通过 `unchain.kernel` 导出。 |
| 对象类型 | Dataclass (frozen)。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `assistant_messages` | `list[dict[str, Any]]` | 构造时必需。 |
| `tool_calls` | `list[ToolCall]` | 构造时必需。 |
| `final_text` | `str` | 默认值：`""`。 |
| `response_id` | `str \| None` | 默认值：`None`。 |
| `reasoning_items` | `list[dict[str, Any]] \| None` | 默认值：`None`。 |
| `consumed_tokens` | `int` | 默认值：`0`。 |
| `input_tokens` | `int` | 默认值：`0`。 |
| `output_tokens` | `int` | 默认值：`0`。 |
| `cache_read_input_tokens` | `int` | 默认值：`0`。 |
| `cache_creation_input_tokens` | `int` | 默认值：`0`。 |

## KernelRunResult

Frozen dataclass，由 agent/kernel 执行入口返回，包含对话、状态以及可选 continuation/interaction 状态。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/kernel/types.py` |
| 继承/协议 | `-` |
| 导出状态 | 通过 `unchain.kernel` 导出。 |
| 对象类型 | Dataclass (frozen)。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `messages` | `list[dict[str, Any]]` | 最终对话消息。 |
| `status` | `str` | 运行结果状态。 |
| `continuation` | `dict[str, Any] \| None` | 默认值：`None`。 |
| `human_input_request` | `dict[str, Any] \| None` | 默认值：`None`。 |
| `consumed_tokens` | `int` | 默认值：`0`。 |
| `input_tokens` | `int` | 默认值：`0`。 |
| `output_tokens` | `int` | 默认值：`0`。 |
| `last_turn_tokens` | `int` | 默认值：`0`。 |
| `last_turn_input_tokens` | `int` | 默认值：`0`。 |
| `last_turn_output_tokens` | `int` | 默认值：`0`。 |
| `cache_read_input_tokens` | `int` | 默认值：`0`。 |
| `cache_creation_input_tokens` | `int` | 默认值：`0`。 |
| `previous_response_id` | `str \| None` | 默认值：`None`。 |
| `iteration` | `int` | 默认值：`0`。 |
| `provider_replay_handle` | `dict[str, Any] \| None` | 内部用于安全交接 repair/resume 的 opaque 进程内 replay capability。序列化后只含 `id` 和 `scope`，不会包含 provider reasoning/signature。默认值：`None`。 |
| `interaction_request` | `dict[str, Any] \| None` | 异步等待 human input 或 tool approval 时返回的不可变 durable request。只有 memory-backed run 配置了 `on_max_iterations` callback 时才会 journal max-budget request；该 callback 是同步 adapter，因此它的 request 通常在等待被中断后从 session checkpoint 恢复，而不是作为正常返回值返回。默认值：`None`。 |

### `src/unchain/runtime/completion.py`

显式启用的 completion policy runtime。Completion policy 不是
`KernelLoop` 内置的自循环；只有 agent 显式配置
`PoliciesModule(completion_policy=...)` 时才会运行。

## CompletionEvaluation

Completion validator 返回的 frozen dataclass。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/runtime/completion.py` |
| 继承/协议 | `-` |
| 导出状态 | 通过 `unchain.runtime` 导出，并从 `unchain.agent` 重新导出。 |
| 对象类型 | Dataclass (frozen)。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `complete` | `bool` | 结果是否满足 validator。 |
| `feedback` | `str` | 不完整时追加为新 user message 的修复提示。 |
| `reason` | `str` | 可选诊断原因，会随 evaluation 事件发出。 |

## CompletionPolicy

配置有界 completion repair 的 frozen dataclass。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/runtime/completion.py` |
| 继承/协议 | `-` |
| 导出状态 | 通过 `unchain.runtime` 导出，并从 `unchain.agent` 重新导出。 |
| 对象类型 | Dataclass (frozen)。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `validator` | `CompletionValidator` | 必填 callback；返回 `CompletionEvaluation`、`bool` 或 dict。 |
| `max_repair_turns` | `int` | 默认值：`1`。 |
| `repair_max_iterations` | `int \| None` | repair run 的可选 max-iteration 覆盖。 |
| `max_total_tokens` | `int \| None` | 可选总 token 预算。 |
| `max_elapsed_seconds` | `float \| None` | 可选 wall-time 预算。 |
| `stop_on_no_progress` | `bool` | 默认值：`True`。 |

## CompletionPolicyRunner

Runtime policy runner，会评估 completed result，并可通过传入的 `run_once`
callback 执行有界 repair turn。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/runtime/completion.py` |
| 继承/协议 | `-` |
| 导出状态 | 通过 `unchain.runtime` 导出。 |
| 对象类型 | Dataclass。 |

### 显式启用边界

- `policy=None` 会原样返回 `KernelRunResult`。
- 非 completed run 会原样返回。
- repair 次数受 policy 字段约束，并通过配置的 callback 发出
  `completion_policy_evaluated`、`completion_policy_retry` 和
  `completion_policy_exhausted` 事件。
- Agent 用户通过 `PoliciesModule(completion_policy=...)` 启用；kernel loop
  不硬编码 completion repair 行为。

### `src/unchain/providers/base.py`

Provider 抽象层。`ModelIO` 是所有 provider 实现必须满足的协议；`ModelTurnRequest` 是 frozen 输入。

## ModelTurnRequest

Frozen dataclass，打包单次模型 turn 的消息、payload、格式和 toolkit。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/providers/base.py` |
| 继承/协议 | `-` |
| 导出状态 | 通过 `unchain.providers` 导出。 |
| 对象类型 | Dataclass (frozen)。 |

### 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `messages` | `list[dict[str, Any]]` | 构造时必需。 |
| `payload` | `dict[str, Any]` | 默认值：`{}`。 |
| `response_format` | `ResponseFormat \| None` | 默认值：`None`。 |
| `callback` | `Callable[[dict[str, Any]], None] \| None` | 默认值：`None`。 |
| `verbose` | `bool` | 默认值：`False`。 |
| `run_id` | `str` | 默认值：`"kernel"`。 |
| `iteration` | `int` | 默认值：`0`。 |
| `toolkit` | `Toolkit` | 默认值：`Toolkit()`。 |
| `emit_stream` | `bool` | 默认值：`False`。 |
| `previous_response_id` | `str \| None` | 默认值：`None`。 |
| `openai_text_format` | `dict[str, Any] \| None` | 默认值：`None`。 |
| `fallback_messages` | `list[dict[str, Any]] \| None` | OpenAI 远端 continuation 无法恢复时使用的完整本地上下文。默认值：`None`。 |

### 公共方法

| 方法 | 返回 | 说明 |
| --- | --- | --- |
| `copied_messages()` | `list[dict[str, Any]]` | 请求消息的深拷贝。 |

## ModelIO（协议）

Kernel loop 使用的 provider 边界。所有 provider 实现（OpenAI、Anthropic、Ollama、Gemini）满足此协议。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/providers/base.py` |
| 对象类型 | 协议（runtime-checkable）。 |

### 必需接口

| 属性/方法 | 类型 | 说明 |
| --- | --- | --- |
| `provider` | `str` | Provider 名称标识符。 |
| `fetch_turn(request)` | `-> ModelTurnResult` | 执行一次模型 turn。 |

### Durable interaction 身份要求

上面的基础协议足以支持普通 model turn。Durable interaction resume 对
runtime identity 有更严格的要求：在 selector、模型或工具开始工作前，runtime
会比较 execution checkpoint、不可变 interaction request、continuation 和当前
`ModelIO` 中记录的 provider/model。

因此，用于 durable resume 的 custom `ModelIO` 必须直接暴露非空的
`provider` 与 `model`，或通过 `engine.provider` 与 `engine.model` 同时暴露这两个
值。如果任一值无法推断，或四方身份不一致，resume 会 fail closed。`AgentSpec`
中的值不会被当作当前 adapter 身份的证明。

### `src/unchain/kernel/loop.py`

Harness 驱动的执行循环，编排模型 turn、工具执行、memory 提交和暂停。

## KernelLoop

主执行引擎。运行 step-once 循环：分发 harness phase、获取模型 turn、执行工具、提交 memory，重复直到完成或暂停。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/kernel/loop.py` |
| 继承/协议 | `-` |
| 导出状态 | 通过 `unchain.kernel` 导出。 |
| 对象类型 | 类。 |

### 生命周期与运行时角色

- 构造时接受一个 `ModelIO` 实例。
- `register_harness(harness)` 挂载运行时 harness（工具执行、优化器等）。
- `attach_memory(memory_runtime)` 连接 `KernelMemoryRuntime`。
- `run()` 规范化消息、进入 step 循环、分发 harness phase、获取模型 turn，并返回 `KernelRunResult`。
- `resume_interaction()` 恢复 durable suspension 并消费已校验 receipt；`resume_human_input()` 保留为 human input 兼容入口。

### 最小调用示例

```python
from unchain.kernel.loop import KernelLoop
from unchain.providers import ModelIO

loop = KernelLoop(model_io=my_model_io)
loop.register_harness(my_harness)
result = loop.run(messages=[...], toolkit=my_toolkit)
```
