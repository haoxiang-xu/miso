from __future__ import annotations

import hashlib
import json

import pytest

from unchain.context.tool_catalog import ToolCatalogEnvelope
from unchain.durability import is_durable_persistence_failure
from unchain.journal.models import AttemptRef, GenerationRef, OperationRef
from unchain.journal.provider_result import recover_provider_turn_result
from unchain.journal.provider_wire import (
    persist_provider_wire_snapshot,
    recover_provider_wire_authority,
)
from unchain.kernel.types import ModelTurnResult
from unchain.kernel.run_ledger import build_model_attempt_receipt
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store
from unchain.providers.durable_turn_runtime import (
    DurableProviderTurnError,
    DurableProviderTurnMode,
    DurableProviderTurnRuntime,
    DurableProviderTurnStatus,
    DurableProviderTurnUncertainError,
    ExactProviderRouteFailure,
    ExactProviderRouteFailureKind,
    ExactProviderRouteTransport,
)
from unchain.providers.request_lease import (
    ProviderRequestLeaseCoordinator,
    ProviderRequestStatus,
    ProviderRequestSubject,
)
from unchain.providers.wire_envelope import ProviderWireEnvelope, ProviderWireRoute
from unchain.retry import RetryConfig, RetriesExhaustedError
from unchain.run_bundle import RunIdentity


ATTEMPT = AttemptRef(
    GenerationRef("execution-durable-turn", "generation-durable-turn"),
    "attempt-durable-turn",
)
ITERATION = 4


def _json_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _operation(name: str, digit: str) -> OperationRef:
    return OperationRef(name, digit * 64)


def _store(tmp_path) -> SQLiteContextV2Store:
    return SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )


def _catalog() -> ToolCatalogEnvelope:
    return ToolCatalogEnvelope(
        attempt=ATTEMPT,
        iteration=ITERATION,
        provider="openai",
        model="frontier-model",
        semantic_schemas=[],
        entries=[],
        required_betas_sha256=_json_sha256([]),
        prompt_sha256="0" * 64,
        exposure_plan_sha256="1" * 64,
    )


def _envelope(
    catalog: ToolCatalogEnvelope,
    *,
    fallback: bool = False,
) -> ProviderWireEnvelope:
    common = {
        "model": "frontier-model",
        "stream": True,
        "store": False,
    }
    primary = {
        **common,
        "input": [{"role": "user", "content": "hello"}],
    }
    routes = [ProviderWireRoute(name="primary", request=primary)]
    if fallback:
        primary["previous_response_id"] = "response-before-restart"
        routes = [
            ProviderWireRoute(name="primary", request=primary),
            ProviderWireRoute(
                name="openai_previous_response_fallback",
                request={
                    **common,
                    "input": [{"role": "user", "content": "complete replay"}],
                },
            ),
        ]
    return ProviderWireEnvelope(
        attempt=ATTEMPT,
        iteration=ITERATION,
        provider="openai",
        configured_model="frontier-model",
        request_model="frontier-model",
        adapter_revision="unchain.openai.responses.request.v1",
        transport_kind="openai.responses.create",
        transport_target_sha256="2" * 64,
        source_request_sha256="3" * 64,
        source_payload_sha256="4" * 64,
        catalog_sha256=catalog.catalog_sha256,
        prompt_sha256=catalog.prompt_sha256,
        tool_schema_sha256=catalog.tool_schema_sha256,
        required_betas=(),
        base_anthropic_betas=(),
        routes=routes,
    )


def _authority(tmp_path, *, fallback: bool = False):
    store = _store(tmp_path)
    repository = store.bind_execution(ATTEMPT.generation.execution_id)
    catalog = _catalog()
    envelope = _envelope(catalog, fallback=fallback)
    receipt = persist_provider_wire_snapshot(
        repository,
        envelope=envelope,
        catalog=catalog,
        artifact_operation=_operation("durable-turn-wire-artifact", "8"),
        event_operation=_operation("durable-turn-wire-event", "9"),
        event_id="durable-turn-wire-snapshot",
        expected_artifact_revision=0,
    )
    authority = recover_provider_wire_authority(
        repository,
        attempt=ATTEMPT,
        iteration=ITERATION,
        catalog=catalog,
        expected_provider="openai",
        expected_adapter_revision=envelope.adapter_revision,
        expected_envelope_sha256=envelope.envelope_sha256,
        expected_artifact=receipt.artifact,
        expected_cursor=receipt.cursor,
    )
    return store, repository, authority


