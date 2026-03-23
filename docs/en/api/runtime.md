# Runtime API Reference

Core execution loop types for provider turns, tool execution, token accounting, and the Broth runtime.

| Metric | Value |
| --- | --- |
| Classes | 5 |
| Dataclasses | 4 |
| Protocols | 0 |
| Internal-only types | 0 |

## Coverage map

| Class | Source | Exposure | Kind |
| --- | --- | --- | --- |
| `ToolCall` | `src/miso/runtime/engine.py:68` | subpackage | dataclass |
| `ProviderTurnResult` | `src/miso/runtime/engine.py:74` | subpackage | dataclass |
| `TokenUsage` | `src/miso/runtime/engine.py:86` | internal | dataclass |
| `ToolExecutionOutcome` | `src/miso/runtime/engine.py:93` | subpackage | dataclass |
| `Broth` | `src/miso/runtime/engine.py:103` | subpackage | class |

### `src/miso/runtime/engine.py`

Provider-facing execution loop and the canonical runtime message/tool execution types.

## ToolCall

Dataclass payload used by provider-facing execution loop and the canonical runtime message/tool execution types.

| Item | Details |
| --- | --- |
| Source | `src/miso/runtime/engine.py:68` |
| Module role | Provider-facing execution loop and the canonical runtime message/tool execution types. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `call_id` | `str` | Required at construction time. |
| `name` | `str` | Required at construction time. |
| `arguments` | `dict[str, Any] | str | None` | Required at construction time. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `ProviderTurnResult`
- `TokenUsage`
- `ToolExecutionOutcome`
- `Broth`

### Minimal usage example

```python
ToolCall(call_id=..., name=..., arguments=...)
```

## ProviderTurnResult

Dataclass payload used by provider-facing execution loop and the canonical runtime message/tool execution types.

| Item | Details |
| --- | --- |
| Source | `src/miso/runtime/engine.py:74` |
| Module role | Provider-facing execution loop and the canonical runtime message/tool execution types. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `assistant_messages` | `list[dict[str, Any]]` | Required at construction time. |
| `tool_calls` | `list[ToolCall]` | Required at construction time. |
| `final_text` | `str` | Default: `''`. |
| `response_id` | `str | None` | Default: `None`. |
| `reasoning_items` | `list[dict[str, Any]] | None` | Default: `None`. |
| `consumed_tokens` | `int` | Default: `0`. |
| `input_tokens` | `int` | Default: `0`. |
| `output_tokens` | `int` | Default: `0`. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `ToolCall`
- `TokenUsage`
- `ToolExecutionOutcome`
- `Broth`

### Minimal usage example

```python
ProviderTurnResult(assistant_messages=..., tool_calls=..., final_text=..., response_id=...)
```

## TokenUsage

Dataclass payload used by provider-facing execution loop and the canonical runtime message/tool execution types.

| Item | Details |
| --- | --- |
| Source | `src/miso/runtime/engine.py:86` |
| Module role | Provider-facing execution loop and the canonical runtime message/tool execution types. |
| Inheritance | `-` |
| Exposure | Not exported; treat as implementation detail. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `consumed_tokens` | `int` | Default: `0`. |
| `input_tokens` | `int` | Default: `0`. |
| `output_tokens` | `int` | Default: `0`. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `ToolCall`
- `ProviderTurnResult`
- `ToolExecutionOutcome`
- `Broth`

### Minimal usage example

```python
TokenUsage(consumed_tokens=..., input_tokens=..., output_tokens=...)
```

## ToolExecutionOutcome

Dataclass payload used by provider-facing execution loop and the canonical runtime message/tool execution types.

| Item | Details |
| --- | --- |
| Source | `src/miso/runtime/engine.py:93` |
| Module role | Provider-facing execution loop and the canonical runtime message/tool execution types. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `result_messages` | `list[dict[str, Any]]` | Required at construction time. |
| `should_observe` | `bool` | Default: `False`. |
| `awaiting_human_input` | `bool` | Default: `False`. |
| `human_input_request` | `HumanInputRequest | None` | Default: `None`. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `ToolCall`
- `ProviderTurnResult`
- `TokenUsage`
- `Broth`

### Minimal usage example

```python
ToolExecutionOutcome(result_messages=..., should_observe=..., awaiting_human_input=..., human_input_request=...)
```

## Broth

Canonical provider/runtime loop that prepares context, executes tools, handles suspension, and returns messages plus a bundle.

| Item | Details |
| --- | --- |
| Source | `src/miso/runtime/engine.py:103` |
| Module role | Provider-facing execution loop and the canonical runtime message/tool execution types. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, provider: str | None=None, model: str | None=None, api_key: str | None=None, memory_manager: MemoryManager | None=None, toolkit_catalog_config: ToolkitCatalogConfig | dict[str, Any] | None=None)`

### Properties

- `@property toolkit`: Return a merged view of all registered toolkits.
- `@property max_context_window_tokens`: Return the context window token limit.

### Public methods

#### `__init__(self, *, provider: str | None=None, model: str | None=None, api_key: str | None=None, memory_manager: MemoryManager | None=None, toolkit_catalog_config: ToolkitCatalogConfig | dict[str, Any] | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/runtime/engine.py:104`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `add_toolkit(self, tk: BaseToolkit)`

Append a toolkit to the agent's toolkit list.

- Category: Method
- Declared at: `src/miso/runtime/engine.py:262`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `remove_toolkit(self, tk: BaseToolkit)`

Remove a toolkit from the agent's toolkit list.

- Category: Method
- Declared at: `src/miso/runtime/engine.py:266`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `run(self, messages, payload: dict[str, Any] | None=None, response_format: ResponseFormat | None=None, callback: Callable[[dict[str, Any]], None] | None=None, verbose: bool=False, max_iterations: int | None=None, previous_response_id: str | None=None, on_tool_confirm: Callable | None=None, on_continuation_request: Callable | None=None, session_id: str | None=None, memory_namespace: str | None=None)`

Public method `run` exposed by `Broth`.

- Category: Method
- Declared at: `src/miso/runtime/engine.py:1737`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `resume_human_input(self, *, conversation: list[dict[str, Any]], continuation: dict[str, Any], response: HumanInputResponse | dict[str, Any], payload: dict[str, Any] | None=None, response_format: ResponseFormat | None=None, callback: Callable[[dict[str, Any]], None] | None=None, verbose: bool=False, on_tool_confirm: Callable | None=None, on_continuation_request: Callable | None=None, session_id: str | None=None, memory_namespace: str | None=None)`

Public method `resume_human_input` exposed by `Broth`.

- Category: Method
- Declared at: `src/miso/runtime/engine.py:1826`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Lifecycle and runtime role

- Initialization loads provider defaults and model capabilities, sets bookkeeping counters, and defers provider SDK imports until execution time.
- `run()` normalizes messages, injects workspace pins, prepares memory context, fetches provider turns, executes tools, runs observations, and commits memory each iteration.
- If a tool requires confirmation or human input, the runtime packages continuation state, returns early, and later resumes through `resume_human_input()`.
- Toolkit catalog runtimes are parked by state token so a resumed run sees the same active/managed toolkit set.

### Collaboration and related types

- `ToolCall`
- `ProviderTurnResult`
- `TokenUsage`
- `ToolExecutionOutcome`

### Minimal usage example

```python
obj = Broth(...)
obj.add_toolkit(...)
```
