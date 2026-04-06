# Add a New Tool

This guide explains how to add a new tool to an existing toolkit or create a standalone tool. Tools are the functions that agents can call during execution to interact with the outside world.

## Prerequisites

- Understanding of the tool system (see [Tool System Patterns](../skills/tool-system-patterns.md))
- If adding to a toolkit, familiarity with that toolkit's structure (see [Creating Built-in Toolkits](../skills/creating-builtin-toolkits.md))

## Reference Files

| File | Role |
|------|------|
| `src/unchain/tools/tool.py` | `Tool` class and parameter inference |
| `src/unchain/tools/toolkit.py` | `Toolkit` base class |
| `src/unchain/tools/execution.py` | `ToolExecutionHarness` -- how tools are invoked at runtime |
| `src/unchain/tools/confirmation.py` | Tool confirmation gate logic |
| `src/unchain/tools/messages.py` | Tool result message formatting |

## Steps

### Adding a tool to an existing toolkit

1. **Read the toolkit source** in `src/unchain/toolkits/builtin/<toolkit>/` to understand its structure and conventions.

2. **Add the tool function** with proper type hints and a docstring. The first line of the docstring becomes the tool description shown to the LLM.

3. **Register the tool** via `self.register()` in the toolkit's `__init__` method.

### Creating a standalone tool

1. **Use the `@tool` decorator** to create a standalone tool from a plain function.

2. **Define parameters with type hints.** The tool system infers the JSON schema for the LLM from your type annotations and docstring.

## Template

### Standalone tool using the decorator

```python
from unchain.tools import tool


@tool
def my_tool(param1: str, param2: int = 10) -> dict:
    """Tool description. First line becomes the tool description for the LLM.

    Args:
        param1: Description of param1
        param2: Description of param2
    """
    return {"result": "..."}
```

### Tool with confirmation gate

For tools that perform destructive or sensitive operations, require user confirmation before execution:

```python
Tool(
    name="dangerous_tool",
    func=my_func,
    requires_confirmation=True,
)
```

### Tool with observation injection

For tools whose output should be injected back into the conversation as an observation message:

```python
Tool(
    name="observe_tool",
    func=my_func,
    observe=True,
)
```

## Testing

Run tests for the specific tool:

```bash
PYTHONPATH=src pytest tests/ -q --tb=short -k "<tool_name>"
```

## Related

- [Tool System Patterns](../skills/tool-system-patterns.md) -- detailed tool system concepts
- [Creating Built-in Toolkits](../skills/creating-builtin-toolkits.md) -- toolkit packaging and registration
- [Tools API Reference](../api/tools.md) -- full API surface for the tool system
- [Toolkits API Reference](../api/toolkits.md) -- toolkit registration and discovery
