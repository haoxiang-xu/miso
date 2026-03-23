# Architecture Overview

Canonical English skill chapter for the `architecture-overview` topic.

## Role and boundaries

This chapter explains how the package is layered, which modules are foundational, and how execution/data move from user code into the runtime loop and back out.

## Dependency view

- `miso.tools` stays foundational and dependency-light.
- `miso.runtime` depends on tools, memory, workspace, input, and schema layers.
- `miso.agents` orchestrates `Broth`, memory, and toolkits without inverting dependencies.

## Core objects

- `Agent` and `Team` as orchestration entry points.
- `Broth` as the tool-calling engine.
- `Tool`, `Toolkit`, `ToolkitRegistry`, and `ToolkitCatalogRuntime` as the tool layer.
- `MemoryManager` and related stores/strategies as context infrastructure.

## Execution and state flow

- User code constructs `Agent` or `Broth`.
- The runtime normalizes tools, prepares memory, injects pinned context, and calls a provider.
- Tool calls are executed or suspended for confirmation/human input.
- Conversation state, artifacts, and memory writes are committed before the run completes or pauses.

## Configuration surface

- Provider/model/API key selection.
- Memory configuration and long-term adapters.
- Toolkit catalog and managed toolkit IDs.

## Extension points

- Add providers in `runtime/providers/`.
- Add builtins or plugins through toolkit manifests.
- Swap memory stores/adapters without changing the orchestration API.

## Common gotchas

- The top-level API is intentionally small; most reference detail lives in subpackages.
- A fresh `Broth` instance is created per `Agent.run()` invocation.
- Catalog activation is runtime state, not import-time registration.

## Related class references

- [Agents API](../api/agents.md)
- [Runtime API](../api/runtime.md)
- [Tool System API](../api/tools.md)
- [Memory API](../api/memory.md)

## Source entry points

- `src/miso/__init__.py`
- `src/miso/agents/`
- `src/miso/runtime/`
- `src/miso/tools/`

## Detailed legacy reference

The original repository skill note is preserved below for continuity and extra examples. The canonical copy now lives in this docs tree.

> Module map, dependency graph, and data flow for the miso agent framework.

## Package Layout

```text
src/miso/
├── __init__.py          # Public API: Agent, Team
├── agents/              # High-level Agent and Team
│   ├── agent.py         #   Agent – single agent orchestration
│   └── team.py          #   Team – multi-agent channel coordination
├── runtime/             # Low-level Broth engine + providers
│   ├── engine.py        #   Broth – tool-calling loop, memory, callbacks
│   ├── payloads.py      #   Provider defaults + model capability registry
│   ├── files.py         #   OpenAI file upload helpers
│   ├── providers/       #   Lazy-loaded provider SDKs (openai, anthropic, gemini, ollama)
│   └── resources/       #   JSON configs for model defaults and capabilities
├── tools/               # Tool primitives and discovery
│   ├── tool.py          #   Tool – wrapped callable with metadata
│   ├── toolkit.py       #   Toolkit – dict container of Tools
│   ├── decorators.py    #   @tool decorator
│   ├── models.py        #   ToolParameter, confirmation types, history optimizers
│   ├── registry.py      #   ToolkitRegistry – discovers toolkits from 3 sources
│   ├── catalog.py       #   ToolkitCatalogRuntime – dynamic activation/deactivation
│   └── confirmation.py  #   ToolConfirmationRequest / Response
├── toolkits/            # Builtin + MCP toolkits
│   ├── base.py          #   BuiltinToolkit – workspace-safe base class
│   ├── mcp.py           #   MCPToolkit – MCP server bridge
│   └── builtin/         #   Pre-built toolkits (workspace, terminal, ask_user, external_api)
├── memory/              # Short-term and long-term memory
│   ├── manager.py       #   MemoryManager – orchestrates stores + strategies
│   ├── config.py        #   MemoryConfig / LongTermMemoryConfig dataclasses
│   ├── strategies.py    #   Context window strategies (LastNTurns, Summary, Hybrid)
│   ├── stores.py        #   SessionStore, VectorStoreAdapter interfaces
│   ├── long_term.py     #   LongTermExtractor, profile stores
│   ├── qdrant.py        #   Qdrant vector DB adapter
│   └── tool_history.py  #   Tool call history compaction
├── input/               # Human interaction
│   ├── human_input.py   #   HumanInputRequest / Response, structured selectors
│   └── media.py         #   Media upload utilities
├── workspace/           # Session-scoped pins
│   └── pins.py          #   Anchor-resilient file pin system
├── schemas/             # Structured output
│   └── response.py      #   ResponseFormat for JSON schema output
└── _internal/           # Private helpers
    └── agent_shared.py  #   as_text(), normalize_mentions()
```

