from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest

from unchain.context import (
    ArtifactService,
    ContextCompileRequest,
    SourceMessageCursor,
    resolve_context_budget,
)
from unchain.context.coordinator import ContextCompileCoordinator
from unchain.journal import (
    AttemptRef,
    EventRange,
    GenerationRef,
    OperationRef,
    ResourceRef,
    SemanticEventDraft,
)
from unchain.persistence.sqlite_context_compiler_v2 import (
    SQLiteContextCompilerV2Store,
)
from unchain.persistence.sqlite_generation_lifecycle_v2 import (
    HostGenerationTransitionKind,
    SQLiteHostGenerationLifecycleV2,
)
from unchain.persistence.sqlite_generation_rebase_v2 import (
    GenerationRebaseConflict,
    GenerationRebaseError,
    GenerationRebaseIntent,
    GenerationRebaseKind,
    GenerationRebasePreflight,
    GenerationRebasePreflightBlocked,
    GenerationRebaseRequest,
    GenerationRebaseUnavailable,
    GenerationSnapshotMessage,
    GenerationTaskStateDescriptor,
    SQLiteGenerationRebaseV2Service,
    build_generation_rebase_operation,
)
from unchain.persistence.sqlite_legacy_bootstrap_v2 import (
    SQLiteLegacyBootstrapService,
)
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store


OWNER = "chat-generation-rebase"
SESSION = "session-generation-rebase"
EXECUTION = "execution-generation-rebase"


def _store(root: Path) -> SQLiteContextV2Store:
    return SQLiteContextV2Store(
        database_path=root / "context_v2.sqlite3",
        object_directory=root / "objects",
    )


def _messages(prefix: str, *contents: tuple[str, str]):
    return tuple(
        GenerationSnapshotMessage(
            f"{prefix}-message-{index}",
            role,
            content,
        )
        for index, (role, content) in enumerate(contents, start=1)
    )


def _intent(
    *,
    kind: GenerationRebaseKind = GenerationRebaseKind.CREATE,
    generation_id: str = "generation-1",
    attempt_id: str = "attempt-1",
    previous_generation_id: str = "",
    expected_head_revision: int = 0,
    source_revision: str = "source-revision-1",
    messages=None,
    task_state=None,
) -> GenerationRebaseIntent:
    return GenerationRebaseIntent(
        owner_chat_id=OWNER,
        session_id=SESSION,
        execution_id=EXECUTION,
        generation_id=generation_id,
        attempt_id=attempt_id,
        kind=kind,
        previous_generation_id=previous_generation_id,
        expected_head_revision=expected_head_revision,
        source_revision=source_revision,
        messages=(
            messages
            if messages is not None
            else _messages(
                generation_id,
                ("user", f"prompt for {generation_id}"),
            )
        ),
        preflight=GenerationRebasePreflight(
            proof_id=f"preflight-{generation_id}",
            host_snapshot_sanitized=True,
        ),
        task_state=task_state,
    )


def _request(
    intent: GenerationRebaseIntent,
    *,
    operation_id: str,
) -> GenerationRebaseRequest:
    return GenerationRebaseRequest(
        intent=intent,
        operation=build_generation_rebase_operation(
            operation_id=operation_id,
            intent=intent,
        ),
    )


def _next(
    previous,
    *,
    kind: GenerationRebaseKind,
    ordinal: int,
    messages=None,
):
    intent = _intent(
        kind=kind,
        generation_id=f"generation-{ordinal}",
        attempt_id=f"attempt-{ordinal}",
        previous_generation_id=previous.generation_id,
        expected_head_revision=previous.head_revision,
        source_revision=f"source-revision-{ordinal}",
        messages=messages,
    )
    return intent, _request(intent, operation_id=f"rebase-operation-{ordinal}")


