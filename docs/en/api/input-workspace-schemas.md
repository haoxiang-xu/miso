# Input, Workspace, and Schema Reference

Structured human input models, workspace pin/syntax objects, and structured output schemas used across the runtime.

| Metric | Value |
| --- | --- |
| Classes | 7 |
| Dataclasses | 6 |
| Protocols | 0 |
| Internal-only types | 0 |

## Coverage map

| Class | Source | Exposure | Kind |
| --- | --- | --- | --- |
| `HumanInputOption` | `src/miso/input/human_input.py:61` | subpackage | dataclass |
| `HumanInputRequest` | `src/miso/input/human_input.py:89` | subpackage | dataclass |
| `HumanInputResponse` | `src/miso/input/human_input.py:225` | subpackage | dataclass |
| `ResponseFormat` | `src/miso/schemas/response.py:7` | subpackage | class |
| `WorkspacePinExecutionContext` | `src/miso/workspace/pins.py:35` | subpackage | dataclass |
| `ParsedSyntaxTree` | `src/miso/workspace/syntax.py:215` | internal | dataclass |
| `DeclarationCandidate` | `src/miso/workspace/syntax.py:228` | internal | dataclass |

### `src/miso/input/human_input.py`

Structured question/response models used by the ask-user flow.

## HumanInputOption

Dataclass payload used by structured question/response models used by the ask-user flow.

| Item | Details |
| --- | --- |
| Source | `src/miso/input/human_input.py:61` |
| Module role | Structured question/response models used by the ask-user flow. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `label` | `str` | Required at construction time. |
| `value` | `str` | Required at construction time. |
| `description` | `str` | Default: `''`. |

### Public methods

#### `from_raw(cls, raw: Any)`

Public method `from_raw` exposed by `HumanInputOption`.

- Category: Method
- Declared at: `src/miso/input/human_input.py:67`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_dict(self)`

Public method `to_dict` exposed by `HumanInputOption`.

- Category: Method
- Declared at: `src/miso/input/human_input.py:80`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `HumanInputRequest`
- `HumanInputResponse`

### Minimal usage example

```python
HumanInputOption(label=..., value=..., description=...)
```

## HumanInputRequest

Dataclass payload used by structured question/response models used by the ask-user flow.

| Item | Details |
| --- | --- |
| Source | `src/miso/input/human_input.py:89` |
| Module role | Structured question/response models used by the ask-user flow. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `request_id` | `str` | Required at construction time. |
| `kind` | `Literal['selector']` | Required at construction time. |
| `title` | `str` | Required at construction time. |
| `question` | `str` | Required at construction time. |
| `selection_mode` | `Literal['single', 'multiple']` | Required at construction time. |
| `options` | `list[HumanInputOption]` | Required at construction time. |
| `allow_other` | `bool` | Default: `False`. |
| `other_label` | `str` | Default: `'Other'`. |
| `other_placeholder` | `str` | Default: `''`. |
| `min_selected` | `int | None` | Default: `None`. |
| `max_selected` | `int | None` | Default: `None`. |

### Public methods

#### `from_tool_arguments(cls, arguments: dict[str, Any] | str | None, *, request_id: str)`

Public method `from_tool_arguments` exposed by `HumanInputRequest`.

- Category: Method
- Declared at: `src/miso/input/human_input.py:103`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `from_dict(cls, raw: Any)`

Public method `from_dict` exposed by `HumanInputRequest`.

- Category: Method
- Declared at: `src/miso/input/human_input.py:180`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_dict(self)`

Public method `to_dict` exposed by `HumanInputRequest`.

- Category: Method
- Declared at: `src/miso/input/human_input.py:202`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `allowed_values(self)`

Public method `allowed_values` exposed by `HumanInputRequest`.

- Category: Method
- Declared at: `src/miso/input/human_input.py:217`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `HumanInputOption`
- `HumanInputResponse`

### Minimal usage example

```python
HumanInputRequest(request_id=..., kind=..., title=..., question=...)
```

## HumanInputResponse

Dataclass payload used by structured question/response models used by the ask-user flow.

| Item | Details |
| --- | --- |
| Source | `src/miso/input/human_input.py:225` |
| Module role | Structured question/response models used by the ask-user flow. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `request_id` | `str` | Required at construction time. |
| `selected_values` | `list[str]` | Required at construction time. |
| `other_text` | `str | None` | Default: `None`. |

### Public methods

#### `from_raw(cls, raw: Any, *, request: HumanInputRequest)`

Public method `from_raw` exposed by `HumanInputResponse`.

- Category: Method
- Declared at: `src/miso/input/human_input.py:231`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_dict(self)`

Public method `to_dict` exposed by `HumanInputResponse`.

- Category: Method
- Declared at: `src/miso/input/human_input.py:295`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_tool_result(self)`

