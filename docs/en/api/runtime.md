# Runtime API Reference

Core execution types: kernel loop, provider abstraction (`ModelIO`), model turn results, tool calls, token accounting, and run results.

| Metric | Value |
| --- | --- |
| Classes | 2 |
| Dataclasses | 5 |
| Protocols | 1 |
| Internal-only types | 0 |

## Coverage map

| Class | Source | Exposure | Kind |
| --- | --- | --- | --- |
| `ToolCall` | `src/unchain/kernel/types.py` | subpackage | dataclass (frozen) |
| `TokenUsage` | `src/unchain/kernel/types.py` | subpackage | dataclass (frozen) |
| `ModelTurnResult` | `src/unchain/kernel/types.py` | subpackage | dataclass (frozen) |
| `KernelRunResult` | `src/unchain/kernel/types.py` | subpackage | dataclass (frozen) |
| `ModelTurnRequest` | `src/unchain/providers/model_io.py` | subpackage | dataclass (frozen) |
| `ModelIO` | `src/unchain/providers/model_io.py` | subpackage | protocol |
| `KernelLoop` | `src/unchain/kernel/loop.py` | subpackage | class |

### `src/unchain/kernel/types.py`

Immutable value types shared across the kernel, providers, and agent layers.

## ToolCall

Frozen dataclass representing a single tool invocation requested by the model.

| Item | Details |
| --- | --- |
| Source | `src/unchain/kernel/types.py` |
| Inheritance | `-` |
| Exposure | Exported from `unchain.kernel`. |
| Kind | Dataclass (frozen). |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `call_id` | `str` | Required at construction time. |
| `name` | `str` | Required at construction time. |
| `arguments` | `dict[str, Any] \| str \| None` | Required at construction time. |

### Minimal usage example

```python
ToolCall(call_id="call_abc", name="search_text", arguments={"pattern": "foo"})
```

## TokenUsage

Frozen dataclass for token accounting within a single model turn.

| Item | Details |
| --- | --- |
| Source | `src/unchain/kernel/types.py` |
| Inheritance | `-` |
| Exposure | Exported from `unchain.kernel`. |
| Kind | Dataclass (frozen). |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `consumed_tokens` | `int` | Default: `0`. |
| `input_tokens` | `int` | Default: `0`. |
| `output_tokens` | `int` | Default: `0`. |

## ModelTurnResult

Frozen dataclass returned by `ModelIO.fetch_turn()` with the model's assistant messages, tool calls, and token counts.

| Item | Details |
| --- | --- |
| Source | `src/unchain/kernel/types.py` |
| Inheritance | `-` |
| Exposure | Exported from `unchain.kernel`. |
| Kind | Dataclass (frozen). |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `assistant_messages` | `list[dict[str, Any]]` | Required at construction time. |
| `tool_calls` | `list[ToolCall]` | Required at construction time. |
| `final_text` | `str` | Default: `""`. |
| `response_id` | `str \| None` | Default: `None`. |
| `reasoning_items` | `list[dict[str, Any]] \| None` | Default: `None`. |
| `consumed_tokens` | `int` | Default: `0`. |
| `input_tokens` | `int` | Default: `0`. |
| `output_tokens` | `int` | Default: `0`. |
| `cache_read_input_tokens` | `int` | Default: `0`. |
| `cache_creation_input_tokens` | `int` | Default: `0`. |

## KernelRunResult

Frozen dataclass returned by `Agent.run()` and `PreparedAgent.run()` with the final conversation, status, and optional continuation/human-input state.

