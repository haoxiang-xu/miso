from __future__ import annotations

import multiprocessing
from pathlib import Path
from typing import Any, Callable

import pytest

from unchain.execution import (
    ActiveExecutionLeaseError,
    ExecutionFence,
    ExecutionLeaseConfig,
    ExecutionLeaseConflictError,
    ExecutionLeaseExpiredError,
    ExecutionLeaseNotOwnedError,
    ExecutionRuntime,
    StaleExecutionLeaseError,
)
from unchain.memory import (
    InMemorySessionStore,
    JsonFileSessionStore,
    KernelMemoryRuntime,
    SessionRevisionConflictError,
    SessionSnapshot,
    load_session_snapshot,
    save_session_snapshot,
)
from unchain.runtime import build_runtime_loop


class ManualClock:
    """Deterministic millisecond clock for lease expiry tests."""

    def __init__(self, now_ms: int = 1_000) -> None:
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, delta_ms: int) -> None:
        self.now_ms += delta_ms


StoreFactory = Callable[[ManualClock, Path], Any]


@pytest.fixture(params=["memory", "json"])
def store_factory(request: pytest.FixtureRequest) -> StoreFactory:
    if request.param == "memory":
        return lambda clock, _path: InMemorySessionStore(clock_ms=clock)
    return lambda clock, path: JsonFileSessionStore(path, clock_ms=clock)


def _fence(lease: Any) -> ExecutionFence:
    return ExecutionFence(
        execution_id=lease.execution_id,
        owner_id=lease.owner_id,
        fencing_token=lease.fencing_token,
    )


def _json_process_acquire_worker(
    base_dir: str,
    owner_id: str,
    start: Any,
    ready: Any,
    results: Any,
) -> None:
    store = JsonFileSessionStore(base_dir)
    ready.put(owner_id)
    if not start.wait(timeout=15):
        results.put(("timeout", owner_id, None))
        return
    try:
        lease = store.acquire_lease(
            "process-shared",
            owner_id,
            60_000,
            expected_revision=0,
        )
    except ExecutionLeaseConflictError:
        results.put(("conflict", owner_id, None))
    else:
        results.put(("acquired", owner_id, lease.fencing_token))


