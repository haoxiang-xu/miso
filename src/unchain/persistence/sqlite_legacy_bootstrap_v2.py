"""Transactional lazy bootstrap of host-sanitized legacy chat history.

The host owns legacy storage and admission.  This module accepts only an exact,
already-sanitized user/assistant snapshot and imports it into the Context V2
SQLite journal.  The journal events, bootstrap manifest, operation receipt, and
current-generation head commit in one SQLite transaction; a successful receipt
is therefore the only authority a host may use before setting its sticky V2
flag.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar, Iterator, Mapping

from unchain.journal import (
    AttemptRef,
    EventCursor,
    GenerationRef,
    OperationRef,
    ResourceRef,
    SemanticEventDraft,
)
from unchain.journal.models import (
    JournalEvent,
    ModelValidationError,
    _bounded_int,
    _record_data,
    _record_tuple,
    _required_text,
    _sha256,
)
from unchain.persistence.sqlite_v2 import (
    SQLiteContextV2Store,
    serialized_context_v2_database_access,
)


_MAX_LEGACY_MESSAGES = 10_000
_MAX_LEGACY_SNAPSHOT_BYTES = 32 * 1024 * 1024
_CAPTURE_STATUS = "legacy_partial"


class LegacyBootstrapError(RuntimeError):
    """Base error for legacy-to-Context-V2 admission preparation."""


class LegacyBootstrapConflict(LegacyBootstrapError):
    """A source revision, operation, or generation precondition drifted."""


class LegacyBootstrapUnavailable(LegacyBootstrapError):
    """Bootstrap cannot produce a receipt safe for sticky V2 admission."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
        raise ModelValidationError(
            "legacy bootstrap value is not canonical JSON"
        ) from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _exact_bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{field_name} must be a boolean")
    return value


@dataclass(frozen=True)
class LegacyMessage:
    """One stable host-sanitized legacy chat message."""

    message_id: str
    role: str
    content: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "message_id",
            _required_text(self.message_id, "message_id", identifier=True),
        )
        if type(self.role) is not str or self.role not in {"user", "assistant"}:
            raise ModelValidationError(
                "legacy message role must be exactly user or assistant"
            )
        if (
            type(self.content) is not str
            or not self.content.strip()
            or "\x00" in self.content
        ):
            raise ModelValidationError("legacy message content must be non-empty text")

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
        }


@dataclass(frozen=True)
class LegacyTaskStateDescriptor:
    """Content-free verified descriptor for a host-owned pinned task state."""

    descriptor_id: str
    revision: int
    descriptor_sha256: str
    refs: tuple[ResourceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "descriptor_id",
            _required_text(self.descriptor_id, "descriptor_id", identifier=True),
        )
        object.__setattr__(
            self,
            "revision",
            _bounded_int(self.revision, "revision", minimum=1),
        )
        object.__setattr__(
            self,
            "descriptor_sha256",
            _sha256(self.descriptor_sha256, "descriptor_sha256"),
        )
        object.__setattr__(
            self,
            "refs",
            _record_tuple(self.refs, ResourceRef, "refs"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "descriptor_id": self.descriptor_id,
            "revision": self.revision,
            "descriptor_sha256": self.descriptor_sha256,
            "refs": [ref.to_dict() for ref in self.refs],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LegacyTaskStateDescriptor:
        raw = _record_data(
            value,
            schema="unchain.legacy_task_state_descriptor.v1",
            required=frozenset(
                {"descriptor_id", "revision", "descriptor_sha256", "refs"}
            ),
        )
        return cls(
            descriptor_id=raw["descriptor_id"],
            revision=raw["revision"],
            descriptor_sha256=raw["descriptor_sha256"],
            refs=_record_tuple(raw["refs"], ResourceRef, "refs"),
        )

    def to_record(self) -> dict[str, Any]:
        return {"schema": "unchain.legacy_task_state_descriptor.v1", **self.to_dict()}


@dataclass(frozen=True)
class LegacyBootstrapPreflight:
    """Host proof required before any legacy bytes enter Context V2."""

    proof_id: str
    no_unfinished_durable_checkpoint: bool
    no_pending_interaction: bool
    host_snapshot_sanitized: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "proof_id",
            _required_text(self.proof_id, "proof_id", identifier=True),
        )
        for field_name in (
            "no_unfinished_durable_checkpoint",
            "no_pending_interaction",
            "host_snapshot_sanitized",
        ):
            object.__setattr__(
                self,
                field_name,
                _exact_bool(getattr(self, field_name), field_name),
            )

    @property
    def permits_bootstrap(self) -> bool:
        return (
            self.no_unfinished_durable_checkpoint
            and self.no_pending_interaction
            and self.host_snapshot_sanitized
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proof_id": self.proof_id,
            "no_unfinished_durable_checkpoint": (self.no_unfinished_durable_checkpoint),
            "no_pending_interaction": self.no_pending_interaction,
            "host_snapshot_sanitized": self.host_snapshot_sanitized,
        }


class LegacyRebaseKind(StrEnum):
    INITIAL = "initial"
    EDIT = "edit"
    REGENERATE = "regenerate"


@dataclass(frozen=True)
class LegacyGenerationDescriptor:
    """Runtime identities and the exact current-generation transition."""

    session_id: str
    execution_id: str
    generation_id: str
    attempt_id: str
    rebase_kind: LegacyRebaseKind = LegacyRebaseKind.INITIAL
    previous_generation_id: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "session_id",
            "execution_id",
            "generation_id",
            "attempt_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name, identifier=True),
            )
        try:
            kind = LegacyRebaseKind(self.rebase_kind)
        except (TypeError, ValueError) as exc:
            raise ModelValidationError("legacy rebase kind is invalid") from exc
        object.__setattr__(self, "rebase_kind", kind)
        previous = self.previous_generation_id
        if kind is LegacyRebaseKind.INITIAL:
            if previous not in (None, ""):
                raise ModelValidationError(
                    "initial legacy bootstrap cannot name a previous generation"
                )
            object.__setattr__(self, "previous_generation_id", "")
        else:
            normalized = _required_text(
                previous,
                "previous_generation_id",
                identifier=True,
            )
            if normalized == self.generation_id:
                raise ModelValidationError("legacy rebase must create a new generation")
            object.__setattr__(self, "previous_generation_id", normalized)

    @property
    def attempt(self) -> AttemptRef:
        return AttemptRef(
            GenerationRef(self.execution_id, self.generation_id),
            self.attempt_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "execution_id": self.execution_id,
            "generation_id": self.generation_id,
            "attempt_id": self.attempt_id,
            "rebase_kind": self.rebase_kind.value,
            "previous_generation_id": self.previous_generation_id,
        }


