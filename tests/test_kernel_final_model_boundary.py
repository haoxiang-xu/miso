from __future__ import annotations

import copy
import gc
import pickle
import weakref
from dataclasses import replace

import pytest

from unchain.kernel.delta import HarnessDelta
from unchain.kernel.harness import BaseRuntimeHarness
from unchain.kernel.loop import KernelLoop
from unchain.kernel.model_tool_boundary import (
    FinalModelToolBoundary,
    FinalModelToolBoundaryContext,
    FinalModelToolPreparation,
    _FinalModelToolBoundaryIssuer,
    _FinalModelToolBoundaryBindingRegistry,
    _IssuedBoundaryRecord,
    _issue_final_model_tool_boundary,
)
from unchain.kernel.types import ModelTurnResult, ToolCall
from unchain.tools import Toolkit


def _toolkit_with(name: str, value: str) -> tuple[Toolkit, object]:
    toolkit = Toolkit()
    registered = toolkit.register(lambda: value, name=name)
    return toolkit, registered


def _pass_through_boundary(
    *,
    model_toolkit: Toolkit | None = None,
    execution_toolkit: Toolkit | None = None,
    execution_binding: object | None = None,
    prepared_provider_turn: object | None = None,
) -> FinalModelToolBoundary:
    model_tools = model_toolkit if model_toolkit is not None else Toolkit()
    execution_tools = execution_toolkit if execution_toolkit is not None else Toolkit()
    binding = execution_binding if execution_binding is not None else object()

    def prepare(context):
        assert type(context) is FinalModelToolBoundaryContext
        return FinalModelToolPreparation(
            model_toolkit=model_tools,
            execution_toolkit=execution_tools,
            execution_binding=binding,
            prepared_provider_turn=prepared_provider_turn,
        )

    def validate(context, preparation, turn):
        del context, preparation
        return turn

    return _issue_final_model_tool_boundary(
        prepare=prepare,
        validate=validate,
    )


def test_final_model_boundary_is_exact_issuer_created_and_reserved():
    boundary = _pass_through_boundary()
    loop = KernelLoop(harnesses=[boundary])

    assert type(boundary) is FinalModelToolBoundary
    assert loop.final_model_tool_boundary is boundary
    assert boundary not in loop.harnesses

    with pytest.raises(ValueError, match="already registered"):
        loop.register_harness(_pass_through_boundary())


def test_final_model_boundary_rejects_constructor_subclass_duck_and_manual_probes():
    with pytest.raises(TypeError, match="issuer-created"):
        FinalModelToolBoundary()
    with pytest.raises(TypeError, match="issuer-created"):
        FinalModelToolBoundary(_authority=object())

    with pytest.raises(TypeError, match="cannot be subclassed"):

        class _SubclassProbe(FinalModelToolBoundary):
            pass

    class _DuckProbe:
        boundary_kind = FinalModelToolBoundary.boundary_kind
        name = "forged_final_model_boundary"
        phases = ()
        order = 0

        def applies(self, context):
            del context
            return False

        def build_delta(self, context):
            del context
            return None

        def apply(self, context):
            del context
            return None

    with pytest.raises(TypeError, match="forged"):
        KernelLoop(harnesses=[_DuckProbe()])

    manual = object.__new__(FinalModelToolBoundary)
    with pytest.raises(TypeError, match="invalid final model boundary"):
        KernelLoop(harnesses=[manual])


def test_final_model_boundary_rejects_copy_deepcopy_and_pickle():
    boundary = _pass_through_boundary()

    for probe in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError, match="cannot be copied or serialized"):
            probe(boundary)


def test_final_model_boundary_issuer_records_are_weakly_cleaned():
    boundary = _pass_through_boundary()
    reference = weakref.ref(boundary)

    del boundary
    gc.collect()

    assert reference() is None


