"""Atomic user decisions for published Memory V2 conflict reviews.

This store is the single durable mutation boundary joining an immutable review
proposal, its candidate/job heads, and the target chat workspace.  A decision
either commits every successor and receipt in one ``BEGIN IMMEDIATE``
transaction or commits nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping

from unchain.journal import ResourceRef
from unchain.memory.curator.models import (
    CandidateStatus,
    ConsolidationJobStatus,
    FrozenCandidateSnapshot,
)
from unchain.memory.curator.ports import CurationConflictError, CurationRepositoryError
from unchain.memory.workspace import MemoryEntry, MemoryEntryKind
from unchain.memory.workspace.ports import (
    RepositoryConflictError,
    WorkspaceRepositoryError,
)

from .sqlite_curator_query_v2 import (
    SQLiteCuratorQueryV2Error,
    SQLiteCuratorQueryV2IntegrityError,
    SQLiteCuratorQueryV2Store,
    _decode_record,
)
from .sqlite_curator_v2 import (
    SQLiteCuratorV2IntegrityError,
    SQLiteCuratorV2Store,
    _canonical_json_bytes,
)
from .sqlite_memory_v2 import (
    SQLiteMemoryV2Store,
    SQLiteMemoryV2StoreError,
    SQLiteMemoryV2StoreIntegrityError,
    _path_key,
)


_SCHEMA_VERSION = 1
_RECEIPT_SCHEMA = "unchain.memory_review_decision_receipt.v1"
_REQUEST_SCHEMA = "unchain.memory_review_decision_request.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class MemoryReviewDecisionAction(StrEnum):
    APPLY = "apply"
    REJECT = "reject"


class MemoryReviewDecisionStatus(StrEnum):
    APPLIED = "applied"
    REJECTED = "rejected"


class SQLiteCuratorReviewDecisionV2Error(RuntimeError):
    """A bound review decision could not be completed."""

    def __init__(self, code: str) -> None:
        normalized = ""
        if isinstance(code, str):
            normalized = re.sub(r"[^a-z0-9_:-]+", "_", code.casefold()).strip("_")
        self.code = normalized[:128] or "memory_review_decision_failed"
        super().__init__(self.code)


class SQLiteCuratorReviewDecisionV2Conflict(
    SQLiteCuratorReviewDecisionV2Error
):
    """One of the explicit review, candidate, target, or space fences moved."""

    def __init__(
        self,
        code: str,
        *,
        expected_revision: int | None = None,
        actual_revision: int | None = None,
    ) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(code)


class SQLiteCuratorReviewDecisionV2IntegrityError(
    SQLiteCuratorReviewDecisionV2Error
):
    """Stored review-decision state failed exact durable verification."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _identifier(value: object, field_name: str, *, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value.strip() != value
        or any(character.isspace() for character in value)
        or any(ord(character) < 32 for character in value)
    ):
        raise SQLiteCuratorReviewDecisionV2Error(f"{field_name}_invalid")
    return value