def _append_event(
    store: SQLiteContextV2Store,
    receipt,
    *,
    event_id: str,
    event_type: str,
    interaction_id: str | None = None,
    attempt_id: str | None = None,
    operation_id: str | None = None,
    payload: dict | None = None,
    resource_refs: tuple[ResourceRef, ...] = (),
):
    active_attempt_id = attempt_id or receipt.attempt_id
    attempt = AttemptRef(
        GenerationRef(EXECUTION, receipt.generation_id),
        active_attempt_id,
    )
    event_payload = {
        "run_id": active_attempt_id,
        **(payload or {}),
    }
    if interaction_id is not None:
        event_payload["interaction_id"] = interaction_id
    draft = SemanticEventDraft(
        event_id=event_id,
        event_type=event_type,
        attempt=attempt,
        operation_id=operation_id or f"operation-{event_id}",
        payload=event_payload,
        resource_refs=resource_refs,
    )
    return store.bind_execution(EXECUTION).append(
        request=draft.to_append_request()
    ).event


def _durable_counts(store: SQLiteContextV2Store) -> dict[str, int]:
    with sqlite3.connect(store.database_path) as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "events",
                "operations",
                "legacy_bootstrap_manifests",
                "host_generation_records",
                "host_generation_attempt_bindings",
            )
        }


def _checkpoint_repository(store: SQLiteContextV2Store):
    journal = store.bind_execution(EXECUTION)
    artifacts = ArtifactService(
        journal,
        sanitizer=lambda content, media_type: content,
    )
    return SQLiteContextCompilerV2Store(
        context_store=store,
    ).bind_execution(
        EXECUTION,
        artifacts=artifacts,
    ).checkpoints


def test_create_commits_all_authorities_and_existing_readers_can_verify(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    task_state = GenerationTaskStateDescriptor(
        "task-state-1",
        1,
        "a" * 64,
        (ResourceRef("context_event", "task-state-source", 1),),
    )
    intent = _intent(
        messages=_messages(
            "generation-1",
            ("user", "initial prompt"),
            ("assistant", "initial answer"),
        ),
        task_state=task_state,
    )

    receipt = service.rebase(
        _request(intent, operation_id="rebase-operation-create")
    )

    assert receipt.kind is GenerationRebaseKind.CREATE
    assert receipt.head_revision == 1
    assert receipt.message_count == 2
    assert receipt.task_state == task_state
    assert service.current(
        owner_chat_id=OWNER,
        execution_id=EXECUTION,
        session_id=SESSION,
    ).current_generation_id == "generation-1"
    snapshot = store.bind_execution(EXECUTION).capture_snapshot()
    assert [event.attempt.generation.generation_id for event in snapshot.events] == [
        "generation-1",
        "generation-1",
    ]
    assert SQLiteLegacyBootstrapService(store).current(OWNER).generation_id == (
        "generation-1"
    )
    lifecycle = SQLiteHostGenerationLifecycleV2(store)
    assert lifecycle.current(
        owner_chat_id=OWNER,
        execution_id=EXECUTION,
        session_id=SESSION,
    ).current_generation_id == "generation-1"
    assert lifecycle.attempt_binding(
        owner_chat_id=OWNER,
        execution_id=EXECUTION,
        session_id=SESSION,
        attempt_id="attempt-1",
    ).generation_id == "generation-1"
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM operations WHERE execution_id = ?",
            (EXECUTION,),
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM host_generation_operations"
        ).fetchone()[0] == 2
        host_head = connection.execute(
            "SELECT current_generation_id, revision FROM host_generation_heads"
        ).fetchone()
        bootstrap_head = connection.execute(
            """
            SELECT current_generation_id, head_revision
            FROM legacy_bootstrap_chat_heads
            """
        ).fetchone()
    assert host_head == bootstrap_head == ("generation-1", 1)


