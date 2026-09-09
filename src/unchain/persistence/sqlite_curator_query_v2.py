"""Scope-bound, read-only queries over the durable Memory V2 curator state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator, Mapping

from unchain.journal import ResourceRef
from unchain.journal.models import _freeze_json, _required_text, _thaw_json
from unchain.memory.curator.models import (
    CandidateStatus,
    ConsolidationJob,
    ConsolidationJobStatus,
    FrozenCandidateSnapshot,
)
from unchain.memory.curator.ports import CurationRepositoryError
from unchain.memory.toolkit import MemoryToolContentPage
from unchain.memory.workspace import MemoryEntry, MemorySpace

from .sqlite_curator_v2 import (
    SQLiteCuratorV2IntegrityError,
    _candidate_from_dict,
    _canonical_json_bytes,
    _job_from_dict,
    _sha256,
)


_MAX_LIST_RESULTS = 500
_MAX_REVIEW_CONTENT_PAGE_BYTES = 128 * 1024
_REVIEW_SCHEMA = "unchain.memory_review_diff.v1"


class SQLiteCuratorQueryV2Error(CurationRepositoryError):
    """A scope-bound curator read could not be completed."""


class SQLiteCuratorQueryV2IntegrityError(SQLiteCuratorQueryV2Error):
    """Stored curator state failed exact current-head verification."""


class MemoryReviewStatus(StrEnum):
    PENDING = "pending"


@dataclass(frozen=True)
class PendingMemoryReviewProposal:
    """One immutable, user-confirmation-gated review proposal."""

    review_ref: ResourceRef
    binding_id: str
    job_id: str
    candidate_ref: ResourceRef
    binding_revision: int
    target_entry_ref: ResourceRef
    mode: str
    semantic: Mapping[str, Any]
    review_diff: Mapping[str, Any]
    first_operation_id: str
    created_at_ms: int

    @property
    def status(self) -> MemoryReviewStatus:
        return MemoryReviewStatus.PENDING

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_ref": self.review_ref.to_dict(),
            "binding_id": self.binding_id,
            "job_id": self.job_id,
            "candidate_ref": self.candidate_ref.to_dict(),
            "binding_revision": self.binding_revision,
            "target_entry_ref": self.target_entry_ref.to_dict(),
            "mode": self.mode,
            "semantic": _thaw_json(self.semantic),
            "review_diff": _thaw_json(self.review_diff),
            "first_operation_id": self.first_operation_id,
            "created_at_ms": self.created_at_ms,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class _VerifiedReviewPublication:
    """Immutable evidence that a prepared review was published to the user."""

    review_json: bytes
    review_sha256: str
    candidate: FrozenCandidateSnapshot


def _limit(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > _MAX_LIST_RESULTS
    ):
        raise ValueError(f"limit must be between 1 and {_MAX_LIST_RESULTS}")
    return value


def _positive(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SQLiteCuratorQueryV2IntegrityError(f"{field_name}_invalid")
    return value


def _non_negative(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SQLiteCuratorQueryV2IntegrityError(f"{field_name}_invalid")
    return value


def _page_limit(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > _MAX_REVIEW_CONTENT_PAGE_BYTES
    ):
        raise ValueError(
            "limit must be between 1 and "
            f"{_MAX_REVIEW_CONTENT_PAGE_BYTES}"
        )
    return value


def _required_sha256(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SQLiteCuratorQueryV2IntegrityError(f"{field_name}_invalid")
    return value


def _decode_record(raw_value: object, digest: object) -> Mapping[str, Any]:
    try:
        raw = bytes(raw_value)
    except (TypeError, ValueError) as exc:
        raise SQLiteCuratorQueryV2IntegrityError(
            "durable_record_not_bytes"
        ) from exc
    if not isinstance(digest, str) or _sha256(raw) != digest:
        raise SQLiteCuratorQueryV2IntegrityError(
            "durable_record_digest_changed"
        )
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SQLiteCuratorQueryV2IntegrityError(
            "durable_record_invalid_json"
        ) from exc
    if not isinstance(decoded, Mapping) or _canonical_json_bytes(decoded) != raw:
        raise SQLiteCuratorQueryV2IntegrityError(
            "durable_record_not_canonical"
        )
    return decoded


def _decode_unhashed_record(raw_value: object) -> Mapping[str, Any]:
    try:
        raw = bytes(raw_value)
        decoded = json.loads(raw.decode("utf-8"))
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SQLiteCuratorQueryV2IntegrityError(
            "durable_record_invalid_json"
        ) from exc
    if not isinstance(decoded, Mapping) or _canonical_json_bytes(decoded) != raw:
        raise SQLiteCuratorQueryV2IntegrityError(
            "durable_record_not_canonical"
        )
    return decoded


class SQLiteCuratorQueryV2Store:
    """Open exact-scope read capabilities without creating or repairing state."""

    def __init__(
        self,
        *,
        database_path: str | Path,
        object_directory: str | Path,
    ) -> None:
        self.database_path = Path(database_path)
        self.object_directory = Path(object_directory)

    def _connect(self) -> sqlite3.Connection:
        try:
            uri = self.database_path.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=30.0,
                isolation_level=None,
            )
        except (OSError, sqlite3.Error) as exc:
            raise SQLiteCuratorQueryV2Error(
                "curator_query_database_unavailable"
            ) from exc
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            yield connection
        finally:
            connection.rollback()
            connection.close()

    def bind(
        self,
        *,
        binding_id: str,
        owner_chat_id: str,
        target_space_id: str,
    ) -> SQLiteBoundCuratorQueryV2:
        bound = SQLiteBoundCuratorQueryV2(
            store=self,
            binding_id=_required_text(
                binding_id, "binding_id", maximum=512, identifier=True
            ),
            owner_chat_id=_required_text(
                owner_chat_id,
                "owner_chat_id",
                maximum=512,
                identifier=True,
            ),
            target_space_id=_required_text(
                target_space_id,
                "target_space_id",
                maximum=512,
                identifier=True,
            ),
        )
        try:
            with self._transaction() as connection:
                bound._scope_row(connection)
        except SQLiteCuratorQueryV2Error:
            raise
        except sqlite3.Error as exc:
            raise SQLiteCuratorQueryV2Error(
                "curator_query_scope_read_failed"
            ) from exc
        return bound


class SQLiteBoundCuratorQueryV2:
    """Read current curator heads inside exactly one chat/space binding."""

    def __init__(
        self,
        *,
        store: SQLiteCuratorQueryV2Store,
        binding_id: str,
        owner_chat_id: str,
        target_space_id: str,
    ) -> None:
        self._store = store
        self.binding_id = binding_id
        self.owner_chat_id = owner_chat_id
        self.target_space_id = target_space_id

    def _scope_row(self, connection: sqlite3.Connection) -> sqlite3.Row:
        versions = {
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM curator_v2_schema"
            )
        }
        if versions != {1}:
            raise SQLiteCuratorQueryV2IntegrityError(
                "curator_schema_version_unsupported"
            )
        row = connection.execute(
            "SELECT * FROM curation_scopes WHERE binding_id = ?",
            (self.binding_id,),
        ).fetchone()
        if (
            row is None
            or row["owner_chat_id"] != self.owner_chat_id
            or row["target_space_id"] != self.target_space_id
        ):
            raise SQLiteCuratorQueryV2Error("curation_scope_mismatch")
        space_row = connection.execute(
            "SELECT * FROM spaces WHERE space_id = ?",
            (self.target_space_id,),
        ).fetchone()
        if space_row is None:
            raise SQLiteCuratorQueryV2IntegrityError(
                "curation_workspace_scope_missing"
            )
        try:
            space = MemorySpace.from_dict(
                _decode_record(
                    space_row["space_json"],
                    space_row["space_sha256"],
                )
            )
        except (TypeError, ValueError) as exc:
            raise SQLiteCuratorQueryV2IntegrityError(
                "curation_workspace_scope_invalid"
            ) from exc
        if (
            space_row["owner_chat_id"] != self.owner_chat_id
            or space_row["namespace"] != "chat"
            or space_row["revision"] != space.revision
            or space.space_id != self.target_space_id
            or space.namespace != "chat"
            or space_row["name"] != space.name
            or space_row["description"] != space.description
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "curation_workspace_scope_changed"
            )
        _non_negative(row["created_at_ms"], "scope_created_at_ms")
        return row

    def _object(self, digest: str, byte_length: int) -> None:
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "candidate_object_digest_invalid"
            )
        try:
            content = (self._store.object_directory / digest).read_bytes()
        except OSError as exc:
            raise SQLiteCuratorQueryV2IntegrityError(
                "candidate_object_unreadable"
            ) from exc
        if len(content) != byte_length or hashlib.sha256(content).hexdigest() != digest:
            raise SQLiteCuratorQueryV2IntegrityError(
                "candidate_object_digest_changed"
            )

    def _candidate(
        self,
        connection: sqlite3.Connection,
        *,
        candidate_id: str,
    ) -> FrozenCandidateSnapshot:
        row = connection.execute(
            """
            SELECT c.*, r.snapshot_json, r.snapshot_sha256,
                   r.record_revision AS stored_record_revision
            FROM candidates AS c
            JOIN candidate_revisions AS r
              ON r.candidate_id = c.candidate_id
             AND r.record_revision = c.current_record_revision
            WHERE c.candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()
        if row is None or row["binding_id"] != self.binding_id:
            raise SQLiteCuratorQueryV2Error("candidate_scope_mismatch")
        try:
            candidate = _candidate_from_dict(
                _decode_record(row["snapshot_json"], row["snapshot_sha256"])
            )
        except SQLiteCuratorV2IntegrityError as exc:
            raise SQLiteCuratorQueryV2IntegrityError(str(exc)) from exc
        if (
            candidate.candidate_ref.resource_id != row["candidate_id"]
            or candidate.outcome.value != row["status"]
            or candidate.byte_length != row["byte_length"]
            or row["stored_record_revision"] != row["current_record_revision"]
            or row["created_at_ms"] > row["updated_at_ms"]
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "candidate_current_head_changed"
            )
        run_scope = connection.execute(
            """
            SELECT 1 FROM curation_run_scopes
            WHERE binding_id = ? AND session_id = ?
              AND attempt_id = ? AND run_id = ?
            """,
            (
                self.binding_id,
                row["session_id"],
                row["attempt_id"],
                row["run_id"],
            ),
        ).fetchone()
        if run_scope is None or candidate.source_agent_run_id != row["run_id"]:
            raise SQLiteCuratorQueryV2IntegrityError(
                "candidate_run_scope_changed"
            )
        if candidate.content_ref is None:
            if row["object_sha256"] is not None or candidate.content_sha256:
                raise SQLiteCuratorQueryV2IntegrityError(
                    "candidate_object_metadata_changed"
                )
        else:
            if (
                candidate.content_ref.kind != "memory_candidate_content"
                or candidate.content_ref.resource_id != row["candidate_id"]
                or candidate.content_ref.fragment != self.target_space_id
                or row["object_sha256"] != candidate.content_sha256
            ):
                raise SQLiteCuratorQueryV2IntegrityError(
                    "candidate_object_scope_changed"
                )
            object_row = connection.execute(
                "SELECT byte_length FROM objects WHERE sha256 = ?",
                (candidate.content_sha256,),
            ).fetchone()
            if object_row is None or object_row["byte_length"] != candidate.byte_length:
                raise SQLiteCuratorQueryV2IntegrityError(
                    "candidate_object_metadata_changed"
                )
            self._object(candidate.content_sha256, candidate.byte_length)

        bindings = connection.execute(
            """
            SELECT * FROM candidate_bindings
            WHERE candidate_id = ? ORDER BY binding_revision DESC
            """,
            (row["candidate_id"],),
        ).fetchall()
        if not candidate.is_durable_binding:
            if bindings:
                raise SQLiteCuratorQueryV2IntegrityError(
                    "candidate_binding_head_changed"
                )
            return candidate
        if candidate.target_space_id != self.target_space_id or not bindings:
            raise SQLiteCuratorQueryV2IntegrityError(
                "candidate_binding_scope_changed"
            )
        binding = bindings[0]
        result_ref = (
            ResourceRef.from_dict(
                _decode_unhashed_record(binding["result_ref_json"])
            )
            if binding["result_ref_json"] is not None
            else None
        )
        review_diff = _decode_unhashed_record(binding["review_diff_json"])
        if (
            binding["binding_revision"] != candidate.binding_revision
            or binding["target_space_id"] != self.target_space_id
            or binding["status"] != candidate.outcome.value
            or binding["candidate_record_revision"]
            != row["current_record_revision"]
            or result_ref != candidate.result_ref
            or review_diff != _thaw_json(candidate.review_diff)
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "candidate_binding_head_changed"
            )
        return candidate

    def _job(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: str,
    ) -> ConsolidationJob:
        row = connection.execute(
            """
            SELECT j.*, r.job_json, r.job_sha256,
                   r.revision AS stored_revision
            FROM consolidation_jobs AS j
            JOIN consolidation_job_revisions AS r
              ON r.job_id = j.job_id AND r.revision = j.current_revision
            WHERE j.job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None or row["binding_id"] != self.binding_id:
            raise SQLiteCuratorQueryV2Error(
                "consolidation_job_scope_mismatch"
            )
        try:
            job = _job_from_dict(
                _decode_record(row["job_json"], row["job_sha256"])
            )
        except SQLiteCuratorV2IntegrityError as exc:
            raise SQLiteCuratorQueryV2IntegrityError(str(exc)) from exc
        lease = job.lease
        header_lease = (
            row["lease_owner"],
            row["lease_token"],
            row["lease_expires_at_ms"],
        )
        expected_lease = (
            (None, None, None)
            if lease is None
            else (lease.owner, lease.token, lease.expires_at_ms)
        )
        if (
            job.job_id != row["job_id"]
            or job.revision != row["current_revision"]
            or row["stored_revision"] != row["current_revision"]
            or job.status.value != row["status"]
            or job.trigger.trigger_key != row["trigger_key"]
            or job.next_attempt_at_ms != row["next_attempt_at_ms"]
            or job.created_at_ms != row["created_at_ms"]
            or job.updated_at_ms != row["updated_at_ms"]
            or header_lease != expected_lease
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "consolidation_job_current_head_changed"
            )
        run_scope = connection.execute(
            """
            SELECT 1 FROM curation_run_scopes
            WHERE binding_id = ? AND session_id = ?
              AND attempt_id = ? AND run_id = ?
            """,
            (
                self.binding_id,
                job.trigger.session_id,
                job.trigger.attempt_id,
                job.trigger.run_id,
            ),
        ).fetchone()
        if run_scope is None:
            raise SQLiteCuratorQueryV2IntegrityError(
                "consolidation_job_run_scope_changed"
            )
        for frozen in job.candidates:
            if frozen.target_space_id != self.target_space_id:
                raise SQLiteCuratorQueryV2IntegrityError(
                    "consolidation_job_candidate_scope_changed"
                )
            current = self._candidate(
                connection,
                candidate_id=frozen.candidate_ref.resource_id,
            )
            binding = connection.execute(
                """
                SELECT job_id FROM candidate_bindings
                WHERE candidate_id = ? AND binding_revision = ?
                """,
                (frozen.candidate_ref.resource_id, frozen.binding_revision),
            ).fetchone()
            if current != frozen or binding is None or binding["job_id"] != job.job_id:
                raise SQLiteCuratorQueryV2IntegrityError(
                    "consolidation_job_candidate_head_changed"
                )
        return job

    def list_candidates(
        self,
        *,
        status: CandidateStatus | None = None,
        limit: int = 100,
    ) -> tuple[FrozenCandidateSnapshot, ...]:
        if status is not None and not isinstance(status, CandidateStatus):
            raise TypeError("status must be a CandidateStatus or None")
        page_limit = _limit(limit)
        clauses = ["binding_id = ?"]
        parameters: list[object] = [self.binding_id]
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value)
        parameters.append(page_limit)
        try:
            with self._store._transaction() as connection:
                self._scope_row(connection)
                rows = connection.execute(
                    "SELECT candidate_id FROM candidates WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY updated_at_ms DESC, candidate_id LIMIT ?",
                    tuple(parameters),
                ).fetchall()
                return tuple(
                    self._candidate(connection, candidate_id=row["candidate_id"])
                    for row in rows
                )
        except SQLiteCuratorQueryV2Error:
            raise
        except sqlite3.Error as exc:
            raise SQLiteCuratorQueryV2Error("candidate_list_failed") from exc

    def get_candidate(self, *, candidate_id: str) -> FrozenCandidateSnapshot:
        normalized = _required_text(
            candidate_id, "candidate_id", maximum=512, identifier=True
        )
        try:
            with self._store._transaction() as connection:
                self._scope_row(connection)
                return self._candidate(connection, candidate_id=normalized)
        except SQLiteCuratorQueryV2Error:
            raise
        except sqlite3.Error as exc:
            raise SQLiteCuratorQueryV2Error("candidate_read_failed") from exc

    def list_jobs(
        self,
        *,
        status: ConsolidationJobStatus | None = None,
        limit: int = 100,
    ) -> tuple[ConsolidationJob, ...]:
        if status is not None and not isinstance(status, ConsolidationJobStatus):
            raise TypeError("status must be a ConsolidationJobStatus or None")
        page_limit = _limit(limit)
        clauses = ["binding_id = ?"]
        parameters: list[object] = [self.binding_id]
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value)
        parameters.append(page_limit)
        try:
            with self._store._transaction() as connection:
                self._scope_row(connection)
                rows = connection.execute(
                    "SELECT job_id FROM consolidation_jobs WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY updated_at_ms DESC, job_id LIMIT ?",
                    tuple(parameters),
                ).fetchall()
                return tuple(
                    self._job(connection, job_id=row["job_id"])
                    for row in rows
                )
        except SQLiteCuratorQueryV2Error:
            raise
        except sqlite3.Error as exc:
            raise SQLiteCuratorQueryV2Error(
                "consolidation_job_list_failed"
            ) from exc

    def get_job(self, *, job_id: str) -> ConsolidationJob:
        normalized = _required_text(job_id, "job_id", maximum=512, identifier=True)
        try:
            with self._store._transaction() as connection:
                self._scope_row(connection)
                return self._job(connection, job_id=normalized)
        except SQLiteCuratorQueryV2Error:
            raise
        except sqlite3.Error as exc:
            raise SQLiteCuratorQueryV2Error(
                "consolidation_job_read_failed"
            ) from exc

    def _verified_review_publication(
        self,
        connection: sqlite3.Connection,
        *,
        review_id: str,
    ) -> _VerifiedReviewPublication | None:
        """Verify immutable rows proving that a prepared review was published."""

        row = connection.execute(
            "SELECT * FROM memory_review_proposals WHERE review_id = ?",
            (review_id,),
        ).fetchone()
        if (
            row is None
            or row["binding_id"] != self.binding_id
            or row["target_space_id"] != self.target_space_id
        ):
            raise SQLiteCuratorQueryV2Error(
                "memory_review_content_not_found"
            )

        semantic = _decode_record(row["semantic_json"], row["semantic_sha256"])
        review_diff = _decode_record(row["review_json"], row["review_sha256"])
        review_json = _canonical_json_bytes(review_diff)
        review_sha256 = _required_sha256(
            row["review_sha256"], "memory_review_diff_sha256"
        )
        if set(semantic) != {"binding_id", "job_id", "candidate", "target", "mode"}:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_semantic_shape_changed"
            )
        try:
            semantic_candidate = _candidate_from_dict(semantic["candidate"])
            semantic_target = MemoryEntry.from_dict(semantic["target"])
        except (SQLiteCuratorV2IntegrityError, TypeError, ValueError) as exc:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_semantic_invalid"
            ) from exc
        if (
            semantic["binding_id"] != self.binding_id
            or semantic["job_id"] != row["job_id"]
            or semantic["mode"] != row["mode"]
            or semantic_candidate.candidate_ref.resource_id != row["candidate_id"]
            or semantic_candidate.candidate_ref.revision != row["candidate_revision"]
            or semantic_candidate.binding_revision != row["binding_revision"]
            or semantic_candidate.target_space_id != self.target_space_id
            or semantic_target.space_id != self.target_space_id
            or semantic_target.entry_id != row["target_entry_id"]
            or semantic_target.revision != row["target_revision"]
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_semantic_scope_changed"
            )
        if row["review_id"] != "memory-review-" + row["semantic_sha256"][:32]:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_identity_changed"
            )
        if (
            review_diff.get("schema") != _REVIEW_SCHEMA
            or review_diff.get("mode") != row["mode"]
            or review_diff.get("requires_user_confirmation") is not True
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_diff_changed"
            )

        target_row = connection.execute(
            """
            SELECT entry_json, entry_sha256
            FROM entry_revisions
            WHERE space_id = ? AND entry_id = ? AND revision = ?
            """,
            (
                self.target_space_id,
                row["target_entry_id"],
                row["target_revision"],
            ),
        ).fetchone()
        if target_row is None:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_target_revision_missing"
            )
        try:
            historical_target = MemoryEntry.from_dict(
                _decode_record(
                    target_row["entry_json"],
                    target_row["entry_sha256"],
                )
            )
        except (TypeError, ValueError) as exc:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_target_invalid"
            ) from exc
        if historical_target != semantic_target:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_target_revision_changed"
            )

        prepared_row = connection.execute(
            """
            SELECT b.*, r.snapshot_json, r.snapshot_sha256,
                   r.operation_id AS candidate_operation_id
            FROM candidate_bindings AS b
            JOIN candidate_revisions AS r
              ON r.candidate_id = b.candidate_id
             AND r.record_revision = b.candidate_record_revision
            WHERE b.candidate_id = ? AND b.binding_revision = ?
            """,
            (row["candidate_id"], row["binding_revision"]),
        ).fetchone()
        if prepared_row is None:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_candidate_revision_missing"
            )
        try:
            prepared_candidate = _candidate_from_dict(
                _decode_record(
                    prepared_row["snapshot_json"],
                    prepared_row["snapshot_sha256"],
                )
            )
        except SQLiteCuratorV2IntegrityError as exc:
            raise SQLiteCuratorQueryV2IntegrityError(str(exc)) from exc
        try:
            prepared_result_ref = (
                ResourceRef.from_dict(
                    _decode_unhashed_record(prepared_row["result_ref_json"])
                )
                if prepared_row["result_ref_json"] is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_candidate_result_ref_invalid"
            ) from exc
        prepared_diff = _decode_unhashed_record(
            prepared_row["review_diff_json"]
        )
        if (
            prepared_candidate != semantic_candidate
            or prepared_row["job_id"] != row["job_id"]
            or prepared_row["target_space_id"] != self.target_space_id
            or prepared_row["status"] != prepared_candidate.outcome.value
            or prepared_result_ref != prepared_candidate.result_ref
            or prepared_diff != _thaw_json(prepared_candidate.review_diff)
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_candidate_revision_changed"
            )

        published_row = connection.execute(
            """
            SELECT b.*, r.snapshot_json, r.snapshot_sha256,
                   r.operation_id AS candidate_operation_id
            FROM candidate_bindings AS b
            JOIN candidate_revisions AS r
              ON r.candidate_id = b.candidate_id
             AND r.record_revision = b.candidate_record_revision
            WHERE b.candidate_id = ? AND b.binding_revision = ?
            """,
            (row["candidate_id"], row["binding_revision"] + 1),
        ).fetchone()
        if published_row is None:
            candidate_head = connection.execute(
                """
                SELECT binding_id, current_record_revision
                FROM candidates WHERE candidate_id = ?
                """,
                (row["candidate_id"],),
            ).fetchone()
            if (
                candidate_head is None
                or candidate_head["binding_id"] != self.binding_id
                or candidate_head["current_record_revision"]
                != prepared_row["candidate_record_revision"]
            ):
                raise SQLiteCuratorQueryV2IntegrityError(
                    "memory_review_publication_binding_missing"
                )
            return None
        try:
            published_candidate = _candidate_from_dict(
                _decode_record(
                    published_row["snapshot_json"],
                    published_row["snapshot_sha256"],
                )
            )
        except SQLiteCuratorV2IntegrityError as exc:
            raise SQLiteCuratorQueryV2IntegrityError(str(exc)) from exc
        try:
            published_result_ref = (
                ResourceRef.from_dict(
                    _decode_unhashed_record(published_row["result_ref_json"])
                )
                if published_row["result_ref_json"] is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_publication_result_ref_invalid"
            ) from exc
        published_diff = _decode_unhashed_record(
            published_row["review_diff_json"]
        )
        if (
            published_candidate.candidate_ref
            != semantic_candidate.candidate_ref
            or published_candidate.binding_revision
            != row["binding_revision"] + 1
            or published_candidate.target_space_id != self.target_space_id
            or published_row["job_id"] != row["job_id"]
            or published_row["target_space_id"] != self.target_space_id
            or published_row["status"] != published_candidate.outcome.value
            or published_result_ref != published_candidate.result_ref
            or published_diff != _thaw_json(published_candidate.review_diff)
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_publication_candidate_changed"
            )
        review_ref = ResourceRef(
            "memory_review", row["review_id"], 1, self.target_space_id
        )
        if published_candidate.outcome is not CandidateStatus.AWAITING_USER:
            return None
        try:
            expected_publication = semantic_candidate.with_outcome(
                CandidateStatus.AWAITING_USER,
                result_ref=review_ref,
                review_diff=review_diff,
            )
        except (TypeError, ValueError) as exc:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_publication_invalid"
            ) from exc
        if published_candidate != expected_publication:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_publication_candidate_changed"
            )

        try:
            publication_operation_id = _required_text(
                published_row["candidate_operation_id"],
                "publication_operation_id",
                maximum=512,
                identifier=True,
            )
        except (TypeError, ValueError) as exc:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_publication_operation_invalid"
            ) from exc
        job_header = connection.execute(
            "SELECT binding_id FROM consolidation_jobs WHERE job_id = ?",
            (row["job_id"],),
        ).fetchone()
        if job_header is None or job_header["binding_id"] != self.binding_id:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_publication_job_missing"
            )
        job_rows = connection.execute(
            """
            SELECT * FROM consolidation_job_revisions
            WHERE job_id = ? AND operation_id = ?
            """,
            (row["job_id"], publication_operation_id),
        ).fetchall()
        if len(job_rows) != 1:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_publication_job_ambiguous"
            )
        job_row = job_rows[0]
        try:
            published_job = _job_from_dict(
                _decode_record(job_row["job_json"], job_row["job_sha256"])
            )
        except SQLiteCuratorV2IntegrityError as exc:
            raise SQLiteCuratorQueryV2IntegrityError(str(exc)) from exc
        if (
            published_job.job_id != row["job_id"]
            or published_job.revision != job_row["revision"]
            or published_job.status is not ConsolidationJobStatus.COMPLETED
            or published_candidate not in published_job.candidates
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_publication_job_changed"
            )
        receipt = connection.execute(
            """
            SELECT * FROM curator_operation_receipts
            WHERE binding_id = ? AND operation_id = ?
            """,
            (self.binding_id, publication_operation_id),
        ).fetchone()
        if (
            receipt is None
            or receipt["operation_kind"] != "reconcile_and_complete"
            or receipt["result_kind"] != "job"
            or receipt["result_key"] != published_job.job_id
            or receipt["result_revision"] != published_job.revision
            or receipt["result_count"] is not None
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_publication_receipt_changed"
            )
        _required_sha256(
            receipt["payload_sha256"],
            "memory_review_publication_payload_sha256",
        )
        _required_sha256(
            receipt["semantic_sha256"],
            "memory_review_publication_semantic_sha256",
        )
        _non_negative(
            receipt["created_at_ms"],
            "memory_review_publication_created_at_ms",
        )
        return _VerifiedReviewPublication(
            review_json=review_json,
            review_sha256=review_sha256,
            candidate=published_candidate,
        )

    def read_review_content(
        self,
        *,
        ref: ResourceRef,
        offset: int = 0,
        limit: int = 65_536,
    ) -> MemoryToolContentPage:
        """Read one verified page from an immutable published review."""

        if (
            not isinstance(ref, ResourceRef)
            or ref.kind != "memory_review_content"
            or ref.revision != 1
            or ref.fragment not in {"diff", "proposed"}
        ):
            raise SQLiteCuratorQueryV2Error(
                "memory_review_content_not_found"
            )
        page_offset = _non_negative(offset, "offset")
        page_limit = _page_limit(limit)
        try:
            with self._store._transaction() as connection:
                self._scope_row(connection)
                publication = self._verified_review_publication(
                    connection,
                    review_id=ref.resource_id,
                )
                if publication is None:
                    raise SQLiteCuratorQueryV2Error(
                        "memory_review_not_published"
                    )
                if ref.fragment == "diff":
                    content = publication.review_json
                    media_type = "application/json"
                    digest = publication.review_sha256
                else:
                    candidate = publication.candidate
                    if (
                        candidate.content_ref is None
                        or not candidate.content_sha256
                        or not candidate.media_type
                    ):
                        raise SQLiteCuratorQueryV2Error(
                            "memory_review_content_not_found"
                        )
                    if (
                        candidate.content_ref.kind
                        != "memory_candidate_content"
                        or candidate.content_ref.resource_id
                        != candidate.candidate_ref.resource_id
                        or candidate.content_ref.revision
                        != candidate.candidate_ref.revision
                        or candidate.content_ref.fragment
                        != self.target_space_id
                    ):
                        raise SQLiteCuratorQueryV2IntegrityError(
                            "memory_review_candidate_object_scope_changed"
                        )
                    digest = _required_sha256(
                        candidate.content_sha256,
                        "memory_review_candidate_object_sha256",
                    )
                    object_row = connection.execute(
                        "SELECT byte_length FROM objects WHERE sha256 = ?",
                        (digest,),
                    ).fetchone()
                    if (
                        object_row is None
                        or object_row["byte_length"] != candidate.byte_length
                    ):
                        raise SQLiteCuratorQueryV2IntegrityError(
                            "memory_review_candidate_object_metadata_changed"
                        )
                    try:
                        content = (
                            self._store.object_directory / digest
                        ).read_bytes()
                    except OSError as exc:
                        raise SQLiteCuratorQueryV2IntegrityError(
                            "memory_review_candidate_object_unreadable"
                        ) from exc
                    if (
                        len(content) != candidate.byte_length
                        or hashlib.sha256(content).hexdigest() != digest
                    ):
                        raise SQLiteCuratorQueryV2IntegrityError(
                            "memory_review_candidate_object_digest_changed"
                        )
                    media_type = candidate.media_type
                if page_offset > len(content):
                    raise ValueError(
                        "offset exceeds the memory review content length"
                    )
                return MemoryToolContentPage(
                    ref=ref,
                    media_type=media_type,
                    data=content[page_offset : page_offset + page_limit],
                    offset=page_offset,
                    total_bytes=len(content),
                    sha256=digest,
                )
        except SQLiteCuratorQueryV2Error:
            raise
        except sqlite3.Error as exc:
            raise SQLiteCuratorQueryV2Error(
                "memory_review_content_read_failed"
            ) from exc

    def _pending_review(
        self,
        connection: sqlite3.Connection,
        *,
        review_id: str,
    ) -> PendingMemoryReviewProposal | None:
        row = connection.execute(
            "SELECT * FROM memory_review_proposals WHERE review_id = ?",
            (review_id,),
        ).fetchone()
        if row is None or row["binding_id"] != self.binding_id:
            raise SQLiteCuratorQueryV2Error("memory_review_scope_mismatch")
        if row["target_space_id"] != self.target_space_id:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_target_scope_changed"
            )
        semantic = _decode_record(row["semantic_json"], row["semantic_sha256"])
        review_diff = _decode_record(row["review_json"], row["review_sha256"])
        if set(semantic) != {"binding_id", "job_id", "candidate", "target", "mode"}:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_semantic_shape_changed"
            )
        try:
            semantic_candidate = _candidate_from_dict(semantic["candidate"])
            semantic_target = MemoryEntry.from_dict(semantic["target"])
        except (SQLiteCuratorV2IntegrityError, TypeError, ValueError) as exc:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_semantic_invalid"
            ) from exc
        if (
            semantic["binding_id"] != self.binding_id
            or semantic["job_id"] != row["job_id"]
            or semantic["mode"] != row["mode"]
            or semantic_candidate.candidate_ref.resource_id != row["candidate_id"]
            or semantic_candidate.candidate_ref.revision != row["candidate_revision"]
            or semantic_candidate.binding_revision != row["binding_revision"]
            or semantic_candidate.target_space_id != self.target_space_id
            or semantic_target.space_id != self.target_space_id
            or semantic_target.entry_id != row["target_entry_id"]
            or semantic_target.revision != row["target_revision"]
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_semantic_scope_changed"
            )
        expected_id = "memory-review-" + row["semantic_sha256"][:32]
        if row["review_id"] != expected_id:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_identity_changed"
            )
        prepared_binding = connection.execute(
            """
            SELECT r.snapshot_json, r.snapshot_sha256
            FROM candidate_bindings AS b
            JOIN candidate_revisions AS r
              ON r.candidate_id = b.candidate_id
             AND r.record_revision = b.candidate_record_revision
            WHERE b.candidate_id = ? AND b.binding_revision = ?
              AND b.job_id = ? AND b.target_space_id = ?
              AND b.status = ?
            """,
            (
                row["candidate_id"],
                row["binding_revision"],
                row["job_id"],
                self.target_space_id,
                semantic_candidate.outcome.value,
            ),
        ).fetchone()
        if (
            prepared_binding is None
            or _decode_record(
                prepared_binding["snapshot_json"],
                prepared_binding["snapshot_sha256"],
            )
            != semantic_candidate.to_dict()
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_candidate_revision_missing"
            )
        current_candidate = self._candidate(
            connection, candidate_id=row["candidate_id"]
        )
        current_job = self._job(connection, job_id=row["job_id"])
        review_ref = ResourceRef(
            "memory_review", row["review_id"], 1, self.target_space_id
        )
        if (
            current_candidate.outcome is CandidateStatus.AWAITING_USER
            and current_candidate.result_ref == review_ref
        ):
            if (
                current_candidate.candidate_ref != semantic_candidate.candidate_ref
                or current_candidate.binding_revision
                != row["binding_revision"] + 1
                or _thaw_json(current_candidate.review_diff) != review_diff
                or current_candidate not in current_job.candidates
            ):
                raise SQLiteCuratorQueryV2IntegrityError(
                    "memory_review_candidate_head_changed"
                )
        elif current_candidate == semantic_candidate:
            if (
                current_job.status is ConsolidationJobStatus.LEASED
                and current_candidate in current_job.candidates
            ):
                return None
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_prepared_state_changed"
            )
        elif (
            current_candidate.candidate_ref == semantic_candidate.candidate_ref
            and current_candidate.target_space_id == self.target_space_id
            and current_candidate.binding_revision > row["binding_revision"]
            and current_candidate in current_job.candidates
        ):
            return None
        else:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_candidate_head_changed"
            )
        target_row = connection.execute(
            """
            SELECT e.current_revision, e.deleted, r.entry_json, r.entry_sha256
            FROM entries AS e
            JOIN entry_revisions AS r
              ON r.space_id = e.space_id AND r.entry_id = e.entry_id
             AND r.revision = e.current_revision
            WHERE e.space_id = ? AND e.entry_id = ?
            """,
            (self.target_space_id, row["target_entry_id"]),
        ).fetchone()
        if target_row is None:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_target_missing"
            )
        try:
            current_target = MemoryEntry.from_dict(
                _decode_record(target_row["entry_json"], target_row["entry_sha256"])
            )
        except (TypeError, ValueError) as exc:
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_target_invalid"
            ) from exc
        if (
            target_row["deleted"] != 0
            or target_row["current_revision"] != row["target_revision"]
            or current_target != semantic_target
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_target_revision_changed"
            )
        if (
            review_diff.get("schema") != _REVIEW_SCHEMA
            or review_diff.get("mode") != row["mode"]
            or review_diff.get("requires_user_confirmation") is not True
        ):
            raise SQLiteCuratorQueryV2IntegrityError(
                "memory_review_diff_changed"
            )
        return PendingMemoryReviewProposal(
            review_ref=review_ref,
            binding_id=self.binding_id,
            job_id=row["job_id"],
            candidate_ref=current_candidate.candidate_ref,
            binding_revision=current_candidate.binding_revision,
            target_entry_ref=ResourceRef(
                "memory",
                row["target_entry_id"],
                _positive(row["target_revision"], "review_target_revision"),
                self.target_space_id,
            ),
            mode=_required_text(row["mode"], "review_mode", maximum=32),
            semantic=_freeze_json(semantic, path="memory_review.semantic"),
            review_diff=_freeze_json(
                review_diff, path="memory_review.review_diff"
            ),
            first_operation_id=_required_text(
                row["first_operation_id"],
                "first_operation_id",
                maximum=512,
                identifier=True,
            ),
            created_at_ms=_non_negative(
                row["created_at_ms"], "review_created_at_ms"
            ),
        )

    def list_pending_reviews(
        self,
        *,
        status: MemoryReviewStatus | None = None,
        limit: int = 100,
    ) -> tuple[PendingMemoryReviewProposal, ...]:
        if status is not None and not isinstance(status, MemoryReviewStatus):
            raise TypeError("status must be a MemoryReviewStatus or None")
        page_limit = _limit(limit)
        try:
            with self._store._transaction() as connection:
                self._scope_row(connection)
                rows = connection.execute(
                    """
                    SELECT review_id FROM memory_review_proposals
                    WHERE binding_id = ?
                    ORDER BY created_at_ms DESC, review_id
                    """,
                    (self.binding_id,),
                )
                reviews: list[PendingMemoryReviewProposal] = []
                for row in rows:
                    review = self._pending_review(
                        connection,
                        review_id=row["review_id"],
                    )
                    if review is None:
                        continue
                    reviews.append(review)
                    if len(reviews) == page_limit:
                        break
                return tuple(reviews)
        except SQLiteCuratorQueryV2Error:
            raise
        except sqlite3.Error as exc:
            raise SQLiteCuratorQueryV2Error(
                "memory_review_list_failed"
            ) from exc

    def get_pending_review(
        self, *, review_id: str
    ) -> PendingMemoryReviewProposal:
        normalized = _required_text(
            review_id, "review_id", maximum=512, identifier=True
        )
        try:
            with self._store._transaction() as connection:
                self._scope_row(connection)
                review = self._pending_review(
                    connection,
                    review_id=normalized,
                )
                if review is None:
                    raise SQLiteCuratorQueryV2Error(
                        "memory_review_not_published"
                    )
                return review
        except SQLiteCuratorQueryV2Error:
            raise
        except sqlite3.Error as exc:
            raise SQLiteCuratorQueryV2Error(
                "memory_review_read_failed"
            ) from exc


__all__ = [
    "MemoryReviewStatus",
    "PendingMemoryReviewProposal",
    "SQLiteBoundCuratorQueryV2",
    "SQLiteCuratorQueryV2Error",
    "SQLiteCuratorQueryV2IntegrityError",
    "SQLiteCuratorQueryV2Store",
]
