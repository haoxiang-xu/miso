# Unchain - Codex Project Instructions

## Project Overview

**unchain** is a modular Python framework for building tool-using AI runtimes. Source lives under `src/unchain/`.

## Documentation

Full documentation: [English](docs/README.en.md) | [中文](docs/README.zh-CN.md)

- **Skills chapters**: Architecture, agents, runtime, tools, memory, toolkits, testing
- **API reference**: Class-level docs for all production modules
- **Guides**: How to add models, providers, tools, toolkits, harnesses
- **Appendices**: Class index, export index, glossary

## Key Architecture

```
Agent.run() → AgentBuilder → PreparedAgent → KernelLoop.run()
  → step_once() loop:
    → dispatch_phase(harnesses) → fetch_model_turn(provider) → tool execution → memory commit
  → KernelRunResult
```

## Testing

```bash
PYTHONPATH=src pytest tests/ -q
```
- Tests use fake clients (FakeOpenAIClient, FakeAnthropicClient) — must accept `**kwargs` in `__init__`
- Known flaky: `test_read_file_ast_parses_python_file`, `test_pinned_prompt_messages_relocate_non_python_ranges_via_declaration_metadata`

## Code Style

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
| `src/unchain/providers/model_io.py` | Provider implementations (OpenAI, Anthropic, Ollama, Gemini) |
| `src/unchain/tools/execution.py` | ToolExecutionHarness — runs tools |
| `src/unchain/tools/tool.py` | Tool class and parameter inference |
| `src/unchain/memory/runtime.py` | KernelMemoryRuntime |
| `src/unchain/memory/manager.py` | MemoryManager |
| `src/unchain/runtime/resources/model_capabilities.json` | Model registry |
