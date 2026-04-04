# Memory API Reference

Short-term and long-term memory contracts, stores, strategies, adapters, and the MemoryManager orchestrator.

| Metric | Value |
| --- | --- |
| Classes | 16 |
| Dataclasses | 2 |
| Protocols | 5 |
| Internal-only types | 0 |

## Coverage map

| Class | Source | Exposure | Kind |
| --- | --- | --- | --- |
| `SessionStore` | `src/unchain/memory/manager.py:21` | subpackage | protocol |
| `VectorStoreAdapter` | `src/unchain/memory/manager.py:30` | subpackage | protocol |
| `LongTermProfileStore` | `src/unchain/memory/manager.py:52` | subpackage | protocol |
| `LongTermVectorAdapter` | `src/unchain/memory/manager.py:61` | subpackage | protocol |
| `ContextStrategy` | `src/unchain/memory/manager.py:84` | subpackage | protocol |
| `InMemorySessionStore` | `src/unchain/memory/manager.py:104` | subpackage | class |
| `JsonFileLongTermProfileStore` | `src/unchain/memory/manager.py:117` | subpackage | class |
| `LongTermMemoryConfig` | `src/unchain/memory/manager.py:144` | subpackage | dataclass |
| `MemoryConfig` | `src/unchain/memory/manager.py:167` | subpackage | dataclass |
| `LastNTurnsStrategy` | `src/unchain/memory/manager.py:1642` | subpackage | class |
| `SummaryTokenStrategy` | `src/unchain/memory/manager.py:1675` | subpackage | class |
| `HybridContextStrategy` | `src/unchain/memory/manager.py:1779` | subpackage | class |
| `MemoryManager` | `src/unchain/memory/manager.py:1866` | subpackage | class |
| `QdrantVectorAdapter` | `src/unchain/memory/qdrant.py:198` | internal | class |
| `QdrantLongTermVectorAdapter` | `src/unchain/memory/qdrant.py:305` | internal | class |
| `JsonFileSessionStore` | `src/unchain/memory/qdrant.py:410` | internal | class |

### `src/unchain/memory/manager.py`

Memory contracts, configuration, strategies, and the top-level MemoryManager orchestration layer.

## SessionStore

Protocol that defines a stable contract within memory contracts, configuration, strategies, and the top-level memorymanager orchestration layer.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/manager.py:21` |
| Module role | Memory contracts, configuration, strategies, and the top-level MemoryManager orchestration layer. |
| Inheritance | `Protocol` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Protocol; public-facing or package-visible. |

### Public methods

#### `load(self, session_id: str)`

Public method `load` exposed by `SessionStore`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:22`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `save(self, session_id: str, state: dict[str, Any])`

Public method `save` exposed by `SessionStore`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:25`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`
- `InMemorySessionStore`

### Minimal usage example

```python
class Demo(...):
    pass
```

## VectorStoreAdapter

Protocol that defines a stable contract within memory contracts, configuration, strategies, and the top-level memorymanager orchestration layer.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/manager.py:30` |
| Module role | Memory contracts, configuration, strategies, and the top-level MemoryManager orchestration layer. |
| Inheritance | `Protocol` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Protocol; public-facing or package-visible. |

### Public methods

#### `add_texts(self, *, session_id: str, texts: list[str], metadatas: list[dict[str, Any]])`

Public method `add_texts` exposed by `VectorStoreAdapter`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:31`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `similarity_search(self, *, session_id: str, query: str, k: int, min_score: float | None=None)`

Public method `similarity_search` exposed by `VectorStoreAdapter`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:40`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `SessionStore`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`
- `InMemorySessionStore`

### Minimal usage example

```python
class Demo(...):
    pass
```

## LongTermProfileStore

Protocol that defines a stable contract within memory contracts, configuration, strategies, and the top-level memorymanager orchestration layer.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/manager.py:52` |
| Module role | Memory contracts, configuration, strategies, and the top-level MemoryManager orchestration layer. |
| Inheritance | `Protocol` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Protocol; public-facing or package-visible. |

### Public methods

#### `load(self, namespace: str)`

Public method `load` exposed by `LongTermProfileStore`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:53`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `save(self, namespace: str, profile: dict[str, Any])`

