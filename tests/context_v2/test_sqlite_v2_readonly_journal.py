from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from unchain.journal import JournalRepositoryError
from unchain.persistence.sqlite_v2 import (
    SQLiteContextV2Store,
    SQLiteContextV2StoreError,
    open_existing_execution_journal_readonly,
)


def test_existing_readonly_journal_never_initializes_an_absent_data_plane(tmp_path):
    database = tmp_path / "absent" / "context_v2.sqlite3"

    with pytest.raises(SQLiteContextV2StoreError, match="database is unavailable"):
        open_existing_execution_journal_readonly(
            database_path=database,
            execution_id="readonly-missing-execution",
        )

    assert not database.parent.exists()


def test_existing_readonly_journal_verifies_without_writing_or_creating_objects(
    tmp_path,
):
    database = tmp_path / "memory_v2" / "context_v2.sqlite3"
    objects = tmp_path / "memory_v2" / "objects"
    store = SQLiteContextV2Store(
        database_path=database,
        object_directory=objects,
    )
    store.bind_execution("readonly-existing-execution")
    before_database = database.read_bytes()
    before_entries = tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")))

    journal = open_existing_execution_journal_readonly(
        database_path=database,
        execution_id="readonly-existing-execution",
    )

    assert journal.capture_snapshot().events == ()
    with pytest.raises(JournalRepositoryError, match="read-only journal"):
        journal.append(request=object())
    assert database.read_bytes() == before_database
    assert tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))) == before_entries


def test_existing_readonly_journal_is_side_effect_free_in_a_new_process(tmp_path):
    database = tmp_path / "memory_v2" / "context_v2.sqlite3"
    objects = tmp_path / "memory_v2" / "objects"
    store = SQLiteContextV2Store(
        database_path=database,
        object_directory=objects,
    )
    store.bind_execution("readonly-new-process-execution")
    before_database = database.read_bytes()
    before_entries = tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")))
    source_root = str((Path(__file__).parents[2] / "src").resolve())
    script = "\n".join(
        (
            "from unchain.persistence.sqlite_v2 import open_existing_execution_journal_readonly",
            f"journal = open_existing_execution_journal_readonly(database_path={str(database)!r}, execution_id='readonly-new-process-execution')",
            "assert journal.capture_snapshot().events == ()",
        )
    )
    environment = dict(os.environ, PYTHONPATH=source_root)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert database.read_bytes() == before_database
    assert tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))) == before_entries