def test_edit_regenerate_and_retry_preserve_every_old_generation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    create_request = _request(
        _intent(),
        operation_id="rebase-operation-1",
    )
    create = service.rebase(create_request)
    edit_intent, edit_request = _next(
        create,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )
    edit = service.rebase(edit_request)
    regenerate_intent, regenerate_request = _next(
        edit,
        kind=GenerationRebaseKind.REGENERATE,
        ordinal=3,
    )
    regenerate = service.rebase(regenerate_request)
    retry_intent, retry_request = _next(
        regenerate,
        kind=GenerationRebaseKind.RETRY,
        ordinal=4,
        messages=_messages(
            "generation-4",
            ("user", "retry the same logical request"),
        ),
    )
    retry = service.rebase(retry_request)

    assert [
        create.kind,
        edit.kind,
        regenerate.kind,
        retry.kind,
    ] == [
        GenerationRebaseKind.CREATE,
        GenerationRebaseKind.EDIT,
        GenerationRebaseKind.REGENERATE,
        GenerationRebaseKind.RETRY,
    ]
    assert retry.head_revision == 4
    reopened = SQLiteGenerationRebaseV2Service(_store(tmp_path))
    assert reopened.current(
        owner_chat_id=OWNER,
        execution_id=EXECUTION,
        session_id=SESSION,
    ).current_generation_id == "generation-4"
    assert [
        reopened.receipt_for_generation(
            owner_chat_id=OWNER,
            execution_id=EXECUTION,
            session_id=SESSION,
            generation_id=f"generation-{ordinal}",
        ).kind
        for ordinal in range(1, 5)
    ] == [
        GenerationRebaseKind.CREATE,
        GenerationRebaseKind.EDIT,
        GenerationRebaseKind.REGENERATE,
        GenerationRebaseKind.RETRY,
    ]
    assert reopened.rebase(create_request) == replace(create, duplicate=True)
    assert reopened.rebase(retry_request) == replace(retry, duplicate=True)
    assert store.bind_execution(EXECUTION).capture_snapshot().events[0].attempt == (
        _intent().attempt
    )
    lifecycle = SQLiteHostGenerationLifecycleV2(store)
    assert lifecycle.generation(
        owner_chat_id=OWNER,
        execution_id=EXECUTION,
        session_id=SESSION,
        generation_id="generation-4",
    ).kind is HostGenerationTransitionKind.REGENERATE
    assert SQLiteLegacyBootstrapService(store).current(OWNER).generation_id == (
        "generation-4"
    )
    del edit_intent, regenerate_intent, retry_intent


def test_payload_hash_stale_head_and_source_revision_conflicts_leave_head(
    tmp_path: Path,
) -> None:
    service = SQLiteGenerationRebaseV2Service(_store(tmp_path))
    initial_intent = _intent()
    initial_request = _request(
        initial_intent,
        operation_id="rebase-operation-1",
    )
    initial = service.rebase(initial_request)
    assert service.rebase(initial_request) == replace(initial, duplicate=True)

    changed_payload = replace(
        initial_intent,
        messages=_messages("changed", ("user", "changed payload")),
    )
    with pytest.raises(GenerationRebaseConflict, match="operation|payload"):
        service.rebase(
            _request(changed_payload, operation_id="rebase-operation-1")
        )

    stale = _intent(
        kind=GenerationRebaseKind.EDIT,
        generation_id="generation-2",
        attempt_id="attempt-2",
        previous_generation_id="not-current",
        expected_head_revision=1,
        source_revision="source-revision-2",
    )
    with pytest.raises(GenerationRebaseConflict, match="previous|current"):
        service.rebase(_request(stale, operation_id="rebase-operation-stale"))

    reused_source = replace(
        stale,
        previous_generation_id="generation-1",
        source_revision="source-revision-1",
    )
    with pytest.raises(GenerationRebaseConflict, match="source revision"):
        service.rebase(
            _request(reused_source, operation_id="rebase-operation-source-reuse")
        )
    assert service.current(
        owner_chat_id=OWNER,
        execution_id=EXECUTION,
        session_id=SESSION,
    ).current_generation_id == "generation-1"


