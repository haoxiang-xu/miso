from __future__ import annotations

import copy
import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from unchain.memory import (
    InMemorySessionStore,
    JsonFileSessionStore,
    RevisionedSessionStore,
    SessionRevisionConflictError,
    SessionSnapshot,
    SessionStoreCorruptionError,
    load_session_snapshot,
    save_session_snapshot,
)


class _LegacySessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}

    def load(self, session_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.sessions.get(session_id, {}))

    def save(self, session_id: str, state: dict[str, Any]) -> None:
        self.sessions[session_id] = copy.deepcopy(state)


def _json_process_cas_worker(
    base_dir: str,
    session_id: str,
    value: str,
    start: Any,
    ready: Any,
    results: Any,
) -> None:
    store = JsonFileSessionStore(base_dir)
    ready.put(value)
    if not start.wait(timeout=15):
        results.put(("timeout", value, None))
        return
    try:
        revision = store.save_if_revision(session_id, {"winner": value}, 0)
    except SessionRevisionConflictError as exc:
        results.put(("conflict", value, exc.actual_revision))
    else:
        results.put(("saved", value, revision))


def test_legacy_store_helpers_are_explicitly_best_effort() -> None:
    store = _LegacySessionStore()

    loaded = load_session_snapshot(store, "legacy")
    assert loaded == SessionSnapshot(state={}, revision=None)
    assert loaded.revision_supported is False
    assert loaded.consistency == "best_effort"

    saved = save_session_snapshot(
        store,
        "legacy",
        {"messages": [{"role": "user", "content": "hello"}]},
        expected_revision=99,
    )

    assert saved.revision is None
    assert saved.consistency == "best_effort"
    assert store.load("legacy") == saved.state


@pytest.mark.parametrize("missing_method", ["load_with_revision", "save_if_revision"])
def test_partial_revision_capability_fails_closed(missing_method: str) -> None:
    class PartialStore(_LegacySessionStore):
        def load_with_revision(self, session_id: str) -> SessionSnapshot:
            return SessionSnapshot(state=self.load(session_id), revision=0)

        def save_if_revision(
            self,
            session_id: str,
            state: dict[str, Any],
            expected_revision: int,
        ) -> int:
            del expected_revision
            self.save(session_id, state)
            return 1

    store = PartialStore()
    setattr(store, missing_method, None)

    with pytest.raises(TypeError, match="must be implemented together"):
        load_session_snapshot(store, "partial")
    with pytest.raises(TypeError, match="must be implemented together"):
        save_session_snapshot(
            store,
            "partial",
            {"owner": "stale"},
            expected_revision=0,
        )


def test_revisioned_store_requires_expected_revision_for_helper_save() -> None:
    store = InMemorySessionStore()

    with pytest.raises(ValueError, match="expected_revision is required"):
        save_session_snapshot(
            store,
            "missing-fence",
            {"unsafe": True},
            expected_revision=None,
        )


def test_in_memory_store_rejects_a_stale_writer() -> None:
    store = InMemorySessionStore()
    assert isinstance(store, RevisionedSessionStore)

    initial = load_session_snapshot(store, "shared")
    assert initial.revision == 0
    assert initial.consistency == "compare_and_swap"

    first = save_session_snapshot(
        store,
        "shared",
        {"owner": "first"},
        expected_revision=initial.revision,
    )
    assert first == SessionSnapshot(state={"owner": "first"}, revision=1)

    with pytest.raises(SessionRevisionConflictError) as exc_info:
        save_session_snapshot(
            store,
            "shared",
            {"owner": "stale"},
            expected_revision=initial.revision,
        )

    assert exc_info.value.code == "session_revision_conflict"
    assert exc_info.value.expected_revision == 0
    assert exc_info.value.actual_revision == 1
    assert store.load("shared") == {"owner": "first"}


def test_in_memory_compare_and_swap_has_exactly_one_thread_winner() -> None:
    store = InMemorySessionStore()
    barrier = Barrier(2)

    def write(value: str) -> tuple[str, int]:
        barrier.wait(timeout=5)
        try:
            revision = store.save_if_revision("shared", {"winner": value}, 0)
        except SessionRevisionConflictError as exc:
            return "conflict", exc.actual_revision
        return "saved", revision

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["one", "two"]))

    assert sorted(status for status, _ in results) == ["conflict", "saved"]
    assert {revision for _, revision in results} == {1}
    assert store.load_with_revision("shared").revision == 1