def _result(text: str = "durable result") -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": text}],
        tool_calls=[],
        final_text=text,
        response_id="response-durable-turn",
        consumed_tokens=3,
        input_tokens=2,
        output_tokens=1,
    )


def _receipt_factory():
    identity = RunIdentity(
        execution_id=ATTEMPT.generation.execution_id,
        attempt_id=ATTEMPT.attempt_id,
        root_run_id=ATTEMPT.attempt_id,
        run_id=ATTEMPT.attempt_id,
        parent_run_id=None,
        relation="root",
    )

    def build(send_context, completed_at, outcome, classification, result):
        return build_model_attempt_receipt(
            identity=identity,
            provider="openai",
            model="frontier-model",
            iteration=ITERATION,
            retry_ordinal=send_context.physical_ordinal,
            purpose="agent_turn",
            request_digest="d" * 64,
            route="openai.responses.create",
            started_at="2026-08-13T18:00:00Z",
            completed_at=completed_at,
            turn=result,
            status=outcome,
            classification=classification,
        )

    return build


class _Transport(ExactProviderRouteTransport):
    def __init__(self, outcomes, *, before_send=None) -> None:
        self.outcomes = list(outcomes)
        self.calls = []
        self.before_send = before_send

    def send(self, *, envelope, route, retry_ordinal):
        if self.before_send is not None:
            self.before_send(envelope, route, retry_ordinal)
        self.calls.append((envelope, route, retry_ordinal))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _runtime(
    repository, transport, *, mode=DurableProviderTurnMode.ENFORCE_TEST, sleep=None
):
    return DurableProviderTurnRuntime(
        mode=mode,
        store=repository,
        transport=transport,
        sleep=(sleep if sleep is not None else lambda _seconds: None),
    )


@pytest.mark.parametrize(
    ("mode", "status"),
    (
        (DurableProviderTurnMode.OFF, DurableProviderTurnStatus.BYPASSED),
        (DurableProviderTurnMode.SHADOW, DurableProviderTurnStatus.SHADOWED),
    ),
)
def test_closed_modes_never_claim_or_send(tmp_path, mode, status) -> None:
    _store_value, repository, authority = _authority(tmp_path)
    transport = _Transport([_result()])

    outcome = _runtime(repository, transport, mode=mode).execute(
        authority=authority,
        retry_config=RetryConfig(max_retries=0),
    )

    assert outcome.status is status
    assert outcome.result is None
    assert transport.calls == []
    subject = ProviderRequestSubject(
        ATTEMPT,
        ITERATION,
        authority.envelope.envelope_sha256,
        "primary",
        0,
    )
    assert repository.load(subject=subject) is None


@pytest.mark.parametrize(
    "mode",
    (DurableProviderTurnMode.OFF, DurableProviderTurnMode.SHADOW),
)
@pytest.mark.parametrize("evidence", ("started", "completed"))
def test_closed_modes_fail_when_exact_subject_has_enforce_evidence(
    tmp_path,
    mode,
    evidence,
) -> None:
    _store_value, repository, authority = _authority(tmp_path)
    route = authority.envelope.routes[0]
    if evidence == "started":
        ProviderRequestLeaseCoordinator(repository).claim_initial(
            attempt=ATTEMPT,
            iteration=ITERATION,
            envelope_sha256=authority.envelope.envelope_sha256,
            route=route.name,
            route_sha256=route.route_sha256,
            operation=_operation("mode-downgrade-started", "7"),
        )
    else:
        _runtime(repository, _Transport([_result()])).execute(
            authority=authority,
            retry_config=RetryConfig(max_retries=0),
        )

    no_send = _Transport([])
    with pytest.raises(DurableProviderTurnError, match="durable evidence"):
        _runtime(repository, no_send, mode=mode).execute(
            authority=authority,
            retry_config=RetryConfig(max_retries=0),
        )

    assert no_send.calls == []


