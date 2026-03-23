# Tool System API Reference

Tool primitives, toolkit containers, catalog runtime, and discovery/descriptor types that define the framework tool layer.

| Metric | Value |
| --- | --- |
| Classes | 14 |
| Dataclasses | 10 |
| Protocols | 0 |
| Internal-only types | 0 |

## Coverage map

| Class | Source | Exposure | Kind |
| --- | --- | --- | --- |
| `ToolkitCatalogConfig` | `src/miso/tools/catalog.py:34` | subpackage | dataclass |
| `ToolkitCatalogRuntime` | `src/miso/tools/catalog.py:76` | subpackage | class |
| `ToolParameter` | `src/miso/tools/models.py:155` | subpackage | dataclass |
| `ToolHistoryOptimizationContext` | `src/miso/tools/models.py:178` | subpackage | dataclass |
| `NormalizedToolHistoryRecord` | `src/miso/tools/models.py:191` | subpackage | dataclass |
| `ToolConfirmationRequest` | `src/miso/tools/models.py:209` | subpackage | dataclass |
| `ToolConfirmationResponse` | `src/miso/tools/models.py:233` | subpackage | dataclass |
| `ToolRegistryConfig` | `src/miso/tools/registry.py:192` | subpackage | dataclass |
| `ToolDescriptor` | `src/miso/tools/registry.py:222` | subpackage | dataclass |
| `IconDescriptor` | `src/miso/tools/registry.py:246` | internal | dataclass |
| `ToolkitDescriptor` | `src/miso/tools/registry.py:286` | subpackage | dataclass |
| `ToolkitRegistry` | `src/miso/tools/registry.py:378` | subpackage | class |
| `Tool` | `src/miso/tools/tool.py:16` | subpackage | class |
| `Toolkit` | `src/miso/tools/toolkit.py:9` | subpackage | class |

### `src/miso/tools/catalog.py`

Runtime catalog activation layer that can list, describe, and activate managed toolkits.

## ToolkitCatalogConfig

Dataclass payload used by runtime catalog activation layer that can list, describe, and activate managed toolkits.

| Item | Details |
| --- | --- |
| Source | `src/miso/tools/catalog.py:34` |
| Module role | Runtime catalog activation layer that can list, describe, and activate managed toolkits. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `managed_toolkit_ids` | `tuple[str, ...]` | Required at construction time. |
| `always_active_toolkit_ids` | `tuple[str, ...]` | Required at construction time. |
| `registry` | `ToolRegistryConfig` | Required at construction time. |
| `readme_max_chars` | `int` | Required at construction time. |

### Public methods

#### `__init__(self, *, managed_toolkit_ids: tuple[str, ...] | list[str] | None, always_active_toolkit_ids: tuple[str, ...] | list[str] | None=None, registry: ToolRegistryConfig | dict[str, Any] | None=None, readme_max_chars: int=8000)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/tools/catalog.py:40`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `coerce(cls, value: ToolkitCatalogConfig | dict[str, Any] | None)`

Public method `coerce` exposed by `ToolkitCatalogConfig`.

- Category: Method
- Declared at: `src/miso/tools/catalog.py:66`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolkitCatalogRuntime`

### Minimal usage example

```python
ToolkitCatalogConfig(managed_toolkit_ids=..., always_active_toolkit_ids=..., registry=..., readme_max_chars=...)
```

## ToolkitCatalogRuntime

Runtime-visible toolkit that lists, describes, activates, and deactivates managed toolkits without mutating eager toolkits.

| Item | Details |
| --- | --- |
| Source | `src/miso/tools/catalog.py:76` |
| Module role | Runtime catalog activation layer that can list, describe, and activate managed toolkits. |
| Inheritance | `Toolkit` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, config: ToolkitCatalogConfig, eager_toolkits: list[Toolkit])`

### Public methods

#### `__init__(self, *, config: ToolkitCatalogConfig, eager_toolkits: list[Toolkit])`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/tools/catalog.py:77`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `visible_toolkits(self)`

Public method `visible_toolkits` exposed by `ToolkitCatalogRuntime`.

- Category: Method
- Declared at: `src/miso/tools/catalog.py:147`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `active_toolkit_ids(self)`