@dataclass(frozen=True)
class LegacyBootstrapPayload:
    """Exact host snapshot whose digest authorizes one transactional import."""

    owner_chat_id: str
    source_revision: str
    messages: tuple[LegacyMessage, ...]
    generation: LegacyGenerationDescriptor
    preflight: LegacyBootstrapPreflight
    task_state: LegacyTaskStateDescriptor | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "owner_chat_id",
            _required_text(self.owner_chat_id, "owner_chat_id", identifier=True),
        )
        object.__setattr__(
            self,
            "source_revision",
            _required_text(
                self.source_revision,
                "source_revision",
                identifier=True,
            ),
        )
        if not isinstance(self.generation, LegacyGenerationDescriptor):
            raise TypeError("generation must be a LegacyGenerationDescriptor")
        if not isinstance(self.preflight, LegacyBootstrapPreflight):
            raise TypeError("preflight must be a LegacyBootstrapPreflight")
        messages = tuple(self.messages)
        if not messages or len(messages) > _MAX_LEGACY_MESSAGES:
            raise ModelValidationError(
                "legacy messages history must contain between 1 and 10000 items"
            )
        if any(not isinstance(message, LegacyMessage) for message in messages):
            raise TypeError("messages must contain LegacyMessage records")
        message_ids = [message.message_id for message in messages]
        if len(set(message_ids)) != len(message_ids):
            raise ModelValidationError("legacy message ids must be unique")
        encoded_bytes = sum(
            len(message.content.encode("utf-8")) for message in messages
        )
        if encoded_bytes > _MAX_LEGACY_SNAPSHOT_BYTES:
            raise ModelValidationError("legacy messages history exceeds byte limit")
        object.__setattr__(self, "messages", messages)
        if self.task_state is not None and not isinstance(
            self.task_state, LegacyTaskStateDescriptor
        ):
            raise TypeError("task_state must be a LegacyTaskStateDescriptor or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "unchain.legacy_bootstrap_payload.v1",
            "owner_chat_id": self.owner_chat_id,
            "source_revision": self.source_revision,
            "messages": [message.to_dict() for message in self.messages],
            "generation": self.generation.to_dict(),
            "preflight": self.preflight.to_dict(),
            "task_state": (
                self.task_state.to_record() if self.task_state is not None else None
            ),
        }


def build_legacy_bootstrap_operation(
    *,
    operation_id: str,
    payload: LegacyBootstrapPayload,
) -> OperationRef:
    """Bind a caller operation id to the complete canonical legacy snapshot."""

    if not isinstance(payload, LegacyBootstrapPayload):
        raise TypeError("payload must be a LegacyBootstrapPayload")
    draft = SemanticEventDraft(
        event_id="legacy-bootstrap-operation-binding",
        event_type="legacy.bootstrap.binding",
        attempt=payload.generation.attempt,
        operation_id=operation_id,
        payload={"legacy_bootstrap": payload.to_dict()},
    )
    return OperationRef(draft.operation.operation_id, draft.operation.payload_sha256)


