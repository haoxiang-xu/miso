import json
from typing import Any

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
        self.added: list[tuple[str, list[str], list[dict[str, Any]]]] = []
        self.searches: list[tuple[str, str, int]] = []

    def add_texts(self, *, session_id, texts, metadatas):
        self.added.append((session_id, list(texts), [dict(meta) for meta in metadatas]))

    def similarity_search(self, *, session_id, query, k):
        self.searches.append((session_id, query, k))
        if self.fail_search:
            raise RuntimeError("vector search failed")
        return [
            {
                "messages": [
                    {"role": "user", "content": f"recall-user:{query}"},
                    {"role": "assistant", "content": f"recall-assistant:{query}"},
                ]
            },
        ]


def _extract_recall_json_message_array(prepared: list[dict[str, Any]]) -> list[dict[str, str]]:
    for msg in prepared:
        if msg.get("role") != "system" or not isinstance(msg.get("content"), str):
            continue
        content = msg["content"]
        marker = "[Recall messages]"
        if not content.startswith(marker):
            continue
        payload_text = content[len(marker):].strip()
        if not payload_text:
            continue
        try:
            payload = json.loads(payload_text)
        except Exception:
            continue
        if not isinstance(payload, list):
            continue
        if not all(isinstance(item, dict) for item in payload):
            continue
        if not all("role" in item and "content" in item for item in payload):
            continue
        return payload
    return []


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
    recall_payload = _extract_recall_json_message_array(prepared)
    assert recall_payload
    assert recall_payload == [
        {"role": "user", "content": "recall-user:what cache did we pick?"},
        {"role": "assistant", "content": "recall-assistant:what cache did we pick?"},
    ]

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


def test_vector_recall_remains_compatible_with_legacy_string_results():
    class _LegacyVectorAdapter(_FakeVectorAdapter):
        def similarity_search(self, *, session_id, query, k):
            self.searches.append((session_id, query, k))
            return ["legacy_user_note", "", "legacy_assistant_note"]

    adapter = _LegacyVectorAdapter()
    manager = MemoryManager(config=MemoryConfig(vector_adapter=adapter, vector_top_k=2))
    session_id = "s_vector_legacy"
    manager.commit_messages(
        session_id,
        [
            {"role": "user", "content": "legacy_user_note"},
            {"role": "assistant", "content": "legacy_assistant_note"},
        ],
    )

    prepared = manager.prepare_messages(
        session_id,
        [{"role": "user", "content": "follow up"}],
        max_context_window_tokens=20_000,
        model="gpt-5",
        summary_generator=lambda prev, msgs, max_chars, model: prev,
    )

    recall_payload = _extract_recall_json_message_array(prepared)
    assert recall_payload
    assert recall_payload == [
        {"role": "user", "content": "legacy_user_note"},
        {"role": "assistant", "content": "legacy_assistant_note"},
    ]


def test_vector_recall_can_infer_role_from_partial_legacy_text():
    class _LegacyVectorAdapter(_FakeVectorAdapter):
        def similarity_search(self, *, session_id, query, k):
            self.searches.append((session_id, query, k))
            return ["提醒我要报税", "比较感兴趣的是 Qdrant"]

    adapter = _LegacyVectorAdapter()
    manager = MemoryManager(config=MemoryConfig(vector_adapter=adapter, vector_top_k=2))
    session_id = "s_vector_partial_legacy"
    manager.commit_messages(
        session_id,
        [
            {"role": "assistant", "content": "你之前让我记一下等会提醒我要报税。"},
            {"role": "user", "content": "我目前比较感兴趣的是 Qdrant"},
        ],
    )

    prepared = manager.prepare_messages(
        session_id,
        [{"role": "user", "content": "你还记得吗"}],
        max_context_window_tokens=20_000,
        model="gpt-5",
        summary_generator=lambda prev, msgs, max_chars, model: prev,
    )

    recall_payload = _extract_recall_json_message_array(prepared)
    assert recall_payload
    assert recall_payload == [
        {"role": "assistant", "content": "提醒我要报税"},
        {"role": "user", "content": "比较感兴趣的是 Qdrant"},
    ]


def test_vector_commit_indexes_only_completed_turns():
    adapter = _FakeVectorAdapter()
    manager = MemoryManager(config=MemoryConfig(vector_adapter=adapter, vector_top_k=2))
    session_id = "s_vector_turn_indexing"

    manager.commit_messages(
        session_id,
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": ""},
        ],
    )

    assert len(adapter.added) == 1
    _, texts, metadatas = adapter.added[0]
    assert texts == ["user: u1\nassistant: a1"]
    assert metadatas == [{
        "messages": [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ],
        "turn_start_index": 1,
        "turn_end_index": 2,
    }]
    state = manager.store.load(session_id)
    assert state.get("vector_indexed_until") == 3


def test_vector_commit_can_complete_pending_tail_turn_in_next_commit():
    adapter = _FakeVectorAdapter()
    manager = MemoryManager(config=MemoryConfig(vector_adapter=adapter, vector_top_k=2))
    session_id = "s_vector_turn_pending_tail"

    first_conversation = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    manager.commit_messages(session_id, first_conversation)
    assert len(adapter.added) == 1
    assert adapter.added[0][1] == ["user: u1\nassistant: a1"]
    assert manager.store.load(session_id).get("vector_indexed_until") == 2

    second_conversation = first_conversation + [{"role": "assistant", "content": "a2"}]
    manager.commit_messages(session_id, second_conversation)
    assert len(adapter.added) == 2
    assert adapter.added[1][1] == ["user: u2\nassistant: a2"]
    state = manager.store.load(session_id)
    assert state.get("vector_indexed_until") == len(second_conversation)
    assert manager.last_commit_info.get("vector_indexed_turn_count") == 1


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
