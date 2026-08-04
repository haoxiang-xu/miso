from __future__ import annotations

import json
from pathlib import Path

import pytest

from unchain.context import (
    ArtifactService,
    ContextCompileCoordinator,
    ContextInputIngress,
    ContextRuntime,
    DurableContextRuntimeFactory,
    HostResolvedCurrentInput,
    JournalContextRequestFactory,
)
from unchain.agent.modules import ContextModule
from unchain.context.projector import (
    CanonicalSemanticEventProjector,
    SemanticEventProjectionError,
)
from unchain.journal import ArtifactRef, AttemptRef, DurableEventSink, GenerationRef
from unchain.kernel.harness import HarnessContext
from unchain.kernel.state import RunState
from unchain.persistence.sqlite_context_compiler_v2 import (
    SQLiteContextCompilerV2Store,
)
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store


def _shadow_projection_api():
    from unchain.context import projector as projector_module

    assert hasattr(projector_module, "SemanticEventProjectionMode")
    assert hasattr(projector_module, "ShadowObservedToolEventAdapter")
    return (
        projector_module.SemanticEventProjectionMode,
        projector_module.ShadowObservedToolEventAdapter,
    )


def _attempt() -> AttemptRef:
    return AttemptRef(
        generation=GenerationRef("execution-shadow", "generation-shadow"),
        attempt_id="attempt-shadow",
    )


def _store(root: Path):
    store = SQLiteContextV2Store(
        database_path=root / "context_v2.sqlite3",
        object_directory=root / "objects",
    )
    journal = store.bind_execution("execution-shadow")
    artifacts = ArtifactService(
        journal,
        sanitizer=lambda content, media_type: content,
    )
    return store, journal, artifacts


def _shadow_projector(root: Path):
    mode_type, adapter_type = _shadow_projection_api()
    _store_value, journal, artifacts = _store(root)
    adapter = adapter_type(
        attempt=_attempt(),
        journal=journal,
        artifacts=artifacts,
        payload_sanitizer=lambda event_type, payload: payload,
    )
    projector = CanonicalSemanticEventProjector(
        attempt=_attempt(),
        artifacts=artifacts,
        payload_sanitizer=lambda event_type, payload: payload,
        observed_tool_adapter=adapter,
    )
    assert projector.projection_mode is mode_type.SHADOW_OBSERVED
    return journal, artifacts, projector, DurableEventSink(
        journal,
        _attempt(),
        projector,
    )


def _tool_call(*, call_id: str = "call-shadow", tool_name: str = "lookup"):
    return {
        "type": "tool_call",
        "run_id": "attempt-shadow",
        "iteration": 0,
        "tool_name": tool_name,
        "call_id": call_id,
        "arguments": {"query": "weather"},
        "source_provider": "openai",
    }


def _tool_result(*, call_id: str = "call-shadow", tool_name: str = "lookup"):
    return {
        "type": "tool_result",
        "run_id": "attempt-shadow",
        "iteration": 0,
        "tool_name": tool_name,
        "call_id": call_id,
        "result": {"forecast": "sunny", "detail": "complete-result"},
    }


def _compile_context(root: Path, journal, artifacts):
    compiler_capabilities = SQLiteContextCompilerV2Store(
        context_store=SQLiteContextV2Store(
            database_path=root / "context_v2.sqlite3",
            object_directory=root / "objects",
        )
    ).bind_execution("execution-shadow", artifacts=artifacts)
    coordinator = ContextCompileCoordinator(
        journal=journal,
        checkpoint_repository=compiler_capabilities.checkpoints,
        build_repository=compiler_capabilities.context_builds,
        partial_attempt_sink=lambda request, error: None,
    )
    request_factory = JournalContextRequestFactory(
        attempt=_attempt(),
        journal=journal,
        model_window_fallback=lambda provider, model: 16_384,
    )
    state = RunState()
    state.session_state.session_id = "execution-shadow"
    state.provider_state.provider = "openai"
    state.provider_state.model = "gpt-shadow"
    state.provider_state.max_context_window_tokens = 16_384
    state.seed_messages(
        [{"role": "system", "content": "stable instructions"}],
        created_by="test.shadow_observed",
    )
    context = HarnessContext(
        state=state,
        phase="before_model",
        event={"run_id": "attempt-shadow"},
    )
    return coordinator.compile(request_factory(context))


def test_canonical_projection_remains_default_and_rejects_unbound_legacy_result(
    tmp_path: Path,
) -> None:
    mode_type, _adapter_type = _shadow_projection_api()
    _store_value, journal, artifacts = _store(tmp_path)
    projector = CanonicalSemanticEventProjector(
        attempt=_attempt(),
        artifacts=artifacts,
        payload_sanitizer=lambda event_type, payload: payload,
    )

    assert projector.projection_mode is mode_type.CANONICAL
    with pytest.raises(SemanticEventProjectionError, match="subject"):
        projector(_tool_result())
    assert journal.capture_snapshot().events == ()


