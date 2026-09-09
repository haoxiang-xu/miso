from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from unchain.persistence.sqlite_legacy_bootstrap_v2 import (
    LegacyBootstrapConflict,
    LegacyBootstrapPayload,
    LegacyBootstrapPreflight,
    LegacyBootstrapRequest,
    LegacyBootstrapUnavailable,
    LegacyGenerationDescriptor,
    LegacyMessage,
    LegacyRebaseKind,
    LegacyTaskStateDescriptor,
    SQLiteLegacyBootstrapService,
    build_legacy_bootstrap_operation,
)
from unchain.journal import EventCursor, ResourceRef
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store


def _store(tmp_path) -> SQLiteContextV2Store:
    return SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )


def _messages(count: int = 60) -> tuple[LegacyMessage, ...]:
    return tuple(
        LegacyMessage(
            message_id=f"legacy-message-{index}",
            role="user" if index % 2 == 0 else "assistant",
            content=(
                '{"type":"tool_call","call_id":"looks-like-a-tool"}'
                if index == 1
                else f"legacy content {index}"
            ),
        )
        for index in range(count)
    )


def _payload(
    *,
    owner_chat_id: str = "chat-owner-1",
    session_id: str = "runtime-session-1",
    execution_id: str = "runtime-execution-1",
    generation_id: str = "legacy-generation-1",
    attempt_id: str = "legacy-attempt-1",
    source_revision: str = "source-revision-1",
    messages: tuple[LegacyMessage, ...] | None = None,
    rebase_kind: LegacyRebaseKind = LegacyRebaseKind.INITIAL,
    previous_generation_id: str = "",
    preflight: LegacyBootstrapPreflight | None = None,
) -> LegacyBootstrapPayload:
    return LegacyBootstrapPayload(
        owner_chat_id=owner_chat_id,
        source_revision=source_revision,
        messages=_messages() if messages is None else messages,
        generation=LegacyGenerationDescriptor(
            session_id=session_id,
            execution_id=execution_id,
            generation_id=generation_id,
            attempt_id=attempt_id,
            rebase_kind=rebase_kind,
            previous_generation_id=previous_generation_id,
        ),
        task_state=LegacyTaskStateDescriptor(
            descriptor_id="task-state-1",
            revision=3,
            descriptor_sha256="a" * 64,
            refs=(
                ResourceRef("task_state", "pinned-task-state-1", 3),
                ResourceRef("artifact", "requirements-1", 1),
            ),
        ),
        preflight=(
            preflight
            if preflight is not None
            else LegacyBootstrapPreflight(
                proof_id="legacy-preflight-1",
                no_unfinished_durable_checkpoint=True,
                no_pending_interaction=True,
                host_snapshot_sanitized=True,
            )
        ),
    )


def _request(
    payload: LegacyBootstrapPayload,
    *,
    operation_id: str = "legacy-bootstrap-operation-1",
) -> LegacyBootstrapRequest:
    return LegacyBootstrapRequest(
        payload=payload,
        operation=build_legacy_bootstrap_operation(
            operation_id=operation_id,
            payload=payload,
        ),
    )


