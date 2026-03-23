# Memory System

Canonical English skill chapter for the `memory-system` topic.

## Role and boundaries

This chapter documents how short-term context selection, long-term profile extraction, vector search, and workspace pin state interact through `MemoryManager`.

## Dependency view

- Strategies implement the `ContextStrategy` protocol.
- `MemoryManager` orchestrates stores, strategies, summary generation, vector search, and long-term extraction.
- Optional Qdrant adapters provide concrete vector backends for both session-scoped and long-term retrieval.

## Core objects

- `MemoryManager`
- `MemoryConfig`
- `LongTermMemoryConfig`
- `LastNTurnsStrategy`
- `SummaryTokenStrategy`
- `HybridContextStrategy`
- `QdrantVectorAdapter`
- `QdrantLongTermVectorAdapter`

## Execution and state flow

- Prepare incoming messages from session state and strategy rules.
- Inject summaries, similarity hits, and pinned context.
- Commit the new conversation state after a turn.
- Persist long-term facts and vector embeddings if configured.

## Configuration surface

- Session store and vector adapters.
- Summary thresholds and token limits.
- Long-term namespace, extractor model, and persistence directories.

## Extension points

- Implement custom `SessionStore`, `VectorStoreAdapter`, or `ContextStrategy`.
- Swap between in-memory and JSON/Qdrant-backed persistence.
- Tune retrieval, summarization, and long-term extraction independently.

## Common gotchas

- Namespace choice affects long-term isolation.
- Long-term components are optional until the runtime needs them.
- Hybrid retrieval only contributes when a vector adapter is configured.

## Related class references

- [Memory API](../api/memory.md)
- [Runtime API](../api/runtime.md)

## Source entry points

- `src/miso/memory/manager.py`
- `src/miso/memory/qdrant.py`

## Detailed legacy reference

The original repository skill note is preserved below for continuity and extra examples. The canonical copy now lives in this docs tree.

> Memory tiers, configuration, context strategies, namespace scoping, and how to extend with custom adapters.

## Memory Tiers

```text
┌─────────────────────────────────────────────────────────┐
│ Tier 1: Session Store (short-term)                      │
│   In-memory or JSON-file key-value store                │
│   Stores raw conversation turns per session             │
├─────────────────────────────────────────────────────────┤
│ Tier 2: Context Strategy (short-term)                   │
│   Selects which turns to include in the context window  │
│   LastNTurns / SummaryToken / Hybrid                    │
├─────────────────────────────────────────────────────────┤
│ Tier 3: Vector Store (short-term, optional)             │
│   Similarity search on recent messages                  │
│   Retrieves relevant older turns by embedding           │
├─────────────────────────────────────────────────────────┤
│ Tier 4: Long-Term Profile (optional)                    │
│   Extracted facts, episodes, playbooks                  │
│   Persisted per namespace across sessions               │
├─────────────────────────────────────────────────────────┤
│ Tier 5: Long-Term Vectors (optional)                    │
│   Qdrant-backed semantic search on profile entries      │
│   Cross-session knowledge retrieval                     │
└─────────────────────────────────────────────────────────┘
```

Each tier is independently optional. You can use just Tier 1-2 (basic conversation) or stack all five for full persistence.

## Configuration

### `MemoryConfig` — Short-term memory

```python
from miso.memory import MemoryConfig

config = MemoryConfig(
    last_n_turns=8,                     # Always include the last N turns
    summary_trigger_pct=0.75,           # Summarize when context reaches 75% of window
    summary_target_pct=0.45,            # Compact to 45% after summarization
    max_summary_chars=2400,             # Max chars for the summary itself
    vector_top_k=4,                     # Retrieve top-4 similar past messages
    vector_adapter=None,                # Optional VectorStoreAdapter instance
    long_term=None,                     # Optional LongTermMemoryConfig
    deferred_tool_compaction_enabled=True,  # Shrink old tool payloads
)
```

### `LongTermMemoryConfig` — Persistent knowledge

```python
from miso.memory import LongTermMemoryConfig

lt_config = LongTermMemoryConfig(
    profile_store=my_profile_store,     # LongTermProfileStore implementation
    vector_adapter=my_vector_adapter,   # LongTermVectorAdapter implementation (e.g., Qdrant)
    extraction_model=None,              # Model to use for fact extraction (defaults to agent's model)
    extraction_provider=None,           # Provider for extraction
)
```

### Passing to Agent

```python
from miso import Agent

agent = Agent(
    name="coder",
    provider="openai",
    model="gpt-5",
    short_term_memory=MemoryConfig(last_n_turns=10),
    long_term_memory=LongTermMemoryConfig(
        profile_store=JsonFileLongTermProfileStore(path="./memory"),
    ),
)
```

Or as dicts (auto-converted):

```python
agent = Agent(
    short_term_memory={"last_n_turns": 10},
    long_term_memory={"profile_store": my_store},
)
```

## Context Strategies

Strategies determine which messages from the session store are included in the LLM's context window.

### `LastNTurnsStrategy`

Always include the last N message pairs. Simple and predictable.

```python
# Configured via MemoryConfig.last_n_turns
config = MemoryConfig(last_n_turns=8)
```

### `SummaryTokenStrategy`

When the conversation exceeds a percentage of the model's context window, older messages are summarized into a compact form. The summary replaces the detailed messages.

```python
config = MemoryConfig(
    summary_trigger_pct=0.75,   # Start summarizing at 75% context usage
    summary_target_pct=0.45,    # Compress down to 45%
    max_summary_chars=2400,
)
```

The summary is generated by calling the LLM itself (via Broth) with a summarization prompt.

### `HybridContextStrategy`

