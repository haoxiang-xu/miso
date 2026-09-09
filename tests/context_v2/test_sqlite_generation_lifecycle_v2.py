from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from inspect import signature
from threading import Barrier

import pytest

from unchain.journal import OperationRef
from unchain.persistence.sqlite_generation_lifecycle_v2 import (
    HostGenerationAttemptBindingIntent,
    HostGenerationAttemptBindingRequest,
    HostGenerationConflict,
    HostGenerationTransition,
    HostGenerationTransitionKind,
    HostGenerationTransitionRequest,
    SQLiteHostGenerationLifecycleV2,
    build_host_generation_attempt_binding_operation,
    build_host_generation_transition_operation,
)
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store


def _store(tmp_path) -> SQLiteContextV2Store:
    return SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )


def _transition(
    *,
    owner_chat_id: str = "chat-1",
    execution_id: str = "execution-1",
    session_id: str = "session-1",
    generation_id: str = "generation-1",
    kind: HostGenerationTransitionKind = HostGenerationTransitionKind.INITIAL,
    previous_generation_id: str = "",
    expected_revision: int = 0,
) -> HostGenerationTransition:
    return HostGenerationTransition(
        owner_chat_id=owner_chat_id,
        execution_id=execution_id,
        session_id=session_id,
        generation_id=generation_id,
        kind=kind,
        previous_generation_id=previous_generation_id,
        expected_revision=expected_revision,
    )


def _transition_request(
    transition: HostGenerationTransition,
    *,
    operation_id: str,
) -> HostGenerationTransitionRequest:
    return HostGenerationTransitionRequest(
        transition=transition,
        operation=build_host_generation_transition_operation(
            operation_id=operation_id,
            transition=transition,
        ),
    )


def _attempt_request(
    intent: HostGenerationAttemptBindingIntent,
    *,
    operation_id: str,
) -> HostGenerationAttemptBindingRequest:
    return HostGenerationAttemptBindingRequest(
        intent=intent,
        operation=build_host_generation_attempt_binding_operation(
            operation_id=operation_id,
            intent=intent,
        ),
    )


def test_initial_generation_is_host_supplied_and_becomes_revision_one(tmp_path) -> None:
    store = _store(tmp_path)
    service = SQLiteHostGenerationLifecycleV2(store)
    transition = _transition()

    receipt = service.advance(
        _transition_request(transition, operation_id="generation-initial-1")
    )

    assert receipt.duplicate is False
    assert receipt.record.owner_chat_id == "chat-1"
    assert receipt.record.execution_id == "execution-1"
    assert receipt.record.session_id == "session-1"
    assert receipt.record.generation_id == "generation-1"
    assert receipt.record.kind is HostGenerationTransitionKind.INITIAL
    assert receipt.record.previous_generation_id == ""
    assert receipt.record.revision == 1
    assert receipt.head.current_generation_id == "generation-1"
    assert receipt.head.revision == 1
    assert (
        service.current(
            owner_chat_id="chat-1",
            execution_id="execution-1",
            session_id="session-1",
        )
        == receipt.head
    )
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT execution_id FROM executions WHERE execution_id = ?",
            ("execution-1",),
        ).fetchone() == ("execution-1",)


def test_edit_and_regenerate_append_immutable_records_and_advance_head(
    tmp_path,
) -> None:
    service = SQLiteHostGenerationLifecycleV2(_store(tmp_path))
    initial = service.advance(
        _transition_request(_transition(), operation_id="generation-initial-1")
    )
    edit = _transition(
        generation_id="generation-2",
        kind=HostGenerationTransitionKind.EDIT,
        previous_generation_id="generation-1",
        expected_revision=1,
    )
    edit_receipt = service.advance(
        _transition_request(edit, operation_id="generation-edit-1")
    )
    regenerate = _transition(
        generation_id="generation-3",
        kind=HostGenerationTransitionKind.REGENERATE,
        previous_generation_id="generation-2",
        expected_revision=2,
    )
    regenerate_receipt = service.advance(
        _transition_request(regenerate, operation_id="generation-regenerate-1")
    )

    assert initial.record == service.generation(
        owner_chat_id="chat-1",
        execution_id="execution-1",
        session_id="session-1",
        generation_id="generation-1",
    )
    assert edit_receipt.record == service.generation(
        owner_chat_id="chat-1",
        execution_id="execution-1",
        session_id="session-1",
        generation_id="generation-2",
    )
    assert edit_receipt.record.previous_generation_id == "generation-1"
    assert edit_receipt.record.revision == 2
    assert regenerate_receipt.record.previous_generation_id == "generation-2"
    assert regenerate_receipt.record.revision == 3
    assert regenerate_receipt.head.current_generation_id == "generation-3"
    assert regenerate_receipt.head.revision == 3