def test_final_model_boundary_fetch_prepared_delegates_exact_issuer_values():
    from unchain.providers.base import ModelTurnRequest
    from unchain.retry import RetryConfig

    assert hasattr(FinalModelToolBoundary, "fetch_prepared")

    toolkit = Toolkit()
    preparation = FinalModelToolPreparation(
        model_toolkit=toolkit,
        execution_toolkit=Toolkit(),
        execution_binding=object(),
    )
    context = FinalModelToolBoundaryContext(
        messages=(),
        payload={},
        tool_runtime_config={},
        openai_text_format=None,
        provider="openai",
        model="gpt-test",
        session_id="session-1",
        memory_namespace="",
        execution_id="session-1",
        generation_id="generation-1",
        attempt_id="attempt-1",
        run_id="attempt-1",
        iteration=0,
        latest_version_id=None,
        toolkit_object_id=id(toolkit),
        toolkit_prompt_sections=(),
        tools=(),
        tool_runtime_plugin_identities=(),
        model_io_object_id=None,
    )
    request = ModelTurnRequest(
        messages=[{"role": "user", "content": "hello"}],
        toolkit=toolkit,
        run_id="attempt-1",
    )
    retry_config = RetryConfig(max_retries=0)
    before_attempt = lambda attempt: None
    durable_turn = _text_turn()
    observed = []

    def fetch_prepared(
        received_context,
        received_preparation,
        received_request,
        received_retry_config,
        received_before_attempt,
    ):
        observed.append(
            (
                received_context,
                received_preparation,
                received_request,
                received_retry_config,
                received_before_attempt,
            )
        )
        return durable_turn

    boundary = _issue_final_model_tool_boundary(
        prepare=lambda received_context: preparation,
        fetch_prepared=fetch_prepared,
        validate=lambda received_context, received_preparation, turn: turn,
    )

    fetched = boundary.fetch_prepared(
        context,
        preparation,
        request,
        retry_config=retry_config,
        before_attempt=before_attempt,
    )

    assert fetched is durable_turn
    assert observed == [
        (context, preparation, request, retry_config, before_attempt)
    ]


def test_final_model_boundary_rejects_falsey_non_callable_fetch_callback():
    with pytest.raises(TypeError, match="callbacks must be callable"):
        _issue_final_model_tool_boundary(
            prepare=lambda context: None,
            validate=lambda context, preparation, turn: turn,
            fetch_prepared=0,
        )


def test_final_model_boundary_callback_cycles_are_collected_and_registry_is_capped():
    def cyclic_reference():
        holder = {}

        def prepare(context):
            assert holder["boundary"] is not None
            return FinalModelToolPreparation(
                model_toolkit=Toolkit(),
                execution_toolkit=Toolkit(),
                execution_binding=object(),
            )

        boundary = _issue_final_model_tool_boundary(
            prepare=prepare,
            validate=lambda context, preparation, turn: turn,
        )
        holder["boundary"] = boundary
        return weakref.ref(boundary)

    reference = cyclic_reference()
    gc.collect()
    assert reference() is None

    issuer = _FinalModelToolBoundaryIssuer(max_records=2)
    issued = [
        issuer.issue(
            prepare=lambda context: FinalModelToolPreparation(
                model_toolkit=Toolkit(),
                execution_toolkit=Toolkit(),
                execution_binding=object(),
            ),
            validate=lambda context, preparation, turn: turn,
        )
        for _ in range(2)
    ]
    assert len(issued) == 2
    with pytest.raises(RuntimeError, match="capacity"):
        issuer.issue(
            prepare=lambda context: FinalModelToolPreparation(
                model_toolkit=Toolkit(),
                execution_toolkit=Toolkit(),
                execution_binding=object(),
            ),
            validate=lambda context, preparation, turn: turn,
        )


def test_final_model_boundary_rejects_exact_record_replacement_before_network():
    calls = []
    boundary = _pass_through_boundary()
    model_io = _QueueModelIO(_text_turn(), calls)
    loop = KernelLoop(model_io=model_io, harnesses=[boundary])
    forged_record = _IssuedBoundaryRecord(
        prepare=lambda context: FinalModelToolPreparation(
            model_toolkit=Toolkit(),
            execution_toolkit=Toolkit(),
            execution_binding=object(),
        ),
        validate=lambda context, preparation, turn: turn,
    )
    object.__setattr__(
        boundary,
        "_FinalModelToolBoundary__issued_record",
        forged_record,
    )
    state = loop.seed_state([{"role": "user", "content": "start"}])

    with pytest.raises(RuntimeError, match="lost authority"):
        loop.step_once(state, toolkit=Toolkit())

    assert model_io.requests == []


def test_loop_boundary_seal_cycles_are_collected_and_registry_is_capped():
    def cyclic_references():
        holder = {}

        def prepare(context):
            assert holder["loop"] is not None
            return FinalModelToolPreparation(
                model_toolkit=Toolkit(),
                execution_toolkit=Toolkit(),
                execution_binding=object(),
            )

        boundary = _issue_final_model_tool_boundary(
            prepare=prepare,
            validate=lambda context, preparation, turn: turn,
        )
        loop = KernelLoop(harnesses=[boundary])
        holder["loop"] = loop
        return weakref.ref(loop), weakref.ref(boundary)

    loop_ref, boundary_ref = cyclic_references()
    gc.collect()
    assert loop_ref() is None
    assert boundary_ref() is None

    registry = _FinalModelToolBoundaryBindingRegistry(max_records=2)
    loops = [KernelLoop(), KernelLoop()]
    boundaries = [_pass_through_boundary(), _pass_through_boundary()]
    for loop, boundary in zip(loops, boundaries, strict=True):
        registry.bind(loop, boundary)
    with pytest.raises(RuntimeError, match="capacity"):
        registry.bind(KernelLoop(), _pass_through_boundary())


