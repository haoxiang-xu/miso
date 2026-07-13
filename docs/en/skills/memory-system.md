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

- `src/unchain/memory/manager.py`
- `src/unchain/memory/qdrant.py`

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
from unchain.memory import MemoryConfig

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
from unchain.memory import LongTermMemoryConfig

lt_config = LongTermMemoryConfig(
    profile_store=my_profile_store,     # LongTermProfileStore implementation
    vector_adapter=my_vector_adapter,   # LongTermVectorAdapter implementation (e.g., Qdrant)
    extraction_model=None,              # Model to use for fact extraction (defaults to agent's model)
    extraction_provider=None,           # Provider for extraction
)
```

### Passing to Agent

```python
from unchain import Agent
from unchain.agent import MemoryModule

agent = Agent(
    name="coder",
    provider="openai",
    model="gpt-5",
    modules=(
        MemoryModule(memory=MemoryConfig(
            last_n_turns=10,
            long_term=LongTermMemoryConfig(
                profile_store=JsonFileLongTermProfileStore(path="./memory"),
            ),
        )),
    ),
)
```

`MemoryModule` accepts a `KernelMemoryRuntime`, a `MemoryManager`, a `MemoryConfig`, or a raw dict (auto-coerced). Passing a config keeps memory behaviour declarative; passing a `KernelMemoryRuntime` lets you reuse one runtime across multiple agents.

For one `Agent` instance, a config-created runtime is reused across calls, so a fixed `session_id` retains history. Its default store is still process-local. For restart durability, provide a persistent store:

```python
from unchain.memory import JsonFileSessionStore, MemoryConfig, MemoryManager

