<div align="center">
  <img src="./assets/unchain.png" alt="unchain" style="height: 100px; margin-bottom: 32px;" />
  <h1>unchain</h1>
  <p>A lightweight Python agent framework for building tool-using AI runtimes</p>
</div>

[![Version](https://img.shields.io/badge/version-0.2.0-2563eb)](pyproject.toml)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB)](pyproject.toml)
[![License](https://img.shields.io/badge/license-Apache%202.0-0ea5e9)](LICENSE)

Docs: [English](docs/README.en.md) | [中文](docs/README.zh-CN.md)

---

## What is unchain?

unchain is a modular agent framework that separates **agent configuration** from **execution**. It provides a harness-driven kernel loop, pluggable provider support, multi-tier memory, and a rich tool system — all composable through a clean module API.

The project ships two packages under `src/`:

| Package | Role |
|---------|------|
| `unchain` | New public namespace — kernel, agent builder, providers, tools, memory, optimizers, subagents |
| `miso` | Legacy source tree — Broth engine, characters, workspace pins. Retained for backward compatibility |

## Install

```bash
pip install -e ".[dev]"
```

**Prerequisites**: Python 3.12+

## Quick Start

```python
from unchain import Agent
from unchain.tools import Toolkit, tool
from unchain.toolkits import CodeToolkit

agent = Agent(
    name="coder",
    provider="openai",
    model="gpt-5",
    tools=[
        CodeToolkit(workspace_root="."),
    ],
)

result = agent.run("Inspect the repo and explain what matters.")
print(result.messages[-1]["content"])
```

### Custom Tools

```python
@tool
def fetch_price(ticker: str) -> dict:
    """Fetch the latest stock price for a given ticker symbol."""
    return {"ticker": ticker, "price": 142.50}

agent = Agent(
    name="analyst",
    provider="anthropic",
    model="claude-sonnet-4",
    tools=[fetch_price],
)
```

### Multi-Agent Teams

```python
from unchain import Agent, Team

researcher = Agent(name="researcher", provider="openai", model="gpt-5", instructions="...")
writer = Agent(name="writer", provider="anthropic", model="claude-sonnet-4", instructions="...")

team = Team(agents=[researcher, writer])
messages, bundle = team.run("Write a report on recent AI trends.")
```

## Architecture

```
Agent.run(messages)
  |
  v
AgentBuilder --> PreparedAgent --> KernelLoop.run()
                                      |
                    +------ step_once() loop ------+
                    |                              |
                    v                              v
            dispatch_phase()              fetch_model_turn()
            (harnesses run)               (provider SDK call)
                    |                              |
                    v                              v
          Memory / Optimizers             Tool Execution
          Context Injection               Confirmation Gates
                    |                     Human Input Suspend
                    v
             HarnessDelta applied to RunState
                    |
                    v
              KernelRunResult
```

### Kernel Phases

Harnesses plug into these runtime phases:

| Phase | When | Example |
|-------|------|---------|
| `bootstrap` | Run initialization | Load memory |
| `before_model` | Before each LLM call | Inject context, optimize window |
| `after_model` | After LLM response | Process response |
| `on_tool_call` | Per tool invocation | Confirmation gates |
| `after_tool_batch` | All tools in batch done | Commit observations |
| `before_commit` | Before memory persist | Finalize state |
| `on_suspend` / `on_resume` | Pause/resume | Human input flow |

### Delta-Based State

All state mutations flow through immutable `HarnessDelta` objects:

```python
HarnessDelta.append(
    created_by="my_harness",
    messages=[{"role": "assistant", "content": "..."}],
    state_updates={"my_key": value},
)
```

## Providers

| Provider | Models | Streaming | Tools | Reasoning |
|----------|--------|-----------|-------|-----------|
| OpenAI | gpt-5, gpt-4.1, gpt-5-codex | Yes | Yes | Yes (gpt-5) |
| Anthropic | claude-sonnet-4, claude-opus-4-6, claude-haiku-3.5 | Yes | Yes | Yes (thinking) |
| Google Gemini | gemini-2.5-pro, gemini-2.5-flash | Yes | Yes | Yes |
| Ollama | Any local model | Yes | Yes | Model-dependent |

Configure via environment variables:

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
```

## Built-in Toolkits

| Toolkit | Description |
|---------|-------------|
| **CodeToolkit** | File read/write, directory listing, text search, line editing, AST parsing, shell execution |
| **ExternalAPIToolkit** | HTTP GET/POST, git operations (read-only and destructive) |
| **AskUserToolkit** | Structured user-question suspension flow (single/multiple choice) |
| **MCPToolkit** | Bridge MCP (Model Context Protocol) servers as native toolkits |

Toolkits are also discoverable from local directories and entry-point plugins.

## Memory System

Two-tier memory with pluggable backends:

```python
from unchain.memory import MemoryConfig, MemoryManager

config = MemoryConfig(
    last_n_turns=8,
    vector_top_k=4,
    long_term_enabled=True,
)
```

| Tier | Storage | Purpose |
|------|---------|---------|
| **Short-term** | In-memory / JSON sessions | Recent conversation context |
| **Long-term** | Qdrant vector DB | Semantic recall, fact extraction, profiles |

Strategies: `LastNTurns`, `SummaryToken`, `Hybrid`

## Modules

Agents are composed through pluggable modules:

```python
from unchain.agent import Agent, ToolsModule, MemoryModule, PoliciesModule

agent = Agent(
    name="assistant",
    provider="openai",
    model="gpt-5",
    modules=(
        ToolsModule(tools=(code_toolkit,)),
        MemoryModule(memory=memory_manager),
        PoliciesModule(max_iterations=32),
    ),
)
```

| Module | Purpose |
|--------|---------|
| `ToolsModule` | Register tools and toolkits |
| `MemoryModule` | Attach memory manager |
| `PoliciesModule` | Set iteration limits, subagent policies |
| `OptimizersModule` | Context window optimizers |
| `SubagentModule` | Register subagent templates |

## Subagents

Agents can delegate, handoff, or spawn parallel workers:

```python
from unchain.agent import SubagentModule
from unchain.subagents import SubagentTemplate

templates = [
    SubagentTemplate(name="researcher", agent=research_agent, allowed_modes=("delegate",)),
    SubagentTemplate(name="coder", agent=code_agent, allowed_modes=("worker",), parallel_safe=True),
]

parent = Agent(
    name="coordinator",
    modules=(SubagentModule(templates=templates),),
    ...
)
```

## Context Optimizers

Pluggable strategies for managing the context window:

| Optimizer | Strategy |
|-----------|----------|
| `LastNOptimizer` | Keep last N messages |
| `LlmSummaryOptimizer` | Summarize older messages with LLM |
| `ToolHistoryCompactionOptimizer` | Compress verbose tool results |
| `PinnedContextOptimizer` | Preserve pinned file context |

## Package Layout

```
src/
  unchain/                    # Public namespace
    kernel/                   # Execution loop, state, harnesses, deltas
    agent/                    # Agent class, builder, modules
    providers/                # OpenAI, Anthropic, Ollama model I/O
    tools/                    # Tool, Toolkit, execution, confirmation
    toolkits/                 # Built-in toolkits + MCP bridge
    memory/                   # Short-term + long-term memory
    optimizers/               # Context window optimization
    subagents/                # Delegation, handoff, workers
    schemas/                  # ResponseFormat for structured output
    runtime/                  # Provider SDK imports
  miso/                       # Legacy source tree
    agents/                   # Agent, Team
    runtime/                  # Broth engine, model capabilities
    tools/                    # Tool primitives (zero internal deps)
    toolkits/                 # Builtin toolkit implementations
    memory/                   # Memory manager, Qdrant adapter
    characters/               # CharacterAgent, schedules, evaluation
    workspace/                # Anchor-resilient file pinning
    input/                    # Human input, media upload
    schemas/                  # Response format
tests/
  evals/                      # Scenario-based evaluation framework
docs/
  en/                         # English docs (skills chapters + API reference)
  zh-CN/                      # Chinese docs
```

## Public API

```python
# Top-level
from unchain import Agent, Team

# Kernel
from unchain.kernel import KernelLoop, KernelRunResult, RunState, HarnessDelta

# Tools
from unchain.tools import Tool, Toolkit, tool, ToolParameter, ToolkitRegistry

# Providers
from unchain.providers import AnthropicModelIO, OpenAIModelIO, OllamaModelIO

# Toolkits
from unchain.toolkits import (
    CodeToolkit, ExternalAPIToolkit,
    AskUserToolkit, MCPToolkit,
)

# Memory
from unchain.memory import MemoryConfig, MemoryManager

# Schemas
from unchain.schemas import ResponseFormat
```

## Testing

```bash
# Setup
./scripts/init_python312_venv.sh

# Run tests
./run_tests.sh

# Or directly
PYTHONPATH=src pytest tests/ -q
```

## Documentation

Full bilingual docs with skills chapters and API reference:

**Skills Chapters**: Architecture Overview, Agent & Team, Runtime Engine, Tool System Patterns, Memory System, Creating Toolkits, Testing Conventions

**API Reference**: Agents, Runtime, Tools, Toolkits, Memory, Input/Workspace/Schemas

**Appendices**: Class Index (55+ classes), Export Index, Glossary, Return Shapes

See [docs/README.en.md](docs/README.en.md) for the full reading guide.

## License

[Apache 2.0](LICENSE)
