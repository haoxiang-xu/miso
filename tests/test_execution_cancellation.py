from __future__ import annotations

from pathlib import Path
import threading

import pytest

from unchain.agent import (
    Agent,
    AgentCallContext,
    AgentSpec,
    AgentState,
    MemoryModule,
    PreparedAgent,
    SubagentModule,
)
from unchain.execution import (
    ExecutionCancellation,
    ExecutionCancelledError,
    ExecutionLeaseConfig,
    ExecutionLeaseError,
    ExecutionLeaseNotOwnedError,
    ExecutionRuntime,
    _borrow_execution_guard,
)
from unchain.interaction.durable import (
    INTERACTION_JOURNAL_KEY,
    INTERACTION_KIND_TOOL_APPROVAL,
    InteractionNotPendingError,
    build_interaction_request,
)
from unchain.interaction.runtime import (
    DurableInteractionRuntime,
    response_contract_for_kind,
)
from unchain.kernel import KernelRunResult, ModelTurnResult, RunState, ToolCall
from unchain.memory import (
    InMemorySessionStore,
    JsonFileSessionStore,
    KernelMemoryRuntime,
    MemoryManager,
)
from unchain.memory.checkpoint_state import (
    EXECUTION_CHECKPOINT_KEY,
    ExecutionCheckpointCompatibilityError,
    build_execution_checkpoint,
)
from unchain.subagents import SubagentExecutor, SubagentPolicy, SubagentTemplate
from unchain.subagents.plugin import SubagentToolPlugin, _ChildRunError
from unchain.tools import Toolkit


class _ManualClock:
    def __init__(self, now_ms: int = 1_000) -> None:
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms


def _runtime(store):
    return ExecutionRuntime(
        store,
        ExecutionLeaseConfig(ttl_ms=60_000, heartbeat_interval_ms=0),
    )


def _persist_child_tool_wait(
    memory: KernelMemoryRuntime,
    child_guard,
    *,
    session_id: str,
    source_run_id: str,
    occurrence: str,
    expected_revision: int,
):
    state = RunState()
    state.seed_messages([{"role": "user", "content": "perform the child task"}])
    state.session_state.session_id = session_id
    state.provider_state.provider = "ollama"
    state.provider_state.model = "fake"
    state.memory_state["session_revision"] = expected_revision
    state.iteration = 1
    state.last_continuation = {
        "type": "durable_interaction",
        "occurrence": occurrence,
    }
    request = build_interaction_request(
        session_id=session_id,
        kind=INTERACTION_KIND_TOOL_APPROVAL,
        source_run_id=source_run_id,
        occurrence=occurrence,
        payload={"tool_name": "write_file", "call_id": occurrence},
        response_contract=response_contract_for_kind(
            INTERACTION_KIND_TOOL_APPROVAL
        ),
        created_revision=expected_revision,
        subject={"provider": "ollama", "model": "fake"},
    )
    state.suspend_state.payload = {"interaction_request": request.to_dict()}
    checkpoint = build_execution_checkpoint(
        state,
        status="awaiting_interaction",
        run_id=source_run_id,
    )
    _, snapshot = memory.save_execution_checkpoint_snapshot(
        session_id,
        checkpoint,
        interaction_request=request.to_dict(),
        expected_revision=expected_revision,
        execution_fence=child_guard.fence,
    )
    return request, checkpoint, snapshot


def test_cancel_active_owner_revokes_only_that_fence_and_blocks_commit() -> None:
    clock = _ManualClock()
    store = InMemorySessionStore(clock_ms=clock)
    runtime = _runtime(store)
    stale = runtime.acquire("session-1", owner_id="attempt-a")
    snapshot = store.load_with_revision("session-1")

    cancellation = runtime.request_cancel(
        "session-1",
        "attempt-a",
        reason="user_stop",
    )

    assert cancellation.fencing_token == stale.fence.fencing_token
    assert cancellation.reason == "user_stop"
    with pytest.raises(ExecutionCancelledError):
        stale.assert_active()
    with pytest.raises(ExecutionCancelledError):
        store.save_if_revision_and_fence(
            "session-1",
            {"messages": [{"role": "assistant", "content": "late"}]},
            int(snapshot.revision or 0),
            execution_id="session-1",
            owner_id="attempt-a",
            fencing_token=stale.fence.fencing_token,
        )

    winner = runtime.acquire("session-1", owner_id="attempt-b")
    assert winner.fence.fencing_token > stale.fence.fencing_token
    winner.assert_active()
    winner.release()