def test_injected_failure_after_journal_writes_rolls_back_every_authority(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
        messages=_messages(
            "generation-2",
            ("user", "edited prompt"),
            ("assistant", "edited answer"),
        ),
    )
    with sqlite3.connect(store.database_path) as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "events",
                "operations",
                "legacy_bootstrap_manifests",
                "host_generation_records",
                "host_generation_attempt_bindings",
            )
        }
        connection.executescript(
            """
            CREATE TRIGGER fail_generation_rebase_record
            BEFORE INSERT ON host_generation_records
            WHEN NEW.generation_id = 'generation-2'
            BEGIN
                SELECT RAISE(ABORT, 'injected generation failure');
            END;
            """
        )

    with pytest.raises(GenerationRebaseError):
        service.rebase(edit_request)

    with sqlite3.connect(store.database_path) as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        assert connection.execute(
            "SELECT current_generation_id, revision FROM host_generation_heads"
        ).fetchone() == ("generation-1", 1)
        assert connection.execute(
            """
            SELECT current_generation_id, head_revision
            FROM legacy_bootstrap_chat_heads
            """
        ).fetchone() == ("generation-1", 1)
        assert connection.execute(
            "SELECT next_store_seq FROM executions WHERE execution_id = ?",
            (EXECUTION,),
        ).fetchone() == (2,)
    assert after == before
    assert service.receipt_for_generation(
        owner_chat_id=OWNER,
        execution_id=EXECUTION,
        session_id=SESSION,
        generation_id="generation-2",
    ) is None
    del edit_intent


def test_current_head_drives_compiler_to_exclude_old_generation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(
            _intent(
                messages=_messages(
                    "generation-1",
                    ("user", "old prompt must not compile"),
                    ("assistant", "old answer must not compile"),
                )
            ),
            operation_id="rebase-operation-1",
        )
    )
    edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
        messages=_messages(
            "generation-2",
            ("user", "new prompt is current"),
        ),
    )
    edit = service.rebase(edit_request)
    head = service.current(
        owner_chat_id=OWNER,
        execution_id=EXECUTION,
        session_id=SESSION,
    )
    journal = store.bind_execution(EXECUTION)
    artifacts = ArtifactService(
        journal,
        sanitizer=lambda content, media_type: content,
    )
    capabilities = SQLiteContextCompilerV2Store(
        context_store=store,
    ).bind_execution(
        EXECUTION,
        artifacts=artifacts,
    )
    request = ContextCompileRequest(
        case="generation-rebase-current-only",
        source_messages=(
            {"role": "user", "content": "new prompt is current"},
        ),
        current_generation=head.current_generation_id,
        budget=resolve_context_budget(context_window_tokens=8_192),
        source_message_cursors=(
            SourceMessageCursor(
                0,
                edit.last_cursor.event_id,
                edit.last_cursor.store_seq,
            ),
        ),
        provider="openai",
        model="synthetic",
        build_id="generation-rebase-build-2",
        execution_id=EXECUTION,
        generation_id=head.current_generation_id,
        attempt_id=head.current_attempt_id,
    )
    result = ContextCompileCoordinator(
        journal=journal,
        checkpoint_repository=capabilities.checkpoints,
        build_repository=capabilities.context_builds,
        partial_attempt_sink=lambda request, error: None,
    ).compile(request)

    contents = [message.get("content") for message in result.messages]
    assert "new prompt is current" in contents
    assert "old prompt must not compile" not in contents
    assert "old answer must not compile" not in contents
    assert [
        event.attempt.generation.generation_id
        for event in journal.capture_snapshot().events
    ] == ["generation-1", "generation-1", "generation-2"]
    del edit_intent


