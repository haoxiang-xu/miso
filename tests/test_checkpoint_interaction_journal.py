from __future__ import annotations

import copy

import pytest

from unchain.interaction.durable import (
    INTERACTION_JOURNAL_KEY,
    INTERACTION_KIND_TOOL_APPROVAL,
    build_interaction_receipt,
    build_interaction_request,
    record_interaction_receipt,
    validate_interaction_journal,
)
from unchain.kernel import RunState
from unchain.memory import (
    ExecutionCheckpointError,
    ExecutionCheckpointPersistenceError,
    ExecutionCheckpointResumeRequiredError,
    InMemorySessionStore,
    KernelMemoryRuntime,
)
from unchain.memory.checkpoint_state import (
    EXECUTION_CHECKPOINT_KEY,
    build_execution_checkpoint,
    validate_execution_checkpoint,
)


def _interaction_checkpoint(
    *,
    session_id: str,
    revision: int,
    run_id: str,
    occurrence: str,
):
    state = RunState()
    state.seed_messages([{"role": "user", "content": occurrence}])
    state.session_state.session_id = session_id
    state.provider_state.provider = "ollama"
    state.provider_state.model = "fake"
    state.memory_state["session_revision"] = revision
    state.iteration = 1
    state.last_continuation = {
        "type": "durable_interaction",
        "occurrence": occurrence,
    }
    request = build_interaction_request(
        session_id=session_id,
        kind=INTERACTION_KIND_TOOL_APPROVAL,
        source_run_id=run_id,
        occurrence=occurrence,
        payload={"tool_name": "write_file", "arguments": {"path": occurrence}},
        response_contract={
            "type": "object",
            "properties": {"approved": {"type": "boolean"}},
            "required": ["approved"],
        },
        created_revision=revision,
        subject={"call_id": occurrence},
    )
    state.suspend_state.payload = {"interaction_request": request.to_dict()}
    checkpoint = build_execution_checkpoint(
        state,
        status="awaiting_interaction",
        run_id=run_id,
    )
    return checkpoint, request


def _record_receipt(
    store: InMemorySessionStore,
    *,
    session_id: str,
    request,
    response: dict,
) -> int:
    snapshot = store.load_with_revision(session_id)
    state = copy.deepcopy(snapshot.state)
    journal = validate_interaction_journal(state.get(INTERACTION_JOURNAL_KEY))
    receipt = build_interaction_receipt(
        request,
        response,
        submitted_at_ms=100,
    )
    state[INTERACTION_JOURNAL_KEY] = record_interaction_receipt(journal, receipt)
    return store.save_if_revision(session_id, state, snapshot.revision)


def test_checkpoint_v1_adds_only_an_interaction_reference_and_keeps_legacy_valid() -> None:
    checkpoint, request = _interaction_checkpoint(
        session_id="reference",
        revision=0,
        run_id="run-reference",
        occurrence="call-reference",
    )

    assert checkpoint["schema_version"] == 1
    assert checkpoint["status"] == "awaiting_interaction"
    assert checkpoint["interaction_ref"] == {
        "interaction_id": request.interaction_id,
        "request_digest": request.request_digest,
    }
    assert "interaction_request" not in checkpoint
    assert validate_execution_checkpoint(checkpoint) == checkpoint

    legacy_state = RunState()
    legacy_state.seed_messages([{"role": "user", "content": "legacy"}])
    legacy_state.session_state.session_id = "legacy"
    legacy_state.provider_state.provider = "ollama"
    legacy_state.provider_state.model = "fake"
    legacy = build_execution_checkpoint(
        legacy_state,
        status="max_iterations",
        run_id="legacy-run",
    )
    assert "interaction_ref" not in legacy
    assert validate_execution_checkpoint(legacy) == legacy


def test_awaiting_interaction_checkpoint_requires_a_full_request_at_build_time() -> None:
    state = RunState()
    state.seed_messages([{"role": "user", "content": "missing"}])
    state.session_state.session_id = "missing-request"
    state.provider_state.provider = "ollama"
    state.provider_state.model = "fake"
    state.last_continuation = {"type": "durable_interaction"}

    with pytest.raises(ExecutionCheckpointError, match="interaction_request"):
        build_execution_checkpoint(
            state,
            status="awaiting_interaction",
            run_id="run-missing",
        )


