from __future__ import annotations

import copy

import pytest

from unchain.kernel import RunState
from unchain.memory import (
    ExecutionCheckpointCompatibilityError,
    ExecutionCheckpointResumeRequiredError,
    InMemorySessionStore,
    KernelMemoryRuntime,
)
from unchain.memory.checkpoint_state import (
    EXECUTION_CHECKPOINT_DOMAIN_KEY,
    build_execution_checkpoint,
)
from unchain.memory.revision import SessionRevisionConflictError
from unchain.workspace.pins import load_workspace_pins, save_workspace_pins


def _bootstrap(runtime: KernelMemoryRuntime, session_id: str) -> int:
    _, _, info, _ = runtime.bootstrap_session(
        session_id=session_id,
        memory_namespace=None,
        incoming_messages=[],
        resume_mode=False,
        provider="ollama",
        model="fake",
    )
    revision = info.get("session_revision")
    assert isinstance(revision, int)
    return revision


def _checkpoint(
    *,
    session_id: str,
    revision: int,
    run_id: str,
    content: str,
) -> dict:
    state = RunState()
    state.seed_messages([{"role": "user", "content": content}])
    state.session_state.session_id = session_id
    state.provider_state.provider = "ollama"
    state.provider_state.model = "fake"
    state.memory_state["session_revision"] = revision
    state.iteration = 1
    state.run_status = "max_iterations"
    return build_execution_checkpoint(
        state,
        status="max_iterations",
        run_id=run_id,
    )


def test_stale_semantic_commit_cannot_overwrite_newer_worker() -> None:
    store = InMemorySessionStore()
    store.save("shared", {"messages": [{"role": "user", "content": "base"}]})
    first = KernelMemoryRuntime.from_config(store=store)
    second = KernelMemoryRuntime.from_config(store=store)
    first_revision = _bootstrap(first, "shared")
    second_revision = _bootstrap(second, "shared")
    assert first_revision == second_revision

    first.commit_transcript(
        session_id="shared",
        transcript=[
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "winner"},
        ],
        memory_namespace=None,
        model="fake",
        summary_text="",
        expected_revision=first_revision,
    )

    with pytest.raises(SessionRevisionConflictError):
        second.commit_transcript(
            session_id="shared",
            transcript=[
                {"role": "user", "content": "second"},
                {"role": "assistant", "content": "stale"},
            ],
            memory_namespace=None,
            model="fake",
            summary_text="",
            expected_revision=second_revision,
        )

    assert store.load("shared")["messages"][-1]["content"] == "winner"


@pytest.mark.parametrize("checkpoint_wins", [True, False])
def test_checkpoint_and_semantic_commit_race_never_lose_winner_state(
    checkpoint_wins: bool,
) -> None:
    store = InMemorySessionStore()
    store.save("race", {"messages": [{"role": "user", "content": "base"}]})
    checkpoint_runtime = KernelMemoryRuntime.from_config(store=store)
    commit_runtime = KernelMemoryRuntime.from_config(store=store)
    checkpoint_revision = _bootstrap(checkpoint_runtime, "race")
    commit_revision = _bootstrap(commit_runtime, "race")
    checkpoint = _checkpoint(
        session_id="race",
        revision=checkpoint_revision,
        run_id="checkpoint-worker",
        content="checkpoint transcript",
    )

    if checkpoint_wins:
        checkpoint_runtime.save_execution_checkpoint_snapshot(
            "race",
            checkpoint,
            expected_revision=checkpoint_revision,
        )
        with pytest.raises(SessionRevisionConflictError):
            commit_runtime.commit_transcript(
                session_id="race",
                transcript=[{"role": "assistant", "content": "stale commit"}],
                memory_namespace=None,
                model="fake",
                summary_text="",
                expected_revision=commit_revision,
            )
        state = store.load("race")
        assert state["execution_checkpoint"]["checkpoint_id"] == checkpoint["checkpoint_id"]
        assert state["messages"] == [{"role": "user", "content": "base"}]
        return

    commit_runtime.commit_transcript(
        session_id="race",
        transcript=[{"role": "assistant", "content": "committed"}],
        memory_namespace=None,
        model="fake",
        summary_text="",
        expected_revision=commit_revision,
    )
    with pytest.raises(SessionRevisionConflictError):
        checkpoint_runtime.save_execution_checkpoint_snapshot(
            "race",
            checkpoint,
            expected_revision=checkpoint_revision,
        )
    state = store.load("race")
    assert state["messages"] == [{"role": "assistant", "content": "committed"}]
    assert "execution_checkpoint" not in state


def test_repeated_checkpoint_write_is_idempotent_without_revision_bump() -> None:
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    revision = _bootstrap(runtime, "idempotent")
    checkpoint = _checkpoint(
        session_id="idempotent",
        revision=revision,
        run_id="same-run",
        content="same transcript",
    )

    first, first_snapshot = runtime.save_execution_checkpoint_snapshot(
        "idempotent",
        checkpoint,
        expected_revision=revision,
    )
    second, second_snapshot = runtime.save_execution_checkpoint_snapshot(
        "idempotent",
        copy.deepcopy(checkpoint),
        expected_revision=revision,
    )

    assert first == second
    assert second_snapshot.revision == first_snapshot.revision