def test_cancel_before_acquire_is_durable_and_idempotent() -> None:
    clock = _ManualClock()
    store = InMemorySessionStore(clock_ms=clock)
    runtime = _runtime(store)

    first = runtime.request_cancel(
        "session-before-start",
        "attempt-before-start",
        reason="first reason wins",
    )
    clock.now_ms += 50
    second = runtime.request_cancel(
        "session-before-start",
        "attempt-before-start",
        reason="late replacement",
    )

    assert second == first
    assert second.reason == "first reason wins"
    with pytest.raises(ExecutionCancelledError):
        runtime.acquire(
            "session-before-start",
            owner_id="attempt-before-start",
        )
    other = runtime.acquire("session-before-start", owner_id="attempt-new")
    other.release()


def test_late_old_cancel_does_not_revoke_current_owner() -> None:
    store = InMemorySessionStore()
    runtime = _runtime(store)
    old = runtime.acquire("shared-session", owner_id="attempt-old")
    old.release()
    current = runtime.acquire("shared-session", owner_id="attempt-current")

    runtime.request_cancel("shared-session", "attempt-old", reason="late stop")

    current.assert_active()
    current.release()


def test_load_cancellation_rejects_tombstone_for_different_owner() -> None:
    class _WrongOwnerStore(InMemorySessionStore):
        def load_execution_cancellation(self, execution_id, owner_id):
            return ExecutionCancellation(
                execution_id=execution_id,
                owner_id=f"{owner_id}-wrong",
                fencing_token=None,
                requested_at_ms=1,
                reason="wrong owner",
            )

    runtime = _runtime(_WrongOwnerStore())

    with pytest.raises(ExecutionLeaseError, match="different owner"):
        runtime.load_cancellation("session", "attempt")


def test_json_store_persists_cancel_across_instances(tmp_path: Path) -> None:
    clock = _ManualClock()
    first_store = JsonFileSessionStore(tmp_path, clock_ms=clock)
    first_runtime = _runtime(first_store)
    stale = first_runtime.acquire("json-session", owner_id="json-attempt")
    first_runtime.request_cancel(
        "json-session",
        "json-attempt",
        reason="persisted stop",
    )

    restarted_store = JsonFileSessionStore(tmp_path, clock_ms=clock)
    restarted_runtime = _runtime(restarted_store)
    loaded = restarted_runtime.load_cancellation(
        "json-session",
        "json-attempt",
    )

    assert loaded is not None
    assert loaded.reason == "persisted stop"
    assert loaded.fencing_token == stale.fence.fencing_token
    with pytest.raises(ExecutionCancelledError):
        restarted_runtime.acquire("json-session", owner_id="json-attempt")
    successor = restarted_runtime.acquire(
        "json-session",
        owner_id="json-successor",
    )
    successor.release()


def test_prepared_agent_uses_explicit_execution_owner_identity() -> None:
    store = InMemorySessionStore()
    runtime = _runtime(store)

    class _Loop:
        execution_runtime = runtime

    prepared = PreparedAgent(
        loop=_Loop(),  # type: ignore[arg-type]
        toolkit=Toolkit(),
        spec=AgentSpec(name="owner-test", provider="ollama", model="fake"),
        state=AgentState(),
        call_context=AgentCallContext(
            mode="run",
            session_id="owner-session",
            run_id="telemetry-run-id",
            execution_owner_id="attempt-exact",
        ),
    )

    with prepared._execution_scope() as guard:
        assert guard is not None
        assert guard.lease.owner_id == "attempt-exact"


@pytest.mark.parametrize("store_kind", ["memory", "json"])
def test_root_cancellation_fences_child_session_commit(
    tmp_path: Path,
    store_kind: str,
) -> None:
    store = (
        InMemorySessionStore()
        if store_kind == "memory"
        else JsonFileSessionStore(tmp_path / "sessions")
    )
    runtime = _runtime(store)
    root = runtime.acquire("root-session", owner_id="root-attempt")
    root_fence = root.fence
    child_snapshot = store.load_with_revision("root-session:child-1")

    next_revision = store.save_if_revision_and_fence(
        "root-session:child-1",
        {"messages": [{"role": "assistant", "content": "before stop"}]},
        int(child_snapshot.revision or 0),
        execution_id=root_fence.execution_id,
        owner_id=root_fence.owner_id,
        fencing_token=root_fence.fencing_token,
    )
    assert next_revision == 1

    runtime.request_cancel("root-session", "root-attempt", reason="user_stop")
    with pytest.raises(ExecutionCancelledError):
        store.save_if_revision_and_fence(
            "root-session:child-1",
            {"messages": [{"role": "assistant", "content": "after stop"}]},
            next_revision,
            execution_id=root_fence.execution_id,
            owner_id=root_fence.owner_id,
            fencing_token=root_fence.fencing_token,
        )

    assert store.load("root-session:child-1")["messages"][-1]["content"] == "before stop"


