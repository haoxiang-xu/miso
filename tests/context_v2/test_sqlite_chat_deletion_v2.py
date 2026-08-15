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
from unchain.persistence.sqlite_generation_lifecycle_v2 import (
    SQLiteHostGenerationLifecycleV2,
)
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


def _install_declared_host_extensions(database_path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE retained_owner_bindings (
                lifecycle_key TEXT PRIMARY KEY,
                owner_chat_id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                binding_id TEXT NOT NULL,
                chat_space_id TEXT NOT NULL
            );
            CREATE TABLE retained_owner_operations (
                lifecycle_key TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                PRIMARY KEY(lifecycle_key, operation_id),
                FOREIGN KEY(lifecycle_key)
                    REFERENCES retained_owner_bindings(lifecycle_key)
            );
            CREATE TABLE host_admission_operations (
                operation_id TEXT PRIMARY KEY,
                owner_chat_id TEXT NOT NULL
            );
            CREATE TABLE host_admissions (
                admission_id TEXT PRIMARY KEY,
                owner_chat_id TEXT NOT NULL UNIQUE
            );
            """
        )
        for suffix in ("a", "b"):
            connection.execute(
                "INSERT INTO retained_owner_bindings("
                "lifecycle_key, owner_chat_id, execution_id, binding_id, chat_space_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    f"lifecycle-{suffix}",
                    f"chat-{suffix}",
                    f"execution-{suffix}",
                    f"binding-{suffix}",
                    f"space-{suffix}",
                ),
            )
            connection.execute(
                "INSERT INTO retained_owner_operations(lifecycle_key, operation_id) "
                "VALUES (?, ?)",
                (f"lifecycle-{suffix}", f"ownership-operation-{suffix}"),
            )
            connection.execute(
                "INSERT INTO host_admission_operations(operation_id, owner_chat_id) "
                "VALUES (?, ?)",
                (f"admission-operation-{suffix}", f"chat-{suffix}"),
            )
            connection.execute(
                "INSERT INTO host_admissions(admission_id, owner_chat_id) VALUES (?, ?)",
                (f"admission-{suffix}", f"chat-{suffix}"),
            )


def _declared_extension_service(database_path):
    return SQLiteChatDeletionV2Service(
        database_path=database_path,
        retained_scope_tables={
            "retained_owner_bindings": (
                "owner_chat_id",
                "execution_id",
                "binding_id",
                "chat_space_id",
            ),
        },
        retained_owner_child_tables={
            "retained_owner_operations": (
                "retained_owner_bindings",
                "lifecycle_key",
            ),
        },
        owner_scoped_deletion_tables=(
            "host_admission_operations",
            "host_admissions",
        ),
    )


def _insert_complete_execution_extensions(
    connection,
    suffix: str,
    *,
    include_external_vector_point: bool = False,
) -> None:
    execution_id = f"execution-{suffix}"
    owner_chat_id = f"chat-{suffix}"
    session_id = f"session-{suffix}"
    generation_id = f"host-generation-{suffix}"
    operation_id = f"host-generation-operation-{suffix}"
    connection.execute(
        "INSERT INTO host_generation_chat_bindings("
        "owner_chat_id, execution_id, session_id) VALUES (?, ?, ?)",
        (owner_chat_id, execution_id, session_id),
    )
    connection.execute(
        """
        INSERT INTO host_generation_records(
            owner_chat_id, execution_id, session_id, generation_id,
            transition_kind, previous_generation_id, revision,
            operation_id, payload_sha256
        ) VALUES (?, ?, ?, ?, 'initial', '', 1, ?, ?)
        """,
        (
            owner_chat_id,
            execution_id,
            session_id,
            generation_id,
            operation_id,
            "1" * 64,
        ),
    )
    connection.execute(
        """
        INSERT INTO host_generation_operations(
            owner_chat_id, operation_id, payload_sha256, mutation_kind,
            result_generation_id, result_revision, result_attempt_id
        ) VALUES (?, ?, ?, 'transition', ?, 1, ?)
        """,
        (
            owner_chat_id,
            operation_id,
            "1" * 64,
            generation_id,
            f"attempt-{suffix}",
        ),
    )
    connection.execute(
        """
        INSERT INTO host_generation_heads(
            owner_chat_id, execution_id, session_id,
            current_generation_id, revision
        ) VALUES (?, ?, ?, ?, 1)
        """,
        (owner_chat_id, execution_id, session_id, generation_id),
    )
    connection.execute(
        """
        INSERT INTO host_generation_attempt_bindings(
            owner_chat_id, execution_id, session_id, generation_id,
            attempt_id, head_revision, operation_id, payload_sha256
        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            owner_chat_id,
            execution_id,
            session_id,
            generation_id,
            f"attempt-{suffix}",
            operation_id,
            "1" * 64,
        ),
    )
    connection.execute(
        """
        INSERT INTO run_bundle_receipts_v1(
            execution_id, provider_call_id, attempt_id, root_run_id,
            owner_run_id, receipt_json, receipt_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            execution_id,
            f"provider-call-{suffix}",
            f"attempt-{suffix}",
            f"root-run-{suffix}",
            f"run-{suffix}",
            b"{}",
            _sha(b"{}"),
        ),
    )
    connection.execute(
        """
        INSERT INTO run_bundle_projections_v1(
            execution_id, bundle_id, revision, attempt_id,
            root_run_id, run_id, bundle_json, bundle_digest
        ) VALUES (?, ?, 1, ?, ?, ?, ?, ?)
        """,
        (
            execution_id,
            f"bundle-{suffix}",
            f"attempt-{suffix}",
            f"root-run-{suffix}",
            f"run-{suffix}",
            b"{}",
            _sha(b"{}"),
        ),
    )
    connection.execute(
        """
        INSERT INTO run_bundle_continuation_links_v1(
            execution_id, successor_bundle_id, successor_run_id,
            successor_attempt_id, predecessor_bundle_id, predecessor_run_id
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            execution_id,
            f"successor-bundle-{suffix}",
            f"successor-run-{suffix}",
            f"successor-attempt-{suffix}",
            f"predecessor-bundle-{suffix}",
            f"predecessor-run-{suffix}",
        ),
    )
    connection.execute(
        """
        INSERT INTO vector_projection_receipts(
            space_id, entry_id, entry_revision, backend_identity,
            chunker_version, basis_id, basis_version, algorithm,
            dimension, expected_chunks, indexed_chunks, content_digest, complete
        ) VALUES (?, ?, 1, 'backend', 'chunker-v1', 'basis', 1,
                  'pca', 2, 1, 1, ?, 1)
        """,
        (f"space-{suffix}", f"entry-{suffix}", "2" * 64),
    )
    if include_external_vector_point:
        connection.execute(
            """
            INSERT INTO vector_projection_points(
                space_id, entry_id, entry_revision, backend_identity,
                basis_id, basis_version, chunk_id, ordinal, x, y,
                embedding_digest, external_receipt_id
            ) VALUES (?, ?, 1, 'backend', 'basis', 1, 'chunk', 0,
                      0.25, 0.75, ?, ?)
            """,
            (
                f"space-{suffix}",
                f"entry-{suffix}",
                "3" * 64,
                f"external-receipt-{suffix}",
            ),
        )
    connection.execute(
        """
        INSERT INTO vector_projection_watermarks(
            space_id, backend_identity, chunker_version, basis_id,
            basis_version, algorithm, dimension, corpus_epoch
        ) VALUES (?, 'backend', 'chunker-v1', 'basis', 1, 'pca', 2, 1)
        """,
        (f"space-{suffix}",),
    )


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


