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
