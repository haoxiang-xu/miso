from __future__ import annotations

from dataclasses import replace

import pytest

from unchain.context import (
    ContextCompileRequest,
    ContextCompiler,
    ContextCompilerError,
    SourceMessageCursor,
    resolve_context_budget,
)
from unchain.context.compiler import _CheckpointBinding
from unchain.journal import ResourceRef


def _pressure_request(*, receipt_note: str = "receipt-a") -> ContextCompileRequest:
    return ContextCompileRequest(
        case="checkpoint-consumption-proof",
        source_messages=({"role": "user", "content": "current"},),
        current_generation="generation-1",
        semantic_events=(
            {
                "type": "message.user",
                "event_id": "event-1",
                "store_seq": 1,
                "execution_id": "execution-1",
                "generation_id": "generation-1",
                "attempt_id": "attempt-history",
                "run_id": "attempt-history",
                "message": {
                    "role": "user",
                    "content": "old " + ("x" * 30_000),
                },
            },
            {
                "type": "final_message",
                "event_id": "event-2",
                "store_seq": 2,
                "execution_id": "execution-1",
                "generation_id": "generation-1",
                "attempt_id": "attempt-history",
                "run_id": "attempt-history",
                "content": "old answer",
                "workflow_node_id": "final-node",
                "workflow_step_index": 1,
                "workflow_step_count": 2,
                "iteration": 0,
            },
            {
                "type": "artifact.recorded",
                "event_id": "event-3",
                "store_seq": 3,
                "execution_id": "execution-1",
                "generation_id": "generation-1",
                "attempt_id": "attempt-history",
                "run_id": "attempt-history",
                "artifact_ref": {
                    "schema": "unchain.resource_ref.v1",
                    "kind": "artifact",
                    "id": "unrelated-artifact",
                    "revision": 1,
                    "fragment": "",
                },
            },
            {
                "type": "run_completed",
                "event_id": "event-4",
                "store_seq": 4,
                "execution_id": "execution-1",
                "generation_id": "generation-1",
                "attempt_id": "attempt-history",
                "run_id": "attempt-history",
                "status": "completed",
                "workflow_node_id": "final-node",
                "workflow_step_index": 1,
                "workflow_step_count": 2,
                "iteration": 0,
                "receipt_note": receipt_note,
            },
            {
                "type": "message.user",
                "event_id": "event-5",
                "store_seq": 5,
                "execution_id": "execution-1",
                "generation_id": "generation-1",
                "attempt_id": "attempt-current",
                "run_id": "attempt-current",
                "message": {"role": "user", "content": "current"},
            },
        ),
        budget=resolve_context_budget(context_window_tokens=8_192),
        source_message_cursors=(SourceMessageCursor(0, "event-5", 5),),
        provider="openai",
        model="synthetic",
        build_id="build-1",
        execution_id="execution-1",
        generation_id="generation-1",
        attempt_id="attempt-current",
    )


def test_integrated_checkpoint_request_binds_terminal_receipt_payload() -> None:
    compiler = ContextCompiler()

    first = compiler.compile(_pressure_request(receipt_note="receipt-a"))
    second = compiler.compile(_pressure_request(receipt_note="receipt-b"))

    first_request = first.checkpoint_requests[0]
    second_request = second.checkpoint_requests[0]
    assert first_request.source_messages_sha256 == second_request.source_messages_sha256
    assert first_request.source_event_ids == second_request.source_event_ids
    assert first_request.projection_dependencies[0].receipt_cursor.event_id == "event-4"
    assert (
        first_request.projection_dependencies[0].event_sha256
        != second_request.projection_dependencies[0].event_sha256
    )
    assert first_request.request_id != second_request.request_id


def test_second_pass_emits_one_exact_internal_checkpoint_consumption() -> None:
    compiler = ContextCompiler()
    request = _pressure_request()

    first = compiler._compile_for_coordinator(request)
    checkpoint_request = first.result.checkpoint_requests[0]
    checkpoint_ref = ResourceRef("checkpoint", "checkpoint-1", 1)
    second = compiler._compile_for_coordinator(
        request,
        checkpoint_binding=_CheckpointBinding(
            request=checkpoint_request,
            checkpoint_ref=checkpoint_ref,
        ),
    )

    assert first.consumptions == ()
    assert second.result.checkpoint_requests == ()
    assert len(second.consumptions) == 1
    consumption = second.consumptions[0]
    assert consumption.checkpoint_request_id == checkpoint_request.request_id
    assert consumption.checkpoint_ref == checkpoint_ref
    assert consumption.projected_message_index >= 0
    assert consumption.omitted_complete_turns == 1
    assert (
        second.result.messages[consumption.projected_message_index]["content"]
        .startswith("[MEMORY_V2_CHECKPOINT]")
    )


def test_prebound_legacy_checkpoint_id_never_produces_a_consumption() -> None:
    with pytest.raises(
        ContextCompilerError,
        match="checkpoint_binding_requires_coordinator",
    ):
        ContextCompiler()._compile_for_coordinator(
            replace(
                _pressure_request(),
                checkpoint_ref=ResourceRef(
                    "checkpoint",
                    "legacy-checkpoint",
                    1,
                ),
                checkpoint_request_id="checkpoint-" + ("a" * 64),
            )
        )


def test_public_compiler_rejects_prebound_checkpoint_authority() -> None:
    compiler = ContextCompiler()
    request = _pressure_request()
    checkpoint_request = compiler.compile(request).checkpoint_requests[0]

    with pytest.raises(
        ContextCompilerError,
        match="checkpoint_binding_requires_coordinator",
    ):
        compiler.compile(
            replace(
                request,
                checkpoint_ref=ResourceRef(
                    "checkpoint",
                    "external-without-repository",
                    1,
                ),
                checkpoint_request_id=checkpoint_request.request_id,
            )
        )