@pytest.mark.parametrize("replacement", [None, object()])
def test_exact_loop_boundary_seal_tamper_fails_before_network(replacement):
    calls = []
    model_io = _QueueModelIO(_text_turn(), calls)
    loop = KernelLoop(
        model_io=model_io,
        harnesses=[_pass_through_boundary()],
    )
    object.__setattr__(
        loop,
        "_KernelLoop__final_model_tool_boundary_seal",
        replacement,
    )
    state = loop.seed_state([{"role": "user", "content": "start"}])

    with pytest.raises(RuntimeError, match="boundary seal"):
        loop.step_once(state, toolkit=Toolkit())

    assert model_io.requests == []


def test_loop_boundary_seal_tamper_gc_cannot_rebind_authority():
    loop = KernelLoop(harnesses=[_pass_through_boundary()])
    object.__setattr__(
        loop,
        "_KernelLoop__final_model_tool_boundary_seal",
        None,
    )
    gc.collect()

    with pytest.raises(ValueError, match="already registered"):
        loop.register_harness(_pass_through_boundary())

    with pytest.raises(RuntimeError, match="boundary seal"):
        _ = loop.final_model_tool_boundary


def test_loop_boundary_map_set_failure_leaves_no_seal_and_can_retry():
    class _StoreThenFailOnceMap(dict):
        def __init__(self):
            super().__init__()
            self._fail_next_set = True

        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            if self._fail_next_set:
                self._fail_next_set = False
                raise RuntimeError("injected map set failure")

    registry = _FinalModelToolBoundaryBindingRegistry()
    registry._records = _StoreThenFailOnceMap()
    loop = KernelLoop()
    boundary = _pass_through_boundary()

    with pytest.raises(RuntimeError, match="injected map set failure"):
        registry.bind(loop, boundary)

    with pytest.raises(AttributeError):
        object.__getattribute__(loop, registry._STORAGE_ATTRIBUTE)
    assert registry.resolve(loop) is None

    registry.bind(loop, boundary)
    assert registry.resolve(loop) is boundary


def test_loop_boundary_seal_set_failure_rolls_back_and_can_retry():
    class _FailOnceSealDescriptor:
        def __init__(self):
            self._fail_next_set = True
            self._values = weakref.WeakKeyDictionary()

        def __get__(self, instance, owner):
            del owner
            if instance is None:
                return self
            try:
                return self._values[instance]
            except KeyError as exc:
                raise AttributeError("seal is not set") from exc

        def __set__(self, instance, value):
            if self._fail_next_set:
                self._fail_next_set = False
                raise RuntimeError("injected seal set failure")
            self._values[instance] = value

    seal_descriptor = _FailOnceSealDescriptor()

    class _FaultingLoop:
        __slots__ = ("__weakref__",)

        _KernelLoop__final_model_tool_boundary_seal = seal_descriptor

    registry = _FinalModelToolBoundaryBindingRegistry()
    loop = _FaultingLoop()
    boundary = _pass_through_boundary()

    with pytest.raises(RuntimeError, match="injected seal set failure"):
        registry.bind(loop, boundary)

    with pytest.raises(AttributeError):
        object.__getattribute__(loop, registry._STORAGE_ATTRIBUTE)
    assert registry.resolve(loop) is None

    registry.bind(loop, boundary)
    assert registry.resolve(loop) is boundary


def test_loop_boundary_seal_failure_does_not_remove_replaced_authority():
    class _ReplacementAuthority:
        def __init__(self, *, loop, seal):
            self.loop_ref = weakref.ref(loop)
            self.seal_ref = weakref.ref(seal)
            self.boundary_ref = weakref.ref(seal.boundary)

    registry = _FinalModelToolBoundaryBindingRegistry()

    class _ReplaceAuthorityThenFailDescriptor:
        def __init__(self):
            self.seal = None
            self.replacement = None

        def __get__(self, instance, owner):
            del instance, owner
            raise AttributeError("seal is not set")

        def __set__(self, instance, seal):
            self.seal = seal
            self.replacement = _ReplacementAuthority(loop=instance, seal=seal)
            registry._records[id(instance)] = self.replacement
            raise RuntimeError("injected seal replacement failure")

    seal_descriptor = _ReplaceAuthorityThenFailDescriptor()

    class _FaultingLoop:
        __slots__ = ("__weakref__",)

        _KernelLoop__final_model_tool_boundary_seal = seal_descriptor

    loop = _FaultingLoop()

    with pytest.raises(RuntimeError, match="injected seal replacement failure"):
        registry.bind(loop, _pass_through_boundary())

    assert registry._records[id(loop)] is seal_descriptor.replacement
    with pytest.raises(RuntimeError, match="boundary seal"):
        registry.resolve(loop)
    with pytest.raises(ValueError, match="already registered"):
        registry.bind(loop, _pass_through_boundary())