def test_same_checkpoint_does_not_let_stale_worker_adopt_unrelated_revision() -> None:
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    base_revision = _bootstrap(runtime, "checkpoint-fence")
    checkpoint = _checkpoint(
        session_id="checkpoint-fence",
        revision=base_revision,
        run_id="old-run",
        content="old checkpoint",
    )
    _, checkpoint_snapshot = runtime.save_execution_checkpoint_snapshot(
        "checkpoint-fence",
        checkpoint,
        expected_revision=base_revision,
    )
    assert isinstance(checkpoint_snapshot.revision, int)

    unrelated_state = copy.deepcopy(checkpoint_snapshot.state)
    unrelated_state["messages"] = [
        {"role": "assistant", "content": "newer semantic writer"}
    ]
    store.save_if_revision(
        "checkpoint-fence",
        unrelated_state,
        checkpoint_snapshot.revision,
    )
    newer_snapshot = store.load_with_revision("checkpoint-fence")
    assert newer_snapshot.revision == checkpoint_snapshot.revision + 1
    assert newer_snapshot.state["execution_checkpoint"]["checkpoint_id"] == checkpoint[
        "checkpoint_id"
    ]

    with pytest.raises(SessionRevisionConflictError):
        runtime.save_execution_checkpoint_snapshot(
            "checkpoint-fence",
            checkpoint,
            expected_revision=checkpoint_snapshot.revision,
        )

    assert store.load("checkpoint-fence")["messages"] == [
        {"role": "assistant", "content": "newer semantic writer"}
    ]


def test_direct_memory_manager_cannot_bypass_execution_checkpoint() -> None:
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    base_revision = _bootstrap(runtime, "manager-checkpoint-guard")
    checkpoint = _checkpoint(
        session_id="manager-checkpoint-guard",
        revision=base_revision,
        run_id="guarded-run",
        content="guarded",
    )
    runtime.save_execution_checkpoint_snapshot(
        "manager-checkpoint-guard",
        checkpoint,
        expected_revision=base_revision,
    )
    manager = runtime.memory_manager

    with pytest.raises(
        ExecutionCheckpointResumeRequiredError,
        match="durable execution checkpoint|resume",
    ):
        manager.prepare_messages(
            "manager-checkpoint-guard",
            [{"role": "user", "content": "bypass"}],
            max_context_window_tokens=4096,
            model="fake",
        )
    with pytest.raises(
        ExecutionCheckpointResumeRequiredError,
        match="durable execution checkpoint|resume",
    ):
        manager.commit_messages(
            "manager-checkpoint-guard",
            [{"role": "assistant", "content": "bypass"}],
        )


def test_stale_worker_cannot_clear_a_newer_checkpoint() -> None:
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    base_revision = _bootstrap(runtime, "clear-race")
    old_checkpoint = _checkpoint(
        session_id="clear-race",
        revision=base_revision,
        run_id="old-run",
        content="old",
    )
    _, old_snapshot = runtime.save_execution_checkpoint_snapshot(
        "clear-race",
        old_checkpoint,
        expected_revision=base_revision,
    )
    assert isinstance(old_snapshot.revision, int)

    new_checkpoint = _checkpoint(
        session_id="clear-race",
        revision=old_snapshot.revision,
        run_id="new-run",
        content="new",
    )
    _, new_snapshot = runtime.save_execution_checkpoint_snapshot(
        "clear-race",
        new_checkpoint,
        expected_revision=old_snapshot.revision,
    )

    with pytest.raises(SessionRevisionConflictError):
        runtime.clear_execution_checkpoint_snapshot(
            "clear-race",
            expected_checkpoint_id=old_checkpoint["checkpoint_id"],
            expected_revision=old_snapshot.revision,
        )

    persisted = runtime.load_execution_checkpoint("clear-race")
    assert persisted is not None
    assert persisted["checkpoint_id"] == new_checkpoint["checkpoint_id"]
    assert runtime.load_session_snapshot("clear-race").revision == new_snapshot.revision