def test_success_persists_result_and_restart_reuses_it_without_network(
    tmp_path,
) -> None:
    store, repository, authority = _authority(tmp_path)
    transport = _Transport([_result()])

    first = _runtime(repository, transport).execute(
        authority=authority,
        retry_config=RetryConfig(max_retries=0),
    )

    assert first.status is DurableProviderTurnStatus.COMPLETED
    assert first.result.final_text == "durable result"
    assert first.recovered is False
    assert [(call[1].name, call[2]) for call in transport.calls] == [("primary", 0)]

    reopened = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    no_send = _Transport([])
    recovered = _runtime(reopened, no_send).execute(
        authority=authority,
        retry_config=RetryConfig(max_retries=0),
    )

    assert recovered.status is DurableProviderTurnStatus.COMPLETED
    assert recovered.result == first.result
    assert recovered.recovered is True
    assert no_send.calls == []
    assert store.database_path.exists()


def test_retry_safe_failure_gets_a_new_lease_before_the_second_send(tmp_path) -> None:
    _store_value, repository, authority = _authority(tmp_path)
    failure = ExactProviderRouteFailure(
        ExactProviderRouteFailureKind.TRANSIENT_RETRY_SAFE,
        RuntimeError("temporary"),
    )
    started_before_send = []

    def assert_started(envelope, route, retry_ordinal):
        subject = ProviderRequestSubject(
            ATTEMPT,
            ITERATION,
            envelope.envelope_sha256,
            route.name,
            retry_ordinal,
        )
        lease = repository.load(subject=subject)
        assert lease.status is ProviderRequestStatus.STARTED
        assert lease.route_sha256 == route.route_sha256
        started_before_send.append(subject)

    transport = _Transport(
        [failure, _result("after retry")],
        before_send=assert_started,
    )
    sleeps = []

    outcome = _runtime(repository, transport, sleep=sleeps.append).execute(
        authority=authority,
        retry_config=RetryConfig(
            max_retries=1,
            base_delay_ms=1,
            max_delay_ms=1,
            jitter_ratio=0,
        ),
    )

    assert outcome.result.final_text == "after retry"
    assert [(call[1].name, call[2]) for call in transport.calls] == [
        ("primary", 0),
        ("primary", 1),
    ]
    assert [subject.retry_ordinal for subject in started_before_send] == [0, 1]
    assert sleeps == [0.001]
    first_subject = ProviderRequestSubject(
        ATTEMPT,
        ITERATION,
        authority.envelope.envelope_sha256,
        "primary",
        0,
    )
    second_subject = ProviderRequestSubject(
        ATTEMPT,
        ITERATION,
        authority.envelope.envelope_sha256,
        "primary",
        1,
    )
    assert repository.load(subject=first_subject).status is ProviderRequestStatus.FAILED
    assert (
        repository.load(subject=second_subject).status
        is ProviderRequestStatus.COMPLETED
    )
    no_send = _Transport([])
    replay = _runtime(repository, no_send).execute(
        authority=authority,
        retry_config=RetryConfig(
            max_retries=0,
            base_delay_ms=0,
            max_delay_ms=0,
            jitter_ratio=0,
        ),
    )
    assert replay.result == outcome.result
    assert replay.recovered is True
    assert no_send.calls == []