def test_borrowed_child_wait_does_not_release_root_or_sibling_guard() -> None:
    runtime = _runtime(InMemorySessionStore())
    root = runtime.acquire("root-session", owner_id="root-attempt")
    first = _borrow_execution_guard(root, session_id="root-session:child-1")
    sibling = _borrow_execution_guard(root, session_id="root-session:child-2")

    assert first.lease.execution_id == "root-session:child-1"
    assert first.fence.execution_id == "root-session"
    first.release_for_wait()
    sibling.assert_active()
    root.assert_active()
    first.reacquire(expected_revision=0)
    first.assert_active()
    root.release()


def test_root_cancel_terminalizes_descendant_durable_wait_before_late_receipt() -> None:
    store = InMemorySessionStore()
    runtime = _runtime(store)
    root = runtime.acquire("root-session", owner_id="root-attempt")
    child_session_id = "root-session:child-1"
    child = _borrow_execution_guard(root, session_id=child_session_id)
    memory = KernelMemoryRuntime.from_config(store=store)

    state = RunState()
    state.seed_messages([{"role": "user", "content": "perform the child task"}])
    state.session_state.session_id = child_session_id
    state.provider_state.provider = "ollama"
    state.provider_state.model = "fake"
    state.memory_state["session_revision"] = 0
    state.iteration = 1
    state.last_continuation = {
        "type": "durable_interaction",
        "occurrence": "child-approval",
    }
    request = build_interaction_request(
        session_id=child_session_id,
        kind=INTERACTION_KIND_TOOL_APPROVAL,
        source_run_id="child-run",
        occurrence="child-approval",
        payload={"tool_name": "write_file", "call_id": "child-approval"},
        response_contract=response_contract_for_kind(
            INTERACTION_KIND_TOOL_APPROVAL
        ),
        created_revision=0,
        subject={"provider": "ollama", "model": "fake"},
    )
    state.suspend_state.payload = {"interaction_request": request.to_dict()}
    checkpoint = build_execution_checkpoint(
        state,
        status="awaiting_interaction",
        run_id="child-run",
    )
    _, pending_snapshot = memory.save_execution_checkpoint_snapshot(
        child_session_id,
        checkpoint,
        interaction_request=request.to_dict(),
        expected_revision=0,
        execution_fence=child.fence,
    )

    child.release_for_wait()
    runtime.request_cancel("root-session", "root-attempt", reason="user_stop")

    interaction_runtime = DurableInteractionRuntime(memory)
    late_receipt_persisted = False
    try:
        interaction_runtime.record_receipt(
            child_session_id,
            interaction_id=request.interaction_id,
            response={"approved": False},
            submitted_by="callback:late_child_approval",
            expected_revision=pending_snapshot.revision,
        )
    except ExecutionCancelledError:
        pass
    else:
        late_receipt_persisted = True

    with pytest.raises(ExecutionCancelledError):
        child.reacquire(
            expected_revision=memory.load_session_snapshot(
                child_session_id
            ).revision
        )

    persisted = memory.load_session_snapshot(child_session_id).state
    journal = persisted.get(INTERACTION_JOURNAL_KEY, {})
    entry = (
        journal.get("entries", {}).get(request.interaction_id, {})
        if isinstance(journal, dict)
        else {}
    )
    failures: list[str] = []
    if late_receipt_persisted:
        failures.append(
            "late descendant interaction receipt was accepted after root cancellation"
        )
    if EXECUTION_CHECKPOINT_KEY in persisted:
        failures.append("descendant execution checkpoint survived root cancellation")
    if isinstance(journal, dict) and journal.get("active_id") == request.interaction_id:
        failures.append("descendant interaction remained active after root cancellation")
    receipt = entry.get("receipt") if isinstance(entry, dict) else None
    if (
        not isinstance(receipt, dict)
        or receipt.get("submitted_by") != "runtime:cancelled_execution_tree"
        or receipt.get("response")
        != {"cancelled": True, "reason": "user_stop"}
    ):
        failures.append(
            "descendant journal did not retain the canonical cancellation receipt"
        )
    application = entry.get("application") if isinstance(entry, dict) else None
    if (
        not isinstance(application, dict)
        or application.get("applied_checkpoint_id")
        != f"cancelled:{checkpoint.get('checkpoint_id')}"
    ):
        failures.append("descendant cancellation was not durably applied")

    assert not failures, "; ".join(failures)


def test_cancelled_descendant_checkpoint_cannot_be_loaded_or_resumed() -> None:
    store = InMemorySessionStore()
    runtime = _runtime(store)
    root = runtime.acquire("root-load", owner_id="attempt-a")
    child_session_id = "root-load:child"
    child = _borrow_execution_guard(root, session_id=child_session_id)
    memory = KernelMemoryRuntime.from_config(store=store)
    request, checkpoint, _ = _persist_child_tool_wait(
        memory,
        child,
        session_id=child_session_id,
        source_run_id="child-run-a",
        occurrence="approval-a",
        expected_revision=0,
    )
    child.release_for_wait()
    runtime.request_cancel("root-load", "attempt-a", reason="user_stop")

    interactions = DurableInteractionRuntime(memory)
    with pytest.raises(ExecutionCancelledError):
        interactions.load_active(child_session_id)

    assert memory.load_execution_checkpoint(child_session_id) is None
    with pytest.raises(InteractionNotPendingError):
        interactions.load_active(child_session_id)
    audit = interactions.load(
        child_session_id,
        interaction_id=request.interaction_id,
        require_active=False,
    )
    assert audit.receipt is not None
    assert audit.receipt.response == {
        "cancelled": True,
        "reason": "user_stop",
    }
    assert audit.application == {
        "schema_version": 1,
        "receipt_id": audit.receipt.receipt_id,
        "applied_checkpoint_id": f"cancelled:{checkpoint['checkpoint_id']}",
    }