def test_delete_covers_known_execution_and_space_extension_closure(tmp_path) -> None:
    stack = _seed_two_chats(tmp_path)
    SQLiteHostGenerationLifecycleV2(stack["context"])
    with sqlite3.connect(stack["database_path"]) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _insert_complete_execution_extensions(connection, "a")
        _insert_complete_execution_extensions(
            connection,
            "b",
            include_external_vector_point=True,
        )

    receipt = SQLiteChatDeletionV2Service(
        database_path=stack["database_path"]
    ).delete_chat(scope=stack["scope"], operation_id="delete-complete-closure-a")

    for table, column, deleted_value, retained_value in (
        ("host_generation_attempt_bindings", "owner_chat_id", "chat-a", "chat-b"),
        ("host_generation_heads", "owner_chat_id", "chat-a", "chat-b"),
        ("host_generation_operations", "owner_chat_id", "chat-a", "chat-b"),
        ("host_generation_records", "owner_chat_id", "chat-a", "chat-b"),
        ("host_generation_chat_bindings", "owner_chat_id", "chat-a", "chat-b"),
        ("run_bundle_receipts_v1", "execution_id", "execution-a", "execution-b"),
        ("run_bundle_projections_v1", "execution_id", "execution-a", "execution-b"),
        (
            "run_bundle_continuation_links_v1",
            "execution_id",
            "execution-a",
            "execution-b",
        ),
        ("vector_projection_receipts", "space_id", "space-a", "space-b"),
        ("vector_projection_watermarks", "space_id", "space-a", "space-b"),
    ):
        assert _count(
            stack["database_path"], table, f"{column} = ?", (deleted_value,)
        ) == 0
        assert _count(
            stack["database_path"], table, f"{column} = ?", (retained_value,)
        ) == 1
        assert receipt.deleted_rows[table] == 1
    assert _count(
        stack["database_path"],
        "vector_projection_points",
        "space_id = ?",
        ("space-a",),
    ) == 0
    assert _count(
        stack["database_path"],
        "vector_projection_points",
        "space_id = ?",
        ("space-b",),
    ) == 1