def test_cold_retry_preserves_the_physical_ordinal_after_restart(tmp_path) -> None:
    _store_value, repository, authority = _authority(tmp_path)
    transient = ExactProviderRouteFailure(
        ExactProviderRouteFailureKind.TRANSIENT_RETRY_SAFE,
        RuntimeError("temporary before restart"),
    )
    first_transport = _Transport([transient])
    first_contexts = []

    class ProcessRestart(RuntimeError):
        pass

    def restart_during_backoff(_seconds):
        raise ProcessRestart("restart during retry backoff")

    with pytest.raises(ProcessRestart, match="restart during retry backoff"):
        _runtime(
            repository,
            first_transport,
            sleep=restart_during_backoff,
        ).execute(
            authority=authority,
            retry_config=RetryConfig(
                max_retries=1,
                base_delay_ms=0,
                max_delay_ms=0,
                jitter_ratio=0,
            ),
            after_send=lambda context, *_rest: first_contexts.append(context),
        )

    reopened = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    second_transport = _Transport([_result("cold retry")])
    second_contexts = []
    outcome = _runtime(reopened, second_transport).execute(
        authority=authority,
        retry_config=RetryConfig(
            max_retries=1,
            base_delay_ms=0,
            max_delay_ms=0,
            jitter_ratio=0,
        ),
        after_send=lambda context, *_rest: second_contexts.append(context),
        build_run_receipt=_receipt_factory(),
    )

    assert [(call[1].name, call[2]) for call in first_transport.calls] == [
        ("primary", 0)
    ]
    assert [(call[1].name, call[2]) for call in second_transport.calls] == [
        ("primary", 1)
    ]
    assert [context.physical_ordinal for context in first_contexts] == [0]
    assert [context.physical_ordinal for context in second_contexts] == [1]
    assert outcome.run_receipt.identity.retry_ordinal == 1

    no_send = _Transport([])
    replay = _runtime(reopened, no_send).execute(
        authority=authority,
        retry_config=RetryConfig(max_retries=1),
    )
    assert replay.result.final_text == "cold retry"
    assert replay.recovered is True
    assert no_send.calls == []


def test_fallback_retry_uses_consecutive_physical_ordinals(tmp_path) -> None:
    _store_value, repository, authority = _authority(tmp_path, fallback=True)
    primary_fallback = ExactProviderRouteFailure(
        ExactProviderRouteFailureKind.PREVIOUS_RESPONSE_FALLBACK,
        RuntimeError("remote continuation unavailable"),
    )
    fallback_transient = ExactProviderRouteFailure(
        ExactProviderRouteFailureKind.TRANSIENT_RETRY_SAFE,
        RuntimeError("local replay transport is temporarily unavailable"),
    )
    transport = _Transport(
        [primary_fallback, fallback_transient, _result("fallback retry")]
    )
    contexts = []

    outcome = _runtime(repository, transport).execute(
        authority=authority,
        retry_config=RetryConfig(
            max_retries=1,
            base_delay_ms=0,
            max_delay_ms=0,
            jitter_ratio=0,
        ),
        after_send=lambda context, *_rest: contexts.append(context),
        build_run_receipt=_receipt_factory(),
    )

    assert [(call[1].name, call[2]) for call in transport.calls] == [
        ("primary", 0),
        ("openai_previous_response_fallback", 0),
        ("openai_previous_response_fallback", 1),
    ]
    assert [context.physical_ordinal for context in contexts] == [0, 1, 2]
    assert outcome.run_receipt.identity.retry_ordinal == 2


def test_primary_retry_cannot_transition_to_fallback(tmp_path) -> None:
    _store_value, repository, authority = _authority(tmp_path, fallback=True)
    transient = ExactProviderRouteFailure(
        ExactProviderRouteFailureKind.TRANSIENT_RETRY_SAFE,
        RuntimeError("retry primary once"),
    )
    late_fallback = ExactProviderRouteFailure(
        ExactProviderRouteFailureKind.PREVIOUS_RESPONSE_FALLBACK,
        RuntimeError("late fallback is ambiguous"),
    )
    transport = _Transport([transient, late_fallback])

    with pytest.raises(BaseException) as caught:
        _runtime(repository, transport).execute(
            authority=authority,
            retry_config=RetryConfig(
                max_retries=1,
                base_delay_ms=0,
                max_delay_ms=0,
                jitter_ratio=0,
            ),
        )

    assert is_durable_persistence_failure(caught.value)
    assert [(call[1].name, call[2]) for call in transport.calls] == [
        ("primary", 0),
        ("primary", 1),
    ]


