# Runtime Engine

Canonical English skill chapter for the `runtime-engine` topic.

## Role and boundaries

This chapter explains the run loop (`KernelLoop` internally), the `RuntimeHook` extension protocol, the `ModelAdapter` provider boundary, the canonical run-result types, and suspension/resume semantics.

## Dependency view

- `KernelLoop` coordinates hook phases, model turns, tool execution, and run-result assembly.
- `ModelAdapter` is the protocol that providers (OpenAI, Anthropic, Ollama, Gemini) satisfy. `ModelIO` remains a compatibility alias. The kernel never imports a vendor SDK directly.
- `RuntimeHook` is the per-phase extension surface. `RuntimeHarness` remains a compatibility alias. Memory, optimizers, retry, subagents, tool execution, and tool prompting are all implemented as hooks.
- `RunState` is the mutable per-run scratch space; `KernelRunResult` is the immutable return.
- `RunDelta` is the structured effect envelope used by hooks and active tools to modify model context, conversation state, runtime state, artifacts, events, or suspension.

## Core objects

- `KernelLoop`
- `RuntimeHook` / `RuntimePhase` / `HarnessContext`
- `ModelAdapter` / `ModelTurnRequest`
- `RunDelta`
- `ToolCall` / `ModelTurnResult` / `TokenUsage` / `KernelRunResult`

## Execution and state flow

- Construct a `KernelLoop(model_io=...)`.
- Register one or more harnesses with `register_harness(...)`.
- Optionally `attach_memory(KernelMemoryRuntime)` to wire memory commits.
- Call `run(messages, ...)`; the loop iterates `step_once()` until completion or suspension.
- On suspension, the loop returns a `KernelRunResult` with a `continuation` and, for a durable wait, an `interaction_request`. Persist the answer with `Agent.submit_interaction()` and continue with `Agent.resume_interaction()`; `resume_human_input()` remains the legacy human-input adapter.

## Configuration surface

- Provider/model selection happens at `ModelAdapter` construction.
- Per-run options come through the kernel's `run()` arguments (max iterations, response format, callbacks, payload defaults).
- Hook composition is done at `AgentBuilder` time when running through `Agent`; standalone kernel users register hooks by hand.

## Common gotchas

- Observation turns count toward the iteration budget.
- Interaction callbacks are synchronous adapters. In a durable session the runtime releases its lease while the callback waits, records the callback answer as a receipt, then reacquires before continuing; offload unrelated long work.
- Provider SDK imports are lazy; missing SDK fails when `fetch_turn()` runs, not at import.
- Provider calls go through `ModelAdapter` implementations; the old provider runtime compatibility layer has been removed.

## Related class references

- [Runtime API](../api/runtime.md)
- [Toolkits API](../api/toolkits.md)
- [Tool System API](../api/tools.md)

## Source entry points

- `src/unchain/kernel/loop.py`
- `src/unchain/kernel/harness.py`
- `src/unchain/kernel/state.py`
- `src/unchain/kernel/types.py`
- `src/unchain/providers/base.py`
- `src/unchain/providers/openai.py`
- `src/unchain/providers/anthropic.py`
- `src/unchain/providers/ollama.py`

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
2. The loop dispatches hooks across the public extension phases (see `architecture-overview.md` for the full list) before and after each model turn, then crosses reserved durability barriers on suspension and finalization.
3. `ModelAdapter.fetch_turn(request)` returns a `ModelTurnResult` containing assistant messages, tool calls, and token counts.
4. If the model emitted tool calls, `ToolExecutionHook` runs them. Confirmation-gated tools create a durable interaction and return early with `status="awaiting_interaction"` when no synchronous adapter answers it.
5. Tools marked with `observe=True` trigger an additional observation turn during `after_tool_batch`.
6. When a turn no longer produces tool calls, the loop applies any structured-output parsing, commits memory, and returns a `KernelRunResult`.

## Design Notes

