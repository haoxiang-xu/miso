# Creating Builtin Toolkits

Canonical English skill chapter for the `creating-builtin-toolkits` topic.

## Role and boundaries

This chapter is the implementation guide for adding or maintaining builtin toolkits that ship with miso.

## Dependency view

- Builtin toolkits build on `Toolkit` or `BuiltinToolkit`.
- Manifests are validated by `ToolkitRegistry`.
- Runtime safety depends on correct workspace path resolution, manifest metadata, and shutdown behavior.

## Core objects

- `BuiltinToolkit`
- `Toolkit`
- `ToolkitRegistry`
- `WorkspaceToolkit`
- `TerminalToolkit`
- `ExternalAPIToolkit`
- `AskUserToolkit`

## Execution and state flow

- Create the directory, implementation, manifest, and readme.
- Register every tool and ensure manifest/runtime parity.
- Export the toolkit from package entry points and validate discovery.
- Keep package README short and point to the canonical docs tree.

## Configuration surface

- Manifest fields such as `id`, `factory`, `readme`, and `[[tools]]` entries.
- Per-tool flags like `observe` and `requires_confirmation`.
- Workspace roots, icon assets, and registry discovery roots.

## Extension points

- Add new builtin toolkits.
- Attach history optimizers or custom parameter metadata.
- Use `BuiltinToolkit` when filesystem safety is required.

## Common gotchas

- Tool method names and manifest entries must match exactly.
- Workspace-aware toolkits must resolve paths safely.
- Factories must be zero-arg importable callables for discovery.

## Related class references

- [Toolkit Implementations](../api/toolkits.md)
- [Tool System API](../api/tools.md)

## Source entry points

- `src/miso/toolkits/base.py`
- `src/miso/toolkits/builtin/`
- `src/miso/tools/registry.py`

## Detailed legacy reference

The original repository skill note is preserved below for continuity and extra examples. The canonical copy now lives in this docs tree.

> Step-by-step guide for adding a new builtin toolkit, with every common pitfall called out.

## Directory Structure

Every builtin toolkit lives under `src/miso/toolkits/builtin/<toolkit_id>/`:

```text
src/miso/toolkits/builtin/
└── my_toolkit/
    ├── __init__.py       # Re-exports the toolkit class
    ├── my_toolkit.py     # Toolkit implementation
    ├── toolkit.toml      # Manifest (REQUIRED)
    ├── README.md         # Toolkit-level docs (REQUIRED by manifest)
    └── icon.svg          # Optional custom icon
```

## Step 1: Write the Manifest (`toolkit.toml`)

The manifest declares metadata and enumerates every tool the toolkit exposes.

```toml
[toolkit]
id = "my_toolkit"                              # REQUIRED — unique ID across all sources
name = "My Toolkit"                            # REQUIRED — display name
description = "What this toolkit does."        # REQUIRED
factory = "miso.toolkits.builtin.my_toolkit:MyToolkit"  # REQUIRED — no-arg callable → Toolkit
version = "1.0.0"                              # optional
readme = "README.md"                           # REQUIRED — relative to this file
icon = "folder"                                # builtin icon name OR path to .svg/.png
color = "#eff6ff"                              # REQUIRED if icon is a builtin name
backgroundcolor = "#2563eb"                    # REQUIRED if icon is a builtin name
tags = ["builtin", "my-toolkit"]               # optional

[display]
category = "builtin"                           # optional — UI grouping
order = 50                                     # optional — lower sorts first
hidden = false                                 # optional

[compat]
python = ">=3.9"                               # optional
miso = ">=0"                                   # optional

[[tools]]
name = "do_something"                          # REQUIRED — must match Python method name exactly
title = "Do Something"                         # optional — defaults to name
description = "Explain what this tool does."   # REQUIRED
observe = false                                # optional — inject observation after execution
requires_confirmation = false                  # optional — block until user approves

[[tools]]
name = "do_another_thing"
title = "Do Another Thing"
description = "Explain this one too."
```