Public method `active_toolkit_ids` exposed by `ToolkitCatalogRuntime`.

- Category: Method
- Declared at: `src/miso/tools/catalog.py:150`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `toolkit_list(self)`

Public method `toolkit_list` exposed by `ToolkitCatalogRuntime`.

- Category: Method
- Declared at: `src/miso/tools/catalog.py:219`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `toolkit_describe(self, toolkit_id: str, tool_name: str | None=None)`

Public method `toolkit_describe` exposed by `ToolkitCatalogRuntime`.

- Category: Method
- Declared at: `src/miso/tools/catalog.py:228`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `toolkit_activate(self, toolkit_id: str)`

Public method `toolkit_activate` exposed by `ToolkitCatalogRuntime`.

- Category: Method
- Declared at: `src/miso/tools/catalog.py:259`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `toolkit_deactivate(self, toolkit_id: str)`

Public method `toolkit_deactivate` exposed by `ToolkitCatalogRuntime`.

- Category: Method
- Declared at: `src/miso/tools/catalog.py:262`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `toolkit_list_active(self)`

Public method `toolkit_list_active` exposed by `ToolkitCatalogRuntime`.

- Category: Method
- Declared at: `src/miso/tools/catalog.py:292`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `build_continuation_state(self)`

Public method `build_continuation_state` exposed by `ToolkitCatalogRuntime`.

- Category: Method
- Declared at: `src/miso/tools/catalog.py:303`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `shutdown(self)`

Public method `shutdown` exposed by `ToolkitCatalogRuntime`.

- Category: Method
- Declared at: `src/miso/tools/catalog.py:313`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_summary(self)`

Public method `to_summary` exposed by `ToolkitCatalogRuntime`.

- Category: Method
- Declared at: `src/miso/tools/catalog.py:320`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Lifecycle and runtime role

- Initialization validates the managed toolkit set, registers catalog control tools, and eagerly activates always-on toolkits.
- At runtime, `toolkit_list()` and `toolkit_describe()` expose metadata, while activation/deactivation mutates only the managed active set.
- Continuation helpers serialize a state token so suspended runs can recover the same catalog instance later.

### Collaboration and related types

- `ToolkitCatalogConfig`

### Minimal usage example

```python
obj = ToolkitCatalogRuntime(...)
obj.visible_toolkits(...)
```

### `src/miso/tools/models.py`

Small supporting models for tool parameters, history compaction, and confirmation flow.

## ToolParameter

Dataclass payload used by small supporting models for tool parameters, history compaction, and confirmation flow.

| Item | Details |
| --- | --- |
| Source | `src/miso/tools/models.py:155` |
| Module role | Small supporting models for tool parameters, history compaction, and confirmation flow. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `name` | `str` | Required at construction time. |
| `description` | `str` | Required at construction time. |
| `type_` | `str` | Required at construction time. |
| `required` | `bool` | Default: `False`. |
| `pattern` | `str | None` | Default: `None`. |
| `items` | `dict[str, Any] | None` | Default: `None`. |

### Public methods

#### `to_json(self)`

Public method `to_json` exposed by `ToolParameter`.

- Category: Method
- Declared at: `src/miso/tools/models.py:163`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolHistoryOptimizationContext`
- `NormalizedToolHistoryRecord`
- `ToolConfirmationRequest`
- `ToolConfirmationResponse`

### Minimal usage example

```python
ToolParameter(name=..., description=..., type_=..., required=...)
```

## ToolHistoryOptimizationContext

Dataclass payload used by small supporting models for tool parameters, history compaction, and confirmation flow.

| Item | Details |
| --- | --- |
| Source | `src/miso/tools/models.py:178` |
| Module role | Small supporting models for tool parameters, history compaction, and confirmation flow. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `tool_name` | `str` | Required at construction time. |
| `call_id` | `str` | Required at construction time. |
| `kind` | `str` | Required at construction time. |
| `provider` | `str` | Required at construction time. |
| `session_id` | `str` | Required at construction time. |
| `latest_messages` | `list[dict[str, Any]]` | Required at construction time. |
| `max_chars` | `int` | Required at construction time. |
| `preview_chars` | `int` | Required at construction time. |
| `include_hash` | `bool` | Default: `True`. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `ToolParameter`
- `NormalizedToolHistoryRecord`
- `ToolConfirmationRequest`
- `ToolConfirmationResponse`