def test_unconditional_in_memory_save_advances_the_revision() -> None:
    store = InMemorySessionStore()

    store.save("session", {"value": 1})
    first = store.load_with_revision("session")
    store.save("session", {"value": 2})
    second = store.load_with_revision("session")

    assert first == SessionSnapshot(state={"value": 1}, revision=1)
    assert second == SessionSnapshot(state={"value": 2}, revision=2)


@pytest.mark.parametrize("expected_revision", [True, -1, 1.5])
def test_revisioned_stores_reject_invalid_expected_revision(
    tmp_path: Path,
    expected_revision: Any,
) -> None:
    stores = [InMemorySessionStore(), JsonFileSessionStore(tmp_path)]

    for store in stores:
        with pytest.raises(ValueError, match="non-negative integer"):
            store.save_if_revision("invalid", {}, expected_revision)


def test_json_store_migrates_legacy_state_without_exposing_revision(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"messages": [{"role": "user", "content": "old"}]}), encoding="utf-8")
    store = JsonFileSessionStore(tmp_path)

    initial = load_session_snapshot(store, "legacy")
    assert initial.revision == 0
    assert initial.state["messages"][0]["content"] == "old"

    saved = save_session_snapshot(
        store,
        "legacy",
        {"messages": [{"role": "user", "content": "new"}]},
        expected_revision=initial.revision,
    )

    assert saved.revision == 1
    assert store.load("legacy") == saved.state
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["__unchain_session_revision__"] == 1
    assert "__unchain_session_revision__" not in store.load("legacy")


def test_json_store_rejects_cross_instance_stale_writer(tmp_path: Path) -> None:
    first = JsonFileSessionStore(tmp_path)
    second = JsonFileSessionStore(tmp_path)
    snapshot = first.load_with_revision("shared")

    assert first.save_if_revision("shared", {"owner": "first"}, snapshot.revision or 0) == 1
    with pytest.raises(SessionRevisionConflictError):
        second.save_if_revision("shared", {"owner": "stale"}, snapshot.revision or 0)

    assert second.load("shared") == {"owner": "first"}


def test_json_store_file_lock_allows_exactly_one_process_winner(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    ready = context.Queue()
    results = context.Queue()
    processes = [
        context.Process(
            target=_json_process_cas_worker,
            args=(str(tmp_path), "process-shared", value, start, ready, results),
        )
        for value in ("one", "two")
    ]
    for process in processes:
        process.start()
    try:
        assert {ready.get(timeout=15), ready.get(timeout=15)} == {"one", "two"}
        start.set()
        outcomes = [results.get(timeout=15), results.get(timeout=15)]
    finally:
        start.set()
        for process in processes:
            process.join(timeout=15)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert all(process.exitcode == 0 for process in processes)
    assert sorted(outcome[0] for outcome in outcomes) == ["conflict", "saved"]
    assert {outcome[2] for outcome in outcomes} == {1}
    snapshot = JsonFileSessionStore(tmp_path).load_with_revision("process-shared")
    assert snapshot.revision == 1
    assert snapshot.state["winner"] in {"one", "two"}


@pytest.mark.parametrize(
    "contents",
    [
        "{not-json",
        "[]",
        '{"__unchain_session_revision__": true}',
        '{"__unchain_session_revision__": -1}',
    ],
)
def test_json_store_corruption_fails_closed(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "corrupt.json"
    path.write_text(contents, encoding="utf-8")
    store = JsonFileSessionStore(tmp_path)

    with pytest.raises(SessionStoreCorruptionError) as load_error:
        store.load("corrupt")
    assert load_error.value.code == "session_store_corruption"

    with pytest.raises(SessionStoreCorruptionError):
        store.save("corrupt", {"replacement": True})
    assert path.read_text(encoding="utf-8") == contents


def test_json_atomic_write_preserves_previous_state_on_serialization_failure(tmp_path: Path) -> None:
    store = JsonFileSessionStore(tmp_path)
    store.save("atomic", {"value": "before"})
    path = tmp_path / "atomic.json"
    before = path.read_bytes()
    recursive: dict[str, Any] = {}
    recursive["self"] = recursive

    with pytest.raises(ValueError, match="Circular reference"):
        store.save("atomic", recursive)

    assert path.read_bytes() == before
    assert store.load_with_revision("atomic") == SessionSnapshot(
        state={"value": "before"},
        revision=1,
    )
    assert list(tmp_path.glob(".atomic.json.*.tmp")) == []