def test_before_send_runs_immediately_before_every_transport_send(tmp_path) -> None:
    _store_value, repository, authority = _authority(tmp_path)
    failure = ExactProviderRouteFailure(
        ExactProviderRouteFailureKind.TRANSIENT_RETRY_SAFE,
        RuntimeError("temporary"),
    )
    events = []

    def record_transport_send(_envelope, route, retry_ordinal):
        events.append(("send", route.name, retry_ordinal))

    transport = _Transport(
        [failure, _result("after retry")],
        before_send=record_transport_send,
    )

    outcome = _runtime(repository, transport).execute(
        authority=authority,
        retry_config=RetryConfig(
            max_retries=1,
            base_delay_ms=0,
            max_delay_ms=0,
            jitter_ratio=0,
        ),
        before_send=lambda context: events.append(
            ("guard", context.physical_ordinal)
        ),
    )

    assert outcome.result.final_text == "after retry"
    assert events == [
        ("guard", 0),
        ("send", "primary", 0),
        ("guard", 1),
        ("send", "primary", 1),
    ]


def test_before_send_failure_leaves_started_lease_without_sending(tmp_path) -> None:
    _store_value, repository, authority = _authority(tmp_path)
    transport = _Transport([_result()])

    def fail_guard(_context):
        raise RuntimeError("execution lease lost")

    with pytest.raises(DurableProviderTurnUncertainError):
        _runtime(repository, transport).execute(
            authority=authority,
            retry_config=RetryConfig(max_retries=0),
            before_send=fail_guard,
        )

    assert transport.calls == []
    subject = ProviderRequestSubject(
        ATTEMPT,
        ITERATION,
        authority.envelope.envelope_sha256,
        "primary",
        0,
    )
    assert repository.load(subject=subject).status is ProviderRequestStatus.STARTED


def test_unclassified_transport_failure_is_uncertain_and_never_resent(tmp_path) -> None:
    _store_value, repository, authority = _authority(tmp_path)
    transport = _Transport([RuntimeError("unknown network outcome")])

    with pytest.raises(DurableProviderTurnUncertainError):
        _runtime(repository, transport).execute(
            authority=authority,
            retry_config=RetryConfig(max_retries=3),
        )
    assert len(transport.calls) == 1

    no_send = _Transport([])
    with pytest.raises(DurableProviderTurnUncertainError):
        _runtime(repository, no_send).execute(
            authority=authority,
            retry_config=RetryConfig(max_retries=3),
        )
    assert no_send.calls == []


def test_openai_previous_response_fallback_is_a_separate_durable_send(tmp_path) -> None:
    _store_value, repository, authority = _authority(tmp_path, fallback=True)
    fallback = ExactProviderRouteFailure(
        ExactProviderRouteFailureKind.PREVIOUS_RESPONSE_FALLBACK,
        RuntimeError("previous response is unavailable"),
    )
    transport = _Transport([fallback, _result("local replay")])

    outcome = _runtime(repository, transport).execute(
        authority=authority,
        retry_config=RetryConfig(max_retries=0),
    )

    assert outcome.result.final_text == "local replay"
    assert [(call[1].name, call[2]) for call in transport.calls] == [
        ("primary", 0),
        ("openai_previous_response_fallback", 0),
    ]
    primary = repository.load(
        subject=ProviderRequestSubject(
            ATTEMPT,
            ITERATION,
            authority.envelope.envelope_sha256,
            "primary",
            0,
        )
    )
    fallback_lease = repository.load(
        subject=ProviderRequestSubject(
            ATTEMPT,
            ITERATION,
            authority.envelope.envelope_sha256,
            "openai_previous_response_fallback",
            0,
        )
    )
    assert primary.classification == "previous_response_fallback"
    assert fallback_lease.status is ProviderRequestStatus.COMPLETED
    no_send = _Transport([])
    replay = _runtime(repository, no_send).execute(
        authority=authority,
        retry_config=RetryConfig(max_retries=0),
    )
    assert replay.result == outcome.result
    assert replay.recovered is True
    assert no_send.calls == []