### Minimal usage example

```python
ToolHistoryOptimizationContext(tool_name=..., call_id=..., kind=..., provider=...)
```

## NormalizedToolHistoryRecord

Dataclass payload used by small supporting models for tool parameters, history compaction, and confirmation flow.

| Item | Details |
| --- | --- |
| Source | `src/miso/tools/models.py:191` |
| Module role | Small supporting models for tool parameters, history compaction, and confirmation flow. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `tool_name` | `str` | Required at construction time. |
| `call_id` | `str` | Required at construction time. |
| `kind` | `str` | Required at construction time. |
| `payload` | `Any` | Required at construction time. |
| `provider` | `str` | Required at construction time. |
| `message_index` | `int` | Required at construction time. |
| `location_type` | `str` | Required at construction time. |
| `payload_format` | `str` | Required at construction time. |
| `block_index` | `int | None` | Default: `None`. |
| `part_index` | `int | None` | Default: `None`. |
| `field_name` | `str | None` | Default: `None`. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `ToolParameter`
- `ToolHistoryOptimizationContext`
- `ToolConfirmationRequest`
- `ToolConfirmationResponse`

### Minimal usage example

```python
NormalizedToolHistoryRecord(tool_name=..., call_id=..., kind=..., payload=...)
```

## ToolConfirmationRequest

Dataclass payload used by small supporting models for tool parameters, history compaction, and confirmation flow.

| Item | Details |
| --- | --- |
| Source | `src/miso/tools/models.py:209` |
| Module role | Small supporting models for tool parameters, history compaction, and confirmation flow. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `tool_name` | `str` | Required at construction time. |
| `call_id` | `str` | Required at construction time. |
| `arguments` | `dict[str, Any]` | Required at construction time. |
| `description` | `str` | Default: `''`. |
| `interact_type` | `str` | Default: `'confirmation'`. |
| `interact_config` | `dict[str, Any] | list[Any] | None` | Default: `None`. |

### Public methods

#### `to_dict(self)`

Public method `to_dict` exposed by `ToolConfirmationRequest`.

- Category: Method
- Declared at: `src/miso/tools/models.py:217`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolParameter`
- `ToolHistoryOptimizationContext`
- `NormalizedToolHistoryRecord`
- `ToolConfirmationResponse`

### Minimal usage example

```python
ToolConfirmationRequest(tool_name=..., call_id=..., arguments=..., description=...)
```

## ToolConfirmationResponse

Dataclass payload used by small supporting models for tool parameters, history compaction, and confirmation flow.

| Item | Details |
| --- | --- |
| Source | `src/miso/tools/models.py:233` |
| Module role | Small supporting models for tool parameters, history compaction, and confirmation flow. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `approved` | `bool` | Default: `True`. |
| `modified_arguments` | `dict[str, Any] | None` | Default: `None`. |
| `reason` | `str` | Default: `''`. |

### Public methods

#### `from_raw(cls, raw: bool | dict[str, Any] | 'ToolConfirmationResponse')`

Public method `from_raw` exposed by `ToolConfirmationResponse`.

- Category: Method
- Declared at: `src/miso/tools/models.py:239`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolParameter`
- `ToolHistoryOptimizationContext`
- `NormalizedToolHistoryRecord`
- `ToolConfirmationRequest`

### Minimal usage example

```python
ToolConfirmationResponse(approved=..., modified_arguments=..., reason=...)
```

### `src/miso/tools/registry.py`

Manifest discovery, metadata validation, icon resolution, and toolkit instantiation.

## ToolRegistryConfig

Dataclass payload used by manifest discovery, metadata validation, icon resolution, and toolkit instantiation.

| Item | Details |
| --- | --- |
| Source | `src/miso/tools/registry.py:192` |
| Module role | Manifest discovery, metadata validation, icon resolution, and toolkit instantiation. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `local_roots` | `tuple[str, ...]` | Default: `()`. |
| `enabled_plugins` | `tuple[str, ...]` | Default: `()`. |
| `include_builtin` | `bool` | Default: `True`. |
| `validate` | `bool` | Default: `True`. |

### Public methods

