"""Independent AC-015 review of an unlocked official reader closing its WAL."""

import threading
import time

import pytest

import test_graph_interaction_resume_checkpoint as fixture
from unchain.persistence import (
    SQLiteContextV2StoreError,
    open_existing_execution_journal_readonly,
)


def test_official_reader_close_between_wal_probe_and_connect_creates_no_files(
    tmp_path, monkeypatch,
):
    """Migrated 2026-09-05: the mutex serializes real cross-thread closers.

    The original single-thread version closed the last reader from inside
    the ``_wal_exists`` hook itself.  A same-thread ``RLock`` is reentrant by
    design, so no correct mutex implementation can make that interleaving
    fail; it tested an interleaving the mutex was never meant to prevent.
    This keeps the same fixture and assertions but puts the closing reader on
    a real second thread, and asserts the mutex actually serialized it
    (the probe cannot run until the closer's connection is gone) instead of
    merely asserting the end state.
    """

    store, _, _, sinks, _, _ = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    database = tmp_path / "memory_v2" / "context_v2.sqlite3"
    reader = open_existing_execution_journal_readonly(
        database_path=database, execution_id=fixture.GENERATION.execution_id,
    )
    original_probe = reader._wal_exists
    probe_results: list = []
    keeper_open = threading.Event()
    release_keeper = threading.Event()
    closed_at: list = []
    at_close: set = set()

    def recording_probe():
        exists = original_probe()
        probe_results.append(exists)
        return exists

    monkeypatch.setattr(reader, "_wal_exists", recording_probe)

    def hold_last_official_reader():
        with store._transaction(immediate=False) as connection:
            connection.execute("SELECT count(*) FROM events").fetchone()
            keeper_open.set()
            release_keeper.wait(timeout=10)
        closed_at.append(time.monotonic())
        at_close.update(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    keeper = threading.Thread(target=hold_last_official_reader)
    keeper.start()
    assert keeper_open.wait(timeout=5)
    assert original_probe()  # WAL exists while the official reader is open

    preflight = threading.Thread(target=reader.capture_snapshot)
    preflight.start()
    preflight.join(timeout=0.5)
    assert preflight.is_alive() and not probe_results, "probe ran before the reader closed"
    release_keeper.set()
    keeper.join(timeout=5)
    preflight.join(timeout=5)
    assert not preflight.is_alive()
    assert closed_at and probe_results == [False]
    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    assert after == at_close, f"Readonly created {after - at_close}"


def test_wal_without_shm_is_rejected_without_creating_files(tmp_path):
    _, _, _, sinks, _, _ = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    database = tmp_path / "memory_v2" / "context_v2.sqlite3"
    wal = database.with_name(database.name + "-wal")
    shm = database.with_name(database.name + "-shm")
    assert not wal.exists()
    wal.write_bytes(b"")  # crash-shaped leftover: WAL present, SHM absent
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    with pytest.raises(SQLiteContextV2StoreError, match="needs recovery"):
        open_existing_execution_journal_readonly(
            database_path=database, execution_id=fixture.GENERATION.execution_id,
        )
    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    assert after == before and not shm.exists()


def test_plain_reader_close_is_serialized_behind_existing_only_snapshot(tmp_path):
    store, _, _, sinks, _, _ = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    database = tmp_path / "memory_v2" / "context_v2.sqlite3"
    reader = open_existing_execution_journal_readonly(
        database_path=database, execution_id=fixture.GENERATION.execution_id,
    )
    keeper_open = threading.Event()
    release_keeper = threading.Event()
    keeper_closed_at: list = []

    def hold_plain_reader():
        with store._transaction(immediate=False) as connection:
            connection.execute("SELECT count(*) FROM events").fetchone()
            keeper_open.set()
            release_keeper.wait(timeout=10)
        keeper_closed_at.append(time.monotonic())

    keeper = threading.Thread(target=hold_plain_reader)
    keeper.start()
    assert keeper_open.wait(timeout=5)
    assert database.with_name(database.name + "-wal").exists()

    snapshot_done_at: list = []
    snapshot_error: list = []

    def snapshot():
        try:
            reader.capture_snapshot()
            snapshot_done_at.append(time.monotonic())
        except BaseException as exc:  # pragma: no cover - surfaced below
            snapshot_error.append(exc)

    preflight = threading.Thread(target=snapshot)
    preflight.start()
    # The preflight must block behind the plain reader instead of racing it.
    preflight.join(timeout=0.5)
    assert preflight.is_alive(), "existing-only snapshot ran concurrently with a plain reader"
    release_keeper.set()
    keeper.join(timeout=5)
    preflight.join(timeout=5)
    assert not preflight.is_alive() and not snapshot_error
    assert keeper_closed_at and snapshot_done_at
    assert keeper_closed_at[0] <= snapshot_done_at[0]
    assert not database.with_name(database.name + "-wal").exists()
    assert not database.with_name(database.name + "-shm").exists()
