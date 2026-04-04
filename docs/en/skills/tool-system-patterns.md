# Tool System Patterns

Canonical English skill chapter for the `tool-system-patterns` topic.

## Role and boundaries

This chapter covers the framework tool abstraction from raw callable inference through manifest discovery and runtime activation.

## Dependency view

- `Tool` and `Toolkit` are the local execution primitives.
- `ToolkitRegistry` reads manifests and validates metadata against runtime objects.
- `ToolkitCatalogRuntime` layers runtime activation/deactivation on top of discovered descriptors.

## Core objects

- `Tool`
- `Toolkit`
- `ToolParameter`
- `ToolkitRegistry`
- `ToolkitCatalogRuntime`
- `ToolConfirmationRequest`
- `ToolConfirmationResponse`

## Execution and state flow

- Wrap a callable into a `Tool`.
- Register it into a `Toolkit`.
- Discover toolkits from builtin/local/plugin sources through manifests.
- Optionally allow the model to activate/deactivate toolkits at runtime through the catalog.

## Configuration surface

- `observe` and `requires_confirmation` flags.
- History payload optimizers.
- Registry local roots, enabled plugins, and catalog managed IDs.

## Extension points

- Create builtin toolkits.
- Publish plugin toolkits via entry points.
- Customize history compaction and tool metadata.

## Common gotchas

- Tool-name collisions prevent simultaneous activation.
- Manifest and runtime metadata must agree.
- `@tool` returns a `Tool`, not a raw function.

## Related class references

- [Tool System API](../api/tools.md)
- [Toolkit Implementations](../api/toolkits.md)
- [Builtin Toolkit Guide](creating-builtin-toolkits.md)

## Source entry points

- `src/unchain/tools/tool.py`
- `src/unchain/tools/toolkit.py`
- `src/unchain/tools/registry.py`
- `src/unchain/tools/catalog.py`

## Detailed legacy reference

The original repository skill note is preserved below for continuity and extra examples. The canonical copy now lives in this docs tree.

> How tools are defined, registered, discovered, and managed. Covers `Tool`, `Toolkit`, `@tool` decorator, parameter inference, confirmation flow, and the dynamic catalog.

## Core Abstractions

```text
Tool          A single callable with metadata (name, description, JSON schema parameters)
Toolkit       A dict of Tools — register, lookup, execute by name
@tool         Decorator that turns a function into a Tool object
ToolParameter Manual parameter definition (rarely needed — auto-inference handles most cases)
```

## Tool Class Anatomy

```python
from unchain.tools import Tool

t = Tool(
    name="greet",
    description="Say hello.",
    func=lambda name: {"message": f"Hello, {name}!"},
    parameters=[ToolParameter(name="name", description="Who to greet", type_="string", required=True)],
    observe=False,               # If True, Broth injects an observation turn after execution
    requires_confirmation=False, # If True, Broth suspends for user approval before execution
)

result = t.execute({"name": "Alice"})
# → {"message": "Hello, Alice!"}
```

You almost never construct `Tool` directly — use `@tool` or `Toolkit.register()` instead.

## Parameter Inference

Parameters are **auto-inferred** from the function signature and docstring. Manual `ToolParameter` is only needed for edge cases.

### Type Hint → JSON Schema

| Python Type                 | JSON Schema `type`              |
| --------------------------- | ------------------------------- |
| `str`                       | `"string"`                      |
| `int`                       | `"integer"`                     |
| `float`                     | `"number"`                      |
| `bool`                      | `"boolean"`                     |
| `list[T]`, `tuple[T, ...]`  | `"array"` with `items` from `T` |
| `dict[K, V]`                | `"object"`                      |
| `T \| None` (`Optional[T]`) | type of `T` (None is stripped)  |

### Inference Sources

```python
def read_files(self, paths: list[str], max_chars_per_file: int = 20000) -> dict[str, Any]:
    """Read UTF-8 text files from the workspace.

    :param paths: File paths relative to workspace root.
    :param max_chars_per_file: Maximum characters to return per file.
    """
```

What gets extracted:

| Source               | Extracted                                                           |
| -------------------- | ------------------------------------------------------------------- |
| Function name        | → `tool.name` = `"read_files"`                                             |
| Docstring first line | → `tool.description` = `"Read UTF-8 text files..."`                        |
| Type hints           | → parameter types (`array`, `integer`)                                     |
| Default values       | → parameter `required` flag (`paths` required, `max_chars_per_file` optional) |
| `:param ...:` lines  | → parameter descriptions                                            |
| `self` parameter     | → **skipped** automatically                                         |

### Docstring Styles Supported

```python
# Style 1: Sphinx-style
"""Description.

:param path: File path.
:param max_chars: Limit.
"""

# Style 2: Google-style
"""Description.

Args:
    path: File path.
    max_chars: Limit.
"""
```

Both produce the same parameter schema.

## The `@tool` Decorator

### Bare usage (most common)

```python
from unchain.tools import tool

@tool
def greet(name: str) -> dict:
    """Say hello to someone."""
    return {"message": f"Hello, {name}!"}

# greet is now a Tool object, not a plain function
assert isinstance(greet, Tool)
assert greet.name == "greet"
```

### With options

```python
@tool(name="custom_name", observe=True, requires_confirmation=True)
def greet(name: str) -> dict:
    """Say hello to someone."""
    return {"message": f"Hello, {name}!"}
```

### Available options

| Option                        | Type                  | Default              | Effect                                    |
| ----------------------------- | --------------------- | -------------------- | ----------------------------------------- |
| `name`                        | `str`                 | function name        | Override tool name                        |
| `description`                 | `str`                 | docstring first line | Override description                      |
| `parameters`                  | `list[ToolParameter]` | auto-inferred        | Override all parameters                   |
| `observe`                     | `bool`                | `False`              | Inject observation turn after execution   |
| `requires_confirmation`       | `bool`                | `False`              | Suspend for user approval                 |
| `history_arguments_optimizer` | callable              | `None`               | Compact arguments in conversation history |
| `history_result_optimizer`    | callable              | `None`               | Compact results in conversation history   |

**Gotcha**: `@tool` returns a `Tool` object. If you need the raw function elsewhere, keep a reference before decorating.

## `observe` vs `requires_confirmation`

### `observe=True`

After the tool executes, Broth makes an **extra LLM call** with the tool result injected as context. The LLM gets to "observe" the outcome and decide next steps before the user sees intermediate results.

Use for: tools whose output needs interpretation (code analysis, search results).

### `requires_confirmation=True`

Before execution, Broth **suspends** and emits a `ToolConfirmationRequest` to the UI. Execution resumes only after a `ToolConfirmationResponse` arrives.

Use for: destructive operations (file deletion, database writes, irreversible actions).

### Confirmation Flow

```text
1. LLM requests tool call with requires_confirmation=True
2. Broth builds ToolConfirmationRequest(tool_name, call_id, arguments)
3. Framework sends request to UI via callback
4. UI shows confirmation dialog to user
5. User approves/rejects (optionally modifies arguments)
6. ToolConfirmationResponse(approved, modified_arguments, reason) sent back
7. If approved → tool executes (with modified args if any)
   If rejected → tool skipped, error message sent to LLM
```

```python
from unchain.tools import ToolConfirmationRequest, ToolConfirmationResponse

# Request (built by framework)
req = ToolConfirmationRequest(
    tool_name="write_file",
    call_id="call_abc123",
    arguments={"path": "important.py", "content": "updated text"},
    description="Write important.py",
)

# Response (from UI)
resp = ToolConfirmationResponse(approved=True)
# or: ToolConfirmationResponse(approved=False, reason="Not safe")
# or: ToolConfirmationResponse(approved=True, modified_arguments={"path": "temp.py"})
```

## Toolkit Registration Patterns

### Adding tools to a Toolkit

```python
from unchain.tools import Toolkit

tk = Toolkit()

# Register a callable (auto-wraps in Tool)
tk.register(my_function)

# Register with metadata overrides
tk.register(my_function, name="custom_name", observe=True)

# Register a pre-built Tool object
tk.register(some_tool_object)

# Register many at once
tk.register_many(func_a, func_b, func_c)
```

