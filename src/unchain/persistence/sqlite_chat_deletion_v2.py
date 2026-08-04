"""Transactional chat deletion for the shared Context/Memory V2 data plane.

Object bytes are intentionally not garbage-collected here.  The durable
tombstone and exact deleted scopes remain available after restart so host
attachment and bootstrap paths can fail closed instead of recreating a chat.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$")
_SCOPE_SCHEMA = "unchain.chat_deletion_scope.v1"
_RECEIPT_SCHEMA = "unchain.chat_deletion_receipt.v1"
_SCHEMA_VERSION = 1


class ChatDeletionError(RuntimeError):
    """Base failure for the durable chat-deletion boundary."""


class ChatDeletionConflict(ChatDeletionError):
    """The requested owner/scope or idempotency payload changed."""


class ChatDeletionUnavailable(ChatDeletionError):
    """The complete shared data plane cannot be deleted transactionally."""


class ChatDeletedError(ChatDeletionError):
    """A durable tombstone forbids recreating the chat scope."""


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text")
    normalized = value.strip()
    if _IDENTIFIER_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} is invalid")
    return normalized


def _identifiers(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} must be a collection of identifiers")
    normalized = tuple(_identifier(value, field_name) for value in values)
    if len(normalized) > 10_000:
        raise ValueError(f"{field_name} exceeds the deletion scope limit")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} contains duplicate identifiers")
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class ChatDeletionScope:
    """Host-resolved exact durable scopes belonging to one chat."""

    owner_chat_id: str
    execution_ids: tuple[str, ...] = ()
    space_ids: tuple[str, ...] = ()
    binding_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "owner_chat_id",
            _identifier(self.owner_chat_id, "owner_chat_id"),
        )
        for field_name in ("execution_ids", "space_ids", "binding_ids"):
            object.__setattr__(
                self,
                field_name,
                _identifiers(getattr(self, field_name), field_name),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _SCOPE_SCHEMA,
            "owner_chat_id": self.owner_chat_id,
            "execution_ids": list(self.execution_ids),
            "space_ids": list(self.space_ids),
            "binding_ids": list(self.binding_ids),
        }


@dataclass(frozen=True, slots=True)
class ChatDeletionReceipt:
    owner_chat_id: str
    tombstone_revision: int
    deleted_rows: Mapping[str, int]
    pending_unreferenced_scan: bool
    replayed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "owner_chat_id",
            _identifier(self.owner_chat_id, "owner_chat_id"),
        )
        if self.tombstone_revision != 1:
            raise ValueError("tombstone_revision must be one")
        counts: dict[str, int] = {}
        for key, value in self.deleted_rows.items():
            normalized = _identifier(key, "deleted_rows key")
            if type(value) is not int or value < 0:
                raise ValueError("deleted row counts must be non-negative integers")
            counts[normalized] = value
        object.__setattr__(
            self, "deleted_rows", MappingProxyType(dict(sorted(counts.items())))
        )
        if self.pending_unreferenced_scan is not True:
            raise ValueError("P0 deletion must defer the unreferenced object scan")
        if type(self.replayed) is not bool:
            raise TypeError("replayed must be a boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _RECEIPT_SCHEMA,
            "owner_chat_id": self.owner_chat_id,
            "tombstone_revision": self.tombstone_revision,
            "deleted_rows": dict(self.deleted_rows),
            "pending_unreferenced_scan": self.pending_unreferenced_scan,
        }


@dataclass(frozen=True, slots=True)
class ChatDeletionTombstone:
    scope: ChatDeletionScope
    receipt: ChatDeletionReceipt
    first_operation_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ChatDeletionScope):
            raise TypeError("scope must be a ChatDeletionScope")
        if not isinstance(self.receipt, ChatDeletionReceipt):
            raise TypeError("receipt must be a ChatDeletionReceipt")
        if self.scope.owner_chat_id != self.receipt.owner_chat_id:
            raise ValueError("tombstone scope and receipt owners must match")
        object.__setattr__(
            self,
            "first_operation_id",
            _identifier(self.first_operation_id, "first_operation_id"),
        )


def _canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeError) as error:
        raise ChatDeletionConflict(
            "chat deletion payload is not canonical JSON"
        ) from error


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


_REQUIRED_TABLES = frozenset(
    {
        "executions",
        "operations",
        "events",
        "event_receipts",
        "artifacts",
        "provider_request_lease_revisions",
        "provider_request_lease_heads",
        "provider_turn_result_receipts",
        "checkpoints",
        "context_builds",
        "spaces",
        "entries",
        "entry_revisions",
        "links",
        "memory_operation_receipts",
        "task_state_heads",
        "task_state_revisions",
        "index_state",
        "workspace_entries_fts",
        "curation_scopes",
        "curation_run_scopes",
        "candidates",
        "candidate_revisions",
        "consolidation_jobs",
        "consolidation_job_revisions",
        "candidate_bindings",
        "curator_operation_receipts",
        "memory_review_proposals",
        "promotion_namespace_bindings",
        "promotion_bindings",
        "promotion_proposals",
        "promotion_revisions",
        "promotion_operation_receipts",
        "legacy_bootstrap_manifests",
        "legacy_bootstrap_chat_heads",
    }
)


_GUARD_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS chat_deletion_guard_execution_insert
BEFORE INSERT ON executions
WHEN EXISTS (
    SELECT 1 FROM chat_deletion_execution_scopes
    WHERE execution_id = NEW.execution_id
)
BEGIN
    SELECT RAISE(ABORT, 'chat_deleted');
END;

CREATE TRIGGER IF NOT EXISTS chat_deletion_guard_space_insert
BEFORE INSERT ON spaces
WHEN NEW.owner_chat_id != '' AND EXISTS (
    SELECT 1 FROM chat_deletion_tombstones
    WHERE owner_chat_id = NEW.owner_chat_id
)
BEGIN
    SELECT RAISE(ABORT, 'chat_deleted');
END;

CREATE TRIGGER IF NOT EXISTS chat_deletion_guard_space_update
BEFORE UPDATE ON spaces
WHEN (NEW.owner_chat_id != '' AND EXISTS (
    SELECT 1 FROM chat_deletion_tombstones
    WHERE owner_chat_id = NEW.owner_chat_id
)) OR EXISTS (
    SELECT 1 FROM chat_deletion_space_scopes
    WHERE space_id = NEW.space_id
)
BEGIN
    SELECT RAISE(ABORT, 'chat_deleted');
END;

CREATE TRIGGER IF NOT EXISTS chat_deletion_guard_curation_insert
BEFORE INSERT ON curation_scopes
WHEN EXISTS (
    SELECT 1 FROM chat_deletion_tombstones
    WHERE owner_chat_id = NEW.owner_chat_id
)
BEGIN
    SELECT RAISE(ABORT, 'chat_deleted');
END;

CREATE TRIGGER IF NOT EXISTS chat_deletion_guard_legacy_manifest_insert
BEFORE INSERT ON legacy_bootstrap_manifests
WHEN EXISTS (
    SELECT 1 FROM chat_deletion_tombstones
    WHERE owner_chat_id = NEW.owner_chat_id
)
BEGIN
    SELECT RAISE(ABORT, 'chat_deleted');
END;

CREATE TRIGGER IF NOT EXISTS chat_deletion_guard_legacy_head_insert
BEFORE INSERT ON legacy_bootstrap_chat_heads
WHEN EXISTS (
    SELECT 1 FROM chat_deletion_tombstones
    WHERE owner_chat_id = NEW.owner_chat_id
)
BEGIN
    SELECT RAISE(ABORT, 'chat_deleted');
END;

CREATE TRIGGER IF NOT EXISTS chat_deletion_guard_task_head_insert
BEFORE INSERT ON task_state_heads
WHEN EXISTS (
    SELECT 1 FROM chat_deletion_binding_scopes
    WHERE binding_id = NEW.binding_id
)
BEGIN
    SELECT RAISE(ABORT, 'chat_deleted');
END;

CREATE TRIGGER IF NOT EXISTS chat_deletion_guard_task_revision_insert
BEFORE INSERT ON task_state_revisions
WHEN EXISTS (
    SELECT 1 FROM chat_deletion_binding_scopes
    WHERE binding_id = NEW.binding_id
)
BEGIN
    SELECT RAISE(ABORT, 'chat_deleted');
END;

CREATE TRIGGER IF NOT EXISTS chat_deletion_guard_review_insert
BEFORE INSERT ON memory_review_proposals
WHEN EXISTS (
    SELECT 1 FROM chat_deletion_binding_scopes
    WHERE binding_id = NEW.binding_id
)
BEGIN
    SELECT RAISE(ABORT, 'chat_deleted');
END;
"""


