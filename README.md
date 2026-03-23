# miso

[![Version](https://img.shields.io/badge/version-0.2.0-2563eb)](pyproject.toml)
[![Dependencies](https://img.shields.io/badge/dependencies-8%20core-16a34a)](pyproject.toml)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB)](pyproject.toml)
[![License](https://img.shields.io/badge/license-Apache%202.0-0ea5e9)](LICENSE)

English / 中文: [English Docs](docs/README.en.md) | [中文文档](docs/README.zh-CN.md)

`miso` is a lightweight Python agent framework organized around a small public API and explicit lower-level modules.

## Install

```bash
pip install -e ".[dev]"
```

## Public API

Top-level exports are intentionally minimal:

```python
from miso import Agent, Team
```

Lower-level modules are imported from their own packages:

```python
from miso.runtime import Broth
from miso.tools import Tool, Toolkit, ToolParameter, tool
from miso.toolkits import (
    AskUserToolkit,
    ExternalAPIToolkit,
    MCPToolkit,
    TerminalToolkit,
    WorkspaceToolkit,
)
from miso.schemas import ResponseFormat
from miso.memory import MemoryConfig, MemoryManager
from miso.input import media
```

## Quick Example

```python
from miso import Agent
from miso.toolkits import WorkspaceToolkit, TerminalToolkit

agent = Agent(
    name="coder",
    provider="openai",
    model="gpt-5",
    tools=[
        WorkspaceToolkit(workspace_root="."),
        TerminalToolkit(workspace_root=".", terminal_strict_mode=True),
    ],
)

messages, bundle = agent.run("Inspect the repo and explain what matters.")
```

## Package Layout

```text
src/miso/
  agents/
  runtime/
  tools/
  toolkits/
  memory/
  input/
  workspace/
  schemas/
  _internal/
```

## Built-in Toolkits

- `WorkspaceToolkit`: workspace file, Python AST, and line editing
- `TerminalToolkit`: restricted terminal execution and sessions
- `ExternalAPIToolkit`: simple HTTP GET/POST tools plus git commands (read-only and destructive)
- `AskUserToolkit`: structured user-question suspension flow
- `MCPToolkit`: expose MCP servers as toolkits

## Testing

```bash
./scripts/init_python312_venv.sh
./run_tests.sh
```
