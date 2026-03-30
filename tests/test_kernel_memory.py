import copy
import json

from unchain.input.human_input import ASK_USER_QUESTION_TOOL_NAME
from unchain.kernel import KernelLoop, ModelTurnResult
from unchain.memory import KernelMemoryRuntime
from unchain.kernel.types import ToolCall as KernelToolCall
from unchain.memory import InMemorySessionStore, LongTermMemoryConfig, MemoryConfig, MemoryManager
from unchain.tools import Toolkit


class _QueueModelIO:
    def __init__(self, results):
        self.results = list(results)
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(copy.deepcopy(request))
        if not self.results:
            raise AssertionError("unexpected fetch_turn call")
        return self.results.pop(0)


class _VectorAdapter:
    def __init__(self, *, search_results=None, fail_add: bool = False):
        self.search_results = list(search_results or [])
        self.fail_add = fail_add
        self.add_calls = []
        self.search_calls = []

    def add_texts(self, *, session_id, texts, metadatas):
        self.add_calls.append(
            {
                "session_id": session_id,
                "texts": list(texts),
                "metadatas": copy.deepcopy(metadatas),
            }
        )
        if self.fail_add:
            raise RuntimeError("vector add failed")

    def similarity_search(self, *, session_id, query, k, min_score=None):
        self.search_calls.append(
            {
                "session_id": session_id,
                "query": query,
                "k": k,
                "min_score": min_score,
            }
        )
        return copy.deepcopy(self.search_results)


class _ProfileStore:
    def __init__(self, *, initial=None, fail_save: bool = False):
        self._profiles = copy.deepcopy(initial or {})
        self.fail_save = fail_save
        self.save_calls = []

    def load(self, namespace):
        return copy.deepcopy(self._profiles.get(namespace, {}))

    def save(self, namespace, profile):
        self.save_calls.append({"namespace": namespace, "profile": copy.deepcopy(profile)})
        if self.fail_save:
            raise RuntimeError("profile save failed")
        self._profiles[namespace] = copy.deepcopy(profile)


class _LongTermVectorAdapter:
    def __init__(self, *, search_results_by_type=None, fail_add: bool = False):
        self.search_results_by_type = copy.deepcopy(search_results_by_type or {})
        self.fail_add = fail_add
        self.add_calls = []
        self.search_calls = []

    def add_texts(self, *, namespace, texts, metadatas):
        self.add_calls.append(
            {
                "namespace": namespace,
                "texts": list(texts),
                "metadatas": copy.deepcopy(metadatas),
            }
        )
        if self.fail_add:
            raise RuntimeError("long-term vector add failed")

    def similarity_search(self, *, namespace, query, k, filters=None, min_score=None):
        memory_type = (filters or {}).get("memory_type")
        self.search_calls.append(
            {
                "namespace": namespace,
                "query": query,
                "k": k,
                "filters": copy.deepcopy(filters),
                "min_score": min_score,
            }
        )
        return copy.deepcopy(self.search_results_by_type.get(memory_type, []))


def test_memory_bootstrap_merges_history_and_restores_summary():
    store = InMemorySessionStore()
    session_id = "kernel-memory-bootstrap"
    history = [
        {"role": "user", "content": "old user"},
        {"role": "assistant", "content": "old assistant"},
    ]
    store.save(
        session_id,
        {
            "messages": history,
            "summary": "persisted summary",
            "vector_indexed_until": 2,
            "long_term_indexed_until": 4,
            "long_term_pending_turn_count": 1,
        },
    )
    runtime = KernelMemoryRuntime.from_config(store=store)
    loop = KernelLoop()
    loop.attach_memory(runtime)

    state = loop.seed_state(
        [{"role": "user", "content": "new user"}],
        session_id=session_id,
        memory_namespace="mem-ns",
    )
    loop.dispatch_phase(
        state,
        phase="bootstrap",
        event={"resume_mode": False, "toolkit": Toolkit(), "run_id": "run_bootstrap"},
    )

    assert state.transcript == history + [{"role": "user", "content": "new user"}]
    assert state.optimizer_state["llm_summary"]["summary"] == "persisted summary"
    assert state.memory_prepare_info["vector_indexed_until"] == 2
    assert state.memory_state["long_term_indexed_until"] == 4
    assert state.memory_state["long_term_pending_turn_count"] == 1