def test_completed_transcript_and_checkpoint_clear_are_one_atomic_write() -> None:
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    base_revision = _bootstrap(runtime, "atomic-completion")
    checkpoint = _checkpoint(
        session_id="atomic-completion",
        revision=base_revision,
        run_id="suspended-run",
        content="before completion",
    )
    _, suspended_snapshot = runtime.save_execution_checkpoint_snapshot(
        "atomic-completion",
        checkpoint,
        expected_revision=base_revision,
    )
    final_transcript = [
        {"role": "user", "content": "before completion"},
        {"role": "assistant", "content": "already completed"},
    ]

    commit_info, persisted_state = runtime.commit_transcript(
        session_id="atomic-completion",
        transcript=final_transcript,
        memory_namespace=None,
        model="fake",
        summary_text="",
        expected_revision=suspended_snapshot.revision,
        expected_checkpoint_id=checkpoint["checkpoint_id"],
    )

    assert commit_info["execution_checkpoint_cleared"] is True
    assert persisted_state["messages"] == final_transcript
    assert "execution_checkpoint" not in persisted_state
    assert EXECUTION_CHECKPOINT_DOMAIN_KEY not in persisted_state
    assert store.load("atomic-completion") == persisted_state

    cold_runtime = KernelMemoryRuntime.from_config(store=store)
    restored, _, prepare_info, _ = cold_runtime.bootstrap_session(
        session_id="atomic-completion",
        memory_namespace=None,
        incoming_messages=[],
        resume_mode=False,
        provider="ollama",
        model="fake",
    )
    assert prepare_info["execution_checkpoint_restored"] is False
    assert restored == final_transcript


def test_legacy_stale_checkpoint_cannot_roll_back_newer_semantic_history() -> None:
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    checkpoint = _checkpoint(
        session_id="legacy-stale-checkpoint",
        revision=0,
        run_id="old-run",
        content="before completion",
    )
    store.save(
        "legacy-stale-checkpoint",
        {
            "messages": [
                {"role": "user", "content": "before completion"},
                {"role": "assistant", "content": "newer final answer"},
            ],
            "execution_checkpoint": checkpoint,
        },
    )

    with pytest.raises(
        ExecutionCheckpointCompatibilityError,
        match="semantic history diverges|stale checkpoint",
    ):
        runtime.bootstrap_session(
            session_id="legacy-stale-checkpoint",
            memory_namespace=None,
            incoming_messages=[],
            resume_mode=False,
            provider="ollama",
            model="fake",
        )


def test_legacy_store_reports_best_effort_consistency() -> None:
    class LegacyStore:
        def __init__(self) -> None:
            self.state: dict[str, dict] = {}

        def load(self, session_id: str) -> dict:
            return copy.deepcopy(self.state.get(session_id, {}))

        def save(self, session_id: str, state: dict) -> None:
            self.state[session_id] = copy.deepcopy(state)

    runtime = KernelMemoryRuntime.from_config(store=LegacyStore())
    _, _, info, _ = runtime.bootstrap_session(
        session_id="legacy",
        memory_namespace=None,
        incoming_messages=[],
        resume_mode=False,
        provider="ollama",
        model="fake",
    )

    assert info["session_revision"] is None
    assert info["session_revision_supported"] is False
    assert info["session_consistency"] == "best_effort"


def test_workspace_pin_write_preserves_newer_memory_and_checkpoint_fields() -> None:
    store = InMemorySessionStore()
    store.save(
        "pins",
        {
            "messages": [{"role": "user", "content": "base"}],
            "execution_checkpoint": {"checkpoint_id": "keep-me"},
        },
    )
    stale_state, _ = load_workspace_pins(store, "pins")
    snapshot = store.load_with_revision("pins")
    newer_state = copy.deepcopy(snapshot.state)
    newer_state["summary"] = "newer memory write"
    store.save_if_revision("pins", newer_state, snapshot.revision)

    save_workspace_pins(
        store,
        "pins",
        stale_state,
        [{"pin_id": "pin-1", "path": "/tmp/demo.py"}],
    )

    persisted = store.load("pins")
    assert persisted["summary"] == "newer memory write"
    assert persisted["messages"] == [{"role": "user", "content": "base"}]
    assert persisted["execution_checkpoint"] == {"checkpoint_id": "keep-me"}
    assert persisted["workspace_pins"] == [
        {"pin_id": "pin-1", "path": "/tmp/demo.py"}
    ]


def test_workspace_pin_write_legacy_store_preserves_newer_memory_state() -> None:
    class LegacyStore:
        def __init__(self) -> None:
            self.state: dict[str, dict] = {}

        def load(self, session_id: str) -> dict:
            return copy.deepcopy(self.state.get(session_id, {}))

        def save(self, session_id: str, state: dict) -> None:
            self.state[session_id] = copy.deepcopy(state)

    store = LegacyStore()
    store.save(
        "legacy-pins",
        {
            "messages": [{"role": "user", "content": "base"}],
            "summary": "old summary",
        },
    )
    stale_state, _ = load_workspace_pins(store, "legacy-pins")
    store.save(
        "legacy-pins",
        {
            "messages": [
                {"role": "user", "content": "base"},
                {"role": "assistant", "content": "newer reply"},
            ],
            "summary": "newer memory write",
        },
    )

    save_workspace_pins(
        store,
        "legacy-pins",
        stale_state,
        [{"pin_id": "pin-legacy", "path": "/tmp/legacy.py"}],
    )

    persisted = store.load("legacy-pins")
    assert persisted["summary"] == "newer memory write"
    assert persisted["messages"] == [
        {"role": "user", "content": "base"},
        {"role": "assistant", "content": "newer reply"},
    ]
    assert persisted["workspace_pins"] == [
        {"pin_id": "pin-legacy", "path": "/tmp/legacy.py"}
    ]
