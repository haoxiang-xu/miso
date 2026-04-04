# Toolkit Implementations Reference

Builtin and MCP toolkit implementations, including terminal runtime internals and workspace-safe base helpers.

| Metric | Value |
| --- | --- |
| Classes | 8 |
| Dataclasses | 1 |
| Protocols | 0 |
| Internal-only types | 2 |

## Coverage map

| Class | Source | Exposure | Kind |
| --- | --- | --- | --- |
| `BuiltinToolkit` | `src/unchain/toolkits/base.py:10` | subpackage | class |
| `AskUserToolkit` | `src/unchain/toolkits/builtin/ask_user/ask_user.py:7` | subpackage | class |
| `ExternalAPIToolkit` | `src/unchain/toolkits/builtin/external_api/external_api.py:12` | subpackage | class |
| `TerminalToolkit` | `src/unchain/toolkits/builtin/terminal/terminal.py:10` | subpackage | class |
| `_TerminalSession` | `src/unchain/toolkits/builtin/terminal_runtime.py:15` | internal | dataclass |
| `_TerminalRuntime` | `src/unchain/toolkits/builtin/terminal_runtime.py:22` | internal | class |
| `WorkspaceToolkit` | `src/unchain/toolkits/builtin/workspace/workspace.py:24` | subpackage | class |
| `MCPToolkit` | `src/unchain/toolkits/mcp.py:62` | subpackage | class |

### `src/unchain/toolkits/base.py`

Workspace-aware base class shared by builtin toolkits.

## BuiltinToolkit

Workspace-aware base toolkit that resolves a root directory and manages workspace pin execution contexts.

| Item | Details |
| --- | --- |
| Source | `src/unchain/toolkits/base.py:10` |
| Module role | Workspace-aware base class shared by builtin toolkits. |
| Inheritance | `Toolkit` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, workspace_root: str | Path | None=None)`

### Properties

- `@property current_execution_context`: Public property accessor.

### Public methods

#### `__init__(self, *, workspace_root: str | Path | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/toolkits/base.py:23`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `push_execution_context(self, context: WorkspacePinExecutionContext)`

Public method `push_execution_context` exposed by `BuiltinToolkit`.

- Category: Method
- Declared at: `src/unchain/toolkits/base.py:45`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `pop_execution_context(self)`

Public method `pop_execution_context` exposed by `BuiltinToolkit`.

- Category: Method
- Declared at: `src/unchain/toolkits/base.py:48`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `AskUserToolkit`
- `ExternalAPIToolkit`
- `TerminalToolkit`
- `_TerminalSession`
- `_TerminalRuntime`

### Minimal usage example

```python
obj = BuiltinToolkit(...)
obj.push_execution_context(...)
```

### `src/unchain/toolkits/builtin/ask_user/ask_user.py`

Reserved human-input toolkit that surfaces the ask-user runtime tool.

## AskUserToolkit

Implementation class used by reserved human-input toolkit that surfaces the ask-user runtime tool.

| Item | Details |
| --- | --- |
| Source | `src/unchain/toolkits/builtin/ask_user/ask_user.py:7` |
| Module role | Reserved human-input toolkit that surfaces the ask-user runtime tool. |
| Inheritance | `Toolkit` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self)`

### Public methods

#### `__init__(self)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/toolkits/builtin/ask_user/ask_user.py:10`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `ask_user_question(self, **kwargs)`

Reserved runtime placeholder for structured user input requests.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/ask_user/ask_user.py:20`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `BuiltinToolkit`
- `ExternalAPIToolkit`
- `TerminalToolkit`
- `_TerminalSession`
- `_TerminalRuntime`

### Minimal usage example

```python
obj = AskUserToolkit(...)
obj.ask_user_question(...)
```

### `src/unchain/toolkits/builtin/external_api/external_api.py`

Outbound HTTP toolkit with simple GET/POST helpers.

## ExternalAPIToolkit

Implementation class used by outbound http toolkit with simple get/post helpers.

| Item | Details |
| --- | --- |
| Source | `src/unchain/toolkits/builtin/external_api/external_api.py:12` |
| Module role | Outbound HTTP toolkit with simple GET/POST helpers. |
| Inheritance | `BuiltinToolkit` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, workspace_root: str | Path | None=None)`

### Public methods

#### `__init__(self, *, workspace_root: str | Path | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/toolkits/builtin/external_api/external_api.py:15`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `http_get(self, url: str, headers: dict[str, str] | None=None, timeout_seconds: int=30, max_response_chars: int=50000)`

Send a GET request to an external API endpoint.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/external_api/external_api.py:29`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.
- Notes: :param url: Full URL to send the GET request to.
:param headers: Optional dictionary of HTTP headers to include.
:param timeout_seconds: Maximum seconds to wait for response.
:param max_response_chars: Maximum response body chars to return.

#### `http_post(self, url: str, body: str | dict[str, Any], headers: dict[str, str] | None=None, timeout_seconds: int=30, max_response_chars: int=50000)`

Send a POST request to an external API endpoint.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/external_api/external_api.py:90`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.
- Notes: :param url: Full URL to send the POST request to.
:param body: Request body as string or dict (dict will be JSON-encoded).
:param headers: Optional dictionary of HTTP headers to include.
:param timeout_seconds: Maximum seconds to wait for response.
:param max_response_chars: Maximum response body chars to return.

### Collaboration and related types

- `BuiltinToolkit`
- `AskUserToolkit`
- `TerminalToolkit`
- `_TerminalSession`
- `_TerminalRuntime`

### Minimal usage example

```python
obj = ExternalAPIToolkit(...)
obj.http_get(...)
```

### `src/unchain/toolkits/builtin/terminal/terminal.py`

User-facing terminal toolkit built on the internal terminal runtime.

## TerminalToolkit

Implementation class used by user-facing terminal toolkit built on the internal terminal runtime.

| Item | Details |
| --- | --- |
| Source | `src/unchain/toolkits/builtin/terminal/terminal.py:10` |
| Module role | User-facing terminal toolkit built on the internal terminal runtime. |
| Inheritance | `BuiltinToolkit` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, workspace_root: str | Path | None=None, terminal_strict_mode: bool=True)`

### Public methods

#### `__init__(self, *, workspace_root: str | Path | None=None, terminal_strict_mode: bool=True)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/toolkits/builtin/terminal/terminal.py:13`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `terminal_exec(self, command: str, cwd: str='.', timeout_seconds: int=30, max_output_chars: int=20000, shell: str='/bin/bash')`

Execute a real shell command string via an allowed shell, preserving normal shell features such as pipes, redirects, quoting, and `&&`.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/terminal/terminal.py:34`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `terminal_session_open(self, shell: str='/bin/bash', cwd: str='.', timeout_seconds: int=3600)`

