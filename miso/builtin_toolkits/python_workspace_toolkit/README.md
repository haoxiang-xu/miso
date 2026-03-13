# python_workspace_toolkit

An all-in-one workspace toolkit for the **miso** agent framework.  
Provides **file operations**, **line-level editing**, **directory management**, an **isolated Python runtime**, and a **restricted terminal runtime** — all scoped to a user-specified `workspace_root`.

---

## Quick Start

```python
from miso import Agent, python_workspace_toolkit

a = Agent(
    name="coder",
    tools=[python_workspace_toolkit(workspace_root="/path/to/project")],
)
```

Or attach it to the low-level runtime directly:

```python
from miso import broth as Broth, python_workspace_toolkit

a = Broth()
a.toolkit = python_workspace_toolkit(workspace_root=".")
```

---

## Registered Tools

### File-Level Operations

| Tool | Description |
|------|-------------|
| `read_file` | Read an entire UTF-8 text file (with optional truncation). Returns `total_lines`. |
| `write_file` | Write / overwrite a file, or `append=True` to append. Auto-creates parent dirs. |
| `create_file` | Create a new file. **Errors** if the file already exists (prevents accidental overwrite). |
| `delete_file` | Delete a single file. |
| `copy_file` | Copy a file to another path within the workspace. |
| `move_file` | Move or rename a file within the workspace. |
| `file_exists` | Check whether a path exists and return its type (`file` / `directory` / `None`). |

### Line-Level Editing (1-based line numbers)

| Tool | Description |
|------|-------------|
| `read_lines` | Read lines `[start, end]` from a file. Defaults to reading until EOF. |
| `insert_lines` | Insert new content **before** a given line number. Use `total_lines + 1` to append at end. |
| `replace_lines` | Replace lines `[start, end]` with new content (can change line count). |
| `delete_lines` | Delete lines `[start, end]`. |
| `copy_lines` | Copy lines `[start, end]` and insert them before `to_line` (same file). |
| `move_lines` | Cut lines `[start, end]` and paste them before `to_line` (same file). |
| `search_and_replace` | Find & replace text in a file. Supports regex, case-insensitive, and `max_count`. |

### Directory Operations

| Tool | Description |
|------|-------------|
| `list_directory` | List files and folders. Supports `recursive=True` and `max_entries`. |
| `create_directory` | Create a directory (including parents). |
| `search_text` | Search a regex pattern across workspace files recursively. (`observe=True`) |

### Python Runtime (isolated venv)

| Tool | Description |
|------|-------------|
| `python_runtime_init` | Create an isolated venv at `.miso_python_runtime/`. `reset=True` to rebuild. |
| `python_runtime_install` | `pip install` packages into the venv. (`observe=True`) |
| `python_runtime_run` | Execute a Python code string inside the venv. (`observe=True`) |
| `python_runtime_reset` | Delete the entire venv. |

### Terminal Runtime (restricted shell)

| Tool | Description |
|------|-------------|
| `terminal_exec` | Execute one command (`shell=False`, parsed by `shlex.split`) and return `ok`, `returncode`, `stdout`, `stderr`, `timed_out`, `truncated`. |
| `terminal_session_open` | Start a persistent shell session and return `session_id`. |
| `terminal_session_write` | Write input to a session and collect available output. |
| `terminal_session_close` | Close a session and return final output. |

Strict mode blocks high-risk and network-related commands (for example `sudo`, `shutdown`, `reboot`, `mkfs`, `dd`, `curl`, `wget`, `ssh`, and `rm -rf /`).

> Tools marked `observe=True` have their results reviewed by the observation sub-agent for error checking.

---

## Constructor Parameters

```python
python_workspace_toolkit(
    workspace_root="/path/to/project",   # defaults to cwd
    include_python_runtime=True,         # set False to skip venv tools
    include_terminal_runtime=True,       # set False to skip terminal tools
    terminal_strict_mode=True,           # strict command safety checks
)
```

- **`workspace_root`** — All file paths are resolved relative to this directory and are prevented from escaping it.
- **`include_python_runtime`** — When `False`, only filesystem & editing tools are registered (no venv).
- **`include_terminal_runtime`** — When `False`, terminal tools are not registered.
- **`terminal_strict_mode`** — When `True`, disallowed command patterns are blocked before execution.

---

## Path Safety

All path arguments go through `_resolve_workspace_path()` which:

1. Resolves relative paths against `workspace_root`
2. Resolves symlinks
3. **Rejects** any path that resolves outside `workspace_root` (raises `ValueError`)

---

## Examples

```python
from miso import python_workspace_toolkit

tk = python_workspace_toolkit(workspace_root="/tmp/demo")

# Write a file, then read specific lines
tk.execute("write_file", {"path": "hello.py", "content": "line1\nline2\nline3\nline4\n"})
result = tk.execute("read_lines", {"path": "hello.py", "start": 2, "end": 3})
# result["content"] == "line2\nline3\n"

# Insert before line 2
tk.execute("insert_lines", {"path": "hello.py", "line": 2, "content": "inserted\n"})

# Replace lines 1-2 with new content
tk.execute("replace_lines", {"path": "hello.py", "start": 1, "end": 2, "content": "new_line1\nnew_line2\n"})

# Search and replace across the file
tk.execute("search_and_replace", {"path": "hello.py", "search": "old", "replace": "new"})

# Move lines 3-4 to before line 1
tk.execute("move_lines", {"path": "hello.py", "start": 3, "end": 4, "to_line": 1})

# Run Python code in isolated venv
tk.execute("python_runtime_init", {})
tk.execute("python_runtime_run", {"code": "print('hello from venv')"})

# Execute one terminal command
tk.execute("terminal_exec", {"command": "echo hello terminal"})

# Open/write/close a terminal session
opened = tk.execute("terminal_session_open", {"shell": "/bin/bash"})
sid = opened["session_id"]
tk.execute("terminal_session_write", {"session_id": sid, "input": "echo session-ok\n"})
tk.execute("terminal_session_close", {"session_id": sid})
```
