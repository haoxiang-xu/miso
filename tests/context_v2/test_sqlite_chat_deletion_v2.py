from __future__ import annotations

import hashlib
import sqlite3

import pytest

from unchain.context import (
    ArtifactService,
    ContextBuildEnvelope,
    resolve_context_budget,
)
from unchain.journal import EventCursor, EventRange
from unchain.journal.runtime import build_operation_ref
from unchain.memory.workspace import MemorySpace
from unchain.persistence.sqlite_chat_deletion_v2 import (
    ChatDeletedError,
    ChatDeletionConflict,
    ChatDeletionScope,
    ChatDeletionUnavailable,
    SQLiteChatDeletionV2Service,
    is_chat_deleted,
    is_chat_deletion_tombstoned,
    read_chat_deletion_tombstone,
)
from unchain.persistence.sqlite_context_compiler_v2 import (
    SQLiteContextCompilerV2Store,
)
from unchain.persistence.sqlite_curator_v2 import SQLiteCuratorV2Store
from unchain.persistence.sqlite_legacy_bootstrap_v2 import (
    LegacyBootstrapPayload,
    LegacyBootstrapPreflight,
    LegacyBootstrapRequest,
    LegacyBootstrapUnavailable,
    LegacyGenerationDescriptor,
    LegacyMessage,
    SQLiteLegacyBootstrapService,
    build_legacy_bootstrap_operation,
)
from unchain.persistence.sqlite_memory_v2 import SQLiteMemoryV2Store
from unchain.persistence.sqlite_promotion_v2 import SQLitePromotionV2Store
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _initialize_stack(tmp_path):
    database_path = tmp_path / "context_v2.sqlite3"
    object_directory = tmp_path / "objects"
    context = SQLiteContextV2Store(
        database_path=database_path,
        object_directory=object_directory,
    )
    compiler = SQLiteContextCompilerV2Store(context_store=context)
    memory = SQLiteMemoryV2Store(
        database_path=database_path,
        object_directory=object_directory,
    )
    curator = SQLiteCuratorV2Store(
        database_path=database_path,
        object_directory=object_directory,
    )
    SQLitePromotionV2Store(
        database_path=database_path,
        object_directory=object_directory,
    )
    legacy = SQLiteLegacyBootstrapService(context)
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_host_v2_schema (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT OR IGNORE INTO memory_host_v2_schema(version) VALUES (1);
            CREATE TABLE IF NOT EXISTS memory_review_proposals (
                review_id TEXT PRIMARY KEY,
                binding_id TEXT NOT NULL
            );
            """
        )
    return (
        database_path,
        object_directory,
        context,
        compiler,
        memory,
        curator,
        legacy,
    )


def _insert_execution(connection, suffix: str) -> None:
    execution_id = f"execution-{suffix}"
    operation_id = f"event-operation-{suffix}"
    connection.execute(
        "INSERT INTO executions(execution_id, next_store_seq) VALUES (?, 2)",
        (execution_id,),
    )
    connection.execute(
        """
        INSERT INTO operations(
            execution_id, operation_id, payload_sha256, target_kind, target_key
        ) VALUES (?, ?, ?, 'journal_event', ?)
        """,
        (execution_id, operation_id, "a" * 64, f"event-{suffix}"),
    )
    connection.execute(
        """
        INSERT INTO events(
            execution_id, store_seq, event_id, generation_id, attempt_id,
            event_type, operation_id, event_json, event_sha256
        ) VALUES (?, 1, ?, ?, ?, 'message.user', ?, ?, ?)
        """,
        (
            execution_id,
            f"event-{suffix}",
            f"generation-{suffix}",
            f"attempt-{suffix}",
            operation_id,
            b"{}",
            _sha(b"{}"),
        ),
    )
    artifact_operation = f"artifact-operation-{suffix}"
    object_sha = _sha(f"object-{suffix}".encode())
    connection.execute(
        "INSERT OR IGNORE INTO objects(sha256, byte_length) VALUES (?, ?)",
        (object_sha, len(f"object-{suffix}")),
    )
    connection.execute(
        """
        INSERT INTO operations(
            execution_id, operation_id, payload_sha256, target_kind, target_key
        ) VALUES (?, ?, ?, 'artifact', ?)
        """,
        (execution_id, artifact_operation, "b" * 64, f"artifact-{suffix}"),
    )
    connection.execute(
        """
        INSERT INTO artifacts(
            execution_id, artifact_id, revision, logical_kind, logical_key,
            object_sha256, media_type, byte_length, preview, operation_id,
            artifact_json, artifact_record_sha256
        ) VALUES (?, ?, 1, 'tool_result', ?, ?, 'text/plain', ?, '', ?, ?, ?)
        """,
        (
            execution_id,
            f"artifact-{suffix}",
            f"artifact-key-{suffix}",
            object_sha,
            len(f"object-{suffix}"),
            artifact_operation,
            b"{}",
            _sha(b"{}"),
        ),
    )
    bootstrap_operation = f"bootstrap-operation-{suffix}"
    connection.execute(
        """
        INSERT INTO operations(
            execution_id, operation_id, payload_sha256, target_kind, target_key
        ) VALUES (?, ?, ?, 'legacy_bootstrap_manifest', ?)
        """,
        (
            execution_id,
            bootstrap_operation,
            "9" * 64,
            f"chat-{suffix}:generation-{suffix}",
        ),
    )
    connection.execute(
        """
        INSERT INTO legacy_bootstrap_manifests(
            owner_chat_id, source_revision, session_id, execution_id,
            generation_id, attempt_id, operation_id, payload_sha256,
            manifest_json, manifest_sha256, first_store_seq, last_store_seq,
            event_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 1)
        """,
        (
            f"chat-{suffix}",
            f"source-{suffix}",
            execution_id,
            execution_id,
            f"generation-{suffix}",
            f"attempt-{suffix}",
            bootstrap_operation,
            "9" * 64,
            b"{}",
            _sha(b"{}"),
        ),
    )
    connection.execute(
        """
        INSERT INTO legacy_bootstrap_chat_heads(
            owner_chat_id, execution_id, session_id, current_generation_id,
            current_source_revision, head_revision
        ) VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            f"chat-{suffix}",
            execution_id,
            execution_id,
            f"generation-{suffix}",
            f"source-{suffix}",
        ),
    )


