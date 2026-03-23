# Pupu Subsystem Reference

Catalog import, installed-server persistence, runtime attachment, and MCP management types grouped under the optional Pupu subsystem.

| Metric | Value |
| --- | --- |
| Classes | 20 |
| Dataclasses | 10 |
| Protocols | 0 |
| Internal-only types | 0 |

## Coverage map

| Class | Source | Exposure | Kind |
| --- | --- | --- | --- |
| `ToolPreview` | `src/miso/pupu/models.py:46` | subpackage | dataclass |
| `FieldSpec` | `src/miso/pupu/models.py:67` | subpackage | dataclass |
| `InstallProfile` | `src/miso/pupu/models.py:101` | subpackage | dataclass |
| `ConnectionIssue` | `src/miso/pupu/models.py:141` | subpackage | dataclass |
| `ConnectionTestResult` | `src/miso/pupu/models.py:163` | subpackage | dataclass |
| `DraftEntry` | `src/miso/pupu/models.py:205` | subpackage | dataclass |
| `MCPImportDraft` | `src/miso/pupu/models.py:251` | subpackage | dataclass |
| `CatalogEntry` | `src/miso/pupu/models.py:286` | subpackage | dataclass |
| `InstalledServer` | `src/miso/pupu/models.py:348` | subpackage | dataclass |
| `GitHubRepositoryClient` | `src/miso/pupu/services.py:220` | subpackage | class |
| `CatalogService` | `src/miso/pupu/services.py:254` | subpackage | class |
| `ImportService` | `src/miso/pupu/services.py:373` | subpackage | class |
| `NamespacedToolkit` | `src/miso/pupu/services.py:646` | subpackage | class |
| `MCPTestService` | `src/miso/pupu/services.py:685` | subpackage | class |
| `ManagedRuntimeHandle` | `src/miso/pupu/services.py:885` | internal | dataclass |
| `MCPRuntimeManager` | `src/miso/pupu/services.py:892` | subpackage | class |
| `PupuMCPService` | `src/miso/pupu/services.py:954` | subpackage | class |
| `FileCatalogCache` | `src/miso/pupu/stores.py:19` | subpackage | class |
| `FileInstalledServerStore` | `src/miso/pupu/stores.py:36` | subpackage | class |
| `InMemorySecretStore` | `src/miso/pupu/stores.py:79` | subpackage | class |

### `src/miso/pupu/models.py`

Dataclasses for Pupu catalog, draft, install profile, and installed-server payloads.

## ToolPreview

Dataclass payload used by dataclasses for pupu catalog, draft, install profile, and installed-server payloads.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/models.py:46` |
| Module role | Dataclasses for Pupu catalog, draft, install profile, and installed-server payloads. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `name` | `str` | Required at construction time. |
| `description` | `str` | Default: `''`. |

### Public methods

#### `to_dict(self)`

Public method `to_dict` exposed by `ToolPreview`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:50`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `from_dict(cls, value: dict[str, Any] | str)`

Public method `from_dict` exposed by `ToolPreview`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:57`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `FieldSpec`
- `InstallProfile`
- `ConnectionIssue`
- `ConnectionTestResult`
- `DraftEntry`

### Minimal usage example

```python
ToolPreview(name=..., description=...)
```

## FieldSpec

Dataclass payload used by dataclasses for pupu catalog, draft, install profile, and installed-server payloads.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/models.py:67` |
| Module role | Dataclasses for Pupu catalog, draft, install profile, and installed-server payloads. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `key` | `str` | Required at construction time. |
| `label` | `str` | Required at construction time. |
| `kind` | `str` | Default: `'string'`. |
| `required` | `bool` | Default: `False`. |
| `secret` | `bool` | Default: `False`. |
| `placeholder` | `str` | Default: `''`. |
| `help_text` | `str` | Default: `''`. |

### Public methods

#### `to_dict(self)`