@dataclass(frozen=True)
class LegacyBootstrapRequest:
    payload: LegacyBootstrapPayload
    operation: OperationRef

    def __post_init__(self) -> None:
        if not isinstance(self.payload, LegacyBootstrapPayload):
            raise TypeError("payload must be a LegacyBootstrapPayload")
        if not isinstance(self.operation, OperationRef):
            object.__setattr__(
                self,
                "operation",
                OperationRef.from_dict(self.operation),
            )


@dataclass(frozen=True)
class LegacyBootstrapReceipt:
    """Completed durable import receipt; never represents a partial write."""

    owner_chat_id: str
    source_revision: str
    session_id: str
    execution_id: str
    generation_id: str
    attempt_id: str
    manifest_sha256: str
    message_count: int
    first_cursor: EventCursor
    last_cursor: EventCursor
    task_state: LegacyTaskStateDescriptor | None = None
    capture_status: str = _CAPTURE_STATUS
    ready_for_sticky_v2: bool = True
    duplicate: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "owner_chat_id",
            "source_revision",
            "session_id",
            "execution_id",
            "generation_id",
            "attempt_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name, identifier=True),
            )
        object.__setattr__(
            self,
            "manifest_sha256",
            _sha256(self.manifest_sha256, "manifest_sha256"),
        )
        object.__setattr__(
            self,
            "message_count",
            _bounded_int(self.message_count, "message_count", minimum=1),
        )
        if not isinstance(self.first_cursor, EventCursor):
            object.__setattr__(
                self,
                "first_cursor",
                EventCursor.from_dict(self.first_cursor),
            )
        if not isinstance(self.last_cursor, EventCursor):
            object.__setattr__(
                self,
                "last_cursor",
                EventCursor.from_dict(self.last_cursor),
            )
        if self.first_cursor.store_seq > self.last_cursor.store_seq:
            raise ModelValidationError("legacy bootstrap cursor range is invalid")
        if self.task_state is not None and not isinstance(
            self.task_state, LegacyTaskStateDescriptor
        ):
            raise TypeError("task_state must be a LegacyTaskStateDescriptor or None")
        if self.capture_status != _CAPTURE_STATUS:
            raise ModelValidationError("legacy bootstrap capture status is invalid")
        if type(self.ready_for_sticky_v2) is not bool or not self.ready_for_sticky_v2:
            raise ModelValidationError(
                "completed bootstrap receipt must be sticky-ready"
            )
        if type(self.duplicate) is not bool:
            raise TypeError("duplicate must be a boolean")