def test_active_context_module_rejects_observation_only_factory() -> None:
    mode_type, _adapter_type = _shadow_projection_api()
    factory = DurableContextRuntimeFactory(
        bundle_builder=lambda attempt: None,
        generation_resolver=lambda context, execution_id: "generation-shadow",
        current_input_resolver=lambda context, attempt: None,
        projection_mode=mode_type.SHADOW_OBSERVED,
    )
    module = ContextModule(
        runtime=ContextRuntime.from_factory(
            owner_id="shadow-observer",
            execution_factory=factory,
        )
    )

    class _Builder:
        def attach_context_runtime(self, runtime):
            raise AssertionError("observation-only runtime must not be attached")

    with pytest.raises(ValueError, match="observation-only"):
        module.configure(_Builder())


def test_shadow_observer_persists_full_result_before_non_authoritative_event(
    tmp_path: Path,
) -> None:
    journal, artifacts, _projector, sink = _shadow_projector(tmp_path)
    sink(_tool_call())
    result = sink(_tool_result())

    assert result is not None
    events = journal.capture_snapshot().events
    assert [event.event_type for event in events] == ["tool_call", "tool_result"]
    call, completion = events
    assert call.payload["call_id"] == completion.payload["call_id"]
    assert call.payload["tool_name"] == completion.payload["tool_name"]
    assert call.payload["observation"] == completion.payload["observation"] == {
        "schema": "unchain.shadow_observed_tool_event.v1",
        "mode": "shadow",
        "observed": True,
        "authoritative": False,
        "source": "legacy_runtime_callback",
    }
    assert "execution_subject" not in completion.payload
    assert "execution_subject_sha256" not in completion.payload
    assert all(event.event_type != "tool.started" for event in events)
    full_ref = completion.payload["full_output_ref"]
    stored = artifacts.read_full(
        ArtifactRef(
            ref=completion.resource_refs[0],
            media_type="application/json",
            byte_length=completion.payload["result_bytes"],
            sha256=completion.payload["result_sha256"],
            preview=completion.payload["preview"],
        ),
        remaining_budget_bytes=completion.payload["result_bytes"],
    )
    assert json.loads(stored) == _tool_result()["result"]
    assert completion.resource_refs[0].to_dict() == full_ref


@pytest.mark.parametrize(
    "result",
    [
        _tool_result(call_id="call-missing"),
        _tool_result(tool_name="different-tool"),
        {key: value for key, value in _tool_result().items() if key != "call_id"},
    ],
)
def test_shadow_observer_fails_closed_for_missing_or_mismatched_pair(
    tmp_path: Path,
    result,
) -> None:
    journal, _artifacts, _projector, sink = _shadow_projector(tmp_path)
    sink(_tool_call())

    with pytest.raises(SemanticEventProjectionError, match="tool|call|pair"):
        sink(result)

    assert [
        event.event_type for event in journal.capture_snapshot().events
    ] == ["tool_call"]


def test_shadow_observer_recovers_call_pair_after_projector_restart(
    tmp_path: Path,
) -> None:
    _journal, _artifacts, _projector, first_sink = _shadow_projector(tmp_path)
    first_sink(_tool_call())

    reopened_journal, _reopened_artifacts, _reopened_projector, reopened_sink = (
        _shadow_projector(tmp_path)
    )
    reopened_sink(_tool_result())

    assert [
        event.event_type for event in reopened_journal.capture_snapshot().events
    ] == ["tool_call", "tool_result"]


def test_shadow_observed_pair_is_consumed_as_a_marked_neutral_exchange(
    tmp_path: Path,
) -> None:
    journal, artifacts, projector, sink = _shadow_projector(tmp_path)
    ingress = ContextInputIngress(
        attempt=_attempt(),
        projector=projector,
        sink=sink,
    )
    ingress.persist(
        HostResolvedCurrentInput(
            attempt=_attempt(),
            content="check the weather",
        )
    )
    sink(_tool_call())
    sink(_tool_result())

    compiled = _compile_context(tmp_path, journal, artifacts)
    serialized_messages = json.dumps(
        compiled.to_dict()["messages"],
        ensure_ascii=False,
        sort_keys=True,
    )

    assert "unchain.shadow_observed_tool_event.v1" in serialized_messages
    assert "authoritative" in serialized_messages
    assert "false" in serialized_messages
    assert compiled.diagnostics["shadow_observed_tool_exchange_count"] == 1
