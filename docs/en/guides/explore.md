# Explore the Architecture

This guide provides a map of the unchain codebase, organized by subsystem. Use it as a starting point when you need to understand or modify a specific part of the framework.

## Prerequisites

- Basic familiarity with the project structure (see [Architecture Overview](../skills/architecture-overview.md))

## Architecture Map

### Kernel Loop

The central execution engine that drives agent runs through harness-dispatched phases.

| File | Role |
|------|------|
| `src/unchain/kernel/loop.py` | `KernelLoop` -- main execution engine |
| `src/unchain/kernel/state.py` | `RunState` -- mutable run state with message versioning |
| `src/unchain/kernel/harness.py` | `BaseRuntimeHarness` protocol |
| `src/unchain/kernel/delta.py` | `HarnessDelta` -- immutable state mutation operations |

### Memory System

Manages conversation memory, context bootstrapping, and long-term storage.

| File | Role |
|------|------|
| `src/unchain/memory/runtime.py` | `KernelMemoryRuntime` |
| `src/unchain/memory/manager.py` | `MemoryManager` |
| `src/unchain/memory/config.py` | Memory configuration |
| `src/unchain/memory/stores.py` | Storage backends |
| `src/unchain/memory/long_term.py` | Long-term memory persistence |
| `src/unchain/memory/bootstrap.py` | Bootstrap harness (context injection) |

### Tool Execution

How tools are defined, discovered, and executed during agent runs.

| File | Role |
|------|------|
| `src/unchain/tools/execution.py` | `ToolExecutionHarness` -- runs tools during kernel phases |
| `src/unchain/tools/tool.py` | `Tool` class and parameter inference |
| `src/unchain/tools/toolkit.py` | `Toolkit` base class |
| `src/unchain/tools/confirmation.py` | Tool confirmation gates |
| `src/unchain/tools/messages.py` | Provider-specific tool message formatting |

### Providers

LLM provider integrations (OpenAI, Anthropic, Gemini, Ollama).

| File | Role |
|------|------|
| `src/unchain/providers/model_io.py` | Provider implementations and `_NativeModelIOBase` |
| `src/unchain/agent/model_io.py` | `ModelIOFactoryRegistry` -- provider name resolution |

### Agent Builder

User-facing API for constructing and running agents.

| File | Role |
|------|------|
| `src/unchain/agent/agent.py` | `Agent` class -- primary user-facing API |
| `src/unchain/agent/builder.py` | `AgentBuilder` -- constructs kernel runs |
| `src/unchain/agent/spec.py` | Agent specification |
| `src/unchain/agent/modules/` | Pluggable agent modules (Tools, Memory, Policies, etc.) |

### Subagents

Spawning and coordinating child agents within a parent run.

| File | Role |
|------|------|
| `src/unchain/subagents/executor.py` | Subagent execution |
| `src/unchain/subagents/plugin.py` | Subagent plugin interface |
| `src/unchain/subagents/types.py` | Subagent type definitions |

### Optimizers

Context optimization strategies (message truncation, summarization).

| File | Role |
|------|------|
| `src/unchain/optimizers/base.py` | Optimizer base class |
| `src/unchain/optimizers/last_n.py` | Last-N messages optimizer |
| `src/unchain/optimizers/llm_summary.py` | LLM-based summarization optimizer |

### Workspace

File pinning and workspace management.

| File | Role |
|------|------|
| `src/unchain/workspace/pins.py` | File pinning system |

### Runtime Resources

Static configuration files for model capabilities and defaults.

| File | Role |
|------|------|
| `src/unchain/runtime/resources/model_capabilities.json` | Model registry with capabilities |
| `src/unchain/runtime/resources/model_default_payloads.json` | Default request payloads per model |
| `src/unchain/schemas/models.py` | Named model constants |

## Execution Flow

```
Agent.run()
  -> AgentBuilder
    -> PreparedAgent
      -> KernelLoop.run()
        -> step_once() loop:
          -> dispatch_phase(harnesses)
          -> fetch_model_turn(provider)
          -> tool execution
          -> memory commit
        -> KernelRunResult
```

## Key Extension Points

| What you want to do | Start here |
|---------------------|-----------|
| Add behavior to the execution loop | [Add a Harness](add-harness.md) |
| Support a new LLM service | [Add a Provider](add-provider.md) |
| Register a new model | [Add a Model](add-model.md) |
| Add a new tool | [Add a Tool](add-tool.md) |
| Create a tool collection | [Add a Toolkit](add-toolkit.md) |

## Related

- [Architecture Overview](../skills/architecture-overview.md) -- conceptual architecture guide
- [Agent and Subagents](../skills/agent-and-team.md) -- agent composition patterns
- [Class Index](../appendix/class-index.md) -- complete class reference
- [Export Index](../appendix/export-index.md) -- public API exports
- [Glossary](../appendix/glossary.md) -- terminology reference