Open a persistent shell session and return a session id.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/terminal/terminal.py:49`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `terminal_session_write(self, session_id: str, input: str='', yield_time_ms: int=300, max_output_chars: int=20000)`

Write to a session stdin and collect available output.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/terminal/terminal.py:62`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `terminal_session_close(self, session_id: str)`

Close a persistent shell session and return final output.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/terminal/terminal.py:77`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `shutdown(self)`

Public method `shutdown` exposed by `TerminalToolkit`.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/terminal/terminal.py:81`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `BuiltinToolkit`
- `AskUserToolkit`
- `ExternalAPIToolkit`
- `_TerminalSession`
- `_TerminalRuntime`

### Minimal usage example

```python
obj = TerminalToolkit(...)
obj.terminal_exec(...)
```

### `src/unchain/toolkits/builtin/terminal_runtime.py`

Internal terminal session/runtime implementation used by TerminalToolkit.

## _TerminalSession

Dataclass payload used by internal terminal session/runtime implementation used by terminaltoolkit.

| Item | Details |
| --- | --- |
| Source | `src/unchain/toolkits/builtin/terminal_runtime.py:15` |
| Module role | Internal terminal session/runtime implementation used by TerminalToolkit. |
| Inheritance | `-` |
| Exposure | Not exported; treat as implementation detail. |
| Kind | Dataclass; internal implementation. |

### Internal implementation note

Owned by `_TerminalRuntime` session bookkeeping.

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `process` | `subprocess.Popen[bytes]` | Required at construction time. |
| `cwd` | `Path` | Required at construction time. |
| `opened_at` | `float` | Required at construction time. |
| `timeout_seconds` | `int` | Required at construction time. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `_TerminalRuntime`

### Minimal usage example

```python
_TerminalSession(process=..., cwd=..., opened_at=..., timeout_seconds=...)
```

## _TerminalRuntime

Internal helper used by internal terminal session/runtime implementation used by terminaltoolkit. Not intended as a stable external surface.