def test_memory_bootstrap_resume_mode_does_not_duplicate_history():
    store = InMemorySessionStore()
    session_id = "kernel-memory-resume"
    history = [
        {"role": "user", "content": "persisted user"},
        {"role": "assistant", "content": "persisted assistant"},
    ]
    store.save(session_id, {"messages": history, "summary": "persisted summary"})
    runtime = KernelMemoryRuntime.from_config(store=store)
    loop = KernelLoop()
    loop.attach_memory(runtime)

    conversation = history + [
        {
            "type": "function_call",
            "call_id": "call_resume",
            "name": ASK_USER_QUESTION_TOOL_NAME,
            "arguments": json.dumps(
                {
                    "title": "Choose",
                    "question": "Which one?",
                    "selection_mode": "single",
                    "options": [{"label": "One", "value": "one"}],
                }
            ),
        }
    ]
    state = loop.seed_state(
        copy.deepcopy(conversation),
        session_id=session_id,
        memory_namespace="mem-ns",
    )

    loop.dispatch_phase(
        state,
        phase="bootstrap",
        event={"resume_mode": True, "toolkit": Toolkit(), "run_id": "run_resume"},
    )
    transcript_after_bootstrap = copy.deepcopy(state.transcript)
    loop.dispatch_phase(
        state,
        phase="on_resume",
        event={"resume_mode": True, "toolkit": Toolkit(), "run_id": "run_resume"},
    )

    assert transcript_after_bootstrap == conversation
    assert state.transcript == conversation
    assert state.optimizer_state["llm_summary"]["summary"] == "persisted summary"


def test_attach_memory_registers_default_stack_and_restores_history_across_runs():
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)

    first_model_io = _QueueModelIO(
        [
            ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "hi there"}],
                tool_calls=[],
                final_text="hi there",
                response_id="resp_1",
            )
        ]
    )
    first_loop = KernelLoop(model_io=first_model_io)
    first_loop.attach_memory(runtime)

    expected_harnesses = {
        "tool_history_compaction",
        "llm_summary",
        "last_n",
        "memory_short_term_recall",
        "memory_long_term_recall",
        "workspace_pins",
        "memory_bootstrap",
        "memory_commit",
    }
    assert expected_harnesses.issubset({harness.name for harness in first_loop.harnesses})

    first_result = first_loop.run(
        [{"role": "user", "content": "hello"}],
        session_id="shared-session",
        memory_namespace="mem-ns",
        provider="openai",
        model="gpt-4.1",
        max_context_window_tokens=8_192,
    )
    assert first_result.status == "completed"
    assert store.load("shared-session")["messages"][-1]["content"] == "hi there"

    second_model_io = _QueueModelIO(
        [
            ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "again"}],
                tool_calls=[],
                final_text="again",
                response_id="resp_2",
            )
        ]
    )
    second_loop = KernelLoop(model_io=second_model_io)
    second_loop.attach_memory(runtime)

    second_result = second_loop.run(
        [{"role": "user", "content": "what next"}],
        session_id="shared-session",
        memory_namespace="mem-ns",
        provider="openai",
        model="gpt-4.1",
        max_context_window_tokens=8_192,
    )

    assert second_result.status == "completed"
    request_messages = second_model_io.requests[0].messages
    assert any(message.get("content") == "hello" for message in request_messages)
    assert any(message.get("content") == "hi there" for message in request_messages)