def test_checkpoint_and_request_are_one_idempotent_revision_cas() -> None:
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    checkpoint, request = _interaction_checkpoint(
        session_id="atomic-register",
        revision=0,
        run_id="run-atomic",
        occurrence="call-atomic",
    )

    first, first_snapshot = runtime.save_execution_checkpoint_snapshot(
        "atomic-register",
        checkpoint,
        interaction_request=request.to_dict(),
        expected_revision=0,
    )
    retry, retry_snapshot = runtime.save_execution_checkpoint_snapshot(
        "atomic-register",
        copy.deepcopy(checkpoint),
        interaction_request=copy.deepcopy(request.to_dict()),
        expected_revision=0,
    )

    assert retry == first
    assert retry_snapshot.revision == first_snapshot.revision == 1
    persisted = store.load("atomic-register")
    assert persisted[EXECUTION_CHECKPOINT_KEY] == checkpoint
    journal = validate_interaction_journal(persisted[INTERACTION_JOURNAL_KEY])
    assert journal["active_id"] == request.interaction_id
    assert journal["entries"][request.interaction_id]["request"] == request.to_dict()
    assert journal["entries"][request.interaction_id]["checkpoint_id"] == checkpoint[
        "checkpoint_id"
    ]


def test_unanswered_interaction_cannot_be_overwritten_but_answered_one_rolls_forward() -> None:
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    first_checkpoint, first_request = _interaction_checkpoint(
        session_id="roll-forward",
        revision=0,
        run_id="run-first",
        occurrence="call-first",
    )
    _, first_snapshot = runtime.save_execution_checkpoint_snapshot(
        "roll-forward",
        first_checkpoint,
        interaction_request=first_request,
        expected_revision=0,
    )
    second_checkpoint, second_request = _interaction_checkpoint(
        session_id="roll-forward",
        revision=first_snapshot.revision,
        run_id="run-second",
        occurrence="call-second",
    )

    with pytest.raises(
        ExecutionCheckpointResumeRequiredError,
        match="unanswered",
    ):
        runtime.save_execution_checkpoint_snapshot(
            "roll-forward",
            second_checkpoint,
            interaction_request=second_request,
            expected_revision=first_snapshot.revision,
        )
    assert store.load("roll-forward")[EXECUTION_CHECKPOINT_KEY] == first_checkpoint

    answered_revision = _record_receipt(
        store,
        session_id="roll-forward",
        request=first_request,
        response={"approved": True},
    )
    second_checkpoint, second_request = _interaction_checkpoint(
        session_id="roll-forward",
        revision=answered_revision,
        run_id="run-second",
        occurrence="call-second",
    )
    _, second_snapshot = runtime.save_execution_checkpoint_snapshot(
        "roll-forward",
        second_checkpoint,
        interaction_request=second_request,
        expected_revision=answered_revision,
    )

    assert second_snapshot.revision == answered_revision + 1
    journal = validate_interaction_journal(
        store.load("roll-forward")[INTERACTION_JOURNAL_KEY]
    )
    assert journal["active_id"] == second_request.interaction_id
    first_entry = journal["entries"][first_request.interaction_id]
    assert first_entry["application"]["applied_checkpoint_id"] == second_checkpoint[
        "checkpoint_id"
    ]


def test_clear_requires_a_receipt_and_atomically_keeps_an_applied_tombstone() -> None:
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    checkpoint, request = _interaction_checkpoint(
        session_id="clear-interaction",
        revision=0,
        run_id="run-clear",
        occurrence="call-clear",
    )
    _, suspended = runtime.save_execution_checkpoint_snapshot(
        "clear-interaction",
        checkpoint,
        interaction_request=request,
        expected_revision=0,
    )

    with pytest.raises(ExecutionCheckpointResumeRequiredError, match="receipt"):
        runtime.clear_execution_checkpoint_snapshot(
            "clear-interaction",
            expected_checkpoint_id=checkpoint["checkpoint_id"],
            expected_revision=suspended.revision,
        )
    assert EXECUTION_CHECKPOINT_KEY in store.load("clear-interaction")

    answered_revision = _record_receipt(
        store,
        session_id="clear-interaction",
        request=request,
        response={"approved": False},
    )
    cleared, cleared_snapshot = runtime.clear_execution_checkpoint_snapshot(
        "clear-interaction",
        expected_checkpoint_id=checkpoint["checkpoint_id"],
        expected_revision=answered_revision,
    )

    assert cleared is True
    assert EXECUTION_CHECKPOINT_KEY not in cleared_snapshot.state
    journal = validate_interaction_journal(
        cleared_snapshot.state[INTERACTION_JOURNAL_KEY]
    )
    assert journal["active_id"] is None
    assert journal["entries"][request.interaction_id]["application"] == {
        "schema_version": 1,
        "receipt_id": journal["entries"][request.interaction_id]["receipt"][
            "receipt_id"
        ],
        "applied_checkpoint_id": checkpoint["checkpoint_id"],
    }