#### `__init__(self, local_roots: Sequence[str | Path] | None=None, enabled_plugins: Sequence[str] | None=None, include_builtin: bool=True, validate: bool=True)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/tools/registry.py:198`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `coerce(cls, value: ToolRegistryConfig | dict[str, Any] | None)`

Public method `coerce` exposed by `ToolRegistryConfig`.

- Category: Method
- Declared at: `src/miso/tools/registry.py:211`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolDescriptor`
- `IconDescriptor`
- `ToolkitDescriptor`
- `ToolkitRegistry`

### Minimal usage example

```python
ToolRegistryConfig(local_roots=..., enabled_plugins=..., include_builtin=..., validate=...)
```

## ToolDescriptor

Dataclass payload used by manifest discovery, metadata validation, icon resolution, and toolkit instantiation.

| Item | Details |
| --- | --- |
| Source | `src/miso/tools/registry.py:222` |
| Module role | Manifest discovery, metadata validation, icon resolution, and toolkit instantiation. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `name` | `str` | Required at construction time. |
| `title` | `str` | Required at construction time. |
| `description` | `str` | Required at construction time. |
| `icon_path` | `Path | None` | Required at construction time. |
| `icon` | `'IconDescriptor'` | Required at construction time. |
| `hidden` | `bool` | Default: `False`. |
| `requires_confirmation` | `bool` | Default: `False`. |
| `observe` | `bool` | Default: `False`. |

### Public methods

#### `to_summary(self)`

Public method `to_summary` exposed by `ToolDescriptor`.

- Category: Method
- Declared at: `src/miso/tools/registry.py:232`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolRegistryConfig`
- `IconDescriptor`
- `ToolkitDescriptor`
- `ToolkitRegistry`

### Minimal usage example

```python
ToolDescriptor(name=..., title=..., description=..., icon_path=...)
```

## IconDescriptor

Dataclass payload used by manifest discovery, metadata validation, icon resolution, and toolkit instantiation.

| Item | Details |
| --- | --- |
| Source | `src/miso/tools/registry.py:246` |
| Module role | Manifest discovery, metadata validation, icon resolution, and toolkit instantiation. |
| Inheritance | `-` |
| Exposure | Not exported; treat as implementation detail. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `type` | `str` | Required at construction time. |
| `path` | `Path | None` | Default: `None`. |
| `name` | `str | None` | Default: `None`. |
| `color` | `str | None` | Default: `None`. |
| `background_color` | `str | None` | Default: `None`. |

### Public methods

#### `from_file(cls, path: Path)`

Public method `from_file` exposed by `IconDescriptor`.

- Category: Method
- Declared at: `src/miso/tools/registry.py:254`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `from_builtin(cls, name: str, color: str, background_color: str)`

Public method `from_builtin` exposed by `IconDescriptor`.

- Category: Method
- Declared at: `src/miso/tools/registry.py:258`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_summary(self)`

Public method `to_summary` exposed by `IconDescriptor`.

- Category: Method
- Declared at: `src/miso/tools/registry.py:271`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolRegistryConfig`
- `ToolDescriptor`
- `ToolkitDescriptor`
- `ToolkitRegistry`

### Minimal usage example

```python
IconDescriptor(type=..., path=..., name=..., color=...)
```

## ToolkitDescriptor

Dataclass payload used by manifest discovery, metadata validation, icon resolution, and toolkit instantiation.

| Item | Details |
| --- | --- |
| Source | `src/miso/tools/registry.py:286` |
| Module role | Manifest discovery, metadata validation, icon resolution, and toolkit instantiation. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `id` | `str` | Required at construction time. |
| `name` | `str` | Required at construction time. |
| `description` | `str` | Required at construction time. |
| `factory` | `str` | Required at construction time. |
| `version` | `str | None` | Required at construction time. |
| `tags` | `tuple[str, ...]` | Required at construction time. |
| `manifest_path` | `Path` | Required at construction time. |
| `root_path` | `Path` | Required at construction time. |
| `readme_path` | `Path` | Required at construction time. |
| `icon_path` | `Path | None` | Required at construction time. |
| `icon` | `IconDescriptor` | Required at construction time. |
| `source` | `str` | Required at construction time. |
| `display_category` | `str | None` | Default: `None`. |
| `display_order` | `int` | Default: `0`. |
| `hidden` | `bool` | Default: `False`. |
| `compat_python` | `str | None` | Default: `None`. |
| `compat_miso` | `str | None` | Default: `None`. |
| `tools` | `dict[str, ToolDescriptor]` | Default: `field(default_factory=dict)`. |
| `import_roots` | `tuple[Path, ...]` | Default: `field(default_factory=tuple, repr=False)`. |

