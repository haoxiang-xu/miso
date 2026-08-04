"""SQLite/WAL and content-addressed persistence for Context V2.

The database and object directory are one durable data plane.  Objects become
durable before SQLite is allowed to reference them; journal events and their
receipt-index rows are committed in the same transaction.  A provider request
``started`` lease is likewise a committed CAS record before a caller may send
the corresponding network request.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from unchain.context.ports import (
    BoundArtifactRepository,
    ContextConflictError,
    ContextRepositoryError,
    ContextScopeError,
)
from unchain.journal.models import (
    MAX_TOOL_EXECUTION_RECEIPTS,
    TOOL_EXECUTION_RECEIPT_TYPES,
    ArtifactRef,
    AttemptRef,
    EventCursor,
    JournalAppendRequest,
    JournalAppendResult,
    JournalEvent,
    JournalPage,
    OperationRef,
    ResourceRef,
    ToolExecutionReceiptLookup,
    _required_text,
)
from unchain.journal.ports import (
    BoundExecutionJournal,
    JournalConflictError,
    JournalRepositoryError,
    JournalScopeError,
)
from unchain.journal.provider_result import (
    MAX_PROVIDER_TURN_RESULT_RECEIPTS,
    PROVIDER_TURN_RESULT_EVENT_TYPE,
    BoundProviderTurnResultStore,
    ProviderTurnResultIntegrityError,
    ProviderTurnResultPersistRequest,
    ProviderTurnResultReceipt,
    ProviderTurnResultReceiptLookup,
    build_provider_turn_result_event_payload,
    provider_turn_result_event_fields,
)
from unchain.journal.provider_wire import (
    MAX_PROVIDER_WIRE_RECEIPTS,
    PROVIDER_WIRE_SNAPSHOT_EVENT_TYPE,
    BoundProviderWireStore,
    ProviderWireReceiptLookup,
)
from unchain.journal.snapshot import JournalSnapshot, capture_journal_snapshot
from unchain.journal.tool_catalog import (
    MAX_TOOL_CATALOG_RECEIPTS,
    TOOL_CATALOG_SNAPSHOT_EVENT_TYPE,
    BoundToolCatalogIndex,
    ToolCatalogReceiptLookup,
)
from unchain.providers.request_lease import (
    ProviderRequestLease,
    ProviderRequestLeaseConflict,
    ProviderRequestLeaseIntegrityError,
    ProviderRequestLeasePort,
    ProviderRequestStatus,
    ProviderRequestSubject,
)
from unchain.providers.wire_envelope import ProviderWireEnvelope


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SQLiteContextV2StoreError(RuntimeError):
    """Base failure for the SQLite Context V2 data plane."""


class SQLiteContextV2StoreIntegrityError(
    SQLiteContextV2StoreError,
    JournalRepositoryError,
    ContextRepositoryError,
    ProviderRequestLeaseIntegrityError,
):
    """Durable metadata or object bytes failed exact verification."""


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
        raise SQLiteContextV2StoreIntegrityError(
            "durable record is not canonical JSON"
        ) from exc


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _exact_non_negative_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _exact_positive_int(value: object, field_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _subject_key(subject: ProviderRequestSubject) -> tuple[Any, ...]:
    return (
        subject.attempt.generation.execution_id,
        subject.attempt.generation.generation_id,
        subject.attempt.attempt_id,
        subject.iteration,
        subject.envelope_sha256,
        subject.route,
        subject.retry_ordinal,
    )


def _subject_target(subject: ProviderRequestSubject, revision: int) -> str:
    body = {
        "subject": subject.to_dict(),
        "revision": revision,
    }
    return _sha256(_canonical_json_bytes(body))


def _subject_sha256(subject: ProviderRequestSubject) -> str:
    return _sha256(_canonical_json_bytes(subject.to_dict()))


class SQLiteContextV2Store:
    """Own one SQLite database and one immutable SHA-256 object directory."""

    def __init__(
        self,
        *,
        database_path: str | os.PathLike[str],
        object_directory: str | os.PathLike[str],
    ) -> None:
        self.database_path = Path(database_path)
        self.object_directory = Path(object_directory)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.object_directory.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _existing_schema_versions(
        connection: sqlite3.Connection,
    ) -> set[int] | None:
        table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'context_v2_schema'
            """
        ).fetchone()
        if table is None:
            return None
        values = [
            row[0]
            for row in connection.execute("SELECT version FROM context_v2_schema")
        ]
        if not values or any(type(value) is not int for value in values):
            raise SQLiteContextV2StoreIntegrityError(
                "SQLite Context V2 schema version is malformed"
            )
        return set(values)

    @staticmethod
    def _upgrade_provider_request_leases(
        connection: sqlite3.Connection,
    ) -> None:
        rows = list(
            connection.execute("SELECT rowid, * FROM provider_request_lease_revisions")
        )
        for row in rows:
            try:
                raw = bytes(row["lease_json"])
                if _sha256(raw) != row["lease_sha256"]:
                    raise SQLiteContextV2StoreIntegrityError(
                        "provider request lease digest changed during migration"
                    )
                decoded = json.loads(raw.decode("utf-8"))
                if type(decoded) is not dict or _canonical_json_bytes(decoded) != raw:
                    raise SQLiteContextV2StoreIntegrityError(
                        "provider request lease migration input is not canonical"
                    )
                lease = ProviderRequestLease.from_durable_dict(decoded)
            except SQLiteContextV2StoreIntegrityError:
                raise
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                TypeError,
                ValueError,
                ProviderRequestLeaseIntegrityError,
            ) as exc:
                raise SQLiteContextV2StoreIntegrityError(
                    "provider request lease legacy migration failed"
                ) from exc

            if (
                _subject_key(lease.subject)
                != tuple(
                    row[name]
                    for name in (
                        "execution_id",
                        "generation_id",
                        "attempt_id",
                        "iteration",
                        "envelope_sha256",
                        "route",
                        "retry_ordinal",
                    )
                )
                or lease.revision != row["revision"]
                or lease.operation.operation_id != row["operation_id"]
            ):
                raise SQLiteContextV2StoreIntegrityError(
                    "provider request lease migration index changed"
                )

            upgraded = _canonical_json_bytes(lease.to_dict())
            if upgraded == raw:
                continue
            updated = connection.execute(
                """
                UPDATE provider_request_lease_revisions
                SET lease_json = ?, lease_sha256 = ?
                WHERE rowid = ? AND lease_sha256 = ?
                """,
                (upgraded, _sha256(upgraded), row["rowid"], row["lease_sha256"]),
            )
            if updated.rowcount != 1:
                raise SQLiteContextV2StoreIntegrityError(
                    "provider request lease migration CAS failed"
                )

    @staticmethod
    def _validate_provider_request_lease_heads(
        connection: sqlite3.Connection,
    ) -> None:
        invalid_head = connection.execute(
            """
            SELECT 1
            FROM provider_request_lease_heads AS h
            LEFT JOIN provider_request_lease_revisions AS r
              ON r.execution_id = h.execution_id
             AND r.generation_id = h.generation_id
             AND r.attempt_id = h.attempt_id
             AND r.iteration = h.iteration
             AND r.envelope_sha256 = h.envelope_sha256
             AND r.route = h.route
             AND r.retry_ordinal = h.retry_ordinal
             AND r.revision = h.current_revision
            WHERE r.revision IS NULL
               OR h.current_revision != (
                    SELECT MAX(latest.revision)
                    FROM provider_request_lease_revisions AS latest
                    WHERE latest.execution_id = h.execution_id
                      AND latest.generation_id = h.generation_id
                      AND latest.attempt_id = h.attempt_id
                      AND latest.iteration = h.iteration
                      AND latest.envelope_sha256 = h.envelope_sha256
                      AND latest.route = h.route
                      AND latest.retry_ordinal = h.retry_ordinal
               )
            LIMIT 1
            """
        ).fetchone()
        orphan_revision = connection.execute(
            """
            SELECT 1
            FROM provider_request_lease_revisions AS r
            LEFT JOIN provider_request_lease_heads AS h
              ON h.execution_id = r.execution_id
             AND h.generation_id = r.generation_id
             AND h.attempt_id = r.attempt_id
             AND h.iteration = r.iteration
             AND h.envelope_sha256 = r.envelope_sha256
             AND h.route = r.route
             AND h.retry_ordinal = r.retry_ordinal
            WHERE h.execution_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if invalid_head is not None or orphan_revision is not None:
            raise SQLiteContextV2StoreIntegrityError(
                "provider request lease head or revision is inconsistent"
            )

    @staticmethod
    def _validate_provider_request_evidence(
        connection: sqlite3.Connection,
    ) -> None:
        orphan_lease_operation = connection.execute(
            """
            SELECT 1
            FROM operations AS o
            LEFT JOIN provider_request_lease_revisions AS r
              ON r.execution_id = o.execution_id
             AND r.operation_id = o.operation_id
            WHERE o.target_kind = 'provider_request_lease'
              AND r.operation_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        orphan_result_receipt = connection.execute(
            """
            SELECT 1
            FROM provider_turn_result_receipts AS p
            LEFT JOIN provider_request_lease_heads AS h
              ON h.execution_id = p.execution_id
             AND h.generation_id = p.generation_id
             AND h.attempt_id = p.attempt_id
             AND h.iteration = p.iteration
             AND h.envelope_sha256 = p.envelope_sha256
             AND h.route = p.route
             AND h.retry_ordinal = p.retry_ordinal
            WHERE h.execution_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        orphan_result_event = connection.execute(
            """
            SELECT 1
            FROM events AS e
            LEFT JOIN provider_turn_result_receipts AS p
              ON p.execution_id = e.execution_id
             AND p.store_seq = e.store_seq
            WHERE e.event_type = ?
              AND p.store_seq IS NULL
            LIMIT 1
            """,
            (PROVIDER_TURN_RESULT_EVENT_TYPE,),
        ).fetchone()
        if (
            orphan_lease_operation is not None
            or orphan_result_receipt is not None
            or orphan_result_event is not None
        ):
            raise SQLiteContextV2StoreIntegrityError(
                "provider request lease has orphan durable send evidence"
            )

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            existing_versions = self._existing_schema_versions(connection)
            if existing_versions not in (None, {1}, {1, 2}):
                raise SQLiteContextV2StoreIntegrityError(
                    "SQLite Context V2 schema version is unsupported"
                )
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).casefold() != "wal":
                raise SQLiteContextV2StoreIntegrityError(
                    "SQLite refused WAL journal mode"
                )
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS context_v2_schema (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT OR IGNORE INTO context_v2_schema(version) VALUES (1);
                INSERT OR IGNORE INTO context_v2_schema(version) VALUES (2);

                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    next_store_seq INTEGER NOT NULL DEFAULT 1
                        CHECK(next_store_seq >= 1)
                );

                CREATE TABLE IF NOT EXISTS operations (
                    execution_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    target_kind TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    PRIMARY KEY (execution_id, operation_id),
                    FOREIGN KEY (execution_id)
                        REFERENCES executions(execution_id)
                );

                CREATE TABLE IF NOT EXISTS events (
                    execution_id TEXT NOT NULL,
                    store_seq INTEGER NOT NULL CHECK(store_seq >= 1),
                    event_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    event_json BLOB NOT NULL,
                    event_sha256 TEXT NOT NULL,
                    PRIMARY KEY (execution_id, store_seq),
                    UNIQUE (execution_id, event_id),
                    UNIQUE (execution_id, operation_id),
                    FOREIGN KEY (execution_id, operation_id)
                        REFERENCES operations(execution_id, operation_id)
                );

                CREATE TABLE IF NOT EXISTS event_receipts (
                    execution_id TEXT NOT NULL,
                    store_seq INTEGER NOT NULL,
                    receipt_kind TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    call_id TEXT,
                    iteration INTEGER,
                    PRIMARY KEY (execution_id, store_seq, receipt_kind),
                    FOREIGN KEY (execution_id, store_seq)
                        REFERENCES events(execution_id, store_seq)
                        ON DELETE CASCADE,
                    CHECK (
                        (receipt_kind = 'tool_execution'
                            AND call_id IS NOT NULL AND iteration IS NULL)
                        OR
                        (receipt_kind IN ('tool_catalog', 'provider_wire')
                            AND call_id IS NULL AND iteration IS NOT NULL)
                    )
                );
                CREATE INDEX IF NOT EXISTS idx_tool_execution_receipts
                    ON event_receipts(
                        execution_id,
                        generation_id,
                        attempt_id,
                        receipt_kind,
                        call_id,
                        store_seq
                    );
                CREATE INDEX IF NOT EXISTS idx_iteration_receipts
                    ON event_receipts(
                        execution_id,
                        generation_id,
                        attempt_id,
                        receipt_kind,
                        iteration,
                        store_seq
                    );

                CREATE TABLE IF NOT EXISTS objects (
                    sha256 TEXT PRIMARY KEY,
                    byte_length INTEGER NOT NULL CHECK(byte_length >= 0)
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    execution_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    logical_kind TEXT NOT NULL,
                    logical_key TEXT NOT NULL,
                    object_sha256 TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
                    preview TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    artifact_json BLOB NOT NULL,
                    artifact_record_sha256 TEXT NOT NULL,
                    PRIMARY KEY (execution_id, artifact_id, revision),
                    UNIQUE (execution_id, logical_kind, logical_key, revision),
                    UNIQUE (execution_id, operation_id),
                    FOREIGN KEY (execution_id)
                        REFERENCES executions(execution_id),
                    FOREIGN KEY (object_sha256)
                        REFERENCES objects(sha256),
                    FOREIGN KEY (execution_id, operation_id)
                        REFERENCES operations(execution_id, operation_id)
                );

                CREATE TABLE IF NOT EXISTS provider_request_lease_revisions (
                    execution_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    iteration INTEGER NOT NULL,
                    envelope_sha256 TEXT NOT NULL,
                    route TEXT NOT NULL,
                    retry_ordinal INTEGER NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    operation_id TEXT NOT NULL,
                    lease_json BLOB NOT NULL,
                    lease_sha256 TEXT NOT NULL,
                    PRIMARY KEY (
                        execution_id,
                        generation_id,
                        attempt_id,
                        iteration,
                        envelope_sha256,
                        route,
                        retry_ordinal,
                        revision
                    ),
                    UNIQUE (execution_id, operation_id),
                    FOREIGN KEY (execution_id, operation_id)
                        REFERENCES operations(execution_id, operation_id)
                );

                CREATE TABLE IF NOT EXISTS provider_request_lease_heads (
                    execution_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    iteration INTEGER NOT NULL,
                    envelope_sha256 TEXT NOT NULL,
                    route TEXT NOT NULL,
                    retry_ordinal INTEGER NOT NULL,
                    current_revision INTEGER NOT NULL CHECK(current_revision >= 1),
                    PRIMARY KEY (
                        execution_id,
                        generation_id,
                        attempt_id,
                        iteration,
                        envelope_sha256,
                        route,
                        retry_ordinal
                    ),
                    FOREIGN KEY (execution_id)
                        REFERENCES executions(execution_id)
                );

                CREATE TABLE IF NOT EXISTS provider_turn_result_receipts (
                    execution_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    iteration INTEGER NOT NULL,
                    envelope_sha256 TEXT NOT NULL,
                    route TEXT NOT NULL,
                    retry_ordinal INTEGER NOT NULL,
                    store_seq INTEGER NOT NULL,
                    PRIMARY KEY (
                        execution_id,
                        generation_id,
                        attempt_id,
                        iteration,
                        envelope_sha256,
                        route,
                        retry_ordinal
                    ),
                    UNIQUE (execution_id, store_seq),
                    FOREIGN KEY (execution_id, store_seq)
                        REFERENCES events(execution_id, store_seq)
                        ON DELETE CASCADE
                );
                """
            )
            self._upgrade_provider_request_leases(connection)
            self._validate_provider_request_lease_heads(connection)
            self._validate_provider_request_evidence(connection)
            versions = {
                int(row[0])
                for row in connection.execute("SELECT version FROM context_v2_schema")
            }
            if versions != {1, 2}:
                raise SQLiteContextV2StoreIntegrityError(
                    "SQLite Context V2 schema version is unsupported"
                )
            check = connection.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                raise SQLiteContextV2StoreIntegrityError(
                    f"SQLite quick_check failed: {check}"
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _transaction(self, *, immediate: bool) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _ensure_execution(
        connection: sqlite3.Connection,
        execution_id: str,
    ) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO executions(execution_id) VALUES (?)",
            (execution_id,),
        )

    def bind_execution(self, execution_id: str) -> _SQLiteBoundContextV2Repository:
        normalized = _required_text(
            execution_id,
            "execution_id",
            identifier=True,
        )
        with self._transaction(immediate=True) as connection:
            self._ensure_execution(connection, normalized)
        return _SQLiteBoundContextV2Repository(self, normalized)

    def _object_path(self, digest: str) -> Path:
        if _SHA256_RE.fullmatch(digest) is None:
            raise SQLiteContextV2StoreIntegrityError("object digest is not canonical")
        return self.object_directory / digest

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _read_object(self, *, digest: str, byte_length: int) -> bytes:
        path = self._object_path(digest)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise SQLiteContextV2StoreIntegrityError(
                "artifact object is missing or unreadable"
            ) from exc
        if len(content) != byte_length or _sha256(content) != digest:
            raise SQLiteContextV2StoreIntegrityError(
                "artifact object length or digest changed"
            )
        return content

    def _install_object(self, content: bytes) -> tuple[str, int]:
        if type(content) is not bytes:
            raise TypeError("object content must be exact bytes")
        digest = _sha256(content)
        target = self._object_path(digest)
        if target.exists():
            self._read_object(digest=digest, byte_length=len(content))
            return digest, len(content)

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".context-v2-object-",
            dir=self.object_directory,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
            self._fsync_directory(self.object_directory)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        self._read_object(digest=digest, byte_length=len(content))
        return digest, len(content)


class _SQLiteBoundContextV2Repository(
    BoundToolCatalogIndex,
    BoundProviderWireStore,
    BoundArtifactRepository,
    BoundProviderTurnResultStore,
    ProviderRequestLeasePort,
):
    """Execution-bound implementation of all P0 durable repository ports."""

    def __init__(self, store: SQLiteContextV2Store, execution_id: str) -> None:
        BoundExecutionJournal.__init__(self, execution_id)
        self._store = store

    @staticmethod
    def _request_matches_event(
        request: JournalAppendRequest,
        event: JournalEvent,
    ) -> bool:
        return (
            request.event_id == event.event_id
            and request.event_type == event.event_type
            and request.attempt == event.attempt
            and request.operation == event.operation
            and dict(request.payload) == dict(event.payload)
            and request.resource_refs == event.resource_refs
        )

    def _scope_attempt(
        self, attempt: AttemptRef, *, error_type: type[Exception]
    ) -> None:
        if not isinstance(attempt, AttemptRef):
            raise TypeError("attempt must be an AttemptRef")
        if attempt.generation.execution_id != self.execution_id:
            raise error_type("attempt belongs to a foreign execution scope")

    def _operation_row(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT payload_sha256, target_kind, target_key
            FROM operations
            WHERE execution_id = ? AND operation_id = ?
            """,
            (self.execution_id, operation_id),
        ).fetchone()

    def _claim_operation(
        self,
        connection: sqlite3.Connection,
        *,
        operation: OperationRef,
        target_kind: str,
        target_key: str,
        conflict_type: type[Exception],
    ) -> bool:
        previous = self._operation_row(connection, operation.operation_id)
        if previous is not None:
            if (
                previous["payload_sha256"] == operation.payload_sha256
                and previous["target_kind"] == target_kind
                and previous["target_key"] == target_key
            ):
                return False
            raise conflict_type("operation payload or durable target changed")
        connection.execute(
            """
            INSERT INTO operations(
                execution_id,
                operation_id,
                payload_sha256,
                target_kind,
                target_key
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                self.execution_id,
                operation.operation_id,
                operation.payload_sha256,
                target_kind,
                target_key,
            ),
        )
        return True

    def _event_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> JournalEvent:
        raw = bytes(row["event_json"])
        if _sha256(raw) != row["event_sha256"]:
            raise SQLiteContextV2StoreIntegrityError(
                "journal event digest changed on disk"
            )
        try:
            decoded = json.loads(raw.decode("utf-8"))
            event = JournalEvent.from_dict(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise SQLiteContextV2StoreIntegrityError(
                "journal event record is malformed"
            ) from exc
        if _canonical_json_bytes(event.to_dict()) != raw:
            raise SQLiteContextV2StoreIntegrityError("journal event is not canonical")
        if (
            event.attempt.generation.execution_id != self.execution_id
            or event.store_seq != row["store_seq"]
            or event.event_id != row["event_id"]
            or event.attempt.generation.generation_id != row["generation_id"]
            or event.attempt.attempt_id != row["attempt_id"]
            or event.event_type != row["event_type"]
            or event.operation.operation_id != row["operation_id"]
        ):
            raise SQLiteContextV2StoreIntegrityError(
                "journal event indexed fields changed"
            )
        operation = self._operation_row(
            connection,
            event.operation.operation_id,
        )
        if (
            operation is None
            or operation["payload_sha256"] != event.operation.payload_sha256
            or operation["target_kind"] != "journal_event"
            or operation["target_key"] != event.event_id
        ):
            raise SQLiteContextV2StoreIntegrityError(
                "journal operation payload or target changed"
            )
        return event

    @staticmethod
    def _receipt_subject(
        event: JournalEvent,
    ) -> tuple[str, str | None, int | None] | None:
        if event.event_type in TOOL_EXECUTION_RECEIPT_TYPES:
            call_id = _required_text(
                event.payload.get("call_id"),
                "call_id",
                identifier=True,
            )
            return "tool_execution", call_id, None
        if event.event_type == TOOL_CATALOG_SNAPSHOT_EVENT_TYPE:
            iteration = event.payload.get("iteration")
            if type(iteration) is not int or iteration < 0:
                raise JournalRepositoryError(
                    "tool catalog receipt iteration is invalid"
                )
            return "tool_catalog", None, iteration
        if event.event_type == PROVIDER_WIRE_SNAPSHOT_EVENT_TYPE:
            iteration = event.payload.get("iteration")
            if type(iteration) is not int or iteration < 0:
                raise JournalRepositoryError(
                    "provider wire receipt iteration is invalid"
                )
            return "provider_wire", None, iteration
        return None

    def append(self, *, request: JournalAppendRequest) -> JournalAppendResult:
        if not isinstance(request, JournalAppendRequest):
            raise TypeError("request must be a JournalAppendRequest")
        self._scope_attempt(request.attempt, error_type=JournalScopeError)
        try:
            with self._store._transaction(immediate=True) as connection:
                self._store._ensure_execution(connection, self.execution_id)
                previous = self._operation_row(
                    connection,
                    request.operation.operation_id,
                )
                if previous is not None:
                    if (
                        previous["payload_sha256"] != request.operation.payload_sha256
                        or previous["target_kind"] != "journal_event"
                        or previous["target_key"] != request.event_id
                    ):
                        raise JournalConflictError(
                            "operation payload or event target changed"
                        )
                    row = connection.execute(
                        """
                        SELECT * FROM events
                        WHERE execution_id = ? AND operation_id = ?
                        """,
                        (self.execution_id, request.operation.operation_id),
                    ).fetchone()
                    if row is None:
                        raise SQLiteContextV2StoreIntegrityError(
                            "journal operation has no event"
                        )
                    event = self._event_from_row(connection, row)
                    if not self._request_matches_event(request, event):
                        raise JournalConflictError(
                            "operation replay changed the event payload"
                        )
                    cursor = EventCursor(event.store_seq, event.event_id)
                    return JournalAppendResult(event, cursor, duplicate=True)

                if (
                    connection.execute(
                        """
                    SELECT 1 FROM events
                    WHERE execution_id = ? AND event_id = ?
                    """,
                        (self.execution_id, request.event_id),
                    ).fetchone()
                    is not None
                ):
                    raise JournalConflictError("event id belongs to another operation")
                head = connection.execute(
                    """
                    SELECT next_store_seq FROM executions
                    WHERE execution_id = ?
                    """,
                    (self.execution_id,),
                ).fetchone()
                if head is None:
                    raise SQLiteContextV2StoreIntegrityError(
                        "execution journal head is missing"
                    )
                store_seq = int(head["next_store_seq"])
                event = JournalEvent(
                    event_id=request.event_id,
                    event_type=request.event_type,
                    attempt=request.attempt,
                    operation=request.operation,
                    store_seq=store_seq,
                    payload=request.payload,
                    resource_refs=request.resource_refs,
                )
                receipt = self._receipt_subject(event)
                self._claim_operation(
                    connection,
                    operation=request.operation,
                    target_kind="journal_event",
                    target_key=request.event_id,
                    conflict_type=JournalConflictError,
                )
                event_json = _canonical_json_bytes(event.to_dict())
                connection.execute(
                    """
                    INSERT INTO events(
                        execution_id,
                        store_seq,
                        event_id,
                        generation_id,
                        attempt_id,
                        event_type,
                        operation_id,
                        event_json,
                        event_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.execution_id,
                        store_seq,
                        event.event_id,
                        event.attempt.generation.generation_id,
                        event.attempt.attempt_id,
                        event.event_type,
                        event.operation.operation_id,
                        event_json,
                        _sha256(event_json),
                    ),
                )
                if receipt is not None:
                    kind, call_id, iteration = receipt
                    connection.execute(
                        """
                        INSERT INTO event_receipts(
                            execution_id,
                            store_seq,
                            receipt_kind,
                            generation_id,
                            attempt_id,
                            call_id,
                            iteration
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            self.execution_id,
                            store_seq,
                            kind,
                            event.attempt.generation.generation_id,
                            event.attempt.attempt_id,
                            call_id,
                            iteration,
                        ),
                    )
                updated = connection.execute(
                    """
                    UPDATE executions SET next_store_seq = ?
                    WHERE execution_id = ? AND next_store_seq = ?
                    """,
                    (store_seq + 1, self.execution_id, store_seq),
                )
                if updated.rowcount != 1:
                    raise JournalConflictError("journal sequence allocation conflicted")
                cursor = EventCursor(store_seq, event.event_id)
                return JournalAppendResult(event, cursor, duplicate=False)
        except sqlite3.IntegrityError as exc:
            raise JournalConflictError("journal operation or event conflicted") from exc
        except sqlite3.Error as exc:
            raise JournalRepositoryError("SQLite journal append failed") from exc

    def _event_rows_after(
        self,
        connection: sqlite3.Connection,
        *,
        store_seq: int,
        limit: int,
    ) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                """
                SELECT * FROM events
                WHERE execution_id = ? AND store_seq > ?
                ORDER BY store_seq
                LIMIT ?
                """,
                (self.execution_id, store_seq, limit),
            )
        )

    def read(
        self,
        *,
        after: EventCursor | None = None,
        limit: int = 100,
    ) -> JournalPage:
        limit = _exact_positive_int(limit, "limit")
        if after is not None and not isinstance(after, EventCursor):
            raise TypeError("after must be an EventCursor or None")
        try:
            with self._store._transaction(immediate=False) as connection:
                start = 0
                if after is not None:
                    row = connection.execute(
                        """
                        SELECT event_id FROM events
                        WHERE execution_id = ? AND store_seq = ?
                        """,
                        (self.execution_id, after.store_seq),
                    ).fetchone()
                    if row is None or row["event_id"] != after.event_id:
                        raise JournalScopeError(
                            "cursor does not belong to this execution scope"
                        )
                    start = after.store_seq
                rows = self._event_rows_after(
                    connection,
                    store_seq=start,
                    limit=limit + 1,
                )
                has_more = len(rows) > limit
                events = tuple(
                    self._event_from_row(connection, row) for row in rows[:limit]
                )
                next_cursor = (
                    EventCursor(events[-1].store_seq, events[-1].event_id)
                    if events
                    else after
                )
                return JournalPage(events, next_cursor, has_more)
        except sqlite3.Error as exc:
            raise JournalRepositoryError("SQLite journal read failed") from exc

    def capture_snapshot(
        self,
        *,
        max_events: int = 10_000,
        max_bytes: int = 32 * 1024 * 1024,
    ) -> JournalSnapshot:
        max_events = _exact_non_negative_int(max_events, "max_events")
        max_bytes = _exact_non_negative_int(max_bytes, "max_bytes")
        try:
            with self._store._transaction(immediate=False) as connection:
                rows = list(
                    connection.execute(
                        """
                        SELECT * FROM events
                        WHERE execution_id = ?
                        ORDER BY store_seq
                        LIMIT ?
                        """,
                        (self.execution_id, max_events + 1),
                    )
                )
                if len(rows) > max_events:
                    raise JournalRepositoryError(
                        "journal snapshot event limit exceeded"
                    )
                events = tuple(self._event_from_row(connection, row) for row in rows)
                encoded = _canonical_json_bytes([event.to_dict() for event in events])
                if len(encoded) > max_bytes:
                    raise JournalRepositoryError("journal snapshot byte limit exceeded")
                return capture_journal_snapshot(
                    execution_id=self.execution_id,
                    events=events,
                )
        except sqlite3.Error as exc:
            raise JournalRepositoryError("SQLite journal snapshot failed") from exc

    def _lookup_receipt_events(
        self,
        *,
        attempt: AttemptRef,
        receipt_kind: str,
        call_id: str | None,
        iteration: int | None,
        maximum: int,
    ) -> tuple[tuple[JournalEvent, ...], bool]:
        self._scope_attempt(attempt, error_type=JournalScopeError)
        with self._store._transaction(immediate=False) as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT e.*
                    FROM event_receipts AS r
                    JOIN events AS e
                      ON e.execution_id = r.execution_id
                     AND e.store_seq = r.store_seq
                    WHERE r.execution_id = ?
                      AND r.generation_id = ?
                      AND r.attempt_id = ?
                      AND r.receipt_kind = ?
                      AND r.call_id IS ?
                      AND r.iteration IS ?
                    ORDER BY r.store_seq
                    LIMIT ?
                    """,
                    (
                        self.execution_id,
                        attempt.generation.generation_id,
                        attempt.attempt_id,
                        receipt_kind,
                        call_id,
                        iteration,
                        maximum + 1,
                    ),
                )
            )
            return (
                tuple(self._event_from_row(connection, row) for row in rows[:maximum]),
                len(rows) > maximum,
            )

    def lookup_tool_execution_receipts(
        self,
        *,
        attempt: AttemptRef,
        call_id: str,
    ) -> ToolExecutionReceiptLookup:
        normalized_call_id = _required_text(
            call_id,
            "call_id",
            identifier=True,
        )
        events, overflow = self._lookup_receipt_events(
            attempt=attempt,
            receipt_kind="tool_execution",
            call_id=normalized_call_id,
            iteration=None,
            maximum=MAX_TOOL_EXECUTION_RECEIPTS,
        )
        return ToolExecutionReceiptLookup(
            attempt=attempt,
            call_id=normalized_call_id,
            events=events,
            overflow=overflow,
        )

    def lookup_tool_catalog_receipts(
        self,
        *,
        attempt: AttemptRef,
        iteration: int,
    ) -> ToolCatalogReceiptLookup:
        iteration = _exact_non_negative_int(iteration, "iteration")
        events, overflow = self._lookup_receipt_events(
            attempt=attempt,
            receipt_kind="tool_catalog",
            call_id=None,
            iteration=iteration,
            maximum=MAX_TOOL_CATALOG_RECEIPTS,
        )
        return ToolCatalogReceiptLookup(
            attempt=attempt,
            iteration=iteration,
            events=events,
            overflow=overflow,
        )

    def lookup_provider_wire_receipts(
        self,
        *,
        attempt: AttemptRef,
        iteration: int,
    ) -> ProviderWireReceiptLookup:
        iteration = _exact_non_negative_int(iteration, "iteration")
        events, overflow = self._lookup_receipt_events(
            attempt=attempt,
            receipt_kind="provider_wire",
            call_id=None,
            iteration=iteration,
            maximum=MAX_PROVIDER_WIRE_RECEIPTS,
        )
        return ProviderWireReceiptLookup(
            attempt=attempt,
            iteration=iteration,
            events=events,
            overflow=overflow,
        )

    @staticmethod
    def _artifact_id(
        *,
        execution_id: str,
        logical_kind: str,
        logical_key: str,
    ) -> str:
        digest = _sha256(
            _canonical_json_bytes(
                {
                    "execution_id": execution_id,
                    "logical_kind": logical_kind,
                    "logical_key": logical_key,
                }
            )
        )
        return f"artifact-{digest}"

    def _artifact_from_row(self, row: sqlite3.Row) -> ArtifactRef:
        raw = bytes(row["artifact_json"])
        if _sha256(raw) != row["artifact_record_sha256"]:
            raise SQLiteContextV2StoreIntegrityError(
                "artifact descriptor digest changed"
            )
        try:
            decoded = json.loads(raw.decode("utf-8"))
            artifact = ArtifactRef.from_dict(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise SQLiteContextV2StoreIntegrityError(
                "artifact descriptor is malformed"
            ) from exc
        if _canonical_json_bytes(artifact.to_dict()) != raw:
            raise SQLiteContextV2StoreIntegrityError(
                "artifact descriptor is not canonical"
            )
        if (
            artifact.ref.resource_id != row["artifact_id"]
            or artifact.ref.revision != row["revision"]
            or artifact.sha256 != row["object_sha256"]
            or artifact.media_type != row["media_type"]
            or artifact.byte_length != row["byte_length"]
            or artifact.preview != row["preview"]
        ):
            raise SQLiteContextV2StoreIntegrityError(
                "artifact indexed metadata changed"
            )
        return artifact

    def _artifact_by_operation(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> ArtifactRef:
        row = connection.execute(
            """
            SELECT * FROM artifacts
            WHERE execution_id = ? AND operation_id = ?
            """,
            (self.execution_id, operation_id),
        ).fetchone()
        if row is None:
            raise SQLiteContextV2StoreIntegrityError(
                "artifact operation has no descriptor"
            )
        return self._artifact_from_row(row)

    def _put_artifact(
        self,
        *,
        content: bytes,
        media_type: str,
        operation: OperationRef,
        preview: str,
        logical_kind: str,
        logical_key: str,
        expected_revision: int,
        conflict_type: type[Exception],
    ) -> ArtifactRef:
        if type(content) is not bytes:
            raise TypeError("artifact content must be exact bytes")
        if not isinstance(operation, OperationRef):
            raise TypeError("operation must be an OperationRef")
        expected_revision = _exact_non_negative_int(
            expected_revision,
            "expected_revision",
        )
        media_type = _required_text(media_type, "media_type", maximum=255)
        preview = (
            ""
            if preview == ""
            else _required_text(
                preview,
                "preview",
                maximum=4096,
            )
        )
        digest, byte_length = self._store._install_object(content)
        artifact_id = self._artifact_id(
            execution_id=self.execution_id,
            logical_kind=logical_kind,
            logical_key=logical_key,
        )
        revision = 1
        target_key = f"{artifact_id}@{revision}"
        try:
            with self._store._transaction(immediate=True) as connection:
                self._store._ensure_execution(connection, self.execution_id)
                previous = self._operation_row(
                    connection,
                    operation.operation_id,
                )
                if previous is not None:
                    if (
                        previous["payload_sha256"] != operation.payload_sha256
                        or previous["target_kind"] != logical_kind
                        or previous["target_key"] != target_key
                    ):
                        raise conflict_type(
                            "artifact operation payload or target changed"
                        )
                    artifact = self._artifact_by_operation(
                        connection,
                        operation.operation_id,
                    )
                    if (
                        artifact.media_type != media_type
                        or artifact.byte_length != byte_length
                        or artifact.sha256 != digest
                        or artifact.preview != preview
                    ):
                        raise conflict_type("artifact operation replay changed content")
                    return artifact
                if expected_revision != 0:
                    raise conflict_type("artifact CAS expected revision does not exist")
                existing = connection.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE execution_id = ?
                      AND logical_kind = ?
                      AND logical_key = ?
                      AND revision = 1
                    """,
                    (self.execution_id, logical_kind, logical_key),
                ).fetchone()
                if existing is not None:
                    raise conflict_type("artifact logical claim already exists")
                resource = ResourceRef("artifact", artifact_id, revision)
                artifact = ArtifactRef(
                    ref=resource,
                    media_type=media_type,
                    byte_length=byte_length,
                    sha256=digest,
                    preview=preview,
                )
                self._claim_operation(
                    connection,
                    operation=operation,
                    target_kind=logical_kind,
                    target_key=target_key,
                    conflict_type=conflict_type,
                )
                object_row = connection.execute(
                    "SELECT byte_length FROM objects WHERE sha256 = ?",
                    (digest,),
                ).fetchone()
                if object_row is not None and object_row["byte_length"] != byte_length:
                    raise SQLiteContextV2StoreIntegrityError(
                        "object metadata byte length changed"
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO objects(sha256, byte_length) VALUES (?, ?)",
                    (digest, byte_length),
                )
                artifact_json = _canonical_json_bytes(artifact.to_dict())
                connection.execute(
                    """
                    INSERT INTO artifacts(
                        execution_id,
                        artifact_id,
                        revision,
                        logical_kind,
                        logical_key,
                        object_sha256,
                        media_type,
                        byte_length,
                        preview,
                        operation_id,
                        artifact_json,
                        artifact_record_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.execution_id,
                        artifact_id,
                        revision,
                        logical_kind,
                        logical_key,
                        digest,
                        media_type,
                        byte_length,
                        preview,
                        operation.operation_id,
                        artifact_json,
                        _sha256(artifact_json),
                    ),
                )
                return artifact
        except sqlite3.IntegrityError as exc:
            raise conflict_type("artifact operation or claim conflicted") from exc
        except sqlite3.Error as exc:
            raise ContextRepositoryError("SQLite artifact write failed") from exc

    def put(
        self,
        *,
        content: bytes,
        media_type: str,
        operation: OperationRef,
        preview: str = "",
    ) -> ArtifactRef:
        return self._put_artifact(
            content=content,
            media_type=media_type,
            operation=operation,
            preview=preview,
            logical_kind="artifact",
            logical_key=operation.operation_id,
            expected_revision=0,
            conflict_type=ContextConflictError,
        )

    def _load_exact_artifact(self, artifact: ArtifactRef) -> ArtifactRef:
        if not isinstance(artifact, ArtifactRef):
            raise TypeError("artifact must be an ArtifactRef")
        if artifact.ref.kind != "artifact" or artifact.ref.fragment:
            raise ContextScopeError("artifact ref is outside this repository")
        try:
            with self._store._transaction(immediate=False) as connection:
                row = connection.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE execution_id = ? AND artifact_id = ? AND revision = ?
                    """,
                    (
                        self.execution_id,
                        artifact.ref.resource_id,
                        artifact.ref.revision,
                    ),
                ).fetchone()
                if row is None:
                    raise ContextScopeError(
                        "artifact does not belong to the bound execution"
                    )
                stored = self._artifact_from_row(row)
                if stored != artifact:
                    raise SQLiteContextV2StoreIntegrityError(
                        "artifact descriptor disagrees with durable metadata"
                    )
                object_row = connection.execute(
                    "SELECT byte_length FROM objects WHERE sha256 = ?",
                    (stored.sha256,),
                ).fetchone()
                if (
                    object_row is None
                    or object_row["byte_length"] != stored.byte_length
                ):
                    raise SQLiteContextV2StoreIntegrityError(
                        "artifact object metadata is missing or changed"
                    )
                return stored
        except sqlite3.Error as exc:
            raise ContextRepositoryError("SQLite artifact read failed") from exc

    def read_full_verified(self, *, artifact: ArtifactRef) -> bytes:
        stored = self._load_exact_artifact(artifact)
        return self._store._read_object(
            digest=stored.sha256,
            byte_length=stored.byte_length,
        )

    def read_verified(
        self,
        *,
        artifact: ArtifactRef,
        offset: int = 0,
        limit: int = 65_536,
    ) -> bytes:
        offset = _exact_non_negative_int(offset, "offset")
        limit = _exact_non_negative_int(limit, "limit")
        content = self.read_full_verified(artifact=artifact)
        return content[offset : offset + limit]

    def write_provider_wire_cas(
        self,
        *,
        content: bytes,
        media_type: str,
        preview: str,
        operation: OperationRef,
        expected_revision: int,
    ) -> ArtifactRef:
        if type(content) is not bytes:
            raise TypeError("provider wire content must be exact bytes")
        try:
            decoded = json.loads(content.decode("utf-8"))
            envelope = ProviderWireEnvelope.from_dict(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise SQLiteContextV2StoreIntegrityError(
                "provider wire object is not a canonical envelope"
            ) from exc
        if envelope.canonical_bytes() != content:
            raise SQLiteContextV2StoreIntegrityError(
                "provider wire object is not canonical"
            )
        self._scope_attempt(envelope.attempt, error_type=JournalScopeError)
        logical_key = ":".join(
            (
                envelope.attempt.generation.generation_id,
                envelope.attempt.attempt_id,
                str(envelope.iteration),
            )
        )
        return self._put_artifact(
            content=content,
            media_type=media_type,
            operation=operation,
            preview=preview,
            logical_kind="provider_wire_artifact",
            logical_key=logical_key,
            expected_revision=expected_revision,
            conflict_type=ContextConflictError,
        )

    def read_provider_wire_full_verified(
        self,
        *,
        artifact: ArtifactRef,
    ) -> bytes:
        return self.read_full_verified(artifact=artifact)

    def _provider_result_event_row(
        self,
        connection: sqlite3.Connection,
        *,
        subject: ProviderRequestSubject,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT e.*
            FROM provider_turn_result_receipts AS r
            JOIN events AS e
              ON e.execution_id = r.execution_id
             AND e.store_seq = r.store_seq
            WHERE r.execution_id = ?
              AND r.generation_id = ?
              AND r.attempt_id = ?
              AND r.iteration = ?
              AND r.envelope_sha256 = ?
              AND r.route = ?
              AND r.retry_ordinal = ?
            """,
            _subject_key(subject),
        ).fetchone()

    def _persist_provider_turn_result_cas(
        self,
        *,
        request: ProviderTurnResultPersistRequest,
    ) -> ProviderTurnResultReceipt:
        if type(request) is not ProviderTurnResultPersistRequest:
            raise TypeError("request must be an exact ProviderTurnResultPersistRequest")
        started = request.started_lease
        envelope = request.envelope
        content = envelope.canonical_bytes()
        digest, byte_length = self._store._install_object(content)
        logical_kind = "provider_turn_result_artifact"
        logical_key = envelope.subject_sha256
        artifact_id = self._artifact_id(
            execution_id=self.execution_id,
            logical_kind=logical_kind,
            logical_key=logical_key,
        )
        artifact = ArtifactRef(
            ref=ResourceRef("artifact", artifact_id, 1),
            media_type="application/json",
            byte_length=byte_length,
            sha256=digest,
            preview="",
        )
        artifact_target = f"{artifact_id}@1"

        try:
            with self._store._transaction(immediate=True) as connection:
                self._store._ensure_execution(connection, self.execution_id)
                current = self._load_lease_in_transaction(
                    connection,
                    started.subject,
                )
                if (
                    current != started
                    or current is None
                    or current.status is not ProviderRequestStatus.STARTED
                ):
                    raise ProviderTurnResultIntegrityError(
                        "provider result lease is no longer the exact STARTED revision"
                    )

                existing_row = self._provider_result_event_row(
                    connection,
                    subject=started.subject,
                )
                if existing_row is not None:
                    event = self._event_from_row(connection, existing_row)
                    fields = provider_turn_result_event_fields(event)
                    stored_artifact = self._artifact_by_operation(
                        connection,
                        request.artifact_operation.operation_id,
                    )
                    artifact_operation = self._operation_row(
                        connection,
                        request.artifact_operation.operation_id,
                    )
                    if (
                        artifact_operation is None
                        or artifact_operation["payload_sha256"]
                        != request.artifact_operation.payload_sha256
                        or artifact_operation["target_kind"] != logical_kind
                        or artifact_operation["target_key"] != artifact_target
                        or stored_artifact != artifact
                        or fields.subject != started.subject
                        or fields.route_sha256 != envelope.route_sha256
                        or fields.visible_output != envelope.visible_output
                        or fields.result_sha256 != envelope.result_sha256
                        or fields.artifact != artifact
                        or event.event_id != request.event_id
                        or event.operation != request.event_operation
                    ):
                        raise ProviderTurnResultIntegrityError(
                            "provider result idempotent replay changed"
                        )
                    return ProviderTurnResultReceipt(
                        envelope=envelope,
                        artifact=artifact,
                        event=event,
                        cursor=EventCursor(event.store_seq, event.event_id),
                        duplicate=True,
                    )

                if (
                    self._operation_row(
                        connection,
                        request.artifact_operation.operation_id,
                    )
                    is not None
                ):
                    raise ProviderTurnResultIntegrityError(
                        "provider result artifact operation has no receipt"
                    )
                self._claim_operation(
                    connection,
                    operation=request.artifact_operation,
                    target_kind=logical_kind,
                    target_key=artifact_target,
                    conflict_type=ProviderTurnResultIntegrityError,
                )
                object_row = connection.execute(
                    "SELECT byte_length FROM objects WHERE sha256 = ?",
                    (digest,),
                ).fetchone()
                if object_row is not None and object_row["byte_length"] != byte_length:
                    raise SQLiteContextV2StoreIntegrityError(
                        "provider result object byte length changed"
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO objects(sha256, byte_length) VALUES (?, ?)",
                    (digest, byte_length),
                )
                artifact_json = _canonical_json_bytes(artifact.to_dict())
                connection.execute(
                    """
                    INSERT INTO artifacts(
                        execution_id,
                        artifact_id,
                        revision,
                        logical_kind,
                        logical_key,
                        object_sha256,
                        media_type,
                        byte_length,
                        preview,
                        operation_id,
                        artifact_json,
                        artifact_record_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.execution_id,
                        artifact_id,
                        1,
                        logical_kind,
                        logical_key,
                        digest,
                        artifact.media_type,
                        byte_length,
                        artifact.preview,
                        request.artifact_operation.operation_id,
                        artifact_json,
                        _sha256(artifact_json),
                    ),
                )

                if (
                    self._operation_row(
                        connection,
                        request.event_operation.operation_id,
                    )
                    is not None
                ):
                    raise ProviderTurnResultIntegrityError(
                        "provider result event operation is already claimed"
                    )
                if (
                    connection.execute(
                        """
                        SELECT 1 FROM events
                        WHERE execution_id = ? AND event_id = ?
                        """,
                        (self.execution_id, request.event_id),
                    ).fetchone()
                    is not None
                ):
                    raise ProviderTurnResultIntegrityError(
                        "provider result event id is already claimed"
                    )
                head = connection.execute(
                    """
                    SELECT next_store_seq FROM executions
                    WHERE execution_id = ?
                    """,
                    (self.execution_id,),
                ).fetchone()
                if head is None:
                    raise SQLiteContextV2StoreIntegrityError(
                        "provider result journal head is missing"
                    )
                store_seq = int(head["next_store_seq"])
                event = JournalEvent(
                    event_id=request.event_id,
                    event_type=PROVIDER_TURN_RESULT_EVENT_TYPE,
                    attempt=started.subject.attempt,
                    operation=request.event_operation,
                    store_seq=store_seq,
                    payload=build_provider_turn_result_event_payload(
                        envelope=envelope,
                        artifact=artifact,
                    ),
                    resource_refs=(artifact.ref,),
                )
                self._claim_operation(
                    connection,
                    operation=request.event_operation,
                    target_kind="journal_event",
                    target_key=request.event_id,
                    conflict_type=ProviderTurnResultIntegrityError,
                )
                event_json = _canonical_json_bytes(event.to_dict())
                connection.execute(
                    """
                    INSERT INTO events(
                        execution_id,
                        store_seq,
                        event_id,
                        generation_id,
                        attempt_id,
                        event_type,
                        operation_id,
                        event_json,
                        event_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.execution_id,
                        store_seq,
                        event.event_id,
                        event.attempt.generation.generation_id,
                        event.attempt.attempt_id,
                        event.event_type,
                        event.operation.operation_id,
                        event_json,
                        _sha256(event_json),
                    ),
                )
                subject_key = _subject_key(started.subject)
                connection.execute(
                    """
                    INSERT INTO provider_turn_result_receipts(
                        execution_id,
                        generation_id,
                        attempt_id,
                        iteration,
                        envelope_sha256,
                        route,
                        retry_ordinal,
                        store_seq
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*subject_key, store_seq),
                )
                updated = connection.execute(
                    """
                    UPDATE executions SET next_store_seq = ?
                    WHERE execution_id = ? AND next_store_seq = ?
                    """,
                    (store_seq + 1, self.execution_id, store_seq),
                )
                if updated.rowcount != 1:
                    raise ProviderTurnResultIntegrityError(
                        "provider result journal sequence conflicted"
                    )
                return ProviderTurnResultReceipt(
                    envelope=envelope,
                    artifact=artifact,
                    event=event,
                    cursor=EventCursor(store_seq, event.event_id),
                    duplicate=False,
                )
        except sqlite3.IntegrityError as exc:
            raise ProviderTurnResultIntegrityError(
                "provider result artifact, event, or subject conflicted"
            ) from exc
        except sqlite3.Error as exc:
            raise SQLiteContextV2StoreIntegrityError(
                "SQLite provider result persistence failed"
            ) from exc

    def read_provider_turn_result_full_verified(
        self,
        *,
        artifact: ArtifactRef,
    ) -> bytes:
        return self.read_full_verified(artifact=artifact)

    def lookup_provider_turn_result_receipts(
        self,
        *,
        subject: ProviderRequestSubject,
    ) -> ProviderTurnResultReceiptLookup:
        if type(subject) is not ProviderRequestSubject:
            raise TypeError("subject must be an exact ProviderRequestSubject")
        self._scope_attempt(
            subject.attempt,
            error_type=ProviderTurnResultIntegrityError,
        )
        try:
            with self._store._transaction(immediate=False) as connection:
                rows = list(
                    connection.execute(
                        """
                        SELECT e.*
                        FROM provider_turn_result_receipts AS r
                        JOIN events AS e
                          ON e.execution_id = r.execution_id
                         AND e.store_seq = r.store_seq
                        WHERE r.execution_id = ?
                          AND r.generation_id = ?
                          AND r.attempt_id = ?
                          AND r.iteration = ?
                          AND r.envelope_sha256 = ?
                          AND r.route = ?
                          AND r.retry_ordinal = ?
                        ORDER BY r.store_seq
                        LIMIT ?
                        """,
                        (*_subject_key(subject), MAX_PROVIDER_TURN_RESULT_RECEIPTS + 1),
                    )
                )
                events = tuple(
                    self._event_from_row(connection, row)
                    for row in rows[:MAX_PROVIDER_TURN_RESULT_RECEIPTS]
                )
                return ProviderTurnResultReceiptLookup(
                    subject=subject,
                    events=events,
                    overflow=len(rows) > MAX_PROVIDER_TURN_RESULT_RECEIPTS,
                )
        except sqlite3.Error as exc:
            raise SQLiteContextV2StoreIntegrityError(
                "SQLite provider result receipt lookup failed"
            ) from exc

    def _lease_from_row(self, row: sqlite3.Row) -> ProviderRequestLease:
        raw = bytes(row["lease_json"])
        if _sha256(raw) != row["lease_sha256"]:
            raise SQLiteContextV2StoreIntegrityError(
                "provider request lease digest changed"
            )
        try:
            decoded = json.loads(raw.decode("utf-8"))
            lease = ProviderRequestLease.from_dict(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise SQLiteContextV2StoreIntegrityError(
                "provider request lease record is malformed"
            ) from exc
        if _canonical_json_bytes(lease.to_dict()) != raw:
            raise SQLiteContextV2StoreIntegrityError(
                "provider request lease record is not canonical"
            )
        expected_key = _subject_key(lease.subject)
        if (
            expected_key
            != tuple(
                row[name]
                for name in (
                    "execution_id",
                    "generation_id",
                    "attempt_id",
                    "iteration",
                    "envelope_sha256",
                    "route",
                    "retry_ordinal",
                )
            )
            or lease.revision != row["revision"]
            or (lease.operation.operation_id != row["operation_id"])
        ):
            raise SQLiteContextV2StoreIntegrityError(
                "provider request lease indexed fields changed"
            )
        return lease

    def _load_lease_in_transaction(
        self,
        connection: sqlite3.Connection,
        subject: ProviderRequestSubject,
    ) -> ProviderRequestLease | None:
        key = _subject_key(subject)
        head = connection.execute(
            """
            SELECT current_revision
            FROM provider_request_lease_heads
            WHERE execution_id = ?
              AND generation_id = ?
              AND attempt_id = ?
              AND iteration = ?
              AND envelope_sha256 = ?
              AND route = ?
              AND retry_ordinal = ?
            """,
            key,
        ).fetchone()
        if head is None:
            orphan = connection.execute(
                """
                SELECT 1
                FROM provider_request_lease_revisions
                WHERE execution_id = ?
                  AND generation_id = ?
                  AND attempt_id = ?
                  AND iteration = ?
                  AND envelope_sha256 = ?
                  AND route = ?
                  AND retry_ordinal = ?
                LIMIT 1
                """,
                key,
            ).fetchone()
            if orphan is not None:
                raise ProviderRequestLeaseIntegrityError(
                    "provider request lease revision has no durable head"
                )
            lease_operation = connection.execute(
                """
                SELECT 1 FROM operations
                WHERE execution_id = ?
                  AND target_kind = 'provider_request_lease'
                  AND target_key = ?
                LIMIT 1
                """,
                (self.execution_id, _subject_target(subject, 1)),
            ).fetchone()
            result_receipt = connection.execute(
                """
                SELECT 1 FROM provider_turn_result_receipts
                WHERE execution_id = ?
                  AND generation_id = ?
                  AND attempt_id = ?
                  AND iteration = ?
                  AND envelope_sha256 = ?
                  AND route = ?
                  AND retry_ordinal = ?
                LIMIT 1
                """,
                key,
            ).fetchone()
            result_artifact = connection.execute(
                """
                SELECT 1 FROM artifacts
                WHERE execution_id = ?
                  AND logical_kind = 'provider_turn_result_artifact'
                  AND logical_key = ?
                LIMIT 1
                """,
                (self.execution_id, _subject_sha256(subject)),
            ).fetchone()
            if (
                lease_operation is not None
                or result_receipt is not None
                or result_artifact is not None
            ):
                raise ProviderRequestLeaseIntegrityError(
                    "provider request lease rows are missing despite durable send evidence"
                )
            return None

        current_revision = head["current_revision"]
        revision_state = connection.execute(
            """
            SELECT MAX(revision) AS latest_revision
            FROM provider_request_lease_revisions
            WHERE execution_id = ?
              AND generation_id = ?
              AND attempt_id = ?
              AND iteration = ?
              AND envelope_sha256 = ?
              AND route = ?
              AND retry_ordinal = ?
            """,
            key,
        ).fetchone()
        if (
            revision_state is None
            or revision_state["latest_revision"] is None
            or revision_state["latest_revision"] != current_revision
        ):
            raise ProviderRequestLeaseIntegrityError(
                "provider request lease head does not name its latest revision"
            )

        row = connection.execute(
            """
            SELECT *
            FROM provider_request_lease_revisions
            WHERE execution_id = ?
              AND generation_id = ?
              AND attempt_id = ?
              AND iteration = ?
              AND envelope_sha256 = ?
              AND route = ?
              AND retry_ordinal = ?
              AND revision = ?
            """,
            (*key, current_revision),
        ).fetchone()
        if row is None:
            raise ProviderRequestLeaseIntegrityError(
                "provider request lease head points to a missing revision"
            )
        return self._lease_from_row(row)

    def load(
        self,
        *,
        subject: ProviderRequestSubject,
    ) -> ProviderRequestLease | None:
        if type(subject) is not ProviderRequestSubject:
            raise TypeError("subject must be an exact ProviderRequestSubject")
        self._scope_attempt(
            subject.attempt,
            error_type=ProviderRequestLeaseIntegrityError,
        )
        try:
            with self._store._transaction(immediate=False) as connection:
                return self._load_lease_in_transaction(connection, subject)
        except sqlite3.Error as exc:
            raise ProviderRequestLeaseIntegrityError(
                "SQLite provider request lease read failed"
            ) from exc

    def compare_and_swap(
        self,
        *,
        subject: ProviderRequestSubject,
        expected_revision: int,
        replacement: ProviderRequestLease,
    ) -> ProviderRequestLease:
        if type(subject) is not ProviderRequestSubject:
            raise TypeError("subject must be an exact ProviderRequestSubject")
        if type(replacement) is not ProviderRequestLease:
            raise TypeError("replacement must be an exact ProviderRequestLease")
        if replacement.subject != subject:
            raise ProviderRequestLeaseIntegrityError("replacement subject changed")
        self._scope_attempt(
            subject.attempt,
            error_type=ProviderRequestLeaseIntegrityError,
        )
        expected_revision = _exact_non_negative_int(
            expected_revision,
            "expected_revision",
        )
        try:
            with self._store._transaction(immediate=True) as connection:
                self._store._ensure_execution(connection, self.execution_id)
                current = self._load_lease_in_transaction(connection, subject)
                current_revision = 0 if current is None else current.revision
                if current_revision != expected_revision:
                    raise ProviderRequestLeaseConflict(
                        "provider request lease CAS conflict"
                    )
                if replacement.revision != expected_revision + 1:
                    raise ProviderRequestLeaseConflict(
                        "provider request lease revision is not the next revision"
                    )
                target_key = _subject_target(subject, replacement.revision)
                self._claim_operation(
                    connection,
                    operation=replacement.operation,
                    target_kind="provider_request_lease",
                    target_key=target_key,
                    conflict_type=ProviderRequestLeaseConflict,
                )
                lease_json = _canonical_json_bytes(replacement.to_dict())
                key = _subject_key(subject)
                connection.execute(
                    """
                    INSERT INTO provider_request_lease_revisions(
                        execution_id,
                        generation_id,
                        attempt_id,
                        iteration,
                        envelope_sha256,
                        route,
                        retry_ordinal,
                        revision,
                        operation_id,
                        lease_json,
                        lease_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        *key,
                        replacement.revision,
                        replacement.operation.operation_id,
                        lease_json,
                        _sha256(lease_json),
                    ),
                )
                if current is None:
                    connection.execute(
                        """
                        INSERT INTO provider_request_lease_heads(
                            execution_id,
                            generation_id,
                            attempt_id,
                            iteration,
                            envelope_sha256,
                            route,
                            retry_ordinal,
                            current_revision
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (*key, replacement.revision),
                    )
                else:
                    updated = connection.execute(
                        """
                        UPDATE provider_request_lease_heads
                        SET current_revision = ?
                        WHERE execution_id = ?
                          AND generation_id = ?
                          AND attempt_id = ?
                          AND iteration = ?
                          AND envelope_sha256 = ?
                          AND route = ?
                          AND retry_ordinal = ?
                          AND current_revision = ?
                        """,
                        (
                            replacement.revision,
                            *key,
                            expected_revision,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise ProviderRequestLeaseConflict(
                            "provider request lease head CAS conflict"
                        )
                return replacement
        except sqlite3.IntegrityError as exc:
            raise ProviderRequestLeaseConflict(
                "provider request lease operation or subject conflicted"
            ) from exc
        except sqlite3.Error as exc:
            raise ProviderRequestLeaseIntegrityError(
                "SQLite provider request lease CAS failed"
            ) from exc


__all__ = [
    "SQLiteContextV2Store",
    "SQLiteContextV2StoreError",
    "SQLiteContextV2StoreIntegrityError",
]