- Memory is integrated as hook components (bootstrap/before-model recall + before-commit write). Runs without memory simply omit the `MemoryModule`.
- Retry is a wrapper around `ModelAdapter.fetch_turn()` (see `unchain.retry`). Debug-only request tracing events do not commit an attempt, so a transient failure may still retry; the first user-visible token/tool/reasoning event closes that gate to prevent duplicate output.
- Provider-specific projection and native replay rehydration live in the provider layer (the context assembler plus model adapters), so the kernel stays vendor-agnostic.
- The current semantic model context is authoritative for every request. Provider replay contributes only retained native reasoning/tool envelopes; it cannot overwrite new memory, prompt, optimizer, or user deltas. OpenAI remote continuation keeps a separate delta input and a complete local fallback.
- A request-only model context that is still pending at a durable suspend boundary is stored in the execution checkpoint and restored before the next `before_model` phase.
- Human-input continuations expose only an opaque, process-local replay handle; provider reasoning/signatures are not serialized into the public continuation. Use session memory/checkpoints when a continuation must survive a process restart.
- For human-input and tool-approval waits, plus max-budget waits created by a configured memory-backed callback, the immutable interaction request and its checkpoint reference are created atomically. A response receipt is persisted before resume, and a missing or conflicting receipt fails before model/tool work.

## Provider Abstraction

Providers implement `ModelAdapter` (`ModelIO` compatibility alias):

```python
class ModelAdapter(Protocol):
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
2. Implement a `ModelAdapter` subclass with `fetch_turn()`.
3. Add capabilities and default payloads under `src/unchain/runtime/resources/`.
4. Either pass an instance directly to `Agent(model_io_factory=...)` or register a factory in `ModelIOFactoryRegistry`.

The provider module is **lazy-loaded** — its SDK is only imported when the model adapter is actually constructed.

## Callback Events

Hooks and the loop emit events through the `callback` passed into `Agent.run()` / `KernelLoop.run()`. This powers UI streaming, logging, and observability.

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

For a memory-backed session, when a tool with `requires_confirmation=True` is called:

```text
KernelLoop.run()
  ├── LLM requests tool call
  ├── on_tool_call phase: ToolExecutionHarness builds ToolConfirmationRequest
  ├── on_suspend phase atomically stores checkpoint + InteractionRequest
  ├── loop returns KernelRunResult(status="awaiting_interaction", interaction_request=...)
  │
  │   ← External: UI shows confirmation dialog
  │   ← External: User approves/rejects
  │
  ├── Agent.submit_interaction(...) durably records the response receipt
  ├── Agent.resume_interaction(session_id=...)
  │   └── Verifies and consumes the receipt on the on_resume path
  ├── If approved: tool executes (with modified args if any)
  ├── If rejected: error sent to LLM, loop continues
  └── run() continues or returns final result
```

When `ToolOptimizer` selected the tool surface, the durable interaction continuation stores a strict versioned exposure plan: catalog digest plus the exact direct, deferred, and loaded tool names. `Agent.resume_interaction()` validates that catalog and rebuilds the same surface without another selector call. A malformed present plan or catalog drift fails before model/tool work. This exact replay applies to durable human-input, tool-approval, and configured max-budget interaction resumes; a later `Agent.run()` over an ordinary `max_iterations` checkpoint is a new invocation and may optimize its tool surface again. `on_tool_confirm` remains available, but it performs the same sequence as a synchronous adapter: persist request and checkpoint, release the lease, call the callback, persist the receipt, reacquire, and resume. It does not bypass the journal.

The recommended durable API keeps receipt submission separate from execution:

```python
suspended = agent.run("Do something risky", session_id="job-42")

agent.submit_interaction(
    session_id="job-42",
    interaction_id=suspended.interaction_request["interaction_id"],
    response={"approved": True},
)

resumed = agent.resume_interaction(session_id="job-42")
```

`resume_interaction(response=...)` is a one-call convenience. Existing human-input integrations may continue to call `resume_human_input()`; with durable memory it uses the same receipt journal rather than a separate reliability path.

Without a durable memory-backed `session_id`, existing process-local confirmation and callback behavior remains available, but it cannot claim cross-process receipt recovery.

This receipt boundary makes the decision durable, not an arbitrary external side effect. If an approved remote tool changes the outside world and the worker dies before recording its tool result, recovery still needs the D3 tool-intent/tool-receipt protocol, an idempotency key, reconciliation, or a transactional outbox.

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

7. **Provider boundaries are explicit** — New work should target `ModelIO` implementations directly; `runtime/` now holds resource loading, not a provider runtime.

## Related Skills

- [architecture-overview.md](architecture-overview.md) — System-level view
- [tool-system-patterns.md](tool-system-patterns.md) — Tool execution details
- [memory-system.md](memory-system.md) — How memory harnesses plug into the loop
- [agent-and-team.md](agent-and-team.md) — How Agent builds the kernel