class _QueueModelIO:
    def __init__(self, result: ModelTurnResult, calls: list[str]) -> None:
        self.result = result
        self.calls = calls
        self.requests = []

    def fetch_turn(self, request):
        self.calls.append("model")
        self.requests.append(request)
        return self.result


class _MultiTurnModelIO:
    def __init__(self, results: list[ModelTurnResult], calls: list[str]) -> None:
        self.results = list(results)
        self.calls = calls
        self.requests = []

    def fetch_turn(self, request):
        self.calls.append("model")
        self.requests.append(request)
        return self.results.pop(0)


class _BeforeMutationHarness(BaseRuntimeHarness):
    def __init__(
        self,
        *,
        name: str,
        order: int,
        message: str,
        tool_name: str,
        calls: list[str],
    ) -> None:
        super().__init__(name=name, phases=("before_model",), order=order)
        self._message = message
        self._tool_name = tool_name
        self._calls = calls

    def build_delta(self, context):
        self._calls.append(self.name)
        context.event["payload"].setdefault("harnesses", []).append(self.name)
        context.event["toolkit"].register(lambda: None, name=self._tool_name)
        return HarnessDelta.append(
            created_by=self.name,
            messages=[{"role": "system", "content": self._message}],
        )


class _BoundarySlotTamperHarness(BaseRuntimeHarness):
    def __init__(self, *, replacement, phases=("before_model",)) -> None:
        super().__init__(name="boundary_slot_tamper", phases=phases)
        self._replacement = replacement

    def build_delta(self, context):
        context.event["loop"]._final_model_tool_boundary = self._replacement
        return None


class _PhaseRecorder(BaseRuntimeHarness):
    def __init__(
        self,
        *,
        phase: str,
        calls: list[str],
        observations: list[tuple[str, object, object, object]] | None = None,
    ) -> None:
        super().__init__(name=f"record_{phase}", phases=(phase,), order=10)
        self._calls = calls
        self._observations = observations

    def build_delta(self, context):
        self._calls.append(self.name)
        if self._observations is not None:
            toolkit = context.event["toolkit"]
            tool_obj = toolkit.get("demo")
            self._observations.append(
                (
                    context.phase,
                    toolkit,
                    context.event.get("tool_execution_binding"),
                    tool_obj.func() if tool_obj is not None else None,
                )
            )
        return None


def _text_turn(*, tool_calls=None) -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": "done"}],
        tool_calls=list(tool_calls or []),
        final_text="done",
        consumed_tokens=7,
        input_tokens=4,
        output_tokens=3,
    )