Public method `to_dict` exposed by `FieldSpec`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:76`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `from_dict(cls, value: dict[str, Any])`

Public method `from_dict` exposed by `FieldSpec`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:88`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolPreview`
- `InstallProfile`
- `ConnectionIssue`
- `ConnectionTestResult`
- `DraftEntry`

### Minimal usage example

```python
FieldSpec(key=..., label=..., kind=..., required=...)
```

## InstallProfile

Dataclass payload used by dataclasses for pupu catalog, draft, install profile, and installed-server payloads.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/models.py:101` |
| Module role | Dataclasses for Pupu catalog, draft, install profile, and installed-server payloads. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `id` | `str` | Required at construction time. |
| `label` | `str` | Required at construction time. |
| `runtime` | `str` | Required at construction time. |
| `transport` | `str` | Required at construction time. |
| `platforms` | `tuple[str, ...]` | Required at construction time. |
| `fields` | `tuple[FieldSpec, ...]` | Required at construction time. |
| `required_secrets` | `tuple[str, ...]` | Required at construction time. |
| `default_values` | `dict[str, Any]` | Required at construction time. |

### Public methods

#### `to_dict(self)`

Public method `to_dict` exposed by `InstallProfile`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:111`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `from_dict(cls, value: dict[str, Any])`

Public method `from_dict` exposed by `InstallProfile`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:124`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolPreview`
- `FieldSpec`
- `ConnectionIssue`
- `ConnectionTestResult`
- `DraftEntry`

### Minimal usage example

```python
InstallProfile(id=..., label=..., runtime=..., transport=...)
```

## ConnectionIssue

Dataclass payload used by dataclasses for pupu catalog, draft, install profile, and installed-server payloads.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/models.py:141` |
| Module role | Dataclasses for Pupu catalog, draft, install profile, and installed-server payloads. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `code` | `str` | Required at construction time. |
| `message` | `str` | Required at construction time. |
| `detail` | `str` | Default: `''`. |

### Public methods

#### `to_dict(self)`

Public method `to_dict` exposed by `ConnectionIssue`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:146`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `from_dict(cls, value: dict[str, Any])`

Public method `from_dict` exposed by `ConnectionIssue`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:154`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolPreview`
- `FieldSpec`
- `InstallProfile`
- `ConnectionTestResult`
- `DraftEntry`

### Minimal usage example

```python
ConnectionIssue(code=..., message=..., detail=...)
```

## ConnectionTestResult

Dataclass payload used by dataclasses for pupu catalog, draft, install profile, and installed-server payloads.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/models.py:163` |
| Module role | Dataclasses for Pupu catalog, draft, install profile, and installed-server payloads. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `status` | `str` | Required at construction time. |
| `phase` | `str` | Required at construction time. |
| `summary` | `str` | Required at construction time. |
| `tool_count` | `int` | Required at construction time. |
| `tools` | `tuple[ToolPreview, ...]` | Required at construction time. |
| `warnings` | `tuple[str, ...]` | Required at construction time. |
| `errors` | `tuple[ConnectionIssue, ...]` | Required at construction time. |

### Public methods

#### `to_dict(self)`

Public method `to_dict` exposed by `ConnectionTestResult`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:172`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `from_dict(cls, value: dict[str, Any] | None)`

Public method `from_dict` exposed by `ConnectionTestResult`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:184`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolPreview`
- `FieldSpec`
- `InstallProfile`
- `ConnectionIssue`
- `DraftEntry`

### Minimal usage example

```python
ConnectionTestResult(status=..., phase=..., summary=..., tool_count=...)
```

## DraftEntry

Dataclass payload used by dataclasses for pupu catalog, draft, install profile, and installed-server payloads.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/models.py:205` |
| Module role | Dataclasses for Pupu catalog, draft, install profile, and installed-server payloads. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `entry_id` | `str` | Required at construction time. |
| `source_kind` | `str` | Required at construction time. |
| `display_name` | `str` | Required at construction time. |
| `profile_candidates` | `tuple[InstallProfile, ...]` | Required at construction time. |
| `prefilled_config` | `dict[str, Any]` | Required at construction time. |
| `required_fields` | `tuple[str, ...]` | Required at construction time. |
| `required_secrets` | `tuple[str, ...]` | Required at construction time. |
| `warnings` | `tuple[str, ...]` | Required at construction time. |
| `catalog_entry_id` | `str | None` | Default: `None`. |

### Public methods

#### `to_dict(self)`