def _insert_space(connection, suffix: str, *, owner: str, namespace: str) -> None:
    space_id = f"space-{suffix}"
    connection.execute(
        """
        INSERT INTO spaces(
            space_id, owner_chat_id, namespace, name, description, revision,
            space_json, space_sha256
        ) VALUES (?, ?, ?, ?, '', 1, ?, ?)
        """,
        (space_id, owner, namespace, f"Space {suffix}", b"{}", _sha(b"{}")),
    )
    connection.execute(
        """
        INSERT INTO entries(
            space_id, entry_id, current_revision, path_key, name_key,
            deleted, updated_seq
        ) VALUES (?, ?, 1, ?, ?, 0, 1)
        """,
        (space_id, f"entry-{suffix}", f"/{suffix}.md", suffix),
    )
    connection.execute(
        """
        INSERT INTO entry_revisions(
            space_id, entry_id, revision, path_key, operation_id,
            entry_json, entry_sha256
        ) VALUES (?, ?, 1, ?, ?, ?, ?)
        """,
        (
            space_id,
            f"entry-{suffix}",
            f"/{suffix}.md",
            f"entry-operation-{suffix}",
            b"{}",
            _sha(b"{}"),
        ),
    )
    connection.execute(
        """
        INSERT INTO memory_operation_receipts(
            scope_kind, scope_id, operation_id, payload_sha256,
            target_kind, target_key, result_id, result_revision
        ) VALUES ('workspace', ?, ?, ?, 'entry', ?, ?, 1)
        """,
        (
            space_id,
            f"entry-operation-{suffix}",
            "c" * 64,
            f"entry-{suffix}",
            f"entry-{suffix}",
        ),
    )
    connection.execute(
        """
        INSERT INTO index_state(index_name, scope_id, status, revision, detail)
        VALUES ('workspace_fts', ?, 'ready', 1, '')
        """,
        (space_id,),
    )
    connection.execute(
        """
        INSERT INTO workspace_entries_fts(
            space_id, entry_id, path, name, description, tags
        ) VALUES (?, ?, ?, ?, '', '')
        """,
        (space_id, f"entry-{suffix}", f"/{suffix}.md", suffix),
    )


