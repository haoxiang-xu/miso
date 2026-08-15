"""Atomic generation rebase over the canonical Context V2 SQLite plane.

This service is the only writer in this module.  One ``BEGIN IMMEDIATE`` owns
the host generation lifecycle CAS, imported canonical journal events and
operations, the bootstrap/rebase manifest and head, and the initial attempt
binding.  It never composes the independently transactional legacy bootstrap
or host-generation services.
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
    ArtifactRef,
    AttemptRef,
    EventCursor,
    EventRange,
    GenerationRef,
    OperationRef,
    ResourceRef,
    SemanticEventDraft,
)
from unchain.journal.models import (
    JournalEvent,
    ModelValidationError,
    _bounded_int,
    _record_tuple,
    _required_text,
    _sha256,
)
from unchain.journal.interaction_resolution_compat import (
    InteractionResolutionCompatibilityError,
    interaction_resolution_compatibility_record,
    legacy_interaction_resolution_supersessions,
)
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store


_MAX_MESSAGES = 10_000
_MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
_LEGACY_CAPTURE_STATUS = "legacy_partial"
_INTERACTION_REQUEST_EVENT_TYPES = frozenset(
    {"interaction.requested", "interaction_requested"}
)
_INTERACTION_RESOLUTION_EVENT_TYPES = frozenset(
    {"interaction.resolved", "interaction_resolved"}
)
_ATTEMPT_TERMINAL_EVENT_TYPES = frozenset(
    {
        "run_completed",
        "run_failed",
        "run_cancelled",
        "run_canceled",
        "run_aborted",
        "run.completed",
        "run.failed",
        "run.cancelled",
        "run.canceled",
        "run.aborted",
    }
)
_TOOL_INTENT_EVENT_TYPES = frozenset({"tool_call"})
_TOOL_STARTED_EVENT_TYPES = frozenset({"tool.started"})
_TOOL_SEALED_EVENT_TYPES = frozenset({"tool.subagent_completion.sealed"})
_TOOL_RESULT_EVENT_TYPES = frozenset({"tool.result", "tool_result"})
_TOOL_LIFECYCLE_EVENT_TYPES = (
    _TOOL_INTENT_EVENT_TYPES
    | _TOOL_STARTED_EVENT_TYPES
    | _TOOL_SEALED_EVENT_TYPES
    | _TOOL_RESULT_EVENT_TYPES
)


class GenerationRebaseError(RuntimeError):
    """Base failure at the atomic generation-rebase boundary."""


class GenerationRebaseConflict(GenerationRebaseError):
    """A generation identity, CAS precondition, or operation payload drifted."""


class GenerationRebaseUnavailable(GenerationRebaseError):
    """The atomic durable rebase could not be completed or verified."""


class GenerationRebasePreflightBlocked(GenerationRebaseConflict):
    """Current durable state does not permit a generation cutover."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeError) as error:
        raise ModelValidationError(
            "generation rebase value is not canonical JSON"
        ) from error


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _exact_bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{field_name} must be a boolean")
    return value


class GenerationRebaseKind(StrEnum):
    """Host-selected generation transition."""

    CREATE = "create"
    EDIT = "edit"
    REGENERATE = "regenerate"
    RETRY = "retry"


@dataclass(frozen=True)
class GenerationSnapshotMessage:
    """One host-sanitized message imported into the new generation."""

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
                "generation snapshot role must be exactly user or assistant"
            )
        if (
            type(self.content) is not str
            or not self.content.strip()
            or "\x00" in self.content
        ):
            raise ModelValidationError(
                "generation snapshot content must be non-empty text"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
        }


@dataclass(frozen=True)
class GenerationRebasePreflight:
    """Host proof for the one fact SQLite cannot derive: snapshot sanitation.

    Interaction and checkpoint clearance are verified from the canonical data
    plane inside the same ``BEGIN IMMEDIATE`` transaction that advances the
    generation head.  They are deliberately not host-supplied booleans.
    """

    proof_id: str
    host_snapshot_sanitized: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "proof_id",
            _required_text(self.proof_id, "proof_id", identifier=True),
        )
        object.__setattr__(
            self,
            "host_snapshot_sanitized",
            _exact_bool(
                self.host_snapshot_sanitized,
                "host_snapshot_sanitized",
            ),
        )

    @property
    def permits_rebase(self) -> bool:
        return self.host_snapshot_sanitized

    def to_dict(self) -> dict[str, Any]:
        return {
            "proof_id": self.proof_id,
            "host_snapshot_sanitized": self.host_snapshot_sanitized,
        }


@dataclass(frozen=True)
class GenerationTaskStateDescriptor:
    """Content-free descriptor for the task state accompanying a snapshot."""

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

    def to_record(self) -> dict[str, Any]:
        return {
            "schema": "unchain.legacy_task_state_descriptor.v1",
            "descriptor_id": self.descriptor_id,
            "revision": self.revision,
            "descriptor_sha256": self.descriptor_sha256,
            "refs": [ref.to_dict() for ref in self.refs],
        }

    @classmethod
    def from_record(
        cls,
        value: Mapping[str, Any],
    ) -> GenerationTaskStateDescriptor:
        raw = dict(value)
        if raw.pop("schema", None) != "unchain.legacy_task_state_descriptor.v1":
            raise GenerationRebaseUnavailable(
                "generation task-state descriptor schema changed"
            )
        if set(raw) != {
            "descriptor_id",
            "revision",
            "descriptor_sha256",
            "refs",
        }:
            raise GenerationRebaseUnavailable(
                "generation task-state descriptor shape changed"
            )
        try:
            return cls(
                descriptor_id=raw["descriptor_id"],
                revision=raw["revision"],
                descriptor_sha256=raw["descriptor_sha256"],
                refs=tuple(ResourceRef.from_dict(ref) for ref in raw["refs"]),
            )
        except (TypeError, ValueError) as error:
            raise GenerationRebaseUnavailable(
                "generation task-state descriptor is invalid"
            ) from error


@dataclass(frozen=True)
class GenerationRebaseIntent:
    """Complete host-owned snapshot and exact head transition."""

    owner_chat_id: str
    session_id: str
    execution_id: str
    generation_id: str
    attempt_id: str
    kind: GenerationRebaseKind
    previous_generation_id: str
    expected_head_revision: int
    source_revision: str
    messages: tuple[GenerationSnapshotMessage, ...]
    preflight: GenerationRebasePreflight
    task_state: GenerationTaskStateDescriptor | None = None

    SCHEMA: ClassVar[str] = "unchain.generation_rebase_intent.v1"

    def __post_init__(self) -> None:
        for field_name in (
            "owner_chat_id",
            "session_id",
            "execution_id",
            "generation_id",
            "attempt_id",
            "source_revision",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name, identifier=True),
            )
        try:
            kind = GenerationRebaseKind(self.kind)
        except (TypeError, ValueError) as error:
            raise ModelValidationError("generation rebase kind is invalid") from error
        object.__setattr__(self, "kind", kind)
        revision = _bounded_int(
            self.expected_head_revision,
            "expected_head_revision",
        )
        object.__setattr__(self, "expected_head_revision", revision)
        if kind is GenerationRebaseKind.CREATE:
            if self.previous_generation_id not in (None, ""):
                raise ModelValidationError(
                    "generation create cannot name a previous generation"
                )
            if revision != 0:
                raise ModelValidationError(
                    "generation create requires expected head revision zero"
                )
            object.__setattr__(self, "previous_generation_id", "")
        else:
            previous = _required_text(
                self.previous_generation_id,
                "previous_generation_id",
                identifier=True,
            )
            if previous == self.generation_id:
                raise ModelValidationError(
                    "generation rebase must create a new generation"
                )
            if revision < 1:
                raise ModelValidationError(
                    "generation rebase requires a positive head revision"
                )
            object.__setattr__(self, "previous_generation_id", previous)
        messages = tuple(self.messages)
        if len(messages) > _MAX_MESSAGES:
            raise ModelValidationError(
                "generation snapshot cannot contain more than 10000 messages"
            )
        if any(not isinstance(item, GenerationSnapshotMessage) for item in messages):
            raise TypeError(
                "generation snapshot messages must be GenerationSnapshotMessage records"
            )
        message_ids = [item.message_id for item in messages]
        if len(message_ids) != len(set(message_ids)):
            raise ModelValidationError(
                "generation snapshot message IDs must be unique"
            )
        if sum(len(item.content.encode("utf-8")) for item in messages) > (
            _MAX_SNAPSHOT_BYTES
        ):
            raise ModelValidationError("generation snapshot exceeds the byte limit")
        object.__setattr__(self, "messages", messages)
        if not isinstance(self.preflight, GenerationRebasePreflight):
            raise TypeError("preflight must be a GenerationRebasePreflight")
        if self.task_state is not None and not isinstance(
            self.task_state,
            GenerationTaskStateDescriptor,
        ):
            raise TypeError(
                "task_state must be a GenerationTaskStateDescriptor or None"
            )

    @property
    def attempt(self) -> AttemptRef:
        return AttemptRef(
            GenerationRef(self.execution_id, self.generation_id),
            self.attempt_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "owner_chat_id": self.owner_chat_id,
            "session_id": self.session_id,
            "execution_id": self.execution_id,
            "generation_id": self.generation_id,
            "attempt_id": self.attempt_id,
            "kind": self.kind.value,
            "previous_generation_id": self.previous_generation_id,
            "expected_head_revision": self.expected_head_revision,
            "source_revision": self.source_revision,
            "messages": [item.to_dict() for item in self.messages],
            "preflight": self.preflight.to_dict(),
            "task_state": (
                self.task_state.to_record() if self.task_state is not None else None
            ),
        }