Public method `save` exposed by `LongTermProfileStore`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:56`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermVectorAdapter`
- `ContextStrategy`
- `InMemorySessionStore`

### Minimal usage example

```python
class Demo(...):
    pass
```

## LongTermVectorAdapter

Protocol that defines a stable contract within memory contracts, configuration, strategies, and the top-level memorymanager orchestration layer.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/manager.py:61` |
| Module role | Memory contracts, configuration, strategies, and the top-level MemoryManager orchestration layer. |
| Inheritance | `Protocol` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Protocol; public-facing or package-visible. |

### Public methods

#### `add_texts(self, *, namespace: str, texts: list[str], metadatas: list[dict[str, Any]])`

Public method `add_texts` exposed by `LongTermVectorAdapter`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:62`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `similarity_search(self, *, namespace: str, query: str, k: int, filters: dict[str, Any] | None=None, min_score: float | None=None)`

Public method `similarity_search` exposed by `LongTermVectorAdapter`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:71`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `ContextStrategy`
- `InMemorySessionStore`

### Minimal usage example

```python
class Demo(...):
    pass
```

## ContextStrategy

Protocol that defines a stable contract within memory contracts, configuration, strategies, and the top-level memorymanager orchestration layer.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/manager.py:84` |
| Module role | Memory contracts, configuration, strategies, and the top-level MemoryManager orchestration layer. |
| Inheritance | `Protocol` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Protocol; public-facing or package-visible. |

### Public methods

#### `prepare(self, *, state: dict[str, Any], incoming: list[dict[str, Any]], max_context_window_tokens: int, model: str)`

Public method `prepare` exposed by `ContextStrategy`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:85`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `commit(self, *, state: dict[str, Any], full_conversation: list[dict[str, Any]])`

Public method `commit` exposed by `ContextStrategy`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:95`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `InMemorySessionStore`

### Minimal usage example

```python
class Demo(...):
    pass
```

## InMemorySessionStore

Implementation class used by memory contracts, configuration, strategies, and the top-level memorymanager orchestration layer.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/manager.py:104` |
| Module role | Memory contracts, configuration, strategies, and the top-level MemoryManager orchestration layer. |
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
- Declared at: `src/unchain/memory/manager.py:107`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `load(self, session_id: str)`

Public method `load` exposed by `InMemorySessionStore`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:110`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `save(self, session_id: str, state: dict[str, Any])`

