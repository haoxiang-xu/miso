"""BC-007/R06: the WAL can appear and disappear between existence checks."""

import sqlite3

import pytest

import test_graph_interaction_resume_checkpoint as fixture
from unchain.context.graph_checkpoint import (
    GraphCheckpointError,
    prove_graph_interaction_lineage,
)
from unchain.persistence import (
    SQLiteContextV2StoreError,
    open_existing_execution_journal_readonly,
)


def test_single_snapshot_cannot_mix_plan_before_and_checkpoint_after_resolution(
    tmp_path, monkeypatch
):
    _, journal, projectors, sinks, _, _ = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    database = tmp_path / "memory_v2" / "context_v2.sqlite3"
    reader = open_existing_execution_journal_readonly(
        database_path=database, execution_id=fixture.GENERATION.execution_id
    )
    identity = dict(
        generation_id=fixture.GENERATION.generation_id,
        coordinator_attempt_id=fixture.ORCHESTRATION.attempt_id,
        source_attempt_id=fixture.STEP.attempt_id,
        current_attempt_id=fixture.STEP.attempt_id,
        interaction_id=fixture.INTERACTION_ID,
    )
    real_exists = reader._wal_exists
    checks = 0

    def interleave_writer():
        nonlocal checks
        checks += 1
        if checks == 1:
            existed = real_exists()
            assert not existed
            # The old implementation performed plan lookup before this write
            # and checkpoint scan afterwards, then returned a hybrid proof.
            # The new implementation acquires exactly one snapshot after this
            # boundary, so it must see the real canonical resolution.
            fixture._resolve(
                projectors[fixture.STEP], sinks[fixture.STEP], fixture.INTERACTION_ID
            )
            return existed
        return real_exists()

    monkeypatch.setattr(reader, "_wal_exists", interleave_writer)
    rejected = None
    try:
        prove_graph_interaction_lineage(reader, **identity)
    except (GraphCheckpointError, SQLiteContextV2StoreError) as exc:
        rejected = exc
    assert checks == 1
    with pytest.raises(GraphCheckpointError):
        prove_graph_interaction_lineage(journal, **identity)
    assert rejected is not None, "One snapshot granted a resolved interaction proof"


def test_wal_reader_does_not_create_files_after_writer_closes(tmp_path):
    _, _, _, sinks, _, _ = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    database = tmp_path / "memory_v2" / "context_v2.sqlite3"
    keeper = sqlite3.connect(database)
    try:
        keeper.execute("SELECT count(*) FROM events").fetchone()
        assert database.with_name(database.name + "-wal").exists()
        reader = open_existing_execution_journal_readonly(
            database_path=database, execution_id=fixture.GENERATION.execution_id
        )
    finally:
        keeper.close()
    assert not database.with_name(database.name + "-wal").exists()
    reader = open_existing_execution_journal_readonly(
        database_path=database, execution_id=fixture.GENERATION.execution_id
    )
    before = set(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    reader.capture_snapshot()
    after = set(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert after == before, f"Read-only snapshot created files: {after - before}"