def test_concurrent_rebases_have_one_complete_winner(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first_service = SQLiteGenerationRebaseV2Service(store)
    second_service = SQLiteGenerationRebaseV2Service(store)
    initial = first_service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    intents = (
        _intent(
            kind=GenerationRebaseKind.EDIT,
            generation_id="generation-edit-a",
            attempt_id="attempt-edit-a",
            previous_generation_id=initial.generation_id,
            expected_head_revision=initial.head_revision,
            source_revision="source-revision-edit-a",
        ),
        _intent(
            kind=GenerationRebaseKind.REGENERATE,
            generation_id="generation-edit-b",
            attempt_id="attempt-edit-b",
            previous_generation_id=initial.generation_id,
            expected_head_revision=initial.head_revision,
            source_revision="source-revision-edit-b",
        ),
    )
    barrier = Barrier(2)

    def rebase(index: int):
        barrier.wait()
        service = first_service if index == 0 else second_service
        return service.rebase(
            _request(
                intents[index],
                operation_id=f"rebase-operation-concurrent-{index}",
            )
        )

    successes = []
    conflicts = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(rebase, index) for index in range(2)]
        for future in as_completed(futures):
            try:
                successes.append(future.result())
            except GenerationRebaseConflict as error:
                conflicts.append(error)

    assert len(successes) == 1
    assert len(conflicts) == 1
    assert successes[0].head_revision == 2
    assert first_service.current(
        owner_chat_id=OWNER,
        execution_id=EXECUTION,
        session_id=SESSION,
    ).current_generation_id == successes[0].generation_id
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM host_generation_records"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM legacy_bootstrap_manifests"
        ).fetchone()[0] == 2


def test_pending_canonical_interaction_blocks_without_rebase_writes(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    _append_event(
        store,
        initial,
        event_id="interaction-request-1",
        event_type="interaction.requested",
        interaction_id="interaction-1",
    )
    _edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )
    before = _durable_counts(store)

    with pytest.raises(
        GenerationRebasePreflightBlocked,
        match="pending durable interaction",
    ):
        service.rebase(edit_request)

    assert _durable_counts(store) == before
    assert service.current(
        owner_chat_id=OWNER,
        execution_id=EXECUTION,
        session_id=SESSION,
    ).current_generation_id == initial.generation_id


def test_resolved_canonical_interaction_allows_rebase(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    _append_event(
        store,
        initial,
        event_id="interaction-request-1",
        event_type="interaction.requested",
        interaction_id="interaction-1",
    )
    _append_event(
        store,
        initial,
        event_id="interaction-resolution-1",
        event_type="interaction.resolved",
        interaction_id="interaction-1",
        payload={"outcome": "approved"},
    )
    _append_event(
        store,
        initial,
        event_id="run-completed-after-interaction",
        event_type="run_completed",
    )
    _edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )

    edited = service.rebase(edit_request)

    assert edited.head_revision == 2
    assert edited.previous_generation_id == initial.generation_id


def test_authorized_canonical_resolution_supersedes_malformed_legacy_for_rebase(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    _append_event(
        store,
        initial,
        event_id="interaction-request-legacy-repair",
        event_type="interaction.requested",
        interaction_id="interaction-legacy-repair",
    )
    _append_event(
        store,
        initial,
        event_id="interaction-resolution-malformed-legacy",
        event_type="interaction_resolved",
        interaction_id="interaction-legacy-repair",
    )
    artifact = ArtifactService(
        store.bind_execution(EXECUTION),
        sanitizer=lambda content, _media_type: content,
    ).persist_exact_json(
        {
            "interaction_id": "interaction-legacy-repair",
            "response": {"answer": "yes"},
            "submitted_by": "ui:test",
        },
        operation_id="artifact-interaction-legacy-repair",
    )
    _append_event(
        store,
        initial,
        event_id="interaction-resolution-canonical-repair",
        event_type="interaction.resolved",
        interaction_id="interaction-legacy-repair",
        payload={
            "submitted_by": "ui:test",
            "content_ref": artifact.ref.to_dict(),
            "content_bytes": artifact.byte_length,
            "content_sha256": artifact.sha256,
            "preview": artifact.preview,
            "preview_truncated": False,
        },
        resource_refs=(artifact.ref,),
    )
    _append_event(
        store,
        initial,
        event_id="run-completed-after-legacy-repair",
        event_type="run_completed",
    )
    _edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )

    edited = service.rebase(edit_request)

    assert edited.head_revision == 2
    assert edited.previous_generation_id == initial.generation_id