def test_first_acquire_uses_token_one_and_same_owner_is_idempotent(
    store_factory: StoreFactory,
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    store = store_factory(clock, tmp_path)

    first = store.acquire_lease(
        "shared",
        "owner-a",
        100,
        expected_revision=0,
    )
    repeated = store.acquire_lease(
        "shared",
        "owner-a",
        250,
        expected_revision=0,
    )

    assert first.execution_id == "shared"
    assert first.owner_id == "owner-a"
    assert first.fencing_token == 1
    assert repeated.execution_id == first.execution_id
    assert repeated.owner_id == first.owner_id
    assert repeated.fencing_token == first.fencing_token
    assert store.verify_lease("shared", "owner-a", 1).fencing_token == 1


def test_active_lease_rejects_a_second_owner(
    store_factory: StoreFactory,
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    store = store_factory(clock, tmp_path)
    store.acquire_lease("shared", "owner-a", 100, expected_revision=0)

    with pytest.raises(ExecutionLeaseConflictError):
        store.acquire_lease("shared", "owner-b", 100, expected_revision=0)

    assert store.verify_lease("shared", "owner-a", 1).owner_id == "owner-a"


def test_exact_expiry_allows_takeover_and_permanently_fences_old_owner(
    store_factory: StoreFactory,
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    store = store_factory(clock, tmp_path)
    old_lease = store.acquire_lease(
        "shared",
        "owner-a",
        100,
        expected_revision=0,
    )

    clock.advance(99)
    with pytest.raises(ExecutionLeaseConflictError):
        store.acquire_lease("shared", "owner-b", 100, expected_revision=0)

    clock.advance(1)
    with pytest.raises(ExecutionLeaseExpiredError):
        store.verify_lease("shared", "owner-a", old_lease.fencing_token)

    new_lease = store.acquire_lease(
        "shared",
        "owner-b",
        100,
        expected_revision=0,
    )
    assert new_lease.fencing_token == old_lease.fencing_token + 1

    with pytest.raises(StaleExecutionLeaseError):
        store.verify_lease("shared", "owner-a", old_lease.fencing_token)
    with pytest.raises(StaleExecutionLeaseError):
        store.renew_lease(
            "shared",
            "owner-a",
            old_lease.fencing_token,
            100,
        )
    with pytest.raises(StaleExecutionLeaseError):
        store.release_lease("shared", "owner-a", old_lease.fencing_token)
    with pytest.raises(StaleExecutionLeaseError):
        store.save_if_revision_and_fence(
            "shared",
            {"owner": "stale"},
            0,
            execution_id="shared",
            owner_id="owner-a",
            fencing_token=old_lease.fencing_token,
        )
    with pytest.raises(ExecutionLeaseNotOwnedError):
        store.verify_lease("shared", "owner-a", new_lease.fencing_token)

    assert store.verify_lease(
        "shared",
        "owner-b",
        new_lease.fencing_token,
    ).owner_id == "owner-b"
    assert store.save_if_revision_and_fence(
        "shared",
        {"owner": "owner-b"},
        0,
        execution_id="shared",
        owner_id="owner-b",
        fencing_token=new_lease.fencing_token,
    ) == 1
    assert store.load_with_revision("shared") == SessionSnapshot(
        state={"owner": "owner-b"},
        revision=1,
    )


def test_release_does_not_reset_the_fencing_token(
    store_factory: StoreFactory,
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    store = store_factory(clock, tmp_path)
    first = store.acquire_lease("shared", "owner-a", 100, expected_revision=0)

    store.release_lease("shared", "owner-a", first.fencing_token)
    second = store.acquire_lease("shared", "owner-b", 100, expected_revision=0)

    assert first.fencing_token == 1
    assert second.fencing_token == 2


def test_lease_operations_do_not_advance_session_revision(
    store_factory: StoreFactory,
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    store = store_factory(clock, tmp_path)
    assert load_session_snapshot(store, "shared").revision == 0

    lease = store.acquire_lease("shared", "owner-a", 100, expected_revision=0)
    store.verify_lease("shared", "owner-a", lease.fencing_token)
    renewed = store.renew_lease(
        "shared",
        "owner-a",
        lease.fencing_token,
        250,
    )
    store.release_lease("shared", "owner-a", renewed.fencing_token)

    assert load_session_snapshot(store, "shared") == SessionSnapshot(
        state={},
        revision=0,
    )


def test_acquire_expected_revision_is_checked_without_consuming_a_token(
    store_factory: StoreFactory,
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    store = store_factory(clock, tmp_path)
    store.save("shared", {"revision": 1})

    with pytest.raises(SessionRevisionConflictError):
        store.acquire_lease("shared", "owner-a", 100, expected_revision=0)

    lease = store.acquire_lease("shared", "owner-a", 100, expected_revision=1)
    assert lease.fencing_token == 1
    assert store.load_with_revision("shared").revision == 1


def test_active_lease_rejects_every_unfenced_write(
    store_factory: StoreFactory,
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    store = store_factory(clock, tmp_path)
    store.acquire_lease("shared", "owner-a", 100, expected_revision=0)

    with pytest.raises(ActiveExecutionLeaseError):
        store.save("shared", {"unsafe": "unconditional"})
    with pytest.raises(ActiveExecutionLeaseError):
        store.save_if_revision("shared", {"unsafe": "cas"}, 0)
    with pytest.raises(ActiveExecutionLeaseError):
        save_session_snapshot(
            store,
            "shared",
            {"unsafe": "helper"},
            expected_revision=0,
        )

    assert store.load_with_revision("shared") == SessionSnapshot(state={}, revision=0)


def test_save_session_snapshot_uses_atomic_fenced_cas(
    store_factory: StoreFactory,
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    store = store_factory(clock, tmp_path)
    lease = store.acquire_lease("shared", "owner-a", 100, expected_revision=0)

    saved = save_session_snapshot(
        store,
        "shared",
        {"safe": True},
        expected_revision=0,
        execution_fence=_fence(lease),
    )

    assert saved == SessionSnapshot(state={"safe": True}, revision=1)
    assert store.load_with_revision("shared") == saved

    with pytest.raises(SessionRevisionConflictError):
        save_session_snapshot(
            store,
            "shared",
            {"stale_revision": True},
            expected_revision=0,
            execution_fence=_fence(lease),
        )
    assert store.load_with_revision("shared") == saved


@pytest.mark.parametrize(
    "missing_method",
    [
        "acquire_lease",
        "verify_lease",
        "renew_lease",
        "release_lease",
        "save_if_revision_and_fence",
    ],
)
def test_partial_execution_lease_capability_fails_closed(
    missing_method: str,
) -> None:
    clock = ManualClock()
    store = InMemorySessionStore(clock_ms=clock)
    lease = store.acquire_lease("shared", "owner-a", 100, expected_revision=0)
    setattr(store, missing_method, None)

    with pytest.raises(TypeError, match="incomplete|implemented together"):
        save_session_snapshot(
            store,
            "shared",
            {"must_not_save": True},
            expected_revision=0,
            execution_fence=_fence(lease),
        )
    assert store.load_with_revision("shared") == SessionSnapshot(state={}, revision=0)


@pytest.mark.parametrize("missing_method", ["load_with_revision", "save_if_revision"])
def test_runtime_assembly_rejects_fenced_store_without_revision_contract(
    missing_method: str,
) -> None:
    store = InMemorySessionStore()
    execution_runtime = ExecutionRuntime(
        store,
        ExecutionLeaseConfig(heartbeat_interval_ms=0),
    )
    setattr(store, missing_method, None)
    memory_runtime = KernelMemoryRuntime.from_config(store=store)

    with pytest.raises(TypeError, match="requires revisioned load/CAS support"):
        build_runtime_loop(
            memory_runtime=memory_runtime,
            execution_runtime=execution_runtime,
        )


def test_runtime_assembly_keeps_revision_only_store_in_best_effort_mode() -> None:
    store = InMemorySessionStore()
    for method in (
        "acquire_lease",
        "verify_lease",
        "renew_lease",
        "release_lease",
        "save_if_revision_and_fence",
    ):
        setattr(store, method, None)

    loop = build_runtime_loop(
        memory_runtime=KernelMemoryRuntime.from_config(store=store),
    )

    assert loop.execution_runtime is None


def test_json_store_shares_lease_state_across_instances(tmp_path: Path) -> None:
    clock = ManualClock()
    first = JsonFileSessionStore(tmp_path, clock_ms=clock)
    second = JsonFileSessionStore(tmp_path, clock_ms=clock)

    first_lease = first.acquire_lease(
        "shared",
        "owner-a",
        100,
        expected_revision=0,
    )
    assert second.verify_lease(
        "shared",
        "owner-a",
        first_lease.fencing_token,
    ).fencing_token == 1
    with pytest.raises(ExecutionLeaseConflictError):
        second.acquire_lease("shared", "owner-b", 100, expected_revision=0)

    clock.advance(100)
    second_lease = second.acquire_lease(
        "shared",
        "owner-b",
        100,
        expected_revision=0,
    )
    assert second_lease.fencing_token == 2
    with pytest.raises(StaleExecutionLeaseError):
        first.verify_lease("shared", "owner-a", first_lease.fencing_token)


def test_json_store_file_lock_allows_exactly_one_process_lease_winner(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    ready = context.Queue()
    results = context.Queue()
    processes = [
        context.Process(
            target=_json_process_acquire_worker,
            args=(str(tmp_path), owner_id, start, ready, results),
        )
        for owner_id in ("owner-a", "owner-b")
    ]
    for process in processes:
        process.start()
    try:
        assert {ready.get(timeout=15), ready.get(timeout=15)} == {
            "owner-a",
            "owner-b",
        }
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
    assert sorted(outcome[0] for outcome in outcomes) == ["acquired", "conflict"]
    assert {outcome[2] for outcome in outcomes if outcome[0] == "acquired"} == {1}

    winner = next(outcome[1] for outcome in outcomes if outcome[0] == "acquired")
    assert JsonFileSessionStore(tmp_path).verify_lease(
        "process-shared",
        winner,
        1,
    ).owner_id == winner