def _insert_curation(connection, suffix: str, *, owner: str) -> None:
    binding_id = f"binding-{suffix}"
    candidate_id = f"candidate-{suffix}"
    job_id = f"job-{suffix}"
    connection.execute(
        """
        INSERT INTO curation_scopes(
            binding_id, owner_chat_id, target_space_id, created_at_ms
        ) VALUES (?, ?, ?, 1)
        """,
        (binding_id, owner, f"space-{suffix}"),
    )
    connection.execute(
        """
        INSERT INTO curation_run_scopes(
            binding_id,
            session_id,
            attempt_id,
            run_id,
            root_run_id,
            created_at_ms
        ) VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            binding_id,
            f"execution-{suffix}",
            f"attempt-{suffix}",
            f"run-{suffix}",
            f"run-{suffix}",
        ),
    )
    connection.execute(
        """
        INSERT INTO candidates(
            candidate_id, binding_id, session_id, attempt_id, run_id,
            current_record_revision, status, object_sha256, byte_length,
            created_at_ms, updated_at_ms
        ) VALUES (?, ?, ?, ?, ?, 1, 'pending', NULL, 0, 1, 1)
        """,
        (
            candidate_id,
            binding_id,
            f"execution-{suffix}",
            f"attempt-{suffix}",
            f"run-{suffix}",
        ),
    )
    connection.execute(
        """
        INSERT INTO candidate_revisions(
            candidate_id, record_revision, snapshot_json, snapshot_sha256,
            operation_id, created_at_ms
        ) VALUES (?, 1, ?, ?, ?, 1)
        """,
        (candidate_id, b"{}", _sha(b"{}"), f"candidate-operation-{suffix}"),
    )
    connection.execute(
        """
        INSERT INTO consolidation_jobs(
            job_id, binding_id, trigger_key, current_revision, status,
            lease_owner, lease_token, lease_expires_at_ms, next_attempt_at_ms,
            created_at_ms, updated_at_ms
        ) VALUES (?, ?, ?, 1, 'pending', NULL, NULL, NULL, 0, 1, 1)
        """,
        (job_id, binding_id, f"trigger-{suffix}"),
    )
    connection.execute(
        """
        INSERT INTO consolidation_job_revisions(
            job_id, revision, job_json, job_sha256, operation_id, created_at_ms
        ) VALUES (?, 1, ?, ?, ?, 1)
        """,
        (job_id, b"{}", _sha(b"{}"), f"job-operation-{suffix}"),
    )
    connection.execute(
        """
        INSERT INTO candidate_bindings(
            candidate_id, binding_revision, job_id, target_space_id, status,
            result_ref_json, review_diff_json, error_code,
            candidate_record_revision, created_at_ms
        ) VALUES (?, 1, ?, ?, 'processing', NULL, ?, '', 1, 1)
        """,
        (candidate_id, job_id, f"space-{suffix}", b"{}"),
    )
    connection.execute(
        """
        INSERT INTO curator_operation_receipts(
            binding_id, operation_id, operation_kind, payload_sha256,
            semantic_sha256, result_kind, result_key, result_revision,
            result_count, created_at_ms
        ) VALUES (?, ?, 'propose', ?, ?, 'candidate', ?, 1, NULL, 1)
        """,
        (
            binding_id,
            f"candidate-operation-{suffix}",
            "d" * 64,
            "e" * 64,
            candidate_id,
        ),
    )
    connection.execute(
        "INSERT INTO memory_review_proposals(review_id, binding_id) VALUES (?, ?)",
        (f"review-{suffix}", binding_id),
    )
    connection.execute(
        """
        INSERT INTO task_state_heads(binding_id, state_id, current_revision)
        VALUES (?, 'pinned', 1)
        """,
        (binding_id,),
    )
    connection.execute(
        """
        INSERT INTO task_state_revisions(
            binding_id, state_id, revision, operation_id, state_json, state_sha256
        ) VALUES (?, 'pinned', 1, ?, ?, ?)
        """,
        (binding_id, f"task-operation-{suffix}", b"{}", _sha(b"{}")),
    )


def _seed_two_chats(tmp_path):
    (
        database_path,
        object_directory,
        context,
        compiler,
        memory,
        curator,
        legacy,
    ) = _initialize_stack(tmp_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _insert_execution(connection, "a")
        _insert_execution(connection, "b")
        _insert_space(connection, "a", owner="chat-a", namespace="chat")
        _insert_space(connection, "b", owner="chat-b", namespace="chat")
        _insert_space(connection, "long", owner="", namespace="user:user-1")
        _insert_curation(connection, "a", owner="chat-a")
        _insert_curation(connection, "b", owner="chat-b")
        connection.execute(
            """
            INSERT INTO promotion_namespace_bindings(
                target_namespace, target_space_id
            ) VALUES ('user:user-1', 'space-long')
            """
        )
        for suffix, owner in (("a", "chat-a"), ("b", "chat-b")):
            connection.execute(
                """
                INSERT INTO promotion_bindings(
                    source_space_id, target_namespace, source_owner_chat_id,
                    target_space_id
                ) VALUES (?, 'user:user-1', ?, 'space-long')
                """,
                (f"space-{suffix}", owner),
            )
            connection.execute(
                """
                INSERT INTO promotion_proposals(
                    source_space_id, target_namespace, target_space_id,
                    proposal_id, current_revision, status, source_entry_id,
                    source_entry_revision, target_path_key
                ) VALUES (?, 'user:user-1', 'space-long', ?, 1, 'pending', ?, 1, ?)
                """,
                (
                    f"space-{suffix}",
                    f"proposal-{suffix}",
                    f"entry-{suffix}",
                    f"/{suffix}.md",
                ),
            )
            connection.execute(
                """
                INSERT INTO promotion_revisions(
                    source_space_id, target_namespace, target_space_id,
                    proposal_id, revision, status, operation_id,
                    confirmation_id, proposal_json, proposal_sha256
                ) VALUES (?, 'user:user-1', 'space-long', ?, 1, 'pending', ?, '', ?, ?)
                """,
                (
                    f"space-{suffix}",
                    f"proposal-{suffix}",
                    f"promotion-operation-{suffix}",
                    b"{}",
                    _sha(b"{}"),
                ),
            )
            connection.execute(
                """
                INSERT INTO promotion_operation_receipts(
                    source_space_id, target_namespace, target_space_id,
                    operation_id, payload_sha256, operation_kind,
                    proposal_id, result_revision
                ) VALUES (?, 'user:user-1', 'space-long', ?, ?, 'propose', ?, 1)
                """,
                (
                    f"space-{suffix}",
                    f"promotion-operation-{suffix}",
                    "f" * 64,
                    f"proposal-{suffix}",
                ),
            )
    for suffix in ("a", "b"):
        journal = context.bind_execution(f"execution-{suffix}")
        capabilities = compiler.bind_execution(
            f"execution-{suffix}",
            artifacts=ArtifactService(
                journal,
                sanitizer=lambda content, media_type: content,
            ),
        )
        cursor = EventCursor(1, f"event-{suffix}")
        source_range = EventRange(cursor, cursor)
        prepared = capabilities.checkpoints.prepare(
            source_range=source_range,
            summary=f'{{"chat":"{suffix}"}}',
            refs=(),
            operation=build_operation_ref(
                f"checkpoint-operation-{suffix}",
                domain="test.sqlite_chat_deletion_v2",
                payload={"scope": suffix, "kind": "checkpoint"},
            ),
        )
        capabilities.checkpoints.commit(prepared=prepared)
        envelope = ContextBuildEnvelope(
            build_id=f"build-{suffix}",
            execution_id=f"execution-{suffix}",
            generation_id=f"generation-{suffix}",
            attempt_id=f"attempt-{suffix}",
            provider="openai",
            model="synthetic",
            budget=resolve_context_budget(context_window_tokens=8_192),
            estimated_input_tokens=512,
        )
        capabilities.context_builds.record(
            envelope=envelope,
            operation=build_operation_ref(
                f"build-operation-{suffix}",
                domain="test.sqlite_chat_deletion_v2",
                payload={"scope": suffix, "kind": "context_build"},
            ),
            trigger_cursor=cursor,
        )
    return {
        "database_path": database_path,
        "object_directory": object_directory,
        "context": context,
        "compiler": compiler,
        "memory": memory,
        "curator": curator,
        "legacy": legacy,
        "scope": ChatDeletionScope(
            owner_chat_id="chat-a",
            execution_ids=("execution-a",),
            space_ids=("space-a",),
            binding_ids=("binding-a",),
        ),
    }


def _count(database_path, table: str, where: str = "", values=()) -> int:
    query = f"SELECT COUNT(*) FROM {table}"
    if where:
        query += f" WHERE {where}"
    with sqlite3.connect(database_path) as connection:
        return int(connection.execute(query, values).fetchone()[0])


def test_chat_deletion_scope_is_canonical_and_blank_store_is_active(tmp_path) -> None:
    database_path = tmp_path / "context_v2.sqlite3"
    scope = ChatDeletionScope(
        owner_chat_id="chat-a",
        execution_ids=("execution-b", "execution-a"),
        space_ids=("space-b", "space-a"),
        binding_ids=("binding-b", "binding-a"),
    )

    assert scope.to_dict() == {
        "schema": "unchain.chat_deletion_scope.v1",
        "owner_chat_id": "chat-a",
        "execution_ids": ["execution-a", "execution-b"],
        "space_ids": ["space-a", "space-b"],
        "binding_ids": ["binding-a", "binding-b"],
    }
    assert (
        is_chat_deletion_tombstoned(
            database_path=database_path,
            owner_chat_id="chat-a",
        )
        is False
    )


def test_delete_is_atomic_chat_scoped_and_preserves_long_term_and_objects(
    tmp_path,
) -> None:
    stack = _seed_two_chats(tmp_path)
    service = SQLiteChatDeletionV2Service(
        database_path=stack["database_path"],
    )
    object_count_before = _count(stack["database_path"], "objects")

    receipt = service.delete_chat(
        scope=stack["scope"],
        operation_id="delete-chat-a",
    )

    assert receipt.owner_chat_id == "chat-a"
    assert receipt.tombstone_revision == 1
    assert receipt.replayed is False
    assert receipt.pending_unreferenced_scan is True
    assert receipt.deleted_rows["executions"] == 1
    assert receipt.deleted_rows["spaces"] == 1
    assert receipt.deleted_rows["curation_scopes"] == 1
    assert receipt.deleted_rows["promotion_bindings"] == 1
    assert receipt.deleted_rows["legacy_bootstrap_manifests"] == 1
    assert receipt.deleted_rows["legacy_bootstrap_chat_heads"] == 1
    assert receipt.deleted_rows["checkpoints"] == 1
    assert receipt.deleted_rows["context_builds"] == 1

    for table, column, value in (
        ("executions", "execution_id", "execution-a"),
        ("spaces", "space_id", "space-a"),
        ("curation_scopes", "binding_id", "binding-a"),
        ("candidates", "binding_id", "binding-a"),
        ("consolidation_jobs", "binding_id", "binding-a"),
        ("memory_review_proposals", "binding_id", "binding-a"),
        ("task_state_heads", "binding_id", "binding-a"),
        ("promotion_bindings", "source_space_id", "space-a"),
    ):
        assert _count(stack["database_path"], table, f"{column} = ?", (value,)) == 0

    for table, column, value in (
        ("executions", "execution_id", "execution-b"),
        ("spaces", "space_id", "space-b"),
        ("curation_scopes", "binding_id", "binding-b"),
        ("candidates", "binding_id", "binding-b"),
        ("consolidation_jobs", "binding_id", "binding-b"),
        ("promotion_bindings", "source_space_id", "space-b"),
        ("legacy_bootstrap_manifests", "owner_chat_id", "chat-b"),
        ("legacy_bootstrap_chat_heads", "owner_chat_id", "chat-b"),
        ("checkpoints", "execution_id", "execution-b"),
        ("context_builds", "execution_id", "execution-b"),
        ("spaces", "space_id", "space-long"),
        ("entries", "space_id", "space-long"),
        ("promotion_namespace_bindings", "target_space_id", "space-long"),
    ):
        assert _count(stack["database_path"], table, f"{column} = ?", (value,)) == 1

    # Metadata rows are eligible for a later reference scan; deletion never
    # performs unsafe physical or logical object GC in this transaction.
    assert _count(stack["database_path"], "objects") == object_count_before
    assert is_chat_deletion_tombstoned(
        database_path=stack["database_path"],
        owner_chat_id="chat-a",
    )
    tombstone = read_chat_deletion_tombstone(
        database_path=stack["database_path"],
        owner_chat_id="chat-a",
    )
    assert tombstone is not None
    assert tombstone.scope == stack["scope"]
    assert tombstone.receipt.deleted_rows == receipt.deleted_rows
    assert tombstone.first_operation_id == "delete-chat-a"
    assert is_chat_deleted(database_path=stack["database_path"], owner_chat_id="chat-a")
    assert (
        read_chat_deletion_tombstone(
            database_path=stack["database_path"], owner_chat_id="chat-missing"
        )
        is None
    )


def test_delete_replays_idempotently_after_cold_restart_and_rejects_scope_drift(
    tmp_path,
) -> None:
    stack = _seed_two_chats(tmp_path)
    first = SQLiteChatDeletionV2Service(
        database_path=stack["database_path"]
    ).delete_chat(scope=stack["scope"], operation_id="delete-chat-a")

    reopened = SQLiteChatDeletionV2Service(database_path=stack["database_path"])
    same_operation = reopened.delete_chat(
        scope=stack["scope"],
        operation_id="delete-chat-a",
    )
    new_operation = reopened.delete_chat(
        scope=stack["scope"],
        operation_id="delete-chat-a-retry",
    )

    assert same_operation.replayed is True
    assert new_operation.replayed is True
    assert same_operation.deleted_rows == first.deleted_rows
    assert new_operation.deleted_rows == first.deleted_rows
    with pytest.raises(ChatDeletionConflict, match="scope|payload"):
        reopened.delete_chat(
            scope=ChatDeletionScope(
                owner_chat_id="chat-a",
                execution_ids=("execution-a", "execution-extra"),
                space_ids=("space-a",),
                binding_ids=("binding-a",),
            ),
            operation_id="delete-chat-a",
        )


@pytest.mark.parametrize(
    "corruption_sql",
    (
        "DELETE FROM chat_deletion_execution_scopes WHERE owner_chat_id = 'chat-a'",
        "DELETE FROM chat_deletion_operations "
        "WHERE owner_chat_id = 'chat-a' AND operation_id = 'delete-chat-a'",
    ),
)
def test_cold_read_and_replay_reject_incomplete_tombstone_evidence(
    tmp_path,
    corruption_sql: str,
) -> None:
    stack = _seed_two_chats(tmp_path)
    SQLiteChatDeletionV2Service(database_path=stack["database_path"]).delete_chat(
        scope=stack["scope"],
        operation_id="delete-chat-a",
    )
    with sqlite3.connect(stack["database_path"]) as connection:
        connection.execute(corruption_sql)

    with pytest.raises(ChatDeletionUnavailable, match="tombstone|scope|operation"):
        read_chat_deletion_tombstone(
            database_path=stack["database_path"],
            owner_chat_id="chat-a",
        )
    with pytest.raises(ChatDeletionUnavailable, match="tombstone|scope|operation"):
        SQLiteChatDeletionV2Service(database_path=stack["database_path"]).delete_chat(
            scope=stack["scope"],
            operation_id="delete-chat-a",
        )


def test_delete_failure_rolls_back_every_table_and_tombstone(tmp_path) -> None:
    stack = _seed_two_chats(tmp_path)
    with sqlite3.connect(stack["database_path"]) as connection:
        connection.executescript(
            """
            CREATE TRIGGER fail_chat_space_delete
            BEFORE DELETE ON spaces
            WHEN OLD.space_id = 'space-a'
            BEGIN
                SELECT RAISE(ABORT, 'injected deletion failure');
            END;
            """
        )

    service = SQLiteChatDeletionV2Service(database_path=stack["database_path"])
    with pytest.raises(RuntimeError, match="deletion|SQLite|persist"):
        service.delete_chat(scope=stack["scope"], operation_id="delete-chat-a")

    assert (
        _count(
            stack["database_path"], "executions", "execution_id = ?", ("execution-a",)
        )
        == 1
    )
    assert (
        _count(stack["database_path"], "candidates", "binding_id = ?", ("binding-a",))
        == 1
    )
    assert (
        _count(
            stack["database_path"],
            "promotion_bindings",
            "source_space_id = ?",
            ("space-a",),
        )
        == 1
    )
    assert not is_chat_deletion_tombstoned(
        database_path=stack["database_path"],
        owner_chat_id="chat-a",
    )

    with sqlite3.connect(stack["database_path"]) as connection:
        connection.execute("DROP TRIGGER fail_chat_space_delete")
    assert (
        service.delete_chat(
            scope=stack["scope"],
            operation_id="delete-chat-a",
        ).tombstone_revision
        == 1
    )


def _legacy_request(owner_chat_id: str, execution_id: str) -> LegacyBootstrapRequest:
    payload = LegacyBootstrapPayload(
        owner_chat_id=owner_chat_id,
        source_revision="source-revision-new",
        messages=(LegacyMessage("message-new", "user", "hello"),),
        generation=LegacyGenerationDescriptor(
            session_id=execution_id,
            execution_id=execution_id,
            generation_id="generation-new",
            attempt_id="attempt-new",
        ),
        preflight=LegacyBootstrapPreflight(
            proof_id="preflight-new",
            no_unfinished_durable_checkpoint=True,
            no_pending_interaction=True,
            host_snapshot_sanitized=True,
        ),
    )
    return LegacyBootstrapRequest(
        payload=payload,
        operation=build_legacy_bootstrap_operation(
            operation_id="bootstrap-new",
            payload=payload,
        ),
    )


def test_tombstone_blocks_workspace_curator_journal_and_legacy_resurrection(
    tmp_path,
) -> None:
    stack = _seed_two_chats(tmp_path)
    service = SQLiteChatDeletionV2Service(database_path=stack["database_path"])
    service.delete_chat(scope=stack["scope"], operation_id="delete-chat-a")

    with pytest.raises(ChatDeletedError, match="deleted"):
        service.assert_chat_active("chat-a")
    with pytest.raises(sqlite3.IntegrityError, match="chat_deleted"):
        stack["context"].bind_execution("execution-a")
    with pytest.raises(RuntimeError):
        stack["memory"].bind_workspace(
            space=MemorySpace(
                "space-new-a",
                "chat",
                "Chat memory",
                "replacement",
                1,
            ),
            owner_chat_id="chat-a",
        )
    with pytest.raises(RuntimeError):
        stack["curator"].bind_curation(
            binding_id="binding-new-a",
            owner_chat_id="chat-a",
            target_space_id="space-new-a",
        )
    with pytest.raises(LegacyBootstrapUnavailable):
        stack["legacy"].bootstrap(_legacy_request("chat-a", "execution-new-a"))

    # A different chat remains writable through every owner-aware boundary.
    stack["context"].bind_execution("execution-new-b")
    stack["memory"].bind_workspace(
        space=MemorySpace(
            "space-new-b",
            "chat",
            "Chat memory",
            "replacement",
            1,
        ),
        owner_chat_id="chat-b",
    )
    stack["curator"].bind_curation(
        binding_id="binding-new-b",
        owner_chat_id="chat-b",
        target_space_id="space-new-b",
    )


def test_delete_rejects_incomplete_or_foreign_exact_scope_before_mutation(
    tmp_path,
) -> None:
    stack = _seed_two_chats(tmp_path)
    service = SQLiteChatDeletionV2Service(database_path=stack["database_path"])

    with pytest.raises(ChatDeletionConflict, match="space|scope"):
        service.delete_chat(
            scope=ChatDeletionScope(
                owner_chat_id="chat-a",
                execution_ids=("execution-a",),
                binding_ids=("binding-a",),
            ),
            operation_id="delete-incomplete",
        )
    with pytest.raises(ChatDeletionConflict, match="owner|space|scope"):
        service.delete_chat(
            scope=ChatDeletionScope(
                owner_chat_id="chat-a",
                execution_ids=("execution-a",),
                space_ids=("space-a", "space-b"),
                binding_ids=("binding-a",),
            ),
            operation_id="delete-foreign",
        )

    assert (
        _count(stack["database_path"], "spaces", "owner_chat_id = ?", ("chat-a",)) == 1
    )
    assert not is_chat_deletion_tombstoned(
        database_path=stack["database_path"],
        owner_chat_id="chat-a",
    )
