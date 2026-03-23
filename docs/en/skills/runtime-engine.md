# Runtime Engine

Canonical English skill chapter for the `runtime-engine` topic.

## Role and boundaries

This chapter explains the `Broth` runtime, canonical provider-turn types, callback events, workspace pin injection, and suspension/resume semantics.

## Dependency view

- `Broth` coordinates provider adapters, memory, toolkits, human-input flow, and structured output.
- `ToolCall`, `ProviderTurnResult`, `TokenUsage`, and `ToolExecutionOutcome` are the canonical runtime payload types.
- Toolkit catalog state is preserved by the runtime across suspensions.

## Core objects

- `Broth`
- `ToolCall`
- `ProviderTurnResult`
- `TokenUsage`
- `ToolExecutionOutcome`

## Execution and state flow

- Prepare canonical messages and inject pinned context.
- Fetch one provider turn and normalize tool requests.
- Execute tools, handle confirmation or human input, and optionally run observation turns.
- Commit memory at terminal states and return messages plus a bundle containing `status`, token counts, and optional human-input or continuation state.

## Configuration surface

- Provider/model/api key.
- Default payload and capability resource files.
- Context window override, response format, callbacks, continuation hooks.

## Extension points

- Add provider dispatch functions under `runtime/providers/`.
- Extend structured-output handling via `ResponseFormat`.
- Attach or remove toolkits dynamically on a runtime instance.

## Common gotchas

- Observation turns count toward iteration budget.
- Callbacks are synchronous.
- Provider SDK imports are lazy and fail at call time if missing.

## Related class references

- [Runtime API](../api/runtime.md)
- [Toolkits API](../api/toolkits.md)
- [Input/Workspace/Schema API](../api/input-workspace-schemas.md)

## Source entry points

- `src/miso/runtime/engine.py`
- `src/miso/runtime/payloads.py`
- `src/miso/runtime/providers/`

## Broth In Practice

`Broth` is the low-level execution runtime. It does not own agent identity, default instructions, or durable configuration. Its job is narrower and more operational: take a prepared request, run provider turns, execute tools, handle suspension and resumption, and return a normalized conversation plus bundle.

This boundary is intentional. `Agent` answers the question "what is this agent configured to be?", while `Broth` answers the question "how does this specific run execute?". That separation is why `Agent.run()` creates a fresh `Broth` each time instead of reusing a long-lived runtime instance.

## Current Execution Flow

1. `run()` canonicalizes incoming messages, validates modality support against model capabilities, and projects canonical messages into the provider-specific shape expected by OpenAI, Anthropic, Gemini, or Ollama.
2. If both `memory_manager` and `session_id` are present, the runtime performs a memory prepare pass before the loop begins, injecting summarized history, retrieved long-term context, and any context-window trimming result into the request.
3. `_run_loop()` resolves visible toolkits for the current iteration, issues one provider turn, and normalizes the provider response into a `ProviderTurnResult` so the loop can stay provider-agnostic.
4. If the model emitted tool calls, the runtime executes tools, applies confirmation gates, or returns early with `awaiting_human_input` when human input is required. Tools marked with `observe=True` trigger an additional observation turn that briefly reviews the latest tool result.
5. When a turn no longer produces tool calls, the runtime applies any structured-output parsing, builds the bundle, commits memory, and returns the final conversation.

## Design Notes

The current implementation treats memory as a boundary capability around `run()`, not as the center of the per-iteration state machine. This keeps the runtime usable without memory, avoids extra summary and extraction cost on every loop iteration, and prevents half-finished suspended states from being committed before a run is truly complete.

## Detailed legacy reference

The original repository skill note is preserved below for continuity and extra examples. The canonical copy now lives in this docs tree.

> The `Broth` execution loop, provider abstraction, observation injection, confirmation suspension, callback events, and structured output.

## Broth — The Core Runtime

`Broth` is the low-level engine that orchestrates LLM calls, tool execution, memory integration, and event emission. `Agent` creates a fresh `Broth` for every `run()` call.

```python
from miso.runtime import Broth
from miso.toolkits import WorkspaceToolkit

runtime = Broth(provider="openai", model="gpt-5")
runtime.add_toolkit(WorkspaceToolkit(workspace_root="."))
messages, bundle = runtime.run("Inspect the repo.")
```

### Key Constructor Parameters