def test_unauthorized_canonical_resolution_cannot_hide_legacy_during_rebase(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    interaction_id = "interaction-unauthorized-repair"
    _append_event(
        store,
        initial,
        event_id="interaction-request-unauthorized-repair",
        event_type="interaction.requested",
        interaction_id=interaction_id,
    )
    _append_event(
        store,
        initial,
        event_id="interaction-resolution-malformed-unauthorized",
        event_type="interaction_resolved",
        interaction_id=interaction_id,
    )
    preview = '{"answer":"yes"}'
    content = preview.encode("utf-8")
    _append_event(
        store,
        initial,
        event_id="interaction-resolution-unauthorized-repair",
        event_type="interaction.resolved",
        interaction_id=interaction_id,
        payload={
            "submitted_by": "ui:test",
            "content_ref": {
                "kind": "artifact",
                "id": "payload-only-artifact",
                "revision": 1,
            },
            "content_bytes": len(content),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "preview": preview,
            "preview_truncated": False,
        },
    )
    _edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )
    before = _durable_counts(store)

    with pytest.raises(
        GenerationRebaseUnavailable,
        match="resolution is duplicated",
    ):
        service.rebase(edit_request)

    assert _durable_counts(store) == before


def test_ambiguous_interaction_lifecycle_fails_closed_without_rebase_writes(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    for ordinal in (1, 2):
        _append_event(
            store,
            initial,
            event_id=f"interaction-request-{ordinal}",
            event_type="interaction.requested",
            interaction_id="interaction-1",
        )
    _edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )
    before = _durable_counts(store)

    with pytest.raises(
        GenerationRebaseUnavailable,
        match="request is duplicated",
    ):
        service.rebase(edit_request)

    assert _durable_counts(store) == before


def test_current_prepared_checkpoint_blocks_until_committed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    checkpoints = _checkpoint_repository(store)
    prepared = checkpoints.prepare(
        source_range=EventRange(initial.first_cursor, initial.last_cursor),
        summary="durable checkpoint in progress",
        refs=(),
        operation=OperationRef("checkpoint-operation-1", "b" * 64),
    )
    _edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )
    before = _durable_counts(store)

    with pytest.raises(
        GenerationRebasePreflightBlocked,
        match="prepared durable checkpoint",
    ):
        service.rebase(edit_request)

    assert _durable_counts(store) == before
    checkpoints.commit(prepared=prepared)
    edited = service.rebase(edit_request)
    assert edited.head_revision == 2


def test_prepared_checkpoint_for_an_old_generation_does_not_block_current(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    _edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )
    edited = service.rebase(edit_request)
    checkpoints = _checkpoint_repository(store)
    checkpoints.prepare(
        source_range=EventRange(initial.first_cursor, initial.last_cursor),
        summary="unfinished checkpoint for archived generation",
        refs=(),
        operation=OperationRef("checkpoint-operation-old", "c" * 64),
    )
    _retry_intent, retry_request = _next(
        edited,
        kind=GenerationRebaseKind.RETRY,
        ordinal=3,
    )

    retried = service.rebase(retry_request)

    assert retried.head_revision == 3
    assert retried.previous_generation_id == edited.generation_id


def test_exact_replay_precedes_new_current_generation_preflight_state(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    _edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )
    edited = service.rebase(edit_request)
    _append_event(
        store,
        edited,
        event_id="run-started-after-edit",
        event_type="run_started",
        attempt_id="attempt-live-after-edit",
    )

    assert service.rebase(edit_request) == replace(edited, duplicate=True)