def build_generation_rebase_operation(
    *,
    operation_id: str,
    intent: GenerationRebaseIntent,
) -> OperationRef:
    """Bind one operation ID to the complete rebase snapshot and CAS input."""

    if not isinstance(intent, GenerationRebaseIntent):
        raise TypeError("intent must be a GenerationRebaseIntent")
    return OperationRef(operation_id, _digest(intent.to_dict()))


@dataclass(frozen=True)
class GenerationRebaseRequest:
    intent: GenerationRebaseIntent
    operation: OperationRef

    def __post_init__(self) -> None:
        if not isinstance(self.intent, GenerationRebaseIntent):
            raise TypeError("intent must be a GenerationRebaseIntent")
        if not isinstance(self.operation, OperationRef):
            object.__setattr__(
                self,
                "operation",
                OperationRef.from_dict(self.operation),
            )


@dataclass(frozen=True)
class GenerationRebaseHead:
    owner_chat_id: str
    session_id: str
    execution_id: str
    current_generation_id: str
    current_attempt_id: str
    current_source_revision: str
    revision: int


@dataclass(frozen=True)
class GenerationRebaseReceipt:
    owner_chat_id: str
    session_id: str
    execution_id: str
    generation_id: str
    attempt_id: str
    kind: GenerationRebaseKind
    previous_generation_id: str
    source_revision: str
    head_revision: int
    manifest_sha256: str
    message_count: int
    first_cursor: EventCursor
    last_cursor: EventCursor
    operation: OperationRef
    lifecycle_operation: OperationRef
    attempt_binding_operation: OperationRef
    task_state: GenerationTaskStateDescriptor | None = None
    duplicate: bool = False


def _derived_operation_id(prefix: str, operation: OperationRef, subject: str) -> str:
    digest = hashlib.sha256(
        "\0".join(
            (operation.operation_id, operation.payload_sha256, subject)
        ).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest}"


def _checkpoint_identity(execution_id: str, operation: OperationRef) -> str:
    return hashlib.sha256(
        (
            "unchain.context_checkpoint.v1\0"
            + execution_id
            + "\0"
            + operation.operation_id
            + "\0"
            + operation.payload_sha256
        ).encode("utf-8")
    ).hexdigest()


def _host_transition_payload(intent: GenerationRebaseIntent) -> dict[str, Any]:
    compatible_kind = {
        GenerationRebaseKind.CREATE: "initial",
        GenerationRebaseKind.EDIT: "edit",
        GenerationRebaseKind.REGENERATE: "regenerate",
        GenerationRebaseKind.RETRY: "regenerate",
    }[intent.kind]
    return {
        "schema": "unchain.host_generation_transition.v1",
        "owner_chat_id": intent.owner_chat_id,
        "execution_id": intent.execution_id,
        "session_id": intent.session_id,
        "generation_id": intent.generation_id,
        "kind": compatible_kind,
        "previous_generation_id": intent.previous_generation_id,
        "expected_revision": intent.expected_head_revision,
    }


def _host_attempt_payload(
    intent: GenerationRebaseIntent,
    *,
    head_revision: int,
) -> dict[str, Any]:
    return {
        "schema": "unchain.host_generation_attempt_binding_intent.v1",
        "owner_chat_id": intent.owner_chat_id,
        "execution_id": intent.execution_id,
        "session_id": intent.session_id,
        "generation_id": intent.generation_id,
        "attempt_id": intent.attempt_id,
        "expected_revision": head_revision,
    }


