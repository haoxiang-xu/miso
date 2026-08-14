from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from unchain.journal import AttemptRef, GenerationRef
from unchain.kernel.run_ledger import build_model_attempt_receipt
from unchain.kernel.state import RunState
from unchain.persistence import SQLiteContextV2Store
from unchain.providers import OpenAIModelIO
from unchain.providers.base import ModelTurnRequest
from unchain.providers.durable_turn_runtime import (
    DurableProviderTurnError,
    DurableProviderTurnMode,
    DurableProviderTurnUncertainError,
    ExactProviderRouteFailure,
    ExactProviderRouteFailureKind,
    ExactProviderRouteTransport,
)
from unchain.retry import RetryConfig, RetriesExhaustedError
from unchain.run_bundle import RunIdentity
from unchain.providers.turn_ownership import ProviderTurnOwnership
from unchain.tools import Toolkit


ATTEMPT = AttemptRef(
    GenerationRef("execution-provider-service", "generation-provider-service"),
    "attempt-provider-service",
)
TARGET_SHA256 = "b" * 64


class _OpenAIStream:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        yield SimpleNamespace(type="response.output_text.delta", delta="durable ")
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id="response-provider-service",
                output=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "durable result"}],
                    }
                ],
                usage={"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
            ),
        )


def _model_io(send_calls: list[dict]):
    class _Responses:
        def create(self, **kwargs):
            send_calls.append(copy.deepcopy(kwargs))
            return _OpenAIStream()

    class _Client:
        responses = _Responses()

    return OpenAIModelIO(
        model="gpt-test",
        api_key="test-key",
        client_factory=lambda **_kwargs: _Client(),
        default_payloads={},
        model_capabilities={},
    )


def _request(events: list[dict] | None = None) -> ModelTurnRequest:
    return ModelTurnRequest(
        messages=[{"role": "user", "content": "execute exactly once"}],
        payload={},
        callback=(events.append if events is not None else None),
        run_id=ATTEMPT.attempt_id,
        iteration=2,
        toolkit=Toolkit(),
        emit_stream=True,
    )


def _repository(tmp_path):
    store = SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )
    return store.bind_execution(ATTEMPT.generation.execution_id)


def _service(tmp_path, mode):
    from unchain.context.provider_execution import (
        ContextProviderTurnExecutionService,
    )
    return ContextProviderTurnExecutionService(
        attempt=ATTEMPT,
        store=_repository(tmp_path),
        mode=mode,
        transport_target_sha256=TARGET_SHA256,
        sleep=lambda _seconds: None,
    )


def _run_receipt_factory():
    identity = RunIdentity(
        execution_id=ATTEMPT.generation.execution_id,
        attempt_id=ATTEMPT.attempt_id,
        root_run_id=ATTEMPT.attempt_id,
        run_id=ATTEMPT.attempt_id,
        parent_run_id=None,
        relation="root",
    )

    def build(attempt, started_at, completed_at, outcome, classification, result):
        return build_model_attempt_receipt(
            identity=identity,
            provider="openai",
            model="gpt-test",
            iteration=2,
            retry_ordinal=attempt,
            purpose="agent_turn",
            request_digest="e" * 64,
            route="openai.responses.create",
            started_at=started_at,
            completed_at=completed_at,
            turn=result,
            status=outcome,
            classification=classification,
        )

    return build


class _FailingTransport(ExactProviderRouteTransport):
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def send(self, *, envelope, route, retry_ordinal):
        del envelope, route, retry_ordinal
        self.calls += 1
        raise self.error

    def discard_buffered_events(self):
        return None

    def release_buffered_events(self):
        return None

def test_off_is_a_read_only_legacy_fallthrough_without_durable_authority(tmp_path):
    send_calls: list[dict] = []
    service = _service(tmp_path, DurableProviderTurnMode.OFF)

    result = service.fetch_prepared(
        model_io=_model_io(send_calls),
        request=_request(),
        retry_config=RetryConfig(max_retries=0),
    )

    assert result is None
    assert send_calls == []
    assert service.store.capture_snapshot().events == ()


def test_shadow_persists_exact_authority_but_never_sends(tmp_path):
    send_calls: list[dict] = []
    service = _service(tmp_path, DurableProviderTurnMode.SHADOW)

    result = service.fetch_prepared(
        model_io=_model_io(send_calls),
        request=_request(),
        retry_config=RetryConfig(max_retries=0),
    )

    assert result is None
    assert send_calls == []
    assert [event.event_type for event in service.store.capture_snapshot().events] == [
        "tool.catalog_snapshot",
        "provider.wire_snapshot",
    ]