def _positive_revision(value: object, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise SQLiteCuratorReviewDecisionV2Error(f"{field_name}_invalid")
    return value


def _non_negative(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise SQLiteCuratorReviewDecisionV2IntegrityError(f"{field_name}_invalid")
    return value


def _reason(value: object) -> str:
    if not isinstance(value, str) or len(value) > 8192 or "\x00" in value:
        raise SQLiteCuratorReviewDecisionV2Error("decision_reason_invalid")
    return value


def _required_sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise SQLiteCuratorReviewDecisionV2IntegrityError(f"{field_name}_invalid")
    return value


def _resource(value: object, field_name: str) -> ResourceRef:
    try:
        return value if isinstance(value, ResourceRef) else ResourceRef.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise SQLiteCuratorReviewDecisionV2IntegrityError(
            f"{field_name}_invalid"
        ) from exc


def _canonical_mapping(value: object, field_name: str) -> Mapping[str, Any]:
    try:
        raw = bytes(value)
        decoded = json.loads(raw.decode("utf-8"))
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SQLiteCuratorReviewDecisionV2IntegrityError(
            f"{field_name}_invalid"
        ) from exc
    if not isinstance(decoded, Mapping) or _canonical_json_bytes(decoded) != raw:
        raise SQLiteCuratorReviewDecisionV2IntegrityError(
            f"{field_name}_not_canonical"
        )
    return decoded


@dataclass(frozen=True, slots=True)
class MemoryReviewDecisionReceipt:
    """Immutable receipt for one atomic review decision."""

    binding_id: str
    owner_chat_id: str
    target_space_id: str
    review_ref: ResourceRef
    proposal_ref: ResourceRef
    decision: MemoryReviewDecisionAction
    status: MemoryReviewDecisionStatus
    candidate_ref: ResourceRef
    candidate_binding_revision_before: int
    candidate_binding_revision_after: int
    job_id: str
    job_revision_before: int
    job_revision_after: int
    target_entry_ref: ResourceRef
    applied_entry_ref: ResourceRef | None
    space_revision_before: int
    space_revision_after: int
    decision_reason: str
    operation_id: str
    payload_sha256: str
    decided_at_ms: int
    replayed: bool = False

    def __post_init__(self) -> None:
        for field_name in ("binding_id", "owner_chat_id", "target_space_id", "job_id"):
            _identifier(getattr(self, field_name), field_name)
        _identifier(self.operation_id, "operation_id", maximum=256)
        _reason(self.decision_reason)
        _required_sha256(self.payload_sha256, "payload_sha256")
        _non_negative(self.decided_at_ms, "decided_at_ms")
        if not isinstance(self.decision, MemoryReviewDecisionAction):
            object.__setattr__(
                self, "decision", MemoryReviewDecisionAction(self.decision)
            )
        if not isinstance(self.status, MemoryReviewDecisionStatus):
            object.__setattr__(self, "status", MemoryReviewDecisionStatus(self.status))
        for field_name in (
            "candidate_binding_revision_before",
            "candidate_binding_revision_after",
            "job_revision_before",
            "job_revision_after",
            "space_revision_before",
            "space_revision_after",
        ):
            _positive_revision(getattr(self, field_name), field_name)
        if not isinstance(self.replayed, bool):
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "receipt_replayed_invalid"
            )
        expected_status = (
            MemoryReviewDecisionStatus.APPLIED
            if self.decision is MemoryReviewDecisionAction.APPLY
            else MemoryReviewDecisionStatus.REJECTED
        )
        if self.status is not expected_status:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_status_changed"
            )
        if (
            self.review_ref.kind != "memory_review"
            or self.review_ref.revision != 2
            or self.review_ref.fragment != self.target_space_id
            or self.proposal_ref.kind != "memory_review"
            or self.proposal_ref.revision != 1
            or self.proposal_ref.fragment != self.target_space_id
            or self.review_ref.resource_id != self.proposal_ref.resource_id
            or self.candidate_ref.kind != "memory_candidate"
            or bool(self.candidate_ref.fragment)
            or self.target_entry_ref.kind != "memory"
            or self.target_entry_ref.fragment != self.target_space_id
        ):
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_reference_changed"
            )
        if self.candidate_binding_revision_after != (
            self.candidate_binding_revision_before + 1
        ) or self.job_revision_after != self.job_revision_before + 1:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_successor_revision_changed"
            )
        if self.decision is MemoryReviewDecisionAction.APPLY:
            if (
                self.applied_entry_ref is None
                or self.applied_entry_ref.kind != "memory"
                or self.applied_entry_ref.fragment != self.target_space_id
                or self.applied_entry_ref.resource_id
                != self.target_entry_ref.resource_id
                or self.applied_entry_ref.revision
                != self.target_entry_ref.revision + 1
                or self.space_revision_after != self.space_revision_before + 1
            ):
                raise SQLiteCuratorReviewDecisionV2IntegrityError(
                    "applied_review_decision_receipt_changed"
                )
        elif (
            self.applied_entry_ref is not None
            or self.space_revision_after != self.space_revision_before
        ):
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "rejected_review_decision_receipt_changed"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": _RECEIPT_SCHEMA,
            "binding_id": self.binding_id,
            "owner_chat_id": self.owner_chat_id,
            "target_space_id": self.target_space_id,
            "review_ref": self.review_ref.to_dict(),
            "proposal_ref": self.proposal_ref.to_dict(),
            "decision": self.decision.value,
            "status": self.status.value,
            "candidate_ref": self.candidate_ref.to_dict(),
            "candidate_binding_revision_before": self.candidate_binding_revision_before,
            "candidate_binding_revision_after": self.candidate_binding_revision_after,
            "job_id": self.job_id,
            "job_revision_before": self.job_revision_before,
            "job_revision_after": self.job_revision_after,
            "target_entry_ref": self.target_entry_ref.to_dict(),
            "applied_entry_ref": (
                self.applied_entry_ref.to_dict()
                if self.applied_entry_ref is not None
                else None
            ),
            "space_revision_before": self.space_revision_before,
            "space_revision_after": self.space_revision_after,
            "decision_reason": self.decision_reason,
            "operation_id": self.operation_id,
            "payload_sha256": self.payload_sha256,
            "decided_at_ms": self.decided_at_ms,
            "replayed": self.replayed,
        }


def _receipt_from_dict(value: Mapping[str, Any]) -> MemoryReviewDecisionReceipt:
    raw = dict(value)
    required = {
        "schema",
        "binding_id",
        "owner_chat_id",
        "target_space_id",
        "review_ref",
        "proposal_ref",
        "decision",
        "status",
        "candidate_ref",
        "candidate_binding_revision_before",
        "candidate_binding_revision_after",
        "job_id",
        "job_revision_before",
        "job_revision_after",
        "target_entry_ref",
        "applied_entry_ref",
        "space_revision_before",
        "space_revision_after",
        "decision_reason",
        "operation_id",
        "payload_sha256",
        "decided_at_ms",
        "replayed",
    }
    if set(raw) != required or raw.get("schema") != _RECEIPT_SCHEMA:
        raise SQLiteCuratorReviewDecisionV2IntegrityError(
            "review_decision_receipt_shape_changed"
        )
    try:
        receipt = MemoryReviewDecisionReceipt(
            binding_id=raw["binding_id"],
            owner_chat_id=raw["owner_chat_id"],
            target_space_id=raw["target_space_id"],
            review_ref=_resource(raw["review_ref"], "review_ref"),
            proposal_ref=_resource(raw["proposal_ref"], "proposal_ref"),
            decision=MemoryReviewDecisionAction(raw["decision"]),
            status=MemoryReviewDecisionStatus(raw["status"]),
            candidate_ref=_resource(raw["candidate_ref"], "candidate_ref"),
            candidate_binding_revision_before=raw[
                "candidate_binding_revision_before"
            ],
            candidate_binding_revision_after=raw[
                "candidate_binding_revision_after"
            ],
            job_id=raw["job_id"],
            job_revision_before=raw["job_revision_before"],
            job_revision_after=raw["job_revision_after"],
            target_entry_ref=_resource(raw["target_entry_ref"], "target_entry_ref"),
            applied_entry_ref=(
                _resource(raw["applied_entry_ref"], "applied_entry_ref")
                if raw["applied_entry_ref"] is not None
                else None
            ),
            space_revision_before=raw["space_revision_before"],
            space_revision_after=raw["space_revision_after"],
            decision_reason=raw["decision_reason"],
            operation_id=raw["operation_id"],
            payload_sha256=raw["payload_sha256"],
            decided_at_ms=raw["decided_at_ms"],
            replayed=raw["replayed"],
        )
    except SQLiteCuratorReviewDecisionV2IntegrityError:
        raise
    except (SQLiteCuratorReviewDecisionV2Error, TypeError, ValueError) as exc:
        raise SQLiteCuratorReviewDecisionV2IntegrityError(
            "review_decision_receipt_invalid"
        ) from exc
    if _canonical_json_bytes(receipt.to_dict()) != _canonical_json_bytes(raw):
        raise SQLiteCuratorReviewDecisionV2IntegrityError(
            "review_decision_receipt_not_canonical"
        )
    return receipt