## Import Hierarchy

The dependency direction flows **downward** — upper layers import from lower layers, never the reverse.

```text
Layer 0  (public API)      miso              → exports Agent, Team
Layer 1  (orchestration)   miso.agents       → imports runtime, tools, toolkits, memory
Layer 2  (engine)          miso.runtime      → imports tools, memory, workspace, input, schemas
Layer 3  (tool system)     miso.tools        → imports nothing from miso (self-contained)
Layer 3  (toolkit impls)   miso.toolkits     → imports tools, workspace
Layer 3  (memory)          miso.memory       → imports runtime (for summarisation calls), tools
Layer 4  (primitives)      miso.input, miso.workspace, miso.schemas, miso._internal
```

**Rule**: `miso.tools` is the foundation — it has **zero internal dependencies**. Everything else builds on top of it.

## Data Flow: Request → Response

```text
User code
  │
  ▼
Agent.run(messages, session_id, ...)
  │  1. Builds a merged Toolkit from all registered tools
  │  2. Creates a fresh Broth runtime engine
  │  3. Attaches MemoryManager (if configured)
  │  4. Calls broth.run(messages, toolkit, ...)
  │
  ▼
Broth.run()  ─── main execution loop ───
  │
  │  for each iteration (up to max_iterations):
  │    ┌─────────────────────────────────────────┐
  │    │ 1. memory.prepare_messages()            │
  │    │    • injects workspace pin context      │
  │    │    • applies context window strategy     │
  │    │                                          │
  │    │ 2. _fetch_once(messages, tools, ...)    │
  │    │    • dispatches to provider SDK          │
  │    │    • receives assistant message + calls  │
  │    │                                          │
  │    │ 3. for each tool_call:                  │
  │    │    • confirmation gate (if required)     │
  │    │    • toolkit.execute(name, args)         │
  │    │    • observation injection (if observe)  │
  │    │                                          │
  │    │ 4. memory.commit_messages()             │
  │    │    • stores conversation turn            │
  │    │    • extracts long-term facts            │
  │    │                                          │
  │    │ 5. check: no more tool_calls? → break   │
  │    └─────────────────────────────────────────┘
  │
  ▼
Returns (messages, bundle)
  │  bundle contains: consumed_tokens, artifacts, stop_reason, ...
  │
  ▼
Back to Agent.run() → returns to user code
```

## Component Relationships

| Component               | Depends On                                                   | Depended On By                   |
| ----------------------- | ------------------------------------------------------------ | -------------------------------- |
| `Tool` / `Toolkit`      | — (self-contained)                                           | Everything                       |
| `BuiltinToolkit`        | `Toolkit`, `workspace.pins`                                  | Builtin toolkit implementations  |
| `ToolkitRegistry`       | `Toolkit`, filesystem                                        | `Agent`, `ToolkitCatalogRuntime` |
| `ToolkitCatalogRuntime` | `ToolkitRegistry`, `Toolkit`                                 | `Agent`, `Broth`                 |
| `MemoryManager`         | `SessionStore`, context strategies, `Broth` (for summaries)  | `Broth`                          |
| `Broth`                 | `Toolkit`, `MemoryManager`, providers, `ResponseFormat`      | `Agent`                          |
| `Agent`                 | `Broth`, `Toolkit`, `MemoryManager`, `ToolkitCatalogRuntime` | `Team`, user code                |
| `Team`                  | `Agent`                                                      | User code                        |

## Key Design Principles

1. **Minimal public surface** — Only `Agent` and `Team` are top-level exports. Everything else is imported from subpackages.

2. **Fresh engine per run** — `Agent.run()` creates a new `Broth` instance each time. No leftover state between runs (memory is externalized).

3. **Tools are data** — A `Tool` is just metadata + a callable. Parameter schemas are auto-inferred from Python type hints and docstrings.

4. **Three toolkit discovery sources** — Builtin (shipped with miso), local (user directories), plugins (entry points). All use the same `toolkit.toml` manifest.

5. **Memory is optional and layered** — Short-term context strategies and long-term vector-backed profiles are independently configurable.

6. **Provider-agnostic core** — The `Broth` engine speaks a canonical message format. Provider-specific projections happen at the boundary.

## Related Skills

- [creating-builtin-toolkits.md](creating-builtin-toolkits.md) — How to add a new builtin toolkit
- [tool-system-patterns.md](tool-system-patterns.md) — Tool definition and registration patterns
- [memory-system.md](memory-system.md) — Memory tiers and configuration
- [runtime-engine.md](runtime-engine.md) — Broth execution loop details
- [agent-and-team.md](agent-and-team.md) — Agent/Team high-level API
- [testing-conventions.md](testing-conventions.md) — Test patterns and eval framework
