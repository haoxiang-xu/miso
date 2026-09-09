"""Durable, confirmation-gated long-term promotion persistence.

This module deliberately stays private to the host assembly layer.  It shares
the Memory V2 SQLite/CAS data plane, but exposes only a repository already
bound to one source chat and one long-term namespace.  A pending proposal does
not write long-term state; the approved decision, derived target revision, and
idempotency receipt are committed in one ``BEGIN IMMEDIATE`` transaction.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

from unchain.journal import OperationRef, ResourceRef
from unchain.journal.models import _required_text
from unchain.memory.workspace import (
    MemoryEntry,
    MemoryEntryKind,
    MemorySpace,
    PromotionProposal,
    PromotionStatus,
)
from unchain.memory.workspace.paths import canonical_entry_path, virtual_name
from unchain.memory.workspace.ports import (
    BoundPromotionDecisionRepository,
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryScopeError,
    WorkspaceRepositoryError,
)

from .sqlite_memory_v2 import (
    SQLiteMemoryV2Store,
    SQLiteMemoryV2StoreIntegrityError,
    _canonical_json_bytes,
    _path_key,
    _sha256,
)


_LONG_TERM_NAME = "Long-term memory"
_LONG_TERM_DESCRIPTION = "Namespaced durable long-term memory"
_MAX_LIST_CURRENT_RESULTS = 1_000


class SQLitePromotionV2StoreError(WorkspaceRepositoryError):
    """Base failure for the durable promotion persistence slice."""


class SQLitePromotionV2StoreIntegrityError(SQLitePromotionV2StoreError):
    """A durable proposal, binding, or receipt failed integrity checks."""


def _target_space_identifier(namespace: str, requested: str | None) -> str:
    if requested is not None:
        return _required_text(
            requested,
            "target_space_id",
            identifier=True,
        )
    digest = hashlib.sha256(namespace.encode("utf-8")).hexdigest()
    return f"long-term-{digest[:32]}"


def _positive_revision(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


class SQLitePromotionV2Store:
    """Construct exact-scope promotion repositories over Memory V2 storage."""

    def __init__(
        self,
        *,
        database_path: str | os.PathLike[str],
        object_directory: str | os.PathLike[str],
    ) -> None:
        self.database_path = Path(database_path)
        self.object_directory = Path(object_directory)
        self._memory = SQLiteMemoryV2Store(
            database_path=self.database_path,
            object_directory=self.object_directory,
        )
        self._initialize()

    def _initialize(self) -> None:
        try:
            with self._memory._transaction(immediate=True) as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS promotion_namespace_bindings (
                        target_namespace TEXT PRIMARY KEY,
                        target_space_id TEXT NOT NULL UNIQUE,
                        FOREIGN KEY (target_space_id) REFERENCES spaces(space_id)
                    );

                    CREATE TABLE IF NOT EXISTS promotion_bindings (
                        source_space_id TEXT NOT NULL,
                        target_namespace TEXT NOT NULL,
                        source_owner_chat_id TEXT NOT NULL,
                        target_space_id TEXT NOT NULL,
                        PRIMARY KEY (source_space_id, target_namespace),
                        FOREIGN KEY (source_space_id) REFERENCES spaces(space_id),
                        FOREIGN KEY (target_namespace)
                            REFERENCES promotion_namespace_bindings(target_namespace),
                        FOREIGN KEY (target_space_id) REFERENCES spaces(space_id)
                    );

                    CREATE TABLE IF NOT EXISTS promotion_proposals (
                        source_space_id TEXT NOT NULL,
                        target_namespace TEXT NOT NULL,
                        target_space_id TEXT NOT NULL,
                        proposal_id TEXT NOT NULL,
                        current_revision INTEGER NOT NULL
                            CHECK(current_revision >= 1),
                        status TEXT NOT NULL,
                        source_entry_id TEXT NOT NULL,
                        source_entry_revision INTEGER NOT NULL
                            CHECK(source_entry_revision >= 1),
                        target_path_key TEXT NOT NULL,
                        PRIMARY KEY (
                            source_space_id,
                            target_namespace,
                            target_space_id,
                            proposal_id
                        ),
                        FOREIGN KEY (source_space_id, target_namespace)
                            REFERENCES promotion_bindings(
                                source_space_id,
                                target_namespace
                            )
                    );

                    CREATE TABLE IF NOT EXISTS promotion_revisions (
                        source_space_id TEXT NOT NULL,
                        target_namespace TEXT NOT NULL,
                        target_space_id TEXT NOT NULL,
                        proposal_id TEXT NOT NULL,
                        revision INTEGER NOT NULL CHECK(revision >= 1),
                        status TEXT NOT NULL,
                        operation_id TEXT NOT NULL,
                        confirmation_id TEXT NOT NULL DEFAULT '',
                        proposal_json BLOB NOT NULL,
                        proposal_sha256 TEXT NOT NULL,
                        PRIMARY KEY (
                            source_space_id,
                            target_namespace,
                            target_space_id,
                            proposal_id,
                            revision
                        ),
                        FOREIGN KEY (
                            source_space_id,
                            target_namespace,
                            target_space_id,
                            proposal_id
                        ) REFERENCES promotion_proposals(
                            source_space_id,
                            target_namespace,
                            target_space_id,
                            proposal_id
                        )
                    );

                    CREATE TABLE IF NOT EXISTS promotion_operation_receipts (
                        source_space_id TEXT NOT NULL,
                        target_namespace TEXT NOT NULL,
                        target_space_id TEXT NOT NULL,
                        operation_id TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        operation_kind TEXT NOT NULL,
                        proposal_id TEXT NOT NULL,
                        result_revision INTEGER NOT NULL
                            CHECK(result_revision >= 1),
                        PRIMARY KEY (
                            source_space_id,
                            target_namespace,
                            target_space_id,
                            operation_id
                        ),
                        FOREIGN KEY (
                            source_space_id,
                            target_namespace,
                            target_space_id,
                            proposal_id,
                            result_revision
                        ) REFERENCES promotion_revisions(
                            source_space_id,
                            target_namespace,
                            target_space_id,
                            proposal_id,
                            revision
                        )
                    );

                    CREATE INDEX IF NOT EXISTS idx_promotion_revisions_lookup
                        ON promotion_revisions(
                            source_space_id,
                            target_namespace,
                            target_space_id,
                            proposal_id,
                            revision
                        );
                    """
                )
        except sqlite3.Error as exc:
            raise SQLitePromotionV2StoreError(
                "SQLite promotion schema initialization failed"
            ) from exc

    def bind(
        self,
        *,
        source_space: MemorySpace,
        source_owner_chat_id: str,
        target_namespace: str,
        target_space_id: str | None = None,
    ) -> _SQLiteBoundPromotionRepository:
        if not isinstance(source_space, MemorySpace):
            raise TypeError("source_space must be a MemorySpace")
        if source_space.namespace != "chat":
            raise RepositoryScopeError("promotion source must be a chat workspace")
        owner = _required_text(
            source_owner_chat_id,
            "source_owner_chat_id",
            maximum=512,
            identifier=True,
        )
        namespace = _required_text(
            target_namespace,
            "target_namespace",
            identifier=True,
        )
        if namespace == "chat":
            raise RepositoryScopeError("promotion target must be long-term memory")
        target_id = _target_space_identifier(namespace, target_space_id)
        if target_id == source_space.space_id:
            raise RepositoryScopeError("source and target spaces must be distinct")

        target_template = MemorySpace(
            target_id,
            namespace,
            _LONG_TERM_NAME,
            _LONG_TERM_DESCRIPTION,
            1,
        )
        try:
            with self._memory._transaction(immediate=True) as connection:
                source_row = connection.execute(
                    "SELECT * FROM spaces WHERE space_id = ?",
                    (source_space.space_id,),
                ).fetchone()
                if source_row is None or source_row["owner_chat_id"] != owner:
                    raise RepositoryScopeError(
                        "source workspace is outside the bound chat"
                    )
                persisted_source = self._memory._space_from_row(source_row)
                if (
                    persisted_source.namespace != source_space.namespace
                    or persisted_source.name != source_space.name
                    or persisted_source.description != source_space.description
                    or source_space.revision > persisted_source.revision
                ):
                    raise RepositoryConflictError("source workspace identity changed")

                namespace_row = connection.execute(
                    """
                    SELECT target_space_id FROM promotion_namespace_bindings
                    WHERE target_namespace = ?
                    """,
                    (namespace,),
                ).fetchone()
                if (
                    namespace_row is not None
                    and namespace_row["target_space_id"] != target_id
                ):
                    raise RepositoryScopeError(
                        "long-term namespace is bound to another space"
                    )
                foreign_namespace_space = connection.execute(
                    """
                    SELECT space_id FROM spaces
                    WHERE namespace = ? AND owner_chat_id = '' AND space_id != ?
                    LIMIT 1
                    """,
                    (namespace, target_id),
                ).fetchone()
                if foreign_namespace_space is not None:
                    raise RepositoryScopeError(
                        "long-term namespace is bound to another space"
                    )

                target_row = connection.execute(
                    "SELECT * FROM spaces WHERE space_id = ?",
                    (target_id,),
                ).fetchone()
                if target_row is None:
                    encoded = _canonical_json_bytes(target_template.to_dict())
                    connection.execute(
                        """
                        INSERT INTO spaces(
                            space_id, owner_chat_id, namespace, name,
                            description, revision, space_json, space_sha256
                        ) VALUES (?, '', ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            target_id,
                            namespace,
                            _LONG_TERM_NAME,
                            _LONG_TERM_DESCRIPTION,
                            encoded,
                            _sha256(encoded),
                        ),
                    )
                    persisted_target = target_template
                else:
                    persisted_target = self._memory._space_from_row(target_row)
                    if (
                        target_row["owner_chat_id"] != ""
                        or persisted_target.namespace != namespace
                        or persisted_target.name != _LONG_TERM_NAME
                        or persisted_target.description != _LONG_TERM_DESCRIPTION
                    ):
                        raise RepositoryScopeError(
                            "target space belongs to another durable scope"
                        )

                connection.execute(
                    """
                    INSERT OR IGNORE INTO promotion_namespace_bindings(
                        target_namespace, target_space_id
                    ) VALUES (?, ?)
                    """,
                    (namespace, target_id),
                )
                persisted_namespace = connection.execute(
                    """
                    SELECT target_space_id FROM promotion_namespace_bindings
                    WHERE target_namespace = ?
                    """,
                    (namespace,),
                ).fetchone()
                if (
                    persisted_namespace is None
                    or persisted_namespace["target_space_id"] != target_id
                ):
                    raise RepositoryScopeError("long-term namespace binding changed")

                binding = connection.execute(
                    """
                    SELECT source_owner_chat_id, target_space_id
                    FROM promotion_bindings
                    WHERE source_space_id = ? AND target_namespace = ?
                    """,
                    (persisted_source.space_id, namespace),
                ).fetchone()
                if binding is None:
                    connection.execute(
                        """
                        INSERT INTO promotion_bindings(
                            source_space_id, target_namespace,
                            source_owner_chat_id, target_space_id
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (persisted_source.space_id, namespace, owner, target_id),
                    )
                elif (
                    binding["source_owner_chat_id"] != owner
                    or binding["target_space_id"] != target_id
                ):
                    raise RepositoryScopeError(
                        "promotion binding belongs to another scope"
                    )
        except (RepositoryConflictError, RepositoryScopeError):
            raise
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflictError("promotion binding conflicted") from exc
        except sqlite3.Error as exc:
            raise SQLitePromotionV2StoreError(
                "SQLite promotion binding failed"
            ) from exc

        source_repository = self._memory.bind_workspace(
            space=persisted_source,
            owner_chat_id=owner,
        )
        target_repository = self._memory.bind_workspace(
            space=persisted_target,
            owner_chat_id=None,
        )
        return _SQLiteBoundPromotionRepository(
            store=self,
            source_space=persisted_source,
            source_owner_chat_id=owner,
            target_namespace=namespace,
            target_space_id=target_id,
            source_repository=source_repository,
            target_repository=target_repository,
        )


class _SQLiteBoundPromotionRepository(BoundPromotionDecisionRepository):
    """One source-chat/target-namespace promotion capability."""

    def __init__(
        self,
        *,
        store: SQLitePromotionV2Store,
        source_space: MemorySpace,
        source_owner_chat_id: str,
        target_namespace: str,
        target_space_id: str,
        source_repository: Any,
        target_repository: Any,
    ) -> None:
        super().__init__(source_space, target_namespace, target_space_id)
        self._store = store
        self._source_owner_chat_id = source_owner_chat_id
        self._source_repository = source_repository
        self._target_repository = target_repository

    def _require_operation(self, operation: OperationRef) -> None:
        if not isinstance(operation, OperationRef):
            raise TypeError("operation must be an OperationRef")

    def _require_proposal_ref(self, ref: ResourceRef) -> None:
        if not isinstance(ref, ResourceRef):
            raise TypeError("ref must be a ResourceRef")
        if ref.kind != "promotion" or ref.fragment != self.target_namespace:
            raise RepositoryScopeError(
                "proposal reference is outside the bound promotion scope"
            )

    def _binding_row(self, connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM promotion_bindings
            WHERE source_space_id = ? AND target_namespace = ?
            """,
            (self.source_space.space_id, self.target_namespace),
        ).fetchone()
        if (
            row is None
            or row["source_owner_chat_id"] != self._source_owner_chat_id
            or row["target_space_id"] != self.target_space_id
        ):
            raise RepositoryScopeError("promotion binding is unavailable")
        return row

    def _receipt(
        self,
        connection: sqlite3.Connection,
        *,
        operation: OperationRef,
        operation_kind: str,
    ) -> sqlite3.Row | None:
        row = connection.execute(
            """
            SELECT * FROM promotion_operation_receipts
            WHERE source_space_id = ? AND target_namespace = ?
              AND target_space_id = ? AND operation_id = ?
            """,
            (
                self.source_space.space_id,
                self.target_namespace,
                self.target_space_id,
                operation.operation_id,
            ),
        ).fetchone()
        if row is None:
            return None
        if (
            row["payload_sha256"] != operation.payload_sha256
            or row["operation_kind"] != operation_kind
        ):
            raise RepositoryConflictError("promotion operation payload changed")
        return row

    def _revision_row(
        self,
        connection: sqlite3.Connection,
        *,
        proposal_id: str,
        revision: int,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM promotion_revisions
            WHERE source_space_id = ? AND target_namespace = ?
              AND target_space_id = ? AND proposal_id = ? AND revision = ?
            """,
            (
                self.source_space.space_id,
                self.target_namespace,
                self.target_space_id,
                proposal_id,
                revision,
            ),
        ).fetchone()

    def _proposal_from_row(self, row: sqlite3.Row) -> PromotionProposal:
        raw = bytes(row["proposal_json"])
        if _sha256(raw) != row["proposal_sha256"]:
            raise SQLitePromotionV2StoreIntegrityError(
                "promotion proposal digest changed"
            )
        try:
            proposal = PromotionProposal.from_dict(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise SQLitePromotionV2StoreIntegrityError(
                "promotion proposal is malformed"
            ) from exc
        if (
            _canonical_json_bytes(proposal.to_dict()) != raw
            or proposal.proposal_id != row["proposal_id"]
            or proposal.revision != row["revision"]
            or proposal.status.value != row["status"]
            or proposal.target_namespace != self.target_namespace
            or proposal.source_entry_ref.fragment != self.source_space.space_id
        ):
            raise SQLitePromotionV2StoreIntegrityError(
                "promotion proposal indexed metadata changed"
            )
        return proposal

    def _proposal_for_receipt(
        self,
        connection: sqlite3.Connection,
        receipt: sqlite3.Row,
    ) -> PromotionProposal:
        row = self._revision_row(
            connection,
            proposal_id=receipt["proposal_id"],
            revision=receipt["result_revision"],
        )
        if row is None:
            raise SQLitePromotionV2StoreIntegrityError(
                "promotion receipt lost its result revision"
            )
        return self._proposal_from_row(row)

    def _write_revision(
        self,
        connection: sqlite3.Connection,
        *,
        proposal: PromotionProposal,
        operation_id: str,
        confirmation_id: str = "",
    ) -> None:
        encoded = _canonical_json_bytes(proposal.to_dict())
        connection.execute(
            """
            INSERT INTO promotion_revisions(
                source_space_id, target_namespace, target_space_id,
                proposal_id, revision, status, operation_id,
                confirmation_id, proposal_json, proposal_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.source_space.space_id,
                self.target_namespace,
                self.target_space_id,
                proposal.proposal_id,
                proposal.revision,
                proposal.status.value,
                operation_id,
                confirmation_id,
                encoded,
                _sha256(encoded),
            ),
        )

    def _claim_receipt(
        self,
        connection: sqlite3.Connection,
        *,
        operation: OperationRef,
        operation_kind: str,
        proposal: PromotionProposal,
    ) -> None:
        connection.execute(
            """
            INSERT INTO promotion_operation_receipts(
                source_space_id, target_namespace, target_space_id,
                operation_id, payload_sha256, operation_kind,
                proposal_id, result_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.source_space.space_id,
                self.target_namespace,
                self.target_space_id,
                operation.operation_id,
                operation.payload_sha256,
                operation_kind,
                proposal.proposal_id,
                proposal.revision,
            ),
        )

    def _source_entry(
        self,
        connection: sqlite3.Connection,
        ref: ResourceRef,
        *,
        require_current: bool,
    ) -> tuple[MemoryEntry, sqlite3.Row]:
        if (
            not isinstance(ref, ResourceRef)
            or ref.kind != "memory"
            or ref.fragment != self.source_space.space_id
        ):
            raise RepositoryScopeError("promotion source belongs to another workspace")
        self._source_repository._load_space_row(connection)
        row = self._source_repository._entry_revision_row(
            connection,
            entry_id=ref.resource_id,
            revision=ref.revision,
        )
        if row is None:
            raise RepositoryNotFoundError("promotion source revision was not found")
        source = self._source_repository._entry_from_row(row)
        if source.deleted:
            raise RepositoryConflictError("archived entries cannot be promoted")
        if require_current:
            current_row = self._source_repository._current_entry_row(
                connection,
                entry_id=ref.resource_id,
            )
            if current_row is None:
                raise RepositoryNotFoundError("promotion source was not found")
            current = self._source_repository._entry_from_row(current_row)
            if current != source:
                raise RepositoryConflictError(
                    "promotion source revision is no longer current"
                )
        return source, row

    def _current_target_at_path(
        self,
        connection: sqlite3.Connection,
        target_path: str,
    ) -> tuple[MemoryEntry, sqlite3.Row] | None:
        row = connection.execute(
            """
            SELECT r.*
            FROM entries AS e
            JOIN entry_revisions AS r
              ON r.space_id = e.space_id
             AND r.entry_id = e.entry_id
             AND r.revision = e.current_revision
            WHERE e.space_id = ? AND e.path_key = ? AND e.deleted = 0
            LIMIT 1
            """,
            (self.target_space_id, _path_key(target_path)),
        ).fetchone()
        if row is None:
            return None
        return self._target_repository._entry_from_row(row), row

    def _target_baseline(
        self,
        connection: sqlite3.Connection,
        *,
        target_path: str,
        target_entry_ref: ResourceRef | None,
    ) -> tuple[MemoryEntry, sqlite3.Row] | None:
        path = canonical_entry_path(target_path)
        self._target_repository._load_space_row(connection)
        current = self._current_target_at_path(connection, path)
        if target_entry_ref is None:
            return current
        if (
            not isinstance(target_entry_ref, ResourceRef)
            or target_entry_ref.kind != "memory"
            or target_entry_ref.fragment != self.target_space_id
        ):
            raise RepositoryScopeError(
                "target baseline is outside the bound long-term space"
            )
        if current is None:
            raise RepositoryConflictError("target baseline changed")
        entry, row = current
        if (
            entry.entry_id != target_entry_ref.resource_id
            or entry.revision != target_entry_ref.revision
            or entry.path != path
            or entry.deleted
        ):
            raise RepositoryConflictError("target baseline changed")
        return entry, row

    def _verify_object(
        self,
        connection: sqlite3.Connection,
        *,
        entry: MemoryEntry,
        row: sqlite3.Row,
        label: str,
    ) -> tuple[str, int] | None:
        digest = row["object_sha256"]
        byte_length = row["byte_length"]
        if entry.kind in {MemoryEntryKind.FOLDER, MemoryEntryKind.LINK}:
            if (
                entry.content_ref is not None
                or digest is not None
                or byte_length is not None
            ):
                raise SQLitePromotionV2StoreIntegrityError(
                    f"{label} unexpectedly references object content"
                )
            return None
        if (
            entry.content_ref is None
            or digest is None
            or isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or byte_length < 0
        ):
            raise SQLitePromotionV2StoreIntegrityError(
                f"{label} lost its object metadata"
            )
        catalog = connection.execute(
            "SELECT byte_length FROM objects WHERE sha256 = ?",
            (digest,),
        ).fetchone()
        if catalog is None or catalog["byte_length"] != byte_length:
            raise SQLitePromotionV2StoreIntegrityError(
                f"{label} object catalog changed"
            )
        self._store._memory._read_object(
            digest=digest,
            byte_length=byte_length,
        )
        return digest, byte_length

    def create(
        self,
        *,
        proposal: PromotionProposal,
        operation: OperationRef,
    ) -> PromotionProposal:
        if not isinstance(proposal, PromotionProposal):
            raise TypeError("proposal must be a PromotionProposal")
        self._require_operation(operation)
        if (
            proposal.target_namespace != self.target_namespace
            or proposal.source_entry_ref.fragment != self.source_space.space_id
            or proposal.status is not PromotionStatus.PENDING
            or proposal.revision != 1
            or proposal.applied_entry_ref is not None
        ):
            raise RepositoryScopeError("proposal is outside the bound pending scope")
        if proposal.target_entry_ref is not None and (
            proposal.target_entry_ref.kind != "memory"
            or proposal.target_entry_ref.fragment != self.target_space_id
        ):
            raise RepositoryScopeError("proposal target baseline is foreign")

        try:
            with self._store._memory._transaction(immediate=True) as connection:
                self._binding_row(connection)
                receipt = self._receipt(
                    connection,
                    operation=operation,
                    operation_kind="proposal",
                )
                if receipt is not None:
                    return self._proposal_for_receipt(connection, receipt)
                self._source_entry(
                    connection,
                    proposal.source_entry_ref,
                    require_current=True,
                )
                baseline = self._target_baseline(
                    connection,
                    target_path=proposal.target_path,
                    target_entry_ref=proposal.target_entry_ref,
                )
                if proposal.target_entry_ref is None and baseline is not None:
                    raise RepositoryConflictError(
                        "an existing target requires an exact baseline"
                    )
                existing = connection.execute(
                    """
                    SELECT 1 FROM promotion_proposals
                    WHERE source_space_id = ? AND target_namespace = ?
                      AND target_space_id = ? AND proposal_id = ?
                    """,
                    (
                        self.source_space.space_id,
                        self.target_namespace,
                        self.target_space_id,
                        proposal.proposal_id,
                    ),
                ).fetchone()
                if existing is not None:
                    raise RepositoryConflictError(
                        "promotion proposal identifier already exists"
                    )
                connection.execute(
                    """
                    INSERT INTO promotion_proposals(
                        source_space_id, target_namespace, target_space_id,
                        proposal_id, current_revision, status,
                        source_entry_id, source_entry_revision, target_path_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.source_space.space_id,
                        self.target_namespace,
                        self.target_space_id,
                        proposal.proposal_id,
                        proposal.revision,
                        proposal.status.value,
                        proposal.source_entry_ref.resource_id,
                        proposal.source_entry_ref.revision,
                        _path_key(proposal.target_path),
                    ),
                )
                self._write_revision(
                    connection,
                    proposal=proposal,
                    operation_id=operation.operation_id,
                )
                self._claim_receipt(
                    connection,
                    operation=operation,
                    operation_kind="proposal",
                    proposal=proposal,
                )
                return proposal
        except (RepositoryConflictError, RepositoryNotFoundError, RepositoryScopeError):
            raise
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflictError(
                "promotion proposal operation conflicted"
            ) from exc
        except sqlite3.Error as exc:
            raise SQLitePromotionV2StoreError(
                "SQLite promotion proposal write failed"
            ) from exc

    def replay(self, *, operation: OperationRef) -> PromotionProposal | None:
        self._require_operation(operation)
        try:
            with self._store._memory._transaction(immediate=False) as connection:
                self._binding_row(connection)
                receipt = self._receipt(
                    connection,
                    operation=operation,
                    operation_kind="proposal",
                )
                return (
                    self._proposal_for_receipt(connection, receipt)
                    if receipt is not None
                    else None
                )
        except sqlite3.Error as exc:
            raise SQLitePromotionV2StoreError("SQLite promotion replay failed") from exc

    def replay_decision(
        self,
        *,
        operation: OperationRef,
    ) -> PromotionProposal | None:
        self._require_operation(operation)
        try:
            with self._store._memory._transaction(immediate=False) as connection:
                self._binding_row(connection)
                receipt = self._receipt(
                    connection,
                    operation=operation,
                    operation_kind="decision",
                )
                return (
                    self._proposal_for_receipt(connection, receipt)
                    if receipt is not None
                    else None
                )
        except sqlite3.Error as exc:
            raise SQLitePromotionV2StoreError(
                "SQLite promotion decision replay failed"
            ) from exc

    def read(self, *, ref: ResourceRef) -> PromotionProposal:
        self._require_proposal_ref(ref)
        try:
            with self._store._memory._transaction(immediate=False) as connection:
                self._binding_row(connection)
                row = self._revision_row(
                    connection,
                    proposal_id=ref.resource_id,
                    revision=ref.revision,
                )
                if row is None:
                    raise RepositoryNotFoundError(
                        "promotion proposal revision was not found"
                    )
                return self._proposal_from_row(row)
        except sqlite3.Error as exc:
            raise SQLitePromotionV2StoreError(
                "SQLite promotion proposal read failed"
            ) from exc

    def list_current(
        self,
        *,
        status: PromotionStatus | None = None,
        limit: int = 100,
    ) -> tuple[PromotionProposal, ...]:
        if status is not None and not isinstance(status, PromotionStatus):
            raise TypeError("status must be a PromotionStatus or None")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > _MAX_LIST_CURRENT_RESULTS
        ):
            raise ValueError(
                f"limit must be between 1 and {_MAX_LIST_CURRENT_RESULTS}"
            )
        clauses = (
            "p.source_space_id = ?",
            "p.target_namespace = ?",
            "p.target_space_id = ?",
        )
        parameters: list[object] = [
            self.source_space.space_id,
            self.target_namespace,
            self.target_space_id,
        ]
        if status is not None:
            clauses = (*clauses, "p.status = ?")
            parameters.append(status.value)
        parameters.append(limit)
        try:
            with self._store._memory._transaction(immediate=False) as connection:
                self._binding_row(connection)
                rows = connection.execute(
                    """
                    SELECT r.*
                    FROM promotion_proposals AS p
                    JOIN promotion_revisions AS r
                      ON r.source_space_id = p.source_space_id
                     AND r.target_namespace = p.target_namespace
                     AND r.target_space_id = p.target_space_id
                     AND r.proposal_id = p.proposal_id
                     AND r.revision = p.current_revision
                    WHERE """
                    + " AND ".join(clauses)
                    + " ORDER BY r.rowid DESC LIMIT ?",
                    tuple(parameters),
                ).fetchall()
                proposals = tuple(self._proposal_from_row(row) for row in rows)
                if any(
                    proposal.source_entry_ref.fragment
                    != self.source_space.space_id
                    or proposal.target_namespace != self.target_namespace
                    or (status is not None and proposal.status is not status)
                    for proposal in proposals
                ):
                    raise SQLitePromotionV2StoreIntegrityError(
                        "promotion listing escaped its bound scope"
                    )
                return proposals
        except SQLitePromotionV2StoreIntegrityError:
            raise
        except sqlite3.Error as exc:
            raise SQLitePromotionV2StoreError(
                "SQLite promotion listing failed"
            ) from exc

    def validate_target_baseline(
        self,
        *,
        target_path: str,
        target_entry_ref: ResourceRef | None,
    ) -> MemoryEntry | None:
        try:
            with self._store._memory._transaction(immediate=False) as connection:
                self._binding_row(connection)
                baseline = self._target_baseline(
                    connection,
                    target_path=target_path,
                    target_entry_ref=target_entry_ref,
                )
                return baseline[0] if baseline is not None else None
        except sqlite3.Error as exc:
            raise SQLitePromotionV2StoreError(
                "SQLite long-term baseline read failed"
            ) from exc

    def read_target(self, *, ref: ResourceRef) -> MemoryEntry:
        if not isinstance(ref, ResourceRef):
            raise TypeError("ref must be a ResourceRef")
        if ref.kind != "memory" or ref.fragment != self.target_space_id:
            raise RepositoryScopeError(
                "target reference is outside the bound long-term space"
            )
        return self._target_repository.read_entry(ref=ref)

    def _derive_target(
        self,
        connection: sqlite3.Connection,
        *,
        proposal: PromotionProposal,
    ) -> MemoryEntry:
        source, source_row = self._source_entry(
            connection,
            proposal.source_entry_ref,
            require_current=False,
        )
        source_object = self._verify_object(
            connection,
            entry=source,
            row=source_row,
            label="promotion source",
        )
        baseline = self._target_baseline(
            connection,
            target_path=proposal.target_path,
            target_entry_ref=proposal.target_entry_ref,
        )
        if proposal.target_entry_ref is None:
            if baseline is not None:
                raise RepositoryConflictError("target baseline changed")
            target_id = f"promoted-{proposal.proposal_id}"
            target_revision = 1
        else:
            if baseline is None:
                raise RepositoryConflictError("target baseline changed")
            baseline_entry, baseline_row = baseline
            self._verify_object(
                connection,
                entry=baseline_entry,
                row=baseline_row,
                label="target baseline",
            )
            if baseline_entry.kind is not source.kind:
                raise RepositoryConflictError(
                    "promotion cannot change the target entry kind"
                )
            target_id = baseline_entry.entry_id
            target_revision = baseline_entry.revision + 1

        target_space_row = self._target_repository._load_space_row(connection)
        target_space_revision = target_space_row["revision"]
        content_ref = None
        object_sha256 = None
        byte_length = None
        if source_object is not None:
            object_sha256, byte_length = source_object
            content_ref = ResourceRef(
                "memory_content",
                f"{target_id}-content",
                target_revision,
                self.target_space_id,
            )
        target = MemoryEntry(
            entry_id=target_id,
            space_id=self.target_space_id,
            path=proposal.target_path,
            name=virtual_name(proposal.target_path),
            description=proposal.reason,
            kind=source.kind,
            revision=target_revision,
            updated_seq=target_space_revision + 1,
            content_ref=content_ref,
            source_refs=proposal.source_refs,
            tags=source.tags,
            media_type=source.media_type,
            link_url=source.link_url,
            deleted=False,
        )
        collision = connection.execute(
            """
            SELECT entry_id FROM entries
            WHERE space_id = ? AND path_key = ? AND deleted = 0
              AND entry_id != ?
            LIMIT 1
            """,
            (self.target_space_id, _path_key(target.path), target.entry_id),
        ).fetchone()
        if collision is not None:
            raise RepositoryConflictError("target baseline changed")
        if source_object is not None:
            catalog = connection.execute(
                "SELECT byte_length FROM objects WHERE sha256 = ?",
                (object_sha256,),
            ).fetchone()
            if catalog is None or catalog["byte_length"] != byte_length:
                raise SQLitePromotionV2StoreIntegrityError(
                    "promotion source object catalog changed"
                )
        self._target_repository._write_entry_revision(
            connection,
            entry=target,
            operation_id=f"promotion:{proposal.proposal_id}",
            object_sha256=object_sha256,
            byte_length=byte_length,
        )
        self._target_repository._advance_space(
            connection,
            current_row=target_space_row,
            expected_revision=target_space_revision,
        )
        return target

    def decide(
        self,
        *,
        ref: ResourceRef,
        expected_revision: int,
        approved: bool,
        confirmation_id: str,
        operation: OperationRef,
    ) -> PromotionProposal:
        self._require_proposal_ref(ref)
        expected = _positive_revision(expected_revision, "expected_revision")
        if not isinstance(approved, bool):
            raise TypeError("approved must be a boolean")
        confirmation = _required_text(
            confirmation_id,
            "confirmation_id",
            identifier=True,
        )
        self._require_operation(operation)

        try:
            with self._store._memory._transaction(immediate=True) as connection:
                self._binding_row(connection)
                receipt = self._receipt(
                    connection,
                    operation=operation,
                    operation_kind="decision",
                )
                if receipt is not None:
                    return self._proposal_for_receipt(connection, receipt)
                proposal_row = self._revision_row(
                    connection,
                    proposal_id=ref.resource_id,
                    revision=ref.revision,
                )
                if proposal_row is None:
                    raise RepositoryNotFoundError(
                        "promotion proposal revision was not found"
                    )
                proposal = self._proposal_from_row(proposal_row)
                head = connection.execute(
                    """
                    SELECT * FROM promotion_proposals
                    WHERE source_space_id = ? AND target_namespace = ?
                      AND target_space_id = ? AND proposal_id = ?
                    """,
                    (
                        self.source_space.space_id,
                        self.target_namespace,
                        self.target_space_id,
                        proposal.proposal_id,
                    ),
                ).fetchone()
                if (
                    head is None
                    or expected != ref.revision
                    or proposal.revision != expected
                    or head["current_revision"] != expected
                    or head["status"] != PromotionStatus.PENDING.value
                    or proposal.status is not PromotionStatus.PENDING
                    or head["source_entry_id"] != proposal.source_entry_ref.resource_id
                    or head["source_entry_revision"]
                    != proposal.source_entry_ref.revision
                    or head["target_path_key"] != _path_key(proposal.target_path)
                ):
                    raise RepositoryConflictError("promotion revision changed")

                applied_ref = None
                status = PromotionStatus.REJECTED
                if approved:
                    target = self._derive_target(connection, proposal=proposal)
                    applied_ref = ResourceRef(
                        "memory",
                        target.entry_id,
                        target.revision,
                        self.target_space_id,
                    )
                    status = PromotionStatus.APPLIED
                decided = replace(
                    proposal,
                    status=status,
                    revision=proposal.revision + 1,
                    applied_entry_ref=applied_ref,
                )
                self._write_revision(
                    connection,
                    proposal=decided,
                    operation_id=operation.operation_id,
                    confirmation_id=confirmation,
                )
                updated = connection.execute(
                    """
                    UPDATE promotion_proposals
                    SET current_revision = ?, status = ?
                    WHERE source_space_id = ? AND target_namespace = ?
                      AND target_space_id = ? AND proposal_id = ?
                      AND current_revision = ? AND status = ?
                    """,
                    (
                        decided.revision,
                        decided.status.value,
                        self.source_space.space_id,
                        self.target_namespace,
                        self.target_space_id,
                        proposal.proposal_id,
                        expected,
                        PromotionStatus.PENDING.value,
                    ),
                )
                if updated.rowcount != 1:
                    raise RepositoryConflictError("promotion revision changed")
                self._claim_receipt(
                    connection,
                    operation=operation,
                    operation_kind="decision",
                    proposal=decided,
                )
                return decided
        except (RepositoryConflictError, RepositoryNotFoundError, RepositoryScopeError):
            raise
        except SQLiteMemoryV2StoreIntegrityError as exc:
            raise SQLitePromotionV2StoreIntegrityError(
                "promotion CAS content is missing or corrupt"
            ) from exc
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflictError(
                "promotion decision operation or baseline conflicted"
            ) from exc
        except sqlite3.Error as exc:
            raise SQLitePromotionV2StoreError(
                "SQLite promotion decision failed"
            ) from exc