def test_import_only_current_generation_allows_rebase(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    _edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )

    edited = service.rebase(edit_request)

    assert edited.head_revision == 2


def test_open_real_attempt_blocks_without_rebase_writes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    _append_event(
        store,
        initial,
        event_id="run-started-live",
        event_type="run_started",
        attempt_id="attempt-live",
    )
    _edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )
    before = _durable_counts(store)

    with pytest.raises(
        GenerationRebasePreflightBlocked,
        match="unfinished durable attempt",
    ):
        service.rebase(edit_request)

    assert _durable_counts(store) == before


def test_open_tool_blocks_without_rebase_writes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    for event_id, event_type in (
        ("tool-call-live", "tool_call"),
        ("tool-started-live", "tool.started"),
    ):
        _append_event(
            store,
            initial,
            event_id=event_id,
            event_type=event_type,
            attempt_id="attempt-live",
            payload={"call_id": "call-live", "tool_name": "lookup"},
        )
    _edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )
    before = _durable_counts(store)

    with pytest.raises(
        GenerationRebasePreflightBlocked,
        match="unfinished durable tool",
    ):
        service.rebase(edit_request)

    assert _durable_counts(store) == before


def test_completed_tool_and_terminal_attempt_allow_rebase(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    for event_id, event_type in (
        ("tool-call-complete", "tool_call"),
        ("tool-started-complete", "tool.started"),
        ("tool-result-complete", "tool_result"),
    ):
        _append_event(
            store,
            initial,
            event_id=event_id,
            event_type=event_type,
            attempt_id="attempt-complete",
            payload={"call_id": "call-complete", "tool_name": "lookup"},
        )
    _append_event(
        store,
        initial,
        event_id="run-completed-tool",
        event_type="run_completed",
        attempt_id="attempt-complete",
    )
    _edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )

    edited = service.rebase(edit_request)

    assert edited.head_revision == 2


def test_duplicate_tool_lifecycle_fails_without_rebase_writes(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    for event_id, event_type in (
        ("tool-call-duplicate", "tool_call"),
        ("tool-started-duplicate-1", "tool.started"),
        ("tool-started-duplicate-2", "tool.started"),
    ):
        _append_event(
            store,
            initial,
            event_id=event_id,
            event_type=event_type,
            attempt_id="attempt-corrupt",
            payload={"call_id": "call-corrupt", "tool_name": "lookup"},
        )
    _edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )
    before = _durable_counts(store)

    with pytest.raises(
        GenerationRebaseUnavailable,
        match="tool lifecycle is not uniquely paired",
    ):
        service.rebase(edit_request)

    assert _durable_counts(store) == before


def test_duplicate_attempt_terminal_fails_without_rebase_writes(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    for event_id, event_type in (
        ("run-started-duplicate", "run_started"),
        ("run-completed-duplicate", "run_completed"),
        ("run-failed-duplicate", "run_failed"),
    ):
        _append_event(
            store,
            initial,
            event_id=event_id,
            event_type=event_type,
            attempt_id="attempt-corrupt",
        )
    _edit_intent, edit_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
    )
    before = _durable_counts(store)

    with pytest.raises(
        GenerationRebaseUnavailable,
        match="duplicate terminal events",
    ):
        service.rebase(edit_request)

    assert _durable_counts(store) == before