def test_bootstrap_reconciles_cancelled_descendant_before_restore() -> None:
    store = InMemorySessionStore()
    runtime = _runtime(store)
    root = runtime.acquire("root-bootstrap", owner_id="attempt-a")
    child_session_id = "root-bootstrap:child"
    child = _borrow_execution_guard(root, session_id=child_session_id)
    memory = KernelMemoryRuntime.from_config(store=store)
    _persist_child_tool_wait(
        memory,
        child,
        session_id=child_session_id,
        source_run_id="child-run-a",
        occurrence="approval-a",
        expected_revision=0,
    )
    child.release_for_wait()
    runtime.request_cancel("root-bootstrap", "attempt-a", reason="user_stop")

    _, loaded_state, prepare_info, _ = memory.bootstrap_session(
        session_id=child_session_id,
        memory_namespace=None,
        incoming_messages=[{"role": "user", "content": "new attempt"}],
        resume_mode=False,
        provider="ollama",
        model="fake",
    )

    assert EXECUTION_CHECKPOINT_KEY not in loaded_state
    assert prepare_info["execution_checkpoint_restored"] is False
    assert loaded_state[INTERACTION_JOURNAL_KEY]["active_id"] is None


def test_detached_child_receipt_allows_same_attempt_after_root_reacquire() -> None:
    store = InMemorySessionStore()
    runtime = _runtime(store)
    root = runtime.acquire("root-reacquire", owner_id="attempt-a")
    first_token = root.fence.fencing_token
    child_session_id = "root-reacquire:child"
    child = _borrow_execution_guard(root, session_id=child_session_id)
    memory = KernelMemoryRuntime.from_config(store=store)
    request, _, pending_snapshot = _persist_child_tool_wait(
        memory,
        child,
        session_id=child_session_id,
        source_run_id="child-run-a",
        occurrence="approval-a",
        expected_revision=0,
    )
    child.release_for_wait()
    root.release()
    reacquired = runtime.acquire("root-reacquire", owner_id="attempt-a")
    assert reacquired.fence.fencing_token > first_token

    recorded = DurableInteractionRuntime(memory).record_receipt(
        child_session_id,
        interaction_id=request.interaction_id,
        response={"approved": True},
        submitted_by="callback:after_reacquire",
        expected_revision=pending_snapshot.revision,
    )

    assert recorded.receipt is not None
    assert recorded.receipt.submitted_by == "callback:after_reacquire"
    reacquired.assert_active()
    reacquired.release()


def test_root_detached_receipt_cannot_cross_cancel_atomic_boundary() -> None:
    class _BlockingRootReceiptStore(InMemorySessionStore):
        def __init__(self) -> None:
            super().__init__()
            self.receipt_cas_started = threading.Event()
            self.allow_receipt_cas = threading.Event()

        def save_if_revision_and_execution_not_cancelled(
            self,
            session_id,
            state,
            expected_revision,
            *,
            execution_id,
            owner_id,
        ):
            self.receipt_cas_started.set()
            if not self.allow_receipt_cas.wait(2):
                raise AssertionError("test did not release receipt CAS")
            return super().save_if_revision_and_execution_not_cancelled(
                session_id,
                state,
                expected_revision,
                execution_id=execution_id,
                owner_id=owner_id,
            )

    store = _BlockingRootReceiptStore()
    runtime = _runtime(store)
    session_id = "root-detached-race"
    root = runtime.acquire(session_id, owner_id="attempt-a")
    memory = KernelMemoryRuntime.from_config(store=store)
    request, _, pending_snapshot = _persist_child_tool_wait(
        memory,
        root,
        session_id=session_id,
        source_run_id="root-run-a",
        occurrence="approval-a",
        expected_revision=0,
    )
    root.release_for_wait()
    outcome: dict[str, object] = {}

    def _record() -> None:
        try:
            outcome["result"] = DurableInteractionRuntime(memory).record_receipt(
                session_id,
                interaction_id=request.interaction_id,
                response={"approved": True},
                submitted_by="callback:late_root",
                expected_revision=pending_snapshot.revision,
            )
        except BaseException as exc:  # noqa: BLE001 - asserted below
            outcome["error"] = exc

    receipt_thread = threading.Thread(target=_record)
    receipt_thread.start()
    assert store.receipt_cas_started.wait(2)
    runtime.request_cancel(session_id, "attempt-a", reason="user_stop")
    store.allow_receipt_cas.set()
    receipt_thread.join(2)

    assert not receipt_thread.is_alive()
    assert isinstance(outcome.get("error"), ExecutionCancelledError)
    assert "result" not in outcome
    assert memory.load_execution_checkpoint(session_id) is None
    audit = DurableInteractionRuntime(memory).load(
        session_id,
        interaction_id=request.interaction_id,
        require_active=False,
    )
    assert audit.receipt is not None
    assert audit.receipt.submitted_by == "runtime:cancelled_execution_tree"