def test_enforce_sends_once_then_releases_buffered_callbacks(tmp_path):
    send_calls: list[dict] = []
    events: list[dict] = []
    attempts: list[int] = []
    service = _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST)

    result = service.fetch_prepared(
        model_io=_model_io(send_calls),
        request=_request(events),
        retry_config=RetryConfig(max_retries=0),
        before_attempt=attempts.append,
    )

    assert result.final_text == "durable result"
    assert len(send_calls) == 1
    assert attempts == [0]
    assert [event["type"] for event in events] == [
        "request_messages",
        "token_delta",
    ]


def test_production_enforce_mode_uses_the_official_transport_identity(tmp_path):
    from unchain.context.provider_execution import (
        ContextProviderTurnExecutionService,
        official_provider_transport_target_sha256,
    )

    service = ContextProviderTurnExecutionService(
        attempt=ATTEMPT,
        store=_repository(tmp_path),
        mode=DurableProviderTurnMode.ENFORCE,
        transport_target_sha256=official_provider_transport_target_sha256(),
        sleep=lambda _seconds: None,
    )
    sends = []
    result = service.fetch_prepared(
        model_io=_model_io(sends),
        request=_request(),
        retry_config=RetryConfig(max_retries=0),
    )

    assert result.final_text == "durable result"
    assert len(sends) == 1


def test_restart_recovers_result_without_resend_or_callback_replay(tmp_path):
    first_sends: list[dict] = []
    first_events: list[dict] = []
    first = _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST)
    expected = first.fetch_prepared(
        model_io=_model_io(first_sends),
        request=_request(first_events),
        retry_config=RetryConfig(max_retries=0),
    )

    restart_sends: list[dict] = []
    restart_events: list[dict] = []
    restarted = _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST)
    recovered = restarted.fetch_prepared(
        model_io=_model_io(restart_sends),
        request=_request(restart_events),
        retry_config=RetryConfig(max_retries=0),
    )

    assert recovered.final_text == expected.final_text
    assert recovered.assistant_messages == expected.assistant_messages
    assert recovered.provider_call_usage is None
    assert len(first_sends) == 1
    assert restart_sends == []
    assert restart_events == []


def test_occurrence_namespace_preserves_owner_iteration_and_replays_distinct_calls(
    tmp_path,
):
    identity = RunIdentity(
        execution_id=ATTEMPT.generation.execution_id,
        attempt_id=ATTEMPT.attempt_id,
        root_run_id=ATTEMPT.attempt_id,
        run_id=ATTEMPT.attempt_id,
        parent_run_id=None,
        relation="root",
    )

    def bound_owner():
        service = _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST)
        owner = ProviderTurnOwnership(
            identity=identity,
            service=service,
            ledger=service.store,
        )
        state = RunState()
        state.session_state.session_id = identity.execution_id
        state.run_ledger.initialize(
            state=state,
            run_id=identity.run_id,
            explicit_identity=identity,
        )
        state.run_ledger.bind_provider_turn_ownership(owner)
        return owner, state

    first_owner, first_state = bound_owner()
    first_sends: list[dict] = []
    first_model = _model_io(first_sends)
    for occurrence in ("same-wire:first", "same-wire:second"):
        result = first_owner.fetch_turn(
            state=first_state,
            model_io=first_model,
            request=_request(),
            occurrence_id=occurrence,
            purpose="tool_observation",
            iteration=2,
            request_sha256="a" * 64,
            retry_config=RetryConfig(max_retries=0),
            provider="openai",
            model="gpt-test",
        )
        assert result.final_text == "durable result"

    assert len(first_sends) == 2
    assert len(first_state.run_ledger.receipts) == 2
    assert len(set(first_state.run_ledger.receipts)) == 2

    result_events = [
        event
        for event in first_owner.service.store.capture_snapshot().events
        if event.event_type == "provider.turn_result"
    ]
    assert len(result_events) == 2
    assert {event.attempt.attempt_id for event in result_events} == {
        ATTEMPT.attempt_id
    }
    assert {event.payload["iteration"] for event in result_events} == {2}
    assert len(
        {event.attempt.generation.generation_id for event in result_events}
    ) == 2

    cold_owner, cold_state = bound_owner()
    cold_sends: list[dict] = []
    cold_model = _model_io(cold_sends)
    for occurrence in ("same-wire:first", "same-wire:second"):
        recovered = cold_owner.fetch_turn(
            state=cold_state,
            model_io=cold_model,
            request=_request(),
            occurrence_id=occurrence,
            purpose="tool_observation",
            iteration=2,
            request_sha256="a" * 64,
            retry_config=RetryConfig(max_retries=0),
            provider="openai",
            model="gpt-test",
        )
        assert recovered.final_text == "durable result"

    assert cold_sends == []
    assert len(cold_state.run_ledger.receipts) == 2