| Parameter        | Type          | Default  | Purpose                                                        |
| ---------------- | ------------- | -------- | -------------------------------------------------------------- |
| `provider`       | `str`         | required | `"openai"`, `"anthropic"`, `"gemini"`, `"ollama"`              |
| `model`          | `str`         | required | Model identifier (e.g., `"gpt-5"`, `"claude-opus-4-20250918"`) |
| `api_key`        | `str \| None` | `None`   | Uses env var if not provided                                   |
| `base_url`       | `str \| None` | `None`   | Custom endpoint (for Ollama, proxies)                          |
| `max_iterations` | `int`         | `6`      | Max tool-calling loop iterations                               |
| `system_prompt`  | `str`         | `""`     | System message prepended to conversation                       |

### Return Value

```python
messages, bundle = runtime.run(...)

# messages: list[dict] — full conversation including tool calls/results
# bundle: dict — metadata
#   bundle["consumed_tokens"]       — total token usage
#   bundle["stop_reason"]           — why the loop ended
#   bundle["artifacts"]             — collected artifacts (if any)
#   bundle["toolkit_catalog_token"] — catalog state (if catalog enabled)
```

## Execution Loop (Step by Step)

```text
Broth.run(messages, toolkit, response_format, max_iterations, ...)
│
│  for iteration in 1..max_iterations:
│
│  ┌─ Step 1: PREPARE CONTEXT ──────────────────────────────┐
│  │ • memory.prepare_messages(session_id)                   │
│  │ • Injects workspace pin context as system messages       │
│  │ • Applies context window strategy (trim/summarize)       │
│  └─────────────────────────────────────────────────────────┘
│
│  ┌─ Step 2: LLM CALL ─────────────────────────────────────┐
│  │ • _fetch_once(messages, tools, response_format)          │
│  │ • Dispatches to provider SDK (OpenAI/Anthropic/etc.)     │
│  │ • Returns ProviderTurnResult:                            │
│  │   { assistant_message, tool_calls[], token_usage }       │
│  │ • Emits: token_delta, message_published events           │
│  └─────────────────────────────────────────────────────────┘
│
│  ┌─ Step 3: TOOL EXECUTION ───────────────────────────────┐
│  │ for each tool_call in result.tool_calls:                 │
│  │                                                          │
│  │   if requires_confirmation:                              │
│  │     → build ToolConfirmationRequest                      │
│  │     → emit event, SUSPEND for user response              │
│  │     → if rejected: skip tool, send error to LLM          │
│  │     → if approved: continue (maybe with modified args)   │
│  │                                                          │
│  │   result = toolkit.execute(tool_name, arguments)         │
│  │   → emit: tool_result event                              │
│  │                                                          │
│  │   if observe=True:                                       │
│  │     → _observation_turn(messages + tool_result)          │
│  │     → extra LLM call to "observe" the outcome            │
│  │     → observation appended to messages                   │
│  └─────────────────────────────────────────────────────────┘
│
│  ┌─ Step 4: MEMORY COMMIT ────────────────────────────────┐
│  │ • memory.commit_messages(session_id, full_conversation)  │
│  │ • Stores turns, compacts history, extracts long-term     │
│  └─────────────────────────────────────────────────────────┘
│
│  ┌─ Step 5: LOOP CHECK ──────────────────────────────────┐
│  │ • No tool_calls in this turn? → BREAK (done)            │
│  │ • Max iterations reached? → BREAK (with warning)         │
│  │ • Human input requested? → SUSPEND                       │
│  └─────────────────────────────────────────────────────────┘
│
▼
Returns (messages, bundle)
```

## Provider Abstraction

Broth speaks a **canonical message format** internally. Provider-specific SDKs are loaded lazily.

### Supported Providers

| Provider    | SDK                   | Notes                                  |
| ----------- | --------------------- | -------------------------------------- |
| `openai`    | `openai`              | Default, most tested                   |
| `anthropic` | `anthropic`           | Claude models                          |
| `gemini`    | `google-generativeai` | Gemini models                          |
| `ollama`    | `openai` (compatible) | Local models via OpenAI-compatible API |

### Model Capabilities

Model capabilities are declared in JSON resource files under `src/miso/runtime/resources/`. These declare what features each model supports:

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

Loaded at runtime via `load_model_capabilities()`.

### Default Payloads

Provider-specific defaults (temperature, top_p, etc.) are also in resource JSON files. Loaded via `load_default_payloads()`.

### Adding a New Provider

1. Create `src/miso/runtime/providers/my_provider.py`
2. Implement the provider dispatch function matching the existing pattern
3. Add model capabilities to resources JSON
4. Add default payloads to resources JSON
5. Register the provider name in `engine.py` dispatch logic

The provider module is **lazy-loaded** — it's only imported when `provider="my_provider"` is used.

## Callback Events

Broth emits events throughout execution via a callback function. This powers UI streaming, logging, and observability.