manager = MemoryManager(
    config=MemoryConfig(last_n_turns=10),
    store=JsonFileSessionStore("./session-state"),
)
agent = Agent(
    name="durable-coder",
    modules=(MemoryModule(memory=manager),),
)
```

## Semantic Memory vs Execution Checkpoints

The session store keeps two deliberately separate layers:

| Layer | Purpose | May be summarized or sanitized? |
| --- | --- | --- |
| `messages` | Completed, human-readable semantic conversation | Yes |
| `execution_checkpoint` | Incomplete tool transaction, continuation, provider replay frame, integrity and tool-schema digests | No |

- `completed`: atomically commit semantic messages and conditionally clear the matching checkpoint in the same CAS write.
- `max_iterations`: leave semantic messages unchanged and persist an execution checkpoint.
- `awaiting_human_input`: leave semantic messages unchanged and persist the transcript plus continuation.

A later `agent.run(..., session_id=same_id)` continues a `max_iterations` checkpoint without executing completed tools again. On that cold continuation, `max_iterations=N` means N additional model iterations for the new invocation; the cumulative iteration counter is still restored for telemetry. An awaiting-human checkpoint must be resumed; a fresh run fails closed. `agent.resume_human_input(session_id=same_id, response=...)` can load the persisted conversation and continuation without an old in-process result object.

The replay frame is provider-native and ordered. OpenAI preserves encrypted reasoning items, Anthropic/Hyperspace preserve thinking signatures, and Ollama preserves the thinking field. It is integrity-checked, bound to provider/model and the active tool schema, excluded from normal memory compaction, and redacted from request traces.

The guarantee begins after the checkpoint write is verified. A hard crash between an external tool side effect and that write still requires an idempotency key, write-ahead log, or transactional outbox; execution checkpoints alone cannot provide arbitrary-crash exactly-once semantics.

For a memory-backed `session_id`, all `on_suspend` contributors finish before the reserved persistence barrier. Blocking `on_human_input` and `on_max_iterations` callbacks, plus their corresponding request events, run only after the checkpoint has been written and read back. Final message events likewise run only after durable finalization. A failed checkpoint write therefore prevents the callback from entering a long wait. A callback response itself is not yet an exactly-once durable interaction record; a crash after the user responds but before the resume delta is applied may still require the caller to submit that response again.

The current execution checkpoint restores the built-in continuation boundary: semantic transcript, provider replay frame, cumulative iteration/token counters, context-window size, and workspace-change state. It is not yet a universal serialization format for arbitrary harness state. Custom `component_state`, optimizer internals, artifacts, and subagent state require a future versioned per-harness checkpoint-slice contract if they must survive a cold process restart.

Every built-in session store also attaches a monotonic revision to the whole session state. Bootstrap captures that revision, and semantic commit, checkpoint save/clear, workspace-pin mutation, and edit/resend replacement use compare-and-swap (CAS). A stale worker raises `SessionRevisionConflictError` instead of overwriting newer messages or a newer checkpoint. Repeating the same deterministic checkpoint write is idempotent.

Memory-backed agent runs also use an execution lease keyed by `session_id`. `PreparedAgent` holds one lease across tool exposure, every kernel turn, completion-policy repairs, and run hooks. Model retries, tool confirmation/execution, observation calls, checkpoint persistence, and finalization all verify the same monotonically increasing fencing token. A heartbeat renews the lease during long model or tool calls; after a durable human-input or max-iteration checkpoint, the blocking callback runs with the lease released, and continuation must reacquire a newer token while the session revision is still unchanged.

`InMemorySessionStore` provides this guarantee only between threads sharing that store object. `JsonFileSessionStore` uses a stable sidecar file lock and lease record, so separate local processes running as the same OS user and sharing the same private directory participate in the same lease; it is not a multi-host distributed-lock claim. Stores that expose only legacy `load`/`save` remain best effort. A custom store must implement the complete lease lifecycle plus an atomic `fencing token + revision CAS + state write` operation; a separate `verify_lease()` followed by `save_if_revision()` is rejected because it has a takeover race.

While a lease is active, the built-in stores reject direct `save()` and unfenced `save_if_revision()` calls for that session. Extensions must use a framework path that carries the current fence; ordinary tools and run hooks do not yet receive a general-purpose fenced session writer and must not mutate canonical session state through the raw store.

Fencing prevents a superseded worker from starting its next guarded step or writing durable session state. It does not make an arbitrary remote tool side effect exactly once if the process loses connectivity or crashes after the remote system accepts the request. Such tools still need an idempotency key, intent/receipt journal, or transactional outbox.

The lease is session-scoped, not memory-namespace-scoped. Two valid runs using different `session_id` values can still update the same long-term profile or vector namespace concurrently. The built-in profile store and external vector adapters therefore remain best-effort shared resources until they gain their own namespace revision/lease or atomic merge contract.

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

The summary is generated by re-entering the kernel with a summarization prompt against the agent's own model.

### `HybridContextStrategy`

Combines LastNTurns + SummaryToken. Recent turns are always kept; older ones are summarized when space runs low. This is the **default** when you provide a `MemoryConfig`.

Compaction is transactional: LastN may delete the source turns only after the
summary stage has produced a non-empty replacement. If the generator is
missing, fails, or returns an empty summary, the original context remains and
the optimizer records `upstream_summary_replacement_unavailable` instead of
silently dropping early evidence.

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
| Subagent                               | Parent's ID   | `{parent_namespace}:{subagent_name}` |
| Nested subagent                        | Root's ID     | `{root}:{parent}:{child}`            |

**Key rule**: Use the same `memory_namespace` across sessions to accumulate long-term knowledge. Use different `session_id` values to keep conversation turns separate.

## Tool History Compaction

The `tool_history` module shrinks old tool call payloads in the conversation history.

### What it does

After each run, large tool arguments and results from **previous turns** (not the current one) are replaced with compact summaries:

```python
# Before compaction (in conversation history):
{"tool_call": "read_files", "arguments": {"paths": ["main.py"]}, "result": {"files": [{"content": "... 50,000 chars ..."}]}}

# After compaction:
{"tool_call": "read_files", "arguments": {"paths": ["main.py"]}, "result": "[compacted: 50000 chars]"}
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
from unchain.memory import InMemorySessionStore

store = InMemorySessionStore()
```

### Custom `SessionStore`

Implement the interface for persistence:

```python
from unchain.memory import SessionStore