def test_delete_preserves_external_vector_cleanup_handle_and_fails_atomically(
    tmp_path,
) -> None:
    stack = _seed_two_chats(tmp_path)
    SQLiteHostGenerationLifecycleV2(stack["context"])
    with sqlite3.connect(stack["database_path"]) as connection:
        _insert_complete_execution_extensions(
            connection,
            "a",
            include_external_vector_point=True,
        )

    with pytest.raises(ChatDeletionUnavailable, match="external vector cleanup"):
        SQLiteChatDeletionV2Service(
            database_path=stack["database_path"]
        ).delete_chat(
            scope=stack["scope"],
            operation_id="delete-external-vector-a",
        )

    with sqlite3.connect(stack["database_path"]) as connection:
        point = connection.execute(
            "SELECT external_receipt_id FROM vector_projection_points "
            "WHERE space_id = 'space-a'"
        ).fetchone()
    assert point == ("external-receipt-a",)
    assert _count(
        stack["database_path"], "executions", "execution_id = ?", ("execution-a",)
    ) == 1
    assert not is_chat_deletion_tombstoned(
        database_path=stack["database_path"],
        owner_chat_id="chat-a",
    )


def test_declared_owner_extensions_delete_atomically_and_provenance_is_immutable(
    tmp_path,
) -> None:
    stack = _seed_two_chats(tmp_path)
    _install_declared_host_extensions(stack["database_path"])
    service = _declared_extension_service(stack["database_path"])

    receipt = service.delete_chat(
        scope=stack["scope"],
        operation_id="delete-declared-extensions-a",
    )

    assert receipt.deleted_rows["host_admission_operations"] == 1
    assert receipt.deleted_rows["host_admissions"] == 1
    assert _count(
        stack["database_path"],
        "retained_owner_bindings",
        "owner_chat_id = ?",
        ("chat-a",),
    ) == 1
    assert _count(
        stack["database_path"],
        "retained_owner_operations",
        "lifecycle_key = ?",
        ("lifecycle-a",),
    ) == 1

    _declared_extension_service(stack["database_path"])
    with sqlite3.connect(stack["database_path"]) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError, match="chat_deleted"):
            connection.execute(
                "INSERT INTO host_admissions(admission_id, owner_chat_id) "
                "VALUES ('resurrected-admission', 'chat-a')"
            )
        with pytest.raises(sqlite3.IntegrityError, match="chat_deleted"):
            connection.execute(
                "INSERT INTO retained_owner_bindings("
                "lifecycle_key, owner_chat_id, execution_id, binding_id, chat_space_id) "
                "VALUES ('resurrected-lifecycle', 'chat-a', 'execution-new', "
                "'binding-new', 'space-new')"
            )
        with pytest.raises(sqlite3.IntegrityError, match="chat_deleted"):
            connection.execute(
                "INSERT INTO retained_owner_operations(lifecycle_key, operation_id) "
                "VALUES ('lifecycle-a', 'ownership-operation-new')"
            )
        with pytest.raises(sqlite3.IntegrityError, match="chat_deleted"):
            connection.execute(
                "DELETE FROM retained_owner_operations "
                "WHERE lifecycle_key = 'lifecycle-a'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="chat_deleted"):
            connection.execute(
                "DELETE FROM retained_owner_bindings "
                "WHERE lifecycle_key = 'lifecycle-a'"
            )


def test_declared_owner_extension_replay_reconciles_without_changing_receipt(
    tmp_path,
) -> None:
    stack = _seed_two_chats(tmp_path)
    _install_declared_host_extensions(stack["database_path"])
    first = _declared_extension_service(stack["database_path"]).delete_chat(
        scope=stack["scope"],
        operation_id="delete-declared-extensions-reconcile-a",
    )

    with sqlite3.connect(stack["database_path"]) as connection:
        trigger_names = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name IN "
                "('host_admission_operations', 'host_admissions')"
            )
        )
        for trigger_name in trigger_names:
            connection.execute(f'DROP TRIGGER "{trigger_name}"')
        connection.execute(
            "INSERT INTO host_admission_operations(operation_id, owner_chat_id) "
            "VALUES ('late-operation-a', 'chat-a')"
        )
        connection.execute(
            "INSERT INTO host_admissions(admission_id, owner_chat_id) "
            "VALUES ('late-admission-a', 'chat-a')"
        )

    replay = _declared_extension_service(stack["database_path"]).delete_chat(
        scope=stack["scope"],
        operation_id="delete-declared-extensions-reconcile-a",
    )

    assert replay.replayed is True
    assert dict(replay.deleted_rows) == dict(first.deleted_rows)
    assert replay.deleted_rows["host_admission_operations"] == 1
    assert replay.deleted_rows["host_admissions"] == 1
    assert _count(
        stack["database_path"],
        "host_admission_operations",
        "owner_chat_id = ?",
        ("chat-a",),
    ) == 0
    assert _count(
        stack["database_path"],
        "host_admissions",
        "owner_chat_id = ?",
        ("chat-a",),
    ) == 0