| Item | Details |
| --- | --- |
| Source | `src/unchain/toolkits/builtin/terminal_runtime.py:22` |
| Module role | Internal terminal session/runtime implementation used by TerminalToolkit. |
| Inheritance | `-` |
| Exposure | Not exported; treat as implementation detail. |
| Kind | Class; internal implementation. |

### Internal implementation note

Owned by `TerminalToolkit` as the stateful terminal backend.

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, workspace_root: Path, strict_mode: bool=True)`

### Public methods

#### `__init__(self, workspace_root: Path, strict_mode: bool=True)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/toolkits/builtin/terminal_runtime.py:45`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `execute(self, command: str, cwd: str='.', timeout_seconds: int=30, max_output_chars: int=20000)`

Public method `execute` exposed by `_TerminalRuntime`.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/terminal_runtime.py:158`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `open_session(self, shell: str='/bin/bash', cwd: str='.', timeout_seconds: int=3600)`

Public method `open_session` exposed by `_TerminalRuntime`.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/terminal_runtime.py:275`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `write_session(self, session_id: str, input: str='', yield_time_ms: int=300, max_output_chars: int=20000)`

Public method `write_session` exposed by `_TerminalRuntime`.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/terminal_runtime.py:349`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `close_session(self, session_id: str)`

Public method `close_session` exposed by `_TerminalRuntime`.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/terminal_runtime.py:415`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `close_all_sessions(self)`

Public method `close_all_sessions` exposed by `_TerminalRuntime`.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/terminal_runtime.py:446`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `_TerminalSession`

### Minimal usage example

```python
# See `src/unchain/toolkits/builtin/terminal_runtime.py` for the owning call flow.

```

### `src/unchain/toolkits/builtin/workspace/workspace.py`

Workspace file, line-edit, search, directory, AST, and pin-management toolkit.

## WorkspaceToolkit

Builtin workspace toolkit covering file IO, line edits, search, directory listing, AST reads, and pin-context management.

