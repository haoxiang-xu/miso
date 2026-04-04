# Add a New Built-in Toolkit

This guide walks you through creating a new built-in toolkit for the unchain framework. A toolkit is a packaged collection of related tools with a manifest and lifecycle management.

## Prerequisites

- Understanding of the tool system (see [Tool System Patterns](../skills/tool-system-patterns.md))
- Familiarity with toolkit packaging (see [Creating Built-in Toolkits](../skills/creating-builtin-toolkits.md))

## Reference Files

| File | Role |
|------|------|
| `src/unchain/toolkits/builtin/workspace/` | Complex toolkit example (file operations) |
| `src/unchain/toolkits/builtin/ask_user/` | Simple toolkit example (single tool) |
| `src/unchain/tools/tool.py` | `Tool` class |
| `src/unchain/tools/toolkit.py` | `Toolkit` base class |
| `src/unchain/toolkits/__init__.py` | Toolkit exports and discovery |

## Steps

1. **Study existing toolkits** for reference patterns:
   - **Complex:** `src/unchain/toolkits/builtin/workspace/` -- multi-tool toolkit with file operations
   - **Simple:** `src/unchain/toolkits/builtin/ask_user/` -- minimal single-tool toolkit

2. **Create the toolkit directory:**
   ```
   src/unchain/toolkits/builtin/<name>/
   ```

3. **Create the `toolkit.toml` manifest** (see Template below).

4. **Create `__init__.py`** with the toolkit class:
   - Extend `Toolkit` from `unchain.tools`
   - Register tools in `__init__` via `self.register()`
   - Use the `@tool` decorator or direct `Tool()` construction
   - Add proper type hints and docstrings for all tool parameters

5. **Export from `src/unchain/toolkits/__init__.py`** so the toolkit is discoverable.

6. **Write tests** in `tests/test_<name>_toolkit.py`.

## Template

### toolkit.toml

```toml
[toolkit]
name = "<name>"
description = "<description>"
version = "0.1.0"
```

### \_\_init\_\_.py

```python
from unchain.tools import Tool, Toolkit, tool


class MyToolkit(Toolkit):
    """My toolkit description."""

    name = "<name>"

    def __init__(self):
        super().__init__()

        @tool
        def my_tool(param: str) -> dict:
            """Tool description for the LLM.

            Args:
                param: Description of param
            """
            return {"result": "..."}

        self.register(my_tool)
```

### Directory structure

```
src/unchain/toolkits/builtin/<name>/
    __init__.py      # Toolkit class
    toolkit.toml     # Manifest
```

## Testing

Run the toolkit tests:

```bash
PYTHONPATH=src pytest tests/test_<name>_toolkit.py -v --tb=short
```

## Related

- [Creating Built-in Toolkits](../skills/creating-builtin-toolkits.md) -- in-depth guide to toolkit design
- [Tool System Patterns](../skills/tool-system-patterns.md) -- how tools and toolkits interact
- [Toolkits API Reference](../api/toolkits.md) -- full toolkit API surface
- [Tools API Reference](../api/tools.md) -- tool class details
