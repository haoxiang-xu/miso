# Runtime Engine

Canonical English skill chapter for the `runtime-engine` topic.

## Role and boundaries

This chapter explains the `KernelLoop` engine, the `RuntimeHarness` extension protocol, the `ModelIO` provider boundary, the canonical run-result types, and suspension/resume semantics.

## Dependency view

- `KernelLoop` coordinates harness phases, model turns, tool execution, and run-result assembly.
- `ModelIO` is the protocol that providers (OpenAI, Anthropic, Ollama, Gemini) satisfy. The kernel never imports a vendor SDK directly.
- `RuntimeHarness` is the per-phase extension surface. Memory, optimizers, retry, subagents, tool execution, and tool prompting are all implemented as harnesses.
- `RunState` is the mutable per-run scratch space; `KernelRunResult` is the immutable return.

## Core objects

- `KernelLoop`
- `RuntimeHarness` / `RuntimePhase` / `HarnessContext`
- `ModelIO` / `ModelTurnRequest`
- `ToolCall` / `ModelTurnResult` / `TokenUsage` / `KernelRunResult`
- `LegacyBrothModelIO` (compat)

## Execution and state flow

- Construct a `KernelLoop(model_io=...)`.
- Register one or more harnesses with `register_harness(...)`.
- Optionally `attach_memory(KernelMemoryRuntime)` to wire memory commits.
- Call `run(messages, ...)`; the loop iterates `step_once()` until completion or suspension.
- On suspension, the loop returns a `KernelRunResult` with `status="awaiting_human_input"` and a `continuation` payload; pass both back into `resume_human_input()` to continue.

## Configuration surface

- Provider/model selection happens at `ModelIO` construction.
- Per-run options come through the kernel's `run()` arguments (max iterations, response format, callbacks, payload defaults).
- Harness composition is done at `AgentBuilder` time when running through `Agent`; standalone kernel users register harnesses by hand.

## Common gotchas

- Observation turns count toward the iteration budget.
- Callbacks run synchronously inside the loop; offload long work.
- Provider SDK imports are lazy; missing SDK fails when `fetch_turn()` runs, not at import.
- `Broth` is **not** a runtime anymore — it survives only as `LegacyBrothModelIO`, an adapter so old code paths can plug into the new kernel.

## Related class references

- [Runtime API](../api/runtime.md)
- [Toolkits API](../api/toolkits.md)
- [Tool System API](../api/tools.md)

## Source entry points

- `src/unchain/kernel/loop.py`
- `src/unchain/kernel/harness.py`
- `src/unchain/kernel/state.py`
- `src/unchain/kernel/types.py`
- `src/unchain/providers/model_io.py`

## KernelLoop In Practice

`KernelLoop` is the low-level execution runtime. It does not own agent identity, default instructions, or modules. Its job is operational: take a normalized request, run model turns, execute tools, dispatch harness phases, handle suspension and resumption, and return a `KernelRunResult`.

This boundary is intentional. `Agent` answers "what is this agent configured to be?", while `KernelLoop` answers "how does this specific run execute?" — which is why `Agent.run()` builds a fresh `KernelLoop` each time instead of reusing one.

```python
from unchain.kernel import KernelLoop
from unchain.providers import OpenAIModelIO

loop = KernelLoop(model_io=OpenAIModelIO(model="gpt-5"))
loop.register_harness(my_harness)
result = loop.run(messages=[{"role": "user", "content": "Hello"}])
```

For day-to-day use, prefer `Agent.run()`. Direct `KernelLoop` use is only needed for embedded scenarios that don't want the agent layer.

## Current Execution Flow

1. `run()` normalizes incoming messages, validates modality support against model capabilities, and builds a `RunState` for this iteration.
2. The loop dispatches harnesses across the eight phases (see `architecture-overview.md` for the full list) before and after each model turn.
3. `ModelIO.fetch_turn(request)` returns a `ModelTurnResult` containing assistant messages, tool calls, and token counts.
4. If the model emitted tool calls, `ToolExecutionHarness` runs them. Confirmation-gated tools cause the loop to return early with `status="awaiting_human_input"`.
5. Tools marked with `observe=True` trigger an additional observation turn during `after_tool_batch`.
6. When a turn no longer produces tool calls, the loop applies any structured-output parsing, commits memory, and returns a `KernelRunResult`.

## Design Notes

- Memory is integrated as a harness pair (bootstrap/before-model recall + before-commit write). Runs without memory simply omit the `MemoryModule`.
- Retry is a wrapper around `ModelIO.fetch_turn()` (see `unchain.retry`) and never retries content that has already been streamed downstream.
- Provider-specific projection (canonical messages → SDK shape) lives entirely inside each `ModelIO` implementation, so the kernel stays vendor-agnostic.

## Provider Abstraction

Providers implement `ModelIO`:

```python
class ModelIO(Protocol):
    provider: str
    def fetch_turn(self, request: ModelTurnRequest) -> ModelTurnResult: ...
```

### Built-in implementations

| Provider    | Class                | SDK                   | Notes                                  |
| ----------- | -------------------- | --------------------- | -------------------------------------- |
| `openai`    | `OpenAIModelIO`      | `openai`              | Default, most tested                   |
| `anthropic` | `AnthropicModelIO`   | `anthropic`           | Claude models                          |
| `ollama`    | `OllamaModelIO`      | `openai`-compatible   | Local models                           |
| `gemini`    | (via providers/)     | `google-generativeai` | Lazy-loaded                            |

