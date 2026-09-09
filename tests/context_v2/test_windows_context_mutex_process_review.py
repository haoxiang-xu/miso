"""Real Windows processes exercise the named mutex and existing-only read."""

import os
import subprocess
import sys

import pytest
import test_graph_interaction_resume_checkpoint as fixture
from unchain.persistence.sqlite_v2 import serialized_context_v2_database_access


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex acceptance")
def test_new_process_read_waits_for_named_mutex_and_creates_no_files(tmp_path):
    _, _, _, sinks, _, _ = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    database = tmp_path / "memory_v2" / "context_v2.sqlite3"
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}
    script = """
import sys
from unchain.persistence import open_existing_execution_journal_readonly
print('ready', flush=True)
reader = open_existing_execution_journal_readonly(database_path=sys.argv[1], execution_id=sys.argv[2])
assert reader.capture_snapshot().events
print('snapshot-ok', flush=True)
"""
    with serialized_context_v2_database_access(database):
        child = subprocess.Popen(
            [sys.executable, "-c", script, str(database), fixture.GENERATION.execution_id],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            assert child.stdout.readline().strip() == "ready"
            with pytest.raises(subprocess.TimeoutExpired):
                child.wait(timeout=0.5)
        except BaseException:
            child.kill()
            child.communicate(timeout=10)
            raise
    stdout, stderr = child.communicate(timeout=15)
    assert child.returncode == 0, stderr
    assert stdout.strip() == "snapshot-ok"
    after = {str(path.relative_to(tmp_path)): path.read_bytes()
             for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
