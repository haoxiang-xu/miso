"""Public, namespace-bound reads for durable long-term Memory V2 state.

The caller supplies a logical namespace and a host binding identifier, never a
physical workspace identifier.  The physical space is resolved from the
durable promotion namespace binding and then exposed only through the
read-only :class:`LongTermMemoryService` capability.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from unchain.journal.models import _required_text
from unchain.memory.workspace import (
    LongTermMemoryService,
    MemorySpace,
    VectorIndex,
    WorkspaceSearchService,
)
from unchain.memory.workspace.ports import (
    RepositoryNotFoundError,
    RepositoryScopeError,
    WorkspaceRepositoryError,
)

from .sqlite_memory_v2 import SQLiteMemoryV2Store
from .sqlite_promotion_v2 import SQLitePromotionV2Store


class SQLiteLongTermMemoryV2ReadError(WorkspaceRepositoryError):
    """The durable namespace binding cannot be opened safely."""


@dataclass(frozen=True, slots=True)
class SQLiteLongTermMemoryV2ReadScope:
    """Logical long-term scope selected by a trusted host."""

    namespace: str
    binding_id: str

    def __post_init__(self) -> None:
        namespace = _required_text(
            self.namespace,
            "namespace",
            identifier=True,
        )
        binding_id = _required_text(
            self.binding_id,
            "binding_id",
            maximum=512,
            identifier=True,
        )
        if namespace == "chat":
            raise RepositoryScopeError(
                "long-term reads cannot bind a chat namespace"
            )
        object.__setattr__(self, "namespace", namespace)
        object.__setattr__(self, "binding_id", binding_id)


class SQLiteLongTermMemoryV2ReadService:
    """Resolve durable namespace bindings into read-only memory capabilities."""

    def __init__(
        self,
        *,
        database_path: str | os.PathLike[str],
        object_directory: str | os.PathLike[str],
        vector_index: VectorIndex | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.object_directory = Path(object_directory)
        self._memory = SQLiteMemoryV2Store(
            database_path=self.database_path,
            object_directory=self.object_directory,
        )
        # Promotion storage owns the namespace-binding schema.  Constructing it
        # is idempotent and keeps schema ownership out of product hosts.
        SQLitePromotionV2Store(
            database_path=self.database_path,
            object_directory=self.object_directory,
        )
        self._vector_index = vector_index

    def bind(
        self,
        scope: SQLiteLongTermMemoryV2ReadScope,
    ) -> LongTermMemoryService:
        if not isinstance(scope, SQLiteLongTermMemoryV2ReadScope):
            raise TypeError("scope must be a SQLiteLongTermMemoryV2ReadScope")
        space = self._resolve_space(scope.namespace)
        repository = self._memory.bind_workspace(
            space=space,
            owner_chat_id=None,
        )
        if (
            repository.space.space_id != space.space_id
            or repository.space.namespace != scope.namespace
        ):
            raise RepositoryScopeError("long-term namespace binding changed")
        search = WorkspaceSearchService(
            repository=repository,
            vector_index=self._vector_index,
        )
        return LongTermMemoryService(
            binding_id=scope.binding_id,
            repository=repository,
            content=repository,
            history=repository,
            search=search,
        )

    def _resolve_space(self, namespace: str) -> MemorySpace:
        try:
            connection = sqlite3.connect(
                self.database_path,
                timeout=30.0,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 30000")
                row = connection.execute(
                    """
                    SELECT s.*, b.target_namespace AS bound_namespace
                    FROM promotion_namespace_bindings AS b
                    JOIN spaces AS s
                      ON s.space_id = b.target_space_id
                    WHERE b.target_namespace = ?
                    """,
                    (namespace,),
                ).fetchone()
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise SQLiteLongTermMemoryV2ReadError(
                "SQLite long-term namespace lookup failed"
            ) from error
        if row is None:
            raise RepositoryNotFoundError(
                "long-term namespace binding was not found"
            )
        if row["owner_chat_id"] != "" or row["bound_namespace"] != namespace:
            raise RepositoryScopeError(
                "long-term namespace belongs to another durable scope"
            )
        try:
            space = self._memory._space_from_row(row)
        except WorkspaceRepositoryError:
            raise
        except (TypeError, ValueError) as error:
            raise SQLiteLongTermMemoryV2ReadError(
                "long-term namespace metadata is invalid"
            ) from error
        if space.namespace != namespace:
            raise RepositoryScopeError("long-term namespace binding changed")
        return space


def open_sqlite_long_term_memory_v2(
    *,
    database_path: str | os.PathLike[str],
    object_directory: str | os.PathLike[str],
    scope: SQLiteLongTermMemoryV2ReadScope,
    vector_index: VectorIndex | None = None,
) -> LongTermMemoryService:
    """Cold-open one exact namespace-bound long-term read capability."""

    return SQLiteLongTermMemoryV2ReadService(
        database_path=database_path,
        object_directory=object_directory,
        vector_index=vector_index,
    ).bind(scope)


__all__ = [
    "SQLiteLongTermMemoryV2ReadError",
    "SQLiteLongTermMemoryV2ReadScope",
    "SQLiteLongTermMemoryV2ReadService",
    "open_sqlite_long_term_memory_v2",
]
