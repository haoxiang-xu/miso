from __future__ import annotations

import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
import httpx

from unchain.kernel import kernel_run_failure_from_exception
from unchain.kernel.failure import attach_kernel_run_failure
from unchain.kernel.loop import KernelLoop
from unchain.kernel.run_ledger import (
    RunLedger,
    build_model_attempt_receipt,
    child_run_identity,
    merge_run_bundle_values,
)
from unchain.kernel.state import RunState
from unchain.kernel.types import ModelTurnResult, ToolCall
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store
from unchain.run_bundle import (
    RunBundleProtocolError,
    RunBundleReducer,
    RunDescriptor,
    RunIdentity,
    RunLifecycle,
    RunMetricEvent,
)
from unchain.run_bundle_ledger import (
    RunBundleContinuationError,
    RunBundleLedgerConflictError,
    RunBundleLedgerIntegrityError,
)
from unchain.run_bundle_v2 import CompactRunBundle, run_bundle_from_dict
from unchain.retry import RetryConfig, RetriesExhaustedError
from unchain.subagents.types import SubagentResult, SubagentState
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


def _canonical_json_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _metric_events_sha256(metric_events: tuple[RunMetricEvent, ...]) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(
            [
                event.to_dict()
                for event in sorted(
                    metric_events,
                    key=lambda item: item.metric_event_id,
                )
            ]
        )
    ).hexdigest()


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


def test_compact_v2_full_envelope_enforces_exact_canonical_byte_limit(
    monkeypatch,
) -> None:
    identity = _identity("compact-v2-byte-boundary")
    bundle, _details = CompactRunBundle.from_facts(
        identity=identity,
        lifecycle=RunLifecycle(
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:01:00Z",
        ),
        descriptor=RunDescriptor(),
        revision=1,
        receipts=(),
        metric_events=(),
        children=(),
    )
    raw = bundle.to_dict()
    encoded_size = len(_canonical_json_bytes(raw))
    monkeypatch.setattr(
        "unchain.run_bundle_v2.COMPACT_RUN_BUNDLE_MAX_CANONICAL_BYTES",
        encoded_size - 1,
    )
    with pytest.raises(
        ValueError,
        match="compact bundle exceeds the canonical byte limit",
    ):
        CompactRunBundle.from_dict(raw)
    monkeypatch.setattr(
        "unchain.run_bundle_v2.COMPACT_RUN_BUNDLE_MAX_CANONICAL_BYTES",
        encoded_size,
    )
    assert CompactRunBundle.from_dict(raw) == bundle


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


def test_sqlite_requires_one_way_revision_advance_from_v1_to_v2(tmp_path) -> None:
    bound = _store(tmp_path).bind_execution("execution-1")
    identity = _identity("schema-advance-root")
    lifecycle = RunLifecycle(
        status="completed",
        started_at="2026-08-13T18:00:00Z",
        completed_at="2026-08-13T18:01:00Z",
    )
    v1 = RunBundleReducer.reduce(
        identity=identity,
        lifecycle=lifecycle,
        receipts=(),
        revision=1,
    )
    bound.persist_bundle(v1)
    compact_same_revision, same_details = CompactRunBundle.from_facts(
        identity=identity,
        lifecycle=lifecycle,
        descriptor=v1.descriptor,
        revision=1,
        receipts=(),
        metric_events=(),
        children=(),
    )
    with pytest.raises(
        RunBundleLedgerConflictError,
        match="advance the v1 durable head",
    ):
        bound.persist_compact_bundle_with_details(
            bundle=compact_same_revision,
            details=same_details,
        )

    compact, compact_details = CompactRunBundle.from_facts(
        identity=identity,
        lifecycle=lifecycle,
        descriptor=v1.descriptor,
        revision=2,
        receipts=(),
        metric_events=(),
        children=(),
    )
    bound.persist_compact_bundle_with_details(
        bundle=compact,
        details=compact_details,
    )
    later_v1 = RunBundleReducer.reduce(
        identity=identity,
        lifecycle=lifecycle,
        receipts=(),
        revision=3,
    )
    with pytest.raises(
        RunBundleLedgerConflictError,
        match="cannot overwrite the compact durable head",
    ):
        bound.persist_bundle(later_v1)


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


def test_continuation_claims_compact_v2_predecessor_idempotently(tmp_path) -> None:
    bound = _store(tmp_path).bind_execution("execution-1")
    predecessor_identity = _identity("old-compact-root")
    predecessor, details = CompactRunBundle.from_facts(
        identity=predecessor_identity,
        lifecycle=RunLifecycle(
            status="cancelled",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:01:00Z",
        ),
        descriptor=RunDescriptor(),
        revision=1,
        receipts=(),
        metric_events=(),
        children=(),
    )
    bound.persist_compact_bundle_with_details(
        bundle=predecessor,
        details=details,
    )
    successor = _identity("new-compact-root")

    assert bound.claim_continuation(
        successor=successor,
        requested_run_id=predecessor_identity.run_id,
    ) == predecessor
    assert bound.claim_continuation(successor=successor) == predecessor


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