Public method `to_dict` exposed by `DraftEntry`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:216`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `from_dict(cls, value: dict[str, Any])`

Public method `from_dict` exposed by `DraftEntry`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:230`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolPreview`
- `FieldSpec`
- `InstallProfile`
- `ConnectionIssue`
- `ConnectionTestResult`

### Minimal usage example

```python
DraftEntry(entry_id=..., source_kind=..., display_name=..., profile_candidates=...)
```

## MCPImportDraft

Dataclass payload used by dataclasses for pupu catalog, draft, install profile, and installed-server payloads.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/models.py:251` |
| Module role | Dataclasses for Pupu catalog, draft, install profile, and installed-server payloads. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `draft_id` | `str` | Required at construction time. |
| `source_kind` | `str` | Required at construction time. |
| `source_label` | `str` | Required at construction time. |
| `warnings` | `tuple[str, ...]` | Required at construction time. |
| `entries` | `tuple[DraftEntry, ...]` | Required at construction time. |

### Public methods

#### `to_dict(self)`

Public method `to_dict` exposed by `MCPImportDraft`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:258`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `create(cls, *, source_kind: str, source_label: str, warnings: list[str] | tuple[str, ...] | None=None, entries: list[DraftEntry] | tuple[DraftEntry, ...] | None=None)`

Public method `create` exposed by `MCPImportDraft`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:268`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolPreview`
- `FieldSpec`
- `InstallProfile`
- `ConnectionIssue`
- `ConnectionTestResult`

### Minimal usage example

```python
MCPImportDraft(draft_id=..., source_kind=..., source_label=..., warnings=...)
```

## CatalogEntry

Dataclass payload used by dataclasses for pupu catalog, draft, install profile, and installed-server payloads.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/models.py:286` |
| Module role | Dataclasses for Pupu catalog, draft, install profile, and installed-server payloads. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `id` | `str` | Required at construction time. |
| `slug` | `str` | Required at construction time. |
| `name` | `str` | Required at construction time. |
| `publisher` | `str` | Required at construction time. |
| `description` | `str` | Required at construction time. |
| `icon_url` | `str` | Required at construction time. |
| `verification` | `str` | Required at construction time. |
| `source_url` | `str` | Required at construction time. |
| `tags` | `tuple[str, ...]` | Required at construction time. |
| `revoked` | `bool` | Required at construction time. |
| `install_profiles` | `tuple[InstallProfile, ...]` | Required at construction time. |
| `tool_preview` | `tuple[ToolPreview, ...]` | Required at construction time. |
| `min_app_version` | `str | None` | Default: `None`. |

### Public methods

#### `to_dict(self)`

Public method `to_dict` exposed by `CatalogEntry`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:301`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `from_dict(cls, value: dict[str, Any])`

Public method `from_dict` exposed by `CatalogEntry`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:319`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolPreview`
- `FieldSpec`
- `InstallProfile`
- `ConnectionIssue`
- `ConnectionTestResult`

### Minimal usage example

```python
CatalogEntry(id=..., slug=..., name=..., publisher=...)
```

## InstalledServer

Dataclass payload used by dataclasses for pupu catalog, draft, install profile, and installed-server payloads.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/models.py:348` |
| Module role | Dataclasses for Pupu catalog, draft, install profile, and installed-server payloads. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `instance_id` | `str` | Required at construction time. |
| `instance_slug` | `str` | Required at construction time. |
| `display_name` | `str` | Required at construction time. |
| `source_kind` | `str` | Required at construction time. |
| `runtime` | `str` | Required at construction time. |
| `transport` | `str` | Required at construction time. |
| `status` | `str` | Required at construction time. |
| `enabled` | `bool` | Required at construction time. |
| `normalized_config` | `dict[str, Any]` | Required at construction time. |
| `required_secrets` | `tuple[str, ...]` | Required at construction time. |
| `catalog_entry_id` | `str | None` | Default: `None`. |
| `tool_count` | `int` | Default: `0`. |
| `cached_tools` | `tuple[ToolPreview, ...]` | Default: `()`. |
| `last_test_result` | `ConnectionTestResult | None` | Default: `None`. |
| `updated_at` | `str` | Default: `''`. |

