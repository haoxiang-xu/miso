# unchain

> unchain harness

[![Version](https://img.shields.io/badge/version-0.2.0-2563eb)](pyproject.toml)
[![Dependencies](https://img.shields.io/badge/dependencies-8%20core-16a34a)](pyproject.toml)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB)](pyproject.toml)
[![License](https://img.shields.io/badge/license-Apache%202.0-0ea5e9)](LICENSE)

English / 中文: [English Docs](docs/README.en.md) | [中文文档](docs/README.zh-CN.md)

`unchain` is the new public package name for the project. Legacy `miso` imports remain supported during the migration, but new examples should prefer `unchain`.

## Install

```bash
pip install -e ".[dev]"
```

## Public API

Top-level exports are intentionally minimal:

```python
from unchain import Agent, Team
```

Lower-level modules are imported from their own packages:

```python
from unchain.runtime import Broth
from unchain.tools import Tool, Toolkit, ToolParameter, tool
from unchain.toolkits import (
    AskUserToolkit,
    ExternalAPIToolkit,
    MCPToolkit,
    TerminalToolkit,
    WorkspaceToolkit,
)
from unchain.schemas import ResponseFormat
from unchain.memory import MemoryConfig, MemoryManager
from unchain.input import media
```

## Quick Example

```python
from unchain import Agent
from unchain.toolkits import WorkspaceToolkit, TerminalToolkit

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
src/unchain/   # new public import namespace
src/miso/      # legacy source tree retained during migration
```

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