class MySessionStore(SessionStore):
    def load(self, session_id: str) -> dict:
        """Load the complete session state."""
        ...

    def save(self, session_id: str, state: dict) -> None:
        """Unconditionally save the complete session state."""
        ...
```

This legacy interface remains supported, but it is reported as `session_consistency="best_effort"` and cannot prevent cross-process stale writes. Production stores for long-running tasks should also implement the optional revision capability:

```python
from unchain.memory import SessionSnapshot

def load_with_revision(session_id: str) -> SessionSnapshot:
    ...

def save_if_revision(
    session_id: str,
    state: dict,
    expected_revision: int,
) -> int:
    """Atomically save or raise SessionRevisionConflictError."""
    ...
```

`InMemorySessionStore` implements process-local CAS. `JsonFileSessionStore` implements cross-process CAS with a per-session file lock, `fsync`, and atomic replacement; malformed state fails closed instead of being treated as an empty session.

## Vector Store Adapters

### `VectorStoreAdapter` (short-term similarity search)

```python
from unchain.memory import VectorStoreAdapter

class MyVectorAdapter(VectorStoreAdapter):
    def add(self, texts: list[str], metadatas: list[dict], namespace: str) -> None:
        """Index text chunks with metadata."""
        ...

    def search(self, query: str, top_k: int, namespace: str) -> list[dict]:
        """Return top-k similar chunks."""
        ...
```

### `LongTermVectorAdapter` (cross-session knowledge)

Same interface shape, but operates on long-term profile entries. The Qdrant adapter (`unchain.memory.qdrant`) is the reference implementation.

## Long-Term Profile Store

```python
from unchain.memory import LongTermProfileStore

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

`MemoryModule` installs two harnesses on the kernel: one that recalls memory before each model turn, and one that commits memory after each iteration. The flow looks like:

```text
Agent.run(messages, session_id=..., memory_namespace=...)
  │
  ▼
KernelLoop.run() — bootstrap + before_model phases:
  MemoryManager.prepare_messages(session_id)
  │  1. Load raw turns from SessionStore
  │  2. Apply context strategy (LastN + Summary)
  │  3. Inject workspace pin context
  │  4. Inject long-term profile summary (if available)
  │  5. Retrieve similar past messages (vector search)
  │  6. Return context-window-sized message list
  │
  ▼
ModelIO.fetch_turn(...) — provider call with prepared messages
  │
  ▼
KernelLoop.run() — before_commit phase:
  MemoryManager.commit_messages(session_id, full_conversation)
  │  1. Save all turns to SessionStore
  │  2. Apply tool history compaction
  │  3. Extract long-term facts/episodes (via LLM)
  │  4. Persist to LongTermProfileStore
  │  5. Index in LongTermVectorAdapter
  │
  ▼
KernelRunResult returned to caller
```

## Common Gotchas

1. **Summary generation calls the LLM** — `SummaryTokenStrategy` makes an extra API call to generate the summary. This adds latency and token cost. If your conversations are short, `LastNTurnsStrategy` alone is sufficient.

2. **`memory_namespace` vs `session_id`** — Confusing these causes either cross-session data leaks (wrong namespace) or failure to accumulate knowledge (wrong session_id). See the naming table above.

3. **Vector adapter is optional** — If you don't provide one, similarity search is silently skipped. The system works fine without it.

4. **Long-term extraction needs a model** — Fact extraction calls the LLM. If `extraction_model` is not set, it uses the agent's own model, which adds token cost to every run.

5. **Tool compaction is lossy** — Old tool results are replaced with summaries. If the LLM needs to reference exact previous results, it may not find them. The current turn is never compacted.

6. **InMemorySessionStore is ephemeral** — Default store loses everything on process restart. For restart recovery, use `JsonFileSessionStore` or another persistent revisioned store. A legacy `load/save` store works, but only with best-effort concurrency.

## Related Skills

- [architecture-overview.md](architecture-overview.md) — Where memory fits in the system
- [runtime-engine.md](runtime-engine.md) — How memory harnesses plug into `KernelLoop`
- [agent-and-team.md](agent-and-team.md) — Memory namespace conventions for subagents