@pytest.mark.parametrize(
    ("purpose", "occurrence_id"),
    (
        ("tool_exposure", "tool_exposure:root"),
        ("tool_observation", "tool_observation:batch-1"),
        ("web_extract", "web_extract:call-1"),
    ),
)
def test_auxiliary_send_crash_after_atomic_cas_cold_replays_without_resend(
    tmp_path,
    purpose,
    occurrence_id,
):
    identity = RunIdentity(
        execution_id=ATTEMPT.generation.execution_id,
        attempt_id=ATTEMPT.attempt_id,
        root_run_id=ATTEMPT.attempt_id,
        run_id=ATTEMPT.attempt_id,
        parent_run_id=None,
        relation="root",
    )

    def bound_owner():
        service = _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST)
        owner = ProviderTurnOwnership(
            identity=identity,
            service=service,
            ledger=service.store,
        )
        state = RunState()
        state.session_state.session_id = identity.execution_id
        state.run_ledger.initialize(
            state=state,
            run_id=identity.run_id,
            explicit_identity=identity,
        )
        state.run_ledger.bind_provider_turn_ownership(owner)
        return owner, state

    owner, state = bound_owner()
    sends: list[dict] = []
    original_append = state.run_ledger.append

    class _ProcessCrash(BaseException):
        pass

    def crash_after_durable_cas(receipt):
        original_append(receipt)
        raise _ProcessCrash()

    state.run_ledger.append = crash_after_durable_cas
    with pytest.raises(_ProcessCrash):
        owner.fetch_turn(
            state=state,
            model_io=_model_io(sends),
            request=_request(),
            occurrence_id=occurrence_id,
            purpose=purpose,
            iteration=2,
            request_sha256="c" * 64,
            retry_config=RetryConfig(max_retries=0),
            provider="openai",
            model="gpt-test",
        )
    assert len(sends) == 1

    cold_owner, cold_state = bound_owner()
    cold_sends: list[dict] = []
    recovered = cold_owner.fetch_turn(
        state=cold_state,
        model_io=_model_io(cold_sends),
        request=_request(),
        occurrence_id=occurrence_id,
        purpose=purpose,
        iteration=2,
        request_sha256="c" * 64,
        retry_config=RetryConfig(max_retries=0),
        provider="openai",
        model="gpt-test",
    )
    assert recovered.final_text == "durable result"
    assert cold_sends == []
    assert len(cold_state.run_ledger.receipts) == 1


def test_web_extract_uses_owned_atomic_send_and_cold_replays(
    tmp_path,
    monkeypatch,
):
    from unchain.agent.model_io import ModelIOFactoryRegistry
    from unchain.toolkits.builtin.core.web_fetch import run_extract_model

    identity = RunIdentity(
        execution_id=ATTEMPT.generation.execution_id,
        attempt_id=ATTEMPT.attempt_id,
        root_run_id=ATTEMPT.attempt_id,
        run_id=ATTEMPT.attempt_id,
        parent_run_id=None,
        relation="root",
    )

    def bound_state():
        service = _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST)
        owner = ProviderTurnOwnership(
            identity=identity,
            service=service,
            ledger=service.store,
        )
        state = RunState()
        state.session_state.session_id = identity.execution_id
        state.run_ledger.initialize(
            state=state,
            run_id=identity.run_id,
            explicit_identity=identity,
        )
        state.run_ledger.bind_provider_turn_ownership(owner)
        return state

    sends: list[dict] = []
    monkeypatch.setattr(
        ModelIOFactoryRegistry,
        "create",
        lambda _registry, **_kwargs: _model_io(sends),
    )
    state = bound_state()
    execution_context = SimpleNamespace(
        run_state=state,
        iteration=7,
        call_id="web-call-7",
    )
    first = run_extract_model(
        url="https://example.test/page",
        content="The durable fact is 42.",
        prompt="What is the durable fact?",
        extract_model_config={
            "provider": "openai",
            "model": "gpt-test",
            "payload": {},
        },
        execution_context=execution_context,
    )
    assert first == "durable result"
    assert len(sends) == 1
    receipt = next(iter(state.run_ledger.receipts.values()))
    assert receipt.identity.iteration == 7
    assert receipt.identity.purpose == "web_extract"

    cold_sends: list[dict] = []
    monkeypatch.setattr(
        ModelIOFactoryRegistry,
        "create",
        lambda _registry, **_kwargs: _model_io(cold_sends),
    )
    cold_state = bound_state()
    recovered = run_extract_model(
        url="https://example.test/page",
        content="The durable fact is 42.",
        prompt="What is the durable fact?",
        extract_model_config={
            "provider": "openai",
            "model": "gpt-test",
            "payload": {},
        },
        execution_context=SimpleNamespace(
            run_state=cold_state,
            iteration=7,
            call_id="web-call-7",
        ),
    )
    assert recovered == first
    assert cold_sends == []
    assert len(cold_state.run_ledger.receipts) == 1


