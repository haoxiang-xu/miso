from __future__ import annotations

import json

import pytest
import unchain.context as context_api

from unchain.context.artifacts import ArtifactService
from unchain.context.compiler import ContextCompiler
from unchain.context.derived_handoff import (
    DerivedHandoffInputError,
    DerivedHandoffInputIngress,
    HostResolvedDerivedHandoffInput,
)
from unchain.context.handoff import DurableHandoffRecorder, HandoffService
from unchain.context.ingress import ContextInputIngress
from unchain.context.models import HandoffStatus
from unchain.context.projector import CanonicalSemanticEventProjector
from unchain.context.request_factory import JournalContextRequestFactory
from unchain.context.ports import ContextConflictError
from unchain.journal import (
    AttemptRef,
    DurableEventSink,
    EventCursor,
    EventRange,
    GenerationRef,
    SemanticEventDraft,
)
from unchain.journal.models import _thaw_json
from unchain.kernel.harness import HarnessContext
from unchain.kernel.state import RunState
from unchain.persistence import SQLiteContextV2Store


GENERATION = GenerationRef("execution-derived", "generation-derived")
SOURCE_ATTEMPT = AttemptRef(GENERATION, "step-source")
CONSUMER_ATTEMPT = AttemptRef(GENERATION, "step-consumer")


def _projector(attempt, artifacts):
    return CanonicalSemanticEventProjector(
        attempt=attempt,
        artifacts=artifacts,
        payload_sanitizer=lambda _event_type, payload: payload,
    )


def _open_runtime(tmp_path):
    store = SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )
    repository = store.bind_execution(GENERATION.execution_id)
    artifacts = ArtifactService(
        repository,
        sanitizer=lambda content, _media_type: content,
    )
    source_projector = _projector(SOURCE_ATTEMPT, artifacts)
    source_sink = DurableEventSink(
        repository,
        SOURCE_ATTEMPT,
        source_projector,
    )
    consumer_projector = _projector(CONSUMER_ATTEMPT, artifacts)
    consumer_sink = DurableEventSink(
        repository,
        CONSUMER_ATTEMPT,
        consumer_projector,
    )
    ingress = ContextInputIngress(
        attempt=CONSUMER_ATTEMPT,
        projector=consumer_projector,
        sink=consumer_sink,
    )
    recorder = DurableHandoffRecorder(
        attempt=CONSUMER_ATTEMPT,
        handoffs=HandoffService(artifacts),
        projector=consumer_projector,
        sink=consumer_sink,
    )
    derived = DerivedHandoffInputIngress(
        consumer_attempt=CONSUMER_ATTEMPT,
        source_attempt=SOURCE_ATTEMPT,
        handoff_recorder=recorder,
        input_ingress=ingress,
    )
    return store, repository, artifacts, source_sink, derived


def _append_source_events(source_sink):
    receipts = []
    for index, event_type in enumerate(("run_started", "source.output"), 1):
        receipts.append(
            source_sink.append_projected(
                SemanticEventDraft(
                    event_id=f"event-source-{index}",
                    event_type=event_type,
                    attempt=SOURCE_ATTEMPT,
                    operation_id=f"operation-source-{index}",
                    payload={
                        "run_id": SOURCE_ATTEMPT.attempt_id,
                        "sequence": index,
                    },
                )
            )
        )
    return EventRange(receipts[0].cursor, receipts[-1].cursor)


def _input(source_range, *, output="answer-1", operation_id="handoff-step-1"):
    return HostResolvedDerivedHandoffInput(
        consumer_attempt=CONSUMER_ATTEMPT,
        source_attempt=SOURCE_ATTEMPT,
        status=HandoffStatus.COMPLETE,
        full_output={
            "summary": "source step complete",
            "output": output,
        },
        summary="source step complete",
        source_event_range=source_range,
        operation_id=operation_id,
    )


def _context():
    state = RunState()
    state.seed_messages(
        [
            {"role": "system", "content": "current graph instructions"},
            {"role": "user", "content": "transcript text is not authoritative"},
        ]
    )
    state.session_state.session_id = GENERATION.execution_id
    state.provider_state.provider = "openai"
    state.provider_state.model = "gpt-derived"
    state.provider_state.max_context_window_tokens = 16_384
    return HarnessContext(
        state=state,
        phase="before_model",
        event={"run_id": CONSUMER_ATTEMPT.attempt_id, "toolkit": _Toolkit()},
    )


