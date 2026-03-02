import json

from miso import broth as Broth
from miso.broth import ProviderTurnResult
from miso.memory import (
    HybridContextStrategy,
    LastNTurnsStrategy,
    MemoryConfig,
    MemoryManager,
    SummaryTokenStrategy,
)


class _FakeVectorAdapter:
    def __init__(self, *, fail_search: bool = False):
        self.fail_search = fail_search
        self.added: list[tuple[str, list[str]]] = []
        self.searches: list[tuple[str, str, int]] = []

    def add_texts(self, *, session_id, texts, metadatas):
        del metadatas
        self.added.append((session_id, list(texts)))

    def similarity_search(self, *, session_id, query, k):
        self.searches.append((session_id, query, k))
        if self.fail_search:
            raise RuntimeError("vector search failed")
        return [f"recall:{query}:1", f"recall:{query}:2"]


def _conversation_with_tool_turn() -> list[dict]:
    return [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "function": {"name": "demo", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'},
        {"role": "assistant", "content": "a3"},
    ]


def test_last_n_strategy_keeps_system_and_recent_turns_without_splitting_tools():
    manager = MemoryManager(
        config=MemoryConfig(last_n_turns=2),
        strategy=LastNTurnsStrategy(last_n_turns=2),
    )
    session_id = "s_last_n"
    manager.commit_messages(session_id, _conversation_with_tool_turn())

    prepared = manager.prepare_messages(
        session_id,
        [{"role": "user", "content": "u4"}],
        max_context_window_tokens=10_000,
        model="gpt-5",
    )

    roles = [msg.get("role") for msg in prepared]
    assert roles[0] == "system"
    assert roles.count("user") == 2
    assert any(msg.get("role") == "tool" and msg.get("tool_call_id") == "call_1" for msg in prepared)
    assert prepared[-1]["content"] == "u4"


def test_summary_strategy_triggers_and_reduces_estimated_tokens():
    config = MemoryConfig(
        last_n_turns=8,
        summary_trigger_pct=0.2,
        summary_target_pct=0.1,
        max_summary_chars=200,
    )
    manager = MemoryManager(config=config)
    session_id = "s_summary"

    history = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "A" * 1200},
        {"role": "assistant", "content": "B" * 1200},
        {"role": "user", "content": "C" * 1200},
        {"role": "assistant", "content": "D" * 1200},
    ]
    manager.commit_messages(session_id, history)

    prepared = manager.prepare_messages(
        session_id,
        [{"role": "user", "content": "latest question"}],
        max_context_window_tokens=1200,
        model="gpt-5",
        summary_generator=lambda prev, msgs, max_chars, model: f"{prev}\nsummary-{len(msgs)}-{model}"[:max_chars],
    )

    info = manager.last_prepare_info
    assert info.get("summary_triggered") is True
    assert info["after_estimated_tokens"] < info["before_estimated_tokens"]
    assert any(
        msg.get("role") == "system"
        and isinstance(msg.get("content"), str)
        and msg["content"].startswith("[MEMORY SUMMARY]")
        for msg in prepared
    )


def test_summary_failure_falls_back_to_last_n_without_interrupting():
    config = MemoryConfig(
        last_n_turns=1,
        summary_trigger_pct=0.2,
        summary_target_pct=0.1,
        max_summary_chars=200,
    )
    summary = SummaryTokenStrategy(
        summary_trigger_pct=config.summary_trigger_pct,
        summary_target_pct=config.summary_target_pct,
        max_summary_chars=config.max_summary_chars,
    )
    last_n = LastNTurnsStrategy(last_n_turns=config.last_n_turns)
    manager = MemoryManager(
        config=config,
        strategy=HybridContextStrategy(
            summary_strategy=summary,
            last_n_strategy=last_n,
            vector_top_k=0,
        ),
    )

    session_id = "s_summary_fallback"
    manager.commit_messages(
        session_id,
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "u1 " * 600},
            {"role": "assistant", "content": "a1 " * 600},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ],
    )

    prepared = manager.prepare_messages(
        session_id,
        [{"role": "user", "content": "u3"}],
        max_context_window_tokens=800,
        model="gpt-5",
        summary_generator=lambda prev, msgs, max_chars, model: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    info = manager.last_prepare_info
    assert "summary_fallback_reason" in info
    assert not any(
        msg.get("role") == "system"
        and isinstance(msg.get("content"), str)
        and msg["content"].startswith("[MEMORY SUMMARY]")
        for msg in prepared
    )
    # last_n=1 should keep only the latest turn (incoming user message).
    assert [msg for msg in prepared if msg.get("role") == "user"][-1]["content"] == "u3"


def test_vector_recall_is_optional_and_non_blocking():
    adapter = _FakeVectorAdapter()
    config = MemoryConfig(vector_adapter=adapter, vector_top_k=2)
    manager = MemoryManager(config=config)
    session_id = "s_vector"

    manager.commit_messages(
        session_id,
        [
            {"role": "user", "content": "project needs redis cache"},
            {"role": "assistant", "content": "noted"},
        ],
    )

    prepared = manager.prepare_messages(
        session_id,
        [{"role": "user", "content": "what cache did we pick?"}],
        max_context_window_tokens=20_000,
        model="gpt-5",
        summary_generator=lambda prev, msgs, max_chars, model: prev,
    )
    assert adapter.searches
    assert any(
        msg.get("role") == "system"
        and isinstance(msg.get("content"), str)
        and msg["content"].startswith("[MEMORY RECALL]")
        for msg in prepared
    )

    failing_adapter = _FakeVectorAdapter(fail_search=True)
    failing_manager = MemoryManager(config=MemoryConfig(vector_adapter=failing_adapter, vector_top_k=2))
    failing_manager.commit_messages(session_id, [{"role": "user", "content": "x"}])
    fallback_prepared = failing_manager.prepare_messages(
        session_id,
        [{"role": "user", "content": "y"}],
        max_context_window_tokens=20_000,
        model="gpt-5",
        summary_generator=lambda prev, msgs, max_chars, model: prev,
    )
    assert isinstance(fallback_prepared, list)
    assert "vector_fallback_reason" in failing_manager.last_prepare_info


def test_broth_emits_memory_events_and_commits_when_session_id_is_set():
    manager = MemoryManager(
        config=MemoryConfig(last_n_turns=2),
        strategy=LastNTurnsStrategy(last_n_turns=2),
    )

    agent = Broth(provider="ollama", model="deepseek-r1:14b", memory_manager=manager)

    def fake_fetch_once(**kwargs):
        del kwargs
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            consumed_tokens=12,
        )

    agent._fetch_once = fake_fetch_once
    events = []
    messages_out, bundle = agent.run(
        messages=[{"role": "user", "content": "hello"}],
        session_id="session_memory_event",
        callback=events.append,
        max_iterations=1,
    )

    event_types = [evt["type"] for evt in events]
    assert "memory_prepare" in event_types
    assert "memory_commit" in event_types
    assert bundle["consumed_tokens"] == 12
    assert messages_out[-1]["content"] == "done"
    state = manager.store.load("session_memory_event")
    assert len(state.get("messages", [])) == len(messages_out)
    # Ensure stored data remains JSON-serializable for future persistence adapters.
    json.dumps(state, ensure_ascii=False)