def test_short_term_recall_memory_harness_injects_recall_messages():
    vector_adapter = _VectorAdapter(
        search_results=[
            {
                "messages": [
                    {"role": "user", "content": "old question"},
                    {"role": "assistant", "content": "old answer"},
                ]
            }
        ]
    )
    runtime = KernelMemoryRuntime.from_config(
        config=MemoryConfig(vector_adapter=vector_adapter),
        store=InMemorySessionStore(),
    )
    loop = KernelLoop()
    loop.attach_memory(runtime)
    original = [{"role": "user", "content": "new question"}]
    state = loop.seed_state(copy.deepcopy(original), session_id="short-term-session")

    loop.dispatch_phase(
        state,
        phase="before_model",
        event={"toolkit": Toolkit(), "supports_tools": False, "run_id": "run_short_term"},
    )

    assert any(
        message.get("role") == "system"
        and str(message.get("content", "")).startswith("[Recall messages]")
        for message in state.latest_messages()
    )
    assert state.transcript == original
    assert state.memory_prepare_info["short_term_recall_count"] == 2


def test_long_term_recall_injects_profile_and_query_hint_memories():
    profile_store = _ProfileStore(initial={"mem-ns": {"preferences": "blue"}})
    long_term_vector = _LongTermVectorAdapter(
        search_results_by_type={
            "fact": [{"memory_type": "fact", "text": "User likes blue"}],
            "episode": [
                {
                    "memory_type": "episode",
                    "situation": "Before auth failed",
                    "action": "Reset cache",
                    "outcome": "The login recovered",
                }
            ],
            "playbook": [
                {
                    "memory_type": "playbook",
                    "trigger": "Auth fails",
                    "goal": "Restore login flow",
                    "steps": ["Reset cache", "Re-run auth check"],
                }
            ],
        }
    )
    runtime = KernelMemoryRuntime.from_config(
        config=MemoryConfig(
            long_term=LongTermMemoryConfig(
                profile_store=profile_store,
                vector_adapter=long_term_vector,
            )
        ),
        store=InMemorySessionStore(),
    )
    loop = KernelLoop()
    loop.attach_memory(runtime)
    state = loop.seed_state(
        [{"role": "user", "content": "Before that, how to fix auth?"}],
        session_id="long-term-session",
        memory_namespace="mem-ns",
    )

    loop.dispatch_phase(
        state,
        phase="before_model",
        event={"toolkit": Toolkit(), "supports_tools": False, "run_id": "run_long_term"},
    )

    system_contents = "\n".join(
        str(message.get("content", ""))
        for message in state.latest_messages()
        if message.get("role") == "system"
    )
    assert "[MEMORY_PROFILE]" in system_contents
    assert "[MEMORY_FACTS]" in system_contents
    assert "[MEMORY_EPISODES]" in system_contents
    assert "[MEMORY_PLAYBOOKS]" in system_contents


def test_long_term_recall_skips_profile_when_tools_supported_and_respects_query_hints():
    profile_store = _ProfileStore(initial={"mem-ns": {"preferences": "blue"}})
    long_term_vector = _LongTermVectorAdapter(
        search_results_by_type={
            "fact": [{"memory_type": "fact", "text": "User likes blue"}],
            "episode": [
                {
                    "memory_type": "episode",
                    "situation": "Before auth failed",
                    "action": "Reset cache",
                    "outcome": "Recovered",
                }
            ],
            "playbook": [
                {
                    "memory_type": "playbook",
                    "trigger": "Auth fails",
                    "goal": "Restore login flow",
                    "steps": ["Reset cache"],
                }
            ],
        }
    )
    runtime = KernelMemoryRuntime.from_config(
        config=MemoryConfig(
            long_term=LongTermMemoryConfig(
                profile_store=profile_store,
                vector_adapter=long_term_vector,
            )
        ),
        store=InMemorySessionStore(),
    )
    loop = KernelLoop()
    loop.attach_memory(runtime)
    state = loop.seed_state(
        [{"role": "user", "content": "Tell me the answer plainly."}],
        session_id="long-term-tools",
        memory_namespace="mem-ns",
    )

    loop.dispatch_phase(
        state,
        phase="before_model",
        event={"toolkit": Toolkit(), "supports_tools": True, "run_id": "run_long_term_tools"},
    )

    system_contents = "\n".join(
        str(message.get("content", ""))
        for message in state.latest_messages()
        if message.get("role") == "system"
    )
    assert "[MEMORY_PROFILE]" not in system_contents
    assert "[MEMORY_FACTS]" in system_contents
    assert "[MEMORY_EPISODES]" not in system_contents
    assert "[MEMORY_PLAYBOOKS]" not in system_contents