class _Toolkit:
    def to_provider_json(self, _provider):
        return []


def test_derived_handoff_boundary_is_publicly_exported():
    assert context_api.DerivedHandoffInputIngress is DerivedHandoffInputIngress
    assert (
        context_api.HostResolvedDerivedHandoffInput
        is HostResolvedDerivedHandoffInput
    )


def test_durable_handoff_precedes_canonical_consumer_input_and_compiles(tmp_path):
    _store, repository, artifacts, source_sink, derived = _open_runtime(tmp_path)
    source_range = _append_source_events(source_sink)

    receipt = derived.persist(_input(source_range))

    events = repository.capture_snapshot().events
    assert [event.event_type for event in events] == [
        "run_started",
        "source.output",
        "handoff.recorded",
        "message.user",
    ]
    handoff_event, input_event = events[-2:]
    assert receipt.handoff_cursor == EventCursor(
        handoff_event.store_seq,
        handoff_event.event_id,
    )
    assert receipt.input_cursor == EventCursor(
        input_event.store_seq,
        input_event.event_id,
    )
    assert (
        _thaw_json(handoff_event.payload["handoff_envelope"])
        == receipt.envelope.to_dict()
    )
    assert (
        _thaw_json(handoff_event.payload["full_output_artifact"])
        == receipt.full_output_artifact.to_dict()
    )
    assert handoff_event.resource_refs[0] == receipt.envelope.full_output_ref
    assert input_event.resource_refs[1] == receipt.envelope.full_output_ref

    derived_message = _thaw_json(input_event.payload["message"])
    derived_content = json.loads(derived_message["content"])
    assert derived_content["schema"] == "unchain.derived_handoff_input.v1"
    assert derived_content["source_attempt"] == SOURCE_ATTEMPT.to_dict()
    assert derived_content["consumer_attempt"] == CONSUMER_ATTEMPT.to_dict()
    assert derived_content["handoff_envelope"] == receipt.envelope.to_dict()
    assert (
        derived_content["full_output_artifact"]
        == receipt.full_output_artifact.to_dict()
    )
    assert derived_message["attachments"] == [
        {
            "schema": "unchain.host_resolved_attachment.v1",
            "kind": "handoff",
            "name": "step-source.json",
            "media_type": "application/json",
            "artifact": receipt.full_output_artifact.to_dict(),
        }
    ]
    restored = artifacts.read_full(
        receipt.full_output_artifact,
        remaining_budget_bytes=receipt.full_output_artifact.byte_length,
    )
    assert json.loads(restored) == {
        "summary": "source step complete",
        "output": "answer-1",
    }

    request = JournalContextRequestFactory(
        attempt=CONSUMER_ATTEMPT,
        journal=repository,
        model_window_fallback=lambda _provider, _model: 8_192,
    )(_context())
    assert request.source_messages[0] == {
        "role": "system",
        "content": "current graph instructions",
    }
    assert _thaw_json(request.source_messages[-1]) == derived_message
    assert request.source_message_cursors[0].event_id == input_event.event_id
    assert request.source_message_cursors[0].store_seq == input_event.store_seq
    assert request.attempt_id == CONSUMER_ATTEMPT.attempt_id
    compiled = ContextCompiler().compile(request)
    compiled_message = _thaw_json(compiled.messages[-1])
    assert compiled_message == {
        "role": derived_message["role"],
        "content": derived_message["content"],
    }
    assert json.loads(compiled_message["content"]) == derived_content
    assert compiled.diagnostics["compacted"] is False


def test_exact_replay_is_idempotent_before_and_after_restart(tmp_path):
    _store, repository, _artifacts, source_sink, derived = _open_runtime(tmp_path)
    source_range = _append_source_events(source_sink)
    current_input = _input(source_range)

    first = derived.persist(current_input)
    replay = derived.persist(current_input)
    assert first.handoff_duplicate is False
    assert first.input_duplicate is False
    assert replay.handoff_duplicate is True
    assert replay.input_duplicate is True
    assert replay.envelope == first.envelope
    assert replay.full_output_artifact == first.full_output_artifact
    assert len(repository.capture_snapshot().events) == 4

    _store, reopened, _artifacts, source_sink, restarted = _open_runtime(tmp_path)
    restarted_range = _append_source_events(source_sink)
    recovered = restarted.persist(_input(restarted_range))
    assert recovered.handoff_duplicate is True
    assert recovered.input_duplicate is True
    assert recovered.envelope == first.envelope
    assert recovered.full_output_artifact == first.full_output_artifact
    assert len(reopened.capture_snapshot().events) == 4