```python
def my_callback(event: dict) -> None:
    print(f"[{event['type']}] {event.get('data', '')}")

messages, bundle = runtime.run("task", callback=my_callback)
```

### Event Types

| Event Type                  | When                        | Payload                             |
| --------------------------- | --------------------------- | ----------------------------------- |
| `run_started`               | Run begins                  | `session_id`, `iteration`           |
| `token_delta`               | Streaming token received    | `delta`, `role`                     |
| `message_published`         | Assistant message complete  | Full message dict                   |
| `tool_call_started`         | Before tool execution       | `tool_name`, `call_id`, `arguments` |
| `tool_result`               | After tool execution        | `tool_name`, `call_id`, `result`    |
| `tool_confirmation_request` | Tool needs approval         | `ToolConfirmationRequest`           |
| `observation_started`       | Before observation LLM call | `tool_name`                         |
| `observation_complete`      | After observation LLM call  | Observation message                 |
| `memory_commit`             | After memory committed      | `session_id`                        |
| `run_completed`             | Run ends normally           | `stop_reason`, `iterations`         |
| `run_error`                 | Run ends with error         | `error`                             |
| `human_input_request`       | Human input needed          | Request details                     |
| `human_input_response`      | Human input received        | Response details                    |
| `iteration_started`         | New loop iteration begins   | `iteration` number                  |
| `context_window_usage`      | After context preparation   | Token counts                        |
| `summary_generated`         | After summarization         | Summary text                        |
| `long_term_extracted`       | After fact extraction       | Profile updates                     |

**Note**: Not all events are emitted in every run. Events depend on configuration (memory, tools, confirmation).

## Confirmation Suspension & Resumption

When a tool with `requires_confirmation=True` is called:

```text
Broth.run()
  ├── LLM requests tool call
  ├── Tool has requires_confirmation
  ├── ToolConfirmationRequest emitted via callback
  ├── run() PAUSES — returns partial state
  │
  │   ← External: UI shows confirmation dialog
  │   ← External: User approves/rejects
  │
  ├── Agent.resume_human_input(response)
  │   └── Resumes Broth with the response
  ├── If approved: tool executes
  ├── If rejected: error sent to LLM, loop continues
  └── run() continues or returns final result
```

The **toolkit catalog state** is preserved across this suspension via state tokens.

## Structured Output (Response Format)

Force the LLM to return JSON matching a schema:

```python
from miso.runtime import Broth
from miso.schemas import ResponseFormat

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

runtime = Broth(provider="openai", model="gpt-5")
messages, bundle = runtime.run("Analyze this code.", response_format=fmt)
# Last message content is guaranteed to be valid JSON matching the schema
```

**Note**: Not all models support structured output. Check `model_capabilities["supports_structured_output"]`.

## Workspace Pin Injection

Before each LLM call, Broth checks if there are **pinned files** in the session store. If so, it injects their contents as system messages:

````text
[system] Pinned file context: src/main.py (lines 10-50)
```python
def main():
    ...
````

```

Pins are managed by `WorkspaceToolkit` tools (`pin_file_context`, `unpin_file_context`). Constraints:

| Limit | Value |
|-------|-------|
| Max pins per session | 8 |
| Max total pinned chars | 16,000 |
| Max single full-file pin | 12,000 chars |

Pins are **resilient to edits** — they use text anchors and fingerprints to relocate content after file modifications.

## Common Gotchas

1. **Fresh Broth per run** — `Agent.run()` creates a new `Broth` each time. Don't try to reuse or reconfigure a `Broth` instance between runs.

2. **`max_iterations` includes observation turns** — If you have tools with `observe=True`, each observation consumes an iteration. Set `max_iterations` higher if using many observable tools.

3. **Provider SDK is lazy-loaded** — The first call to a provider triggers an import. Missing SDK (`pip install openai`) fails at call time, not at import time.

4. **Callback is synchronous** — Event callbacks block the execution loop. Keep callbacks fast or offload work to a queue.

5. **Structured output + tools** — Some providers don't support `response_format` and tool calling simultaneously. The engine handles this by splitting the final turn.

6. **Token counting is approximate** — Token usage reported in `bundle["consumed_tokens"]` depends on provider accuracy. Use it for budgeting, not billing.

## Related Skills

- [architecture-overview.md](architecture-overview.md) — System-level view
- [tool-system-patterns.md](tool-system-patterns.md) — Tool execution details
- [memory-system.md](memory-system.md) — How MemoryManager integrates with Broth
- [agent-and-team.md](agent-and-team.md) — How Agent creates and configures Broth
```