def test_memory_commit_persists_summary_and_advances_index_cursors():
    vector_adapter = _VectorAdapter()
    profile_store = _ProfileStore(initial={"mem-ns": {"identity": "red"}})
    long_term_vector = _LongTermVectorAdapter()

    def extractor(previous_profile, extraction_messages, max_profile_chars, max_fact_items, model):
        assert previous_profile == {"identity": "red"}
        assert extraction_messages[0]["content"] == "user message"
        return {
            "profile_patch": {"preferences": "blue"},
            "facts": [{"text": "User likes blue"}],
            "episodes": [{"situation": "Auth failed", "action": "Reset cache", "outcome": "Recovered"}],
            "playbooks": [{"trigger": "Auth fails", "goal": "Restore login", "steps": ["Reset cache"]}],
        }

    manager = MemoryManager(
        config=MemoryConfig(
            vector_adapter=vector_adapter,
            long_term=LongTermMemoryConfig(
                profile_store=profile_store,
                vector_adapter=long_term_vector,
                extractor=extractor,
                extract_every_n_turns=1,
            ),
        ),
        store=InMemorySessionStore(),
    )
    runtime = KernelMemoryRuntime.from_memory_manager(manager)
    loop = KernelLoop()
    loop.attach_memory(runtime)
    state = loop.seed_state(
        [
            {"role": "user", "content": "user message"},
            {"role": "assistant", "content": "assistant message"},
        ],
        model="gpt-4.1",
        session_id="commit-session",
        memory_namespace="mem-ns",
    )
    state.optimizer_state["llm_summary"] = {"summary": "kernel summary"}

    loop.dispatch_phase(
        state,
        phase="before_commit",
        event={"toolkit": Toolkit(), "run_id": "run_commit"},
    )

    stored_state = runtime.store.load("commit-session")
    assert stored_state["summary"] == "kernel summary"
    assert stored_state["vector_indexed_until"] == 2
    assert stored_state["long_term_indexed_until"] == 2
    assert stored_state["long_term_pending_turn_count"] == 0
    assert profile_store.load("mem-ns")["preferences"] == "blue"
    assert vector_adapter.add_calls
    assert long_term_vector.add_calls
    assert state.memory_commit_info["summary_persisted"] is True


def test_memory_commit_does_not_advance_cursors_when_indexing_fails():
    vector_adapter = _VectorAdapter(fail_add=True)
    profile_store = _ProfileStore(initial={"mem-ns": {"identity": "red"}}, fail_save=True)
    long_term_vector = _LongTermVectorAdapter(fail_add=True)

    def extractor(previous_profile, extraction_messages, max_profile_chars, max_fact_items, model):
        del previous_profile, extraction_messages, max_profile_chars, max_fact_items, model
        return {
            "profile_patch": {"preferences": "blue"},
            "facts": [{"text": "User likes blue"}],
        }

    manager = MemoryManager(
        config=MemoryConfig(
            vector_adapter=vector_adapter,
            long_term=LongTermMemoryConfig(
                profile_store=profile_store,
                vector_adapter=long_term_vector,
                extractor=extractor,
                extract_every_n_turns=1,
            ),
        ),
        store=InMemorySessionStore(),
    )
    runtime = KernelMemoryRuntime.from_memory_manager(manager)
    loop = KernelLoop()
    loop.attach_memory(runtime)
    state = loop.seed_state(
        [
            {"role": "user", "content": "user message"},
            {"role": "assistant", "content": "assistant message"},
        ],
        model="gpt-4.1",
        session_id="commit-failure-session",
        memory_namespace="mem-ns",
    )

    loop.dispatch_phase(
        state,
        phase="before_commit",
        event={"toolkit": Toolkit(), "run_id": "run_commit_failure"},
    )

    stored_state = runtime.store.load("commit-failure-session")
    assert stored_state.get("vector_indexed_until", 0) == 0
    assert stored_state.get("long_term_indexed_until", 0) == 0
    assert state.memory_commit_info["vector_fallback_reason"].startswith("vector_index_failed:")
    assert state.memory_commit_info["long_term_profile_fallback_reason"].startswith("profile_save_failed:")


