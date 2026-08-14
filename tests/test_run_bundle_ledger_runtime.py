from __future__ import annotations

from types import SimpleNamespace

import pytest
import httpx

from unchain.kernel import kernel_run_failure_from_exception
from unchain.kernel.loop import KernelLoop
from unchain.kernel.run_ledger import RunLedger
from unchain.kernel.state import RunState
from unchain.kernel.types import ModelTurnResult, ToolCall
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store
from unchain.run_bundle import (
    RunBundleReducer,
    RunDescriptor,
    RunIdentity,
    RunLifecycle,
)
from unchain.run_bundle_ledger import (
    RunBundleContinuationError,
    RunBundleLedgerConflictError,
)
from unchain.retry import RetryConfig, RetriesExhaustedError
from unchain.tools.execution import ToolExecutionHarness


def _store(tmp_path) -> SQLiteContextV2Store:
    return SQLiteContextV2Store(
        database_path=tmp_path / "context" / "context.sqlite3",
        object_directory=tmp_path / "context" / "objects",
    )


def _identity(run_id: str, *, attempt_id: str | None = None) -> RunIdentity:
    return RunIdentity(
        execution_id="execution-1",
        attempt_id=attempt_id or f"attempt-{run_id}",
        root_run_id=run_id,
        run_id=run_id,
        parent_run_id=None,
        relation="root",
    )


def _state() -> RunState:
    state = RunState()
    state.session_state.session_id = "execution-1"
    return state


def test_cold_identical_materialization_reuses_durable_revision(tmp_path) -> None:
    bound = _store(tmp_path).bind_execution("execution-1")
    identity = _identity("root-1")
    first_state = _state()
    first_state.run_ledger.initialize(
        state=first_state,
        run_id=identity.run_id,
        explicit_identity=identity,
        descriptor=RunDescriptor(model="gpt-test", display_model="openai:gpt-test"),
    )
    first_state.run_ledger.attach_persistence(bound)
    first = first_state.run_ledger.materialize(kernel_status="completed")

    cold_state = _state()
    cold_state.run_ledger.initialize(
        state=cold_state,
        run_id=identity.run_id,
        explicit_identity=identity,
        descriptor=first.descriptor,
    )
    cold_state.run_ledger.attach_persistence(bound)
    replay = cold_state.run_ledger.materialize(kernel_status="completed")
    assert replay == first
    assert replay.revision == 1


def test_sqlite_rejects_revision_regression(tmp_path) -> None:
    bound = _store(tmp_path).bind_execution("execution-1")
    identity = _identity("root-1")
    revision_one = RunBundleReducer.reduce(
        identity=identity,
        lifecycle=RunLifecycle(
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:01:00Z",
        ),
        receipts=(),
        revision=1,
    )
    revision_two = RunBundleReducer.reduce(
        identity=identity,
        lifecycle=revision_one.lifecycle,
        receipts=(),
        revision=2,
    )
    bound.persist_bundle(revision_two)
    with pytest.raises(RunBundleLedgerConflictError, match="regressed"):
        bound.persist_bundle(revision_one)


def test_continuation_requires_explicit_terminal_durable_predecessor(tmp_path) -> None:
    bound = _store(tmp_path).bind_execution("execution-1")
    predecessor_identity = _identity("old-root")
    predecessor = RunBundleReducer.reduce(
        identity=predecessor_identity,
        lifecycle=RunLifecycle(
            status="cancelled",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:01:00Z",
        ),
        receipts=(),
    )
    bound.persist_bundle(predecessor)
    successor_identity = _identity("new-root")
    assert bound.claim_continuation(successor=successor_identity) is None
    assert bound.claim_continuation(
        successor=successor_identity,
        requested_run_id="old-root",
    ) == predecessor
    assert bound.claim_continuation(successor=successor_identity) == predecessor

    other_successor = _identity("other-root")
    with pytest.raises(RunBundleContinuationError) as error:
        bound.claim_continuation(
            successor=other_successor,
            requested_run_id="old-root",
        )
    assert error.value.code == "continued_from_not_claimable"


def test_run_ledger_links_fresh_bundle_without_receipt_overlap(tmp_path) -> None:
    bound = _store(tmp_path).bind_execution("execution-1")
    predecessor = RunBundleReducer.reduce(
        identity=_identity("old-root"),
        lifecycle=RunLifecycle(
            status="suspended",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:01:00Z",
        ),
        receipts=(),
    )
    bound.persist_bundle(predecessor)
    state = _state()
    ledger = RunLedger()
    state.run_ledger = ledger
    successor = _identity("new-root")
    ledger.initialize(
        state=state,
        run_id=successor.run_id,
        explicit_identity=successor,
        continued_from_run_id="old-root",
    )
    ledger.attach_persistence(bound)
    bundle = ledger.materialize(kernel_status="completed")
    assert bundle.identity != predecessor.identity
    assert bundle.lifecycle.continued_from_run_id == "old-root"
    assert bundle.provider_calls == ()
    assert set(bundle.aggregation.all_call_ids).isdisjoint(
        predecessor.aggregation.all_call_ids
    )