def test_restart_completes_input_after_handoff_was_durable(tmp_path, monkeypatch):
    _store, repository, _artifacts, source_sink, derived = _open_runtime(tmp_path)
    source_range = _append_source_events(source_sink)
    current_input = _input(source_range)

    def fail_after_handoff(_current_input):
        raise OSError("simulated process stop before input receipt")

    monkeypatch.setattr(derived.input_ingress, "persist", fail_after_handoff)
    with pytest.raises(OSError, match="before input receipt"):
        derived.persist(current_input)
    assert [
        event.event_type for event in repository.capture_snapshot().events
    ] == ["run_started", "source.output", "handoff.recorded"]

    _store, reopened, _artifacts, source_sink, restarted = _open_runtime(tmp_path)
    restarted_range = _append_source_events(source_sink)
    recovered = restarted.persist(_input(restarted_range))
    assert recovered.handoff_duplicate is True
    assert recovered.input_duplicate is False
    assert [event.event_type for event in reopened.capture_snapshot().events] == [
        "run_started",
        "source.output",
        "handoff.recorded",
        "message.user",
    ]


def test_source_range_and_attempt_bindings_fail_before_handoff_write(tmp_path):
    _store, repository, _artifacts, source_sink, derived = _open_runtime(tmp_path)
    first = source_sink.append_projected(
        SemanticEventDraft(
            event_id="event-source-only",
            event_type="message.assistant",
            attempt=SOURCE_ATTEMPT,
            operation_id="operation-source-only",
            payload={"run_id": SOURCE_ATTEMPT.attempt_id},
        )
    )
    foreign_attempt = AttemptRef(GENERATION, "step-foreign")
    foreign_projector = _projector(
        foreign_attempt,
        derived.handoff_recorder.handoffs.artifacts,
    )
    foreign_sink = DurableEventSink(repository, foreign_attempt, foreign_projector)
    foreign = foreign_sink.append_projected(
        SemanticEventDraft(
            event_id="event-foreign",
            event_type="message.assistant",
            attempt=foreign_attempt,
            operation_id="operation-foreign",
            payload={"run_id": foreign_attempt.attempt_id},
        )
    )
    mixed_range = EventRange(first.cursor, foreign.cursor)

    with pytest.raises(DerivedHandoffInputError, match="foreign attempt"):
        derived.persist(_input(mixed_range))
    assert len(repository.capture_snapshot().events) == 2

    other_generation = GenerationRef(GENERATION.execution_id, "generation-other")
    with pytest.raises(DerivedHandoffInputError, match="share one generation"):
        DerivedHandoffInputIngress(
            consumer_attempt=CONSUMER_ATTEMPT,
            source_attempt=AttemptRef(other_generation, "step-other"),
            handoff_recorder=derived.handoff_recorder,
            input_ingress=derived.input_ingress,
        )


def test_changed_replay_cannot_replace_the_canonical_input(tmp_path):
    _store, repository, _artifacts, source_sink, derived = _open_runtime(tmp_path)
    source_range = _append_source_events(source_sink)
    first = derived.persist(_input(source_range))

    with pytest.raises(ContextConflictError):
        derived.persist(_input(source_range, output="changed-answer"))

    events = repository.capture_snapshot().events
    assert len(events) == 4
    input_content = json.loads(events[-1].payload["message"]["content"])
    assert input_content["handoff_envelope"] == first.envelope.to_dict()


def test_host_contract_has_no_arbitrary_current_input_text_field(tmp_path):
    assert "content" not in HostResolvedDerivedHandoffInput.__dataclass_fields__
    _store, _repository, _artifacts, source_sink, _derived = _open_runtime(tmp_path)
    source_range = _append_source_events(source_sink)

    with pytest.raises(TypeError):
        HostResolvedDerivedHandoffInput(
            consumer_attempt=CONSUMER_ATTEMPT,
            source_attempt=SOURCE_ATTEMPT,
            status=HandoffStatus.COMPLETE,
            full_output={"output": "derived"},
            source_event_range=source_range,
            operation_id="handoff-step-plain-text",
            content="ordinary non-root input",
        )