class SQLiteCuratorReviewDecisionV2Store:
    """Own the schema and bind one atomic decision capability."""

    def __init__(
        self,
        *,
        database_path: str | Path,
        object_directory: str | Path,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.object_directory = Path(object_directory)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.object_directory.mkdir(parents=True, exist_ok=True)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        try:
            self._curator_store = SQLiteCuratorV2Store(
                database_path=self.database_path,
                object_directory=self.object_directory,
                clock_ms=self._clock_ms,
            )
            self._memory_store = SQLiteMemoryV2Store(
                database_path=self.database_path,
                object_directory=self.object_directory,
            )
            self._query_store = SQLiteCuratorQueryV2Store(
                database_path=self.database_path,
                object_directory=self.object_directory,
            )
            self._initialize()
        except SQLiteCuratorReviewDecisionV2Error:
            raise
        except (
            SQLiteCuratorV2IntegrityError,
            SQLiteMemoryV2StoreIntegrityError,
            SQLiteCuratorQueryV2IntegrityError,
        ) as exc:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(str(exc)) from exc
        except (
            sqlite3.Error,
            CurationRepositoryError,
            SQLiteMemoryV2StoreError,
            OSError,
        ) as exc:
            raise SQLiteCuratorReviewDecisionV2Error(
                "review_decision_store_unavailable"
            ) from exc

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

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).casefold() != "wal":
                raise SQLiteCuratorReviewDecisionV2IntegrityError(
                    "sqlite_wal_unavailable"
                )
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS curator_review_decision_v2_schema (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT OR IGNORE INTO curator_review_decision_v2_schema(version)
                VALUES (1);

                CREATE TABLE IF NOT EXISTS memory_review_decisions (
                    binding_id TEXT NOT NULL,
                    review_id TEXT NOT NULL,
                    decision_revision INTEGER NOT NULL
                        CHECK(decision_revision = 2),
                    target_space_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('apply', 'reject')),
                    receipt_json BLOB NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    decided_at_ms INTEGER NOT NULL CHECK(decided_at_ms >= 0),
                    PRIMARY KEY (binding_id, review_id),
                    UNIQUE (binding_id, operation_id),
                    FOREIGN KEY (binding_id) REFERENCES curation_scopes(binding_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS memory_review_operation_receipts (
                    binding_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    review_id TEXT NOT NULL,
                    decision_revision INTEGER NOT NULL
                        CHECK(decision_revision = 2),
                    receipt_sha256 TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
                    PRIMARY KEY (binding_id, operation_id),
                    FOREIGN KEY (binding_id, review_id)
                        REFERENCES memory_review_decisions(binding_id, review_id)
                        ON DELETE CASCADE
                );
                COMMIT;
                """
            )
            versions = {
                int(row[0])
                for row in connection.execute(
                    "SELECT version FROM curator_review_decision_v2_schema"
                )
            }
            if versions != {_SCHEMA_VERSION}:
                raise SQLiteCuratorReviewDecisionV2IntegrityError(
                    "review_decision_schema_unsupported"
                )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def bind(
        self,
        *,
        binding_id: str,
        owner_chat_id: str,
        target_space_id: str,
    ) -> SQLiteBoundCuratorReviewDecisionV2:
        binding = _identifier(binding_id, "binding_id")
        owner = _identifier(owner_chat_id, "owner_chat_id")
        space_id = _identifier(target_space_id, "target_space_id")
        try:
            query = self._query_store.bind(
                binding_id=binding,
                owner_chat_id=owner,
                target_space_id=space_id,
            )
            curator = self._curator_store.bind_curation(
                binding_id=binding,
                owner_chat_id=owner,
                target_space_id=space_id,
            )
            space = self._memory_store._load_space(
                space_id=space_id,
                owner_chat_id=owner,
            )
            workspace = self._memory_store.bind_workspace(
                space=space,
                owner_chat_id=owner,
            )
        except SQLiteCuratorQueryV2IntegrityError as exc:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(str(exc)) from exc
        except SQLiteCuratorQueryV2Error as exc:
            raise SQLiteCuratorReviewDecisionV2Error(str(exc)) from exc
        except (
            SQLiteCuratorV2IntegrityError,
            SQLiteMemoryV2StoreIntegrityError,
        ) as exc:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(str(exc)) from exc
        except (
            CurationRepositoryError,
            WorkspaceRepositoryError,
            sqlite3.Error,
        ) as exc:
            raise SQLiteCuratorReviewDecisionV2Error(
                "review_decision_scope_unavailable"
            ) from exc
        return SQLiteBoundCuratorReviewDecisionV2(
            store=self,
            binding_id=binding,
            owner_chat_id=owner,
            target_space_id=space_id,
            query=query,
            curator=curator,
            workspace=workspace,
        )


class SQLiteBoundCuratorReviewDecisionV2:
    """One exact chat/space authority for review decisions."""

    def __init__(
        self,
        *,
        store: SQLiteCuratorReviewDecisionV2Store,
        binding_id: str,
        owner_chat_id: str,
        target_space_id: str,
        query: Any,
        curator: Any,
        workspace: Any,
    ) -> None:
        self._store = store
        self.binding_id = binding_id
        self.owner_chat_id = owner_chat_id
        self.target_space_id = target_space_id
        self._query = query
        self._curator = curator
        self._workspace = workspace

    @staticmethod
    def _workspace_kind(candidate: FrozenCandidateSnapshot) -> MemoryEntryKind:
        if candidate.kind == "folder":
            return MemoryEntryKind.FOLDER
        if candidate.kind == "link":
            return MemoryEntryKind.LINK
        if candidate.kind != "file":
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "candidate_storage_kind_invalid"
            )
        if candidate.media_type == "text/markdown":
            return MemoryEntryKind.MARKDOWN
        if candidate.media_type.startswith("image/"):
            return MemoryEntryKind.IMAGE
        raise SQLiteCuratorReviewDecisionV2IntegrityError(
            "candidate_media_type_unsupported"
        )

    def _payload_sha256(
        self,
        *,
        review_id: str,
        decision: MemoryReviewDecisionAction,
        expected_review_revision: int,
        expected_candidate_revision: int,
        expected_target_revision: int,
        expected_space_revision: int,
        decision_reason: str,
    ) -> str:
        return _sha256(
            _canonical_json_bytes(
                {
                    "schema": _REQUEST_SCHEMA,
                    "binding_id": self.binding_id,
                    "owner_chat_id": self.owner_chat_id,
                    "target_space_id": self.target_space_id,
                    "review_id": review_id,
                    "decision": decision.value,
                    "expected_review_revision": expected_review_revision,
                    "expected_candidate_revision": expected_candidate_revision,
                    "expected_target_revision": expected_target_revision,
                    "expected_space_revision": expected_space_revision,
                    "decision_reason": decision_reason,
                }
            )
        )

    def _decision_receipt(
        self,
        connection: sqlite3.Connection,
        *,
        review_id: str,
    ) -> MemoryReviewDecisionReceipt | None:
        row = connection.execute(
            """
            SELECT * FROM memory_review_decisions
            WHERE binding_id = ? AND review_id = ?
            """,
            (self.binding_id, review_id),
        ).fetchone()
        if row is None:
            return None
        try:
            raw = bytes(row["receipt_json"])
            decoded = json.loads(raw.decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_receipt_invalid"
            ) from exc
        if (
            not isinstance(decoded, Mapping)
            or _sha256(raw) != row["receipt_sha256"]
            or _canonical_json_bytes(decoded) != raw
        ):
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_receipt_digest_changed"
            )
        receipt = _receipt_from_dict(decoded)
        if (
            receipt.binding_id != self.binding_id
            or receipt.owner_chat_id != self.owner_chat_id
            or receipt.target_space_id != self.target_space_id
            or receipt.review_ref.resource_id != review_id
            or row["target_space_id"] != self.target_space_id
            or row["decision_revision"] != receipt.review_ref.revision
            or row["operation_id"] != receipt.operation_id
            or row["payload_sha256"] != receipt.payload_sha256
            or row["decision"] != receipt.decision.value
            or row["decided_at_ms"] != receipt.decided_at_ms
            or receipt.replayed
        ):
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_receipt_changed"
            )
        return receipt

    def _operation_replay(
        self,
        connection: sqlite3.Connection,
        *,
        operation_id: str,
        payload_sha256: str,
    ) -> MemoryReviewDecisionReceipt | None:
        row = connection.execute(
            """
            SELECT * FROM memory_review_operation_receipts
            WHERE binding_id = ? AND operation_id = ?
            """,
            (self.binding_id, operation_id),
        ).fetchone()
        if row is None:
            return None
        if row["payload_sha256"] != payload_sha256:
            raise SQLiteCuratorReviewDecisionV2Conflict(
                "review_decision_operation_payload_conflict"
            )
        receipt = self._decision_receipt(
            connection,
            review_id=row["review_id"],
        )
        if (
            receipt is None
            or row["decision_revision"] != receipt.review_ref.revision
            or row["receipt_sha256"]
            != _sha256(_canonical_json_bytes(receipt.to_dict()))
            or row["created_at_ms"] != receipt.decided_at_ms
            or receipt.operation_id != operation_id
        ):
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_operation_receipt_changed"
            )
        self._verify_decision_successors(connection, receipt=receipt)
        return replace(receipt, replayed=True)

    def _proposal(
        self,
        connection: sqlite3.Connection,
        *,
        review_id: str,
    ) -> tuple[sqlite3.Row, FrozenCandidateSnapshot, MemoryEntry]:
        publication = self._query._verified_review_publication(
            connection,
            review_id=review_id,
        )
        if publication is None:
            raise SQLiteCuratorReviewDecisionV2Conflict(
                "memory_review_not_published"
            )
        row = connection.execute(
            "SELECT * FROM memory_review_proposals WHERE review_id = ?",
            (review_id,),
        ).fetchone()
        if row is None:
            raise SQLiteCuratorReviewDecisionV2Error(
                "memory_review_not_found"
            )
        if row["mode"] != "overwrite":
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "memory_review_mode_unsupported"
            )
        semantic = _decode_record(row["semantic_json"], row["semantic_sha256"])
        try:
            target = MemoryEntry.from_dict(semantic["target"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "memory_review_target_invalid"
            ) from exc
        if _path_key(publication.candidate.target_path) != _path_key(target.path):
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "memory_review_target_path_changed"
            )
        return row, publication.candidate, target

    def _verify_candidate_successor(
        self,
        connection: sqlite3.Connection,
        *,
        receipt: MemoryReviewDecisionReceipt,
        published: FrozenCandidateSnapshot,
    ) -> FrozenCandidateSnapshot:
        if receipt.candidate_binding_revision_before != published.binding_revision:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_candidate_before_changed"
            )
        expected = (
            published.with_outcome(
                CandidateStatus.APPLIED,
                result_ref=receipt.applied_entry_ref,
            )
            if receipt.decision is MemoryReviewDecisionAction.APPLY
            else published.with_outcome(CandidateStatus.REJECTED)
        )
        row = connection.execute(
            """
            SELECT b.*, r.operation_id AS candidate_operation_id,
                   r.created_at_ms AS candidate_created_at_ms
            FROM candidate_bindings AS b
            JOIN candidate_revisions AS r
              ON r.candidate_id = b.candidate_id
             AND r.record_revision = b.candidate_record_revision
            WHERE b.candidate_id = ? AND b.binding_revision = ?
            """,
            (
                receipt.candidate_ref.resource_id,
                receipt.candidate_binding_revision_after,
            ),
        ).fetchone()
        if row is None:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_candidate_successor_missing"
            )
        actual = self._curator._candidate_revision(
            connection,
            candidate_id=receipt.candidate_ref.resource_id,
            record_revision=row["candidate_record_revision"],
        )
        raw_result_ref = row["result_ref_json"]
        result_ref = (
            _resource(
                _canonical_mapping(raw_result_ref, "candidate_result_ref"),
                "candidate_result_ref",
            )
            if raw_result_ref is not None
            else None
        )
        review_diff = _canonical_mapping(
            row["review_diff_json"],
            "candidate_review_diff",
        )
        if (
            actual != expected
            or actual.candidate_ref != receipt.candidate_ref
            or actual.binding_revision
            != receipt.candidate_binding_revision_after
            or row["job_id"] != receipt.job_id
            or row["target_space_id"] != self.target_space_id
            or row["status"] != actual.outcome.value
            or result_ref != actual.result_ref
            or review_diff != {}
            or row["error_code"] != ""
            or row["candidate_operation_id"] != receipt.operation_id
            or row["candidate_created_at_ms"] != receipt.decided_at_ms
            or row["created_at_ms"] != receipt.decided_at_ms
        ):
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_candidate_successor_changed"
            )
        head = self._curator._current_candidate(
            connection,
            candidate_id=receipt.candidate_ref.resource_id,
        )
        if head.binding_revision < receipt.candidate_binding_revision_after:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_candidate_head_rolled_back"
            )
        return actual

    def _verify_job_successor(
        self,
        connection: sqlite3.Connection,
        *,
        receipt: MemoryReviewDecisionReceipt,
        published: FrozenCandidateSnapshot,
        candidate_after: FrozenCandidateSnapshot,
    ) -> None:
        before = self._curator._job_revision(
            connection,
            job_id=receipt.job_id,
            revision=receipt.job_revision_before,
        )
        after = self._curator._job_revision(
            connection,
            job_id=receipt.job_id,
            revision=receipt.job_revision_after,
        )
        if (
            before.status is not ConsolidationJobStatus.COMPLETED
            or before.candidates.count(published) != 1
        ):
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_job_before_changed"
            )
        expected_candidates = tuple(
            candidate_after
            if item.candidate_ref == published.candidate_ref
            else item
            for item in before.candidates
        )
        expected_after = replace(
            before,
            candidates=expected_candidates,
            revision=before.revision + 1,
            operation_id=receipt.operation_id,
            updated_at_ms=max(receipt.decided_at_ms, before.updated_at_ms),
        )
        row = connection.execute(
            """
            SELECT operation_id, created_at_ms
            FROM consolidation_job_revisions
            WHERE job_id = ? AND revision = ?
            """,
            (receipt.job_id, receipt.job_revision_after),
        ).fetchone()
        if (
            after != expected_after
            or after.candidates.count(candidate_after) != 1
            or row is None
            or row["operation_id"] != receipt.operation_id
            or row["created_at_ms"] != receipt.decided_at_ms
        ):
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_job_successor_changed"
            )
        head = self._curator._current_job(
            connection,
            job_id=receipt.job_id,
        )
        if head.revision < receipt.job_revision_after:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_job_head_rolled_back"
            )

    def _verify_applied_entry_successor(
        self,
        connection: sqlite3.Connection,
        *,
        receipt: MemoryReviewDecisionReceipt,
        published: FrozenCandidateSnapshot,
        target: MemoryEntry,
    ) -> None:
        applied_ref = receipt.applied_entry_ref
        if applied_ref is None:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_applied_entry_missing"
            )
        row = self._workspace._entry_revision_row(
            connection,
            entry_id=applied_ref.resource_id,
            revision=applied_ref.revision,
        )
        if row is None:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_applied_entry_missing"
            )
        entry = self._workspace._entry_from_row(row)
        kind = self._workspace_kind(published)
        content_ref = (
            ResourceRef(
                "memory_content",
                f"{target.entry_id}-content",
                target.revision + 1,
                self.target_space_id,
            )
            if kind in {MemoryEntryKind.MARKDOWN, MemoryEntryKind.IMAGE}
            else None
        )
        expected = MemoryEntry(
            entry_id=target.entry_id,
            space_id=self.target_space_id,
            path=published.target_path,
            name=published.name,
            description=published.description,
            kind=kind,
            revision=target.revision + 1,
            updated_seq=receipt.space_revision_after,
            content_ref=content_ref,
            source_refs=published.source_refs,
            tags=(),
            media_type=(
                published.media_type
                if kind in {MemoryEntryKind.MARKDOWN, MemoryEntryKind.IMAGE}
                else ""
            ),
            link_url=(published.link_url if kind is MemoryEntryKind.LINK else ""),
            deleted=False,
        )
        object_sha256 = row["object_sha256"]
        byte_length = row["byte_length"]
        if (
            entry != expected
            or applied_ref.resource_id != target.entry_id
            or row["operation_id"] != receipt.operation_id
        ):
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_applied_entry_changed"
            )
        if kind in {MemoryEntryKind.MARKDOWN, MemoryEntryKind.IMAGE}:
            if (
                object_sha256 != published.content_sha256
                or byte_length != published.byte_length
            ):
                raise SQLiteCuratorReviewDecisionV2IntegrityError(
                    "review_decision_applied_object_changed"
                )
            self._store._memory_store._read_object(
                digest=object_sha256,
                byte_length=byte_length,
            )
        elif object_sha256 is not None or byte_length is not None:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_applied_object_changed"
            )
        head = connection.execute(
            """
            SELECT current_revision FROM entries
            WHERE space_id = ? AND entry_id = ?
            """,
            (self.target_space_id, target.entry_id),
        ).fetchone()
        if head is None or head["current_revision"] < applied_ref.revision:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_entry_head_rolled_back"
            )

    def _verify_decision_successors(
        self,
        connection: sqlite3.Connection,
        *,
        receipt: MemoryReviewDecisionReceipt,
    ) -> None:
        try:
            proposal_row, published, target = self._proposal(
                connection,
                review_id=receipt.proposal_ref.resource_id,
            )
        except SQLiteCuratorReviewDecisionV2IntegrityError:
            raise
        except (
            SQLiteCuratorQueryV2Error,
            SQLiteCuratorReviewDecisionV2Error,
        ) as exc:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_publication_proof_changed"
            ) from exc
        if (
            receipt.proposal_ref.resource_id != receipt.review_ref.resource_id
            or proposal_row["job_id"] != receipt.job_id
            or published.candidate_ref != receipt.candidate_ref
            or target.entry_id != receipt.target_entry_ref.resource_id
            or target.revision != receipt.target_entry_ref.revision
        ):
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_proposal_binding_changed"
            )
        try:
            candidate_after = self._verify_candidate_successor(
                connection,
                receipt=receipt,
                published=published,
            )
            self._verify_job_successor(
                connection,
                receipt=receipt,
                published=published,
                candidate_after=candidate_after,
            )
            if receipt.decision is MemoryReviewDecisionAction.APPLY:
                self._verify_applied_entry_successor(
                    connection,
                    receipt=receipt,
                    published=published,
                    target=target,
                )
        except SQLiteCuratorReviewDecisionV2IntegrityError:
            raise
        except (
            CurationRepositoryError,
            RepositoryConflictError,
            SQLiteCuratorV2IntegrityError,
            SQLiteMemoryV2StoreError,
            WorkspaceRepositoryError,
        ) as exc:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_successor_proof_changed"
            ) from exc
        current_space_revision = int(self._space_row(connection)["revision"])
        if current_space_revision < receipt.space_revision_after:
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_space_head_rolled_back"
            )

    def _space_row(self, connection: sqlite3.Connection) -> sqlite3.Row:
        try:
            return self._workspace._load_space_row(connection)
        except Exception as exc:
            if isinstance(exc, SQLiteCuratorReviewDecisionV2Error):
                raise
            raise SQLiteCuratorReviewDecisionV2IntegrityError(
                "review_decision_workspace_scope_changed"
            ) from exc

    def _apply_workspace(
        self,
        connection: sqlite3.Connection,
        *,
        candidate: FrozenCandidateSnapshot,
        target: MemoryEntry,
        expected_space_revision: int,
        operation_id: str,
    ) -> tuple[MemoryEntry, int, int]:
        space_row = self._space_row(connection)
        actual_space_revision = int(space_row["revision"])
        if actual_space_revision != expected_space_revision:
            raise SQLiteCuratorReviewDecisionV2Conflict(
                "space_revision_conflict",
                expected_revision=expected_space_revision,
                actual_revision=actual_space_revision,
            )
        current_row = self._workspace._current_entry_row(
            connection,
            entry_id=target.entry_id,
        )
        if current_row is None:
            raise SQLiteCuratorReviewDecisionV2Conflict(
                "target_revision_conflict",
                expected_revision=target.revision,
                actual_revision=None,
            )
        current = self._workspace._entry_from_row(current_row)
        if current != target:
            raise SQLiteCuratorReviewDecisionV2Conflict(
                "target_revision_conflict",
                expected_revision=target.revision,
                actual_revision=current.revision,
            )
        collision = connection.execute(
            """
            SELECT entry_id FROM entries
            WHERE space_id = ? AND path_key = ? AND deleted = 0
              AND entry_id != ?
            LIMIT 1
            """,
            (
                self.target_space_id,
                _path_key(candidate.target_path),
                target.entry_id,
            ),
        ).fetchone()
        if collision is not None:
            raise SQLiteCuratorReviewDecisionV2Conflict(
                "target_path_conflict"
            )

        kind = self._workspace_kind(candidate)
        next_revision = target.revision + 1
        content_ref = None
        object_sha256 = None
        byte_length = None
        if kind in {MemoryEntryKind.MARKDOWN, MemoryEntryKind.IMAGE}:
            object_sha256 = _required_sha256(
                candidate.content_sha256,
                "candidate_content_sha256",
            )
            byte_length = candidate.byte_length
            object_row = connection.execute(
                "SELECT byte_length FROM objects WHERE sha256 = ?",
                (object_sha256,),
            ).fetchone()
            if object_row is None or object_row["byte_length"] != byte_length:
                raise SQLiteCuratorReviewDecisionV2IntegrityError(
                    "candidate_object_metadata_changed"
                )
            self._store._memory_store._read_object(
                digest=object_sha256,
                byte_length=byte_length,
            )
            content_ref = ResourceRef(
                "memory_content",
                f"{target.entry_id}-content",
                next_revision,
                self.target_space_id,
            )
        applied = MemoryEntry(
            entry_id=target.entry_id,
            space_id=self.target_space_id,
            path=candidate.target_path,
            name=candidate.name,
            description=candidate.description,
            kind=kind,
            revision=next_revision,
            updated_seq=actual_space_revision + 1,
            content_ref=content_ref,
            source_refs=candidate.source_refs,
            tags=(),
            media_type=(
                candidate.media_type
                if kind in {MemoryEntryKind.MARKDOWN, MemoryEntryKind.IMAGE}
                else ""
            ),
            link_url=(candidate.link_url if kind is MemoryEntryKind.LINK else ""),
            deleted=False,
        )
        self._workspace._write_entry_revision(
            connection,
            entry=applied,
            operation_id=operation_id,
            object_sha256=object_sha256,
            byte_length=byte_length,
        )
        advanced = self._workspace._advance_space(
            connection,
            current_row=space_row,
            expected_revision=actual_space_revision,
        )
        return applied, actual_space_revision, advanced.revision

    def decide(
        self,
        *,
        review_id: str,
        decision: str | MemoryReviewDecisionAction,
        expected_review_revision: int,
        expected_candidate_revision: int,
        expected_target_revision: int,
        expected_space_revision: int,
        decision_reason: str,
        operation_id: str,
    ) -> MemoryReviewDecisionReceipt:
        normalized_review_id = _identifier(review_id, "review_id")
        try:
            normalized_decision = MemoryReviewDecisionAction(
                str(decision).strip().casefold()
            )
        except ValueError as exc:
            raise SQLiteCuratorReviewDecisionV2Error(
                "review_decision_invalid"
            ) from exc
        review_revision = _positive_revision(
            expected_review_revision, "expected_review_revision"
        )
        candidate_revision = _positive_revision(
            expected_candidate_revision, "expected_candidate_revision"
        )
        target_revision = _positive_revision(
            expected_target_revision, "expected_target_revision"
        )
        space_revision = _positive_revision(
            expected_space_revision, "expected_space_revision"
        )
        normalized_reason = _reason(decision_reason)
        normalized_operation = _identifier(
            operation_id, "operation_id", maximum=256
        )
        payload_sha256 = self._payload_sha256(
            review_id=normalized_review_id,
            decision=normalized_decision,
            expected_review_revision=review_revision,
            expected_candidate_revision=candidate_revision,
            expected_target_revision=target_revision,
            expected_space_revision=space_revision,
            decision_reason=normalized_reason,
        )
        decided_at_ms = _non_negative(self._store._clock_ms(), "clock_ms")

        connection = self._store._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._query._scope_row(connection)
            replay = self._operation_replay(
                connection,
                operation_id=normalized_operation,
                payload_sha256=payload_sha256,
            )
            if replay is not None:
                connection.commit()
                return replay
            if self._decision_receipt(
                connection,
                review_id=normalized_review_id,
            ) is not None:
                raise SQLiteCuratorReviewDecisionV2Conflict(
                    "memory_review_already_decided"
                )

            proposal_row, published_candidate, target = self._proposal(
                connection,
                review_id=normalized_review_id,
            )
            if review_revision != 1:
                raise SQLiteCuratorReviewDecisionV2Conflict(
                    "review_revision_conflict",
                    expected_revision=review_revision,
                    actual_revision=1,
                )
            if candidate_revision != published_candidate.binding_revision:
                raise SQLiteCuratorReviewDecisionV2Conflict(
                    "candidate_revision_conflict",
                    expected_revision=candidate_revision,
                    actual_revision=published_candidate.binding_revision,
                )
            if target_revision != target.revision:
                raise SQLiteCuratorReviewDecisionV2Conflict(
                    "target_revision_conflict",
                    expected_revision=target_revision,
                    actual_revision=target.revision,
                )
            current_candidate = self._curator._current_candidate(
                connection,
                candidate_id=published_candidate.candidate_ref.resource_id,
            )
            if current_candidate != published_candidate:
                raise SQLiteCuratorReviewDecisionV2Conflict(
                    "candidate_revision_conflict",
                    expected_revision=published_candidate.binding_revision,
                    actual_revision=current_candidate.binding_revision,
                )
            current_job = self._curator._current_job(
                connection,
                job_id=proposal_row["job_id"],
            )
            if (
                current_job.status is not ConsolidationJobStatus.COMPLETED
                or current_candidate not in current_job.candidates
            ):
                raise SQLiteCuratorReviewDecisionV2Conflict(
                    "consolidation_job_revision_conflict"
                )

            space_row = self._space_row(connection)
            space_before = int(space_row["revision"])
            applied_entry = None
            space_after = space_before
            if normalized_decision is MemoryReviewDecisionAction.APPLY:
                applied_entry, space_before, space_after = self._apply_workspace(
                    connection,
                    candidate=current_candidate,
                    target=target,
                    expected_space_revision=space_revision,
                    operation_id=normalized_operation,
                )
                result_ref = ResourceRef(
                    "memory",
                    applied_entry.entry_id,
                    applied_entry.revision,
                    self.target_space_id,
                )
                candidate_after = current_candidate.with_outcome(
                    CandidateStatus.APPLIED,
                    result_ref=result_ref,
                )
                status = MemoryReviewDecisionStatus.APPLIED
            else:
                candidate_after = current_candidate.with_outcome(
                    CandidateStatus.REJECTED
                )
                status = MemoryReviewDecisionStatus.REJECTED

            self._curator._write_candidate_transition(
                connection,
                before=current_candidate,
                after=candidate_after,
                job_id=current_job.job_id,
                operation_id=normalized_operation,
                now_ms=decided_at_ms,
            )
            replacements = tuple(
                candidate_after
                if item.candidate_ref == current_candidate.candidate_ref
                else item
                for item in current_job.candidates
            )
            if replacements.count(candidate_after) != 1:
                raise SQLiteCuratorReviewDecisionV2IntegrityError(
                    "review_decision_job_candidate_ambiguous"
                )
            job_after = replace(
                current_job,
                candidates=replacements,
                revision=current_job.revision + 1,
                operation_id=normalized_operation,
                updated_at_ms=max(decided_at_ms, current_job.updated_at_ms),
            )
            self._curator._transition_job(
                connection,
                before=current_job,
                after=job_after,
                operation_id=normalized_operation,
                now_ms=decided_at_ms,
            )

            proposal_ref = ResourceRef(
                "memory_review",
                normalized_review_id,
                1,
                self.target_space_id,
            )
            target_ref = ResourceRef(
                "memory",
                target.entry_id,
                target.revision,
                self.target_space_id,
            )
            applied_ref = (
                ResourceRef(
                    "memory",
                    applied_entry.entry_id,
                    applied_entry.revision,
                    self.target_space_id,
                )
                if applied_entry is not None
                else None
            )
            receipt = MemoryReviewDecisionReceipt(
                binding_id=self.binding_id,
                owner_chat_id=self.owner_chat_id,
                target_space_id=self.target_space_id,
                review_ref=ResourceRef(
                    "memory_review",
                    normalized_review_id,
                    2,
                    self.target_space_id,
                ),
                proposal_ref=proposal_ref,
                decision=normalized_decision,
                status=status,
                candidate_ref=current_candidate.candidate_ref,
                candidate_binding_revision_before=current_candidate.binding_revision,
                candidate_binding_revision_after=candidate_after.binding_revision,
                job_id=current_job.job_id,
                job_revision_before=current_job.revision,
                job_revision_after=job_after.revision,
                target_entry_ref=target_ref,
                applied_entry_ref=applied_ref,
                space_revision_before=space_before,
                space_revision_after=space_after,
                decision_reason=normalized_reason,
                operation_id=normalized_operation,
                payload_sha256=payload_sha256,
                decided_at_ms=decided_at_ms,
            )
            receipt_json = _canonical_json_bytes(receipt.to_dict())
            receipt_sha256 = _sha256(receipt_json)
            connection.execute(
                """
                INSERT INTO memory_review_decisions(
                    binding_id, review_id, decision_revision, target_space_id,
                    operation_id, payload_sha256, decision, receipt_json,
                    receipt_sha256, decided_at_ms
                ) VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.binding_id,
                    normalized_review_id,
                    self.target_space_id,
                    normalized_operation,
                    payload_sha256,
                    normalized_decision.value,
                    receipt_json,
                    receipt_sha256,
                    decided_at_ms,
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_review_operation_receipts(
                    binding_id, operation_id, payload_sha256, review_id,
                    decision_revision, receipt_sha256, created_at_ms
                ) VALUES (?, ?, ?, ?, 2, ?, ?)
                """,
                (
                    self.binding_id,
                    normalized_operation,
                    payload_sha256,
                    normalized_review_id,
                    receipt_sha256,
                    decided_at_ms,
                ),
            )
            self._verify_decision_successors(connection, receipt=receipt)
            connection.commit()
            return receipt
        except SQLiteCuratorReviewDecisionV2Error:
            connection.rollback()
            raise
        except SQLiteCuratorQueryV2IntegrityError as exc:
            connection.rollback()
            raise SQLiteCuratorReviewDecisionV2IntegrityError(str(exc)) from exc
        except SQLiteCuratorQueryV2Error as exc:
            connection.rollback()
            code = str(exc)
            if "not_found" in code or "scope_mismatch" in code:
                raise SQLiteCuratorReviewDecisionV2Error(
                    "memory_review_not_found"
                ) from exc
            raise SQLiteCuratorReviewDecisionV2Error(code) from exc
        except (
            SQLiteCuratorV2IntegrityError,
            SQLiteMemoryV2StoreIntegrityError,
        ) as exc:
            connection.rollback()
            raise SQLiteCuratorReviewDecisionV2IntegrityError(str(exc)) from exc
        except (CurationConflictError, RepositoryConflictError) as exc:
            connection.rollback()
            raise SQLiteCuratorReviewDecisionV2Conflict(str(exc)) from exc
        except CurationRepositoryError as exc:
            connection.rollback()
            raise SQLiteCuratorReviewDecisionV2IntegrityError(str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise SQLiteCuratorReviewDecisionV2Conflict(
                "review_decision_operation_conflict"
            ) from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise SQLiteCuratorReviewDecisionV2Error(
                "review_decision_write_failed"
            ) from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_decision(self, *, review_id: str) -> MemoryReviewDecisionReceipt:
        normalized = _identifier(review_id, "review_id")
        connection = self._store._connect()
        try:
            connection.execute("BEGIN")
            self._query._scope_row(connection)
            receipt = self._decision_receipt(connection, review_id=normalized)
            if receipt is None:
                raise SQLiteCuratorReviewDecisionV2Error(
                    "memory_review_decision_not_found"
                )
            self._verify_decision_successors(connection, receipt=receipt)
            connection.rollback()
            return receipt
        except SQLiteCuratorReviewDecisionV2Error:
            connection.rollback()
            raise
        except SQLiteCuratorQueryV2IntegrityError as exc:
            connection.rollback()
            raise SQLiteCuratorReviewDecisionV2IntegrityError(str(exc)) from exc
        except SQLiteCuratorQueryV2Error as exc:
            connection.rollback()
            raise SQLiteCuratorReviewDecisionV2Error(str(exc)) from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise SQLiteCuratorReviewDecisionV2Error(
                "review_decision_read_failed"
            ) from exc
        finally:
            connection.close()


__all__ = [
    "MemoryReviewDecisionAction",
    "MemoryReviewDecisionReceipt",
    "MemoryReviewDecisionStatus",
    "SQLiteBoundCuratorReviewDecisionV2",
    "SQLiteCuratorReviewDecisionV2Conflict",
    "SQLiteCuratorReviewDecisionV2Error",
    "SQLiteCuratorReviewDecisionV2IntegrityError",
    "SQLiteCuratorReviewDecisionV2Store",
]