def test_transition_shapes_reject_implicit_or_non_advancing_rebases() -> None:
    with pytest.raises(ValueError, match="expected_revision"):
        replace(_transition(), expected_revision=1)

    with pytest.raises((TypeError, ValueError), match="previous_generation_id"):
        _transition(
            generation_id="generation-2",
            kind=HostGenerationTransitionKind.EDIT,
            previous_generation_id="",
            expected_revision=1,
        )

    with pytest.raises(ValueError, match="new generation"):
        _transition(
            kind=HostGenerationTransitionKind.REGENERATE,
            previous_generation_id="generation-1",
            expected_revision=1,
        )


def test_rebase_requires_exact_current_generation_and_head_revision(tmp_path) -> None:
    service = SQLiteHostGenerationLifecycleV2(_store(tmp_path))
    service.advance(
        _transition_request(_transition(), operation_id="generation-initial-1")
    )

    stale_previous = _transition(
        generation_id="generation-2",
        kind=HostGenerationTransitionKind.EDIT,
        previous_generation_id="not-current",
        expected_revision=1,
    )
    with pytest.raises(HostGenerationConflict, match="previous|current"):
        service.advance(
            _transition_request(stale_previous, operation_id="generation-edit-stale")
        )

    stale_revision = _transition(
        generation_id="generation-2",
        kind=HostGenerationTransitionKind.EDIT,
        previous_generation_id="generation-1",
        expected_revision=2,
    )
    with pytest.raises(HostGenerationConflict, match="revision|current"):
        service.advance(
            _transition_request(
                stale_revision,
                operation_id="generation-edit-stale-revision",
            )
        )

    assert (
        service.current(
            owner_chat_id="chat-1",
            execution_id="execution-1",
            session_id="session-1",
        ).current_generation_id
        == "generation-1"
    )


def test_operation_replay_is_idempotent_even_after_the_head_advances(tmp_path) -> None:
    store = _store(tmp_path)
    service = SQLiteHostGenerationLifecycleV2(store)
    initial_request = _transition_request(
        _transition(),
        operation_id="generation-initial-1",
    )
    initial = service.advance(initial_request)
    edit = _transition(
        generation_id="generation-2",
        kind=HostGenerationTransitionKind.EDIT,
        previous_generation_id="generation-1",
        expected_revision=1,
    )
    service.advance(_transition_request(edit, operation_id="generation-edit-1"))

    duplicate = service.advance(initial_request)

    assert duplicate == replace(initial, duplicate=True)
    assert (
        service.current(
            owner_chat_id="chat-1",
            execution_id="execution-1",
            session_id="session-1",
        ).current_generation_id
        == "generation-2"
    )
    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM host_generation_records"
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM host_generation_operations"
            ).fetchone()[0]
            == 2
        )


def test_operation_id_reuse_or_caller_hash_drift_fails_closed(tmp_path) -> None:
    service = SQLiteHostGenerationLifecycleV2(_store(tmp_path))
    initial = _transition()
    request = _transition_request(initial, operation_id="generation-operation-1")
    service.advance(request)

    changed = _transition(
        generation_id="generation-2",
        kind=HostGenerationTransitionKind.EDIT,
        previous_generation_id="generation-1",
        expected_revision=1,
    )
    with pytest.raises(HostGenerationConflict, match="operation|payload"):
        service.advance(
            _transition_request(changed, operation_id="generation-operation-1")
        )

    with pytest.raises(HostGenerationConflict, match="operation|payload"):
        service.advance(
            HostGenerationTransitionRequest(
                transition=initial,
                operation=OperationRef("generation-operation-2", "f" * 64),
            )
        )