def test_bootstrap_imports_full_legacy_history_without_forging_tool_events(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteLegacyBootstrapService(store)
    payload = _payload()

    receipt = service.bootstrap(_request(payload))
    snapshot = store.bind_execution(payload.generation.execution_id).capture_snapshot()

    assert receipt.ready_for_sticky_v2 is True
    assert receipt.duplicate is False
    assert receipt.capture_status == "legacy_partial"
    assert receipt.owner_chat_id != receipt.execution_id
    assert receipt.owner_chat_id != receipt.session_id
    assert receipt.message_count == 60
    assert receipt.task_state == payload.task_state
    assert receipt.first_cursor == EventCursor(
        snapshot.events[0].store_seq,
        snapshot.events[0].event_id,
    )
    assert receipt.last_cursor == EventCursor(
        snapshot.events[-1].store_seq,
        snapshot.events[-1].event_id,
    )
    assert len(snapshot.events) == 60
    assert {event.event_type for event in snapshot.events} == {
        "message.user",
        "message.assistant",
    }
    assert all(event.attempt == payload.generation.attempt for event in snapshot.events)
    assert all(
        event.payload["run_id"] == payload.generation.attempt_id
        and event.payload["legacy_provenance"]["capture_status"] == "legacy_partial"
        and event.payload["legacy_provenance"]["owner_chat_id"] == payload.owner_chat_id
        for event in snapshot.events
    )
    tool_looking = snapshot.events[1]
    assert tool_looking.event_type == "message.assistant"
    assert tool_looking.payload["message"] == {
        "role": "assistant",
        "content": '{"type":"tool_call","call_id":"looks-like-a-tool"}',
    }
    assert service.current(payload.owner_chat_id) == receipt


@pytest.mark.parametrize("role", ["tool", "system", "developer", "trace", ""])
def test_bootstrap_rejects_non_chat_roles_before_storage(tmp_path, role) -> None:
    store = _store(tmp_path)
    service = SQLiteLegacyBootstrapService(store)

    with pytest.raises((TypeError, ValueError), match="role"):
        message = LegacyMessage("legacy-message-1", role, "unsafe")
        service.bootstrap(_request(_payload(messages=(message,))))

    assert store.bind_execution("runtime-execution-1").capture_snapshot().events == ()


def test_bootstrap_rejects_empty_history_and_failed_host_preflight(tmp_path) -> None:
    service = SQLiteLegacyBootstrapService(_store(tmp_path))

    with pytest.raises((TypeError, ValueError), match="messages|history"):
        service.bootstrap(_request(_payload(messages=())))

    for proof in (
        LegacyBootstrapPreflight(
            "preflight-checkpoint-pending",
            no_unfinished_durable_checkpoint=False,
            no_pending_interaction=True,
            host_snapshot_sanitized=True,
        ),
        LegacyBootstrapPreflight(
            "preflight-interaction-pending",
            no_unfinished_durable_checkpoint=True,
            no_pending_interaction=False,
            host_snapshot_sanitized=True,
        ),
        LegacyBootstrapPreflight(
            "preflight-unsanitized",
            no_unfinished_durable_checkpoint=True,
            no_pending_interaction=True,
            host_snapshot_sanitized=False,
        ),
    ):
        with pytest.raises(LegacyBootstrapUnavailable, match="preflight|sanitized"):
            service.bootstrap(_request(_payload(preflight=proof)))


def test_restart_and_same_revision_hash_replay_do_not_duplicate_events(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    payload = _payload()
    first = SQLiteLegacyBootstrapService(store).bootstrap(_request(payload))

    reopened_store = _store(tmp_path)
    reopened = SQLiteLegacyBootstrapService(reopened_store)
    duplicate = reopened.bootstrap(
        _request(payload, operation_id="legacy-bootstrap-operation-replay")
    )

    assert duplicate == replace(first, duplicate=True)
    assert (
        len(
            reopened_store.bind_execution(payload.generation.execution_id)
            .capture_snapshot()
            .events
        )
        == 60
    )
    assert reopened.current(payload.owner_chat_id) == first


def test_operation_payload_and_source_revision_drift_fail_closed(tmp_path) -> None:
    store = _store(tmp_path)
    service = SQLiteLegacyBootstrapService(store)
    payload = _payload()
    request = _request(payload)
    service.bootstrap(request)

    changed_messages = payload.messages[:-1] + (
        replace(payload.messages[-1], content="changed content"),
    )
    changed_same_revision = replace(payload, messages=changed_messages)
    with pytest.raises(LegacyBootstrapConflict, match="revision|payload"):
        service.bootstrap(
            _request(
                changed_same_revision,
                operation_id="legacy-bootstrap-operation-drift",
            )
        )

    changed_operation_digest = replace(
        request.operation,
        payload_sha256="f" * 64,
    )
    with pytest.raises(LegacyBootstrapConflict, match="operation|payload"):
        service.bootstrap(
            LegacyBootstrapRequest(payload=payload, operation=changed_operation_digest)
        )

    new_revision_without_rebase = replace(
        payload,
        source_revision="source-revision-2",
        generation=replace(
            payload.generation,
            generation_id="legacy-generation-2",
            attempt_id="legacy-attempt-2",
        ),
    )
    with pytest.raises(LegacyBootstrapConflict, match="rebase|current"):
        service.bootstrap(
            _request(
                new_revision_without_rebase,
                operation_id="legacy-bootstrap-operation-new-revision",
            )
        )


def test_manifest_failure_rolls_back_events_operation_and_head(tmp_path) -> None:
    store = _store(tmp_path)
    service = SQLiteLegacyBootstrapService(store)
    payload = _payload(messages=_messages(4))

    with sqlite3.connect(store.database_path) as connection:
        connection.executescript(
            """
            CREATE TRIGGER fail_legacy_manifest
            BEFORE INSERT ON legacy_bootstrap_manifests
            BEGIN
                SELECT RAISE(ABORT, 'injected manifest failure');
            END;
            """
        )

    with pytest.raises(LegacyBootstrapUnavailable, match="persist|SQLite|manifest"):
        service.bootstrap(_request(payload))

    assert (
        store.bind_execution(payload.generation.execution_id).capture_snapshot().events
        == ()
    )
    assert service.current(payload.owner_chat_id) is None
    with sqlite3.connect(store.database_path) as connection:
        operation_count = connection.execute(
            """
            SELECT COUNT(*) FROM operations
            WHERE execution_id = ?
            """,
            (payload.generation.execution_id,),
        ).fetchone()[0]
        manifest_count = connection.execute(
            "SELECT COUNT(*) FROM legacy_bootstrap_manifests"
        ).fetchone()[0]
    assert operation_count == 0
    assert manifest_count == 0


def test_restart_receipt_rejects_corrupted_bootstrap_operation_binding(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    payload = _payload(messages=_messages(2))
    request = _request(payload)
    SQLiteLegacyBootstrapService(store).bootstrap(request)

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            UPDATE operations SET target_kind = 'journal_event'
            WHERE execution_id = ? AND operation_id = ?
            """,
            (payload.generation.execution_id, request.operation.operation_id),
        )

    reopened = SQLiteLegacyBootstrapService(_store(tmp_path))
    with pytest.raises(LegacyBootstrapUnavailable, match="operation|binding"):
        reopened.current(payload.owner_chat_id)


def test_explicit_rebase_switches_current_and_preserves_old_generation(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteLegacyBootstrapService(store)
    initial_payload = _payload(messages=_messages(4))
    first = service.bootstrap(_request(initial_payload))
    rebase_payload = _payload(
        source_revision="source-revision-2",
        generation_id="legacy-generation-2",
        attempt_id="legacy-attempt-2",
        messages=(LegacyMessage("edited-message-1", "user", "edited prompt"),),
        rebase_kind=LegacyRebaseKind.EDIT,
        previous_generation_id=initial_payload.generation.generation_id,
    )

    second = service.bootstrap(
        _request(rebase_payload, operation_id="legacy-bootstrap-operation-rebase")
    )

    assert second.generation_id == "legacy-generation-2"
    assert service.current(initial_payload.owner_chat_id) == second
    snapshot = store.bind_execution(
        initial_payload.generation.execution_id
    ).capture_snapshot()
    assert [event.attempt.generation.generation_id for event in snapshot.events] == [
        first.generation_id,
        first.generation_id,
        first.generation_id,
        first.generation_id,
        second.generation_id,
    ]
    assert (
        service.receipt_for_generation(
            initial_payload.owner_chat_id,
            first.generation_id,
        )
        == first
    )

    stale = replace(
        rebase_payload,
        source_revision="source-revision-3",
        generation=replace(
            rebase_payload.generation,
            generation_id="legacy-generation-3",
            attempt_id="legacy-attempt-3",
        ),
    )
    with pytest.raises(LegacyBootstrapConflict, match="current|previous"):
        service.bootstrap(
            _request(stale, operation_id="legacy-bootstrap-operation-stale-rebase")
        )

    with pytest.raises(LegacyBootstrapConflict, match="current|revision"):
        service.bootstrap(
            _request(
                initial_payload,
                operation_id="legacy-bootstrap-operation-stale-replay",
            )
        )


def test_owner_scope_is_independent_from_runtime_session_and_execution(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteLegacyBootstrapService(store)
    first_payload = _payload(messages=_messages(2))
    second_payload = _payload(
        owner_chat_id="chat-owner-2",
        session_id="runtime-session-2",
        execution_id="runtime-execution-2",
        generation_id="legacy-generation-owner-2",
        attempt_id="legacy-attempt-owner-2",
        messages=_messages(2),
    )

    first = service.bootstrap(_request(first_payload))
    second = service.bootstrap(
        _request(second_payload, operation_id="legacy-bootstrap-operation-owner-2")
    )

    assert service.current(first.owner_chat_id) == first
    assert service.current(second.owner_chat_id) == second
    assert first.execution_id != second.execution_id
    assert len(store.bind_execution(first.execution_id).capture_snapshot().events) == 2
    assert len(store.bind_execution(second.execution_id).capture_snapshot().events) == 2


def test_owner_cannot_share_an_existing_execution_journal(tmp_path) -> None:
    store = _store(tmp_path)
    service = SQLiteLegacyBootstrapService(store)
    first_payload = _payload(messages=_messages(2))
    first = service.bootstrap(_request(first_payload))
    foreign_owner = _payload(
        owner_chat_id="chat-owner-foreign",
        session_id="runtime-session-foreign",
        execution_id=first.execution_id,
        generation_id="legacy-generation-foreign",
        attempt_id="legacy-attempt-foreign",
        source_revision="source-revision-foreign",
        messages=_messages(2),
    )

    with pytest.raises(LegacyBootstrapConflict, match="execution|owner|scope"):
        service.bootstrap(
            _request(
                foreign_owner,
                operation_id="legacy-bootstrap-operation-foreign-owner",
            )
        )

    assert service.current(foreign_owner.owner_chat_id) is None
    assert len(store.bind_execution(first.execution_id).capture_snapshot().events) == 2