def test_final_boundary_uses_post_harness_snapshot_and_binds_execution_toolkit():
    calls = []
    observations = []
    runtime_toolkit, runtime_tool = _toolkit_with("demo", "runtime-handler")
    model_toolkit, model_tool = _toolkit_with("demo", "model-schema-handler")
    execution_toolkit, execution_tool = _toolkit_with(
        "demo",
        "sealed-execution-handler",
    )
    execution_binding = object()
    observed = {}

    def prepare(context):
        calls.append("prepare")
        observed["prepare"] = context
        assert [tool.name for tool in context.tools] == [
            "demo",
            "early_tool",
            "late_tool",
        ]
        assert context.tools[0].tool is runtime_tool
        return FinalModelToolPreparation(
            model_toolkit=model_toolkit,
            execution_toolkit=execution_toolkit,
            execution_binding=execution_binding,
        )

    def validate(context, preparation, turn):
        calls.append("validate")
        observed["validate"] = context
        assert context.tools[0].tool is execution_tool
        assert preparation.execution_binding is execution_binding
        return turn

    boundary = _issue_final_model_tool_boundary(
        prepare=prepare,
        validate=validate,
    )
    tool_call = ToolCall(call_id="call_1", name="demo", arguments={})
    model_io = _QueueModelIO(_text_turn(tool_calls=[tool_call]), calls)
    loop = KernelLoop(
        model_io=model_io,
        harnesses=[
            _BeforeMutationHarness(
                name="late",
                order=1_000_000,
                message="late message",
                tool_name="late_tool",
                calls=calls,
            ),
            boundary,
            _BeforeMutationHarness(
                name="early",
                order=-1_000_000,
                message="early message",
                tool_name="early_tool",
                calls=calls,
            ),
            _PhaseRecorder(
                phase="after_model",
                calls=calls,
                observations=observations,
            ),
            _PhaseRecorder(
                phase="on_tool_call",
                calls=calls,
                observations=observations,
            ),
            _PhaseRecorder(
                phase="after_tool_batch",
                calls=calls,
                observations=observations,
            ),
        ],
    )
    state = loop.seed_state(
        [{"role": "user", "content": "start"}],
        provider="openai",
        model="gpt-4.1",
        session_id="session-1",
    )
    state.metadata["generation_id"] = "generation-1"

    turn = loop.step_once(
        state,
        payload={"harnesses": []},
        toolkit=runtime_toolkit,
        run_id="attempt-1",
    )

    assert turn.final_text == "done"
    assert calls == [
        "early",
        "late",
        "prepare",
        "model",
        "validate",
        "record_after_model",
        "record_on_tool_call",
        "record_after_tool_batch",
    ]
    prepare_context = observed["prepare"]
    assert prepare_context.messages == (
        {"role": "user", "content": "start"},
        {"role": "system", "content": "early message"},
        {"role": "system", "content": "late message"},
    )
    assert prepare_context.payload["harnesses"] == ("early", "late")
    assert prepare_context.provider == "openai"
    assert prepare_context.model == "gpt-4.1"
    assert prepare_context.session_id == "session-1"
    assert prepare_context.attempt_id == "attempt-1"
    assert prepare_context.generation_id == "generation-1"
    assert prepare_context.iteration == 0
    assert prepare_context.toolkit_object_id == id(runtime_toolkit)
    assert prepare_context.tools[0].tool_object_id == id(runtime_tool)
    assert prepare_context.tools[0].handler_object_id == id(runtime_tool.func)

    validate_context = observed["validate"]
    assert validate_context.toolkit_object_id == id(execution_toolkit)
    assert model_io.requests[0].toolkit is model_toolkit
    assert model_io.requests[0].toolkit.get("demo") is model_tool
    assert observations == [
        (
            "after_model",
            execution_toolkit,
            execution_binding,
            "sealed-execution-handler",
        ),
        (
            "on_tool_call",
            execution_toolkit,
            execution_binding,
            "sealed-execution-handler",
        ),
        (
            "after_tool_batch",
            execution_toolkit,
            execution_binding,
            "sealed-execution-handler",
        ),
    ]


def test_final_boundary_none_fetch_uses_the_same_request_for_legacy_once():
    from unchain.providers.base import ModelTurnRequest
    from unchain.retry import RetryConfig

    calls = []
    observed = {}
    model_toolkit = Toolkit()

    def fetch_prepared(
        context,
        preparation,
        request,
        retry_config,
        before_attempt,
    ):
        calls.append("fetch_prepared")
        observed["context"] = context
        observed["preparation"] = preparation
        observed["request"] = request
        observed["retry_config"] = retry_config
        observed["before_attempt"] = before_attempt
        return None

    boundary = _issue_final_model_tool_boundary(
        prepare=lambda context: FinalModelToolPreparation(
            model_toolkit=model_toolkit,
            execution_toolkit=Toolkit(),
            execution_binding=object(),
        ),
        fetch_prepared=fetch_prepared,
        validate=lambda context, preparation, turn: turn,
    )
    model_io = _QueueModelIO(_text_turn(), calls)
    loop = KernelLoop(model_io=model_io, harnesses=[boundary])
    state = loop.seed_state([{"role": "user", "content": "start"}])

    turn = loop.step_once(
        state,
        payload={"temperature": 0.2},
        toolkit=Toolkit(),
        run_id="attempt-legacy",
    )

    assert turn.final_text == "done"
    assert calls == ["fetch_prepared", "model"]
    assert type(observed["request"]) is ModelTurnRequest
    assert model_io.requests == [observed["request"]]
    assert model_io.requests[0] is observed["request"]
    assert observed["request"].toolkit is model_toolkit
    assert observed["request"].payload == {"temperature": 0.2}
    assert type(observed["retry_config"]) is RetryConfig
    assert callable(observed["before_attempt"])
    assert callable(getattr(observed["before_attempt"], "after_attempt", None))