### Model Capabilities

Model capabilities are declared in JSON resource files under `src/unchain/runtime/resources/`. They declare what features each model supports:

```json
{
  "gpt-5": {
    "supports_tools": true,
    "supports_vision": true,
    "supports_structured_output": true,
    "context_window": 128000,
    "max_output_tokens": 16384
  }
}
```

### Adding a New Provider

1. Create `src/unchain/providers/my_provider.py`.
2. Implement a `ModelIO` subclass with `fetch_turn()`.
3. Add capabilities and default payloads under `src/unchain/runtime/resources/`.
4. Either pass an instance directly to `Agent(model_io_factory=...)` or register a factory in `ModelIOFactoryRegistry`.

The provider module is **lazy-loaded** — its SDK is only imported when the model IO is actually constructed.

## Callback Events

Harnesses and the loop emit events through the `callback` passed into `Agent.run()` / `KernelLoop.run()`. This powers UI streaming, logging, and observability.

```python
def my_callback(event: dict) -> None:
    print(f"[{event['type']}] {event.get('data', '')}")

result = agent.run("task", callback=my_callback)
```

### Common Event Types

| Event Type                  | When                        | Payload                             |
| --------------------------- | --------------------------- | ----------------------------------- |
| `run_started`               | Run begins                  | `session_id`, `iteration`           |
| `token_delta`               | Streaming token received    | `delta`, `role`                     |
| `message_published`         | Assistant message complete  | Full message dict                   |
| `tool_call_started`         | Before tool execution       | `tool_name`, `call_id`, `arguments` |
| `tool_result`               | After tool execution        | `tool_name`, `call_id`, `result`    |
| `tool_confirmation_request` | Tool needs approval         | `ToolConfirmationRequest`           |
| `observation_started`       | Before observation turn     | `tool_name`                         |
| `observation_complete`      | After observation turn      | Observation message                 |
| `memory_commit`             | After memory committed      | `session_id`                        |
| `run_completed`             | Run ends normally           | `stop_reason`, `iterations`         |
| `run_error`                 | Run ends with error         | `error`                             |
| `human_input_request`       | Human input needed          | Request details                     |
| `human_input_response`      | Human input received        | Response details                    |
| `iteration_started`         | New loop iteration begins   | `iteration` number                  |
| `context_window_usage`      | After context preparation   | Token counts                        |
| `summary_generated`         | After summarization         | Summary text                        |
| `long_term_extracted`       | After fact extraction       | Profile updates                     |

**Note**: Not every event fires in every run. Events depend on which modules and harnesses are configured.

## Confirmation Suspension & Resumption

When a tool with `requires_confirmation=True` is called:

```text
KernelLoop.run()
  ├── LLM requests tool call
  ├── on_tool_call phase: ToolExecutionHarness builds ToolConfirmationRequest
  ├── on_suspend phase fires; loop returns KernelRunResult(status="awaiting_human_input", continuation=...)
  │
  │   ← External: UI shows confirmation dialog
  │   ← External: User approves/rejects
  │
  ├── Agent.resume_human_input(continuation=..., response=...)
  │   └── Re-enters the loop on on_resume phase with the response in hand
  ├── If approved: tool executes (with modified args if any)
  ├── If rejected: error sent to LLM, loop continues
  └── run() continues or returns final result
```

The toolkit catalog and discovery state survive this round trip via the harness `on_suspend` / `on_resume` checkpointing.

## Structured Output (Response Format)

Force the LLM to return JSON matching a schema:

```python
from unchain import Agent
from unchain.schemas import ResponseFormat

fmt = ResponseFormat(
    name="analysis",
    schema={
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "score": {"type": "integer"},
        },
        "required": ["summary", "score"],
        "additionalProperties": False,
    },
)

result = agent.run("Analyze this code.", response_format=fmt)
# result.messages[-1] content is guaranteed to be valid JSON matching the schema
```

**Note**: Not all models support structured output. Check `model_capabilities["supports_structured_output"]`.

## Common Gotchas

1. **Fresh kernel per run** — `Agent.run()` builds a new `KernelLoop` each call. Don't try to reuse a single loop across runs unless you're embedding the kernel without `Agent`.

2. **`max_iterations` includes observation turns** — Tools with `observe=True` consume an iteration each time they fire. Bump `max_iterations` if you depend on many observable tools.

3. **Provider SDK is lazy-loaded** — The first call to a provider triggers an import. Missing SDK (`pip install openai`) fails at `fetch_turn()`, not at import time.

4. **Callback is synchronous** — Event callbacks block the loop. Keep them fast or queue work elsewhere.

5. **Structured output + tools** — Some providers don't support `response_format` and tool calling simultaneously. The relevant `ModelIO` implementation handles this by splitting the final turn.

6. **Token counting is approximate** — Token usage in `KernelRunResult` depends on provider accuracy. Use it for budgeting, not billing.

7. **`Broth` is gone from the runtime path** — If you grep for it, you'll find `LegacyBrothModelIO` in `kernel/model_io.py` and an old `runtime/` package. Both exist for migration only; new work should target `ModelIO` directly.

## Related Skills

- [architecture-overview.md](architecture-overview.md) — System-level view
- [tool-system-patterns.md](tool-system-patterns.md) — Tool execution details
- [memory-system.md](memory-system.md) — How memory harnesses plug into the loop
- [agent-and-team.md](agent-and-team.md) — How Agent builds the kernel