Public method `save` exposed by `InMemorySessionStore`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:113`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### Minimal usage example

```python
obj = InMemorySessionStore(...)
obj.load(...)
```

## JsonFileLongTermProfileStore

Implementation class used by memory contracts, configuration, strategies, and the top-level memorymanager orchestration layer.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/manager.py:117` |
| Module role | Memory contracts, configuration, strategies, and the top-level MemoryManager orchestration layer. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, base_dir: str | Path | None=None)`

### Public methods

#### `__init__(self, base_dir: str | Path | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/memory/manager.py:120`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `load(self, namespace: str)`

Public method `load` exposed by `JsonFileLongTermProfileStore`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:128`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `save(self, namespace: str, profile: dict[str, Any])`

Public method `save` exposed by `JsonFileLongTermProfileStore`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:138`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### Minimal usage example

```python
obj = JsonFileLongTermProfileStore(...)
obj.load(...)
```

## LongTermMemoryConfig

Dataclass payload used by memory contracts, configuration, strategies, and the top-level memorymanager orchestration layer.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/manager.py:144` |
| Module role | Memory contracts, configuration, strategies, and the top-level MemoryManager orchestration layer. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `profile_store` | `LongTermProfileStore | None` | Default: `None`. |
| `vector_adapter` | `LongTermVectorAdapter | None` | Default: `None`. |
| `extractor` | `LongTermExtractor | None` | Default: `None`. |
| `vector_top_k` | `int` | Default: `4`. |
| `vector_min_score` | `float | None` | Default: `None`. |
| `episode_top_k` | `int` | Default: `2`. |
| `episode_min_score` | `float | None` | Default: `None`. |
| `playbook_top_k` | `int` | Default: `2`. |
| `playbook_min_score` | `float | None` | Default: `None`. |
| `max_profile_chars` | `int` | Default: `1200`. |
| `max_fact_items` | `int` | Default: `6`. |
| `max_episode_items` | `int` | Default: `3`. |
| `max_playbook_items` | `int` | Default: `2`. |
| `extract_every_n_turns` | `int` | Default: `1`. |
| `embedding_model` | `str` | Default: `'text-embedding-3-small'`. |
| `embedding_payload` | `dict[str, Any] | None` | Default: `None`. |
| `profile_base_dir` | `str | Path | None` | Default: `None`. |
| `qdrant_path` | `str | Path | None` | Default: `None`. |
| `collection_prefix` | `str` | Default: `'long_term'`. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### Minimal usage example

```python
LongTermMemoryConfig(profile_store=..., vector_adapter=..., extractor=..., vector_top_k=...)
```

## MemoryConfig

Dataclass payload used by memory contracts, configuration, strategies, and the top-level memorymanager orchestration layer.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/manager.py:167` |
| Module role | Memory contracts, configuration, strategies, and the top-level MemoryManager orchestration layer. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Dataclass; public-facing or package-visible. |

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `last_n_turns` | `int` | Default: `8`. |
| `summary_trigger_pct` | `float` | Default: `0.75`. |
| `summary_target_pct` | `float` | Default: `0.45`. |
| `max_summary_chars` | `int` | Default: `2400`. |
| `vector_top_k` | `int` | Default: `4`. |
| `vector_min_score` | `float | None` | Default: `None`. |
| `vector_adapter` | `VectorStoreAdapter | None` | Default: `None`. |
| `long_term` | `LongTermMemoryConfig | None` | Default: `None`. |
| `deferred_tool_compaction_enabled` | `bool` | Default: `True`. |
| `deferred_tool_compaction_keep_completed_turns` | `int` | Default: `1`. |
| `deferred_tool_compaction_max_chars` | `int` | Default: `1200`. |
| `deferred_tool_compaction_preview_chars` | `int` | Default: `160`. |
| `deferred_tool_compaction_include_tools` | `list[str] | None` | Default: `None`. |
| `deferred_tool_compaction_hash_payloads` | `bool` | Default: `True`. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### Minimal usage example

```python
MemoryConfig(last_n_turns=..., summary_trigger_pct=..., summary_target_pct=..., max_summary_chars=...)
```

## LastNTurnsStrategy

Implementation class used by memory contracts, configuration, strategies, and the top-level memorymanager orchestration layer.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/manager.py:1642` |
| Module role | Memory contracts, configuration, strategies, and the top-level MemoryManager orchestration layer. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, last_n_turns: int=8)`

### Public methods

#### `__init__(self, last_n_turns: int=8)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/memory/manager.py:1643`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `prepare(self, *, state: dict[str, Any], incoming: list[dict[str, Any]], max_context_window_tokens: int, model: str)`

Public method `prepare` exposed by `LastNTurnsStrategy`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:1646`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `commit(self, *, state: dict[str, Any], full_conversation: list[dict[str, Any]])`

Public method `commit` exposed by `LastNTurnsStrategy`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:1666`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### Minimal usage example

```python
obj = LastNTurnsStrategy(...)
obj.prepare(...)
```

## SummaryTokenStrategy

Implementation class used by memory contracts, configuration, strategies, and the top-level memorymanager orchestration layer.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/manager.py:1675` |
| Module role | Memory contracts, configuration, strategies, and the top-level MemoryManager orchestration layer. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, summary_trigger_pct: float=0.75, summary_target_pct: float=0.45, max_summary_chars: int=2400)`

### Public methods

#### `__init__(self, *, summary_trigger_pct: float=0.75, summary_target_pct: float=0.45, max_summary_chars: int=2400)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/memory/manager.py:1676`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `prepare(self, *, state: dict[str, Any], incoming: list[dict[str, Any]], max_context_window_tokens: int, model: str)`

Public method `prepare` exposed by `SummaryTokenStrategy`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:1687`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `commit(self, *, state: dict[str, Any], full_conversation: list[dict[str, Any]])`

