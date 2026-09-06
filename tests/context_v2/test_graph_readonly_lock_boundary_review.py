"""Independent AC-015 review of an unlocked official reader closing its WAL."""

import threading
import time

import test_graph_interaction_resume_checkpoint as fixture
from unchain.persistence import open_existing_execution_journal_readonly


def test_official_reader_close_between_wal_probe_and_connect_creates_no_files(
    tmp_path, monkeypatch,
):
    store, _, _, sinks, _, _ = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    database = tmp_path / "memory_v2" / "context_v2.sqlite3"
    reader = open_existing_execution_journal_readonly(
        database_path=database, execution_id=fixture.GENERATION.execution_id,
    )
    keeper = store._transaction(immediate=False)
    connection = keeper.__enter__()
    connection.execute("SELECT count(*) FROM events").fetchone()
    original_probe = reader._wal_exists
    closed = False
    at_connect = set()

    def close_last_official_reader():
        nonlocal closed, at_connect
        exists = original_probe()
        assert exists
        keeper.__exit__(None, None, None)
        closed = True
        assert not original_probe()
        at_connect = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
        return exists

    monkeypatch.setattr(reader, "_wal_exists", close_last_official_reader)
    try:
        reader.capture_snapshot()
    finally:
        if not closed:
            keeper.__exit__(None, None, None)
    assert closed
    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    assert after == at_connect, f"Readonly created {after - at_connect}"


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
