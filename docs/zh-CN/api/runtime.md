# Runtime API 参考

核心执行类型：kernel loop、provider 抽象（`ModelIO`）、模型 turn 结果、工具调用、token 统计和运行结果。

| 指标 | 值 |
| --- | --- |
| 类数量 | 2 |
| Dataclass | 5 |
| 协议 | 1 |
| 仅内部类型 | 0 |

## 覆盖地图

| 类 | 源码 | 导出 | 类型 |
| --- | --- | --- | --- |
| `ToolCall` | `src/unchain/kernel/types.py` | subpackage | dataclass (frozen) |
| `TokenUsage` | `src/unchain/kernel/types.py` | subpackage | dataclass (frozen) |
| `ModelTurnResult` | `src/unchain/kernel/types.py` | subpackage | dataclass (frozen) |
| `KernelRunResult` | `src/unchain/kernel/types.py` | subpackage | dataclass (frozen) |
| `ModelTurnRequest` | `src/unchain/providers/model_io.py` | subpackage | dataclass (frozen) |
| `ModelIO` | `src/unchain/providers/model_io.py` | subpackage | protocol |
| `KernelLoop` | `src/unchain/kernel/loop.py` | subpackage | class |

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

Frozen dataclass，由 `Agent.run()` 和 `PreparedAgent.run()` 返回，包含最终对话、状态及可选的 continuation/human-input 状态。

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

### `src/unchain/providers/model_io.py`

Provider 抽象层。`ModelIO` 是所有 provider 实现必须满足的协议；`ModelTurnRequest` 是 frozen 输入。

## ModelTurnRequest

Frozen dataclass，打包单次模型 turn 的消息、payload、格式和 toolkit。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/providers/model_io.py` |
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

### 公共方法

| 方法 | 返回 | 说明 |
| --- | --- | --- |
| `copied_messages()` | `list[dict[str, Any]]` | 请求消息的深拷贝。 |

## ModelIO（协议）

Kernel loop 使用的 provider 边界。所有 provider 实现（OpenAI、Anthropic、Ollama、Gemini）满足此协议。

| 项目 | 细节 |
| --- | --- |
| 源码 | `src/unchain/providers/model_io.py` |
| 对象类型 | 协议（runtime-checkable）。 |

### 必需接口

| 属性/方法 | 类型 | 说明 |
| --- | --- | --- |
| `provider` | `str` | Provider 名称标识符。 |
| `fetch_turn(request)` | `-> ModelTurnResult` | 执行一次模型 turn。 |

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
- `resume_human_input()` 恢复暂停的对话并继续循环。

### 最小调用示例

```python
from unchain.kernel.loop import KernelLoop
from unchain.providers.model_io import ModelIO

loop = KernelLoop(model_io=my_model_io)
loop.register_harness(my_harness)
result = loop.run(messages=[...], toolkit=my_toolkit)
```