class SQLiteLegacyBootstrapService:
    """Own the all-or-nothing legacy import transaction in the V2 data plane."""

    _SCHEMA_VERSION: ClassVar[int] = 1

    def __init__(self, store: SQLiteContextV2Store) -> None:
        if not isinstance(store, SQLiteContextV2Store):
            raise TypeError("store must be a SQLiteContextV2Store")
        self._store = store
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._store.database_path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _transaction(self, *, immediate: bool) -> Iterator[sqlite3.Connection]:
        with serialized_context_v2_database_access(self._store.database_path):
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

    def _initialize(self) -> None:
        try:
            with self._transaction(immediate=True) as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS legacy_bootstrap_schema (
                        version INTEGER PRIMARY KEY,
                        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT OR IGNORE INTO legacy_bootstrap_schema(version) VALUES (1);

                    CREATE TABLE IF NOT EXISTS legacy_bootstrap_manifests (
                        owner_chat_id TEXT NOT NULL,
                        source_revision TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        execution_id TEXT NOT NULL,
                        generation_id TEXT NOT NULL,
                        attempt_id TEXT NOT NULL,
                        operation_id TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        manifest_json BLOB NOT NULL,
                        manifest_sha256 TEXT NOT NULL,
                        first_store_seq INTEGER NOT NULL CHECK(first_store_seq >= 1),
                        last_store_seq INTEGER NOT NULL CHECK(last_store_seq >= first_store_seq),
                        event_count INTEGER NOT NULL CHECK(event_count >= 1),
                        PRIMARY KEY (owner_chat_id, generation_id),
                        UNIQUE (owner_chat_id, source_revision),
                        UNIQUE (execution_id, generation_id),
                        FOREIGN KEY (execution_id) REFERENCES executions(execution_id),
                        FOREIGN KEY (execution_id, operation_id)
                            REFERENCES operations(execution_id, operation_id)
                    );

                    CREATE TABLE IF NOT EXISTS legacy_bootstrap_chat_heads (
                        owner_chat_id TEXT PRIMARY KEY,
                        execution_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        current_generation_id TEXT NOT NULL,
                        current_source_revision TEXT NOT NULL,
                        head_revision INTEGER NOT NULL CHECK(head_revision >= 1),
                        FOREIGN KEY (owner_chat_id, current_generation_id)
                            REFERENCES legacy_bootstrap_manifests(
                                owner_chat_id,
                                generation_id
                            )
                    );
                    """
                )
                versions = {
                    int(row[0])
                    for row in connection.execute(
                        "SELECT version FROM legacy_bootstrap_schema"
                    )
                }
                if versions != {self._SCHEMA_VERSION}:
                    raise LegacyBootstrapUnavailable(
                        "legacy bootstrap SQLite schema is unsupported"
                    )
        except sqlite3.Error as exc:
            raise LegacyBootstrapUnavailable(
                "legacy bootstrap SQLite schema initialization failed"
            ) from exc

    @staticmethod
    def _target_key(payload: LegacyBootstrapPayload) -> str:
        return f"{payload.owner_chat_id}:{payload.generation.generation_id}"

    @staticmethod
    def _message_draft(
        payload: LegacyBootstrapPayload,
        message: LegacyMessage,
        index: int,
    ) -> SemanticEventDraft:
        identity = {
            "owner_chat_id": payload.owner_chat_id,
            "source_revision": payload.source_revision,
            "execution_id": payload.generation.execution_id,
            "generation_id": payload.generation.generation_id,
            "attempt_id": payload.generation.attempt_id,
            "message_id": message.message_id,
            "message_index": index,
            "role": message.role,
        }
        identity_sha256 = _digest(identity)
        return SemanticEventDraft(
            event_id=f"legacy-import-{identity_sha256}",
            event_type=f"message.{message.role}",
            attempt=payload.generation.attempt,
            operation_id=f"legacy-import-event-{identity_sha256}",
            payload={
                "run_id": payload.generation.attempt_id,
                "message": {
                    "role": message.role,
                    "content": message.content,
                },
                "legacy_provenance": {
                    "source": "host_sanitized_legacy_snapshot",
                    "capture_status": _CAPTURE_STATUS,
                    "owner_chat_id": payload.owner_chat_id,
                    "session_id": payload.generation.session_id,
                    "source_revision": payload.source_revision,
                    "message_id": message.message_id,
                    "message_index": index,
                },
            },
        )

    @staticmethod
    def _claim_operation(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        operation: OperationRef,
        target_kind: str,
        target_key: str,
    ) -> bool:
        row = connection.execute(
            """
            SELECT payload_sha256, target_kind, target_key
            FROM operations
            WHERE execution_id = ? AND operation_id = ?
            """,
            (execution_id, operation.operation_id),
        ).fetchone()
        if row is not None:
            if (
                row["payload_sha256"] == operation.payload_sha256
                and row["target_kind"] == target_kind
                and row["target_key"] == target_key
            ):
                return False
            raise LegacyBootstrapConflict(
                "legacy bootstrap operation payload or target changed"
            )
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
                execution_id,
                operation.operation_id,
                operation.payload_sha256,
                target_kind,
                target_key,
            ),
        )
        return True

    @staticmethod
    def _manifest_row_for_source(
        connection: sqlite3.Connection,
        *,
        owner_chat_id: str,
        source_revision: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM legacy_bootstrap_manifests
            WHERE owner_chat_id = ? AND source_revision = ?
            """,
            (owner_chat_id, source_revision),
        ).fetchone()

    @staticmethod
    def _manifest_row_for_generation(
        connection: sqlite3.Connection,
        *,
        owner_chat_id: str,
        generation_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM legacy_bootstrap_manifests
            WHERE owner_chat_id = ? AND generation_id = ?
            """,
            (owner_chat_id, generation_id),
        ).fetchone()

    @staticmethod
    def _decode_manifest(row: sqlite3.Row) -> Mapping[str, Any]:
        raw = bytes(row["manifest_json"])
        if hashlib.sha256(raw).hexdigest() != row["manifest_sha256"]:
            raise LegacyBootstrapUnavailable(
                "legacy bootstrap manifest digest changed on disk"
            )
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LegacyBootstrapUnavailable(
                "legacy bootstrap manifest is unreadable"
            ) from exc
        if type(decoded) is not dict or _canonical_bytes(decoded) != raw:
            raise LegacyBootstrapUnavailable(
                "legacy bootstrap manifest is not canonical"
            )
        expected_fields = {
            "schema",
            "owner_chat_id",
            "source_revision",
            "session_id",
            "execution_id",
            "generation_id",
            "attempt_id",
            "capture_status",
            "payload_sha256",
            "primary_operation_id",
            "rebase",
            "preflight_proof_id",
            "task_state",
            "events",
        }
        if set(decoded) != expected_fields or decoded.get("schema") != (
            "unchain.legacy_bootstrap_manifest.v1"
        ):
            raise LegacyBootstrapUnavailable(
                "legacy bootstrap manifest shape changed on disk"
            )
        for field_name in (
            "owner_chat_id",
            "source_revision",
            "session_id",
            "execution_id",
            "generation_id",
            "attempt_id",
            "payload_sha256",
            "primary_operation_id",
        ):
            if (
                decoded[field_name]
                != row[
                    field_name
                    if field_name != "primary_operation_id"
                    else "operation_id"
                ]
            ):
                raise LegacyBootstrapUnavailable(
                    "legacy bootstrap manifest index changed on disk"
                )
        if decoded["capture_status"] != _CAPTURE_STATUS:
            raise LegacyBootstrapUnavailable(
                "legacy bootstrap capture status changed on disk"
            )
        events = decoded["events"]
        if (
            type(events) is not list
            or len(events) != row["event_count"]
            or not events
            or events[0].get("store_seq") != row["first_store_seq"]
            or events[-1].get("store_seq") != row["last_store_seq"]
        ):
            raise LegacyBootstrapUnavailable(
                "legacy bootstrap event range changed on disk"
            )
        return MappingProxyType(decoded)

    @staticmethod
    def _task_state_from_manifest(
        manifest: Mapping[str, Any],
    ) -> LegacyTaskStateDescriptor | None:
        raw = manifest["task_state"]
        if raw is None:
            return None
        return LegacyTaskStateDescriptor.from_dict(raw)

    @classmethod
    def _verify_manifest_events(
        cls,
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        manifest: Mapping[str, Any],
    ) -> None:
        expected_events = manifest["events"]
        rows = list(
            connection.execute(
                """
                SELECT * FROM events
                WHERE execution_id = ?
                  AND store_seq BETWEEN ? AND ?
                ORDER BY store_seq
                """,
                (
                    row["execution_id"],
                    row["first_store_seq"],
                    row["last_store_seq"],
                ),
            )
        )
        if len(rows) != row["event_count"] or len(rows) != len(expected_events):
            raise LegacyBootstrapUnavailable(
                "legacy bootstrap canonical event range is incomplete"
            )
        for event_row, expected in zip(rows, expected_events, strict=True):
            event_raw = bytes(event_row["event_json"])
            if hashlib.sha256(event_raw).hexdigest() != event_row["event_sha256"]:
                raise LegacyBootstrapUnavailable(
                    "legacy bootstrap event digest changed on disk"
                )
            try:
                event = JournalEvent.from_dict(json.loads(event_raw.decode("utf-8")))
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ) as exc:
                raise LegacyBootstrapUnavailable(
                    "legacy bootstrap event is unreadable"
                ) from exc
            if _canonical_bytes(event.to_dict()) != event_raw:
                raise LegacyBootstrapUnavailable(
                    "legacy bootstrap event is not canonical"
                )
            operation = connection.execute(
                """
                SELECT payload_sha256, target_kind, target_key
                FROM operations
                WHERE execution_id = ? AND operation_id = ?
                """,
                (row["execution_id"], event.operation.operation_id),
            ).fetchone()
            if (
                event.event_type not in {"message.user", "message.assistant"}
                or event.attempt.generation.execution_id != row["execution_id"]
                or event.attempt.generation.generation_id != row["generation_id"]
                or event.attempt.attempt_id != row["attempt_id"]
                or event.event_id != expected.get("event_id")
                or event.store_seq != expected.get("store_seq")
                or event_row["event_sha256"] != expected.get("event_sha256")
                or event.payload.get("message", {}).get("role") != expected.get("role")
                or event.payload.get("legacy_provenance", {}).get("message_id")
                != expected.get("message_id")
                or operation is None
                or operation["payload_sha256"] != event.operation.payload_sha256
                or operation["target_kind"] != "journal_event"
                or operation["target_key"] != event.event_id
            ):
                raise LegacyBootstrapUnavailable(
                    "legacy bootstrap canonical event binding changed on disk"
                )

    @classmethod
    def _receipt_from_row(
        cls,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        duplicate: bool,
    ) -> LegacyBootstrapReceipt:
        manifest = cls._decode_manifest(row)
        operation = connection.execute(
            """
            SELECT payload_sha256, target_kind, target_key
            FROM operations
            WHERE execution_id = ? AND operation_id = ?
            """,
            (row["execution_id"], row["operation_id"]),
        ).fetchone()
        if (
            operation is None
            or operation["payload_sha256"] != row["payload_sha256"]
            or operation["target_kind"] != "legacy_bootstrap_manifest"
            or operation["target_key"]
            != f'{row["owner_chat_id"]}:{row["generation_id"]}'
        ):
            raise LegacyBootstrapUnavailable(
                "legacy bootstrap operation binding changed on disk"
            )
        cls._verify_manifest_events(
            connection,
            row=row,
            manifest=manifest,
        )
        first = manifest["events"][0]
        last = manifest["events"][-1]
        return LegacyBootstrapReceipt(
            owner_chat_id=row["owner_chat_id"],
            source_revision=row["source_revision"],
            session_id=row["session_id"],
            execution_id=row["execution_id"],
            generation_id=row["generation_id"],
            attempt_id=row["attempt_id"],
            manifest_sha256=row["manifest_sha256"],
            message_count=row["event_count"],
            first_cursor=EventCursor(first["store_seq"], first["event_id"]),
            last_cursor=EventCursor(last["store_seq"], last["event_id"]),
            task_state=cls._task_state_from_manifest(manifest),
            duplicate=duplicate,
        )

    @staticmethod
    def _validate_preflight(payload: LegacyBootstrapPayload) -> None:
        proof = payload.preflight
        if not proof.host_snapshot_sanitized:
            raise LegacyBootstrapUnavailable(
                "legacy bootstrap preflight requires a host-sanitized snapshot"
            )
        if (
            not proof.no_unfinished_durable_checkpoint
            or not proof.no_pending_interaction
        ):
            raise LegacyBootstrapUnavailable(
                "legacy bootstrap preflight found unfinished durable state"
            )

    def bootstrap(self, request: LegacyBootstrapRequest) -> LegacyBootstrapReceipt:
        """Import one initial snapshot or explicit rebase, then return sticky authority."""

        if not isinstance(request, LegacyBootstrapRequest):
            raise TypeError("request must be a LegacyBootstrapRequest")
        payload = request.payload
        expected_operation = build_legacy_bootstrap_operation(
            operation_id=request.operation.operation_id,
            payload=payload,
        )
        if expected_operation != request.operation:
            raise LegacyBootstrapConflict(
                "legacy bootstrap operation payload hash changed"
            )
        self._validate_preflight(payload)
        generation = payload.generation
        target_key = self._target_key(payload)
        try:
            with self._transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO executions(execution_id) VALUES (?)",
                    (generation.execution_id,),
                )

                execution_owners = {
                    row["owner_chat_id"]
                    for row in connection.execute(
                        """
                        SELECT DISTINCT owner_chat_id
                        FROM legacy_bootstrap_manifests
                        WHERE execution_id = ?
                        """,
                        (generation.execution_id,),
                    )
                }
                if execution_owners and execution_owners != {payload.owner_chat_id}:
                    raise LegacyBootstrapConflict(
                        "legacy execution scope belongs to another owner"
                    )

                source_row = self._manifest_row_for_source(
                    connection,
                    owner_chat_id=payload.owner_chat_id,
                    source_revision=payload.source_revision,
                )
                if source_row is not None:
                    if source_row["payload_sha256"] != request.operation.payload_sha256:
                        raise LegacyBootstrapConflict(
                            "legacy source revision payload changed"
                        )
                    current_head = connection.execute(
                        """
                        SELECT current_generation_id, current_source_revision
                        FROM legacy_bootstrap_chat_heads
                        WHERE owner_chat_id = ?
                        """,
                        (payload.owner_chat_id,),
                    ).fetchone()
                    if (
                        current_head is None
                        or current_head["current_generation_id"]
                        != source_row["generation_id"]
                        or current_head["current_source_revision"]
                        != source_row["source_revision"]
                    ):
                        raise LegacyBootstrapConflict(
                            "legacy source revision is no longer current"
                        )
                    self._claim_operation(
                        connection,
                        execution_id=generation.execution_id,
                        operation=request.operation,
                        target_kind="legacy_bootstrap_manifest",
                        target_key=target_key,
                    )
                    return replace(
                        self._receipt_from_row(
                            connection,
                            source_row,
                            duplicate=False,
                        ),
                        duplicate=True,
                    )

                head = connection.execute(
                    """
                    SELECT * FROM legacy_bootstrap_chat_heads
                    WHERE owner_chat_id = ?
                    """,
                    (payload.owner_chat_id,),
                ).fetchone()
                if generation.rebase_kind is LegacyRebaseKind.INITIAL:
                    if head is not None:
                        raise LegacyBootstrapConflict(
                            "legacy chat already has a current generation; explicit rebase required"
                        )
                else:
                    if (
                        head is None
                        or head["execution_id"] != generation.execution_id
                        or head["current_generation_id"]
                        != generation.previous_generation_id
                    ):
                        raise LegacyBootstrapConflict(
                            "legacy rebase previous generation is not current"
                        )

                if (
                    self._manifest_row_for_generation(
                        connection,
                        owner_chat_id=payload.owner_chat_id,
                        generation_id=generation.generation_id,
                    )
                    is not None
                ):
                    raise LegacyBootstrapConflict(
                        "legacy generation belongs to another source revision"
                    )
                foreign_generation = connection.execute(
                    """
                    SELECT 1 FROM legacy_bootstrap_manifests
                    WHERE execution_id = ? AND generation_id = ?
                    """,
                    (generation.execution_id, generation.generation_id),
                ).fetchone()
                if foreign_generation is not None:
                    raise LegacyBootstrapConflict(
                        "legacy generation belongs to another owner"
                    )

                self._claim_operation(
                    connection,
                    execution_id=generation.execution_id,
                    operation=request.operation,
                    target_kind="legacy_bootstrap_manifest",
                    target_key=target_key,
                )
                execution_head = connection.execute(
                    """
                    SELECT next_store_seq FROM executions WHERE execution_id = ?
                    """,
                    (generation.execution_id,),
                ).fetchone()
                if execution_head is None:
                    raise LegacyBootstrapUnavailable(
                        "legacy bootstrap journal head is unavailable"
                    )
                next_store_seq = int(execution_head["next_store_seq"])
                persisted_events: list[JournalEvent] = []
                manifest_events: list[dict[str, Any]] = []
                for index, message in enumerate(payload.messages):
                    draft = self._message_draft(payload, message, index)
                    event = JournalEvent(
                        event_id=draft.event_id,
                        event_type=draft.event_type,
                        attempt=draft.attempt,
                        operation=draft.operation,
                        store_seq=next_store_seq + index,
                        payload=draft.payload,
                        resource_refs=draft.resource_refs,
                    )
                    self._claim_operation(
                        connection,
                        execution_id=generation.execution_id,
                        operation=event.operation,
                        target_kind="journal_event",
                        target_key=event.event_id,
                    )
                    event_json = _canonical_bytes(event.to_dict())
                    event_sha256 = hashlib.sha256(event_json).hexdigest()
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
                            generation.execution_id,
                            event.store_seq,
                            event.event_id,
                            generation.generation_id,
                            generation.attempt_id,
                            event.event_type,
                            event.operation.operation_id,
                            event_json,
                            event_sha256,
                        ),
                    )
                    persisted_events.append(event)
                    manifest_events.append(
                        {
                            "message_id": message.message_id,
                            "role": message.role,
                            "event_id": event.event_id,
                            "store_seq": event.store_seq,
                            "event_sha256": event_sha256,
                        }
                    )

                advanced = connection.execute(
                    """
                    UPDATE executions SET next_store_seq = ?
                    WHERE execution_id = ? AND next_store_seq = ?
                    """,
                    (
                        next_store_seq + len(persisted_events),
                        generation.execution_id,
                        next_store_seq,
                    ),
                )
                if advanced.rowcount != 1:
                    raise LegacyBootstrapConflict(
                        "legacy bootstrap journal head changed"
                    )

                manifest = {
                    "schema": "unchain.legacy_bootstrap_manifest.v1",
                    "owner_chat_id": payload.owner_chat_id,
                    "source_revision": payload.source_revision,
                    "session_id": generation.session_id,
                    "execution_id": generation.execution_id,
                    "generation_id": generation.generation_id,
                    "attempt_id": generation.attempt_id,
                    "capture_status": _CAPTURE_STATUS,
                    "payload_sha256": request.operation.payload_sha256,
                    "primary_operation_id": request.operation.operation_id,
                    "rebase": {
                        "kind": generation.rebase_kind.value,
                        "previous_generation_id": generation.previous_generation_id,
                    },
                    "preflight_proof_id": payload.preflight.proof_id,
                    "task_state": (
                        payload.task_state.to_record()
                        if payload.task_state is not None
                        else None
                    ),
                    "events": manifest_events,
                }
                manifest_json = _canonical_bytes(manifest)
                manifest_sha256 = hashlib.sha256(manifest_json).hexdigest()
                connection.execute(
                    """
                    INSERT INTO legacy_bootstrap_manifests(
                        owner_chat_id,
                        source_revision,
                        session_id,
                        execution_id,
                        generation_id,
                        attempt_id,
                        operation_id,
                        payload_sha256,
                        manifest_json,
                        manifest_sha256,
                        first_store_seq,
                        last_store_seq,
                        event_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload.owner_chat_id,
                        payload.source_revision,
                        generation.session_id,
                        generation.execution_id,
                        generation.generation_id,
                        generation.attempt_id,
                        request.operation.operation_id,
                        request.operation.payload_sha256,
                        manifest_json,
                        manifest_sha256,
                        persisted_events[0].store_seq,
                        persisted_events[-1].store_seq,
                        len(persisted_events),
                    ),
                )

                if head is None:
                    connection.execute(
                        """
                        INSERT INTO legacy_bootstrap_chat_heads(
                            owner_chat_id,
                            execution_id,
                            session_id,
                            current_generation_id,
                            current_source_revision,
                            head_revision
                        ) VALUES (?, ?, ?, ?, ?, 1)
                        """,
                        (
                            payload.owner_chat_id,
                            generation.execution_id,
                            generation.session_id,
                            generation.generation_id,
                            payload.source_revision,
                        ),
                    )
                else:
                    updated = connection.execute(
                        """
                        UPDATE legacy_bootstrap_chat_heads
                        SET session_id = ?,
                            current_generation_id = ?,
                            current_source_revision = ?,
                            head_revision = head_revision + 1
                        WHERE owner_chat_id = ?
                          AND execution_id = ?
                          AND current_generation_id = ?
                          AND head_revision = ?
                        """,
                        (
                            generation.session_id,
                            generation.generation_id,
                            payload.source_revision,
                            payload.owner_chat_id,
                            generation.execution_id,
                            generation.previous_generation_id,
                            head["head_revision"],
                        ),
                    )
                    if updated.rowcount != 1:
                        raise LegacyBootstrapConflict(
                            "legacy rebase current generation changed"
                        )

                row = self._manifest_row_for_generation(
                    connection,
                    owner_chat_id=payload.owner_chat_id,
                    generation_id=generation.generation_id,
                )
                if row is None:
                    raise LegacyBootstrapUnavailable(
                        "legacy bootstrap manifest was not persisted"
                    )
                return self._receipt_from_row(
                    connection,
                    row,
                    duplicate=False,
                )
        except LegacyBootstrapError:
            raise
        except sqlite3.Error as exc:
            raise LegacyBootstrapUnavailable(
                "legacy bootstrap SQLite persistence failed"
            ) from exc

    def current(self, owner_chat_id: str) -> LegacyBootstrapReceipt | None:
        normalized_owner = _required_text(
            owner_chat_id,
            "owner_chat_id",
            identifier=True,
        )
        try:
            with self._transaction(immediate=False) as connection:
                row = connection.execute(
                    """
                    SELECT manifest.*
                    FROM legacy_bootstrap_chat_heads AS head
                    JOIN legacy_bootstrap_manifests AS manifest
                      ON manifest.owner_chat_id = head.owner_chat_id
                     AND manifest.generation_id = head.current_generation_id
                    WHERE head.owner_chat_id = ?
                    """,
                    (normalized_owner,),
                ).fetchone()
                if row is None:
                    return None
                return self._receipt_from_row(
                    connection,
                    row,
                    duplicate=False,
                )
        except LegacyBootstrapError:
            raise
        except sqlite3.Error as exc:
            raise LegacyBootstrapUnavailable(
                "legacy bootstrap current-generation read failed"
            ) from exc

    def receipt_for_generation(
        self,
        owner_chat_id: str,
        generation_id: str,
    ) -> LegacyBootstrapReceipt | None:
        normalized_owner = _required_text(
            owner_chat_id,
            "owner_chat_id",
            identifier=True,
        )
        normalized_generation = _required_text(
            generation_id,
            "generation_id",
            identifier=True,
        )
        try:
            with self._transaction(immediate=False) as connection:
                row = self._manifest_row_for_generation(
                    connection,
                    owner_chat_id=normalized_owner,
                    generation_id=normalized_generation,
                )
                if row is None:
                    return None
                return self._receipt_from_row(
                    connection,
                    row,
                    duplicate=False,
                )
        except LegacyBootstrapError:
            raise
        except sqlite3.Error as exc:
            raise LegacyBootstrapUnavailable(
                "legacy bootstrap generation read failed"
            ) from exc


__all__ = [
    "LegacyBootstrapConflict",
    "LegacyBootstrapError",
    "LegacyBootstrapPayload",
    "LegacyBootstrapPreflight",
    "LegacyBootstrapReceipt",
    "LegacyBootstrapRequest",
    "LegacyBootstrapUnavailable",
    "LegacyGenerationDescriptor",
    "LegacyMessage",
    "LegacyRebaseKind",
    "LegacyTaskStateDescriptor",
    "SQLiteLegacyBootstrapService",
    "build_legacy_bootstrap_operation",
]