def test_empty_scope_owner_evidence_is_rejected_in_core_transaction(tmp_path) -> None:
    stack = _seed_two_chats(tmp_path)
    _install_declared_host_extensions(stack["database_path"])
    with sqlite3.connect(stack["database_path"]) as connection:
        connection.execute(
            "INSERT INTO host_admissions(admission_id, owner_chat_id) "
            "VALUES ('admission-empty', 'chat-empty')"
        )

    empty_scope = ChatDeletionScope(owner_chat_id="chat-empty")
    with pytest.raises(ChatDeletionConflict, match="empty.*owner evidence"):
        _declared_extension_service(stack["database_path"]).delete_chat(
            scope=empty_scope,
            operation_id="delete-empty-with-owner-evidence",
        )

    assert _count(
        stack["database_path"],
        "host_admissions",
        "owner_chat_id = ?",
        ("chat-empty",),
    ) == 1
    assert not is_chat_deletion_tombstoned(
        database_path=stack["database_path"], owner_chat_id="chat-empty"
    )


def test_empty_scope_without_owner_evidence_tombstones_and_cold_replays(
    tmp_path,
) -> None:
    stack = _seed_two_chats(tmp_path)
    empty_scope = ChatDeletionScope(owner_chat_id="chat-empty")

    first = SQLiteChatDeletionV2Service(
        database_path=stack["database_path"]
    ).delete_chat(scope=empty_scope, operation_id="delete-empty-scope")
    replay = SQLiteChatDeletionV2Service(
        database_path=stack["database_path"]
    ).delete_chat(scope=empty_scope, operation_id="delete-empty-scope")

    assert first.replayed is False
    assert replay.replayed is True
    assert dict(replay.deleted_rows) == dict(first.deleted_rows)
    assert is_chat_deletion_tombstoned(
        database_path=stack["database_path"], owner_chat_id="chat-empty"
    )