def test_owner_execution_and_session_binding_is_exact(tmp_path) -> None:
    service = SQLiteHostGenerationLifecycleV2(_store(tmp_path))
    service.advance(
        _transition_request(_transition(), operation_id="generation-initial-1")
    )

    with pytest.raises(HostGenerationConflict, match="binding|scope"):
        service.current(
            owner_chat_id="chat-1",
            execution_id="another-execution",
            session_id="session-1",
        )
    with pytest.raises(HostGenerationConflict, match="binding|scope"):
        service.current(
            owner_chat_id="chat-1",
            execution_id="execution-1",
            session_id="another-session",
        )

    changed_session = _transition(
        session_id="another-session",
        generation_id="generation-2",
        kind=HostGenerationTransitionKind.EDIT,
        previous_generation_id="generation-1",
        expected_revision=1,
    )
    with pytest.raises(HostGenerationConflict, match="binding|scope"):
        service.advance(
            _transition_request(
                changed_session,
                operation_id="generation-edit-wrong-session",
            )
        )

    reused_execution = _transition(
        owner_chat_id="chat-2",
        generation_id="generation-other-chat",
    )
    with pytest.raises(HostGenerationConflict, match="identity|conflict"):
        service.advance(
            _transition_request(
                reused_execution,
                operation_id="generation-other-chat",
            )
        )


def test_wal_and_cold_restart_restore_the_exact_current_head(tmp_path) -> None:
    store = _store(tmp_path)
    first = SQLiteHostGenerationLifecycleV2(store)
    first.advance(
        _transition_request(_transition(), operation_id="generation-initial-1")
    )
    edit = _transition(
        generation_id="generation-2",
        kind=HostGenerationTransitionKind.EDIT,
        previous_generation_id="generation-1",
        expected_revision=1,
    )
    edit_receipt = first.advance(
        _transition_request(edit, operation_id="generation-edit-1")
    )
    attempt_intent = HostGenerationAttemptBindingIntent(
        owner_chat_id="chat-1",
        execution_id="execution-1",
        session_id="session-1",
        generation_id="generation-2",
        attempt_id="attempt-after-edit",
        expected_revision=2,
    )
    attempt_request = _attempt_request(
        attempt_intent,
        operation_id="attempt-after-edit-binding",
    )
    attempt_receipt = first.bind_current_attempt(attempt_request)

    reopened = SQLiteHostGenerationLifecycleV2(_store(tmp_path))

    assert (
        reopened.current(
            owner_chat_id="chat-1",
            execution_id="execution-1",
            session_id="session-1",
        )
        == edit_receipt.head
    )
    assert (
        reopened.generation(
            owner_chat_id="chat-1",
            execution_id="execution-1",
            session_id="session-1",
            generation_id="generation-1",
        ).revision
        == 1
    )
    assert (
        reopened.attempt_binding(
            owner_chat_id="chat-1",
            execution_id="execution-1",
            session_id="session-1",
            attempt_id="attempt-after-edit",
        )
        == attempt_receipt.binding
    )
    assert reopened.bind_current_attempt(attempt_request) == replace(
        attempt_receipt,
        duplicate=True,
    )
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_attempt_binding_accepts_only_the_explicit_current_generation(tmp_path) -> None:
    service = SQLiteHostGenerationLifecycleV2(_store(tmp_path))
    service.advance(
        _transition_request(_transition(), operation_id="generation-initial-1")
    )
    intent = HostGenerationAttemptBindingIntent(
        owner_chat_id="chat-1",
        execution_id="execution-1",
        session_id="session-1",
        generation_id="generation-1",
        attempt_id="attempt-1",
        expected_revision=1,
    )

    receipt = service.bind_current_attempt(
        _attempt_request(intent, operation_id="attempt-binding-1")
    )

    assert receipt.duplicate is False
    assert receipt.binding.owner_chat_id == "chat-1"
    assert receipt.binding.generation_id == "generation-1"
    assert receipt.binding.attempt_id == "attempt-1"
    assert receipt.binding.head_revision == 1
    assert (
        service.attempt_binding(
            owner_chat_id="chat-1",
            execution_id="execution-1",
            session_id="session-1",
            attempt_id="attempt-1",
        )
        == receipt.binding
    )
    assert set(signature(HostGenerationAttemptBindingIntent).parameters) == {
        "owner_chat_id",
        "execution_id",
        "session_id",
        "generation_id",
        "attempt_id",
        "expected_revision",
    }


