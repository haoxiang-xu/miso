"""Independent BC-007 / R06 regression: a live journal is not immutable."""

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


def test_readonly_lineage_rejects_resolution_committed_after_open(tmp_path):
    _, journal, projectors, sinks, _, _ = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    database = tmp_path / "memory_v2" / "context_v2.sqlite3"
    assert not database.with_name(database.name + "-wal").exists()
    reader = open_existing_execution_journal_readonly(
        database_path=database,
        execution_id=fixture.GENERATION.execution_id,
    )
    identity = dict(
        generation_id=fixture.GENERATION.generation_id,
        coordinator_attempt_id=fixture.ORCHESTRATION.attempt_id,
        source_attempt_id=fixture.STEP.attempt_id,
        current_attempt_id=fixture.STEP.attempt_id,
        interaction_id=fixture.INTERACTION_ID,
    )
    prove_graph_interaction_lineage(reader, **identity)

    # A real second connection keeps the WAL alive, as another sidecar reader
    # would. The answer is committed through the real canonical ingress.
    keeper = sqlite3.connect(database)
    try:
        keeper.execute("SELECT count(*) FROM events").fetchone()
        fixture._resolve(
            projectors[fixture.STEP], sinks[fixture.STEP], fixture.INTERACTION_ID
        )
        assert database.with_name(database.name + "-wal").exists()
        with pytest.raises(GraphCheckpointError):
            prove_graph_interaction_lineage(journal, **identity)
        with pytest.raises((GraphCheckpointError, SQLiteContextV2StoreError)):
            prove_graph_interaction_lineage(reader, **identity)
    finally:
        keeper.close()