### Properties

- `@property needs_secret`: Public property accessor.

### Public methods

#### `with_updates(self, **kwargs: Any)`

Public method `with_updates` exposed by `InstalledServer`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:373`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_record(self)`

Public method `to_record` exposed by `InstalledServer`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:390`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `to_public_dict(self, *, configured_secrets: list[str] | tuple[str, ...] | None=None)`

Public method `to_public_dict` exposed by `InstalledServer`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:409`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `create(cls, *, display_name: str, source_kind: str, runtime: str, transport: str, normalized_config: dict[str, Any], required_secrets: list[str] | tuple[str, ...] | None=None, catalog_entry_id: str | None=None, status: str='ready_for_review', enabled: bool=False)`

Public method `create` exposed by `InstalledServer`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:431`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `from_record(cls, value: dict[str, Any])`

Public method `from_record` exposed by `InstalledServer`.

- Category: Method
- Declared at: `src/miso/pupu/models.py:461`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `ToolPreview`
- `FieldSpec`
- `InstallProfile`
- `ConnectionIssue`
- `ConnectionTestResult`

### Minimal usage example

```python
InstalledServer(instance_id=..., instance_slug=..., display_name=..., source_kind=...)
```

### `src/miso/pupu/services.py`

Service layer for catalog refresh, import, runtime attachment, and server lifecycle management.

## GitHubRepositoryClient

Implementation class used by service layer for catalog refresh, import, runtime attachment, and server lifecycle management.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/services.py:220` |
| Module role | Service layer for catalog refresh, import, runtime attachment, and server lifecycle management. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, http_client: httpx.Client | None=None)`

### Public methods

#### `__init__(self, *, http_client: httpx.Client | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/pupu/services.py:221`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `parse_repo_url(self, url: str)`

Public method `parse_repo_url` exposed by `GitHubRepositoryClient`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:224`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `get_default_branch(self, owner: str, repo: str)`

Public method `get_default_branch` exposed by `GitHubRepositoryClient`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:236`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `fetch_file(self, owner: str, repo: str, branch: str, path: str)`

Public method `fetch_file` exposed by `GitHubRepositoryClient`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:245`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `CatalogService`
- `ImportService`
- `NamespacedToolkit`
- `MCPTestService`
- `ManagedRuntimeHandle`

### Minimal usage example

```python
obj = GitHubRepositoryClient(...)
obj.parse_repo_url(...)
```

## CatalogService

Implementation class used by service layer for catalog refresh, import, runtime attachment, and server lifecycle management.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/services.py:254` |
| Module role | Service layer for catalog refresh, import, runtime attachment, and server lifecycle management. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, remote_url: str | None=None, cache: FileCatalogCache | None=None, fetcher: Callable[[str, str | None], tuple[dict[str, Any] | None, str | None, bool]] | None=None, app_version: str | None=None, seed_entries: list[CatalogEntry] | None=None)`

### Public methods

#### `__init__(self, *, remote_url: str | None=None, cache: FileCatalogCache | None=None, fetcher: Callable[[str, str | None], tuple[dict[str, Any] | None, str | None, bool]] | None=None, app_version: str | None=None, seed_entries: list[CatalogEntry] | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/pupu/services.py:255`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `refresh(self, *, force: bool=False)`

Public method `refresh` exposed by `CatalogService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:287`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `list_catalog(self, *, query: str | None=None, tags: list[str] | tuple[str, ...] | None=None, runtime: str | None=None)`

Public method `list_catalog` exposed by `CatalogService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:334`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `get_entry(self, entry_id: str)`

Public method `get_entry` exposed by `CatalogService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:368`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `GitHubRepositoryClient`
- `ImportService`
- `NamespacedToolkit`
- `MCPTestService`
- `ManagedRuntimeHandle`

### Minimal usage example

```python
obj = CatalogService(...)
obj.refresh(...)
```

## ImportService

Implementation class used by service layer for catalog refresh, import, runtime attachment, and server lifecycle management.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/services.py:373` |
| Module role | Service layer for catalog refresh, import, runtime attachment, and server lifecycle management. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, github_client: GitHubRepositoryClient | None=None)`

### Public methods

#### `__init__(self, *, github_client: GitHubRepositoryClient | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/pupu/services.py:374`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `import_claude_config(self, *, json_text: str)`

Public method `import_claude_config` exposed by `ImportService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:377`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `import_github_repo(self, *, url: str)`

Public method `import_github_repo` exposed by `ImportService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:391`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `create_manual_draft(self, *, runtime: str, transport: str, name: str, config: dict[str, Any] | None=None)`

Public method `create_manual_draft` exposed by `ImportService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:430`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `GitHubRepositoryClient`
- `CatalogService`
- `NamespacedToolkit`
- `MCPTestService`
- `ManagedRuntimeHandle`

### Minimal usage example

```python
obj = ImportService(...)
obj.import_claude_config(...)
```

## NamespacedToolkit

Implementation class used by service layer for catalog refresh, import, runtime attachment, and server lifecycle management.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/services.py:646` |
| Module role | Service layer for catalog refresh, import, runtime attachment, and server lifecycle management. |
| Inheritance | `Toolkit` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, namespace: str, inner: Toolkit)`

### Public methods

#### `__init__(self, *, namespace: str, inner: Toolkit)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/pupu/services.py:647`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `refresh_tools(self)`

Public method `refresh_tools` exposed by `NamespacedToolkit`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:653`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `execute(self, function_name: str, arguments: dict[str, Any] | str | None)`

Public method `execute` exposed by `NamespacedToolkit`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:678`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `GitHubRepositoryClient`
- `CatalogService`
- `ImportService`
- `MCPTestService`
- `ManagedRuntimeHandle`

### Minimal usage example

```python
obj = NamespacedToolkit(...)
obj.refresh_tools(...)
```

## MCPTestService

Implementation class used by service layer for catalog refresh, import, runtime attachment, and server lifecycle management.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/services.py:685` |
| Module role | Service layer for catalog refresh, import, runtime attachment, and server lifecycle management. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, secret_store: InMemorySecretStore, toolkit_factory: Callable[[InstalledServer, dict[str, Any]], Toolkit] | None=None)`

### Public methods

#### `__init__(self, *, secret_store: InMemorySecretStore, toolkit_factory: Callable[[InstalledServer, dict[str, Any]], Toolkit] | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/pupu/services.py:686`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `test(self, instance: InstalledServer)`

Public method `test` exposed by `MCPTestService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:695`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `GitHubRepositoryClient`
- `CatalogService`
- `ImportService`
- `NamespacedToolkit`
- `ManagedRuntimeHandle`

### Minimal usage example

```python
obj = MCPTestService(...)
obj.test(...)
```

## ManagedRuntimeHandle

Dataclass payload used by service layer for catalog refresh, import, runtime attachment, and server lifecycle management.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/services.py:885` |
| Module role | Service layer for catalog refresh, import, runtime attachment, and server lifecycle management. |
| Inheritance | `-` |
| Exposure | Not exported; treat as implementation detail. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `instance_id` | `str` | Required at construction time. |
| `toolkit` | `Toolkit` | Required at construction time. |
| `namespaced_toolkit` | `NamespacedToolkit` | Required at construction time. |
| `cached_tools` | `tuple[ToolPreview, ...]` | Required at construction time. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `GitHubRepositoryClient`
- `CatalogService`
- `ImportService`
- `NamespacedToolkit`
- `MCPTestService`

### Minimal usage example

```python
ManagedRuntimeHandle(instance_id=..., toolkit=..., namespaced_toolkit=..., cached_tools=...)
```

## MCPRuntimeManager

Implementation class used by service layer for catalog refresh, import, runtime attachment, and server lifecycle management.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/services.py:892` |
| Module role | Service layer for catalog refresh, import, runtime attachment, and server lifecycle management. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, secret_store: InMemorySecretStore, toolkit_factory: Callable[[InstalledServer, dict[str, Any]], Toolkit] | None=None)`

### Public methods

#### `__init__(self, *, secret_store: InMemorySecretStore, toolkit_factory: Callable[[InstalledServer, dict[str, Any]], Toolkit] | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/pupu/services.py:893`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `ensure_enabled(self, instance: InstalledServer)`

Public method `ensure_enabled` exposed by `MCPRuntimeManager`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:904`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `disable(self, instance_id: str)`

Public method `disable` exposed by `MCPRuntimeManager`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:925`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `attach(self, chat_id: str, instances: list[InstalledServer])`

Public method `attach` exposed by `MCPRuntimeManager`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:936`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `toolkits_for_chat(self, chat_id: str)`

Public method `toolkits_for_chat` exposed by `MCPRuntimeManager`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:945`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `GitHubRepositoryClient`
- `CatalogService`
- `ImportService`
- `NamespacedToolkit`
- `MCPTestService`

### Minimal usage example

```python
obj = MCPRuntimeManager(...)
obj.ensure_enabled(...)
```

## PupuMCPService

Facade over the Pupu subsystem that exposes catalog, import, persistence, testing, enable/disable, and chat attachment operations.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/services.py:954` |
| Module role | Service layer for catalog refresh, import, runtime attachment, and server lifecycle management. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, catalog_service: CatalogService | None=None, import_service: ImportService | None=None, installed_server_store: FileInstalledServerStore | None=None, secret_store: InMemorySecretStore | None=None, test_service: MCPTestService | None=None, runtime_manager: MCPRuntimeManager | None=None)`

### Public methods

#### `__init__(self, *, catalog_service: CatalogService | None=None, import_service: ImportService | None=None, installed_server_store: FileInstalledServerStore | None=None, secret_store: InMemorySecretStore | None=None, test_service: MCPTestService | None=None, runtime_manager: MCPRuntimeManager | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/pupu/services.py:955`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `list_catalog(self, *, query: str | None=None, tags: list[str] | tuple[str, ...] | None=None, runtime: str | None=None)`

Public method `list_catalog` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:973`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `import_claude_config(self, *, json_text: str)`

Public method `import_claude_config` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:983`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `import_github_repo(self, *, url: str)`

Public method `import_github_repo` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:988`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `create_manual_draft(self, *, runtime: str, transport: str, name: str, config: dict[str, Any] | None=None)`

Public method `create_manual_draft` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:993`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `save_installed_server(self, *, draft_entry_id: str, profile_id: str, display_name: str | None=None, config: dict[str, Any] | None=None, secrets: dict[str, str] | None=None)`

Public method `save_installed_server` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1010`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `list_installed_servers(self)`

Public method `list_installed_servers` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1076`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `get_installed_server_detail(self, *, instance_id: str)`

Public method `get_installed_server_detail` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1082`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `test_installed_server(self, *, instance_id: str)`

Public method `test_installed_server` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1086`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `enable_installed_server(self, *, instance_id: str)`

Public method `enable_installed_server` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1120`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `disable_installed_server(self, *, instance_id: str)`

Public method `disable_installed_server` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1143`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `attach_servers_to_chat(self, *, chat_id: str, instance_ids: list[str])`

Public method `attach_servers_to_chat` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1151`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `get_chat_toolkits(self, chat_id: str)`

Public method `get_chat_toolkits` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1161`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `listCatalog(self, filters: dict[str, Any] | None=None)`

Public method `listCatalog` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1226`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `importClaudeConfig(self, payload: dict[str, Any])`

Public method `importClaudeConfig` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1234`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `importGitHubRepo(self, payload: dict[str, Any])`

Public method `importGitHubRepo` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1237`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `createManualDraft(self, payload: dict[str, Any])`

Public method `createManualDraft` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1240`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `saveInstalledServer(self, payload: dict[str, Any])`

Public method `saveInstalledServer` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1248`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `listInstalledServers(self)`

Public method `listInstalledServers` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1257`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `getInstalledServerDetail(self, payload: dict[str, Any])`

Public method `getInstalledServerDetail` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1260`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `testInstalledServer(self, payload: dict[str, Any])`

Public method `testInstalledServer` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1263`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `enableInstalledServer(self, payload: dict[str, Any])`

Public method `enableInstalledServer` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1266`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `disableInstalledServer(self, payload: dict[str, Any])`

Public method `disableInstalledServer` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1269`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `attachServersToChat(self, payload: dict[str, Any])`

Public method `attachServersToChat` exposed by `PupuMCPService`.

- Category: Method
- Declared at: `src/miso/pupu/services.py:1272`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Lifecycle and runtime role

- Construction wires together the catalog service, import service, installed-server store, secret store, test service, and runtime manager.
- Catalog and import methods create drafts or list entries without mutating runtime attachments.
- Installed-server methods persist draft selections, validate configuration/secrets, and delegate enable/disable and chat attachment to the runtime manager.
- CamelCase aliases remain as compatibility shims over the snake_case service surface.

### Collaboration and related types

- `GitHubRepositoryClient`
- `CatalogService`
- `ImportService`
- `NamespacedToolkit`
- `MCPTestService`

### Minimal usage example

```python
obj = PupuMCPService(...)
obj.list_catalog(...)
```

### `src/miso/pupu/stores.py`

Persistence and secret-store helpers used by the Pupu subsystem.

## FileCatalogCache

Implementation class used by persistence and secret-store helpers used by the pupu subsystem.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/stores.py:19` |
| Module role | Persistence and secret-store helpers used by the Pupu subsystem. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, path: str | Path | None=None)`

### Public methods

#### `__init__(self, path: str | Path | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/pupu/stores.py:20`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `load(self)`

Public method `load` exposed by `FileCatalogCache`.

- Category: Method
- Declared at: `src/miso/pupu/stores.py:23`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `save(self, *, etag: str | None, payload: dict[str, Any])`

Public method `save` exposed by `FileCatalogCache`.

- Category: Method
- Declared at: `src/miso/pupu/stores.py:28`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `FileInstalledServerStore`
- `InMemorySecretStore`

### Minimal usage example

```python
obj = FileCatalogCache(...)
obj.load(...)
```

## FileInstalledServerStore

Implementation class used by persistence and secret-store helpers used by the pupu subsystem.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/stores.py:36` |
| Module role | Persistence and secret-store helpers used by the Pupu subsystem. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, path: str | Path | None=None)`

### Public methods

#### `__init__(self, path: str | Path | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/pupu/stores.py:37`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `list_instances(self)`

Public method `list_instances` exposed by `FileInstalledServerStore`.

- Category: Method
- Declared at: `src/miso/pupu/stores.py:40`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `get_instance(self, instance_id: str)`

Public method `get_instance` exposed by `FileInstalledServerStore`.

- Category: Method
- Declared at: `src/miso/pupu/stores.py:50`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `save_instance(self, instance: InstalledServer)`

Public method `save_instance` exposed by `FileInstalledServerStore`.

- Category: Method
- Declared at: `src/miso/pupu/stores.py:56`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `FileCatalogCache`
- `InMemorySecretStore`

### Minimal usage example

```python
obj = FileInstalledServerStore(...)
obj.list_instances(...)
```

## InMemorySecretStore

Implementation class used by persistence and secret-store helpers used by the pupu subsystem.

| Item | Details |
| --- | --- |
| Source | `src/miso/pupu/stores.py:79` |
| Module role | Persistence and secret-store helpers used by the Pupu subsystem. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self)`

### Public methods

#### `__init__(self)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/pupu/stores.py:80`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `set_secret(self, instance_id: str, target: str, value: str)`

Public method `set_secret` exposed by `InMemorySecretStore`.

- Category: Method
- Declared at: `src/miso/pupu/stores.py:83`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `has_secret(self, instance_id: str, target: str)`

Public method `has_secret` exposed by `InMemorySecretStore`.

- Category: Method
- Declared at: `src/miso/pupu/stores.py:86`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `clear_secret(self, instance_id: str, target: str)`

Public method `clear_secret` exposed by `InMemorySecretStore`.

- Category: Method
- Declared at: `src/miso/pupu/stores.py:89`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `resolve_secrets(self, instance_id: str, targets: list[str] | tuple[str, ...] | None=None)`

Public method `resolve_secrets` exposed by `InMemorySecretStore`.

- Category: Method
- Declared at: `src/miso/pupu/stores.py:92`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `FileCatalogCache`
- `FileInstalledServerStore`

### Minimal usage example

```python
obj = InMemorySecretStore(...)
obj.set_secret(...)
```