def test_semantic_commit_clears_checkpoint_and_applies_receipt_in_one_cas() -> None:
    store = InMemorySessionStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    checkpoint, request = _interaction_checkpoint(
        session_id="commit-interaction",
        revision=0,
        run_id="run-commit",
        occurrence="call-commit",
    )
    runtime.save_execution_checkpoint_snapshot(
        "commit-interaction",
        checkpoint,
        interaction_request=request,
        expected_revision=0,
    )
    answered_revision = _record_receipt(
        store,
        session_id="commit-interaction",
        request=request,
        response={"approved": True},
    )

    runtime.commit_transcript(
        session_id="commit-interaction",
        transcript=[
            {"role": "user", "content": "call-commit"},
            {"role": "assistant", "content": "done"},
        ],
        memory_namespace=None,
        model="fake",
        summary_text="",
        expected_revision=answered_revision,
        expected_checkpoint_id=checkpoint["checkpoint_id"],
    )

    persisted = store.load("commit-interaction")
    assert EXECUTION_CHECKPOINT_KEY not in persisted
    assert persisted["messages"][-1]["content"] == "done"
    journal = validate_interaction_journal(persisted[INTERACTION_JOURNAL_KEY])
    assert journal["active_id"] is None
    assert isinstance(
        journal["entries"][request.interaction_id]["application"],
        dict,
    )


def test_failed_checkpoint_cas_never_leaves_a_half_journal() -> None:
    class _FailingStore(InMemorySessionStore):
        def save_if_revision(self, session_id, state, expected_revision):
            del session_id, state, expected_revision
            raise RuntimeError("injected write failure")

    store = _FailingStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    checkpoint, request = _interaction_checkpoint(
        session_id="failed-atomic",
        revision=0,
        run_id="run-failed",
        occurrence="call-failed",
    )

    with pytest.raises(ExecutionCheckpointPersistenceError, match="injected"):
        runtime.save_execution_checkpoint_snapshot(
            "failed-atomic",
            checkpoint,
            interaction_request=request,
            expected_revision=0,
        )

    persisted = store.load("failed-atomic")
    assert EXECUTION_CHECKPOINT_KEY not in persisted
    assert INTERACTION_JOURNAL_KEY not in persisted


def test_failed_clear_cas_keeps_checkpoint_and_unapplied_receipt_together() -> None:
    class _FailingClearStore(InMemorySessionStore):
        fail_writes = False

        def save_if_revision(self, session_id, state, expected_revision):
            if self.fail_writes:
                raise RuntimeError("injected clear failure")
            return super().save_if_revision(session_id, state, expected_revision)

    store = _FailingClearStore()
    runtime = KernelMemoryRuntime.from_config(store=store)
    checkpoint, request = _interaction_checkpoint(
        session_id="failed-clear",
        revision=0,
        run_id="run-failed-clear",
        occurrence="call-failed-clear",
    )
    runtime.save_execution_checkpoint_snapshot(
        "failed-clear",
        checkpoint,
        interaction_request=request,
        expected_revision=0,
    )
    answered_revision = _record_receipt(
        store,
        session_id="failed-clear",
        request=request,
        response={"approved": True},
    )
    store.fail_writes = True

    with pytest.raises(ExecutionCheckpointPersistenceError, match="clear failure"):
        runtime.clear_execution_checkpoint_snapshot(
            "failed-clear",
            expected_checkpoint_id=checkpoint["checkpoint_id"],
            expected_revision=answered_revision,
        )

    persisted = store.load("failed-clear")
    assert persisted[EXECUTION_CHECKPOINT_KEY] == checkpoint
    journal = validate_interaction_journal(persisted[INTERACTION_JOURNAL_KEY])
    entry = journal["entries"][request.interaction_id]
    assert journal["active_id"] == request.interaction_id
    assert isinstance(entry["receipt"], dict)
    assert entry["application"] is None
