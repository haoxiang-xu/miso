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

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **unchain** (8099 symbols, 14390 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/unchain/context` | Codebase overview, check index freshness |
| `gitnexus://repo/unchain/clusters` | All functional areas |
| `gitnexus://repo/unchain/processes` | All execution flows |
| `gitnexus://repo/unchain/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
