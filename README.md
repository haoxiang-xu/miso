# miso

`miso` is a Python agent framework organized around a small public API and a clear internal package layout.

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

## Layout

The project now uses a `src/` layout with one canonical package:

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

## Quick Examples

High-level agent:

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
```

Low-level runtime:

```python
from miso.runtime import Broth
from miso.toolkits import WorkspaceToolkit

runtime = Broth(provider="openai", model="gpt-5")
runtime.add_toolkit(WorkspaceToolkit(workspace_root="."))
messages, bundle = runtime.run("Inspect the repo and explain the structure.")
```

Structured output:

```python
from miso.runtime import Broth
from miso.schemas import ResponseFormat

fmt = ResponseFormat(
    name="answer",
    schema={
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    },
)

runtime = Broth(provider="openai", model="gpt-5")
messages, bundle = runtime.run("Summarize this repo.", response_format=fmt)
```

## Built-in Toolkits

- `WorkspaceToolkit`: workspace file and line editing
- `TerminalToolkit`: restricted terminal execution and sessions
- `ExternalAPIToolkit`: simple HTTP GET/POST tools
- `AskUserToolkit`: structured user-question suspension flow
- `MCPToolkit`: expose MCP servers as toolkits

## Testing

```bash
./run_tests.sh
```

This script expects Python 3.12 and an editable install created by `./scripts/init_python312_venv.sh`.
