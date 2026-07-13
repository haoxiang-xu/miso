# Add a New Built-in Toolkit

This guide walks you through creating a new built-in toolkit for the unchain framework. A toolkit is a packaged collection of related tools with a manifest and lifecycle management.

## Prerequisites

- Understanding of the tool system (see [Tool System Patterns](../skills/tool-system-patterns.md))
- Familiarity with toolkit packaging (see [Creating Built-in Toolkits](../skills/creating-builtin-toolkits.md))

## Reference Files

| File | Role |
|------|------|
| `src/unchain/toolkits/builtin/core/` | Complex toolkit example (code operations) |
| `src/unchain/toolkits/builtin/agent_reach/` | Smaller read-only builtin toolkit example |
| `src/unchain/toolkits/builtin/plan/` | Process-local stateful toolkit example |
| `src/unchain/tools/tool.py` | `Tool` class |
| `src/unchain/tools/toolkit.py` | `Toolkit` base class |
| `src/unchain/toolkits/__init__.py` | Toolkit exports and discovery |

## Steps

1. **Study existing toolkits** for reference patterns:
   - **Complex:** `src/unchain/toolkits/builtin/core/` -- multi-tool toolkit with code operations
   - **Smaller:** `src/unchain/toolkits/builtin/agent_reach/` -- focused read-only builtin toolkit example
   - **Stateful:** `src/unchain/toolkits/builtin/plan/` -- process-local state plus confirmation-gated finalization

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

[[artifact_kinds]]
kind = "example.report"
display_name = "Example report"
description = "Rendered from immutable artifact snapshots."
icon = "bar_chart"
fallback_renderer = "markdown"
```

`fallback_renderer` must be one of `markdown`, `text`, `table`, `kv`, `log`, `link`, or `json`. `icon` is either a PuPu builtin icon id or a relative `.svg` / `.png` asset inside the toolkit package. Do not put raw SVG, HTML, CSS, React components, or icon hints in artifact runtime events; events should only carry immutable snapshot data.

If a toolkit mutates workspace text files, artifact events alone are not enough for the run-level file summary or undo support. Report the mutation through the active execution context when it is available:

```python
context = self.current_execution_context
tracker = getattr(context, "workspace_changes", None) if context is not None else None
if tracker is not None:
    tracker.record_text_file_change(
        str(path),
        before_text,
        after_text,
        operation="modified",
        tool_name="my_tool",
        call_id=context.call_id,
        turn_id=context.turn_id,
    )
```

The workspace change tracker builds one run-scoped `workspace_change_set` artifact with a net diff and safe restore metadata. Toolkit-specific artifacts remain useful for semantic UI, but they do not participate in net diff or undo unless the file mutation is reported to the tracker.

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