def test_attempt_binding_rejects_stale_head_and_cannot_be_rebound(tmp_path) -> None:
    service = SQLiteHostGenerationLifecycleV2(_store(tmp_path))
    service.advance(
        _transition_request(_transition(), operation_id="generation-initial-1")
    )
    first_intent = HostGenerationAttemptBindingIntent(
        owner_chat_id="chat-1",
        execution_id="execution-1",
        session_id="session-1",
        generation_id="generation-1",
        attempt_id="attempt-1",
        expected_revision=1,
    )
    first_request = _attempt_request(
        first_intent,
        operation_id="attempt-binding-1",
    )
    first = service.bind_current_attempt(first_request)
    edit = _transition(
        generation_id="generation-2",
        kind=HostGenerationTransitionKind.EDIT,
        previous_generation_id="generation-1",
        expected_revision=1,
    )
    service.advance(_transition_request(edit, operation_id="generation-edit-1"))

    assert service.bind_current_attempt(first_request) == replace(
        first,
        duplicate=True,
    )

    stale = replace(first_intent, attempt_id="attempt-2")
    with pytest.raises(HostGenerationConflict, match="current|revision"):
        service.bind_current_attempt(
            _attempt_request(stale, operation_id="attempt-binding-stale")
        )

    rebound = replace(
        first_intent,
        generation_id="generation-2",
        expected_revision=2,
    )
    with pytest.raises(HostGenerationConflict, match="attempt|binding"):
        service.bind_current_attempt(
            _attempt_request(rebound, operation_id="attempt-binding-rebound")
        )


def test_attempt_binding_operation_hash_and_id_are_enforced(tmp_path) -> None:
    service = SQLiteHostGenerationLifecycleV2(_store(tmp_path))
    service.advance(
        _transition_request(_transition(), operation_id="generation-initial-1")
    )
    intent = HostGenerationAttemptBindingIntent(
        owner_chat_id="chat-1",
        execution_id="execution-1",
        session_id="session-1",
        generation_id="generation-1",
        attempt_id="attempt-1",
        expected_revision=1,
    )
    with pytest.raises(HostGenerationConflict, match="operation|payload"):
        service.bind_current_attempt(
            HostGenerationAttemptBindingRequest(
                intent=intent,
                operation=OperationRef("attempt-binding-bad-hash", "f" * 64),
            )
        )

    service.bind_current_attempt(
        _attempt_request(intent, operation_id="attempt-binding-1")
    )
    changed = replace(intent, attempt_id="attempt-2")
    with pytest.raises(HostGenerationConflict, match="operation|payload"):
        service.bind_current_attempt(
            _attempt_request(changed, operation_id="attempt-binding-1")
        )


def test_concurrent_generation_compare_and_swap_has_exactly_one_winner(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    first_service = SQLiteHostGenerationLifecycleV2(store)
    second_service = SQLiteHostGenerationLifecycleV2(store)
    first_service.advance(
        _transition_request(_transition(), operation_id="generation-initial-1")
    )
    transitions = (
        _transition(
            generation_id="generation-edit-a",
            kind=HostGenerationTransitionKind.EDIT,
            previous_generation_id="generation-1",
            expected_revision=1,
        ),
        _transition(
            generation_id="generation-edit-b",
            kind=HostGenerationTransitionKind.REGENERATE,
            previous_generation_id="generation-1",
            expected_revision=1,
        ),
    )
    barrier = Barrier(2)

    def advance_after_barrier(index: int):
        barrier.wait()
        service = first_service if index == 0 else second_service
        return service.advance(
            _transition_request(
                transitions[index],
                operation_id=f"generation-concurrent-{index}",
            )
        )

    successes = []
    conflicts = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(advance_after_barrier, index) for index in range(2)]
        for future in as_completed(futures):
            try:
                successes.append(future.result())
            except HostGenerationConflict as exc:
                conflicts.append(exc)

    assert len(successes) == 1
    assert len(conflicts) == 1
    assert successes[0].head.revision == 2
    assert (
        first_service.current(
            owner_chat_id="chat-1",
            execution_id="execution-1",
            session_id="session-1",
        )
        == successes[0].head
    )
    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM host_generation_records"
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM host_generation_operations"
            ).fetchone()[0]
            == 2
        )