def test_final_boundary_durable_fetch_bypasses_legacy_model_and_retry():
    from unchain.providers.base import ModelTurnRequest
    from unchain.retry import RetryConfig

    calls = []
    durable_turn = ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": "durable"}],
        tool_calls=[],
        final_text="durable",
    )
    observed = {}

    def fetch_prepared(
        context,
        preparation,
        request,
        retry_config,
        before_attempt,
    ):
        calls.append("fetch_prepared")
        observed["request"] = request
        observed["retry_config"] = retry_config
        observed["before_attempt"] = before_attempt
        return durable_turn

    def validate(context, preparation, turn):
        calls.append("validate")
        assert turn is durable_turn
        return turn

    boundary = _issue_final_model_tool_boundary(
        prepare=lambda context: FinalModelToolPreparation(
            model_toolkit=Toolkit(),
            execution_toolkit=Toolkit(),
            execution_binding=object(),
        ),
        fetch_prepared=fetch_prepared,
        validate=validate,
    )
    model_io = _QueueModelIO(_text_turn(), calls)
    retry_config = RetryConfig(max_retries=4, base_delay_ms=0, jitter_ratio=0)
    loop = KernelLoop(
        model_io=model_io,
        retry_config=retry_config,
        harnesses=[boundary],
    )
    state = loop.seed_state([{"role": "user", "content": "start"}])

    turn = loop.step_once(state, toolkit=Toolkit(), run_id="attempt-durable")

    assert turn is durable_turn
    assert state.last_model_turn == durable_turn
    assert calls == ["fetch_prepared", "validate"]
    assert model_io.requests == []
    assert type(observed["request"]) is ModelTurnRequest
    assert observed["retry_config"] is retry_config
    assert callable(observed["before_attempt"])
    assert callable(getattr(observed["before_attempt"], "after_attempt", None))


def test_final_boundary_uses_a_cold_physical_ordinal_for_kernel_telemetry():
    calls = []
    completed_at = "2026-08-15T00:00:01Z"

    def fetch_prepared(
        _context,
        _preparation,
        _request,
        _retry_config,
        before_attempt,
    ):
        before_attempt(1)
        turn = _text_turn()
        receipt = before_attempt.run_receipt_factory(
            1,
            "2026-08-15T00:00:00Z",
            completed_at,
            "completed",
            "success",
            turn,
        )
        before_attempt.after_attempt(1, completed_at, "completed", "success")
        return replace(turn, provider_call_receipt=receipt)

    boundary = _issue_final_model_tool_boundary(
        prepare=lambda _context: FinalModelToolPreparation(
            model_toolkit=Toolkit(),
            execution_toolkit=Toolkit(),
            execution_binding=object(),
        ),
        fetch_prepared=fetch_prepared,
        validate=lambda _context, _preparation, turn: turn,
    )
    model_io = _QueueModelIO(_text_turn(), calls)
    loop = KernelLoop(model_io=model_io, harnesses=[boundary])
    state = loop.seed_state([{"role": "user", "content": "start"}])

    turn = loop.step_once(
        state,
        toolkit=Toolkit(),
        run_id="attempt-cold-physical",
    )

    assert turn.final_text == "done"
    assert model_io.requests == []
    [receipt] = state.run_ledger.receipts.values()
    assert receipt.identity.retry_ordinal == 1


def test_final_boundary_rejects_invalid_prepared_fetch_before_validation():
    calls = []

    def validate(context, preparation, turn):
        calls.append("validate")
        return _text_turn()

    boundary = _issue_final_model_tool_boundary(
        prepare=lambda context: FinalModelToolPreparation(
            model_toolkit=Toolkit(),
            execution_toolkit=Toolkit(),
            execution_binding=object(),
        ),
        fetch_prepared=lambda *args: object(),
        validate=validate,
    )
    model_io = _QueueModelIO(_text_turn(), calls)
    loop = KernelLoop(model_io=model_io, harnesses=[boundary])
    state = loop.seed_state([{"role": "user", "content": "start"}])

    with pytest.raises(TypeError, match="prepared fetch"):
        loop.step_once(state, toolkit=Toolkit())

    assert calls == []
    assert model_io.requests == []
    assert state.last_model_turn is None