### Validation Rules (enforced by `ToolkitRegistry`)

| Rule                                                                         | What Happens If Violated |
| ---------------------------------------------------------------------------- | ------------------------ |
| Every `[[tools]].name` must match a registered method in the runtime toolkit | Discovery error          |
| No registered runtime tools may be missing from `[[tools]]`                  | Discovery error          |
| `observe` and `requires_confirmation` in TOML must match the `Tool` object   | Discovery error          |
| `toolkit.id` must be unique across builtin + local + plugins                 | Discovery error          |
| Icon asset paths must stay within the toolkit directory                      | Security error           |
| Factory must be a no-arg callable returning a `Toolkit` instance             | Instantiation error      |

## Step 2: Choose Your Base Class

### `BuiltinToolkit` — For toolkits that touch the filesystem

```python
from miso.toolkits import BuiltinToolkit

class MyToolkit(BuiltinToolkit):
    def __init__(self, *, workspace_root: str | Path | None = None):
        super().__init__(workspace_root=workspace_root)
        # Register tools here
```

Provides:

- `self.workspace_root` — resolved `Path` (defaults to cwd)
- `self._resolve_workspace_path(path)` — **mandatory** path safety check
- Execution context stack for session features (pins)

### `Toolkit` — For toolkits that don't need a workspace

```python
from miso.tools import Toolkit

class MyToolkit(Toolkit):
    def __init__(self):
        super().__init__()
        # Register tools here
```

Use this when your toolkit has no filesystem dependency (e.g., `AskUserToolkit`).

## Step 3: Register Tools

### Pattern A: `register_many` (most common)

```python
def __init__(self, *, workspace_root=None):
    super().__init__(workspace_root=workspace_root)
    self._register_tools()

def _register_tools(self) -> None:
    self.register_many(
        self.do_something,
        self.do_another_thing,
    )
```

### Pattern B: `register` with metadata overrides

Use when you need history optimizers or need to override auto-inferred metadata:

```python
def _register_tools(self) -> None:
    self.register(
        self.read_files,
        history_arguments_optimizer=self._compact_read_args,
        history_result_optimizer=self._compact_read_result,
    )
```

### Pattern C: External tool definition (rare)

For reserved runtime tools like `ask_user_question` where the schema comes from elsewhere:

```python
reserved = build_ask_user_question_tool()
self.register(
    self.ask_user_question,
    name=reserved.name,
    description=reserved.description,
    parameters=reserved.parameters,
)
```

## Step 4: Implement Tool Methods

### Method Signature Rules

```python
def do_something(self, path: str, max_chars: int = 20000) -> dict[str, Any]:
    """Short description of the tool (becomes the tool description).

    :param path: File path relative to workspace root.
    :param max_chars: Maximum characters to return.
    """
    target = self._resolve_workspace_path(path)   # ← ALWAYS validate paths
    # ... implementation ...
    return {"content": "...", "truncated": False}
```

- **`self` is auto-skipped** during parameter inference.
- **Type hints → JSON schema types**: `str→string`, `int→integer`, `float→number`, `bool→boolean`, `list[T]→array`, `dict→object`.
- **Default values** make parameters optional in the schema.
- **Docstring first line** → tool description (if not overridden in `register()`).
- **`:param name:` lines** → parameter descriptions.

### Return Value Convention

Always return `dict[str, Any]`:

```python
# ✅ Success — include relevant data
return {"path": str(target), "content": content, "truncated": False}

# ✅ Error — use "error" key
return {"error": f"file not found: {target}"}

# ❌ Avoid non-dict returns (they get wrapped in {"result": value} automatically,
#    but explicit dicts are clearer for the LLM)
return "some string"
```

**Note**: There is no enforced `"ok"` key convention project-wide. Some toolkits use `{"ok": True, ...}` (ExternalAPI), others don't (Workspace). Pick one pattern per toolkit and stay consistent within it.

### Error Handling Pattern