### Public methods

#### `sorted_tools(self)`

Public method `sorted_tools` exposed by `ToolkitDescriptor`.

- Category: Method
- Declared at: `src/miso/tools/registry.py:307`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_summary(self, *, include_tools: bool=True)`

Public method `to_summary` exposed by `ToolkitDescriptor`.

- Category: Method
- Declared at: `src/miso/tools/registry.py:313`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_metadata(self, *, include_tools: bool=True)`

Public method `to_metadata` exposed by `ToolkitDescriptor`.

- Category: Method
- Declared at: `src/miso/tools/registry.py:342`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolRegistryConfig`
- `ToolDescriptor`
- `IconDescriptor`
- `ToolkitRegistry`

### Minimal usage example

```python
ToolkitDescriptor(id=..., name=..., description=..., factory=...)
```

## ToolkitRegistry

Discovery and validation service that reads toolkit manifests, resolves assets, and instantiates builtin/local/plugin toolkits.

| Item | Details |
| --- | --- |
| Source | `src/miso/tools/registry.py:378` |
| Module role | Manifest discovery, metadata validation, icon resolution, and toolkit instantiation. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, config: ToolRegistryConfig | dict[str, Any] | None=None)`

### Properties

- `@property toolkits`: Public property accessor.

### Public methods

#### `__init__(self, config: ToolRegistryConfig | dict[str, Any] | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/tools/registry.py:381`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `list_toolkits(self, *, include_tools: bool=True)`

Public method `list_toolkits` exposed by `ToolkitRegistry`.

- Category: Method
- Declared at: `src/miso/tools/registry.py:390`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `get(self, toolkit_id: str)`

Public method `get` exposed by `ToolkitRegistry`.

- Category: Method
- Declared at: `src/miso/tools/registry.py:396`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `require(self, toolkit_id: str)`

Public method `require` exposed by `ToolkitRegistry`.

- Category: Method
- Declared at: `src/miso/tools/registry.py:399`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `get_toolkit_metadata(self, toolkit_id: str, tool_name: str | None=None)`

Public method `get_toolkit_metadata` exposed by `ToolkitRegistry`.

- Category: Method
- Declared at: `src/miso/tools/registry.py:405`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `instantiate_toolkit(self, toolkit_id: str)`

Public method `instantiate_toolkit` exposed by `ToolkitRegistry`.

- Category: Method
- Declared at: `src/miso/tools/registry.py:419`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Lifecycle and runtime role

- Initialization stores the registry configuration and eagerly discovers descriptors unless validation is disabled.
- Discovery walks builtin manifests, configured local roots, and plugin entry points, then validates that manifest metadata matches runtime tools.
- Instantiation imports the factory path, builds the runtime toolkit, and returns an executable `Toolkit` object.

### Collaboration and related types

- `ToolRegistryConfig`
- `ToolDescriptor`
- `IconDescriptor`
- `ToolkitDescriptor`

### Minimal usage example

```python
obj = ToolkitRegistry(...)
obj.list_toolkits(...)
```

### `src/miso/tools/tool.py`

The core callable wrapper that carries tool metadata and executes normalized arguments.

## Tool

Metadata-bearing wrapper around a callable, used as the atomic executable tool unit.