def test_tool_observation_harness_uses_owned_send_and_cold_replays(tmp_path):
    from unchain.kernel.harness import HarnessContext
    from unchain.kernel.loop import KernelLoop
    from unchain.tools.execution import ToolExecutionHarness
    from unchain.tools.types import ToolBatchState

    identity = RunIdentity(
        execution_id=ATTEMPT.generation.execution_id,
        attempt_id=ATTEMPT.attempt_id,
        root_run_id=ATTEMPT.attempt_id,
        run_id=ATTEMPT.attempt_id,
        parent_run_id=None,
        relation="root",
    )

    def bound_context(send_calls):
        service = _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST)
        owner = ProviderTurnOwnership(
            identity=identity,
            service=service,
            ledger=service.store,
        )
        state = RunState()
        state.session_state.session_id = identity.execution_id
        state.provider_state.provider = "openai"
        state.provider_state.model = "gpt-test"
        state.iteration = 5
        state.seed_messages([{"role": "user", "content": "inspect result"}])
        state.tool_batch_state = ToolBatchState(
            result_messages=[
                {
                    "role": "user",
                    "content": '{"ok":true}',
                }
            ],
            should_observe=True,
            executed_call_ids=["tool-call-5"],
        )
        state.run_ledger.initialize(
            state=state,
            run_id=identity.run_id,
            explicit_identity=identity,
        )
        state.run_ledger.bind_provider_turn_ownership(owner)
        model_io = _model_io(send_calls)
        loop = KernelLoop(model_io=model_io)
        return state, HarnessContext(
            state=state,
            phase="after_tool_batch",
            event={
                "payload": {},
                "response_format": None,
                "run_id": identity.run_id,
                "loop": loop,
                "model_io": model_io,
                "callback": None,
                "max_iterations": 6,
                "tool_runtime_plugins": [],
            },
        )

    sends: list[dict] = []
    first_state, first_context = bound_context(sends)
    delta = ToolExecutionHarness().build_delta(first_context)
    assert delta is not None
    assert len(sends) == 1
    first_receipt = next(iter(first_state.run_ledger.receipts.values()))
    assert first_receipt.identity.iteration == 5
    assert first_receipt.identity.purpose == "tool_observation"

    cold_sends: list[dict] = []
    cold_state, cold_context = bound_context(cold_sends)
    cold_delta = ToolExecutionHarness().build_delta(cold_context)
    assert cold_delta is not None
    assert cold_sends == []
    assert tuple(cold_state.run_ledger.receipts) == (
        first_receipt.provider_call_id,
    )