Public method `to_tool_result` exposed by `HumanInputResponse`.

- Category: Method
- Declared at: `src/miso/input/human_input.py:302`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `HumanInputOption`
- `HumanInputRequest`

### Minimal usage example

```python
HumanInputResponse(request_id=..., selected_values=..., other_text=...)
```

### `src/miso/schemas/response.py`

Structured output schema wrapper with provider-specific projections and parsing.

## ResponseFormat

Structured-output schema wrapper that can project the same schema into multiple provider-specific request formats.

| Item | Details |
| --- | --- |
| Source | `src/miso/schemas/response.py:7` |
| Module role | Structured output schema wrapper with provider-specific projections and parsing. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, name: str, schema: dict[str, Any], required: list[str] | None=None, post_processor: Callable[[dict[str, Any]], dict[str, Any]] | None=None)`

### Public methods

#### `__init__(self, name: str, schema: dict[str, Any], required: list[str] | None=None, post_processor: Callable[[dict[str, Any]], dict[str, Any]] | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/schemas/response.py:10`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_openai(self)`

Public method `to_openai` exposed by `ResponseFormat`.

- Category: Method
- Declared at: `src/miso/schemas/response.py:27`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_ollama(self)`

Public method `to_ollama` exposed by `ResponseFormat`.

- Category: Method
- Declared at: `src/miso/schemas/response.py:34`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_anthropic(self)`

Return a system-prompt suffix that instructs Claude to output JSON.

- Category: Method
- Declared at: `src/miso/schemas/response.py:38`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_gemini(self)`

Return Gemini-compatible structured output config.

- Category: Method
- Declared at: `src/miso/schemas/response.py:46`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.
- Notes: Returns a dict with ``response_mime_type`` and ``response_schema``
suitable for passing into Gemini's ``generation_config``.

#### `parse(self, content: str | dict[str, Any])`

Public method `parse` exposed by `ResponseFormat`.

- Category: Method
- Declared at: `src/miso/schemas/response.py:57`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `HumanInputOption`
- `HumanInputRequest`
- `HumanInputResponse`
- `WorkspacePinExecutionContext`
- `ParsedSyntaxTree`

### Minimal usage example

```python
obj = ResponseFormat(...)
obj.to_openai(...)
```

### `src/miso/workspace/pins.py`

Workspace pin execution context and relocation helpers for pinned file context.

## WorkspacePinExecutionContext

Dataclass payload used by workspace pin execution context and relocation helpers for pinned file context.

| Item | Details |
| --- | --- |
| Source | `src/miso/workspace/pins.py:35` |
| Module role | Workspace pin execution context and relocation helpers for pinned file context. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `session_id` | `str` | Required at construction time. |
| `session_store` | `SessionStore` | Required at construction time. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `HumanInputOption`
- `HumanInputRequest`
- `HumanInputResponse`
- `ResponseFormat`
- `ParsedSyntaxTree`

### Minimal usage example

```python
WorkspacePinExecutionContext(session_id=..., session_store=...)
```

### `src/miso/workspace/syntax.py`

Syntax-tree parsing output types shared by the workspace toolkit.

## ParsedSyntaxTree

Dataclass payload used by syntax-tree parsing output types shared by the workspace toolkit.

| Item | Details |
| --- | --- |
| Source | `src/miso/workspace/syntax.py:215` |
| Module role | Syntax-tree parsing output types shared by the workspace toolkit. |
| Inheritance | `-` |
| Exposure | Not exported; treat as implementation detail. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `language` | `str` | Required at construction time. |
| `source_bytes` | `bytes` | Required at construction time. |
| `tree` | `Any` | Required at construction time. |
| `parser` | `str` | Default: `PARSER_NAME`. |
| `tree_kind` | `str` | Default: `TREE_KIND`. |

### Properties

- `@property root_node`: Public property accessor.

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `DeclarationCandidate`

### Minimal usage example

```python
ParsedSyntaxTree(language=..., source_bytes=..., tree=..., parser=...)
```

## DeclarationCandidate

Dataclass payload used by syntax-tree parsing output types shared by the workspace toolkit.

| Item | Details |
| --- | --- |
| Source | `src/miso/workspace/syntax.py:228` |
| Module role | Syntax-tree parsing output types shared by the workspace toolkit. |
| Inheritance | `-` |
| Exposure | Not exported; treat as implementation detail. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `language` | `str` | Required at construction time. |
| `type` | `str` | Required at construction time. |
| `name` | `str` | Required at construction time. |
| `start_line` | `int` | Required at construction time. |
| `end_line` | `int` | Required at construction time. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `ParsedSyntaxTree`

### Minimal usage example

```python
DeclarationCandidate(language=..., type=..., name=..., start_line=...)
```