Combines LastNTurns + SummaryToken. Recent turns are always kept; older ones are summarized when space runs low. This is the **default** when you provide a `MemoryConfig`.

## Namespace Scoping

Memory is scoped by two identifiers:

| Identifier         | Purpose                     | Default              |
| ------------------ | --------------------------- | -------------------- |
| `session_id`       | Isolates conversation turns | Auto-generated UUID  |
| `memory_namespace` | Isolates long-term profiles | Same as `session_id` |

### Naming conventions

| Context                                | `session_id`  | `memory_namespace`                   |
| -------------------------------------- | ------------- | ------------------------------------ |
| Single agent, single run               | UUID          | UUID                                 |
| Single agent, multi-run (same session) | Fixed user ID | Fixed user ID                        |
| Team agent                             | UUID          | `{session_id}:{agent_name}`          |
| Subagent                               | Parent's ID   | `{parent_namespace}:{subagent_name}` |

**Key rule**: Use the same `memory_namespace` across sessions to accumulate long-term knowledge. Use different `session_id` values to keep conversation turns separate.

## Tool History Compaction

The `tool_history` module shrinks old tool call payloads in the conversation history.

### What it does

After each run, large tool arguments and results from **previous turns** (not the current one) are replaced with compact summaries:

```python
# Before compaction (in conversation history):
{"tool_call": "read_file", "arguments": {"path": "main.py"}, "result": {"content": "... 50,000 chars ..."}}

# After compaction:
{"tool_call": "read_file", "arguments": {"path": "main.py"}, "result": "[compacted: 50000 chars]"}
```

### Configuration

```python
config = MemoryConfig(
    deferred_tool_compaction_enabled=True,  # Default: True
)
```

### Custom compaction via history optimizers

Register per-tool optimizers when the default compaction isn't good enough:

```python
self.register(
    self.search_text,
    history_result_optimizer=lambda result: {
        **result,
        "matches": f"[{len(result.get('matches', []))} matches, details omitted]",
    },
)
```

## Session Stores

### `InMemorySessionStore` (default)

Ephemeral — conversation is lost when the process exits.

```python
from miso.memory import InMemorySessionStore

store = InMemorySessionStore()
```

### Custom `SessionStore`

Implement the interface for persistence:

```python
from miso.memory import SessionStore

class MySessionStore(SessionStore):
    def load(self, session_id: str) -> list[dict]:
        """Load conversation turns for session."""
        ...

    def save(self, session_id: str, messages: list[dict]) -> None:
        """Save conversation turns for session."""
        ...

    def delete(self, session_id: str) -> None:
        """Delete all turns for session."""
        ...
```

## Vector Store Adapters

### `VectorStoreAdapter` (short-term similarity search)

```python
from miso.memory import VectorStoreAdapter

class MyVectorAdapter(VectorStoreAdapter):
    def add(self, texts: list[str], metadatas: list[dict], namespace: str) -> None:
        """Index text chunks with metadata."""
        ...

    def search(self, query: str, top_k: int, namespace: str) -> list[dict]:
        """Return top-k similar chunks."""
        ...
```

### `LongTermVectorAdapter` (cross-session knowledge)

Same interface shape, but operates on long-term profile entries. The Qdrant adapter (`miso.memory.qdrant`) is the reference implementation.

## Long-Term Profile Store

```python
from miso.memory import LongTermProfileStore

class MyProfileStore(LongTermProfileStore):
    def load(self, namespace: str) -> dict:
        """Load profile (facts, episodes, playbooks)."""
        ...

    def save(self, namespace: str, profile: dict) -> None:
        """Save profile."""
        ...
```

The built-in `JsonFileLongTermProfileStore` saves profiles as JSON files in a directory.

## Memory Flow During a Run

```text
Agent.run(messages, session_id, memory_namespace)
  │
  ▼
MemoryManager.prepare_messages(session_id)
  │  1. Load raw turns from SessionStore
  │  2. Apply context strategy (LastN + Summary)
  │  3. Inject workspace pin context
  │  4. Inject long-term profile summary (if available)
  │  5. Retrieve similar past messages (vector search)
  │  6. Return context-window-sized message list
  │
  ▼
Broth.run() — executes LLM loop with prepared messages
  │
  ▼
MemoryManager.commit_messages(session_id, full_conversation)
  │  1. Save all turns to SessionStore
  │  2. Apply tool history compaction
  │  3. Extract long-term facts/episodes (async, via LLM)
  │  4. Persist to LongTermProfileStore
  │  5. Index in LongTermVectorAdapter
  │
  ▼
Done
```

## Common Gotchas

1. **Summary generation calls the LLM** — `SummaryTokenStrategy` makes an extra API call to generate the summary. This adds latency and token cost. If your conversations are short, `LastNTurnsStrategy` alone is sufficient.

2. **`memory_namespace` vs `session_id`** — Confusing these causes either cross-session data leaks (wrong namespace) or failure to accumulate knowledge (wrong session_id). See the naming table above.

3. **Vector adapter is optional** — If you don't provide one, similarity search is silently skipped. The system works fine without it.

4. **Long-term extraction needs a model** — Fact extraction calls the LLM. If `extraction_model` is not set, it uses the agent's own model, which adds token cost to every run.

5. **Tool compaction is lossy** — Old tool results are replaced with summaries. If the LLM needs to reference exact previous results, it may not find them. The current turn is never compacted.

6. **InMemorySessionStore is ephemeral** — Default store loses everything on process restart. For multi-session use, implement a persistent `SessionStore`.

## Related Skills

- [architecture-overview.md](architecture-overview.md) — Where memory fits in the system
- [runtime-engine.md](runtime-engine.md) — How Broth integrates with MemoryManager
- [agent-and-team.md](agent-and-team.md) — Memory namespace conventions for teams/subagents