def test_boundary_context_is_detached_and_read_only_on_prepare_failure():
    calls = []
    runtime_toolkit, runtime_tool = _toolkit_with("demo", "runtime-handler")
    original_payload = {"nested": {"value": "original"}}

    def prepare(context):
        calls.append("prepare")
        assert context.tools[0].tool is runtime_tool
        assert not hasattr(context, "state")
        assert not hasattr(context, "event")
        with pytest.raises(TypeError):
            context.messages[0]["content"] = "corrupt"
        with pytest.raises(TypeError):
            context.payload["nested"]["value"] = "corrupt"
        raise RuntimeError("prepare failed")

    boundary = _issue_final_model_tool_boundary(
        prepare=prepare,
        validate=lambda context, preparation, turn: turn,
    )
    model_io = _QueueModelIO(_text_turn(), calls)
    loop = KernelLoop(model_io=model_io, harnesses=[boundary])
    state = loop.seed_state([{"role": "user", "content": "start"}])

    with pytest.raises(RuntimeError, match="prepare failed"):
        loop.step_once(
            state,
            payload=original_payload,
            toolkit=runtime_toolkit,
        )

    assert calls == ["prepare"]
    assert model_io.requests == []
    assert original_payload == {"nested": {"value": "original"}}
    assert state.transcript == [{"role": "user", "content": "start"}]
    assert state.latest_messages() == [{"role": "user", "content": "start"}]
    assert state.last_model_turn is None
    assert state.iteration == 0


@pytest.mark.parametrize("replacement_kind", ["none", "authentic"])
def test_before_model_cannot_disable_or_replace_registered_boundary(
    replacement_kind,
):
    calls = []

    def prepare(context):
        calls.append("prepare")
        return FinalModelToolPreparation(
            model_toolkit=Toolkit(),
            execution_toolkit=Toolkit(),
            execution_binding=object(),
        )

    def validate(context, preparation, turn):
        calls.append("validate")
        return turn

    boundary = _issue_final_model_tool_boundary(prepare=prepare, validate=validate)
    replacement = None if replacement_kind == "none" else _pass_through_boundary()
    model_io = _QueueModelIO(_text_turn(), calls)
    loop = KernelLoop(
        model_io=model_io,
        harnesses=[boundary, _BoundarySlotTamperHarness(replacement=replacement)],
    )
    state = loop.seed_state([{"role": "user", "content": "start"}])

    loop.step_once(state, toolkit=Toolkit())

    assert calls == ["prepare", "model", "validate"]
    assert len(model_io.requests) == 1
    assert loop.final_model_tool_boundary is boundary


def test_after_model_and_on_tool_call_slot_tamper_cannot_disable_next_turn():
    calls = []
    prepare_count = 0
    validate_count = 0

    def prepare(context):
        nonlocal prepare_count
        prepare_count += 1
        return FinalModelToolPreparation(
            model_toolkit=Toolkit(),
            execution_toolkit=Toolkit(),
            execution_binding=object(),
        )

    def validate(context, preparation, turn):
        nonlocal validate_count
        validate_count += 1
        return turn

    boundary = _issue_final_model_tool_boundary(prepare=prepare, validate=validate)
    model_io = _MultiTurnModelIO(
        [
            _text_turn(tool_calls=[ToolCall("call-1", "demo", {})]),
            _text_turn(),
        ],
        calls,
    )
    loop = KernelLoop(
        model_io=model_io,
        harnesses=[
            boundary,
            _BoundarySlotTamperHarness(
                replacement=None,
                phases=("after_model", "on_tool_call"),
            ),
        ],
    )
    state = loop.seed_state([{"role": "user", "content": "start"}])

    loop.step_once(state, toolkit=Toolkit())
    loop.step_once(state, toolkit=Toolkit())

    assert prepare_count == 2
    assert validate_count == 2
    assert len(model_io.requests) == 2
    assert loop.final_model_tool_boundary is boundary


def test_boundary_snapshot_rejects_unknown_payload_and_non_tool_entries():
    class _UnknownMutable:
        pass

    calls = []
    model_io = _QueueModelIO(_text_turn(), calls)
    boundary = _pass_through_boundary()
    loop = KernelLoop(model_io=model_io, harnesses=[boundary])
    state = loop.seed_state([{"role": "user", "content": "start"}])

    with pytest.raises(TypeError, match="unsupported value"):
        loop.step_once(
            state,
            payload={"custom": _UnknownMutable()},
            toolkit=Toolkit(),
        )

    invalid_toolkit = Toolkit()
    invalid_toolkit.tools["forged"] = object()
    state = loop.seed_state([{"role": "user", "content": "start"}])
    with pytest.raises(TypeError, match="exact Tool"):
        loop.step_once(state, toolkit=invalid_toolkit)

    invalid_prompt_toolkit = Toolkit()
    invalid_prompt_toolkit.prompt_sections = (object(),)
    state = loop.seed_state([{"role": "user", "content": "start"}])
    with pytest.raises(TypeError, match="prompt_sections"):
        loop.step_once(state, toolkit=invalid_prompt_toolkit)
    assert model_io.requests == []