@pytest.mark.parametrize("recovered_primary_failure", (False, True))
def test_before_send_runs_for_fresh_and_recovered_openai_fallback(
    tmp_path,
    recovered_primary_failure,
) -> None:
    _store_value, repository, authority = _authority(tmp_path, fallback=True)
    fallback_failure = ExactProviderRouteFailure(
        ExactProviderRouteFailureKind.PREVIOUS_RESPONSE_FALLBACK,
        RuntimeError("previous response is unavailable"),
    )
    outcomes = [fallback_failure, _result("local replay")]
    if recovered_primary_failure:
        primary_route = authority.envelope.routes[0]
        coordinator = ProviderRequestLeaseCoordinator(repository)
        started = coordinator.claim_initial(
            attempt=ATTEMPT,
            iteration=ITERATION,
            envelope_sha256=authority.envelope.envelope_sha256,
            route=primary_route.name,
            route_sha256=primary_route.route_sha256,
            operation=_operation("fallback-primary-started", "5"),
        )
        coordinator.record_failure(
            started,
            classification="previous_response_fallback",
            retryable=True,
            visible_output=False,
            operation=_operation("fallback-primary-failed", "6"),
        )
        outcomes = [_result("local replay")]

    events = []

    def record_transport_send(_envelope, route, retry_ordinal):
        events.append(("send", route.name, retry_ordinal))

    transport = _Transport(outcomes, before_send=record_transport_send)
    outcome = _runtime(repository, transport).execute(
        authority=authority,
        retry_config=RetryConfig(max_retries=0),
        before_send=lambda context: events.append(
            ("guard", context.physical_ordinal)
        ),
    )

    assert outcome.result.final_text == "local replay"
    if recovered_primary_failure:
        assert events == [
            ("guard", 1),
            ("send", "openai_previous_response_fallback", 0),
        ]
    else:
        assert events == [
            ("guard", 0),
            ("send", "primary", 0),
            ("guard", 1),
            ("send", "openai_previous_response_fallback", 0),
        ]


def test_result_persistence_failure_is_durable_and_never_retried(tmp_path) -> None:
    _store_value, repository, authority = _authority(tmp_path)
    transport = _Transport([_result()])

    def fail_result_persistence(*, request):
        del request
        raise OSError("disk full")

    repository._persist_provider_turn_result_cas = fail_result_persistence
    with pytest.raises(BaseException) as caught:
        _runtime(repository, transport).execute(
            authority=authority,
            retry_config=RetryConfig(max_retries=3),
        )

    assert is_durable_persistence_failure(caught.value)
    assert len(transport.calls) == 1
    no_send = _Transport([])
    with pytest.raises(DurableProviderTurnUncertainError):
        _runtime(repository, no_send).execute(
            authority=authority,
            retry_config=RetryConfig(max_retries=3),
        )
    assert no_send.calls == []


def test_restart_finalizes_receipted_started_lease_without_resending(tmp_path) -> None:
    _store_value, repository, authority = _authority(tmp_path)
    transport = _Transport([_result("persisted before crash")])
    original_cas = repository.compare_and_swap

    def fail_completion(*, subject, expected_revision, replacement):
        if replacement.status is ProviderRequestStatus.COMPLETED:
            raise OSError("completion lease write failed")
        return original_cas(
            subject=subject,
            expected_revision=expected_revision,
            replacement=replacement,
        )

    repository.compare_and_swap = fail_completion
    with pytest.raises(BaseException) as caught:
        _runtime(repository, transport).execute(
            authority=authority,
            retry_config=RetryConfig(max_retries=0),
            build_run_receipt=_receipt_factory(),
        )
    assert is_durable_persistence_failure(caught.value)
    assert len(transport.calls) == 1

    durable_run_receipts = repository.load_receipts(
        root_run_id=ATTEMPT.attempt_id,
        owner_run_id=ATTEMPT.attempt_id,
        attempt_id=ATTEMPT.attempt_id,
    )
    assert len(durable_run_receipts) == 1
    assert durable_run_receipts[0].status == "completed"

    reopened = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    outcome = _runtime(reopened, _Transport([])).execute(
        authority=authority,
        retry_config=RetryConfig(max_retries=0),
    )

    assert outcome.result.final_text == "persisted before crash"
    assert outcome.recovered is True
    subject = ProviderRequestSubject(
        ATTEMPT,
        ITERATION,
        authority.envelope.envelope_sha256,
        "primary",
        0,
    )
    completed = reopened.load(subject=subject)
    receipt = recover_provider_turn_result(
        reopened,
        subject=subject,
        expected_route_sha256=completed.route_sha256,
    )
    assert completed.result_binding.result_sha256 == receipt.envelope.result_sha256