def test_memory_suspend_skips_commit_and_resume_does_not_duplicate_history():
    store = InMemorySessionStore()
    session_id = "memory-human-input"
    history = [
        {"role": "user", "content": "persisted user"},
        {"role": "assistant", "content": "persisted assistant"},
    ]
    store.save(session_id, {"messages": history})
    runtime = KernelMemoryRuntime.from_config(store=store)
    toolkit = Toolkit()
    toolkit.register(
        lambda **_: {"error": "reserved"},
        name=ASK_USER_QUESTION_TOOL_NAME,
        parameters=[],
    )

    ask_model_io = _QueueModelIO(
        [
            ModelTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": "call_user",
                        "name": ASK_USER_QUESTION_TOOL_NAME,
                        "arguments": json.dumps(
                            {
                                "title": "Choose stack",
                                "question": "Which stack?",
                                "selection_mode": "single",
                                "options": [
                                    {"label": "React", "value": "react"},
                                    {"label": "Vue", "value": "vue"},
                                ],
                            }
                        ),
                    }
                ],
                tool_calls=[
                    KernelToolCall(
                        call_id="call_user",
                        name=ASK_USER_QUESTION_TOOL_NAME,
                        arguments={
                            "title": "Choose stack",
                            "question": "Which stack?",
                            "selection_mode": "single",
                            "options": [
                                {"label": "React", "value": "react"},
                                {"label": "Vue", "value": "vue"},
                            ],
                        },
                    )
                ],
                response_id="resp_ask",
            )
        ]
    )
    suspend_loop = KernelLoop(model_io=ask_model_io)
    suspend_loop.attach_memory(runtime)

    suspended = suspend_loop.run(
        [{"role": "user", "content": "pick a stack"}],
        session_id=session_id,
        memory_namespace="mem-ns",
        provider="openai",
        model="gpt-4.1",
        toolkit=toolkit,
        max_iterations=3,
    )
    assert suspended.status == "awaiting_human_input"
    assert store.load(session_id)["messages"] == history

    resume_model_io = _QueueModelIO(
        [
            ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "React it is"}],
                tool_calls=[],
                final_text="React it is",
                response_id="resp_done",
            )
        ]
    )
    resume_loop = KernelLoop(model_io=resume_model_io)
    resume_loop.attach_memory(runtime)

    resumed = resume_loop.resume_human_input(
        conversation=suspended.messages,
        continuation=suspended.continuation,
        response={"request_id": "call_user", "selected_values": ["react"]},
        session_id=session_id,
        toolkit=toolkit,
    )

    assert resumed.status == "completed"
    assert sum(1 for message in resumed.messages if message.get("content") == "persisted assistant") == 1
    stored_after_resume = store.load(session_id)
    assert stored_after_resume["messages"][-1]["content"] == "React it is"


def test_kernel_memory_runtime_from_memory_manager_reuses_existing_components():
    store = InMemorySessionStore()
    config = MemoryConfig()
    manager = MemoryManager(config=config, store=store)

    runtime = KernelMemoryRuntime.from_memory_manager(manager)

    assert runtime.memory_manager is manager
    assert runtime.store is store
    assert runtime.config is config