def test_materialize_compact_projection_on_run_bundle_canonical_limit(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "unchain.run_bundle._MAX_CANONICAL_BYTES",
        50_000,
    )
    bound = _store(tmp_path).bind_execution("execution-1")
    state = _state()
    identity = _identity("compact-root")
    state.run_ledger.initialize(
        state=state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    state.run_ledger.attach_persistence(bound)
    for index in range(20):
        receipt = build_model_attempt_receipt(
            identity=identity,
            provider="openai",
            model="gpt-test",
            iteration=1,
            retry_ordinal=index,
            purpose="agent_turn",
            request_digest="d" * 64,
            route="openai.responses.create",
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:01Z",
            payload={},
        )
        state.run_ledger.append(receipt)
        state.run_ledger.record_metric_event(
            kind="model_attempt",
            subject_id=receipt.provider_call_id,
            outcome="completed",
        )
        state.run_ledger.record_metric_event(
            kind="artifact",
            subject_id=f"artifact-{index}",
            outcome="completed",
        )

    bundle = state.run_ledger.materialize(kernel_status="completed")
    compact = bundle.extensions["unchain.runtime/compact_projection"]
    assert compact["mode"] == "compact_v1"
    assert compact["projection_truncation"] == ["metric_events"]
    assert compact["projection_status"] == "completed"
    assert compact["metric_events_sha256"] == _metric_events_sha256(
        tuple(state.run_ledger.metric_events.values())
    )
    assert compact["projection_counts"]["provider_calls"] == 20
    assert compact["projection_counts"]["metric_events"] == 40
    assert len(bundle.provider_calls) == 20
    assert {receipt.provider for receipt in bundle.provider_calls} == {"openai"}
    assert tuple(sorted(event.kind for event in bundle.metrics.events)) == (
        "model_attempt",
    ) * 20
    assert len(bundle.metrics.events) == 20
    assert bundle.extensions["unchain.runtime/kernel_status"] == "completed"
    first_revision = bundle.revision

    state.run_ledger.record_metric_event(
        kind="iteration",
        subject_id="iteration:1",
        outcome="completed",
    )
    rebased = state.run_ledger.materialize(kernel_status="completed")
    assert rebased.revision == first_revision + 1
    rebased_compact = rebased.extensions["unchain.runtime/compact_projection"]
    assert rebased_compact["projection_counts"]["metric_events"] == 41


def test_materialize_compact_projection_on_proactive_threshold(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "unchain.kernel.run_ledger._COMPACT_PROJECTION_TRIGGER_BYTES",
        1,
    )
    bound = _store(tmp_path).bind_execution("execution-1")
    state = _state()
    identity = _identity("compact-proactive-root")
    state.run_ledger.initialize(
        state=state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    state.run_ledger.attach_persistence(bound)
    state.run_ledger.record_metric_event(
        kind="artifact",
        subject_id="proactive-artifact",
        outcome="completed",
    )

    bundle = state.run_ledger.materialize(kernel_status="completed")

    compact = bundle.extensions["unchain.runtime/compact_projection"]
    assert compact["mode"] == "compact_v1"
    assert compact["projection_truncation"] == ["metric_events"]
    assert compact["projection_counts"]["metric_events"] == 1
    assert bundle.metrics.events == ()


def test_compact_projection_metric_events_roundtrip_with_persistence(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "unchain.run_bundle._MAX_CANONICAL_BYTES",
        50_000,
    )
    bound = _store(tmp_path).bind_execution("execution-1")
    identity = _identity("compact-root")
    first_state = _state()
    first_state.run_ledger.initialize(
        state=first_state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    first_state.run_ledger.attach_persistence(bound)
    for index in range(20):
        receipt = build_model_attempt_receipt(
            identity=identity,
            provider="openai",
            model="gpt-test",
            iteration=1,
            retry_ordinal=index,
            purpose="agent_turn",
            request_digest="d" * 64,
            route="openai.responses.create",
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:01Z",
            payload={},
        )
        first_state.run_ledger.append(receipt)
        first_state.run_ledger.record_metric_event(
            kind="model_attempt",
            subject_id=receipt.provider_call_id,
            outcome="completed",
        )
        first_state.run_ledger.record_metric_event(
            kind="artifact",
            subject_id=f"artifact-{index}",
            outcome="completed",
        )

    compact = first_state.run_ledger.materialize(kernel_status="completed")
    compact_projection = compact.extensions["unchain.runtime/compact_projection"]
    assert compact_projection["mode"] == "compact_v1"
    assert compact_projection["projection_counts"]["metric_events"] == 40
    assert compact_projection["metric_events_sha256"] == _metric_events_sha256(
        tuple(first_state.run_ledger.metric_events.values())
    )
    assert tuple(sorted(event.kind for event in compact.metrics.events)) == (
        "model_attempt",
    ) * 20
    assert {event.outcome for event in compact.metrics.events} == {"completed"}

    cold_state = _state()
    cold_state.run_ledger.initialize(
        state=cold_state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    cold_state.run_ledger.attach_persistence(bound)
    replay = cold_state.run_ledger.materialize(kernel_status="completed")
    assert replay.bundle_id == compact.bundle_id
    assert replay.revision == compact.revision
    assert replay.extensions["unchain.runtime/compact_projection"] == compact_projection
    assert len(cold_state.run_ledger.metric_events) == 40
    compact_metric_events = tuple(
        sorted(
            first_state.run_ledger.metric_events.values(),
            key=lambda item: item.metric_event_id,
        )
    )
    assert {
        event.metric_event_id for event in cold_state.run_ledger.metric_events.values()
    } == {event.metric_event_id for event in compact_metric_events}
    assert {event.kind for event in compact_metric_events} == {
        "model_attempt",
        "artifact",
    }


def test_compact_projection_preserves_child_bundle_facts_on_canonical_limit(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "unchain.run_bundle._MAX_CANONICAL_BYTES",
        55_000,
    )
    bound = _store(tmp_path).bind_execution("execution-1")
    root_identity = _identity("compact-graph-root")
    child_identity = child_run_identity(
        parent=root_identity,
        child_run_id="compact-child",
        child_attempt_id="attempt-compact-child",
    )
    child_receipt = build_model_attempt_receipt(
        identity=child_identity,
        provider="openai",
        model="gpt-test",
        iteration=1,
        retry_ordinal=0,
        purpose="agent_turn",
        request_digest="e" * 64,
        route="openai.responses.create",
        status="completed",
        started_at="2026-08-13T18:00:00Z",
        completed_at="2026-08-13T18:00:01Z",
        payload={},
    )
    child_metric = RunMetricEvent(
        execution_id=child_identity.execution_id,
        attempt_id=child_identity.attempt_id,
        root_run_id=child_identity.root_run_id,
        owner_run_id=child_identity.run_id,
        parent_run_id=child_identity.parent_run_id,
        kind="artifact",
        subject_id="child-artifact",
        outcome="completed",
    )
    child_bundle = RunBundleReducer.reduce(
        identity=child_identity,
        lifecycle=RunLifecycle(
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:02Z",
        ),
        receipts=(child_receipt,),
        metric_events=(child_metric,),
    )

    first_state = _state()
    first_state.run_ledger.initialize(
        state=first_state,
        run_id=root_identity.run_id,
        explicit_identity=root_identity,
    )
    first_state.run_ledger.attach_persistence(bound)
    for index in range(20):
        receipt = build_model_attempt_receipt(
            identity=root_identity,
            provider="openai",
            model="gpt-test",
            iteration=1,
            retry_ordinal=index,
            purpose="agent_turn",
            request_digest="d" * 64,
            route="openai.responses.create",
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:01Z",
            payload={},
        )
        first_state.run_ledger.append(receipt)
        first_state.run_ledger.record_metric_event(
            kind="model_attempt",
            subject_id=receipt.provider_call_id,
            outcome="completed",
        )
        first_state.run_ledger.record_metric_event(
            kind="artifact",
            subject_id=f"root-artifact-{index}",
            outcome="completed",
        )

    compact = first_state.run_ledger.materialize(
        kernel_status="completed",
        child_bundle_values={child_bundle.bundle_id: child_bundle.to_dict()},
    )
    compact_projection = compact.extensions["unchain.runtime/compact_projection"]
    assert compact_projection["mode"] == "compact_v1"
    assert compact_projection["projection_counts"]["provider_calls"] == 21
    assert compact_projection["projection_counts"]["children"] == 1
    assert compact_projection["projection_counts"]["metric_events"] == 42
    expected_metric_events = (
        tuple(first_state.run_ledger.metric_events.values())
        + child_bundle.metrics.events
    )
    assert compact_projection["metric_events_sha256"] == _metric_events_sha256(
        expected_metric_events
    )
    assert {child.run_id for child in compact.children} == {"compact-child"}
    assert child_receipt.provider_call_id in compact.aggregation.descendant_call_ids
    assert child_receipt.provider_call_id in {
        receipt.provider_call_id for receipt in compact.provider_calls
    }

    cold_state = _state()
    cold_state.run_ledger.initialize(
        state=cold_state,
        run_id=root_identity.run_id,
        explicit_identity=root_identity,
    )
    cold_state.run_ledger.attach_persistence(bound)
    replay = cold_state.run_ledger.materialize(
        kernel_status="completed",
        child_bundle_values={child_bundle.bundle_id: child_bundle.to_dict()},
    )
    assert replay == compact
    assert len(cold_state.run_ledger.metric_events) == 42


def test_compact_child_projection_details_are_hydrated_for_root_graph_replay(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "unchain.run_bundle._MAX_CANONICAL_BYTES",
        30_000,
    )
    bound = _store(tmp_path).bind_execution("execution-1")
    root_identity = _identity("compact-graph-root")
    child_identity = child_run_identity(
        parent=root_identity,
        child_run_id="compact-child",
        child_attempt_id="attempt-compact-child",
    )

    child_state = _state()
    child_state.run_ledger.initialize(
        state=child_state,
        run_id=child_identity.run_id,
        explicit_identity=child_identity,
    )
    child_state.run_ledger.attach_persistence(bound)
    for index in range(100):
        child_state.run_ledger.record_metric_event(
            kind="artifact",
            subject_id=f"child-artifact-{index}",
            outcome="completed",
        )
    child_bundle = child_state.run_ledger.materialize(kernel_status="completed")
    assert child_bundle.extensions["unchain.runtime/compact_projection"][
        "projection_counts"
    ]["metric_events"] == 100
    child_metric_ids = {
        event.metric_event_id for event in child_state.run_ledger.metric_events.values()
    }

    root_state = _state()
    root_state.run_ledger.initialize(
        state=root_state,
        run_id=root_identity.run_id,
        explicit_identity=root_identity,
    )
    root_state.run_ledger.attach_persistence(bound)
    root_bundle = root_state.run_ledger.materialize(
        kernel_status="completed",
        child_bundle_values={child_bundle.bundle_id: child_bundle.to_dict()},
    )

    cold_state = _state()
    cold_state.run_ledger.initialize(
        state=cold_state,
        run_id=root_identity.run_id,
        explicit_identity=root_identity,
    )
    cold_state.run_ledger.attach_persistence(bound)
    replay = cold_state.run_ledger.materialize(
        kernel_status="completed",
        child_bundle_values={child_bundle.bundle_id: child_bundle.to_dict()},
    )

    assert replay == root_bundle
    assert child_metric_ids <= set(cold_state.run_ledger.metric_events)


def test_compact_projection_rejects_malformed_extension_on_replay(tmp_path) -> None:
    bound = _store(tmp_path).bind_execution("execution-1")
    identity = _identity("compact-malformed-root")
    malformed = RunBundleReducer.reduce(
        identity=identity,
        lifecycle=RunLifecycle(
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:01Z",
        ),
        receipts=(),
        extensions={
            "unchain.runtime/compact_projection": {
                "mode": "compact_v1",
            },
        },
    )
    bound.persist_bundle(malformed)

    cold_state = _state()
    cold_state.run_ledger.initialize(
        state=cold_state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    with pytest.raises(
        RunBundleProtocolError,
        match="compact projection extension is invalid",
    ):
        cold_state.run_ledger.attach_persistence(bound)


def test_compact_projection_rejects_present_null_extension_on_replay(tmp_path) -> None:
    bound = _store(tmp_path).bind_execution("execution-1")
    identity = _identity("compact-null-extension")
    malformed = RunBundleReducer.reduce(
        identity=identity,
        lifecycle=RunLifecycle(
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:01Z",
        ),
        receipts=(),
        extensions={"unchain.runtime/compact_projection": None},
    )
    bound.persist_bundle(malformed)

    cold_state = _state()
    cold_state.run_ledger.initialize(
        state=cold_state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    with pytest.raises(
        RunBundleProtocolError,
        match="compact projection extension is invalid",
    ):
        cold_state.run_ledger.attach_persistence(bound)


def test_compact_projection_details_digest_is_bound_to_bundle_extension(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "unchain.run_bundle._MAX_CANONICAL_BYTES",
        50_000,
    )
    store = _store(tmp_path)
    bound = store.bind_execution("execution-1")
    identity = _identity("compact-bound-details")
    first_state = _state()
    first_state.run_ledger.initialize(
        state=first_state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    first_state.run_ledger.attach_persistence(bound)
    for index in range(20):
        receipt = build_model_attempt_receipt(
            identity=identity,
            provider="openai",
            model="gpt-test",
            iteration=1,
            retry_ordinal=index,
            purpose="agent_turn",
            request_digest="d" * 64,
            route="openai.responses.create",
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:01Z",
            payload={},
        )
        first_state.run_ledger.append(receipt)
        first_state.run_ledger.record_metric_event(
            kind="model_attempt",
            subject_id=receipt.provider_call_id,
            outcome="completed",
        )
        first_state.run_ledger.record_metric_event(
            kind="artifact",
            subject_id=f"artifact-{index}",
            outcome="completed",
        )
    compact = first_state.run_ledger.materialize(kernel_status="completed")
    compact_projection = compact.extensions["unchain.runtime/compact_projection"]

    tampered_events = tuple(
        RunMetricEvent(
            execution_id=identity.execution_id,
            attempt_id=identity.attempt_id,
            root_run_id=identity.root_run_id,
            owner_run_id=identity.run_id,
            parent_run_id=identity.parent_run_id,
            kind="artifact",
            subject_id=f"tampered-{index}",
            outcome="completed",
        )
        for index in range(compact_projection["projection_counts"]["metric_events"])
    )
    tampered_json = _canonical_json_bytes(
        [
            event.to_dict()
            for event in sorted(tampered_events, key=lambda item: item.metric_event_id)
        ]
    )
    tampered_sha256 = hashlib.sha256(tampered_json).hexdigest()
    assert tampered_sha256 != compact_projection["metric_events_sha256"]
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            UPDATE run_bundle_projection_details_v1
            SET metric_events_json = ?,
                metric_events_sha256 = ?,
                metric_events_count = ?
            WHERE execution_id = ? AND bundle_id = ? AND revision = ?
            """,
            (
                tampered_json,
                tampered_sha256,
                len(tampered_events),
                identity.execution_id,
                compact.bundle_id,
                compact.revision,
            ),
        )
        connection.commit()

    cold_state = _state()
    cold_state.run_ledger.initialize(
        state=cold_state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    with pytest.raises(
        RunBundleLedgerIntegrityError,
        match="projection details durable bytes changed",
    ):
        cold_state.run_ledger.attach_persistence(bound)


def test_compact_projection_reuses_final_revision_details_row_and_never_conflicts(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "unchain.run_bundle._MAX_CANONICAL_BYTES",
        50_000,
    )
    store = _store(tmp_path)
    bound = store.bind_execution("execution-1")
    identity = _identity("compact-root")
    first_state = _state()
    first_state.run_ledger.initialize(
        state=first_state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    first_state.run_ledger.attach_persistence(bound)
    for index in range(20):
        receipt = build_model_attempt_receipt(
            identity=identity,
            provider="openai",
            model="gpt-test",
            iteration=1,
            retry_ordinal=index,
            purpose="agent_turn",
            request_digest="d" * 64,
            route="openai.responses.create",
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:01Z",
            payload={},
        )
        first_state.run_ledger.append(receipt)
        first_state.run_ledger.record_metric_event(
            kind="model_attempt",
            subject_id=receipt.provider_call_id,
            outcome="completed",
        )
        first_state.run_ledger.record_metric_event(
            kind="artifact",
            subject_id=f"artifact-{index}",
            outcome="completed",
        )

    compact = first_state.run_ledger.materialize(kernel_status="completed")
    assert compact.extensions["unchain.runtime/compact_projection"]["mode"] == "compact_v1"
    with sqlite3.connect(store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        before = connection.execute(
            "SELECT COUNT(*) AS count FROM run_bundle_projection_details_v1 "
            "WHERE bundle_id = ?",
            (compact.bundle_id,),
        ).fetchone()["count"]
    assert before == 1

    replay_state = _state()
    replay_state.run_ledger.initialize(
        state=replay_state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    replay_state.run_ledger.attach_persistence(bound)
    replay_state.run_ledger.record_metric_event(
        kind="iteration",
        subject_id="iteration:1",
        outcome="completed",
    )
    rebased = replay_state.run_ledger.materialize(kernel_status="completed")
    assert rebased.revision == compact.revision + 1
    with sqlite3.connect(store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        after = connection.execute(
            "SELECT COUNT(*) AS count FROM run_bundle_projection_details_v1 "
            "WHERE bundle_id = ?",
            (compact.bundle_id,),
        ).fetchone()["count"]
    assert after == 2


def test_materialize_switches_to_v2_when_compaction_still_exceeds_v1_limit(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "unchain.run_bundle._MAX_CANONICAL_BYTES",
        4096,
    )
    bound = _store(tmp_path).bind_execution("execution-1")
    state = _state()
    identity = _identity("compact-root-last-resort")
    state.run_ledger.initialize(
        state=state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    state.run_ledger.attach_persistence(bound)
    for index in range(20):
        receipt = build_model_attempt_receipt(
            identity=identity,
            provider="openai",
            model="gpt-test",
            iteration=1,
            retry_ordinal=index,
            purpose="agent_turn",
            request_digest="d" * 64,
            route="openai.responses.create",
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:01Z",
            payload={},
        )
        state.run_ledger.append(receipt)
        state.run_ledger.record_metric_event(
            kind="model_attempt",
            subject_id=receipt.provider_call_id,
            outcome="completed",
        )
        state.run_ledger.record_metric_event(
            kind="artifact",
            subject_id=f"artifact-{index}",
            outcome="completed",
        )

    bundle = state.run_ledger.materialize(kernel_status="completed")

    assert isinstance(bundle, CompactRunBundle)
    assert bundle.to_dict()["schema"] == "unchain.run_bundle.v2"
    assert bundle.provider_call_count == 20
    assert bundle.metrics["event_count"] == 40
    receipts, metric_events, children = bound.load_compact_bundle_details(
        bundle=bundle,
    )
    assert len(receipts) == 20
    assert len(metric_events) == 40
    assert children == ()

    cold_state = _state()
    cold_state.run_ledger.initialize(
        state=cold_state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    cold_state.run_ledger.attach_persistence(bound)
    replay = cold_state.run_ledger.materialize(kernel_status="completed")
    assert replay == bundle
    assert len(cold_state.run_ledger.receipts) == 20
    assert len(cold_state.run_ledger.metric_events) == 40

    error = RuntimeError("private provider failure")
    failure_bundle, failure_details = CompactRunBundle.from_facts(
        identity=identity,
        lifecycle=RunLifecycle(
            status="failed",
            started_at=bundle.lifecycle.started_at,
            completed_at=bundle.lifecycle.completed_at,
        ),
        descriptor=bundle.descriptor,
        revision=bundle.revision + 1,
        receipts=receipts,
        metric_events=metric_events,
        children=children,
        extensions=bundle.extensions,
    )
    bound.persist_compact_bundle_with_details(
        bundle=failure_bundle,
        details=failure_details,
    )
    record = attach_kernel_run_failure(
        error,
        error_category="provider",
        error_code="provider_failed",
        run_bundle=failure_bundle,
    )
    assert kernel_run_failure_from_exception(error) == record


def test_compact_v2_details_ref_rejects_durable_fact_tampering(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("unchain.run_bundle._MAX_CANONICAL_BYTES", 4096)
    store = _store(tmp_path)
    bound = store.bind_execution("execution-1")
    state = _state()
    identity = _identity("compact-v2-tamper")
    state.run_ledger.initialize(
        state=state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    state.run_ledger.attach_persistence(bound)
    for index in range(20):
        receipt = build_model_attempt_receipt(
            identity=identity,
            provider="openai",
            model="gpt-test",
            iteration=1,
            retry_ordinal=index,
            purpose="agent_turn",
            request_digest="d" * 64,
            route="openai.responses.create",
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:01Z",
            payload={},
        )
        state.run_ledger.append(receipt)
        state.run_ledger.record_metric_event(
            kind="model_attempt",
            subject_id=receipt.provider_call_id,
            outcome="completed",
        )
        state.run_ledger.record_metric_event(
            kind="artifact",
            subject_id=f"artifact-before-tamper-{index}",
            outcome="completed",
        )
    bundle = state.run_ledger.materialize(kernel_status="completed")
    assert isinstance(bundle, CompactRunBundle)

    with sqlite3.connect(store.database_path) as connection:
        row = connection.execute(
            "SELECT details_json FROM run_bundle_compact_v2 "
            "WHERE execution_id = ? AND bundle_id = ? AND revision = ?",
            ("execution-1", bundle.bundle_id, bundle.revision),
        ).fetchone()
        details = json.loads(bytes(row[0]).decode("utf-8"))
        details["metric_events"][0]["subject_id"] = "artifact-after-tamper"
        encoded = _canonical_json_bytes(details)
        connection.execute(
            "UPDATE run_bundle_compact_v2 "
            "SET details_json = ?, details_sha256 = ? "
            "WHERE execution_id = ? AND bundle_id = ? AND revision = ?",
            (
                encoded,
                hashlib.sha256(encoded).hexdigest(),
                "execution-1",
                bundle.bundle_id,
                bundle.revision,
            ),
        )

    corrupted = _state()
    corrupted.run_ledger.initialize(
        state=corrupted,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    with pytest.raises(
        RunBundleLedgerIntegrityError,
        match="compact durable row is invalid",
    ):
        corrupted.run_ledger.attach_persistence(bound)


def test_completion_merge_hydrates_v2_root_facts_without_double_counting(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("unchain.run_bundle._MAX_CANONICAL_BYTES", 4096)
    bound = _store(tmp_path).bind_execution("execution-1")
    root_identity = _identity("compact-v2-completion-root")
    root_state = _state()
    root_state.run_ledger.initialize(
        state=root_state,
        run_id=root_identity.run_id,
        explicit_identity=root_identity,
    )
    root_state.run_ledger.attach_persistence(bound)
    for index in range(20):
        receipt = build_model_attempt_receipt(
            identity=root_identity,
            provider="openai",
            model="gpt-test",
            iteration=1,
            retry_ordinal=index,
            purpose="agent_turn",
            request_digest="d" * 64,
            route="openai.responses.create",
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:01Z",
            payload={},
        )
        root_state.run_ledger.append(receipt)
        root_state.run_ledger.record_metric_event(
            kind="model_attempt",
            subject_id=receipt.provider_call_id,
            outcome="completed",
        )
        root_state.run_ledger.record_metric_event(
            kind="artifact",
            subject_id=f"root-artifact-{index}",
            outcome="completed",
        )
    root_bundle = root_state.run_ledger.materialize(kernel_status="completed")
    assert isinstance(root_bundle, CompactRunBundle)

    repair_identity = child_run_identity(
        parent=root_identity,
        child_run_id="completion-repair-1",
        child_attempt_id="attempt-completion-repair-1",
        relation="auxiliary",
    )
    repair_receipt = build_model_attempt_receipt(
        identity=repair_identity,
        provider="openai",
        model="gpt-test",
        iteration=1,
        retry_ordinal=0,
        purpose="completion_repair",
        request_digest="e" * 64,
        route="openai.responses.create",
        status="completed",
        started_at="2026-08-13T18:00:00Z",
        completed_at="2026-08-13T18:00:01Z",
        payload={},
    )
    repair_state = _state()
    repair_state.run_ledger.initialize(
        state=repair_state,
        run_id=repair_identity.run_id,
        explicit_identity=repair_identity,
    )
    repair_state.run_ledger.attach_persistence(bound)
    repair_state.run_ledger.append(repair_receipt)
    repair_state.run_ledger.record_metric_event(
        kind="model_attempt",
        subject_id=repair_receipt.provider_call_id,
        outcome="completed",
    )
    repair_bundle = repair_state.run_ledger.materialize(
        kernel_status="completed"
    )
    assert isinstance(repair_bundle, CompactRunBundle)

    merged_value = merge_run_bundle_values(
        [root_bundle.to_dict(), repair_bundle.to_dict(), repair_bundle.to_dict()],
        compact_details_ledger=bound,
    )
    merged = run_bundle_from_dict(merged_value)
    assert isinstance(merged, CompactRunBundle)
    assert merged.provider_call_count == 21
    receipts, events, children = bound.load_compact_bundle_details(bundle=merged)
    assert repair_receipt.provider_call_id in {
        receipt.provider_call_id for receipt in receipts
    }
    assert len(receipts) == 21
    assert len(events) == 41
    assert {child.run_id for child in children} == {repair_identity.run_id}


def test_compact_v2_child_roundtrips_through_subagent_state_and_root_union(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("unchain.run_bundle._MAX_CANONICAL_BYTES", 4096)
    bound = _store(tmp_path).bind_execution("execution-1")
    root_identity = _identity("compact-v2-graph-root")
    child_identity = child_run_identity(
        parent=root_identity,
        child_run_id="compact-v2-child",
        child_attempt_id="attempt-compact-v2-child",
    )
    child_state = _state()
    child_state.run_ledger.initialize(
        state=child_state,
        run_id=child_identity.run_id,
        explicit_identity=child_identity,
    )
    child_state.run_ledger.attach_persistence(bound)
    for index in range(20):
        receipt = build_model_attempt_receipt(
            identity=child_identity,
            provider="openai",
            model="gpt-test",
            iteration=1,
            retry_ordinal=index,
            purpose="agent_turn",
            request_digest="d" * 64,
            route="openai.responses.create",
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:01Z",
            payload={},
        )
        child_state.run_ledger.append(receipt)
        child_state.run_ledger.record_metric_event(
            kind="model_attempt",
            subject_id=receipt.provider_call_id,
            outcome="completed",
        )
    child_bundle = child_state.run_ledger.materialize(kernel_status="completed")
    assert isinstance(child_bundle, CompactRunBundle)

    result = SubagentResult(
        mode="delegate",
        agent_name="worker",
        template_name=None,
        status="completed",
        run_bundle=child_bundle.to_dict(),
    )
    restored = SubagentState.from_raw(
        {"run_bundles": {child_bundle.bundle_id: result.run_bundle}}
    )
    assert restored.run_bundles[child_bundle.bundle_id] == child_bundle.to_dict()

    root_state = _state()
    root_state.run_ledger.initialize(
        state=root_state,
        run_id=root_identity.run_id,
        explicit_identity=root_identity,
    )
    root_state.run_ledger.attach_persistence(bound)
    root_bundle = root_state.run_ledger.materialize(
        kernel_status="completed",
        child_bundle_values=restored.run_bundles,
    )
    assert isinstance(root_bundle, CompactRunBundle)
    assert root_bundle.provider_call_count == 20
    receipts, events, children = bound.load_compact_bundle_details(
        bundle=root_bundle
    )
    assert len(receipts) == 20
    assert len(events) == 20
    assert {child.run_id for child in children} == {child_identity.run_id}

    cold_root = _state()
    cold_root.run_ledger.initialize(
        state=cold_root,
        run_id=root_identity.run_id,
        explicit_identity=root_identity,
    )
    cold_root.run_ledger.attach_persistence(bound)
    assert cold_root.run_ledger.materialize(
        kernel_status="completed",
        child_bundle_values=restored.run_bundles,
    ) == root_bundle


def test_compact_projection_requires_durable_projection_details(monkeypatch) -> None:
    monkeypatch.setattr(
        "unchain.run_bundle._MAX_CANONICAL_BYTES",
        50_000,
    )
    state = _state()
    identity = _identity("compact-root-no-details")
    state.run_ledger.initialize(
        state=state,
        run_id=identity.run_id,
        explicit_identity=identity,
    )
    for index in range(20):
        receipt = build_model_attempt_receipt(
            identity=identity,
            provider="openai",
            model="gpt-test",
            iteration=1,
            retry_ordinal=index,
            purpose="agent_turn",
            request_digest="d" * 64,
            route="openai.responses.create",
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:01Z",
            payload={},
        )
        state.run_ledger.append(receipt)
        state.run_ledger.record_metric_event(
            kind="model_attempt",
            subject_id=receipt.provider_call_id,
            outcome="completed",
        )
        state.run_ledger.record_metric_event(
            kind="artifact",
            subject_id=f"artifact-{index}",
            outcome="completed",
        )

    with pytest.raises(
        RunBundleProtocolError,
        match="compact projection requires durable projection detail persistence",
    ):
        state.run_ledger.materialize(kernel_status="completed")


def test_persist_bundle_with_projection_details_is_atomic_on_projection_conflict(
    tmp_path,
) -> None:
    bound = _store(tmp_path).bind_execution("execution-1")
    identity = _identity("compact-root")
    original = RunBundleReducer.reduce(
        identity=identity,
        lifecycle=RunLifecycle(
            status="running",
            started_at="2026-08-13T18:00:00Z",
            completed_at=None,
        ),
        receipts=(),
        revision=1,
    )
    bound.persist_bundle(original)
    conflict = RunBundleReducer.reduce(
        identity=identity,
        lifecycle=RunLifecycle(
            status="completed",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:01Z",
        ),
        receipts=(),
        revision=1,
    )
    with pytest.raises(RunBundleLedgerConflictError):
        bound.persist_bundle_with_projection_details(
            bundle=conflict,
            projection_hash="a" * 64,
            projection_metric_events=(),
        )
    with sqlite3.connect(tmp_path / "context" / "context.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        detail_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM run_bundle_projection_details_v1
            WHERE execution_id = ? AND bundle_id = ? AND revision = ?
            """,
            (
                "execution-1",
                conflict.bundle_id,
                conflict.revision,
            ),
        ).fetchone()["count"]
    assert detail_count == 0


def test_projection_details_accept_more_than_legacy_receipt_query_limit(
    tmp_path,
) -> None:
    bound = _store(tmp_path).bind_execution("execution-1")
    identity = _identity("compact-root-large-details")
    bundle = RunBundleReducer.reduce(
        identity=identity,
        lifecycle=RunLifecycle(
            status="running",
            started_at="2026-08-13T18:00:00Z",
            completed_at=None,
        ),
        receipts=(),
        revision=1,
    )
    metric_events = tuple(
        RunMetricEvent(
            execution_id=identity.execution_id,
            attempt_id=identity.attempt_id,
            root_run_id=identity.root_run_id,
            owner_run_id=identity.run_id,
            parent_run_id=identity.parent_run_id,
            kind="artifact",
            subject_id=f"artifact-{index}",
            outcome="completed",
        )
        for index in range(10_001)
    )

    bound.persist_bundle_with_projection_details(
        bundle=bundle,
        projection_hash="a" * 64,
        projection_metric_events=metric_events,
    )
    assert (
        len(
            bound.load_projection_details(
                bundle_id=bundle.bundle_id,
                revision=bundle.revision,
                projection_hash="a" * 64,
                metric_events_sha256=_metric_events_sha256(metric_events),
            )
        )
        == 10_001
    )
