# Debug a Streaming Issue

This guide helps you diagnose why a chat stream is stuck, erroring, or not completing in the unchain runtime stack. It covers the full request path from the frontend through the kernel to the LLM provider.

## Prerequisites

- Access to application logs
- Understanding of the request flow (see [Architecture Overview](../skills/architecture-overview.md))

## Reference Files

| File | Role |
|------|------|
| `src/unchain/providers/model_io.py` | Provider fetch implementations (OpenAI, Anthropic, Ollama) |
| `src/unchain/kernel/loop.py` | Kernel loop step dispatch |
| `src/unchain/tools/execution.py` | Tool execution harness |
| `src/unchain/tools/confirmation.py` | Tool confirmation gate (blocking wait) |
| `src/unchain/runtime/resources/model_capabilities.json` | Model context window limits |

## Steps

### 1. Identify the layer

Match the symptom to the likely layer:

| Symptom | Likely Layer | Where to Look |
|---------|-------------|---------------|
| No stream starts at all | Frontend / IPC | Entry point (adapter or route handler) |
| Stream starts but no tokens appear | Provider fetch | `src/unchain/providers/model_io.py` |
| Stuck after a tool call | Tool confirmation | `src/unchain/tools/confirmation.py`, `src/unchain/tools/execution.py` |
| No continuation after tool results | Kernel loop | `src/unchain/kernel/loop.py` (max_iterations) |
| Error not surfacing to user | SSE / event parsing | Event serialization layer |

### 2. Trace the request path

The full request path through the stack:

```
Frontend / client
  -> Route handler / adapter
    -> Agent.run()
      -> KernelLoop.run()
        -> step_once() loop:
          -> dispatch_phase(harnesses)
          -> fetch_model_turn(provider)  [src/unchain/providers/model_io.py]
          -> tool execution              [src/unchain/tools/execution.py]
          -> memory commit
        -> KernelRunResult
```

### 3. Check common issues

- **Context window overflow:** Count total message tokens plus tool definitions against the model's `max_context_window_tokens` in `model_capabilities.json`.
- **Missing API key:** Verify the provider's API key environment variable is set.
- **Tool confirmation deadlock:** The confirmation gate uses `threading.Event.wait()`. If no timeout is set and the frontend never responds, the stream hangs.
- **Provider SDK timeout / retry:** Some SDKs have aggressive retry policies that can cause long delays before surfacing errors.
- **SSE parsing edge cases:** Missing `\n\n` delimiters in server-sent events can cause the client parser to buffer indefinitely.

### 4. Diagnose and fix

1. Read the relevant source file(s) identified in step 1.
2. Add logging at the suspected failure point if not already present.
3. Reproduce the issue with a minimal agent configuration.
4. Apply the fix with specific file and line references.

## Testing

After applying a fix, run the full test suite:

```bash
PYTHONPATH=src pytest tests/ -q --tb=short
```

## Related

- [Architecture Overview](../skills/architecture-overview.md) -- full system data flow
- [Runtime Engine](../skills/runtime-engine.md) -- engine internals and streaming behavior
- [Agents API Reference](../api/agents.md) -- agent run configuration
- [Glossary](../appendix/glossary.md) -- terminology reference