def test_empty_snapshot_uses_a_marker_and_survives_restart(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(_intent(), operation_id="rebase-operation-1")
    )
    _empty_intent, empty_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
        messages=(),
    )

    emptied = service.rebase(empty_request)

    assert emptied.message_count == 0
    assert emptied.first_cursor == emptied.last_cursor
    marker = store.bind_execution(EXECUTION).capture_snapshot().events[-1]
    assert marker.event_type == "generation.rebased"
    assert marker.attempt.generation.generation_id == emptied.generation_id
    assert marker.payload["generation_rebase"]["empty_snapshot"] is True
    assert marker.payload["generation_rebase"]["replacement_message_count"] == 0
    assert "message" not in marker.payload
    reopened = SQLiteGenerationRebaseV2Service(_store(tmp_path))
    assert reopened.current(
        owner_chat_id=OWNER,
        execution_id=EXECUTION,
        session_id=SESSION,
    ).current_generation_id == emptied.generation_id
    assert reopened.receipt_for_generation(
        owner_chat_id=OWNER,
        execution_id=EXECUTION,
        session_id=SESSION,
        generation_id=emptied.generation_id,
    ) == emptied
    assert reopened.rebase(empty_request) == replace(emptied, duplicate=True)


def test_empty_initial_snapshot_has_a_durable_generation_head(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    intent = _intent(messages=())

    receipt = service.rebase(
        _request(intent, operation_id="rebase-operation-empty-create")
    )

    assert receipt.kind is GenerationRebaseKind.CREATE
    assert receipt.message_count == 0
    assert service.current(
        owner_chat_id=OWNER,
        execution_id=EXECUTION,
        session_id=SESSION,
    ).current_generation_id == receipt.generation_id
    snapshot = store.bind_execution(EXECUTION).capture_snapshot()
    assert len(snapshot.events) == 1
    assert snapshot.events[0].event_type == "generation.rebased"


def test_compiler_on_empty_current_generation_does_not_restore_old_messages(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = SQLiteGenerationRebaseV2Service(store)
    initial = service.rebase(
        _request(
            _intent(
                messages=_messages(
                    "generation-1",
                    ("user", "old prompt must remain archived"),
                    ("assistant", "old answer must remain archived"),
                )
            ),
            operation_id="rebase-operation-1",
        )
    )
    _empty_intent, empty_request = _next(
        initial,
        kind=GenerationRebaseKind.EDIT,
        ordinal=2,
        messages=(),
    )
    emptied = service.rebase(empty_request)
    journal = store.bind_execution(EXECUTION)
    current_user = journal.append(
        request=SemanticEventDraft(
            event_id="message-after-empty-rebase",
            event_type="message.user",
            attempt=AttemptRef(
                GenerationRef(EXECUTION, emptied.generation_id),
                emptied.attempt_id,
            ),
            operation_id="operation-message-after-empty-rebase",
            payload={
                "run_id": emptied.attempt_id,
                "message": {
                    "role": "user",
                    "content": "new prompt after clearing history",
                },
            },
        ).to_append_request()
    ).event
    artifacts = ArtifactService(
        journal,
        sanitizer=lambda content, media_type: content,
    )
    capabilities = SQLiteContextCompilerV2Store(
        context_store=store,
    ).bind_execution(
        EXECUTION,
        artifacts=artifacts,
    )
    request = ContextCompileRequest(
        case="generation-rebase-empty-current",
        source_messages=(
            {"role": "system", "content": "current policy"},
            {"role": "user", "content": "new prompt after clearing history"},
        ),
        current_generation=emptied.generation_id,
        budget=resolve_context_budget(context_window_tokens=8_192),
        source_message_cursors=(
            SourceMessageCursor(
                1,
                current_user.event_id,
                current_user.store_seq,
            ),
        ),
        provider="openai",
        model="synthetic",
        build_id="generation-rebase-empty-build",
        execution_id=EXECUTION,
        generation_id=emptied.generation_id,
        attempt_id=emptied.attempt_id,
    )

    result = ContextCompileCoordinator(
        journal=journal,
        checkpoint_repository=capabilities.checkpoints,
        build_repository=capabilities.context_builds,
        partial_attempt_sink=lambda request, error: None,
    ).compile(request)

    contents = [message.get("content") for message in result.messages]
    assert "current policy" in contents
    assert "new prompt after clearing history" in contents
    assert "old prompt must remain archived" not in contents
    assert "old answer must remain archived" not in contents
    assert all(message.get("role") != "generation.rebased" for message in result.messages)
