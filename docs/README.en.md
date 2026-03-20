# Miso Overview

`miso` is a Python agent framework with a minimal top-level API and explicit lower-level modules.

## Official Imports

```python
from miso import Agent, Team
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

## Runtime Layers

- `miso.agents`: high-level `Agent` and `Team`
- `miso.runtime`: the lower-level `Broth` runtime and model payload resources
- `miso.tools`: tool definitions, decorators, registry, and catalog
- `miso.toolkits`: built-in toolkits and MCP integration
- `miso.memory`: short-term and long-term memory components
- `miso.input`: human-input and media helpers
- `miso.schemas`: structured output models

## Built-in Toolkits

- `WorkspaceToolkit`: file, directory, line-edit, and workspace pin tools
- `TerminalToolkit`: restricted shell execution and persistent sessions
- `ExternalAPIToolkit`: basic outbound HTTP access
- `AskUserToolkit`: structured user-question suspension flow
- `MCPToolkit`: remote MCP server bridge

## Quick Start

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

## Structured Output

```python
from miso.runtime import Broth
from miso.schemas import ResponseFormat

runtime = Broth(provider="openai", model="gpt-5")
fmt = ResponseFormat(
    name="summary",
    schema={
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    },
)
messages, bundle = runtime.run("Summarize the repository.", response_format=fmt)
```

## Testing

```bash
./scripts/init_python312_venv.sh
./run_tests.sh
```