def test_memory_off_agent_and_tool_selector_share_owner_and_cold_replay(
    tmp_path,
):
    from unchain.agent import Agent, ToolOptimizerModule, ToolsModule
    from unchain.providers.turn_ownership import ProviderTurnOwnershipFactory
    from unchain.tools import Tool, ToolOptimizerConfig

    identity = RunIdentity(
        execution_id=ATTEMPT.generation.execution_id,
        attempt_id=ATTEMPT.attempt_id,
        root_run_id=ATTEMPT.attempt_id,
        run_id=ATTEMPT.attempt_id,
        parent_run_id=None,
        relation="root",
    )

    class _Factory:
        def bind(self, *, identity: RunIdentity):
            assert identity == globals_identity
            service = _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST)
            return ProviderTurnOwnership(
                identity=identity,
                service=service,
                ledger=service.store,
                factory=self,
            )

    globals_identity = identity
    factory = _Factory()
    assert isinstance(factory, ProviderTurnOwnershipFactory)

    toolkit = Toolkit()
    for index in range(2):
        toolkit.register(
            Tool.from_callable(
                lambda value="", index=index: {"index": index, "value": value},
                name=f"owned_tool_{index}",
                description=f"Owned tool {index}",
                parameters=[],
            )
        )

    def run_once(send_calls):
        model_io = _model_io(send_calls)
        agent = Agent(
            name="owned-memory-off-agent",
            provider="openai",
            model="gpt-test",
            modules=(
                ToolsModule(tools=(toolkit,)),
                ToolOptimizerModule(
                    config=ToolOptimizerConfig(
                        max_direct_tools=1,
                        trigger_tool_count=1,
                    )
                ),
            ),
            model_io_factory=lambda _spec, _context: model_io,
        )
        return agent.run(
            "finish without tools",
            max_iterations=1,
            session_id=identity.execution_id,
            run_id=identity.run_id,
            _run_bundle_identity=identity,
            _provider_turn_ownership_factory=factory,
        )

    first_sends: list[dict] = []
    first = run_once(first_sends)
    assert first.status == "completed"
    assert len(first_sends) == 2
    assert first.run_bundle is not None
    assert {
        receipt["identity"]["purpose"]
        for receipt in first.run_bundle["provider_calls"]
    } == {"tool_exposure", "agent_turn"}

    cold_sends: list[dict] = []
    cold = run_once(cold_sends)
    assert cold.status == "completed"
    assert cold_sends == []
    assert cold.run_bundle == first.run_bundle


def test_guard_failure_preserves_uncertain_fence_without_network_or_callbacks(
    tmp_path,
):
    send_calls: list[dict] = []
    events: list[dict] = []
    service = _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST)

    def fail_guard(_attempt: int) -> None:
        raise RuntimeError("execution lease was lost")

    with pytest.raises(DurableProviderTurnUncertainError):
        service.fetch_prepared(
            model_io=_model_io(send_calls),
            request=_request(events),
            retry_config=RetryConfig(max_retries=0),
            before_attempt=fail_guard,
        )

    assert send_calls == []
    assert events == []
    with pytest.raises(DurableProviderTurnUncertainError):
        service.fetch_prepared(
            model_io=_model_io(send_calls),
            request=_request(events),
            retry_config=RetryConfig(max_retries=0),
        )
    assert send_calls == []


def test_closed_mode_rejects_prior_enforce_evidence_instead_of_falling_through(
    tmp_path,
):
    send_calls: list[dict] = []
    _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST).fetch_prepared(
        model_io=_model_io(send_calls),
        request=_request(),
        retry_config=RetryConfig(max_retries=0),
    )

    with pytest.raises(DurableProviderTurnError, match="durable evidence"):
        _service(tmp_path, DurableProviderTurnMode.OFF).fetch_prepared(
            model_io=_model_io([]),
            request=_request(),
            retry_config=RetryConfig(max_retries=0),
        )


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_error"),
    [
        (
            ExactProviderRouteFailure(
                ExactProviderRouteFailureKind.TRANSIENT_RETRY_SAFE,
                RuntimeError("temporary"),
            ),
            "failed",
            RetriesExhaustedError,
        ),
        (RuntimeError("connection state unknown"), "uncertain", DurableProviderTurnUncertainError),
    ],
)
def test_failed_and_uncertain_sends_persist_one_atomic_receipt(
    tmp_path,
    monkeypatch,
    error,
    expected_status,
    expected_error,
):
    service = _service(tmp_path, DurableProviderTurnMode.ENFORCE_TEST)
    transport = _FailingTransport(error)
    monkeypatch.setattr(
        "unchain.context.provider_execution._exact_transport",
        lambda **_kwargs: transport,
    )
    observed = []

    with pytest.raises(expected_error):
        service.fetch_prepared(
            model_io=_model_io([]),
            request=_request(),
            retry_config=RetryConfig(max_retries=0),
            run_receipt_factory=_run_receipt_factory(),
            run_receipt_observed=observed.append,
        )

    receipts = service.store.load_receipts(
        root_run_id=ATTEMPT.attempt_id,
        owner_run_id=ATTEMPT.attempt_id,
        attempt_id=ATTEMPT.attempt_id,
    )
    assert transport.calls == 1
    assert receipts == tuple(observed)
    assert len(receipts) == 1
    assert receipts[0].status == expected_status
    assert receipts[0].timing.started_at is not None
    assert receipts[0].timing.completed_at is not None