| Item | Details |
| --- | --- |
| Source | `src/unchain/toolkits/builtin/workspace/workspace.py:24` |
| Module role | Workspace file, line-edit, search, directory, AST, and pin-management toolkit. |
| Inheritance | `BuiltinToolkit` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, workspace_root: str | Path | None=None)`

### Public methods

#### `__init__(self, *, workspace_root: str | Path | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/toolkits/builtin/workspace/workspace.py:27`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `read_files(self, paths: list[str], max_chars_per_file: int=20000, max_total_chars: int=50000, ast_threshold: int=256)`

Read multiple UTF-8 text files from workspace.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/workspace/workspace.py:377`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.
- Notes: :param paths: File paths relative or absolute inside workspace.
:param max_chars_per_file: Truncate each file after this many characters.
:param max_total_chars: Stop once the combined returned content reaches this many characters.
:param ast_threshold: If greater than zero, large supported source files may return AST output instead of raw text.

#### `write_file(self, path: str, content: str, append: bool=False)`

Write UTF-8 text file into workspace (overwrite or append).

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/workspace/workspace.py:464`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.
- Notes: :param path: Relative or absolute path inside workspace.
:param content: Text content to write.
:param append: If True, append to existing content instead of overwriting.

#### `list_directories(self, paths: list[str], recursive: bool=False, max_entries_per_directory: int=200, max_total_entries: int=500)`

List multiple workspace directories in one call.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/workspace/workspace.py:564`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.
- Notes: :param paths: Directory paths relative or absolute inside workspace.
:param recursive: If True, list descendants recursively for each directory.
:param max_entries_per_directory: Maximum entries to return per directory.
:param max_total_entries: Stop once the combined returned entries reach this many items.

#### `search_text(self, pattern: str, path: str='.', max_results: int=100, case_sensitive: bool=False, file_glob: str | None=None, context_lines: int=0)`

Search text pattern across workspace files.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/workspace/workspace.py:642`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.
- Notes: :param pattern: Regex pattern to search for.
:param path: Directory or file path to search within.
:param max_results: Maximum number of matches to return.
:param case_sensitive: Whether the search is case-sensitive.
:param file_glob: Optional filename filter applied while scanning files.
:param context_lines: Optional number of lines of surrounding context to include with each match.

#### `read_lines(self, path: str, start: int=1, end: int | None=None)`

Read a range of lines from a file (1-based, inclusive).

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/workspace/workspace.py:799`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.
- Notes: :param path: File path relative to workspace root.
:param start: First line number to read (1-based).
:param end: Last line number to read (inclusive). Defaults to end of file.

#### `insert_lines(self, path: str, line: int, content: str)`

Insert text before a given line number (1-based).

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/workspace/workspace.py:833`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.
- Notes: :param path: File path relative to workspace root.
:param line: Line number to insert before (1-based). Use total_lines+1 to append.
:param content: Text content to insert (will be split into lines).

#### `replace_lines(self, path: str, start: int, end: int, content: str)`

Replace a range of lines [start, end] (1-based, inclusive) with new content.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/workspace/workspace.py:865`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.
- Notes: :param path: File path relative to workspace root.
:param start: First line to replace (1-based).
:param end: Last line to replace (inclusive).
:param content: Replacement text (can be any number of lines).

#### `delete_lines(self, path: str, start: int, end: int)`

Delete a range of lines [start, end] (1-based, inclusive).

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/workspace/workspace.py:899`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.
- Notes: :param path: File path relative to workspace root.
:param start: First line to delete (1-based).
:param end: Last line to delete (inclusive).

#### `pin_file_context(self, path: str, start: int | None=None, end: int | None=None, start_with: str | None=None, end_with: str | None=None, reason: str | None=None)`

Pin a file or line range into the current session.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/workspace/workspace.py:1064`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `unpin_file_context(self, pin_id: str | None=None, path: str | None=None, start: int | None=None, end: int | None=None, all: bool=False)`

Remove one or more pinned file contexts from the current session.

- Category: Method
- Declared at: `src/unchain/toolkits/builtin/workspace/workspace.py:1155`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Lifecycle and runtime role

- Construction registers a lean set of structured file, directory, line-edit, search, AST-upgraded read, and pin-management tools against the workspace root.
- Each workspace operation resolves paths through the base safety helpers so actions stay inside the workspace root.
- Pin operations read/write session-scoped pin state through the execution context injected by the runtime.
- The class remains a stateless toolkit from the caller perspective; stateful behavior lives in the session store and file system.

### Collaboration and related types

- `BuiltinToolkit`
- `AskUserToolkit`
- `ExternalAPIToolkit`
- `TerminalToolkit`
- `_TerminalSession`

### Minimal usage example

```python
obj = WorkspaceToolkit(...)
obj.read_files(...)
```

### `src/unchain/toolkits/mcp.py`

MCP bridge that exposes remote server tools through the local toolkit abstraction.

## MCPToolkit

Toolkit bridge that connects to an MCP server and proxies its tools into the local runtime.

| Item | Details |
| --- | --- |
| Source | `src/unchain/toolkits/mcp.py:62` |
| Module role | MCP bridge that exposes remote server tools through the local toolkit abstraction. |
| Inheritance | `Toolkit` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, command: str | None=None, args: list[str] | None=None, env: dict[str, str] | None=None, cwd: str | None=None, url: str | None=None, headers: dict[str, str] | None=None, transport: str | None=None)`

### Properties

- `@property connected`: Public property accessor.

### Public methods

#### `__init__(self, *, command: str | None=None, args: list[str] | None=None, env: dict[str, str] | None=None, cwd: str | None=None, url: str | None=None, headers: dict[str, str] | None=None, transport: str | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/toolkits/mcp.py:83`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `connect(self)`

Connect to the MCP server, discover tools, and populate the toolkit.

- Category: Method
- Declared at: `src/unchain/toolkits/mcp.py:127`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.
- Notes: This method blocks until the session is ready and tools have been
fetched.  It is safe to call ``connect()`` on an already-connected
instance (it will be a no-op).

Returns ``self`` for convenient chaining.

#### `disconnect(self)`

Disconnect from the MCP server and clean up resources.

- Category: Method
- Declared at: `src/unchain/toolkits/mcp.py:156`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `execute(self, function_name: str, arguments: dict[str, Any] | str | None)`

Execute a tool on the MCP server.

- Category: Method
- Declared at: `src/unchain/toolkits/mcp.py:185`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.
- Notes: Falls back to local toolkit execution if the server is disconnected.

### Collaboration and related types

- `BuiltinToolkit`
- `AskUserToolkit`
- `ExternalAPIToolkit`
- `TerminalToolkit`
- `_TerminalSession`

### Minimal usage example

```python
obj = MCPToolkit(...)
obj.connect(...)
```