| Item | Details |
| --- | --- |
| Source | `src/miso/tools/tool.py:16` |
| Module role | The core callable wrapper that carries tool metadata and executes normalized arguments. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, name: str | Callable[..., Any]='', description: str='', func: Callable[..., Any] | None=None, parameters: list[ToolParameter | dict[str, Any]] | None=None, observe: bool=False, requires_confirmation: bool=False, history_arguments_optimizer: HistoryPayloadOptimizer | None=None, history_result_optimizer: HistoryPayloadOptimizer | None=None)`

### Public methods

#### `__init__(self, name: str | Callable[..., Any]='', description: str='', func: Callable[..., Any] | None=None, parameters: list[ToolParameter | dict[str, Any]] | None=None, observe: bool=False, requires_confirmation: bool=False, history_arguments_optimizer: HistoryPayloadOptimizer | None=None, history_result_optimizer: HistoryPayloadOptimizer | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/tools/tool.py:17`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `from_callable(cls, func: Callable[..., Any], *, name: str | None=None, description: str | None=None, parameters: list[ToolParameter | dict[str, Any]] | None=None, observe: bool=False, requires_confirmation: bool=False, history_arguments_optimizer: HistoryPayloadOptimizer | None=None, history_result_optimizer: HistoryPayloadOptimizer | None=None)`

Public method `from_callable` exposed by `Tool`.

- Category: Method
- Declared at: `src/miso/tools/tool.py:71`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_json(self)`

Public method `to_json` exposed by `Tool`.

- Category: Method
- Declared at: `src/miso/tools/tool.py:147`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `execute(self, arguments: dict[str, Any] | str | None)`

Public method `execute` exposed by `Tool`.

- Category: Method
- Declared at: `src/miso/tools/tool.py:167`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolkitCatalogConfig`
- `ToolkitCatalogRuntime`
- `ToolParameter`
- `ToolHistoryOptimizationContext`
- `NormalizedToolHistoryRecord`

### Minimal usage example

```python
obj = Tool(...)
obj.from_callable(...)
```

### `src/miso/tools/toolkit.py`

Tool container and registration surface used by runtimes and toolkit implementations.

## Toolkit

Dictionary-like container of `Tool` instances with registration, lookup, execution, and shutdown helpers.

| Item | Details |
| --- | --- |
| Source | `src/miso/tools/toolkit.py:9` |
| Module role | Tool container and registration surface used by runtimes and toolkit implementations. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, tools: dict[str, Tool] | None=None)`

### Public methods

#### `__init__(self, tools: dict[str, Tool] | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/tools/toolkit.py:10`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `register(self, tool_obj: Tool | Callable[..., Any], *, observe: bool | None=None, requires_confirmation: bool | None=None, name: str | None=None, description: str | None=None, parameters: list[ToolParameter | dict[str, Any]] | None=None, history_arguments_optimizer: HistoryPayloadOptimizer | None=None, history_result_optimizer: HistoryPayloadOptimizer | None=None)`

Public method `register` exposed by `Toolkit`.

- Category: Method
- Declared at: `src/miso/tools/toolkit.py:16`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `register_many(self, *tool_objs: Tool | Callable[..., Any])`

Public method `register_many` exposed by `Toolkit`.

- Category: Method
- Declared at: `src/miso/tools/toolkit.py:62`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `tool(self, func: Callable[..., Any] | None=None, *, observe: bool=False, requires_confirmation: bool=False, name: str | None=None, description: str | None=None, parameters: list[ToolParameter | dict[str, Any]] | None=None, history_arguments_optimizer: HistoryPayloadOptimizer | None=None, history_result_optimizer: HistoryPayloadOptimizer | None=None)`

Public method `tool` exposed by `Toolkit`.

- Category: Method
- Declared at: `src/miso/tools/toolkit.py:68`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `get(self, function_name: str)`

Public method `get` exposed by `Toolkit`.

- Category: Method
- Declared at: `src/miso/tools/toolkit.py:106`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `execute(self, function_name: str, arguments: dict[str, Any] | str | None)`

Public method `execute` exposed by `Toolkit`.

- Category: Method
- Declared at: `src/miso/tools/toolkit.py:109`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_json(self)`

Public method `to_json` exposed by `Toolkit`.

- Category: Method
- Declared at: `src/miso/tools/toolkit.py:115`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `shutdown(self)`

Public method `shutdown` exposed by `Toolkit`.

- Category: Method
- Declared at: `src/miso/tools/toolkit.py:118`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolkitCatalogConfig`
- `ToolkitCatalogRuntime`
- `ToolParameter`
- `ToolHistoryOptimizationContext`
- `NormalizedToolHistoryRecord`

### Minimal usage example

```python
obj = Toolkit(...)
obj.register(...)
```