def test_checkpoint_retry_after_same_owner_reacquire_preserves_audit_binding() -> None:
    store = InMemorySessionStore()
    runtime = _runtime(store)
    session_id = "root-checkpoint-retry"
    root = runtime.acquire(session_id, owner_id="attempt-a")
    first_token = root.fence.fencing_token
    memory = KernelMemoryRuntime.from_config(store=store)
    request, checkpoint, first_snapshot = _persist_child_tool_wait(
        memory,
        root,
        session_id=session_id,
        source_run_id="root-run-a",
        occurrence="approval-a",
        expected_revision=0,
    )
    stored_before = store.load(session_id)
    root.release()
    reacquired = runtime.acquire(session_id, owner_id="attempt-a")
    assert reacquired.fence.fencing_token > first_token

    persisted, retry_snapshot = memory.save_execution_checkpoint_snapshot(
        session_id,
        checkpoint,
        interaction_request=request.to_dict(),
        expected_revision=0,
        execution_fence=reacquired.fence,
    )

    assert persisted["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert retry_snapshot.revision == first_snapshot.revision
    assert store.load(session_id) == stored_before
    reacquired.release()


def test_cancel_and_descendant_receipt_race_always_terminalizes_wait() -> None:
    for round_index in range(24):
        store = InMemorySessionStore()
        runtime = _runtime(store)
        root_session_id = f"root-race-{round_index}"
        owner_id = f"attempt-{round_index}"
        root = runtime.acquire(root_session_id, owner_id=owner_id)
        child_session_id = f"{root_session_id}:child"
        child = _borrow_execution_guard(root, session_id=child_session_id)
        memory = KernelMemoryRuntime.from_config(store=store)
        request, checkpoint, pending_snapshot = _persist_child_tool_wait(
            memory,
            child,
            session_id=child_session_id,
            source_run_id=f"child-run-{round_index}",
            occurrence=f"approval-{round_index}",
            expected_revision=0,
        )
        child.release_for_wait()
        interactions = DurableInteractionRuntime(memory)
        barrier = threading.Barrier(2)
        outcomes: list[object] = []
        outcomes_lock = threading.Lock()

        def _cancel() -> None:
            barrier.wait()
            outcome: object
            try:
                outcome = runtime.request_cancel(
                    root_session_id,
                    owner_id,
                    reason="race_stop",
                )
            except BaseException as exc:  # noqa: BLE001 - asserted below
                outcome = exc
            with outcomes_lock:
                outcomes.append(outcome)

        def _record() -> None:
            barrier.wait()
            outcome: object
            try:
                outcome = interactions.record_receipt(
                    child_session_id,
                    interaction_id=request.interaction_id,
                    response={"approved": False},
                    submitted_by="callback:race",
                    expected_revision=pending_snapshot.revision,
                )
            except BaseException as exc:  # noqa: BLE001 - either order is valid
                outcome = exc
            with outcomes_lock:
                outcomes.append(outcome)

        cancel_thread = threading.Thread(target=_cancel)
        receipt_thread = threading.Thread(target=_record)
        cancel_thread.start()
        receipt_thread.start()
        cancel_thread.join(2)
        receipt_thread.join(2)

        assert not cancel_thread.is_alive()
        assert not receipt_thread.is_alive()
        assert len(outcomes) == 2
        assert memory.load_execution_checkpoint(child_session_id) is None
        persisted = memory.load_session_snapshot(child_session_id).state
        journal = persisted[INTERACTION_JOURNAL_KEY]
        entry = journal["entries"][request.interaction_id]
        assert journal["active_id"] is None
        assert entry["receipt"]["submitted_by"] in {
            "callback:race",
            "runtime:cancelled_execution_tree",
        }
        assert entry["application"]["applied_checkpoint_id"] == (
            f"cancelled:{checkpoint['checkpoint_id']}"
        )


def test_json_restart_and_successor_attempt_isolate_descendant_cancellation(
    tmp_path: Path,
) -> None:
    root_session_id = "restart-root"
    child_session_id = f"{root_session_id}:child"
    first_store = JsonFileSessionStore(tmp_path / "sessions")
    first_runtime = _runtime(first_store)
    first_root = first_runtime.acquire(root_session_id, owner_id="attempt-a")
    first_child = _borrow_execution_guard(
        first_root,
        session_id=child_session_id,
    )
    first_memory = KernelMemoryRuntime.from_config(store=first_store)
    request_a, _, _ = _persist_child_tool_wait(
        first_memory,
        first_child,
        session_id=child_session_id,
        source_run_id="child-run-a",
        occurrence="approval-a",
        expected_revision=0,
    )
    first_child.release_for_wait()
    first_runtime.request_cancel(
        root_session_id,
        "attempt-a",
        reason="restart_stop",
    )

    restarted_store = JsonFileSessionStore(tmp_path / "sessions")
    restarted_runtime = _runtime(restarted_store)
    restarted_memory = KernelMemoryRuntime.from_config(store=restarted_store)
    restarted_interactions = DurableInteractionRuntime(restarted_memory)
    with pytest.raises(ExecutionCancelledError):
        restarted_interactions.load_active(child_session_id)
    assert restarted_memory.load_execution_checkpoint(child_session_id) is None

    successor_root = restarted_runtime.acquire(
        root_session_id,
        owner_id="attempt-b",
    )
    successor_child = _borrow_execution_guard(
        successor_root,
        session_id=child_session_id,
    )
    revision = restarted_memory.load_session_snapshot(child_session_id).revision
    assert revision is not None
    request_b, checkpoint_b, snapshot_b = _persist_child_tool_wait(
        restarted_memory,
        successor_child,
        session_id=child_session_id,
        source_run_id="child-run-b",
        occurrence="approval-b",
        expected_revision=revision,
    )

    restarted_runtime.request_cancel(
        root_session_id,
        "attempt-a",
        reason="late_duplicate_stop",
    )
    with pytest.raises(ExecutionCheckpointCompatibilityError):
        restarted_interactions.record_receipt(
            child_session_id,
            interaction_id=request_a.interaction_id,
            response={"approved": False},
            submitted_by="callback:late_attempt_a",
        )

    current = restarted_memory.load_execution_checkpoint(child_session_id)
    assert current is not None
    assert current["checkpoint_id"] == checkpoint_b["checkpoint_id"]
    recorded_b = restarted_interactions.record_receipt(
        child_session_id,
        interaction_id=request_b.interaction_id,
        response={"approved": True},
        submitted_by="callback:attempt_b",
        expected_revision=snapshot_b.revision,
    )
    assert recorded_b.receipt is not None
    assert recorded_b.receipt.submitted_by == "callback:attempt_b"
    successor_root.assert_active()
    successor_root.release()


def test_json_restart_reconciles_root_tombstone_without_host_cleanup(
    tmp_path: Path,
) -> None:
    session_id = "root-crash-window"
    first_store = JsonFileSessionStore(tmp_path / "root-sessions")
    first_runtime = _runtime(first_store)
    root = first_runtime.acquire(session_id, owner_id="attempt-a")
    first_memory = KernelMemoryRuntime.from_config(store=first_store)
    request, checkpoint, _ = _persist_child_tool_wait(
        first_memory,
        root,
        session_id=session_id,
        source_run_id="root-run-a",
        occurrence="approval-a",
        expected_revision=0,
    )
    root.release_for_wait()
    first_runtime.request_cancel(session_id, "attempt-a", reason="user_stop")

    restarted_store = JsonFileSessionStore(tmp_path / "root-sessions")
    restarted_memory = KernelMemoryRuntime.from_config(store=restarted_store)
    assert restarted_memory.load_execution_checkpoint(session_id) is None
    audit = DurableInteractionRuntime(restarted_memory).load(
        session_id,
        interaction_id=request.interaction_id,
        require_active=False,
    )
    assert audit.receipt is not None
    assert audit.receipt.response == {
        "cancelled": True,
        "reason": "user_stop",
    }
    assert audit.application is not None
    assert audit.application["applied_checkpoint_id"] == (
        f"cancelled:{checkpoint['checkpoint_id']}"
    )


def test_borrowed_guard_rejects_session_outside_root_domain() -> None:
    runtime = _runtime(InMemorySessionStore())
    root = runtime.acquire("root-session", owner_id="root-attempt")

    with pytest.raises(ValueError, match="root session or a descendant"):
        _borrow_execution_guard(root, session_id="other-session:child")

    root.assert_active()
    root.release()


def test_guarded_subagent_keeps_legacy_run_signature_compatible() -> None:
    runtime = _runtime(InMemorySessionStore())
    root = runtime.acquire("root-session", owner_id="root-attempt")
    parent = Agent(name="parent", provider="openai")
    plugin = SubagentToolPlugin(
        parent_agent=parent,
        templates=(),
        policy=SubagentPolicy(),
        executor=SubagentExecutor(),
    )

    class _LegacyChild:
        name = "legacy-child"

        def run(
            self,
            input_messages,
            *,
            session_id,
            memory_namespace,
            max_iterations,
            callback,
            on_tool_confirm,
            on_human_input,
            on_max_iterations,
            run_id,
        ):
            del (
                input_messages,
                session_id,
                memory_namespace,
                max_iterations,
                on_tool_confirm,
                on_human_input,
                on_max_iterations,
                run_id,
            )
            callback({"type": "custom_child_progress"})
            return KernelRunResult(
                messages=[{"role": "assistant", "content": "legacy result"}],
                status="completed",
            )

    result = plugin._run_child(
        agent=_LegacyChild(),  # type: ignore[arg-type]
        mode="delegate",
        child_id="legacy-child",
        lineage=["parent", "legacy-child"],
        template_name="legacy",
        session_id="root-session:legacy-child",
        memory_namespace="",
        input_messages="work",
        max_iterations=1,
        callback=lambda event: None,
        execution_guard=root,
    )

    assert result.status == "completed"
    assert result.output == "legacy result"
    root.assert_active()
    root.release()


def test_waiting_child_rechecks_root_cancellation_before_returning_result() -> None:
    store = InMemorySessionStore()
    runtime = _runtime(store)
    root = runtime.acquire("root-session", owner_id="root-attempt")
    parent = Agent(name="parent", provider="openai")
    plugin = SubagentToolPlugin(
        parent_agent=parent,
        templates=(),
        policy=SubagentPolicy(),
        executor=SubagentExecutor(),
    )

    class _WaitingChild:
        name = "waiting-child"

        def run(self, input_messages, **kwargs):
            del input_messages
            child_guard = kwargs["_execution_guard"]
            child_guard.release_for_wait()
            runtime.request_cancel(
                "root-session",
                "root-attempt",
                reason="user_stop",
            )
            return KernelRunResult(messages=[], status="awaiting_human_input")

    with pytest.raises(_ChildRunError) as error:
        plugin._run_child(
            agent=_WaitingChild(),  # type: ignore[arg-type]
            mode="delegate",
            child_id="waiting-child",
            lineage=["parent", "waiting-child"],
            template_name="waiting",
            session_id="root-session:waiting-child",
            memory_namespace="",
            input_messages="work",
            max_iterations=1,
            execution_guard=root,
        )

    assert isinstance(error.value.original, ExecutionCancelledError)


def test_non_waiting_child_must_still_own_its_borrowed_guard() -> None:
    runtime = _runtime(InMemorySessionStore())
    root = runtime.acquire("root-session", owner_id="root-attempt")
    parent = Agent(name="parent", provider="openai")
    plugin = SubagentToolPlugin(
        parent_agent=parent,
        templates=(),
        policy=SubagentPolicy(),
        executor=SubagentExecutor(),
    )

    class _InvalidReleasedChild:
        name = "invalid-released-child"

        def run(self, input_messages, **kwargs):
            del input_messages
            kwargs["_execution_guard"].release_for_wait()
            return KernelRunResult(messages=[], status="max_iterations")

    with pytest.raises(_ChildRunError) as error:
        plugin._run_child(
            agent=_InvalidReleasedChild(),  # type: ignore[arg-type]
            mode="delegate",
            child_id="invalid-released-child",
            lineage=["parent", "invalid-released-child"],
            template_name="invalid",
            session_id="root-session:invalid-released-child",
            memory_namespace="",
            input_messages="work",
            max_iterations=1,
            execution_guard=root,
        )

    assert isinstance(error.value.original, ExecutionLeaseNotOwnedError)
    root.assert_active()
    root.release()


def test_cancel_root_while_child_model_is_running_blocks_child_and_parent_commits() -> None:
    store = InMemorySessionStore()
    memory = MemoryManager(store=store)
    child_started = threading.Event()
    release_child = threading.Event()

    class _BlockingChildModelIO:
        provider = "openai"
        model = "fake-child"

        def fetch_turn(self, request):
            del request
            child_started.set()
            if not release_child.wait(2):
                raise AssertionError("test did not release child model")
            return ModelTurnResult(
                assistant_messages=[
                    {"role": "assistant", "content": "late child answer"}
                ],
                tool_calls=[],
                final_text="late child answer",
            )

    child = Agent(
        name="specialist",
        provider="openai",
        modules=(MemoryModule(memory=memory),),
        model_io_factory=lambda spec, ctx: _BlockingChildModelIO(),
    )

    class _ParentModelIO:
        provider = "openai"
        model = "fake-parent"

        def fetch_turn(self, request):
            del request
            return ModelTurnResult(
                assistant_messages=[
                    {
                        "role": "assistant",
                        "type": "function_call",
                        "call_id": "handoff-1",
                        "name": "handoff_to_subagent",
                        "arguments": '{"target":"specialist","reason":"delegate"}',
                    }
                ],
                tool_calls=[
                    ToolCall(
                        call_id="handoff-1",
                        name="handoff_to_subagent",
                        arguments={"target": "specialist", "reason": "delegate"},
                    )
                ],
                final_text="",
            )

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            MemoryModule(memory=memory),
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="specialist",
                        description="blocking specialist",
                        agent=child,
                        allowed_modes=("handoff",),
                        memory_policy="scoped_persistent",
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: _ParentModelIO(),
    )
    outcome: dict[str, object] = {}

    def _run() -> None:
        try:
            outcome["result"] = parent.run(
                "start",
                session_id="root-session",
                run_id="root-run",
                execution_owner_id="root-attempt",
                max_iterations=1,
            )
        except BaseException as exc:  # noqa: BLE001 - thread outcome assertion
            outcome["error"] = exc

    thread = threading.Thread(target=_run)
    thread.start()
    assert child_started.wait(2)
    _runtime(store).request_cancel(
        "root-session",
        "root-attempt",
        reason="user_stop",
    )
    release_child.set()
    thread.join(3)

    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), ExecutionCancelledError)
    assert "result" not in outcome
    child_state = store.load("root-session:manager.specialist.1")
    assert all(
        message.get("content") != "late child answer"
        for message in child_state.get("messages", [])
        if isinstance(message, dict)
    )
    root_state = store.load("root-session")
    assert all(
        message.get("content") != "late child answer"
        for message in root_state.get("messages", [])
        if isinstance(message, dict)
    )


