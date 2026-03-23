# Agents API Reference

Single-agent orchestration, team coordination, and the internal subagent runtime scaffolding used to expose nested execution safely.

| Metric | Value |
| --- | --- |
| Classes | 5 |
| Dataclasses | 3 |
| Protocols | 0 |
| Internal-only types | 3 |

## Coverage map

| Class | Source | Exposure | Kind |
| --- | --- | --- | --- |
| `_SubagentConfig` | `src/miso/agents/agent.py:74` | internal | dataclass |
| `_SubagentCounters` | `src/miso/agents/agent.py:83` | internal | dataclass |
| `_SubagentRuntime` | `src/miso/agents/agent.py:89` | internal | dataclass |
| `Agent` | `src/miso/agents/agent.py:114` | top-level | class |
| `Team` | `src/miso/agents/team.py:11` | top-level | class |

### `src/miso/agents/agent.py`

High-level single-agent orchestration with memory, toolkit merging, suspension/resume handling, and optional subagent spawning.

## _SubagentConfig

Dataclass payload used by high-level single-agent orchestration with memory, toolkit merging, suspension/resume handling, and optional subagent spawning.

| Item | Details |
| --- | --- |
| Source | `src/miso/agents/agent.py:74` |
| Module role | High-level single-agent orchestration with memory, toolkit merging, suspension/resume handling, and optional subagent spawning. |
| Inheritance | `-` |
| Exposure | Not exported; treat as implementation detail. |
| Kind | Dataclass; internal implementation. |

### Internal implementation note

Owned by `Agent` as the stored subagent configuration.

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `tool_name` | `str` | Required at construction time. |
| `description` | `str | None` | Required at construction time. |
| `max_depth` | `int` | Required at construction time. |
| `max_children_per_agent` | `int` | Required at construction time. |
| `max_total_subagents` | `int` | Required at construction time. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `_SubagentCounters`
- `_SubagentRuntime`
- `Agent`

### Minimal usage example

```python
_SubagentConfig(tool_name=..., description=..., max_depth=..., max_children_per_agent=...)
```

## _SubagentCounters

Dataclass payload used by high-level single-agent orchestration with memory, toolkit merging, suspension/resume handling, and optional subagent spawning.

| Item | Details |
| --- | --- |
| Source | `src/miso/agents/agent.py:83` |
| Module role | High-level single-agent orchestration with memory, toolkit merging, suspension/resume handling, and optional subagent spawning. |
| Inheritance | `-` |
| Exposure | Not exported; treat as implementation detail. |
| Kind | Dataclass; internal implementation. |

### Internal implementation note

Owned by `Agent` subagent runtime bookkeeping.

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `total_created` | `int` | Default: `0`. |
| `direct_children` | `dict[tuple[str, ...], int]` | Default: `field(default_factory=dict)`. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `_SubagentConfig`
- `_SubagentRuntime`
- `Agent`

### Minimal usage example

```python
_SubagentCounters(total_created=..., direct_children=...)
```

## _SubagentRuntime

Dataclass payload used by high-level single-agent orchestration with memory, toolkit merging, suspension/resume handling, and optional subagent spawning.

| Item | Details |
| --- | --- |
| Source | `src/miso/agents/agent.py:89` |
| Module role | High-level single-agent orchestration with memory, toolkit merging, suspension/resume handling, and optional subagent spawning. |
| Inheritance | `-` |
| Exposure | Not exported; treat as implementation detail. |
| Kind | Dataclass; internal implementation. |

### Internal implementation note

Created inside `Agent` subagent execution paths and passed through nested runs.

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `config` | `_SubagentConfig` | Required at construction time. |
| `current_depth` | `int` | Required at construction time. |
| `lineage` | `tuple[str, ...]` | Required at construction time. |
| `counters` | `_SubagentCounters` | Required at construction time. |
| `current_session_id` | `str` | Required at construction time. |
| `current_memory_namespace` | `str` | Required at construction time. |
| `payload` | `dict[str, Any] | None` | Required at construction time. |
| `callback` | `Callable[[dict[str, Any]], None] | None` | Required at construction time. |
| `max_iterations` | `int | None` | Required at construction time. |
| `verbose` | `bool` | Required at construction time. |
| `on_tool_confirm` | `Callable | None` | Required at construction time. |
| `on_continuation_request` | `Callable | None` | Required at construction time. |