### Executing tools

```python
result = tk.execute("tool_name", {"arg1": "value"})
# → {"result": ...} or {"error": ...}
```

### Merging toolkits

Tools from multiple toolkits are merged into one for the runtime:

```python
agent = Agent(
    tools=[
        WorkspaceToolkit(workspace_root="."),
        TerminalToolkit(workspace_root="."),
        my_custom_tool,          # A single Tool or callable
    ],
)
# Agent merges all into a single Toolkit for Broth
```

**Conflict detection**: If two toolkits register tools with the same name, the catalog system will reject simultaneous activation. Within a single toolkit, later registrations overwrite earlier ones.

## Toolkit Discovery (3 Sources)

The `ToolkitRegistry` discovers toolkits from three sources:

| Source      | Location                                        | When                                    |
| ----------- | ----------------------------------------------- | --------------------------------------- |
| **Builtin** | `src/unchain/toolkits/builtin/*/toolkit.toml`      | Always (unless `include_builtin=False`) |
| **Local**   | Directories in `ToolRegistryConfig.local_roots` | When configured                         |
| **Plugins** | `entry_points(group="unchain.toolkits")`           | When installed packages declare them    |

### Plugin entry point convention

```toml
# In the plugin's pyproject.toml
[project.entry-points."unchain.toolkits"]
my_plugin = "my_package.toolkit:MyToolkit"
```

The entry point **name must match** the `toolkit.id` in the plugin's `toolkit.toml`.

## Dynamic Catalog (Runtime Activation)

The `ToolkitCatalogRuntime` lets agents activate/deactivate toolkits at runtime:

```python
agent.enable_toolkit_catalog(
    managed_toolkit_ids=["workspace", "terminal", "external_api"],
    always_active_toolkit_ids=["workspace"],
)
```

This injects 5 catalog management tools into the agent:

| Catalog Tool          | Purpose                                      |
| --------------------- | -------------------------------------------- |
| `toolkit_list`        | List available (non-hidden) toolkits         |
| `toolkit_describe`    | Get metadata + readme for a toolkit          |
| `toolkit_activate`    | Activate a toolkit (with conflict detection) |
| `toolkit_deactivate`  | Deactivate (except always-active)            |
| `toolkit_list_active` | List currently active toolkit IDs            |

**State tokens**: Catalog state is preserved across run suspensions via `build_continuation_state()` / `extract_toolkit_catalog_token()`, allowing resumption without losing activation state.

## History Payload Optimization

For tools that produce large outputs (file reads, API responses), register optimizers to shrink the conversation history:

```python
self.register(
    self.read_files,
    history_arguments_optimizer=self._compact_args,
    history_result_optimizer=self._compact_result,
)

def _compact_result(self, result: dict) -> dict:
    content = result.get("content", "")
    if len(content) > 500:
        return {**result, "content": f"[{len(content)} chars, see tool output above]"}
    return result
```

The optimizer receives the original dict and must return a (possibly smaller) dict. It is applied **only to historical turns**, not the current one — the LLM always sees full results on the current turn.

## Common Gotchas

1. **`@tool` returns a `Tool`, not a function** — calling `greet("Alice")` on a decorated function goes through `Tool.execute()`, not the raw function.

2. **`self` is auto-skipped** — don't include it in `ToolParameter` lists or docstring params.

3. **Non-dict returns are wrapped** — `return "hello"` becomes `{"result": "hello"}`. Always return explicit dicts.

4. **Tool name conflicts block activation** — the catalog rejects activating two toolkits that share a tool name.

5. **`observe` + `requires_confirmation` on the same tool** — works, but the confirmation happens first, then the observation happens after execution.

6. **Parameter default values** — parameters with defaults become optional in the JSON schema. Parameters without defaults are required.

## Related Skills

- [creating-builtin-toolkits.md](creating-builtin-toolkits.md) — End-to-end toolkit creation guide
- [architecture-overview.md](architecture-overview.md) — Where the tool system fits
- [runtime-engine.md](runtime-engine.md) — How Broth executes tool calls