Public method `commit` exposed by `SummaryTokenStrategy`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:1770`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### Minimal usage example

```python
obj = SummaryTokenStrategy(...)
obj.prepare(...)
```

## HybridContextStrategy

Implementation class used by memory contracts, configuration, strategies, and the top-level memorymanager orchestration layer.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/manager.py:1779` |
| Module role | Memory contracts, configuration, strategies, and the top-level MemoryManager orchestration layer. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, summary_strategy: SummaryTokenStrategy, last_n_strategy: LastNTurnsStrategy, vector_top_k: int=4, vector_min_score: float | None=None, vector_adapter: VectorStoreAdapter | None=None)`

### Public methods

#### `__init__(self, *, summary_strategy: SummaryTokenStrategy, last_n_strategy: LastNTurnsStrategy, vector_top_k: int=4, vector_min_score: float | None=None, vector_adapter: VectorStoreAdapter | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/memory/manager.py:1780`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `prepare(self, *, state: dict[str, Any], incoming: list[dict[str, Any]], max_context_window_tokens: int, model: str)`

Public method `prepare` exposed by `HybridContextStrategy`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:1795`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `commit(self, *, state: dict[str, Any], full_conversation: list[dict[str, Any]])`

Public method `commit` exposed by `HybridContextStrategy`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:1856`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### Minimal usage example

```python
obj = HybridContextStrategy(...)
obj.prepare(...)
```

## MemoryManager

Top-level memory orchestrator that prepares messages from session state and commits new turns back into short-term and long-term storage.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/manager.py:1866` |
| Module role | Memory contracts, configuration, strategies, and the top-level MemoryManager orchestration layer. |
| Inheritance | `-` |
| Exposure | Exported from its subpackage `__init__`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, config: MemoryConfig | None=None, store: SessionStore | None=None, strategy: ContextStrategy | None=None)`

### Properties

- `@property last_prepare_info`: Public property accessor.
- `@property last_commit_info`: Public property accessor.

### Public methods

#### `__init__(self, config: MemoryConfig | None=None, store: SessionStore | None=None, strategy: ContextStrategy | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/memory/manager.py:1867`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `ensure_long_term_components(self, *, broth_instance: Any | None=None)`

Public method `ensure_long_term_components` exposed by `MemoryManager`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:1897`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `estimate_tokens(self, messages: list[dict[str, Any]])`

Public method `estimate_tokens` exposed by `MemoryManager`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:1919`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `prepare_messages(self, session_id: str, incoming: list[dict[str, Any]], *, max_context_window_tokens: int, model: str, summary_generator: SummaryGenerator | None=None, memory_namespace: str | None=None, provider: str | None=None, tool_resolver: HistoryToolResolver | None=None)`

Public method `prepare_messages` exposed by `MemoryManager`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:2070`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `commit_messages(self, session_id: str, full_conversation: list[dict[str, Any]], *, memory_namespace: str | None=None, model: str | None=None, long_term_extractor: LongTermExtractor | None=None)`

Public method `commit_messages` exposed by `MemoryManager`.

- Category: Method
- Declared at: `src/unchain/memory/manager.py:2269`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Lifecycle and runtime role

- Initialization binds config, a session store, and the chosen context strategy.
- `prepare_messages()` loads session state, asks the strategy to produce the working context, and can enrich it with summaries or vector hits.
- `commit_messages()` persists the updated short-term state and optionally writes long-term facts/profile updates.
- Long-term components are created lazily; Qdrant/profile stores are only required when the configuration enables them.

### Collaboration and related types

- `SessionStore`
- `VectorStoreAdapter`
- `LongTermProfileStore`
- `LongTermVectorAdapter`
- `ContextStrategy`

### Minimal usage example

```python
obj = MemoryManager(...)
obj.ensure_long_term_components(...)
```

### `src/unchain/memory/qdrant.py`

Qdrant-backed vector adapters plus a JSON session-store implementation.

## QdrantVectorAdapter

Implementation class used by qdrant-backed vector adapters plus a json session-store implementation.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/qdrant.py:198` |
| Module role | Qdrant-backed vector adapters plus a JSON session-store implementation. |
| Inheritance | `-` |
| Exposure | Not exported; treat as implementation detail. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, client: 'QdrantClient', embed_fn, vector_size: int, collection_prefix: str='chat')`

### Public methods

#### `__init__(self, client: 'QdrantClient', embed_fn, vector_size: int, collection_prefix: str='chat')`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/memory/qdrant.py:205`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `add_texts(self, *, session_id: str, texts: list[str], metadatas: list[dict[str, Any]])`

Public method `add_texts` exposed by `QdrantVectorAdapter`.

- Category: Method
- Declared at: `src/unchain/memory/qdrant.py:236`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `similarity_search(self, *, session_id: str, query: str, k: int, min_score: float | None=None)`

Public method `similarity_search` exposed by `QdrantVectorAdapter`.

- Category: Method
- Declared at: `src/unchain/memory/qdrant.py:256`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `QdrantLongTermVectorAdapter`
- `JsonFileSessionStore`

### Minimal usage example

```python
obj = QdrantVectorAdapter(...)
obj.add_texts(...)
```

## QdrantLongTermVectorAdapter

Implementation class used by qdrant-backed vector adapters plus a json session-store implementation.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/qdrant.py:305` |
| Module role | Qdrant-backed vector adapters plus a JSON session-store implementation. |
| Inheritance | `-` |
| Exposure | Not exported; treat as implementation detail. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, client: 'QdrantClient', embed_fn, vector_size: int, collection_prefix: str='long_term')`

### Public methods

#### `__init__(self, client: 'QdrantClient', embed_fn, vector_size: int, collection_prefix: str='long_term')`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/memory/qdrant.py:312`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `add_texts(self, *, namespace: str, texts: list[str], metadatas: list[dict[str, Any]])`

Public method `add_texts` exposed by `QdrantLongTermVectorAdapter`.

- Category: Method
- Declared at: `src/unchain/memory/qdrant.py:343`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `similarity_search(self, *, namespace: str, query: str, k: int, filters: dict[str, Any] | None=None, min_score: float | None=None)`

Public method `similarity_search` exposed by `QdrantLongTermVectorAdapter`.

- Category: Method
- Declared at: `src/unchain/memory/qdrant.py:363`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `QdrantVectorAdapter`
- `JsonFileSessionStore`

### Minimal usage example

```python
obj = QdrantLongTermVectorAdapter(...)
obj.add_texts(...)
```

## JsonFileSessionStore

Implementation class used by qdrant-backed vector adapters plus a json session-store implementation.

| Item | Details |
| --- | --- |
| Source | `src/unchain/memory/qdrant.py:410` |
| Module role | Qdrant-backed vector adapters plus a JSON session-store implementation. |
| Inheritance | `-` |
| Exposure | Not exported; treat as implementation detail. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, base_dir: str | Path)`

### Public methods

#### `__init__(self, base_dir: str | Path)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/unchain/memory/qdrant.py:421`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `load(self, session_id: str)`

Public method `load` exposed by `JsonFileSessionStore`.

- Category: Method
- Declared at: `src/unchain/memory/qdrant.py:429`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `save(self, session_id: str, state: dict[str, Any])`

Public method `save` exposed by `JsonFileSessionStore`.

- Category: Method
- Declared at: `src/unchain/memory/qdrant.py:438`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Collaboration and related types

- `QdrantVectorAdapter`
- `QdrantLongTermVectorAdapter`

### Minimal usage example

```python
obj = JsonFileSessionStore(...)
obj.load(...)
```
