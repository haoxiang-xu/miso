# Runtime API Reference

Core execution types: kernel loop, provider abstraction (`ModelIO`), model turn results, tool calls, token accounting, and run results.

| Metric | Value |
| --- | --- |
| Classes | 5 |
| Dataclasses | 10 |
| Protocols | 2 |
| Internal-only types | 0 |

## Coverage map

| Class | Source | Exposure | Kind |
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

### Execution ownership

`ExecutionRuntime` acquires an `ExecutionGuard` from an `ExecutionLeaseStore`. The guard owns a time-bounded `ExecutionLease`; its `ExecutionFence` is passed to atomic session writes. `ExecutionLeaseConfig` controls the TTL and heartbeat interval. Memory-backed built-in stores are wired automatically by `build_runtime_loop`; callers may also inject an explicit runtime when constructing a loop. Lease conflicts and stale fencing tokens fail closed through the exported `ExecutionLeaseError` hierarchy.

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
| `provider_replay_handle` | `dict[str, Any] \| None` | Opaque process-local replay capability used internally for safe repair/resume handoff. Its serialized form contains only `id` and `scope`, never provider reasoning/signatures. Default: `None`. |

### `src/unchain/runtime/completion.py`

An opt-in completion policy runtime. Completion policy is not part of the
`KernelLoop` self-loop; it runs only when an agent is explicitly configured with
`PoliciesModule(completion_policy=...)`.

## CompletionEvaluation

Frozen dataclass returned by a completion validator.

| Item | Details |
| --- | --- |
| Source | `src/unchain/runtime/completion.py` |
| Inheritance | `-` |
| Exposure | Exported from `unchain.runtime` and re-exported from `unchain.agent`. |
| Kind | Dataclass (frozen). |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `complete` | `bool` | Whether the result satisfies the validator. |
| `feedback` | `str` | Repair prompt appended as a new user message when incomplete. |
| `reason` | `str` | Optional diagnostic reason emitted with evaluation events. |

## CompletionPolicy

Frozen dataclass configuring bounded completion repair.

| Item | Details |
| --- | --- |
| Source | `src/unchain/runtime/completion.py` |
| Inheritance | `-` |
| Exposure | Exported from `unchain.runtime` and re-exported from `unchain.agent`. |
| Kind | Dataclass (frozen). |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `validator` | `CompletionValidator` | Required callback; returns `CompletionEvaluation`, `bool`, or a dict. |
| `max_repair_turns` | `int` | Default: `1`. |
| `repair_max_iterations` | `int \| None` | Optional max-iteration override for repair runs. |
| `max_total_tokens` | `int \| None` | Optional aggregate token budget. |
| `max_elapsed_seconds` | `float \| None` | Optional wall-time budget. |
| `stop_on_no_progress` | `bool` | Default: `True`. |

## CompletionPolicyRunner

Runtime policy runner that evaluates a completed result and may perform bounded
repair turns through the provided `run_once` callback.

| Item | Details |
| --- | --- |
| Source | `src/unchain/runtime/completion.py` |
| Inheritance | `-` |
| Exposure | Exported from `unchain.runtime`. |
| Kind | Dataclass. |

### opt-in boundary

- `policy=None` returns the original `KernelRunResult` unchanged.
- Non-completed runs are returned unchanged.
- Repair attempts are capped by policy fields and emit
  `completion_policy_evaluated`, `completion_policy_retry`, and
  `completion_policy_exhausted` events through the configured callback.
- Agent users enable this through `PoliciesModule(completion_policy=...)`; the
  kernel loop never hard-codes completion repair behavior.

### `src/unchain/providers/base.py`

Provider abstraction layer. `ModelIO` is the protocol that all provider implementations satisfy; `ModelTurnRequest` is the frozen input.

## ModelTurnRequest

Frozen dataclass packaging messages, payload, format, and toolkit for a single model turn.

| Item | Details |
| --- | --- |
| Source | `src/unchain/providers/base.py` |
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
| `fallback_messages` | `list[dict[str, Any]] \| None` | Complete local context used only when an OpenAI remote continuation cannot be resumed. Default: `None`. |

### Public methods

| Method | Returns | Description |
| --- | --- | --- |
| `copied_messages()` | `list[dict[str, Any]]` | Deep-copy of the request messages. |

## ModelIO (Protocol)

Provider-facing boundary used by the kernel loop. All provider implementations (OpenAI, Anthropic, Ollama, Gemini) satisfy this protocol.

| Item | Details |
| --- | --- |
| Source | `src/unchain/providers/base.py` |
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
from unchain.providers import ModelIO

loop = KernelLoop(model_io=my_model_io)
loop.register_harness(my_harness)
result = loop.run(messages=[...], toolkit=my_toolkit)
```
