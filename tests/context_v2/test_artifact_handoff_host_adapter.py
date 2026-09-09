from __future__ import annotations

import json

import pytest

from unchain.context.artifacts import MAX_INLINE_TOOL_RESULT_BYTES, ArtifactService
from unchain.context.handoff import DurableHandoffRecorder, HandoffService
from unchain.context.host_adapter import (
    ContextArtifactHandoffHostAdapter,
    HostHandoffIntegrityError,
)
from unchain.context.projector import CanonicalSemanticEventProjector
from unchain.journal.models import (
    AttemptRef,
    EventCursor,
    EventRange,
    GenerationRef,
    _thaw_json,
)
from unchain.journal.runtime import DurableEventSink
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store
from unchain.subagents.types import SubagentResult
from unchain.tools.runtime import ToolRuntimeOutcome


PARENT_ATTEMPT = AttemptRef(
    GenerationRef("execution-host-adapter", "generation-parent"),
    "parent-run",
)
CHILD_ATTEMPT = AttemptRef(
    GenerationRef("execution-child", "generation-child"),
    "child-run",
)
CHILD_RANGE = EventRange(
    EventCursor(1, "child-event-1"),
    EventCursor(8, "child-event-8"),
)


def _identity_sanitizer(content: bytes, media_type: str) -> bytes:
    assert media_type == "application/json"
    return content


def _build_adapter(tmp_path):
    store = SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )
    repository = store.bind_execution(PARENT_ATTEMPT.generation.execution_id)
    artifacts = ArtifactService(repository, sanitizer=_identity_sanitizer)
    projector = CanonicalSemanticEventProjector(
        attempt=PARENT_ATTEMPT,
        artifacts=artifacts,
        payload_sanitizer=lambda _event_type, payload: payload,
    )
    sink = DurableEventSink(repository, PARENT_ATTEMPT, projector)
    recorder = DurableHandoffRecorder(
        attempt=PARENT_ATTEMPT,
        handoffs=HandoffService(artifacts),
        projector=projector,
        sink=sink,
    )
    return ContextArtifactHandoffHostAdapter(recorder=recorder), repository, store


def test_completed_tool_payload_is_durable_before_inline_reduction_and_paged_after_restart(
    tmp_path,
) -> None:
    adapter, _repository, store = _build_adapter(tmp_path)
    complete_payload = {
        "status": "completed",
        "output": "x" * (MAX_INLINE_TOOL_RESULT_BYTES + 4_096),
    }

    receipt = adapter.persist_tool_outcome(
        ToolRuntimeOutcome(handled=True, tool_result=complete_payload),
        operation_id="host-tool-result-call-1",
    )

    assert receipt.artifact.byte_length > MAX_INLINE_TOOL_RESULT_BYTES
    assert receipt.visible_result["full_output_ref"] == receipt.artifact.ref.to_dict()
    assert complete_payload["output"] not in json.dumps(
        _thaw_json(receipt.visible_result)
    )

    restarted, _reopened_repository, _reopened_store = _build_adapter(tmp_path)
    first = restarted.read_page(receipt.artifact, offset=0, limit=257)
    second = restarted.read_page(
        receipt.artifact,
        offset=first.next_offset,
        limit=257,
    )

    expected = json.dumps(
        complete_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert first.data + second.data == expected[:514]
    assert (
        store.object_directory.joinpath(receipt.artifact.sha256).read_bytes()
        == expected
    )


def test_subagent_result_is_recorded_as_handoff_before_parent_visible_envelope(
    tmp_path,
) -> None:
    adapter, repository, _store = _build_adapter(tmp_path)
    result = SubagentResult(
        mode="delegate",
        agent_name="specialist",
        template_name="specialist-template",
        status="completed",
        output="final answer",
        summary="child summary",
        messages=[
            {"role": "assistant", "content": "m" * 24_000},
        ],
        lineage=["parent", "specialist"],
    )

    receipt = adapter.record_subagent_result(
        result,
        child_attempt=CHILD_ATTEMPT,
        source_event_range=CHILD_RANGE,
        operation_id="host-handoff-child-run",
    )

    assert receipt.envelope.child_attempt == CHILD_ATTEMPT
    assert receipt.envelope.summary == "child summary"
    assert receipt.envelope.full_output_ref == receipt.full_output_artifact.ref
    assert receipt.envelope.byte_length == receipt.full_output_artifact.byte_length
    assert receipt.envelope.sha256 == receipt.full_output_artifact.sha256
    assert receipt.model_payload == {
        "status": "complete",
        "summary": receipt.envelope.summary,
        "child_run_id": CHILD_ATTEMPT.attempt_id,
        "full_output_ref": receipt.full_output_artifact.ref.to_dict(),
        "artifact_refs": [],
        "content_bytes": receipt.full_output_artifact.byte_length,
        "content_sha256": receipt.full_output_artifact.sha256,
    }
    assert "messages" not in receipt.model_payload

    page = repository.read()
    assert len(page.events) == 1
    event = page.events[0]
    assert event.event_type == "handoff.recorded"
    assert (
        event.payload["full_output_artifact"] == receipt.full_output_artifact.to_dict()
    )
    assert event.resource_refs == (receipt.full_output_artifact.ref,)

    full_output = adapter.artifacts.read_full(
        receipt.full_output_artifact,
        remaining_budget_bytes=receipt.full_output_artifact.byte_length,
    )
    assert json.loads(full_output) == result.to_dict()


def test_handoff_descriptor_recovers_from_journal_and_reads_after_restart(
    tmp_path,
) -> None:
    adapter, repository, _store = _build_adapter(tmp_path)
    result = SubagentResult(
        mode="worker",
        agent_name="worker-1",
        template_name=None,
        status="max_iterations",
        output="partial output",
        summary="partial summary",
    )
    first = adapter.record_subagent_result(
        result,
        child_attempt=CHILD_ATTEMPT,
        source_event_range=CHILD_RANGE,
        operation_id="host-handoff-recovery",
    )
    replay = adapter.record_subagent_result(
        result,
        child_attempt=CHILD_ATTEMPT,
        source_event_range=CHILD_RANGE,
        operation_id="host-handoff-recovery",
    )
    assert replay.duplicate is True
    assert replay.envelope == first.envelope
    event = repository.read().events[0]

    restarted, _repository, _store = _build_adapter(tmp_path)
    recovered = restarted.recover_handoff(event)
    page = restarted.read_page(
        recovered.full_output_artifact,
        offset=7,
        limit=31,
    )

    assert recovered.envelope == first.envelope
    assert recovered.full_output_artifact == first.full_output_artifact
    assert page.data
    assert recovered.envelope.status.value == "partial"

    changed = dict(event.payload)
    descriptor = dict(changed["full_output_artifact"])
    descriptor["sha256"] = "0" * 64
    changed["full_output_artifact"] = descriptor
    forged = type(event)(
        event.event_id,
        event.event_type,
        event.attempt,
        event.operation,
        event.store_seq,
        changed,
        event.resource_refs,
    )
    with pytest.raises(HostHandoffIntegrityError, match="descriptor"):
        restarted.recover_handoff(forged)
