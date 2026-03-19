# miso

[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](./.python-version)
[![Agent Builder](https://img.shields.io/badge/Agent%20Builder-Multi--agent%20Python-1f6feb.svg)](./docs/README.en.md)
[![Providers](https://img.shields.io/badge/Providers-OpenAI%20%7C%20Anthropic%20%7C%20Gemini%20%7C%20Ollama-0a7f5a.svg)](./docs/README.en.md)
[![MCP](https://img.shields.io/badge/MCP-Bridge-black.svg)](./docs/README.en.md#mcp-toolkit-bridge-mcp)
[![Tests](https://img.shields.io/badge/Tests-pytest-6f42c1.svg)](./run_tests.sh)

[![English Docs](https://img.shields.io/badge/Read-English-111111?style=for-the-badge)](./docs/README.en.md)
[![中文文档](https://img.shields.io/badge/阅读-中文-cc332b?style=for-the-badge)](./docs/README.zh-CN.md)

`miso` is a lightweight Python agent builder for composing multi-turn, tool-using agents from small, reusable parts. It provides high-level `Agent` / `Team` APIs, the lower-level `broth` runtime loop, session memory, tool and toolkit abstractions, structured output, multimodal input blocks, and an MCP bridge in one repo.

## What Is `miso`

`miso` is designed around a small set of composable building blocks:

- `Agent` and `Team` for single-agent and multi-agent workflows
- `broth` as the runtime loop for provider calls and tool execution
- `memory` for session context windows and optional long-term recall
- `tool` / `toolkit` abstractions for local and remote tools
- `response_format` for schema-constrained output
- `media` for canonical multimodal input blocks
- `mcp` for exposing MCP servers as `miso` toolkits

## Built With / Uses

- Python `3.12` with a repo-standard `.venv`
- `requirements.txt`-managed dependencies
- OpenAI, Anthropic, Gemini, and Ollama provider support
- Built-in `workspace`, `terminal`, `external_api`, and `interaction` toolkits
- `pytest` via [`run_tests.sh`](/Users/red/Desktop/GITRepo/miso/run_tests.sh)

## How To Set Up

`miso` expects Python `3.12.x` and a `.venv/` at the repository root.

macOS / Linux:

```bash
./scripts/init_python312_venv.sh
source .venv/bin/activate
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init_python312_venv.ps1
.\.venv\Scripts\Activate.ps1
```

Manual setup is also supported if you already have Python `3.12.x` available:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Quick Start

```python
from miso import Agent, Team

planner = Agent(
    name="planner",
    provider="openai",
    model="gpt-5",
    api_key="YOUR_OPENAI_API_KEY",
    instructions="You plan work and coordinate the team.",
)

reviewer = Agent(
    name="reviewer",
    provider="openai",
    model="gpt-5",
    api_key="YOUR_OPENAI_API_KEY",
    instructions="You review plans and call out risks.",
)

team = Team(
    agents=[planner, reviewer],
    owner="planner",
    channels={"shared": ["planner", "reviewer"]},
)

result = team.run("Give me a minimal release plan.")
print(result["final"])
```

## Testing

Run the full test suite:

```bash
./run_tests.sh
```

Smoke tests use optional environment variables for provider-specific coverage:

- OpenAI: `OPENAI_API_KEY`, `OPENAI_MODEL`
- Anthropic: `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`
- Gemini: `GEMINI_API_KEY` or `GOOGLE_API_KEY`, plus `GEMINI_MODEL`
- Ollama: local server at `http://localhost:11434`, optional `OLLAMA_MODEL`
- MCP smoke: `MCP_SMOKE=1` and local `npx`

## Read The Full Docs

- [Full English manual](./docs/README.en.md)
- [完整中文文档](./docs/README.zh-CN.md)

The long-form manuals cover the public API surface, architecture, session memory, tool system, toolkit catalog, MCP bridge, provider differences, end-to-end examples, tests, and repo boundaries.
