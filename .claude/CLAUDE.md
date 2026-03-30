# Unchain Harness - Claude Code Project Instructions

## Project Overview

This is the **unchain** agent framework — a modular Python framework for building tool-using AI runtimes. The repo ships two packages under `src/`:

- **`unchain`** — New public namespace: kernel loop, agent builder, providers, tools, memory, optimizers, subagents
- **`miso`** — Legacy source tree: Broth engine, characters, workspace pins (retained for backward compat)

## Key Architecture

### Execution Flow
```
Agent.run() → AgentBuilder → PreparedAgent → KernelLoop.run()
  → step_once() loop:
    → dispatch_phase(harnesses) → fetch_model_turn(provider) → tool execution → memory commit
  → KernelRunResult
```

### Core Concepts
- **KernelLoop** (`unchain/kernel/loop.py`): Harness-driven execution loop
- **HarnessDelta**: Immutable state mutations (append/insert/replace/delete messages)
- **RunState** (`unchain/kernel/state.py`): Mutable run state with message versioning
- **ModelIO** (`unchain/providers/model_io.py`): Provider abstraction (OpenAI, Anthropic, Ollama, Gemini)
- **AgentModule**: Pluggable agent composition (ToolsModule, MemoryModule, PoliciesModule, etc.)

### Two Engine Paths
1. **Unchain kernel** (`unchain/kernel/loop.py`): New architecture with harnesses and deltas
2. **Miso Broth** (`miso/runtime/engine.py`): Legacy engine, 157KB monolith

PuPu (the Electron frontend) uses unchain agents via `miso_runtime/server/miso_adapter.py`.

## Development Conventions

### Testing
```bash
PYTHONPATH=src pytest tests/ -q
```
- Tests use fake clients (FakeOpenAIClient, FakeAnthropicClient) — these must accept `**kwargs` in `__init__`
- Known flaky: `test_read_file_ast_parses_python_file`, `test_pinned_prompt_messages_relocate_non_python_ranges_via_declaration_metadata`
- Evals framework in `tests/evals/`

### File Layout
- Source: `src/unchain/` and `src/miso/`
- Tests: `tests/`
- Docs: `docs/en/` and `docs/zh-CN/`
- Model configs: `src/miso/runtime/resources/model_capabilities.json` and `model_default_payloads.json`
- Toolkit manifests: `src/*/toolkits/builtin/*/toolkit.toml`

### Adding a New Model
1. Add entry to `src/miso/runtime/resources/model_capabilities.json`
2. Add default payload to `src/miso/runtime/resources/model_default_payloads.json`
3. If model has a different API name, set `provider_model` field
4. Update `src/miso/schemas/models.py` if adding a named constant

### Adding a New Provider
1. Create `ModelIO` implementation in `src/unchain/providers/model_io.py`
2. Register in `ModelIOFactoryRegistry` (`src/unchain/agent/model_io.py`)
3. Add provider-specific message builder in `src/unchain/tools/messages.py`

### Adding a New Toolkit
1. Create directory under `src/unchain/toolkits/builtin/<name>/`
2. Add `toolkit.toml` manifest
3. Implement toolkit class extending `Toolkit`
4. Register tools via `self.register()` or `@tool` decorator

### Code Style
- Python 3.12+ features OK (type unions `X | Y`, etc.)
- `from __future__ import annotations` in most files
- Dataclasses preferred over dicts for structured data
- No default mutable arguments (use `field(default_factory=...)`)

## Key Files Reference

| File | Purpose |
|------|---------|
| `src/unchain/kernel/loop.py` | KernelLoop — main execution engine |
| `src/unchain/kernel/state.py` | RunState — mutable run state |
| `src/unchain/kernel/harness.py` | RuntimeHarness protocol |
| `src/unchain/agent/agent.py` | Agent class — user-facing API |
| `src/unchain/agent/builder.py` | AgentBuilder — constructs kernel runs |
| `src/unchain/providers/model_io.py` | Provider implementations (OpenAI, Anthropic, Ollama) |
| `src/unchain/tools/execution.py` | ToolExecutionHarness — runs tools |
| `src/unchain/tools/tool.py` | Tool class and parameter inference |
| `src/unchain/memory/runtime.py` | KernelMemoryRuntime |
| `src/unchain/memory/manager.py` | MemoryManager (96KB) |
| `src/miso/runtime/engine.py` | Broth — legacy engine (157KB) |
| `src/miso/runtime/resources/model_capabilities.json` | Model registry |
| `src/miso/characters/character.py` | CharacterAgent system |
| `src/miso/workspace/pins.py` | File pinning system |
