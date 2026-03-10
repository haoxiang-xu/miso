import json
from typing import Any

from miso import broth as Broth
from miso.broth import ProviderTurnResult
from miso.memory import (
    HybridContextStrategy,
    LastNTurnsStrategy,
    LongTermMemoryConfig,
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


class _FakeLongTermProfileStore:
    def __init__(self, *, fail_load: bool = False, fail_save: bool = False):
        self.fail_load = fail_load
        self.fail_save = fail_save
        self.profiles: dict[str, dict[str, Any]] = {}

    def load(self, namespace: str) -> dict[str, Any]:
        if self.fail_load:
            raise RuntimeError("profile load failed")
        return json.loads(json.dumps(self.profiles.get(namespace, {}), ensure_ascii=False))

    def save(self, namespace: str, profile: dict[str, Any]) -> None:
        if self.fail_save:
            raise RuntimeError("profile save failed")
        self.profiles[namespace] = json.loads(json.dumps(profile, ensure_ascii=False))


class _FakeLongTermVectorAdapter:
    def __init__(self, *, fail_search: bool = False, fail_add: bool = False):
        self.fail_search = fail_search
        self.fail_add = fail_add
        self.added: list[tuple[str, list[str], list[dict[str, Any]]]] = []
        self.searches: list[tuple[str, str, int, dict[str, Any] | None]] = []
        self.records: dict[str, list[dict[str, Any]]] = {}

    def add_texts(self, *, namespace, texts, metadatas):
        if self.fail_add:
            raise RuntimeError("long-term add failed")
        self.added.append((namespace, list(texts), [dict(meta) for meta in metadatas]))
        bucket = self.records.setdefault(namespace, [])
        for text, metadata in zip(texts, metadatas):
            bucket.append({"text": text, **dict(metadata)})

    def similarity_search(self, *, namespace, query, k, filters=None):
        self.searches.append((namespace, query, k, filters))
        if self.fail_search:
            raise RuntimeError("long-term search failed")
        items = [dict(item) for item in self.records.get(namespace, [])]
        if isinstance(filters, dict) and filters:
            filtered = []
            for item in items:
                if all(item.get(key) == value for key, value in filters.items()):
                    filtered.append(item)
            items = filtered
        return items[:k]


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


def _extract_tagged_json_payload(prepared: list[dict[str, Any]], marker: str):
    for msg in prepared:
        if msg.get("role") != "system" or not isinstance(msg.get("content"), str):
            continue
        content = msg["content"]
        if not content.startswith(marker):
            continue
        payload_text = content[len(marker):].strip()
        if not payload_text:
            continue
        try:
            return json.loads(payload_text)
        except Exception:
            return None
    return None


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


def test_long_term_memory_recalls_profile_and_facts_across_sessions_with_namespace():
    profile_store = _FakeLongTermProfileStore()
    adapter = _FakeLongTermVectorAdapter()
    manager = MemoryManager(
        config=MemoryConfig(
            long_term=LongTermMemoryConfig(
                profile_store=profile_store,
                vector_adapter=adapter,
                vector_top_k=2,
                max_fact_items=4,
            )
        )
    )

    def extractor(previous_profile, messages, max_profile_chars, max_fact_items, model):
        del previous_profile, messages, max_profile_chars, max_fact_items, model
        return {
            "profile_patch": {"preferences": {"answer_style": "concise"}},
            "facts": [
                {"kind": "fact", "text": "User prefers concise answers."},
                {"kind": "event", "text": "Selected Qdrant as the default vector store."},
            ],
        }

    manager.commit_messages(
        "session_a",
        [
            {"role": "user", "content": "Please keep answers concise."},
            {"role": "assistant", "content": "Noted."},
        ],
        memory_namespace="user_123",
        model="gpt-5",
        long_term_extractor=extractor,
    )

    prepared = manager.prepare_messages(
        "session_b",
        [{"role": "user", "content": "what do you remember?"}],
        max_context_window_tokens=20_000,
        model="gpt-5",
        memory_namespace="user_123",
    )

    profile_payload = _extract_tagged_json_payload(prepared, "[MEMORY_PROFILE]")
    facts_payload = _extract_tagged_json_payload(prepared, "[MEMORY_FACTS]")

    assert profile_payload == {"preferences": {"answer_style": "concise"}}
    assert [item["text"] for item in facts_payload] == [
        "User prefers concise answers.",
        "Selected Qdrant as the default vector store.",
    ]


def test_long_term_memory_falls_back_to_session_id_when_namespace_missing():
    profile_store = _FakeLongTermProfileStore()
    adapter = _FakeLongTermVectorAdapter()
    manager = MemoryManager(
        config=MemoryConfig(
            long_term=LongTermMemoryConfig(
                profile_store=profile_store,
                vector_adapter=adapter,
            )
        )
    )

    manager.commit_messages(
        "session_only_namespace",
        [
            {"role": "user", "content": "I prefer bullet points."},
            {"role": "assistant", "content": "Understood."},
        ],
        model="gpt-5",
        long_term_extractor=lambda *args: {
            "profile_patch": {"preferences": {"format": "bullets"}},
            "facts": [{"kind": "fact", "text": "User prefers bullet points."}],
        },
    )

    prepared_same_session = manager.prepare_messages(
        "session_only_namespace",
        [{"role": "user", "content": "remind me"}],
        max_context_window_tokens=20_000,
        model="gpt-5",
    )
    prepared_other_session = manager.prepare_messages(
        "different_session",
        [{"role": "user", "content": "remind me"}],
        max_context_window_tokens=20_000,
        model="gpt-5",
    )

    assert _extract_tagged_json_payload(prepared_same_session, "[MEMORY_PROFILE]") == {
        "preferences": {"format": "bullets"}
    }
    assert _extract_tagged_json_payload(prepared_other_session, "[MEMORY_PROFILE]") is None


def test_long_term_commit_indexes_only_new_complete_turns():
    profile_store = _FakeLongTermProfileStore()
    adapter = _FakeLongTermVectorAdapter()
    manager = MemoryManager(
        config=MemoryConfig(
            long_term=LongTermMemoryConfig(
                profile_store=profile_store,
                vector_adapter=adapter,
                max_fact_items=2,
            )
        )
    )

    def extractor(previous_profile, messages, max_profile_chars, max_fact_items, model):
        del previous_profile, max_profile_chars, max_fact_items, model
        return {
            "profile_patch": {},
            "facts": [{"kind": "event", "text": messages[-1]["content"]}],
        }

    first_conversation = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    manager.commit_messages(
        "lt_incremental",
        first_conversation,
        memory_namespace="user_incremental",
        model="gpt-5",
        long_term_extractor=extractor,
    )
    assert len(adapter.added) == 1
    assert adapter.added[0][1] == ["a1"]
    assert manager.store.load("lt_incremental").get("long_term_indexed_until") == 2

    second_conversation = first_conversation + [{"role": "assistant", "content": "a2"}]
    manager.commit_messages(
        "lt_incremental",
        second_conversation,
        memory_namespace="user_incremental",
        model="gpt-5",
        long_term_extractor=extractor,
    )
    assert len(adapter.added) == 2
    assert adapter.added[1][1] == ["a2"]
    assert manager.store.load("lt_incremental").get("long_term_indexed_until") == len(second_conversation)


def test_long_term_profile_patch_merges_and_overrides_nested_keys():
    profile_store = _FakeLongTermProfileStore()
    adapter = _FakeLongTermVectorAdapter()
    manager = MemoryManager(
        config=MemoryConfig(
            long_term=LongTermMemoryConfig(
                profile_store=profile_store,
                vector_adapter=adapter,
            )
        )
    )

    manager.commit_messages(
        "profile_s1",
        [
            {"role": "user", "content": "Use concise English."},
            {"role": "assistant", "content": "Noted."},
        ],
        memory_namespace="user_profile",
        model="gpt-5",
        long_term_extractor=lambda *args: {
            "profile_patch": {"preferences": {"tone": "concise", "language": "en"}},
            "facts": [],
        },
    )
    manager.commit_messages(
        "profile_s2",
        [
            {"role": "user", "content": "Actually be more detailed."},
            {"role": "assistant", "content": "Will do."},
        ],
        memory_namespace="user_profile",
        model="gpt-5",
        long_term_extractor=lambda *args: {
            "profile_patch": {"preferences": {"tone": "detailed"}},
            "facts": [],
        },
    )

    assert profile_store.load("user_profile") == {
        "preferences": {"tone": "detailed", "language": "en"}
    }


def test_long_term_recall_is_optional_and_non_blocking():
    profile_store = _FakeLongTermProfileStore()
    failing_search_adapter = _FakeLongTermVectorAdapter(fail_search=True)
    manager = MemoryManager(
        config=MemoryConfig(
            long_term=LongTermMemoryConfig(
                profile_store=profile_store,
                vector_adapter=failing_search_adapter,
            )
        )
    )
    profile_store.save("user_lt", {"preferences": {"style": "concise"}})
    prepared = manager.prepare_messages(
        "lt_prepare_fail",
        [{"role": "user", "content": "what do you know?"}],
        max_context_window_tokens=20_000,
        model="gpt-5",
        memory_namespace="user_lt",
    )
    assert isinstance(prepared, list)
    assert "long_term_vector_fallback_reason" in manager.last_prepare_info

    failing_add_adapter = _FakeLongTermVectorAdapter(fail_add=True)
    commit_manager = MemoryManager(
        config=MemoryConfig(
            long_term=LongTermMemoryConfig(
                profile_store=_FakeLongTermProfileStore(),
                vector_adapter=failing_add_adapter,
            )
        )
    )
    commit_manager.commit_messages(
        "lt_commit_fail",
        [
            {"role": "user", "content": "Remember that I like weekly digests."},
            {"role": "assistant", "content": "Okay."},
        ],
        memory_namespace="user_lt_fail",
        model="gpt-5",
        long_term_extractor=lambda *args: {
            "profile_patch": {"preferences": {"digest": "weekly"}},
            "facts": [{"kind": "fact", "text": "User likes weekly digests."}],
        },
    )
    assert "long_term_vector_fallback_reason" in commit_manager.last_commit_info
    assert commit_manager.store.load("lt_commit_fail").get("long_term_indexed_until") is None


def test_long_term_memory_can_coexist_with_short_term_recall():
    short_term_adapter = _FakeVectorAdapter()
    long_term_profile_store = _FakeLongTermProfileStore()
    long_term_adapter = _FakeLongTermVectorAdapter()
    manager = MemoryManager(
        config=MemoryConfig(
            vector_adapter=short_term_adapter,
            vector_top_k=2,
            long_term=LongTermMemoryConfig(
                profile_store=long_term_profile_store,
                vector_adapter=long_term_adapter,
                vector_top_k=2,
            ),
        )
    )
    session_id = "hybrid_memory_session"

    manager.commit_messages(
        session_id,
        [
            {"role": "user", "content": "We picked Redis."},
            {"role": "assistant", "content": "Yes, Redis is selected."},
        ],
        memory_namespace="user_hybrid",
        model="gpt-5",
        long_term_extractor=lambda *args: {
            "profile_patch": {"preferences": {"cache": "redis"}},
            "facts": [{"kind": "fact", "text": "Redis was selected as the cache."}],
        },
    )

    prepared = manager.prepare_messages(
        session_id,
        [{"role": "user", "content": "what cache did we pick?"}],
        max_context_window_tokens=20_000,
        model="gpt-5",
        memory_namespace="user_hybrid",
        summary_generator=lambda prev, msgs, max_chars, model: prev,
    )
    assert _extract_recall_json_message_array(prepared)
    assert _extract_tagged_json_payload(prepared, "[MEMORY_PROFILE]") == {
        "preferences": {"cache": "redis"}
    }
    facts_payload = _extract_tagged_json_payload(prepared, "[MEMORY_FACTS]")
    assert [item["text"] for item in facts_payload] == ["Redis was selected as the cache."]


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


def test_broth_passes_memory_namespace_to_long_term_memory():
    profile_store = _FakeLongTermProfileStore()
    adapter = _FakeLongTermVectorAdapter()
    manager = MemoryManager(
        config=MemoryConfig(
            long_term=LongTermMemoryConfig(
                profile_store=profile_store,
                vector_adapter=adapter,
            )
        )
    )
    agent = Broth(provider="ollama", model="deepseek-r1:14b", memory_manager=manager)

    captured_requests: list[list[dict[str, Any]]] = []

    def fake_fetch_once(**kwargs):
        captured_requests.append(json.loads(json.dumps(kwargs["messages"], ensure_ascii=False)))
        return ProviderTurnResult(
            assistant_messages=[{"role": "assistant", "content": "done"}],
            tool_calls=[],
            final_text="done",
            consumed_tokens=8,
        )

    agent._fetch_once = fake_fetch_once
    agent._extract_long_term_memory = lambda *args, **kwargs: {
        "profile_patch": {"preferences": {"namespace": "shared_user"}},
        "facts": [{"kind": "fact", "text": "Shared namespace fact."}],
    }

    agent.run(
        messages=[{"role": "user", "content": "remember this"}],
        session_id="broth_lt_s1",
        memory_namespace="shared_user",
        max_iterations=1,
    )
    agent.run(
        messages=[{"role": "user", "content": "what do you know?"}],
        session_id="broth_lt_s2",
        memory_namespace="shared_user",
        max_iterations=1,
    )

    second_request = captured_requests[-1]
    profile_payload = _extract_tagged_json_payload(second_request, "[MEMORY_PROFILE]")
    assert profile_payload == {"preferences": {"namespace": "shared_user"}}


def test_long_term_extraction_waits_for_configured_number_of_complete_turns():
    profile_store = _FakeLongTermProfileStore()
    adapter = _FakeLongTermVectorAdapter()
    manager = MemoryManager(
        config=MemoryConfig(
            long_term=LongTermMemoryConfig(
                profile_store=profile_store,
                vector_adapter=adapter,
                extract_every_n_turns=2,
            )
        )
    )

    extractor_calls = []

    def extractor(previous_profile, messages, max_profile_chars, max_fact_items, model):
        del previous_profile, max_profile_chars, max_fact_items, model
        extractor_calls.append([dict(message) for message in messages])
        return {
            "profile_patch": {},
            "facts": [{"subtype": "fact", "text": messages[-1]["content"]}],
            "episodes": [],
            "playbooks": [],
        }

    manager.commit_messages(
        "lt_deferred",
        [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ],
        memory_namespace="shared",
        model="gpt-5",
        long_term_extractor=extractor,
    )
    assert extractor_calls == []
    assert manager.last_commit_info.get("long_term_extraction_deferred") is True
    assert manager.store.load("lt_deferred").get("long_term_pending_turn_count") == 1

    manager.commit_messages(
        "lt_deferred",
        [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ],
        memory_namespace="shared",
        model="gpt-5",
        long_term_extractor=extractor,
    )
    assert len(extractor_calls) == 1
    assert [message["content"] for message in extractor_calls[0]] == ["u1", "a1", "u2", "a2"]
    assert manager.store.load("lt_deferred").get("long_term_pending_turn_count") == 0
    assert adapter.added[-1][1] == ["a2"]


def test_long_term_prepare_can_recall_typed_memory_blocks():
    profile_store = _FakeLongTermProfileStore()
    adapter = _FakeLongTermVectorAdapter()
    manager = MemoryManager(
        config=MemoryConfig(
            long_term=LongTermMemoryConfig(
                profile_store=profile_store,
                vector_adapter=adapter,
                vector_top_k=4,
                episode_top_k=2,
                playbook_top_k=2,
            )
        )
    )
    profile_store.save("typed_user", {"preferences": {"tone": "concise"}})
    adapter.records["typed_user"] = [
        {"memory_type": "fact", "text": "Project uses Qdrant for long-term memory."},
        {
            "memory_type": "episode",
            "situation": "Asked how memory was wired in PuPu.",
            "action": "Explained the sidecar integration path.",
            "outcome": "User accepted the design.",
            "text": "Situation: Asked how memory was wired in PuPu.\nAction: Explained the sidecar integration path.\nOutcome: User accepted the design.",
        },
        {
            "memory_type": "playbook",
            "trigger": "Need to debug memory integration.",
            "goal": "Verify storage, sidecar wiring, and retrieval.",
            "steps": ["Inspect memory.py", "Inspect memory_factory.py", "Run targeted tests"],
            "caveats": "Do not index raw transcripts into long-term memory.",
            "text": "Trigger: Need to debug memory integration.\nGoal: Verify storage, sidecar wiring, and retrieval.\nSteps:\n1. Inspect memory.py\n2. Inspect memory_factory.py\n3. Run targeted tests\nCaveats: Do not index raw transcripts into long-term memory.",
        },
    ]

    prepared = manager.prepare_messages(
        "typed_session",
        [{"role": "user", "content": "how do we debug this memory workflow again?"}],
        max_context_window_tokens=20_000,
        model="gpt-5",
        memory_namespace="typed_user",
    )

    assert _extract_tagged_json_payload(prepared, "[MEMORY_PROFILE]") == {
        "preferences": {"tone": "concise"}
    }
    facts_payload = _extract_tagged_json_payload(prepared, "[MEMORY_FACTS]")
    episodes_payload = _extract_tagged_json_payload(prepared, "[MEMORY_EPISODES]")
    playbooks_payload = _extract_tagged_json_payload(prepared, "[MEMORY_PLAYBOOKS]")
    assert [item["text"] for item in facts_payload] == ["Project uses Qdrant for long-term memory."]
    assert episodes_payload[0]["outcome"] == "User accepted the design."
    assert playbooks_payload[0]["trigger"] == "Need to debug memory integration."
