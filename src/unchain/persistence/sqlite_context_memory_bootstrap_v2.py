"""Atomic bootstrap for an empty Context/Memory V2 SQLite data plane."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from .sqlite_chat_deletion_v2 import SQLiteChatDeletionV2Service
from .sqlite_context_compiler_v2 import SQLiteContextCompilerV2Store
from .sqlite_curator_v2 import SQLiteCuratorV2Store
from .sqlite_legacy_bootstrap_v2 import SQLiteLegacyBootstrapService
from .sqlite_memory_host_v2 import initialize_sqlite_memory_host_v2_schema
from .sqlite_memory_v2 import SQLiteMemoryV2Store
from .sqlite_promotion_v2 import SQLitePromotionV2Store
from .sqlite_v2 import SQLiteContextV2Store


class SQLiteContextMemoryBootstrapError(RuntimeError):
    """An empty Context/Memory V2 data plane could not be published safely."""


def _exact_paths(
    *,
    database_path: str | Path,
    object_directory: str | Path,
) -> tuple[Path, Path]:
    database = Path(database_path).expanduser().resolve()
    objects = Path(object_directory).expanduser().resolve()
    if database.name != "context_v2.sqlite3":
        raise ValueError("database_path must name context_v2.sqlite3")
    if objects != database.parent / "objects":
        raise ValueError("object_directory must be the database sibling objects")
    return database, objects


def _checkpoint(database_path: Path) -> None:
    connection = sqlite3.connect(database_path, timeout=30.0, isolation_level=None)
    try:
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None or int(checkpoint[0]) != 0:
            raise SQLiteContextMemoryBootstrapError("SQLite checkpoint is unavailable")
    finally:
        connection.close()


def bootstrap_empty_context_memory_v2_database(
    *,
    database_path: str | Path,
    object_directory: str | Path,
) -> bool:
    """Publish a complete empty schema only when the target database is absent.

    A complete candidate is built under a sibling temporary directory and
    published with an exclusive hard link.  A concurrent publisher therefore
    cannot overwrite a tombstone or leave an extension-only partial target.
    The caller may retry after ``False`` and open the database normally.
    """

    database, objects = _exact_paths(
        database_path=database_path,
        object_directory=object_directory,
    )
    if database.exists():
        return False
    if objects.exists() and (not objects.is_dir() or any(objects.iterdir())):
        raise SQLiteContextMemoryBootstrapError(
            "empty database bootstrap refuses a non-empty object directory"
        )

    database.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".context-memory-v2-bootstrap-",
        dir=database.parent,
    ) as temporary_root:
        candidate_root = Path(temporary_root)
        candidate_database = candidate_root / database.name
        candidate_objects = candidate_root / "objects"
        context = SQLiteContextV2Store(
            database_path=candidate_database,
            object_directory=candidate_objects,
        )
        SQLiteContextCompilerV2Store(context_store=context)
        SQLiteMemoryV2Store(
            database_path=candidate_database,
            object_directory=candidate_objects,
        )
        SQLiteCuratorV2Store(
            database_path=candidate_database,
            object_directory=candidate_objects,
        )
        SQLitePromotionV2Store(
            database_path=candidate_database,
            object_directory=candidate_objects,
        )
        SQLiteLegacyBootstrapService(context)
        initialize_sqlite_memory_host_v2_schema(database_path=candidate_database)
        SQLiteChatDeletionV2Service(database_path=candidate_database)
        _checkpoint(candidate_database)
        try:
            os.link(candidate_database, database)
        except FileExistsError:
            return False

    objects.mkdir(parents=True, exist_ok=True)
    return True


__all__ = [
    "SQLiteContextMemoryBootstrapError",
    "bootstrap_empty_context_memory_v2_database",
]