class SQLiteChatDeletionV2Service:
    """Delete one exact chat scope and persist a non-resurrectable tombstone."""

    def __init__(self, *, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        if not self.database_path.is_file():
            raise ChatDeletionUnavailable("shared Context V2 database is unavailable")
        self._initialize()

    def _initialize(self) -> None:
        connection = _connect(self.database_path)
        try:
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).casefold() != "wal":
                raise ChatDeletionUnavailable("SQLite WAL is unavailable")
            existing = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                )
            }
            missing = sorted(_REQUIRED_TABLES - existing)
            if missing:
                raise ChatDeletionUnavailable(
                    "shared Context/Memory V2 schema is incomplete: "
                    + ", ".join(missing)
                )
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS chat_deletion_v2_schema (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT OR IGNORE INTO chat_deletion_v2_schema(version) VALUES (1);

                CREATE TABLE IF NOT EXISTS chat_deletion_tombstones (
                    owner_chat_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL CHECK(revision = 1),
                    first_operation_id TEXT NOT NULL,
                    scope_json BLOB NOT NULL,
                    scope_sha256 TEXT NOT NULL,
                    result_json BLOB NOT NULL,
                    result_sha256 TEXT NOT NULL,
                    deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS chat_deletion_execution_scopes (
                    owner_chat_id TEXT NOT NULL,
                    execution_id TEXT NOT NULL UNIQUE,
                    PRIMARY KEY(owner_chat_id, execution_id),
                    FOREIGN KEY(owner_chat_id)
                        REFERENCES chat_deletion_tombstones(owner_chat_id)
                );
                CREATE TABLE IF NOT EXISTS chat_deletion_space_scopes (
                    owner_chat_id TEXT NOT NULL,
                    space_id TEXT NOT NULL UNIQUE,
                    PRIMARY KEY(owner_chat_id, space_id),
                    FOREIGN KEY(owner_chat_id)
                        REFERENCES chat_deletion_tombstones(owner_chat_id)
                );
                CREATE TABLE IF NOT EXISTS chat_deletion_binding_scopes (
                    owner_chat_id TEXT NOT NULL,
                    binding_id TEXT NOT NULL UNIQUE,
                    PRIMARY KEY(owner_chat_id, binding_id),
                    FOREIGN KEY(owner_chat_id)
                        REFERENCES chat_deletion_tombstones(owner_chat_id)
                );
                CREATE TABLE IF NOT EXISTS chat_deletion_operations (
                    owner_chat_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    result_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(owner_chat_id, operation_id),
                    FOREIGN KEY(owner_chat_id)
                        REFERENCES chat_deletion_tombstones(owner_chat_id)
                );
                """
                + _GUARD_TRIGGERS
                + "COMMIT;"
            )
            versions = {
                int(row[0])
                for row in connection.execute(
                    "SELECT version FROM chat_deletion_v2_schema"
                )
            }
            if versions != {_SCHEMA_VERSION}:
                raise ChatDeletionUnavailable(
                    "chat deletion SQLite schema is unsupported"
                )
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ChatDeletionUnavailable("chat deletion SQLite quick_check failed")
        except ChatDeletionError:
            connection.rollback()
            raise
        except sqlite3.Error as error:
            connection.rollback()
            raise ChatDeletionUnavailable(
                "chat deletion SQLite schema initialization failed"
            ) from error
        finally:
            connection.close()

    def is_chat_tombstoned(self, owner_chat_id: str) -> bool:
        return is_chat_deletion_tombstoned(
            database_path=self.database_path,
            owner_chat_id=owner_chat_id,
        )

    def assert_chat_active(self, owner_chat_id: str) -> None:
        owner = _identifier(owner_chat_id, "owner_chat_id")
        if self.is_chat_tombstoned(owner):
            raise ChatDeletedError(f"chat {owner!r} was durably deleted")

    @staticmethod
    def _decode_receipt(
        row: sqlite3.Row,
        *,
        owner_chat_id: str,
        replayed: bool,
    ) -> ChatDeletionReceipt:
        encoded = bytes(row["result_json"])
        if _sha256(encoded) != row["result_sha256"]:
            raise ChatDeletionUnavailable("chat deletion receipt digest changed")
        try:
            raw = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ChatDeletionUnavailable(
                "chat deletion receipt is malformed"
            ) from error
        if not isinstance(raw, Mapping) or _canonical_json_bytes(raw) != encoded:
            raise ChatDeletionUnavailable("chat deletion receipt is not canonical")
        if (
            set(raw)
            != {
                "schema",
                "owner_chat_id",
                "tombstone_revision",
                "deleted_rows",
                "pending_unreferenced_scan",
            }
            or raw["schema"] != _RECEIPT_SCHEMA
        ):
            raise ChatDeletionUnavailable("chat deletion receipt shape changed")
        if raw["owner_chat_id"] != owner_chat_id:
            raise ChatDeletionUnavailable("chat deletion receipt owner changed")
        try:
            return ChatDeletionReceipt(
                owner_chat_id=raw["owner_chat_id"],
                tombstone_revision=raw["tombstone_revision"],
                deleted_rows=raw["deleted_rows"],
                pending_unreferenced_scan=raw["pending_unreferenced_scan"],
                replayed=replayed,
            )
        except (TypeError, ValueError) as error:
            raise ChatDeletionUnavailable("chat deletion receipt is invalid") from error

    @staticmethod
    def _verify_tombstone_evidence(
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        scope: ChatDeletionScope,
    ) -> None:
        for table_name, id_column, expected in (
            (
                "chat_deletion_execution_scopes",
                "execution_id",
                scope.execution_ids,
            ),
            ("chat_deletion_space_scopes", "space_id", scope.space_ids),
            ("chat_deletion_binding_scopes", "binding_id", scope.binding_ids),
        ):
            actual = tuple(
                sorted(
                    str(item[0])
                    for item in connection.execute(
                        f"SELECT {id_column} FROM {table_name} "
                        "WHERE owner_chat_id = ?",
                        (scope.owner_chat_id,),
                    )
                )
            )
            if actual != expected:
                raise ChatDeletionUnavailable(
                    "chat deletion tombstone scope evidence changed"
                )
        first_operation = connection.execute(
            """
            SELECT payload_sha256, result_sha256
            FROM chat_deletion_operations
            WHERE owner_chat_id = ? AND operation_id = ?
            """,
            (scope.owner_chat_id, row["first_operation_id"]),
        ).fetchone()
        if first_operation is None or (
            first_operation["payload_sha256"] != row["scope_sha256"]
            or first_operation["result_sha256"] != row["result_sha256"]
        ):
            raise ChatDeletionUnavailable(
                "chat deletion tombstone operation evidence changed"
            )
        drifted_operation = connection.execute(
            """
            SELECT 1 FROM chat_deletion_operations
            WHERE owner_chat_id = ?
              AND (payload_sha256 != ? OR result_sha256 != ?)
            LIMIT 1
            """,
            (
                scope.owner_chat_id,
                row["scope_sha256"],
                row["result_sha256"],
            ),
        ).fetchone()
        if drifted_operation is not None:
            raise ChatDeletionUnavailable(
                "chat deletion tombstone operation evidence changed"
            )

    @staticmethod
    def _validate_scope(
        connection: sqlite3.Connection, scope: ChatDeletionScope
    ) -> None:
        owned_spaces = {
            str(row[0])
            for row in connection.execute(
                "SELECT space_id FROM spaces WHERE owner_chat_id = ?",
                (scope.owner_chat_id,),
            )
        }
        if owned_spaces != set(scope.space_ids):
            raise ChatDeletionConflict("chat workspace scope is incomplete or changed")
        if scope.space_ids:
            placeholders = ",".join("?" for _ in scope.space_ids)
            rows = list(
                connection.execute(
                    f"SELECT space_id, owner_chat_id FROM spaces "
                    f"WHERE space_id IN ({placeholders})",
                    scope.space_ids,
                )
            )
            if len(rows) != len(scope.space_ids) or any(
                row["owner_chat_id"] != scope.owner_chat_id for row in rows
            ):
                raise ChatDeletionConflict("chat workspace owner scope changed")

        owned_bindings = {
            str(row[0])
            for row in connection.execute(
                "SELECT binding_id FROM curation_scopes WHERE owner_chat_id = ?",
                (scope.owner_chat_id,),
            )
        }
        if owned_bindings != set(scope.binding_ids):
            raise ChatDeletionConflict(
                "chat curator binding scope is incomplete or changed"
            )
        if scope.binding_ids:
            placeholders = ",".join("?" for _ in scope.binding_ids)
            rows = list(
                connection.execute(
                    f"SELECT binding_id, owner_chat_id FROM curation_scopes "
                    f"WHERE binding_id IN ({placeholders})",
                    scope.binding_ids,
                )
            )
            if len(rows) != len(scope.binding_ids) or any(
                row["owner_chat_id"] != scope.owner_chat_id for row in rows
            ):
                raise ChatDeletionConflict("chat curator owner scope changed")

        existing_executions = {
            str(row[0])
            for row in connection.execute("SELECT execution_id FROM executions")
            if str(row[0]) in set(scope.execution_ids)
        }
        if existing_executions != set(scope.execution_ids):
            raise ChatDeletionConflict("chat execution scope is incomplete or changed")
        legacy_executions = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT execution_id FROM legacy_bootstrap_manifests "
                "WHERE owner_chat_id = ?",
                (scope.owner_chat_id,),
            )
        }
        if not legacy_executions.issubset(scope.execution_ids):
            raise ChatDeletionConflict("legacy execution scope is incomplete")
        if scope.execution_ids:
            placeholders = ",".join("?" for _ in scope.execution_ids)
            foreign = connection.execute(
                f"SELECT 1 FROM legacy_bootstrap_manifests "
                f"WHERE execution_id IN ({placeholders}) AND owner_chat_id != ? LIMIT 1",
                (*scope.execution_ids, scope.owner_chat_id),
            ).fetchone()
            if foreign is not None:
                raise ChatDeletionConflict("chat execution owner scope changed")

        promoted_sources = {
            str(row[0])
            for row in connection.execute(
                "SELECT source_space_id FROM promotion_bindings "
                "WHERE source_owner_chat_id = ?",
                (scope.owner_chat_id,),
            )
        }
        if not promoted_sources.issubset(scope.space_ids):
            raise ChatDeletionConflict("chat promotion source scope is incomplete")

    @staticmethod
    def _delete_rows(
        connection: sqlite3.Connection,
        statement: str,
        values: tuple[object, ...],
    ) -> int:
        cursor = connection.execute(statement, values)
        return max(int(cursor.rowcount), 0)

    @classmethod
    def _delete_scoped_rows(
        cls,
        connection: sqlite3.Connection,
        owner_chat_id: str,
    ) -> dict[str, int]:
        execution_scope = (
            "SELECT execution_id FROM chat_deletion_execution_scopes "
            "WHERE owner_chat_id = ?"
        )
        space_scope = (
            "SELECT space_id FROM chat_deletion_space_scopes WHERE owner_chat_id = ?"
        )
        binding_scope = (
            "SELECT binding_id FROM chat_deletion_binding_scopes "
            "WHERE owner_chat_id = ?"
        )
        counts: dict[str, int] = {}

        statements = (
            (
                "legacy_bootstrap_chat_heads",
                "DELETE FROM legacy_bootstrap_chat_heads WHERE owner_chat_id = ?",
                (owner_chat_id,),
            ),
            (
                "legacy_bootstrap_manifests",
                "DELETE FROM legacy_bootstrap_manifests WHERE owner_chat_id = ?",
                (owner_chat_id,),
            ),
            (
                "memory_review_proposals",
                f"DELETE FROM memory_review_proposals WHERE binding_id IN ({binding_scope})",
                (owner_chat_id,),
            ),
            (
                "candidate_bindings",
                f"DELETE FROM candidate_bindings WHERE candidate_id IN "
                f"(SELECT candidate_id FROM candidates WHERE binding_id IN ({binding_scope})) "
                f"OR job_id IN (SELECT job_id FROM consolidation_jobs "
                f"WHERE binding_id IN ({binding_scope}))",
                (owner_chat_id, owner_chat_id),
            ),
            (
                "candidate_revisions",
                f"DELETE FROM candidate_revisions WHERE candidate_id IN "
                f"(SELECT candidate_id FROM candidates WHERE binding_id IN ({binding_scope}))",
                (owner_chat_id,),
            ),
            (
                "consolidation_job_revisions",
                f"DELETE FROM consolidation_job_revisions WHERE job_id IN "
                f"(SELECT job_id FROM consolidation_jobs WHERE binding_id IN ({binding_scope}))",
                (owner_chat_id,),
            ),
            (
                "curator_operation_receipts",
                f"DELETE FROM curator_operation_receipts WHERE binding_id IN ({binding_scope})",
                (owner_chat_id,),
            ),
            (
                "candidates",
                f"DELETE FROM candidates WHERE binding_id IN ({binding_scope})",
                (owner_chat_id,),
            ),
            (
                "consolidation_jobs",
                f"DELETE FROM consolidation_jobs WHERE binding_id IN ({binding_scope})",
                (owner_chat_id,),
            ),
            (
                "curation_run_scopes",
                f"DELETE FROM curation_run_scopes WHERE binding_id IN ({binding_scope})",
                (owner_chat_id,),
            ),
            (
                "curation_scopes",
                f"DELETE FROM curation_scopes WHERE binding_id IN ({binding_scope})",
                (owner_chat_id,),
            ),
            (
                "promotion_operation_receipts",
                f"DELETE FROM promotion_operation_receipts "
                f"WHERE source_space_id IN ({space_scope})",
                (owner_chat_id,),
            ),
            (
                "promotion_revisions",
                f"DELETE FROM promotion_revisions WHERE source_space_id IN ({space_scope})",
                (owner_chat_id,),
            ),
            (
                "promotion_proposals",
                f"DELETE FROM promotion_proposals WHERE source_space_id IN ({space_scope})",
                (owner_chat_id,),
            ),
            (
                "promotion_bindings",
                f"DELETE FROM promotion_bindings WHERE source_space_id IN ({space_scope})",
                (owner_chat_id,),
            ),
            (
                "links",
                f"DELETE FROM links WHERE space_id IN ({space_scope})",
                (owner_chat_id,),
            ),
            (
                "entry_revisions",
                f"DELETE FROM entry_revisions WHERE space_id IN ({space_scope})",
                (owner_chat_id,),
            ),
            (
                "entries",
                f"DELETE FROM entries WHERE space_id IN ({space_scope})",
                (owner_chat_id,),
            ),
            (
                "workspace_entries_fts",
                f"DELETE FROM workspace_entries_fts WHERE space_id IN ({space_scope})",
                (owner_chat_id,),
            ),
            (
                "memory_operation_receipts",
                f"DELETE FROM memory_operation_receipts WHERE "
                f"scope_id IN ({space_scope}) OR scope_id IN ({binding_scope}) "
                f"OR scope_id IN ({execution_scope})",
                (owner_chat_id, owner_chat_id, owner_chat_id),
            ),
            (
                "task_state_heads",
                f"DELETE FROM task_state_heads WHERE binding_id IN ({binding_scope})",
                (owner_chat_id,),
            ),
            (
                "task_state_revisions",
                f"DELETE FROM task_state_revisions WHERE binding_id IN ({binding_scope})",
                (owner_chat_id,),
            ),
            (
                "index_state",
                f"DELETE FROM index_state WHERE scope_id IN ({space_scope}) "
                f"OR scope_id IN ({binding_scope})",
                (owner_chat_id, owner_chat_id),
            ),
            (
                "spaces",
                f"DELETE FROM spaces WHERE space_id IN ({space_scope})",
                (owner_chat_id,),
            ),
            (
                "provider_turn_result_receipts",
                f"DELETE FROM provider_turn_result_receipts "
                f"WHERE execution_id IN ({execution_scope})",
                (owner_chat_id,),
            ),
            (
                "context_builds",
                f"DELETE FROM context_builds WHERE execution_id IN ({execution_scope})",
                (owner_chat_id,),
            ),
            (
                "checkpoints",
                f"DELETE FROM checkpoints WHERE execution_id IN ({execution_scope})",
                (owner_chat_id,),
            ),
            (
                "provider_request_lease_heads",
                f"DELETE FROM provider_request_lease_heads "
                f"WHERE execution_id IN ({execution_scope})",
                (owner_chat_id,),
            ),
            (
                "provider_request_lease_revisions",
                f"DELETE FROM provider_request_lease_revisions "
                f"WHERE execution_id IN ({execution_scope})",
                (owner_chat_id,),
            ),
            (
                "artifacts",
                f"DELETE FROM artifacts WHERE execution_id IN ({execution_scope})",
                (owner_chat_id,),
            ),
            (
                "event_receipts",
                f"DELETE FROM event_receipts WHERE execution_id IN ({execution_scope})",
                (owner_chat_id,),
            ),
            (
                "events",
                f"DELETE FROM events WHERE execution_id IN ({execution_scope})",
                (owner_chat_id,),
            ),
            (
                "operations",
                f"DELETE FROM operations WHERE execution_id IN ({execution_scope})",
                (owner_chat_id,),
            ),
            (
                "executions",
                f"DELETE FROM executions WHERE execution_id IN ({execution_scope})",
                (owner_chat_id,),
            ),
        )
        for table_name, statement, values in statements:
            deleted = cls._delete_rows(connection, statement, values)
            counts[table_name] = counts.get(table_name, 0) + deleted
        return counts

    def delete_chat(
        self,
        *,
        scope: ChatDeletionScope,
        operation_id: str,
    ) -> ChatDeletionReceipt:
        if not isinstance(scope, ChatDeletionScope):
            raise TypeError("scope must be a ChatDeletionScope")
        operation = _identifier(operation_id, "operation_id")
        scope_json = _canonical_json_bytes(scope.to_dict())
        scope_sha256 = _sha256(scope_json)
        connection = _connect(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            tombstone = connection.execute(
                "SELECT * FROM chat_deletion_tombstones WHERE owner_chat_id = ?",
                (scope.owner_chat_id,),
            ).fetchone()
            if tombstone is not None:
                if (
                    bytes(tombstone["scope_json"]) != scope_json
                    or tombstone["scope_sha256"] != scope_sha256
                    or _sha256(bytes(tombstone["scope_json"])) != scope_sha256
                ):
                    raise ChatDeletionConflict("chat deletion scope payload changed")
                self._verify_tombstone_evidence(
                    connection,
                    row=tombstone,
                    scope=scope,
                )
                existing_operation = connection.execute(
                    "SELECT payload_sha256, result_sha256 "
                    "FROM chat_deletion_operations "
                    "WHERE owner_chat_id = ? AND operation_id = ?",
                    (scope.owner_chat_id, operation),
                ).fetchone()
                if existing_operation is not None and (
                    existing_operation["payload_sha256"] != scope_sha256
                    or existing_operation["result_sha256"] != tombstone["result_sha256"]
                ):
                    raise ChatDeletionConflict(
                        "chat deletion operation payload changed"
                    )
                if existing_operation is None:
                    connection.execute(
                        """
                        INSERT INTO chat_deletion_operations(
                            owner_chat_id, operation_id, payload_sha256, result_sha256
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            scope.owner_chat_id,
                            operation,
                            scope_sha256,
                            tombstone["result_sha256"],
                        ),
                    )
                receipt = self._decode_receipt(
                    tombstone,
                    owner_chat_id=scope.owner_chat_id,
                    replayed=True,
                )
                connection.commit()
                return receipt

            self._validate_scope(connection, scope)
            connection.execute(
                """
                INSERT INTO chat_deletion_tombstones(
                    owner_chat_id, revision, first_operation_id,
                    scope_json, scope_sha256, result_json, result_sha256
                ) VALUES (?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    scope.owner_chat_id,
                    operation,
                    scope_json,
                    scope_sha256,
                    b"{}",
                    _sha256(b"{}"),
                ),
            )
            connection.executemany(
                "INSERT INTO chat_deletion_execution_scopes "
                "(owner_chat_id, execution_id) VALUES (?, ?)",
                ((scope.owner_chat_id, value) for value in scope.execution_ids),
            )
            connection.executemany(
                "INSERT INTO chat_deletion_space_scopes "
                "(owner_chat_id, space_id) VALUES (?, ?)",
                ((scope.owner_chat_id, value) for value in scope.space_ids),
            )
            connection.executemany(
                "INSERT INTO chat_deletion_binding_scopes "
                "(owner_chat_id, binding_id) VALUES (?, ?)",
                ((scope.owner_chat_id, value) for value in scope.binding_ids),
            )
            deleted_rows = self._delete_scoped_rows(connection, scope.owner_chat_id)
            receipt = ChatDeletionReceipt(
                owner_chat_id=scope.owner_chat_id,
                tombstone_revision=1,
                deleted_rows=deleted_rows,
                pending_unreferenced_scan=True,
            )
            result_json = _canonical_json_bytes(receipt.to_dict())
            result_sha256 = _sha256(result_json)
            connection.execute(
                """
                UPDATE chat_deletion_tombstones
                SET result_json = ?, result_sha256 = ?
                WHERE owner_chat_id = ?
                """,
                (result_json, result_sha256, scope.owner_chat_id),
            )
            connection.execute(
                """
                INSERT INTO chat_deletion_operations(
                    owner_chat_id, operation_id, payload_sha256, result_sha256
                ) VALUES (?, ?, ?, ?)
                """,
                (scope.owner_chat_id, operation, scope_sha256, result_sha256),
            )
            connection.commit()
            return receipt
        except ChatDeletionError:
            connection.rollback()
            raise
        except sqlite3.Error as error:
            connection.rollback()
            raise ChatDeletionUnavailable(
                "chat deletion SQLite transaction failed"
            ) from error
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


def read_chat_deletion_tombstone(
    *,
    database_path: str | Path,
    owner_chat_id: str,
) -> ChatDeletionTombstone | None:
    """Read and verify a tombstone without creating or mutating a database."""

    owner = _identifier(owner_chat_id, "owner_chat_id")
    path = Path(database_path)
    if not path.is_file():
        return None
    connection = _connect(path)
    try:
        connection.execute("PRAGMA query_only = ON")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'chat_deletion_tombstones'"
        ).fetchone()
        if table is None:
            return None
        row = connection.execute(
            "SELECT * FROM chat_deletion_tombstones WHERE owner_chat_id = ?",
            (owner,),
        ).fetchone()
        if row is None:
            return None
        if int(row["revision"]) != 1:
            raise ChatDeletionUnavailable("chat deletion tombstone revision changed")
        scope_json = bytes(row["scope_json"])
        if _sha256(scope_json) != row["scope_sha256"]:
            raise ChatDeletionUnavailable("chat deletion scope digest changed")
        try:
            raw_scope = json.loads(scope_json.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ChatDeletionUnavailable("chat deletion scope is malformed") from error
        expected_scope_fields = {
            "schema",
            "owner_chat_id",
            "execution_ids",
            "space_ids",
            "binding_ids",
        }
        if (
            not isinstance(raw_scope, Mapping)
            or set(raw_scope) != expected_scope_fields
            or raw_scope.get("schema") != _SCOPE_SCHEMA
        ):
            raise ChatDeletionUnavailable("chat deletion scope shape changed")
        try:
            scope = ChatDeletionScope(
                owner_chat_id=raw_scope["owner_chat_id"],
                execution_ids=tuple(raw_scope["execution_ids"]),
                space_ids=tuple(raw_scope["space_ids"]),
                binding_ids=tuple(raw_scope["binding_ids"]),
            )
        except (TypeError, ValueError) as error:
            raise ChatDeletionUnavailable("chat deletion scope is invalid") from error
        if (
            scope.owner_chat_id != owner
            or _canonical_json_bytes(scope.to_dict()) != scope_json
        ):
            raise ChatDeletionUnavailable("chat deletion scope is not canonical")
        receipt = SQLiteChatDeletionV2Service._decode_receipt(
            row,
            owner_chat_id=owner,
            replayed=True,
        )
        SQLiteChatDeletionV2Service._verify_tombstone_evidence(
            connection,
            row=row,
            scope=scope,
        )
        return ChatDeletionTombstone(
            scope=scope,
            receipt=receipt,
            first_operation_id=row["first_operation_id"],
        )
    except ChatDeletionError:
        raise
    except sqlite3.Error as error:
        raise ChatDeletionUnavailable("chat deletion tombstone read failed") from error
    finally:
        connection.close()


def is_chat_deleted(
    *,
    database_path: str | Path,
    owner_chat_id: str,
) -> bool:
    return (
        read_chat_deletion_tombstone(
            database_path=database_path,
            owner_chat_id=owner_chat_id,
        )
        is not None
    )


def is_chat_deletion_tombstoned(
    *,
    database_path: str | Path,
    owner_chat_id: str,
) -> bool:
    """Compatibility spelling for product hosts preparing an attachment."""

    return is_chat_deleted(
        database_path=database_path,
        owner_chat_id=owner_chat_id,
    )


__all__ = [
    "ChatDeletedError",
    "ChatDeletionConflict",
    "ChatDeletionError",
    "ChatDeletionReceipt",
    "ChatDeletionScope",
    "ChatDeletionTombstone",
    "ChatDeletionUnavailable",
    "SQLiteChatDeletionV2Service",
    "is_chat_deleted",
    "is_chat_deletion_tombstoned",
    "read_chat_deletion_tombstone",
]