def test_cancel_root_stops_parallel_worker_commits() -> None:
    store = InMemorySessionStore()
    memory = MemoryManager(store=store)
    all_workers_started = threading.Event()
    release_workers = threading.Event()
    seen_sessions: list[str] = []
    seen_lock = threading.Lock()

    class _BlockingWorkerModelIO:
        provider = "openai"
        model = "fake-worker"

        def __init__(self, session_id: str) -> None:
            self.session_id = session_id

        def fetch_turn(self, request):
            del request
            with seen_lock:
                seen_sessions.append(self.session_id)
                if len(seen_sessions) == 2:
                    all_workers_started.set()
            if not release_workers.wait(2):
                raise AssertionError("test did not release worker models")
            return ModelTurnResult(
                assistant_messages=[
                    {"role": "assistant", "content": f"late {self.session_id}"}
                ],
                tool_calls=[],
                final_text=f"late {self.session_id}",
            )

    worker = Agent(
        name="worker",
        provider="openai",
        modules=(MemoryModule(memory=memory),),
        model_io_factory=lambda spec, ctx: _BlockingWorkerModelIO(
            str(ctx.session_id or "")
        ),
    )

    class _ParentModelIO:
        provider = "openai"
        model = "fake-parent"

        def fetch_turn(self, request):
            del request
            arguments = {
                "target": "worker",
                "tasks": [{"task": "one"}, {"task": "two"}],
            }
            return ModelTurnResult(
                assistant_messages=[
                    {
                        "role": "assistant",
                        "type": "function_call",
                        "call_id": "workers-1",
                        "name": "spawn_worker_batch",
                        "arguments": (
                            '{"target":"worker","tasks":'
                            '[{"task":"one"},{"task":"two"}]}'
                        ),
                    }
                ],
                tool_calls=[
                    ToolCall(
                        call_id="workers-1",
                        name="spawn_worker_batch",
                        arguments=arguments,
                    )
                ],
                final_text="",
            )

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            MemoryModule(memory=memory),
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="worker",
                        description="blocking worker",
                        agent=worker,
                        allowed_modes=("worker",),
                        memory_policy="scoped_persistent",
                        parallel_safe=True,
                    ),
                ),
                policy=SubagentPolicy(max_parallel_workers=2),
            ),
        ),
        model_io_factory=lambda spec, ctx: _ParentModelIO(),
    )
    outcome: dict[str, object] = {}

    def _run() -> None:
        try:
            outcome["result"] = parent.run(
                "start workers",
                session_id="parallel-root",
                run_id="parallel-run",
                execution_owner_id="parallel-attempt",
                max_iterations=1,
            )
        except BaseException as exc:  # noqa: BLE001 - thread outcome assertion
            outcome["error"] = exc

    thread = threading.Thread(target=_run)
    thread.start()
    assert all_workers_started.wait(2)
    _runtime(store).request_cancel(
        "parallel-root",
        "parallel-attempt",
        reason="user_stop",
    )
    release_workers.set()
    thread.join(3)

    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), ExecutionCancelledError)
    assert len(seen_sessions) == 2
    for child_session_id in seen_sessions:
        child_state = store.load(child_session_id)
        assert all(
            not str(message.get("content") or "").startswith("late ")
            for message in child_state.get("messages", [])
            if isinstance(message, dict)
        )