def test_non_none_prepared_provider_turn_cannot_escape_through_subclass_hook():
    class _PreparedHookProbeLoop(KernelLoop):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.prepared_hook_called = False

        def fetch_prepared_model_turn(self, *args, **kwargs):
            del args, kwargs
            self.prepared_hook_called = True
            return _text_turn()

    calls = []
    model_io = _QueueModelIO(_text_turn(), calls)
    boundary = _pass_through_boundary(prepared_provider_turn=object())
    loop = _PreparedHookProbeLoop(model_io=model_io, harnesses=[boundary])
    state = loop.seed_state([{"role": "user", "content": "start"}])

    with pytest.raises(RuntimeError, match="authenticated provider consumer"):
        loop.step_once(state, toolkit=Toolkit())

    assert loop.prepared_hook_called is False
    assert model_io.requests == []
    assert state.last_model_turn is None


def test_validate_failure_stops_before_state_application_and_later_phases():
    calls = []
    execution_toolkit = Toolkit()

    def validate(context, preparation, turn):
        del context, preparation, turn
        calls.append("validate")
        raise RuntimeError("validate failed")

    boundary = _issue_final_model_tool_boundary(
        prepare=lambda context: FinalModelToolPreparation(
            model_toolkit=Toolkit(),
            execution_toolkit=execution_toolkit,
            execution_binding=object(),
        ),
        validate=validate,
    )
    tool_call = ToolCall(call_id="call_1", name="demo", arguments={})
    model_io = _QueueModelIO(_text_turn(tool_calls=[tool_call]), calls)
    loop = KernelLoop(
        model_io=model_io,
        harnesses=[
            boundary,
            _PhaseRecorder(phase="after_model", calls=calls),
            _PhaseRecorder(phase="on_tool_call", calls=calls),
            _PhaseRecorder(phase="after_tool_batch", calls=calls),
        ],
    )
    state = loop.seed_state([{"role": "user", "content": "start"}])

    with pytest.raises(RuntimeError, match="validate failed"):
        loop.step_once(state, toolkit=Toolkit())

    assert calls == ["model", "validate"]
    assert state.transcript == [{"role": "user", "content": "start"}]
    assert state.last_model_turn is None
    assert state.pending_tool_calls == []
    assert state.iteration == 0
    assert state.token_state.consumed_tokens == 0


def test_boundary_requires_exact_preparation_and_model_turn_result():
    calls = []
    model_io = _QueueModelIO(_text_turn(), calls)
    invalid_preparation = _issue_final_model_tool_boundary(
        prepare=lambda context: {"model_toolkit": Toolkit()},
        validate=lambda context, preparation, turn: turn,
    )
    loop = KernelLoop(model_io=model_io, harnesses=[invalid_preparation])
    state = loop.seed_state([{"role": "user", "content": "start"}])

    with pytest.raises(TypeError, match="exact FinalModelToolPreparation"):
        loop.step_once(state, toolkit=Toolkit())
    assert model_io.requests == []

    calls.clear()
    invalid_turn = _issue_final_model_tool_boundary(
        prepare=lambda context: FinalModelToolPreparation(
            model_toolkit=Toolkit(),
            execution_toolkit=Toolkit(),
            execution_binding=object(),
        ),
        validate=lambda context, preparation, turn: object(),
    )
    loop = KernelLoop(model_io=model_io, harnesses=[invalid_turn])
    state = loop.seed_state([{"role": "user", "content": "start"}])

    with pytest.raises(TypeError, match="exact ModelTurnResult"):
        loop.step_once(state, toolkit=Toolkit())
    assert calls == ["model"]
    assert state.last_model_turn is None


def test_legacy_step_without_boundary_preserves_original_toolkit_and_phases():
    calls = []
    observations = []
    toolkit, _tool = _toolkit_with("demo", "legacy-handler")
    model_io = _QueueModelIO(_text_turn(), calls)
    loop = KernelLoop(
        model_io=model_io,
        harnesses=[
            _BeforeMutationHarness(
                name="before",
                order=1,
                message="legacy",
                tool_name="extra",
                calls=calls,
            ),
            _PhaseRecorder(
                phase="after_model",
                calls=calls,
                observations=observations,
            ),
            _PhaseRecorder(phase="before_commit", calls=calls),
        ],
    )
    state = loop.seed_state([{"role": "user", "content": "start"}])

    loop.step_once(state, toolkit=toolkit)

    assert calls == ["before", "model", "record_after_model", "record_before_commit"]
    assert model_io.requests[0].toolkit is toolkit
    assert observations == [("after_model", toolkit, None, "legacy-handler")]