def test_exhausted_retry_is_durable_terminal_and_not_sent_after_restart(
    tmp_path,
) -> None:
    _store_value, repository, authority = _authority(tmp_path)
    failures = [
        ExactProviderRouteFailure(
            ExactProviderRouteFailureKind.TRANSIENT_RETRY_SAFE,
            RuntimeError(f"temporary-{index}"),
        )
        for index in range(2)
    ]
    transport = _Transport(failures)

    with pytest.raises(RetriesExhaustedError):
        _runtime(repository, transport).execute(
            authority=authority,
            retry_config=RetryConfig(
                max_retries=1,
                base_delay_ms=0,
                max_delay_ms=0,
                jitter_ratio=0,
            ),
        )
    assert len(transport.calls) == 2

    no_send = _Transport([])
    with pytest.raises(Exception, match="terminal|transient|failed"):
        _runtime(repository, no_send).execute(
            authority=authority,
            retry_config=RetryConfig(max_retries=2),
        )
    assert no_send.calls == []


def test_after_send_closes_retry_attempts_immediately(tmp_path) -> None:
    _store_value, repository, authority = _authority(tmp_path)
    retry_failure = ExactProviderRouteFailure(
        ExactProviderRouteFailureKind.TRANSIENT_RETRY_SAFE,
        RuntimeError("retry safe"),
    )
    events = []
    transport = _Transport(
        [retry_failure, _result("after retry")],
        before_send=lambda _envelope, route, ordinal: events.append(
            ("send", route.name, ordinal)
        ),
    )

    outcome = _runtime(repository, transport).execute(
        authority=authority,
        retry_config=RetryConfig(
            max_retries=1,
            base_delay_ms=0,
            max_delay_ms=0,
            jitter_ratio=0,
        ),
        after_send=lambda context, completed_at, status, classification: events.append(
            ("after", context, completed_at, status, classification)
        ),
    )

    assert outcome.result.final_text == "after retry"
    assert [event[0] for event in events] == [
        "send",
        "after",
        "send",
        "after",
    ]
    after = [event for event in events if event[0] == "after"]
    assert [(event[1].physical_ordinal, event[3], event[4]) for event in after] == [
        (0, "failed", "transient_retry_safe"),
        (1, "completed", "success"),
    ]
    assert all(event[2].endswith("Z") for event in after)


def test_after_send_closes_fallback_and_success_attempts(tmp_path) -> None:
    _store_value, repository, authority = _authority(tmp_path, fallback=True)
    fallback_failure = ExactProviderRouteFailure(
        ExactProviderRouteFailureKind.PREVIOUS_RESPONSE_FALLBACK,
        RuntimeError("fallback"),
    )
    events = []

    outcome = _runtime(
        repository,
        _Transport([fallback_failure, _result("local replay")]),
    ).execute(
        authority=authority,
        retry_config=RetryConfig(max_retries=0),
        after_send=lambda *event: events.append(event),
    )

    assert outcome.result.final_text == "local replay"
    assert [(event[0].physical_ordinal, event[2], event[3]) for event in events] == [
        (0, "failed", "previous_response_fallback"),
        (1, "completed", "success"),
    ]


def test_after_send_closes_uncertain_final_error_once(tmp_path) -> None:
    _store_value, repository, authority = _authority(tmp_path)
    events = []

    with pytest.raises(DurableProviderTurnUncertainError):
        _runtime(repository, _Transport([RuntimeError("unknown")])).execute(
            authority=authority,
            retry_config=RetryConfig(max_retries=3),
            after_send=lambda *event: events.append(event),
        )

    assert len(events) == 1
    assert events[0][0].physical_ordinal == 0
    assert events[0][2:] == ("uncertain", "uncertain")