class SQLiteGenerationRebaseV2Service:
    """Own the all-or-nothing generation rebase transaction."""

    _SCHEMA_VERSION: ClassVar[int] = 1

    def __init__(self, store: SQLiteContextV2Store) -> None:
        if type(store) is not SQLiteContextV2Store:
            raise TypeError("store must be the official SQLiteContextV2Store")
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
        connection = self._connect()
        try:
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).casefold() != "wal":
                raise GenerationRebaseUnavailable(
                    "generation rebase SQLite WAL mode is unavailable"
                )
            connection.executescript(
                """
                BEGIN IMMEDIATE;

                CREATE TABLE IF NOT EXISTS generation_rebase_v2_schema (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT OR IGNORE INTO generation_rebase_v2_schema(version)
                VALUES (1);

                CREATE TABLE IF NOT EXISTS host_generation_schema (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT OR IGNORE INTO host_generation_schema(version) VALUES (1);

                CREATE TABLE IF NOT EXISTS host_generation_chat_bindings (
                    owner_chat_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    UNIQUE(owner_chat_id, execution_id, session_id),
                    FOREIGN KEY (execution_id) REFERENCES executions(execution_id)
                );

                CREATE TABLE IF NOT EXISTS host_generation_records (
                    owner_chat_id TEXT NOT NULL,
                    execution_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    transition_kind TEXT NOT NULL CHECK(
                        transition_kind IN ('initial', 'edit', 'regenerate')
                    ),
                    previous_generation_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    operation_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (owner_chat_id, generation_id),
                    UNIQUE(execution_id, generation_id),
                    UNIQUE(owner_chat_id, revision),
                    FOREIGN KEY (owner_chat_id, execution_id, session_id)
                        REFERENCES host_generation_chat_bindings(
                            owner_chat_id, execution_id, session_id
                        )
                );

                CREATE TABLE IF NOT EXISTS host_generation_heads (
                    owner_chat_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    current_generation_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    FOREIGN KEY (owner_chat_id, execution_id, session_id)
                        REFERENCES host_generation_chat_bindings(
                            owner_chat_id, execution_id, session_id
                        ),
                    FOREIGN KEY (owner_chat_id, current_generation_id)
                        REFERENCES host_generation_records(
                            owner_chat_id, generation_id
                        )
                );

                CREATE TABLE IF NOT EXISTS host_generation_operations (
                    owner_chat_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    mutation_kind TEXT NOT NULL,
                    result_generation_id TEXT NOT NULL,
                    result_revision INTEGER NOT NULL CHECK(result_revision >= 1),
                    result_attempt_id TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (owner_chat_id, operation_id),
                    FOREIGN KEY (owner_chat_id, result_generation_id)
                        REFERENCES host_generation_records(
                            owner_chat_id, generation_id
                        )
                );

                CREATE TABLE IF NOT EXISTS host_generation_attempt_bindings (
                    owner_chat_id TEXT NOT NULL,
                    execution_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    head_revision INTEGER NOT NULL CHECK(head_revision >= 1),
                    operation_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (execution_id, attempt_id),
                    UNIQUE(owner_chat_id, operation_id),
                    FOREIGN KEY (owner_chat_id, execution_id, session_id)
                        REFERENCES host_generation_chat_bindings(
                            owner_chat_id, execution_id, session_id
                        ),
                    FOREIGN KEY (owner_chat_id, generation_id)
                        REFERENCES host_generation_records(
                            owner_chat_id, generation_id
                        ),
                    FOREIGN KEY (owner_chat_id, operation_id)
                        REFERENCES host_generation_operations(
                            owner_chat_id, operation_id
                        )
                );

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
                    last_store_seq INTEGER NOT NULL CHECK(
                        last_store_seq >= first_store_seq
                    ),
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
                            owner_chat_id, generation_id
                        )
                );

                COMMIT;
                """
            )
            for table in (
                "generation_rebase_v2_schema",
                "host_generation_schema",
                "legacy_bootstrap_schema",
            ):
                versions = {
                    int(row[0])
                    for row in connection.execute(f"SELECT version FROM {table}")
                }
                if versions != {self._SCHEMA_VERSION}:
                    raise GenerationRebaseUnavailable(
                        "generation rebase SQLite schema is unsupported"
                    )
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise GenerationRebaseUnavailable(
                    "generation rebase SQLite quick_check failed"
                )
        except GenerationRebaseError:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error as error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise GenerationRebaseUnavailable(
                "generation rebase SQLite schema initialization failed"
            ) from error
        finally:
            connection.close()

    @staticmethod
    def _target_key(intent: GenerationRebaseIntent) -> str:
        return f"{intent.owner_chat_id}:{intent.generation_id}"

    @staticmethod
    def _message_draft(
        intent: GenerationRebaseIntent,
        message: GenerationSnapshotMessage,
        index: int,
    ) -> SemanticEventDraft:
        identity = {
            "owner_chat_id": intent.owner_chat_id,
            "source_revision": intent.source_revision,
            "execution_id": intent.execution_id,
            "generation_id": intent.generation_id,
            "attempt_id": intent.attempt_id,
            "message_id": message.message_id,
            "message_index": index,
            "role": message.role,
            "kind": intent.kind.value,
        }
        identity_sha256 = _digest(identity)
        return SemanticEventDraft(
            event_id=f"generation-rebase-import-{identity_sha256}",
            event_type=f"message.{message.role}",
            attempt=intent.attempt,
            operation_id=f"generation-rebase-event-{identity_sha256}",
            payload={
                "run_id": intent.attempt_id,
                "message": {
                    "role": message.role,
                    "content": message.content,
                },
                "legacy_provenance": {
                    "source": "host_sanitized_generation_snapshot",
                    "capture_status": _LEGACY_CAPTURE_STATUS,
                    "owner_chat_id": intent.owner_chat_id,
                    "session_id": intent.session_id,
                    "source_revision": intent.source_revision,
                    "message_id": message.message_id,
                    "message_index": index,
                },
                "generation_rebase": {
                    "kind": intent.kind.value,
                    "previous_generation_id": intent.previous_generation_id,
                    "expected_head_revision": intent.expected_head_revision,
                },
            },
        )

    @staticmethod
    def _marker_draft(intent: GenerationRebaseIntent) -> SemanticEventDraft:
        """Persist an empty visible snapshot without inventing a chat message."""

        identity = {
            "owner_chat_id": intent.owner_chat_id,
            "session_id": intent.session_id,
            "source_revision": intent.source_revision,
            "execution_id": intent.execution_id,
            "generation_id": intent.generation_id,
            "attempt_id": intent.attempt_id,
            "kind": intent.kind.value,
            "previous_generation_id": intent.previous_generation_id,
            "expected_head_revision": intent.expected_head_revision,
            "empty_snapshot": True,
        }
        identity_sha256 = _digest(identity)
        return SemanticEventDraft(
            event_id=f"generation-rebase-marker-{identity_sha256}",
            event_type="generation.rebased",
            attempt=intent.attempt,
            operation_id=f"generation-rebase-marker-operation-{identity_sha256}",
            payload={
                "run_id": intent.attempt_id,
                "generation_rebase": {
                    "kind": intent.kind.value,
                    "previous_generation_id": intent.previous_generation_id,
                    "expected_head_revision": intent.expected_head_revision,
                    "empty_snapshot": True,
                    "replacement_message_count": 0,
                },
                "legacy_provenance": {
                    "source": "host_sanitized_generation_snapshot",
                    "capture_status": _LEGACY_CAPTURE_STATUS,
                    "owner_chat_id": intent.owner_chat_id,
                    "session_id": intent.session_id,
                    "source_revision": intent.source_revision,
                },
            },
        )

    @staticmethod
    def _operation_row(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        operation_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT payload_sha256, target_kind, target_key
            FROM operations
            WHERE execution_id = ? AND operation_id = ?
            """,
            (execution_id, operation_id),
        ).fetchone()

    @classmethod
    def _claim_operation(
        cls,
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        operation: OperationRef,
        target_kind: str,
        target_key: str,
    ) -> bool:
        row = cls._operation_row(
            connection,
            execution_id=execution_id,
            operation_id=operation.operation_id,
        )
        if row is not None:
            if (
                row["payload_sha256"] == operation.payload_sha256
                and row["target_kind"] == target_kind
                and row["target_key"] == target_key
            ):
                return False
            raise GenerationRebaseConflict(
                "generation rebase operation payload or target changed"
            )
        connection.execute(
            """
            INSERT INTO operations(
                execution_id, operation_id, payload_sha256,
                target_kind, target_key
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
    def _compatible_kind(kind: GenerationRebaseKind) -> str:
        return {
            GenerationRebaseKind.CREATE: "initial",
            GenerationRebaseKind.EDIT: "edit",
            GenerationRebaseKind.REGENERATE: "regenerate",
            GenerationRebaseKind.RETRY: "regenerate",
        }[kind]

    @staticmethod
    def _manifest_kind(kind: GenerationRebaseKind) -> str:
        return "initial" if kind is GenerationRebaseKind.CREATE else kind.value

    @staticmethod
    def _kind_from_manifest(value: object) -> GenerationRebaseKind:
        if value == "initial":
            return GenerationRebaseKind.CREATE
        try:
            return GenerationRebaseKind(value)
        except (TypeError, ValueError) as error:
            raise GenerationRebaseUnavailable(
                "generation rebase manifest kind is invalid"
            ) from error

    @staticmethod
    def _head_rows(
        connection: sqlite3.Connection,
        owner_chat_id: str,
    ) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
        host = connection.execute(
            "SELECT * FROM host_generation_heads WHERE owner_chat_id = ?",
            (owner_chat_id,),
        ).fetchone()
        bootstrap = connection.execute(
            "SELECT * FROM legacy_bootstrap_chat_heads WHERE owner_chat_id = ?",
            (owner_chat_id,),
        ).fetchone()
        return host, bootstrap

    @staticmethod
    def _assert_matching_heads(
        host: sqlite3.Row | None,
        bootstrap: sqlite3.Row | None,
    ) -> None:
        if (host is None) != (bootstrap is None):
            raise GenerationRebaseUnavailable(
                "generation lifecycle and bootstrap heads diverged"
            )
        if host is None:
            return
        if (
            host["owner_chat_id"] != bootstrap["owner_chat_id"]
            or host["execution_id"] != bootstrap["execution_id"]
            or host["session_id"] != bootstrap["session_id"]
            or host["current_generation_id"]
            != bootstrap["current_generation_id"]
            or int(host["revision"]) != int(bootstrap["head_revision"])
        ):
            raise GenerationRebaseUnavailable(
                "generation lifecycle and bootstrap heads changed independently"
            )

    def _verified_event_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> JournalEvent:
        raw = bytes(row["event_json"])
        if hashlib.sha256(raw).hexdigest() != row["event_sha256"]:
            raise GenerationRebaseUnavailable(
                "generation rebase journal event digest changed"
            )
        try:
            event = JournalEvent.from_dict(json.loads(raw.decode("utf-8")))
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as error:
            raise GenerationRebaseUnavailable(
                "generation rebase journal event is unreadable"
            ) from error
        operation = self._operation_row(
            connection,
            execution_id=row["execution_id"],
            operation_id=event.operation.operation_id,
        )
        if (
            _canonical_bytes(event.to_dict()) != raw
            or event.attempt.generation.execution_id != row["execution_id"]
            or event.store_seq != row["store_seq"]
            or event.event_id != row["event_id"]
            or event.attempt.generation.generation_id != row["generation_id"]
            or event.attempt.attempt_id != row["attempt_id"]
            or event.event_type != row["event_type"]
            or event.operation.operation_id != row["operation_id"]
            or operation is None
            or operation["payload_sha256"] != event.operation.payload_sha256
            or operation["target_kind"] != "journal_event"
            or operation["target_key"] != event.event_id
        ):
            raise GenerationRebaseUnavailable(
                "generation rebase journal event authority changed"
            )
        return event

    @staticmethod
    def _interaction_id(event: JournalEvent) -> str:
        direct = event.payload.get("interaction_id")
        request = event.payload.get("interaction_request")
        nested = request.get("interaction_id") if isinstance(request, Mapping) else None
        values = {
            str(value).strip()
            for value in (direct, nested)
            if isinstance(value, str) and value.strip()
        }
        if len(values) != 1:
            raise GenerationRebaseUnavailable(
                "generation rebase interaction identity is ambiguous"
            )
        interaction_id = next(iter(values))
        try:
            return _required_text(
                interaction_id,
                "interaction_id",
                identifier=True,
            )
        except (TypeError, ValueError) as error:
            raise GenerationRebaseUnavailable(
                "generation rebase interaction identity is invalid"
            ) from error

    def _assert_no_pending_interaction(
        self,
        connection: sqlite3.Connection,
        intent: GenerationRebaseIntent,
    ) -> None:
        rows = list(
            connection.execute(
                """
                SELECT * FROM events
                WHERE execution_id = ? AND generation_id = ?
                ORDER BY store_seq
                """,
                (intent.execution_id, intent.previous_generation_id),
            )
        )
        events = tuple(
            self._verified_event_from_row(connection, row) for row in rows
        )
        try:
            suppressed_legacy_resolutions = (
                legacy_interaction_resolution_supersessions(
                    tuple(
                        interaction_resolution_compatibility_record(
                            ordinal=event.store_seq,
                            event_type=event.event_type,
                            interaction_id=self._interaction_id(event),
                            execution_id=event.attempt.generation.execution_id,
                            generation_id=event.attempt.generation.generation_id,
                            attempt_id=event.attempt.attempt_id,
                            payload=event.payload,
                            resource_refs=event.resource_refs,
                        )
                        for event in events
                        if event.event_type
                        in _INTERACTION_RESOLUTION_EVENT_TYPES
                    )
                )
            )
        except InteractionResolutionCompatibilityError as error:
            raise GenerationRebaseUnavailable(
                "generation rebase interaction resolution is duplicated"
            ) from error
        requests: dict[str, JournalEvent] = {}
        resolutions: dict[str, JournalEvent] = {}
        terminal_store_seqs: dict[str, int] = {}
        for event in events:
            if event.event_type in _INTERACTION_REQUEST_EVENT_TYPES:
                interaction_id = self._interaction_id(event)
                if interaction_id in requests:
                    raise GenerationRebaseUnavailable(
                        "generation rebase interaction request is duplicated"
                    )
                requests[interaction_id] = event
            elif event.event_type in _INTERACTION_RESOLUTION_EVENT_TYPES:
                if event.store_seq in suppressed_legacy_resolutions:
                    continue
                interaction_id = self._interaction_id(event)
                if interaction_id in resolutions:
                    raise GenerationRebaseUnavailable(
                        "generation rebase interaction resolution is duplicated"
                    )
                resolutions[interaction_id] = event
            elif event.event_type in _ATTEMPT_TERMINAL_EVENT_TYPES:
                previous = terminal_store_seqs.get(event.attempt.attempt_id, 0)
                terminal_store_seqs[event.attempt.attempt_id] = max(
                    previous,
                    event.store_seq,
                )

        for interaction_id, resolution in resolutions.items():
            request = requests.get(interaction_id)
            if request is None or resolution.store_seq <= request.store_seq:
                raise GenerationRebaseUnavailable(
                    "generation rebase interaction lifecycle is not uniquely paired"
                )

        pending = tuple(
            interaction_id
            for interaction_id, request in requests.items()
            if interaction_id not in resolutions
            and terminal_store_seqs.get(request.attempt.attempt_id, 0)
            <= request.store_seq
        )
        if pending:
            raise GenerationRebasePreflightBlocked(
                "generation rebase found a pending durable interaction"
            )

    @staticmethod
    def _checkpoint_tables(
        connection: sqlite3.Connection,
    ) -> tuple[bool, bool]:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        return (
            "context_compiler_v2_schema" in tables,
            "checkpoints" in tables,
        )

    def _assert_no_prepared_checkpoint(
        self,
        connection: sqlite3.Connection,
        intent: GenerationRebaseIntent,
    ) -> None:
        has_schema, has_checkpoints = self._checkpoint_tables(connection)
        if has_schema != has_checkpoints:
            raise GenerationRebaseUnavailable(
                "generation rebase compiler checkpoint schema is incomplete"
            )
        if not has_checkpoints:
            orphan = connection.execute(
                """
                SELECT 1 FROM operations
                WHERE execution_id = ? AND target_kind = 'checkpoint'
                LIMIT 1
                """,
                (intent.execution_id,),
            ).fetchone()
            if orphan is not None:
                raise GenerationRebaseUnavailable(
                    "generation rebase found checkpoint operations without a store"
                )
            return
        versions = {
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM context_compiler_v2_schema"
            )
        }
        if versions != {1}:
            raise GenerationRebaseUnavailable(
                "generation rebase compiler checkpoint schema is unsupported"
            )

        rows = list(
            connection.execute(
                """
                SELECT * FROM checkpoints
                WHERE execution_id = ? AND status = 'prepared'
                ORDER BY source_start_seq, source_end_seq, checkpoint_id
                """,
                (intent.execution_id,),
            )
        )
        for row in rows:
            try:
                semantic_raw = bytes(row["semantic_json"])
                artifact_raw = bytes(row["artifact_json"])
            except (TypeError, ValueError) as error:
                raise GenerationRebaseUnavailable(
                    "generation rebase checkpoint record bytes are invalid"
                ) from error
            if (
                hashlib.sha256(semantic_raw).hexdigest()
                != row["semantic_sha256"]
                or hashlib.sha256(artifact_raw).hexdigest()
                != row["artifact_sha256"]
            ):
                raise GenerationRebaseUnavailable(
                    "generation rebase checkpoint record digest changed"
                )
            try:
                semantic = json.loads(semantic_raw.decode("utf-8"))
                artifact_record = json.loads(artifact_raw.decode("utf-8"))
                source_range = EventRange.from_dict(semantic["source_range"])
                refs = tuple(
                    ResourceRef.from_dict(value) for value in semantic["refs"]
                )
                artifact = ArtifactRef.from_dict(artifact_record)
                operation = OperationRef(
                    row["operation_id"],
                    row["operation_payload_sha256"],
                )
                summary_sha256 = _sha256(
                    semantic["summary_sha256"],
                    "summary_sha256",
                )
            except (
                KeyError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ) as error:
                raise GenerationRebaseUnavailable(
                    "generation rebase checkpoint record is unreadable"
                ) from error
            expected_semantic = {
                "schema": "unchain.sqlite_checkpoint_prepare.v1",
                "source_range": source_range.to_dict(),
                "summary_sha256": summary_sha256,
                "refs": [ref.to_dict() for ref in refs],
            }
            operation_row = self._operation_row(
                connection,
                execution_id=intent.execution_id,
                operation_id=operation.operation_id,
            )
            checkpoint_id = _checkpoint_identity(intent.execution_id, operation)
            if (
                semantic != expected_semantic
                or _canonical_bytes(semantic) != semantic_raw
                or _canonical_bytes(artifact.to_dict()) != artifact_raw
                or row["checkpoint_id"] != checkpoint_id
                or row["preparation_id"] != "preparation-" + checkpoint_id
                or int(row["revision"]) != 1
                or row["source_start_seq"] != source_range.start.store_seq
                or row["source_start_event_id"] != source_range.start.event_id
                or row["source_end_seq"] != source_range.end.store_seq
                or row["source_end_event_id"] != source_range.end.event_id
                or artifact.ref.kind != "artifact"
                or artifact.ref.fragment
                or operation_row is None
                or operation_row["payload_sha256"] != operation.payload_sha256
                or operation_row["target_kind"] != "checkpoint"
                or operation_row["target_key"] != checkpoint_id
            ):
                raise GenerationRebaseUnavailable(
                    "generation rebase checkpoint authority changed"
                )
            endpoint_rows = list(
                connection.execute(
                    """
                    SELECT * FROM events
                    WHERE execution_id = ? AND store_seq IN (?, ?)
                    ORDER BY store_seq
                    """,
                    (
                        intent.execution_id,
                        source_range.start.store_seq,
                        source_range.end.store_seq,
                    ),
                )
            )
            expected_endpoint_count = (
                1
                if source_range.start.store_seq == source_range.end.store_seq
                else 2
            )
            if len(endpoint_rows) != expected_endpoint_count:
                raise GenerationRebaseUnavailable(
                    "generation rebase checkpoint endpoints are unavailable"
                )
            endpoints = tuple(
                self._verified_event_from_row(connection, endpoint)
                for endpoint in endpoint_rows
            )
            if (
                endpoints[0].event_id != source_range.start.event_id
                or endpoints[-1].event_id != source_range.end.event_id
            ):
                raise GenerationRebaseUnavailable(
                    "generation rebase checkpoint endpoint identity changed"
                )
            overlaps_current = connection.execute(
                """
                SELECT 1 FROM events
                WHERE execution_id = ? AND generation_id = ?
                  AND store_seq BETWEEN ? AND ?
                LIMIT 1
                """,
                (
                    intent.execution_id,
                    intent.previous_generation_id,
                    source_range.start.store_seq,
                    source_range.end.store_seq,
                ),
            ).fetchone()
            if overlaps_current is not None:
                raise GenerationRebasePreflightBlocked(
                    "generation rebase found a prepared durable checkpoint"
                )

    def _assert_durable_preflight(
        self,
        connection: sqlite3.Connection,
        intent: GenerationRebaseIntent,
        current_receipt: GenerationRebaseReceipt | None,
    ) -> None:
        if intent.kind is GenerationRebaseKind.CREATE:
            return
        if current_receipt is None:
            raise GenerationRebaseUnavailable(
                "generation rebase current receipt is unavailable"
            )
        self._assert_no_prepared_checkpoint(connection, intent)
        self._assert_no_pending_interaction(connection, intent)
        self._assert_no_open_attempt_or_tool(
            connection,
            intent,
            current_receipt,
        )

    def _assert_no_open_attempt_or_tool(
        self,
        connection: sqlite3.Connection,
        intent: GenerationRebaseIntent,
        current_receipt: GenerationRebaseReceipt,
    ) -> None:
        """Reject a cutover while current-generation work is still live.

        The receipt's manifest range is the atomic import attempt and is
        already verified by ``_receipt_for_generation``.  Only events outside
        that sealed range represent runtime work, even when a host reuses the
        import attempt ID.
        """

        rows = list(
            connection.execute(
                """
                SELECT * FROM events
                WHERE execution_id = ? AND generation_id = ?
                ORDER BY store_seq
                """,
                (intent.execution_id, intent.previous_generation_id),
            )
        )
        events = tuple(
            self._verified_event_from_row(connection, row) for row in rows
        )
        import_start = current_receipt.first_cursor.store_seq
        import_end = current_receipt.last_cursor.store_seq
        runtime_events = tuple(
            event
            for event in events
            if not import_start <= event.store_seq <= import_end
        )
        if not runtime_events:
            return

        tool_groups: dict[tuple[str, str], list[JournalEvent]] = {}
        for event in runtime_events:
            if event.event_type not in _TOOL_LIFECYCLE_EVENT_TYPES:
                continue
            call_id = event.payload.get("call_id")
            if (
                not isinstance(call_id, str)
                or not call_id
                or call_id != call_id.strip()
            ):
                raise GenerationRebaseUnavailable(
                    "generation rebase tool lifecycle has no stable call identity"
                )
            tool_groups.setdefault(
                (event.attempt.attempt_id, call_id),
                [],
            ).append(event)

        for lifecycle in tool_groups.values():
            starts = [
                event
                for event in lifecycle
                if event.event_type in _TOOL_STARTED_EVENT_TYPES
            ]
            if not starts:
                continue
            intents = [
                event
                for event in lifecycle
                if event.event_type in _TOOL_INTENT_EVENT_TYPES
            ]
            seals = [
                event
                for event in lifecycle
                if event.event_type in _TOOL_SEALED_EVENT_TYPES
            ]
            results = [
                event
                for event in lifecycle
                if event.event_type in _TOOL_RESULT_EVENT_TYPES
            ]
            if (
                len(intents) != 1
                or len(starts) != 1
                or len(seals) > 1
                or len(results) > 1
            ):
                raise GenerationRebaseUnavailable(
                    "generation rebase tool lifecycle is not uniquely paired"
                )
            ordered = intents[0], starts[0]
            if ordered[1].store_seq <= ordered[0].store_seq:
                raise GenerationRebaseUnavailable(
                    "generation rebase tool start precedes its durable intent"
                )
            if seals and seals[0].store_seq <= starts[0].store_seq:
                raise GenerationRebaseUnavailable(
                    "generation rebase sealed tool completion precedes its start"
                )
            if results and results[0].store_seq <= starts[0].store_seq:
                raise GenerationRebaseUnavailable(
                    "generation rebase tool result precedes its start"
                )
            if seals and results and results[0].store_seq <= seals[0].store_seq:
                raise GenerationRebaseUnavailable(
                    "generation rebase tool result precedes sealed completion"
                )
            tool_names = {
                event.payload.get("tool_name") for event in lifecycle
            }
            tool_name = next(iter(tool_names)) if len(tool_names) == 1 else None
            if (
                not isinstance(tool_name, str)
                or not tool_name
                or tool_name != tool_name.strip()
            ):
                raise GenerationRebaseUnavailable(
                    "generation rebase tool lifecycle identity changed"
                )
            if not results:
                raise GenerationRebasePreflightBlocked(
                    "generation rebase found an unfinished durable tool"
                )

        attempts: dict[str, list[JournalEvent]] = {}
        for event in runtime_events:
            attempts.setdefault(event.attempt.attempt_id, []).append(event)
        for attempt_events in attempts.values():
            terminals = [
                event
                for event in attempt_events
                if event.event_type in _ATTEMPT_TERMINAL_EVENT_TYPES
            ]
            if len(terminals) > 1:
                raise GenerationRebaseUnavailable(
                    "generation rebase attempt has duplicate terminal events"
                )
            if not terminals:
                raise GenerationRebasePreflightBlocked(
                    "generation rebase found an unfinished durable attempt"
                )
            if terminals[0].store_seq != attempt_events[-1].store_seq:
                raise GenerationRebaseUnavailable(
                    "generation rebase attempt continued after its terminal event"
                )

    @staticmethod
    def _ensure_initial_execution(
        connection: sqlite3.Connection,
        intent: GenerationRebaseIntent,
    ) -> None:
        execution = connection.execute(
            "SELECT next_store_seq FROM executions WHERE execution_id = ?",
            (intent.execution_id,),
        ).fetchone()
        if execution is None:
            connection.execute(
                "INSERT INTO executions(execution_id) VALUES (?)",
                (intent.execution_id,),
            )
            return
        event = connection.execute(
            "SELECT 1 FROM events WHERE execution_id = ? LIMIT 1",
            (intent.execution_id,),
        ).fetchone()
        operation = connection.execute(
            "SELECT 1 FROM operations WHERE execution_id = ? LIMIT 1",
            (intent.execution_id,),
        ).fetchone()
        if int(execution["next_store_seq"]) != 1 or event is not None or (
            operation is not None
        ):
            raise GenerationRebaseConflict(
                "generation create cannot claim a non-empty execution"
            )

    @staticmethod
    def _validate_preflight(intent: GenerationRebaseIntent) -> None:
        if not intent.preflight.permits_rebase:
            raise GenerationRebasePreflightBlocked(
                "generation rebase preflight found an unsanitized host snapshot"
            )

    def rebase(
        self,
        request: GenerationRebaseRequest,
    ) -> GenerationRebaseReceipt:
        """Atomically create, import, bind, and select one generation."""

        if not isinstance(request, GenerationRebaseRequest):
            raise TypeError("request must be a GenerationRebaseRequest")
        intent = request.intent
        expected_operation = build_generation_rebase_operation(
            operation_id=request.operation.operation_id,
            intent=intent,
        )
        if expected_operation != request.operation:
            raise GenerationRebaseConflict(
                "generation rebase operation payload hash changed"
            )
        self._validate_preflight(intent)
        target_key = self._target_key(intent)
        next_revision = intent.expected_head_revision + 1
        transition_payload = _host_transition_payload(intent)
        lifecycle_operation = OperationRef(
            _derived_operation_id(
                "generation-transition",
                request.operation,
                intent.generation_id,
            ),
            _digest(transition_payload),
        )
        attempt_payload = _host_attempt_payload(
            intent,
            head_revision=next_revision,
        )
        attempt_operation = OperationRef(
            _derived_operation_id(
                "generation-attempt-binding",
                request.operation,
                intent.attempt_id,
            ),
            _digest(attempt_payload),
        )
        try:
            with self._transaction(immediate=True) as connection:
                existing_primary = self._operation_row(
                    connection,
                    execution_id=intent.execution_id,
                    operation_id=request.operation.operation_id,
                )
                if existing_primary is not None:
                    if (
                        existing_primary["payload_sha256"]
                        != request.operation.payload_sha256
                        or existing_primary["target_kind"]
                        != "legacy_bootstrap_manifest"
                        or existing_primary["target_key"] != target_key
                    ):
                        raise GenerationRebaseConflict(
                            "generation rebase operation ID was reused"
                        )
                    receipt = self._receipt_for_generation(
                        connection,
                        owner_chat_id=intent.owner_chat_id,
                        execution_id=intent.execution_id,
                        session_id=intent.session_id,
                        generation_id=intent.generation_id,
                    )
                    if receipt is None or receipt.operation != request.operation:
                        raise GenerationRebaseUnavailable(
                            "generation rebase operation receipt is incomplete"
                        )
                    return replace(receipt, duplicate=True)

                current_receipt = None
                host_head, bootstrap_head = self._head_rows(
                    connection,
                    intent.owner_chat_id,
                )
                self._assert_matching_heads(host_head, bootstrap_head)
                if intent.kind is GenerationRebaseKind.CREATE:
                    if host_head is not None:
                        raise GenerationRebaseConflict(
                            "generation create requires an empty chat head"
                        )
                    orphaned_owner_state = any(
                        connection.execute(query, (intent.owner_chat_id,)).fetchone()
                        is not None
                        for query in (
                            "SELECT 1 FROM host_generation_chat_bindings "
                            "WHERE owner_chat_id = ? LIMIT 1",
                            "SELECT 1 FROM host_generation_records "
                            "WHERE owner_chat_id = ? LIMIT 1",
                            "SELECT 1 FROM legacy_bootstrap_manifests "
                            "WHERE owner_chat_id = ? LIMIT 1",
                        )
                    )
                    if orphaned_owner_state:
                        raise GenerationRebaseUnavailable(
                            "generation create found owner state without a current head"
                        )
                    foreign_binding = connection.execute(
                        """
                        SELECT owner_chat_id FROM host_generation_chat_bindings
                        WHERE execution_id = ?
                        """,
                        (intent.execution_id,),
                    ).fetchone()
                    if foreign_binding is not None:
                        raise GenerationRebaseConflict(
                            "generation execution is already bound to a chat"
                        )
                    self._ensure_initial_execution(connection, intent)
                    connection.execute(
                        """
                        INSERT INTO host_generation_chat_bindings(
                            owner_chat_id, execution_id, session_id
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            intent.owner_chat_id,
                            intent.execution_id,
                            intent.session_id,
                        ),
                    )
                else:
                    if host_head is None or bootstrap_head is None:
                        raise GenerationRebaseConflict(
                            "generation rebase has no current generation"
                        )
                    if (
                        host_head["execution_id"] != intent.execution_id
                        or host_head["session_id"] != intent.session_id
                    ):
                        raise GenerationRebaseConflict(
                            "generation rebase binding is outside the durable chat"
                        )
                    if (
                        host_head["current_generation_id"]
                        != intent.previous_generation_id
                    ):
                        raise GenerationRebaseConflict(
                            "generation rebase previous generation is not current"
                        )
                    if int(host_head["revision"]) != intent.expected_head_revision:
                        raise GenerationRebaseConflict(
                            "generation rebase head revision is not current"
                        )
                    current_receipt = self._receipt_for_generation(
                        connection,
                        owner_chat_id=intent.owner_chat_id,
                        execution_id=intent.execution_id,
                        session_id=intent.session_id,
                        generation_id=intent.previous_generation_id,
                    )
                    if (
                        current_receipt is None
                        or current_receipt.head_revision
                        != intent.expected_head_revision
                        or current_receipt.source_revision
                        != bootstrap_head["current_source_revision"]
                    ):
                        raise GenerationRebaseUnavailable(
                            "generation rebase current receipt is incomplete"
                        )

                self._assert_durable_preflight(
                    connection,
                    intent,
                    current_receipt,
                )

                if connection.execute(
                    """
                    SELECT 1 FROM host_generation_records
                    WHERE owner_chat_id = ? AND generation_id = ?
                    """,
                    (intent.owner_chat_id, intent.generation_id),
                ).fetchone() is not None:
                    raise GenerationRebaseConflict(
                        "generation ID already belongs to a lifecycle record"
                    )
                if connection.execute(
                    """
                    SELECT 1 FROM legacy_bootstrap_manifests
                    WHERE owner_chat_id = ? AND source_revision = ?
                    """,
                    (intent.owner_chat_id, intent.source_revision),
                ).fetchone() is not None:
                    raise GenerationRebaseConflict(
                        "generation source revision is already imported"
                    )
                if connection.execute(
                    """
                    SELECT 1 FROM host_generation_attempt_bindings
                    WHERE execution_id = ? AND attempt_id = ?
                    """,
                    (intent.execution_id, intent.attempt_id),
                ).fetchone() is not None:
                    raise GenerationRebaseConflict(
                        "generation attempt ID is already bound"
                    )

                if not self._claim_operation(
                    connection,
                    execution_id=intent.execution_id,
                    operation=request.operation,
                    target_kind="legacy_bootstrap_manifest",
                    target_key=target_key,
                ):
                    raise GenerationRebaseUnavailable(
                        "generation rebase operation replay lost its receipt"
                    )
                execution = connection.execute(
                    "SELECT next_store_seq FROM executions WHERE execution_id = ?",
                    (intent.execution_id,),
                ).fetchone()
                if execution is None:
                    raise GenerationRebaseUnavailable(
                        "generation rebase journal head is unavailable"
                    )
                next_store_seq = int(execution["next_store_seq"])
                events: list[JournalEvent] = []
                manifest_events: list[dict[str, Any]] = []
                drafts: tuple[
                    tuple[SemanticEventDraft, GenerationSnapshotMessage | None],
                    ...,
                ] = (
                    tuple(
                        (self._message_draft(intent, message, index), message)
                        for index, message in enumerate(intent.messages)
                    )
                    if intent.messages
                    else ((self._marker_draft(intent), None),)
                )
                for index, (draft, message) in enumerate(drafts):
                    event = JournalEvent(
                        event_id=draft.event_id,
                        event_type=draft.event_type,
                        attempt=draft.attempt,
                        operation=draft.operation,
                        store_seq=next_store_seq + index,
                        payload=draft.payload,
                        resource_refs=draft.resource_refs,
                    )
                    if not self._claim_operation(
                        connection,
                        execution_id=intent.execution_id,
                        operation=event.operation,
                        target_kind="journal_event",
                        target_key=event.event_id,
                    ):
                        raise GenerationRebaseConflict(
                            "generation rebase event operation already exists"
                        )
                    event_json = _canonical_bytes(event.to_dict())
                    event_sha256 = hashlib.sha256(event_json).hexdigest()
                    connection.execute(
                        """
                        INSERT INTO events(
                            execution_id, store_seq, event_id, generation_id,
                            attempt_id, event_type, operation_id,
                            event_json, event_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            intent.execution_id,
                            event.store_seq,
                            event.event_id,
                            intent.generation_id,
                            intent.attempt_id,
                            event.event_type,
                            event.operation.operation_id,
                            event_json,
                            event_sha256,
                        ),
                    )
                    events.append(event)
                    descriptor = {
                        "event_id": event.event_id,
                        "store_seq": event.store_seq,
                        "event_sha256": event_sha256,
                    }
                    if message is not None:
                        descriptor.update(
                            {
                                "message_id": message.message_id,
                                "role": message.role,
                            }
                        )
                    else:
                        descriptor["record_kind"] = "generation_marker"
                    manifest_events.append(descriptor)
                advanced = connection.execute(
                    """
                    UPDATE executions SET next_store_seq = ?
                    WHERE execution_id = ? AND next_store_seq = ?
                    """,
                    (
                        next_store_seq + len(events),
                        intent.execution_id,
                        next_store_seq,
                    ),
                )
                if advanced.rowcount != 1:
                    raise GenerationRebaseConflict(
                        "generation rebase journal head changed"
                    )

                manifest = {
                    "schema": "unchain.legacy_bootstrap_manifest.v1",
                    "owner_chat_id": intent.owner_chat_id,
                    "source_revision": intent.source_revision,
                    "session_id": intent.session_id,
                    "execution_id": intent.execution_id,
                    "generation_id": intent.generation_id,
                    "attempt_id": intent.attempt_id,
                    "capture_status": _LEGACY_CAPTURE_STATUS,
                    "payload_sha256": request.operation.payload_sha256,
                    "primary_operation_id": request.operation.operation_id,
                    "rebase": {
                        "kind": self._manifest_kind(intent.kind),
                        "previous_generation_id": intent.previous_generation_id,
                    },
                    "preflight_proof_id": intent.preflight.proof_id,
                    "task_state": (
                        intent.task_state.to_record()
                        if intent.task_state is not None
                        else None
                    ),
                    "events": manifest_events,
                }
                manifest_json = _canonical_bytes(manifest)
                manifest_sha256 = hashlib.sha256(manifest_json).hexdigest()
                connection.execute(
                    """
                    INSERT INTO legacy_bootstrap_manifests(
                        owner_chat_id, source_revision, session_id,
                        execution_id, generation_id, attempt_id,
                        operation_id, payload_sha256,
                        manifest_json, manifest_sha256,
                        first_store_seq, last_store_seq, event_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent.owner_chat_id,
                        intent.source_revision,
                        intent.session_id,
                        intent.execution_id,
                        intent.generation_id,
                        intent.attempt_id,
                        request.operation.operation_id,
                        request.operation.payload_sha256,
                        manifest_json,
                        manifest_sha256,
                        events[0].store_seq,
                        events[-1].store_seq,
                        len(events),
                    ),
                )

                connection.execute(
                    """
                    INSERT INTO host_generation_records(
                        owner_chat_id, execution_id, session_id, generation_id,
                        transition_kind, previous_generation_id, revision,
                        operation_id, payload_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent.owner_chat_id,
                        intent.execution_id,
                        intent.session_id,
                        intent.generation_id,
                        self._compatible_kind(intent.kind),
                        intent.previous_generation_id,
                        next_revision,
                        lifecycle_operation.operation_id,
                        lifecycle_operation.payload_sha256,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO host_generation_operations(
                        owner_chat_id, operation_id, payload_sha256,
                        mutation_kind, result_generation_id, result_revision
                    ) VALUES (?, ?, ?, 'transition', ?, ?)
                    """,
                    (
                        intent.owner_chat_id,
                        lifecycle_operation.operation_id,
                        lifecycle_operation.payload_sha256,
                        intent.generation_id,
                        next_revision,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO host_generation_operations(
                        owner_chat_id, operation_id, payload_sha256,
                        mutation_kind, result_generation_id,
                        result_revision, result_attempt_id
                    ) VALUES (?, ?, ?, 'attempt_binding', ?, ?, ?)
                    """,
                    (
                        intent.owner_chat_id,
                        attempt_operation.operation_id,
                        attempt_operation.payload_sha256,
                        intent.generation_id,
                        next_revision,
                        intent.attempt_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO host_generation_attempt_bindings(
                        owner_chat_id, execution_id, session_id, generation_id,
                        attempt_id, head_revision, operation_id, payload_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent.owner_chat_id,
                        intent.execution_id,
                        intent.session_id,
                        intent.generation_id,
                        intent.attempt_id,
                        next_revision,
                        attempt_operation.operation_id,
                        attempt_operation.payload_sha256,
                    ),
                )

                if host_head is None:
                    connection.execute(
                        """
                        INSERT INTO host_generation_heads(
                            owner_chat_id, execution_id, session_id,
                            current_generation_id, revision
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            intent.owner_chat_id,
                            intent.execution_id,
                            intent.session_id,
                            intent.generation_id,
                            next_revision,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO legacy_bootstrap_chat_heads(
                            owner_chat_id, execution_id, session_id,
                            current_generation_id, current_source_revision,
                            head_revision
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            intent.owner_chat_id,
                            intent.execution_id,
                            intent.session_id,
                            intent.generation_id,
                            intent.source_revision,
                            next_revision,
                        ),
                    )
                else:
                    host_updated = connection.execute(
                        """
                        UPDATE host_generation_heads
                        SET current_generation_id = ?, revision = ?
                        WHERE owner_chat_id = ? AND execution_id = ?
                          AND session_id = ? AND current_generation_id = ?
                          AND revision = ?
                        """,
                        (
                            intent.generation_id,
                            next_revision,
                            intent.owner_chat_id,
                            intent.execution_id,
                            intent.session_id,
                            intent.previous_generation_id,
                            intent.expected_head_revision,
                        ),
                    )
                    bootstrap_updated = connection.execute(
                        """
                        UPDATE legacy_bootstrap_chat_heads
                        SET current_generation_id = ?,
                            current_source_revision = ?,
                            head_revision = ?
                        WHERE owner_chat_id = ? AND execution_id = ?
                          AND session_id = ? AND current_generation_id = ?
                          AND head_revision = ?
                        """,
                        (
                            intent.generation_id,
                            intent.source_revision,
                            next_revision,
                            intent.owner_chat_id,
                            intent.execution_id,
                            intent.session_id,
                            intent.previous_generation_id,
                            intent.expected_head_revision,
                        ),
                    )
                    if host_updated.rowcount != 1 or (
                        bootstrap_updated.rowcount != 1
                    ):
                        raise GenerationRebaseConflict(
                            "generation rebase head changed during compare-and-swap"
                        )

                receipt = self._receipt_for_generation(
                    connection,
                    owner_chat_id=intent.owner_chat_id,
                    execution_id=intent.execution_id,
                    session_id=intent.session_id,
                    generation_id=intent.generation_id,
                )
                if receipt is None or receipt.operation != request.operation:
                    raise GenerationRebaseUnavailable(
                        "generation rebase receipt was not durably completed"
                    )
                return receipt
        except GenerationRebaseError:
            raise
        except sqlite3.IntegrityError as error:
            raise GenerationRebaseConflict(
                "generation rebase conflicted with durable state"
            ) from error
        except sqlite3.Error as error:
            raise GenerationRebaseUnavailable(
                "generation rebase SQLite transaction failed"
            ) from error

    @staticmethod
    def _decode_manifest(row: sqlite3.Row) -> Mapping[str, Any]:
        raw = bytes(row["manifest_json"])
        if hashlib.sha256(raw).hexdigest() != row["manifest_sha256"]:
            raise GenerationRebaseUnavailable(
                "generation rebase manifest digest changed"
            )
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GenerationRebaseUnavailable(
                "generation rebase manifest is unreadable"
            ) from error
        if type(decoded) is not dict or _canonical_bytes(decoded) != raw:
            raise GenerationRebaseUnavailable(
                "generation rebase manifest is not canonical"
            )
        expected = {
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
        if set(decoded) != expected or decoded["schema"] != (
            "unchain.legacy_bootstrap_manifest.v1"
        ):
            raise GenerationRebaseUnavailable(
                "generation rebase manifest shape changed"
            )
        for field_name in (
            "owner_chat_id",
            "source_revision",
            "session_id",
            "execution_id",
            "generation_id",
            "attempt_id",
            "payload_sha256",
        ):
            if decoded[field_name] != row[field_name]:
                raise GenerationRebaseUnavailable(
                    "generation rebase manifest index changed"
                )
        if (
            decoded["primary_operation_id"] != row["operation_id"]
            or decoded["capture_status"] != _LEGACY_CAPTURE_STATUS
            or type(decoded["rebase"]) is not dict
            or set(decoded["rebase"])
            != {"kind", "previous_generation_id"}
        ):
            raise GenerationRebaseUnavailable(
                "generation rebase manifest authority changed"
            )
        events = decoded["events"]
        if (
            type(events) is not list
            or len(events) != row["event_count"]
            or not events
            or events[0].get("store_seq") != row["first_store_seq"]
            or events[-1].get("store_seq") != row["last_store_seq"]
        ):
            raise GenerationRebaseUnavailable(
                "generation rebase manifest event range changed"
            )
        event_kinds = tuple(
            (
                "generation_marker"
                if type(event) is dict
                and event.get("record_kind") == "generation_marker"
                else "message"
                if type(event) is dict
                and set(event)
                == {
                    "message_id",
                    "role",
                    "event_id",
                    "store_seq",
                    "event_sha256",
                }
                else "invalid"
            )
            for event in events
        )
        if event_kinds != ("generation_marker",) and (
            not event_kinds or any(kind != "message" for kind in event_kinds)
        ):
            raise GenerationRebaseUnavailable(
                "generation rebase manifest message accounting changed"
            )
        return MappingProxyType(decoded)

    @classmethod
    def _verify_events(
        cls,
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        manifest: Mapping[str, Any],
        expected_head_revision: int,
    ) -> None:
        persisted = list(
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
        expected = manifest["events"]
        if len(persisted) != row["event_count"] or len(persisted) != len(expected):
            raise GenerationRebaseUnavailable(
                "generation rebase journal range is incomplete"
            )
        for event_row, descriptor in zip(persisted, expected, strict=True):
            if type(descriptor) is not dict:
                raise GenerationRebaseUnavailable(
                    "generation rebase event descriptor is invalid"
                )
            raw = bytes(event_row["event_json"])
            if hashlib.sha256(raw).hexdigest() != event_row["event_sha256"]:
                raise GenerationRebaseUnavailable(
                    "generation rebase event digest changed"
                )
            try:
                event = JournalEvent.from_dict(json.loads(raw.decode("utf-8")))
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ) as error:
                raise GenerationRebaseUnavailable(
                    "generation rebase event is unreadable"
                ) from error
            operation = cls._operation_row(
                connection,
                execution_id=row["execution_id"],
                operation_id=event.operation.operation_id,
            )
            descriptor_kind = (
                "generation_marker"
                if descriptor.get("record_kind") == "generation_marker"
                else "message"
            )
            legacy_provenance = event.payload.get("legacy_provenance")
            common_invalid = (
                _canonical_bytes(event.to_dict()) != raw
                or event.event_id != descriptor.get("event_id")
                or event.store_seq != descriptor.get("store_seq")
                or event_row["event_sha256"] != descriptor.get("event_sha256")
                or event_row["event_id"] != event.event_id
                or event_row["store_seq"] != event.store_seq
                or event_row["generation_id"]
                != event.attempt.generation.generation_id
                or event_row["attempt_id"] != event.attempt.attempt_id
                or event_row["event_type"] != event.event_type
                or event_row["operation_id"] != event.operation.operation_id
                or event.attempt.generation.execution_id != row["execution_id"]
                or event.attempt.generation.generation_id != row["generation_id"]
                or event.attempt.attempt_id != row["attempt_id"]
                or event.payload.get("run_id") != row["attempt_id"]
                or not isinstance(legacy_provenance, Mapping)
                or legacy_provenance.get("source")
                != "host_sanitized_generation_snapshot"
                or legacy_provenance.get("capture_status")
                != _LEGACY_CAPTURE_STATUS
                or legacy_provenance.get("owner_chat_id") != row["owner_chat_id"]
                or legacy_provenance.get("session_id") != row["session_id"]
                or legacy_provenance.get("source_revision")
                != row["source_revision"]
                or operation is None
                or operation["payload_sha256"] != event.operation.payload_sha256
                or operation["target_kind"] != "journal_event"
                or operation["target_key"] != event.event_id
            )
            if descriptor_kind == "message":
                message = event.payload.get("message")
                kind_invalid = (
                    set(descriptor)
                    != {
                        "message_id",
                        "role",
                        "event_id",
                        "store_seq",
                        "event_sha256",
                    }
                    or event.event_type
                    not in {"message.user", "message.assistant"}
                    or not isinstance(message, Mapping)
                    or message.get("role") != descriptor.get("role")
                    or event.event_type != f"message.{descriptor.get('role')}"
                    or legacy_provenance.get("message_id")
                    != descriptor.get("message_id")
                )
            elif descriptor_kind == "generation_marker":
                generation_rebase = event.payload.get("generation_rebase")
                kind_invalid = (
                    set(descriptor)
                    != {
                        "record_kind",
                        "event_id",
                        "store_seq",
                        "event_sha256",
                    }
                    or event.event_type != "generation.rebased"
                    or not isinstance(generation_rebase, Mapping)
                    or dict(generation_rebase)
                    != {
                        "kind": cls._kind_from_manifest(
                            manifest["rebase"]["kind"]
                        ).value,
                        "previous_generation_id": manifest["rebase"][
                            "previous_generation_id"
                        ],
                        "expected_head_revision": expected_head_revision,
                        "empty_snapshot": True,
                        "replacement_message_count": 0,
                    }
                )
            else:
                kind_invalid = True
            if common_invalid or kind_invalid:
                raise GenerationRebaseUnavailable(
                    "generation rebase event binding changed"
                )

    def _receipt_for_generation(
        self,
        connection: sqlite3.Connection,
        *,
        owner_chat_id: str,
        execution_id: str,
        session_id: str,
        generation_id: str,
    ) -> GenerationRebaseReceipt | None:
        manifest_row = connection.execute(
            """
            SELECT * FROM legacy_bootstrap_manifests
            WHERE owner_chat_id = ? AND generation_id = ?
            """,
            (owner_chat_id, generation_id),
        ).fetchone()
        if manifest_row is None:
            return None
        lifecycle = connection.execute(
            """
            SELECT * FROM host_generation_records
            WHERE owner_chat_id = ? AND generation_id = ?
            """,
            (owner_chat_id, generation_id),
        ).fetchone()
        attempt = connection.execute(
            """
            SELECT * FROM host_generation_attempt_bindings
            WHERE owner_chat_id = ? AND execution_id = ?
              AND generation_id = ? AND attempt_id = ?
            """,
            (
                owner_chat_id,
                execution_id,
                generation_id,
                manifest_row["attempt_id"],
            ),
        ).fetchone()
        if lifecycle is None or attempt is None:
            raise GenerationRebaseUnavailable(
                "generation rebase lifecycle receipt is incomplete"
            )
        manifest = self._decode_manifest(manifest_row)
        kind = self._kind_from_manifest(manifest["rebase"]["kind"])
        operation = OperationRef(
            manifest_row["operation_id"],
            manifest_row["payload_sha256"],
        )
        primary = self._operation_row(
            connection,
            execution_id=execution_id,
            operation_id=operation.operation_id,
        )
        lifecycle_operation = OperationRef(
            lifecycle["operation_id"],
            lifecycle["payload_sha256"],
        )
        attempt_operation = OperationRef(
            attempt["operation_id"],
            attempt["payload_sha256"],
        )
        lifecycle_receipt = connection.execute(
            """
            SELECT * FROM host_generation_operations
            WHERE owner_chat_id = ? AND operation_id = ?
            """,
            (owner_chat_id, lifecycle_operation.operation_id),
        ).fetchone()
        attempt_receipt = connection.execute(
            """
            SELECT * FROM host_generation_operations
            WHERE owner_chat_id = ? AND operation_id = ?
            """,
            (owner_chat_id, attempt_operation.operation_id),
        ).fetchone()
        expected_compatible_kind = self._compatible_kind(kind)
        if (
            manifest_row["execution_id"] != execution_id
            or manifest_row["session_id"] != session_id
            or lifecycle["execution_id"] != execution_id
            or lifecycle["session_id"] != session_id
            or lifecycle["transition_kind"] != expected_compatible_kind
            or lifecycle["previous_generation_id"]
            != manifest["rebase"]["previous_generation_id"]
            or int(lifecycle["revision"]) != int(attempt["head_revision"])
            or primary is None
            or primary["payload_sha256"] != operation.payload_sha256
            or primary["target_kind"] != "legacy_bootstrap_manifest"
            or primary["target_key"] != f"{owner_chat_id}:{generation_id}"
            or lifecycle_receipt is None
            or lifecycle_receipt["payload_sha256"]
            != lifecycle_operation.payload_sha256
            or lifecycle_receipt["mutation_kind"] != "transition"
            or lifecycle_receipt["result_generation_id"] != generation_id
            or int(lifecycle_receipt["result_revision"])
            != int(lifecycle["revision"])
            or attempt_receipt is None
            or attempt_receipt["payload_sha256"]
            != attempt_operation.payload_sha256
            or attempt_receipt["mutation_kind"] != "attempt_binding"
            or attempt_receipt["result_generation_id"] != generation_id
            or attempt_receipt["result_attempt_id"] != attempt["attempt_id"]
        ):
            raise GenerationRebaseUnavailable(
                "generation rebase durable authorities diverged"
            )
        self._verify_events(
            connection,
            row=manifest_row,
            manifest=manifest,
            expected_head_revision=int(lifecycle["revision"]) - 1,
        )
        task_state = (
            GenerationTaskStateDescriptor.from_record(manifest["task_state"])
            if manifest["task_state"] is not None
            else None
        )
        return GenerationRebaseReceipt(
            owner_chat_id=owner_chat_id,
            session_id=session_id,
            execution_id=execution_id,
            generation_id=generation_id,
            attempt_id=manifest_row["attempt_id"],
            kind=kind,
            previous_generation_id=manifest["rebase"][
                "previous_generation_id"
            ],
            source_revision=manifest_row["source_revision"],
            head_revision=int(lifecycle["revision"]),
            manifest_sha256=manifest_row["manifest_sha256"],
            message_count=(
                0
                if manifest["events"][0].get("record_kind")
                == "generation_marker"
                else len(manifest["events"])
            ),
            first_cursor=EventCursor(
                manifest["events"][0]["store_seq"],
                manifest["events"][0]["event_id"],
            ),
            last_cursor=EventCursor(
                manifest["events"][-1]["store_seq"],
                manifest["events"][-1]["event_id"],
            ),
            operation=operation,
            lifecycle_operation=lifecycle_operation,
            attempt_binding_operation=attempt_operation,
            task_state=task_state,
        )

    def current(
        self,
        *,
        owner_chat_id: str,
        execution_id: str,
        session_id: str,
    ) -> GenerationRebaseHead | None:
        """Read the one head only when lifecycle and bootstrap agree."""

        owner = _required_text(owner_chat_id, "owner_chat_id", identifier=True)
        execution = _required_text(execution_id, "execution_id", identifier=True)
        session = _required_text(session_id, "session_id", identifier=True)
        try:
            with self._transaction(immediate=False) as connection:
                host, bootstrap = self._head_rows(connection, owner)
                self._assert_matching_heads(host, bootstrap)
                if host is None or bootstrap is None:
                    return None
                if host["execution_id"] != execution or host["session_id"] != session:
                    raise GenerationRebaseConflict(
                        "generation head read is outside the durable chat binding"
                    )
                receipt = self._receipt_for_generation(
                    connection,
                    owner_chat_id=owner,
                    execution_id=execution,
                    session_id=session,
                    generation_id=host["current_generation_id"],
                )
                if (
                    receipt is None
                    or receipt.head_revision != int(host["revision"])
                    or receipt.source_revision
                    != bootstrap["current_source_revision"]
                ):
                    raise GenerationRebaseUnavailable(
                        "generation head has no complete durable receipt"
                    )
                return GenerationRebaseHead(
                    owner_chat_id=owner,
                    session_id=session,
                    execution_id=execution,
                    current_generation_id=host["current_generation_id"],
                    current_attempt_id=receipt.attempt_id,
                    current_source_revision=bootstrap["current_source_revision"],
                    revision=int(host["revision"]),
                )
        except GenerationRebaseError:
            raise
        except sqlite3.Error as error:
            raise GenerationRebaseUnavailable(
                "generation rebase current-head read failed"
            ) from error

    def receipt_for_generation(
        self,
        *,
        owner_chat_id: str,
        execution_id: str,
        session_id: str,
        generation_id: str,
    ) -> GenerationRebaseReceipt | None:
        """Read and verify one immutable historical generation receipt."""

        owner = _required_text(owner_chat_id, "owner_chat_id", identifier=True)
        execution = _required_text(execution_id, "execution_id", identifier=True)
        session = _required_text(session_id, "session_id", identifier=True)
        generation = _required_text(
            generation_id,
            "generation_id",
            identifier=True,
        )
        try:
            with self._transaction(immediate=False) as connection:
                return self._receipt_for_generation(
                    connection,
                    owner_chat_id=owner,
                    execution_id=execution,
                    session_id=session,
                    generation_id=generation,
                )
        except GenerationRebaseError:
            raise
        except sqlite3.Error as error:
            raise GenerationRebaseUnavailable(
                "generation rebase receipt read failed"
            ) from error


__all__ = [
    "GenerationRebaseConflict",
    "GenerationRebaseError",
    "GenerationRebaseHead",
    "GenerationRebaseIntent",
    "GenerationRebaseKind",
    "GenerationRebasePreflight",
    "GenerationRebasePreflightBlocked",
    "GenerationRebaseReceipt",
    "GenerationRebaseRequest",
    "GenerationRebaseUnavailable",
    "GenerationSnapshotMessage",
    "GenerationTaskStateDescriptor",
    "SQLiteGenerationRebaseV2Service",
    "build_generation_rebase_operation",
]