def test_continued_from_without_authoritative_ledger_fails_before_send() -> None:
    sends = []

    class _ModelIO:
        def fetch_turn(self, request):
            sends.append(request)
            return ModelTurnResult(assistant_messages=[], tool_calls=[])

    with pytest.raises(RunBundleContinuationError) as caught:
        KernelLoop(model_io=_ModelIO()).run(
            messages=[{"role": "user", "content": "fresh"}],
            session_id="execution-no-ledger",
            run_id="fresh-run",
            provider="openai",
            model="gpt-test",
            _continued_from_run_id="attacker-controlled-run",
        )

    assert caught.value.code == "continuation_ledger_unavailable"
    assert sends == []


def test_kernel_retry_receipts_have_atomic_timing_and_outcomes() -> None:
    request = httpx.Request("POST", "https://example.test")
    response = httpx.Response(status_code=503, request=request)
    script = [
        httpx.HTTPStatusError("unavailable", request=request, response=response),
        ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": "ok"}],
            tool_calls=[],
            final_text="ok",
            response_id="response-1",
        ),
    ]

    class _RetryModelIO:
        def fetch_turn(self, _request):
            outcome = script.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    loop = KernelLoop(
        model_io=_RetryModelIO(),
        retry_config=RetryConfig(
            max_retries=1,
            base_delay_ms=0,
            max_delay_ms=0,
            jitter_ratio=0,
        ),
    )
    state = loop.seed_state(
        [{"role": "user", "content": "hello"}],
        provider="openai",
        model="gpt-test",
    )

    loop.step_once(state, run_id="retry-run")
    receipts = sorted(
        state.run_ledger.receipts.values(),
        key=lambda receipt: receipt.identity.retry_ordinal,
    )

    assert [receipt.status for receipt in receipts] == ["failed", "completed"]
    assert [receipt.identity.route for receipt in receipts] == [
        "openai.responses.create",
        "openai.responses.create",
    ]
    assert all(receipt.timing.started_at is not None for receipt in receipts)
    assert all(receipt.timing.completed_at is not None for receipt in receipts)


def test_kernel_retry_exhaustion_keeps_uncertain_attempts_closed() -> None:
    class _FailingModelIO:
        def fetch_turn(self, _request):
            raise httpx.ConnectError("connection outcome unknown")

    loop = KernelLoop(
        model_io=_FailingModelIO(),
        retry_config=RetryConfig(
            max_retries=1,
            base_delay_ms=0,
            max_delay_ms=0,
            jitter_ratio=0,
        ),
    )
    state = loop.seed_state(
        [{"role": "user", "content": "hello"}],
        provider="openai",
        model="gpt-test",
    )

    with pytest.raises(RetriesExhaustedError):
        loop.step_once(state, run_id="failed-retry-run")

    receipts = tuple(state.run_ledger.receipts.values())
    assert len(receipts) == 2
    assert {receipt.status for receipt in receipts} == {"uncertain"}
    assert all(receipt.timing.completed_at is not None for receipt in receipts)


def test_failed_run_attaches_content_free_canonical_bundle() -> None:
    class _FailingModelIO:
        def fetch_turn(self, _request):
            raise ValueError("secret provider exception text")

    loop = KernelLoop(
        model_io=_FailingModelIO(),
        retry_config=RetryConfig(max_retries=0),
    )

    with pytest.raises(Exception) as caught:
        loop.run(
            messages=[{"role": "user", "content": "private prompt"}],
            session_id="failure-execution",
            run_id="failure-run",
            provider="openai",
            model="gpt-test",
            max_iterations=1,
        )

    failure = kernel_run_failure_from_exception(caught.value)
    assert failure is not None
    assert failure.run_bundle.lifecycle.status == "failed"
    assert failure.run_bundle.lifecycle.started_at is not None
    assert failure.run_bundle.lifecycle.completed_at is not None
    assert failure.error_code == "kernel_run_failed"
    assert "secret provider exception text" not in str(
        failure.run_bundle.to_dict()
    )
    assert "private prompt" not in str(failure.run_bundle.to_dict())


def test_artifact_metrics_cover_the_full_set_without_raw_ids_or_truncation() -> None:
    state = _state()
    identity = _identity("artifact-root")
    state.run_ledger.initialize(
        state=state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    context = SimpleNamespace(state=state)
    artifacts = [
        {"artifact_id": f"private-artifact-{index}", "revision": 1}
        for index in range(20)
    ]

    ToolExecutionHarness._record_tool_result_metric(
        context,
        ToolCall(call_id="tool-call-1", name="build", arguments={}),
        {"status": "ok"},
        artifacts,
    )
    bundle = state.run_ledger.materialize(kernel_status="completed")
    artifact_events = [
        event for event in bundle.metrics.events if event.kind == "artifact"
    ]
    tool_result = next(
        event for event in bundle.metrics.events if event.kind == "tool_result"
    )

    assert bundle.metrics.all.artifacts == 20
    assert len(artifact_events) == 20
    assert len(tool_result.evidence_refs) == 1
    assert all(event.subject_id.startswith("artifact_") for event in artifact_events)
    assert "private-artifact" not in str(bundle.to_dict())
