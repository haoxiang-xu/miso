# Toolkit Implementations Reference

Builtin and MCP toolkit implementations, including workspace-safe base helpers.

| Metric | Value |
| --- | --- |
| Classes | 4 |
| Dataclasses | 0 |
| Protocols | 0 |
| Internal-only types | 0 |

## Coverage map

| Class | Source | Exposure | Kind |
| --- | --- | --- | --- |
| `BuiltinToolkit` | `src/unchain/toolkits/base.py:10` | subpackage | class |
| `CoreToolkit` | `src/unchain/toolkits/builtin/core/core.py:30` | subpackage | class |
| `ExternalAPIToolkit` | `src/unchain/toolkits/builtin/external_api/external_api.py:12` | subpackage | class |
| `MCPToolkit` | `src/unchain/toolkits/mcp.py:62` | subpackage | class |

### `src/unchain/toolkits/base.py`

Base class shared by builtin toolkits.

## BuiltinToolkit

Base toolkit that resolves a root directory and manages execution contexts.

| Item | Details |
| --- | --- |
| Source | `src/unchain/toolkits/base.py:10` |
| Module role | Base class shared by builtin toolkits. |
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

- `CoreToolkit`
- `ExternalAPIToolkit`

### Minimal usage example

```python
obj = BuiltinToolkit(...)
obj.push_execution_context(...)
```

### `src/unchain/toolkits/builtin/core/core.py`

Core builtin toolkit shipping the workspace-aware coding, shell, web fetch, LSP, and structured user-question tools.

## CoreToolkit

Workspace-scoped toolkit registering the nine tools that most coding agents need by default.

| Item | Details |
| --- | --- |
| Source | `src/unchain/toolkits/builtin/core/core.py:30` |
| Module role | Core builtin toolkit for coding, shell, web fetch, LSP, and structured user questions. |
| Inheritance | `BuiltinToolkit` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing. |

### Constructor surface

- `__init__(self, *, workspace_root: str | Path | None=None, workspace_roots: list[str | Path] | None=None)`

`workspace_root` is the single-root convenience form; `workspace_roots` accepts a list when the agent operates over multiple roots. At least one form must resolve to a usable directory; otherwise the toolkit raises `ValueError`.

### Registered tools

All nine tools are registered eagerly during `__init__` and validated against `toolkit.toml`.

| Tool | Signature | Confirmation | Notes |
| --- | --- | --- | --- |
| `read` | `read(path: str, offset: int = 0, limit: int | None = None)` | no | Reads UTF-8 text by absolute path with line-numbered output and optional slicing. Records a freshness snapshot used by `write`/`edit`. |
| `write` | `write(path: str, content: str)` | yes | Creates or fully overwrites a UTF-8 text file. Existing files must be fully read first; aborts on a stale snapshot. |
| `edit` | `edit(path: str, old_string: str, new_string: str, replace_all: bool = False)` | yes | Replaces one unique match (or all matches when `replace_all=True`) inside a previously-read file. |
| `glob` | `glob(pattern: str, ...)` | no | Returns up to 200 paths matching a glob, sorted by most-recently-modified first. |
| `grep` | `grep(pattern: str, globs: list[str] | None = None, mode: str = ...)` | no | Regex search over UTF-8 text with optional glob filters and paginated result modes. |
| `web_fetch` | `web_fetch(url: str, extract: str | None = None)` | yes | Fetches an HTTP(S) page; `extract` toggles raw vs. runtime-configured extraction model. |
| `shell` | `shell(command: str, ...)` | yes (risk-classified) | Runs a shell command, polls a background task, or kills one. Low-risk commands skip confirmation. |
| `lsp` | `lsp(path: str, method: str, ...)` | no | Queries a language server (Python or TS/JS) for `goToDefinition`, `findReferences`, `hover`, `documentSymbol`, `workspaceSymbol`. |
| `ask_user_question` | `ask_user_question(title, question, selection_mode, options, ...)` | n/a | Reserved runtime tool: it suspends the run and is fulfilled by the framework, not by direct execution. |

### Runtime collaborators

- `LSPRuntime` (Python + TS/JS language servers, lazy-started per `workspace_roots`).
- `ShellRuntime` (detects `bash`/`zsh`/`sh`, applies risk classification before confirmation).
- `WebFetchService` (caches responses; supports a runtime-configured extraction model).
- `_ReadSnapshot` table per session — write/edit refuse to operate on files that were not fully read or have changed on disk since the last read.

### Lifecycle and runtime role

- Construction wires the workspace roots, instantiates the runtimes, and calls `_register_tools()`.
- Tool methods run via the standard `Toolkit.execute()` path. Confirmation-gated tools route through the kernel's `ToolExecutionHarness`.
- `shutdown()` (inherited from `Toolkit`) tears down the LSP and shell runtimes.

### Collaboration and related types

- `BuiltinToolkit`
- `ExternalAPIToolkit`
- `MCPToolkit`

### Minimal usage example

```python
from unchain import Agent
from unchain.agent import ToolsModule
from unchain.toolkits import CoreToolkit

agent = Agent(
    name="coder",
    instructions="You are a coding assistant.",
    modules=(ToolsModule(tools=(CoreToolkit(workspace_root="."),)),),
)
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
- `CoreToolkit`

### Minimal usage example

```python
obj = ExternalAPIToolkit(...)
obj.http_get(...)
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
- `CoreToolkit`
- `ExternalAPIToolkit`

### Minimal usage example

```python
obj = MCPToolkit(...)
obj.connect(...)
```