### Public methods

This type does not expose public methods beyond dataclass/protocol structure.

### Collaboration and related types

- `_SubagentConfig`
- `_SubagentCounters`
- `Agent`

### Minimal usage example

```python
_SubagentRuntime(config=..., current_depth=..., lineage=..., counters=...)
```

## Agent

High-level single-agent facade that owns configuration, normalizes tools, creates fresh runtimes, and exposes run/resume/step/as-tool entry points.

| Item | Details |
| --- | --- |
| Source | `src/miso/agents/agent.py:114` |
| Module role | High-level single-agent orchestration with memory, toolkit merging, suspension/resume handling, and optional subagent spawning. |
| Inheritance | `-` |
| Exposure | Top-level export via `miso`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, name: str, instructions: str='', provider: str='openai', model: str='gpt-5', api_key: str | None=None, tools: list[Tool | Toolkit | Callable[..., Any]] | None=None, short_term_memory: MemoryManager | MemoryConfig | dict[str, Any] | None=None, long_term_memory: LongTermMemoryConfig | dict[str, Any] | None=None, defaults: dict[str, Any] | None=None, broth_options: dict[str, Any] | None=None, toolkit_catalog_config: ToolkitCatalogConfig | dict[str, Any] | None=None)`

### Public methods

#### `__init__(self, *, name: str, instructions: str='', provider: str='openai', model: str='gpt-5', api_key: str | None=None, tools: list[Tool | Toolkit | Callable[..., Any]] | None=None, short_term_memory: MemoryManager | MemoryConfig | dict[str, Any] | None=None, long_term_memory: LongTermMemoryConfig | dict[str, Any] | None=None, defaults: dict[str, Any] | None=None, broth_options: dict[str, Any] | None=None, toolkit_catalog_config: ToolkitCatalogConfig | dict[str, Any] | None=None)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/agents/agent.py:115`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `enable_subagents(self, *, tool_name: str='spawn_subagent', description: str | None=None, max_depth: int=6, max_children_per_agent: int=10, max_total_subagents: int=100)`

Public method `enable_subagents` exposed by `Agent`.

- Category: Method
- Declared at: `src/miso/agents/agent.py:185`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `enable_toolkit_catalog(self, *, managed_toolkit_ids: tuple[str, ...] | list[str] | None, always_active_toolkit_ids: tuple[str, ...] | list[str] | None=None, registry: dict[str, Any] | None=None, readme_max_chars: int=8000)`

Public method `enable_toolkit_catalog` exposed by `Agent`.

- Category: Method
- Declared at: `src/miso/agents/agent.py:213`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `run(self, messages: str | list[dict[str, Any]] | None, *, payload: dict[str, Any] | None=None, response_format: ResponseFormat | None=None, callback: Callable[[dict[str, Any]], None] | None=None, verbose: bool=False, max_iterations: int | None=None, previous_response_id: str | None=None, on_tool_confirm: Callable | None=None, on_continuation_request: Callable | None=None, session_id: str | None=None, memory_namespace: str | None=None)`

Public method `run` exposed by `Agent`.

- Category: Method
- Declared at: `src/miso/agents/agent.py:613`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `resume_human_input(self, *, conversation: list[dict[str, Any]], continuation: dict[str, Any], response: dict[str, Any] | Any, payload: dict[str, Any] | None=None, response_format: ResponseFormat | None=None, callback: Callable[[dict[str, Any]], None] | None=None, verbose: bool=False, on_tool_confirm: Callable | None=None, on_continuation_request: Callable | None=None, session_id: str | None=None, memory_namespace: str | None=None)`

Public method `resume_human_input` exposed by `Agent`.

- Category: Method
- Declared at: `src/miso/agents/agent.py:695`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `step(self, *, inbox: list[dict[str, Any]], channels: dict[str, list[str]], owner: str, team_transcript: list[dict[str, Any]] | None=None, mode: str='channel_collab', payload: dict[str, Any] | None=None, callback: Callable[[dict[str, Any]], None] | None=None, verbose: bool=False, max_iterations: int | None=None, session_id: str | None=None, memory_namespace: str | None=None)`

Public method `step` exposed by `Agent`.

- Category: Method
- Declared at: `src/miso/agents/agent.py:776`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `as_tool(self, *, name: str | None=None, description: str | None=None)`

Public method `as_tool` exposed by `Agent`.

- Category: Method
- Declared at: `src/miso/agents/agent.py:928`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Lifecycle and runtime role

- Construction validates identity, copies config dictionaries, and coerces memory configuration into a `MemoryManager` when needed.
- `run()` builds a merged toolkit, creates a fresh `Broth`, forwards runtime options, and captures toolkit-catalog continuation state if the run pauses.
- `resume_human_input()` restores suspended catalog state back into a fresh runtime instance before continuing the same conversation.
- `step()` is the team-facing wrapper that asks the model to publish/handoff/finalize against a structured step schema.

### Collaboration and related types

- `_SubagentConfig`
- `_SubagentCounters`
- `_SubagentRuntime`

### Minimal usage example

```python
obj = Agent(...)
obj.enable_subagents(...)
```

### `src/miso/agents/team.py`

Multi-agent coordination via channel delivery, scoring, handoff handling, and owner-controlled completion.

## Team

Coordinator that routes envelopes across named channels, scores pending work, and lets an owner agent finalize a multi-agent run.

| Item | Details |
| --- | --- |
| Source | `src/miso/agents/team.py:11` |
| Module role | Multi-agent coordination via channel delivery, scoring, handoff handling, and owner-controlled completion. |
| Inheritance | `-` |
| Exposure | Top-level export via `miso`. |
| Kind | Class; public-facing or package-visible. |

### Constructor surface

The constructor is the primary place where this class defines required inputs and validation.

- `__init__(self, *, agents: list[Agent], owner: str, channels: dict[str, list[str]], mode: str='channel_collab', visible_transcript: bool=True, completion_policy: str='owner_finalize', max_steps: int=24)`

### Public methods

#### `__init__(self, *, agents: list[Agent], owner: str, channels: dict[str, list[str]], mode: str='channel_collab', visible_transcript: bool=True, completion_policy: str='owner_finalize', max_steps: int=24)`

Initializes the instance and validates/coerces construction-time inputs where the class enforces them.

- Category: Constructor
- Declared at: `src/miso/agents/team.py:12`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

#### `run(self, messages: str | list[dict[str, Any]], *, entry_channel: str | None=None, payload: dict[str, Any] | None=None, callback: Callable[[dict[str, Any]], None] | None=None, session_id: str | None=None, memory_namespace: str | None=None, max_steps: int | None=None)`

Public method `run` exposed by `Team`.

- Category: Method
- Declared at: `src/miso/agents/team.py:139`
- Return shape: see the source signature/body for the concrete payload; most user-facing surfaces return dict payloads or serialized dataclass content when applicable.
- Errors and validation: this surface may raise propagated `ValueError`/`TypeError` for invalid construction/configuration inputs; tool-style methods may also return `{"error": ...}` payloads.

### Lifecycle and runtime role

- Construction normalizes agent names, validates channel subscribers, and fixes step-limit defaults.
- `run()` converts the initial user request into a channel envelope, keeps per-agent pending inboxes, and repeatedly schedules the highest-scoring agent.
- Each agent receives a namespaced session and memory namespace, publishes messages back into channels, may hand off explicitly, and only the owner may finalize.

### Collaboration and related types

- `_SubagentConfig`
- `_SubagentCounters`
- `_SubagentRuntime`
- `Agent`

### Minimal usage example

```python
obj = Team(...)
obj.run(...)
```