| Item | Details |
| --- | --- |
| Source | `src/unchain/kernel/types.py` |
| Inheritance | `-` |
| Exposure | Exported from `unchain.kernel`. |
| Kind | Dataclass (frozen). |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `messages` | `list[dict[str, Any]]` | Final conversation messages. |
| `status` | `str` | Run outcome status. |
| `continuation` | `dict[str, Any] \| None` | Default: `None`. |
| `human_input_request` | `dict[str, Any] \| None` | Default: `None`. |
| `consumed_tokens` | `int` | Default: `0`. |
| `input_tokens` | `int` | Default: `0`. |
| `output_tokens` | `int` | Default: `0`. |
| `last_turn_tokens` | `int` | Default: `0`. |
| `last_turn_input_tokens` | `int` | Default: `0`. |
| `last_turn_output_tokens` | `int` | Default: `0`. |
| `cache_read_input_tokens` | `int` | Default: `0`. |
| `cache_creation_input_tokens` | `int` | Default: `0`. |
| `previous_response_id` | `str \| None` | Default: `None`. |
| `iteration` | `int` | Default: `0`. |

### `src/unchain/providers/model_io.py`

Provider abstraction layer. `ModelIO` is the protocol that all provider implementations satisfy; `ModelTurnRequest` is the frozen input.

## ModelTurnRequest

Frozen dataclass packaging messages, payload, format, and toolkit for a single model turn.

| Item | Details |
| --- | --- |
| Source | `src/unchain/providers/model_io.py` |
| Inheritance | `-` |
| Exposure | Exported from `unchain.providers`. |
| Kind | Dataclass (frozen). |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `messages` | `list[dict[str, Any]]` | Required at construction time. |
| `payload` | `dict[str, Any]` | Default: `{}`. |
| `response_format` | `ResponseFormat \| None` | Default: `None`. |
| `callback` | `Callable[[dict[str, Any]], None] \| None` | Default: `None`. |
| `verbose` | `bool` | Default: `False`. |
| `run_id` | `str` | Default: `"kernel"`. |
| `iteration` | `int` | Default: `0`. |
| `toolkit` | `Toolkit` | Default: `Toolkit()`. |
| `emit_stream` | `bool` | Default: `False`. |
| `previous_response_id` | `str \| None` | Default: `None`. |
| `openai_text_format` | `dict[str, Any] \| None` | Default: `None`. |

### Public methods

| Method | Returns | Description |
| --- | --- | --- |
| `copied_messages()` | `list[dict[str, Any]]` | Deep-copy of the request messages. |

## ModelIO (Protocol)

Provider-facing boundary used by the kernel loop. All provider implementations (OpenAI, Anthropic, Ollama, Gemini) satisfy this protocol.

| Item | Details |
| --- | --- |
| Source | `src/unchain/providers/model_io.py` |
| Kind | Protocol (runtime-checkable). |

### Required interface

| Attribute/Method | Type | Description |
| --- | --- | --- |
| `provider` | `str` | Provider name identifier. |
| `fetch_turn(request)` | `-> ModelTurnResult` | Execute one model turn. |

### `src/unchain/kernel/loop.py`

Harness-driven execution loop that orchestrates model turns, tool execution, memory commits, and suspension.

## KernelLoop

The main execution engine. Runs a step-once loop: dispatch harness phases, fetch model turn, execute tools, commit memory, repeat until completion or suspension.

| Item | Details |
| --- | --- |
| Source | `src/unchain/kernel/loop.py` |
| Inheritance | `-` |
| Exposure | Exported from `unchain.kernel`. |
| Kind | Class. |

### Lifecycle and runtime role

- Construction takes a `ModelIO` instance.
- `register_harness(harness)` attaches runtime harnesses (tool execution, optimizers, etc.).
- `attach_memory(memory_runtime)` connects a `KernelMemoryRuntime`.
- `run()` normalizes messages, enters the step loop, dispatches harness phases, fetches model turns, and returns a `KernelRunResult`.
- `resume_human_input()` restores a suspended conversation and continues the loop.

### Minimal usage example

```python
from unchain.kernel.loop import KernelLoop
from unchain.providers.model_io import ModelIO

loop = KernelLoop(model_io=my_model_io)
loop.register_harness(my_harness)
result = loop.run(messages=[...], toolkit=my_toolkit)
```