def test_tombstone_guards_modern_host_and_vector_resurrection_after_restart(
    tmp_path,
) -> None:
    stack = _seed_two_chats(tmp_path)
    SQLiteHostGenerationLifecycleV2(stack["context"])
    with sqlite3.connect(stack["database_path"]) as connection:
        _insert_complete_execution_extensions(connection, "a")

    SQLiteChatDeletionV2Service(
        database_path=stack["database_path"]
    ).delete_chat(scope=stack["scope"], operation_id="delete-modern-guards-a")
    SQLiteChatDeletionV2Service(database_path=stack["database_path"])

    with sqlite3.connect(stack["database_path"]) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO executions(execution_id) VALUES ('execution-new-a')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="chat_deleted"):
            connection.execute(
                "INSERT INTO host_generation_chat_bindings("
                "owner_chat_id, execution_id, session_id) "
                "VALUES ('chat-a', 'execution-new-a', 'session-new-a')"
            )
        with pytest.raises(sqlite3.IntegrityError, match="chat_deleted"):
            connection.execute(
                "INSERT INTO vector_projection_receipts("
                "space_id, entry_id, entry_revision, backend_identity, "
                "chunker_version, basis_id, basis_version, algorithm, dimension, "
                "expected_chunks, indexed_chunks, content_digest, complete) "
                "VALUES ('space-a', 'entry-new-a', 1, 'backend', 'chunker', "
                "'basis', 1, 'algorithm', 2, 1, 1, 'digest', 1)"
            )
        with pytest.raises(sqlite3.IntegrityError, match="chat_deleted"):
            connection.execute(
                "INSERT INTO vector_projection_watermarks("
                "space_id, backend_identity, chunker_version, basis_id, "
                "basis_version, algorithm, dimension, corpus_epoch) "
                "VALUES ('space-a', 'backend', 'chunker', 'basis', 1, "
                "'algorithm', 2, 1)"
            )
        with pytest.raises(sqlite3.IntegrityError, match="chat_deleted"):
            connection.execute(
                "INSERT INTO vector_projection_points("
                "space_id, entry_id, entry_revision, backend_identity, basis_id, "
                "basis_version, chunk_id, ordinal, x, y, embedding_digest, "
                "external_receipt_id) VALUES ('space-a', 'entry-new-a', 1, "
                "'backend', 'basis', 1, 'chunk-new-a', 0, 0.0, 0.0, "
                "'embedding', 'external-new-a')"
            )


def test_retained_owner_scope_rejects_foreign_execution_before_tombstone(
    tmp_path,
) -> None:
    stack = _seed_two_chats(tmp_path)
    _install_declared_host_extensions(stack["database_path"])
    with sqlite3.connect(stack["database_path"]) as connection:
        connection.execute(
            "INSERT INTO retained_owner_bindings("
            "lifecycle_key, owner_chat_id, execution_id, binding_id, chat_space_id) "
            "VALUES ('lifecycle-foreign', 'chat-b', 'execution-a', "
            "'binding-foreign', 'space-foreign')"
        )

    with pytest.raises(ChatDeletionConflict, match="foreign owner"):
        _declared_extension_service(stack["database_path"]).delete_chat(
            scope=stack["scope"],
            operation_id="delete-foreign-provenance-a",
        )

    assert _count(
        stack["database_path"], "executions", "execution_id = ?", ("execution-a",)
    ) == 1
    assert not is_chat_deletion_tombstoned(
        database_path=stack["database_path"], owner_chat_id="chat-a"
    )


def test_retained_owner_child_declaration_requires_exact_foreign_key(tmp_path) -> None:
    stack = _seed_two_chats(tmp_path)
    _install_declared_host_extensions(stack["database_path"])
    with sqlite3.connect(stack["database_path"]) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("ALTER TABLE retained_owner_operations RENAME TO old_ops")
        connection.execute(
            "CREATE TABLE retained_owner_operations("
            "lifecycle_key TEXT NOT NULL, operation_id TEXT NOT NULL, "
            "PRIMARY KEY(lifecycle_key, operation_id))"
        )
        connection.execute("DROP TABLE old_ops")

    with pytest.raises(ChatDeletionUnavailable, match="child foreign key"):
        _declared_extension_service(stack["database_path"])