```python
def http_get(self, url: str, ...) -> dict[str, Any]:
    try:
        # ... make request ...
        return {"ok": True, "status_code": 200, "body": body}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

Tool exceptions are also caught by `Tool.execute()` and wrapped in `{"error": str(exc), "tool": name}`, but explicit error dicts give better context to the LLM.

## Step 5: Wire Up Exports

### `my_toolkit/__init__.py`

```python
from .my_toolkit import MyToolkit

__all__ = ["MyToolkit"]
```

### `builtin/__init__.py` — Add to existing exports

```python
from .my_toolkit import MyToolkit  # Add this line
```

### `toolkits/__init__.py` — Add to package exports

```python
from .builtin import MyToolkit  # Add this line
```

This ensures `from miso.toolkits import MyToolkit` works.

## Step 6: Write the README

Keep it brief — the toolkit.toml holds the machine-readable metadata.

````markdown
# My Toolkit

`MyToolkit` does X.

## Usage

\```python
from miso.toolkits import MyToolkit

tk = MyToolkit(workspace_root=".")
\```

## Included Tools

- `do_something`
- `do_another_thing`

## Design Constraints

- Only operates within `workspace_root`
- Does not do Y — combine with Z toolkit for that
````

## Common Mistakes

### 1. Forgetting `_resolve_workspace_path()`

```python
# ❌ Path traversal vulnerability — user can escape workspace
target = self.workspace_root / path

# ✅ Safe — resolves symlinks, blocks escapes
target = self._resolve_workspace_path(path)
```

This is a **security requirement**, not a suggestion. Every path argument from the LLM must go through this method.

### 2. `[[tools]]` name doesn't match method name

```toml
[[tools]]
name = "doSomething"   # ❌ camelCase
```

```python
def do_something(self, ...):  # ✅ snake_case
```

The `name` in TOML must be the **exact Python method name**.

### 3. Missing `[[tools]]` entry for a registered method

Every tool registered at runtime must have a corresponding `[[tools]]` section in `toolkit.toml`. The registry validates completeness in both directions.

### 4. Factory requires arguments

```toml
factory = "my_module:MyToolkit"
```

The factory is called with **no arguments** during discovery/instantiation. If your `__init__` requires `workspace_root`, the framework handles it separately — the factory must work as a no-arg call. Use `workspace_root=None` with a default to `os.getcwd()`.

### 5. Forgetting `shutdown()`

If your toolkit owns resources (sessions, connections, temp files), override `shutdown()`:

```python
def shutdown(self) -> None:
    self._runtime.close_all()
```

The framework calls `shutdown()` during cleanup.

### 6. History optimizer not returning compact form

History optimizers must reduce payload size. If your tool returns large content (file reads, API responses), implement compaction:

```python
self.register(
    self.read_files,
    history_result_optimizer=self._compact_result,
)

def _compact_result(self, result: dict) -> dict:
    """Replace large content with a summary for conversation history."""
    if len(result.get("content", "")) > 500:
        return {**result, "content": f"[{len(result['content'])} chars, truncated in history]"}
    return result
```

## Checklist

- [ ] `toolkit.toml` has all required fields (`id`, `name`, `description`, `factory`, `readme`)
- [ ] Every `[[tools]].name` matches a Python method exactly
- [ ] Every registered method has a `[[tools]]` entry
- [ ] `observe` and `requires_confirmation` match between TOML and code
- [ ] All file paths go through `_resolve_workspace_path()`
- [ ] Tool methods return `dict[str, Any]`
- [ ] `__init__.py` export chain is complete (toolkit → builtin → toolkits)
- [ ] README.md exists and is referenced in `toolkit.toml`
- [ ] `shutdown()` is implemented if toolkit owns resources
- [ ] Icon assets stay within the toolkit directory

## Related Skills

- [architecture-overview.md](architecture-overview.md) — Where toolkits fit in the system
- [tool-system-patterns.md](tool-system-patterns.md) — Tool definition, decorators, parameter inference
- [testing-conventions.md](testing-conventions.md) — How to test toolkits