def test_retained_child_guard_is_rebuilt_when_join_declaration_changes(
    tmp_path,
) -> None:
    stack = _seed_two_chats(tmp_path)
    with sqlite3.connect(stack["database_path"]) as connection:
        connection.executescript(
            """
            CREATE TABLE retained_owner_bindings (
                lifecycle_key TEXT PRIMARY KEY,
                alternate_key TEXT NOT NULL UNIQUE,
                owner_chat_id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                binding_id TEXT NOT NULL,
                chat_space_id TEXT NOT NULL
            );
            CREATE TABLE retained_owner_operations (
                lifecycle_key TEXT NOT NULL,
                alternate_key TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                PRIMARY KEY(lifecycle_key, operation_id),
                FOREIGN KEY(lifecycle_key)
                    REFERENCES retained_owner_bindings(lifecycle_key),
                FOREIGN KEY(alternate_key)
                    REFERENCES retained_owner_bindings(alternate_key)
            );
            """
        )

    common_options = {
        "database_path": stack["database_path"],
        "retained_scope_tables": {
            "retained_owner_bindings": (
                "owner_chat_id",
                "execution_id",
                "binding_id",
                "chat_space_id",
            ),
        },
    }
    SQLiteChatDeletionV2Service(
        **common_options,
        retained_owner_child_tables={
            "retained_owner_operations": (
                "retained_owner_bindings",
                "lifecycle_key",
            ),
        },
    )
    SQLiteChatDeletionV2Service(
        **common_options,
        retained_owner_child_tables={
            "retained_owner_operations": (
                "retained_owner_bindings",
                "alternate_key",
            ),
        },
    )

    with sqlite3.connect(stack["database_path"]) as connection:
        trigger_sql = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name = 'retained_owner_operations'"
            )
        )
    assert len(trigger_sql) == 3
    assert all('parent."alternate_key"' in sql for sql in trigger_sql)
    assert all('parent."lifecycle_key"' not in sql for sql in trigger_sql)


def test_known_deletion_table_rejects_new_reverse_foreign_key(tmp_path) -> None:
    stack = _seed_two_chats(tmp_path)
    with sqlite3.connect(stack["database_path"]) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DROP TABLE run_bundle_projections_v1")
        connection.execute(
            "CREATE TABLE run_bundle_projections_v1("
            "execution_id TEXT NOT NULL, bundle_id TEXT NOT NULL, "
            "revision INTEGER NOT NULL, provider_call_id TEXT, "
            "PRIMARY KEY(execution_id, bundle_id, revision), "
            "FOREIGN KEY(execution_id) REFERENCES executions(execution_id), "
            "FOREIGN KEY(execution_id, provider_call_id) "
            "REFERENCES run_bundle_receipts_v1(execution_id, provider_call_id))"
        )

    with pytest.raises(ChatDeletionUnavailable, match="foreign key order"):
        SQLiteChatDeletionV2Service(database_path=stack["database_path"])


@pytest.mark.parametrize(
    "missing_table",
    (
        "host_generation_heads",
        "run_bundle_receipts_v1",
        "vector_projection_points",
    ),
)
def test_deletion_schema_gate_rejects_incomplete_known_optional_group(
    tmp_path,
    missing_table: str,
) -> None:
    stack = _seed_two_chats(tmp_path)
    SQLiteHostGenerationLifecycleV2(stack["context"])
    with sqlite3.connect(stack["database_path"]) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(f"DROP TABLE {missing_table}")

    with pytest.raises(ChatDeletionUnavailable, match="schema|group|incomplete"):
        SQLiteChatDeletionV2Service(database_path=stack["database_path"])


def test_deletion_schema_gate_rejects_unknown_foreign_key_child(tmp_path) -> None:
    stack = _seed_two_chats(tmp_path)
    with sqlite3.connect(stack["database_path"]) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE unknown_execution_extension (
                extension_id TEXT PRIMARY KEY,
                execution_id TEXT NOT NULL,
                FOREIGN KEY (execution_id) REFERENCES executions(execution_id)
            );
            INSERT INTO unknown_execution_extension(extension_id, execution_id)
            VALUES ('extension-a', 'execution-a');
            """
        )

    with pytest.raises(ChatDeletionUnavailable, match="foreign key|closure|schema"):
        SQLiteChatDeletionV2Service(database_path=stack["database_path"])


def test_deletion_schema_gate_rejects_unknown_unconstrained_scope_columns(
    tmp_path,
) -> None:
    stack = _seed_two_chats(tmp_path)
    with sqlite3.connect(stack["database_path"]) as connection:
        connection.executescript(
            """
            CREATE TABLE unknown_logical_scope_extension (
                extension_id TEXT PRIMARY KEY,
                owner_chat_id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                space_id TEXT NOT NULL,
                binding_id TEXT NOT NULL
            );
            INSERT INTO unknown_logical_scope_extension(
                extension_id, owner_chat_id, execution_id, space_id, binding_id
            ) VALUES (
                'extension-a', 'chat-a', 'execution-a', 'space-a', 'binding-a'
            );
            """
        )

    with pytest.raises(ChatDeletionUnavailable, match="scoped-column|closure"):
        SQLiteChatDeletionV2Service(database_path=stack["database_path"])


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
