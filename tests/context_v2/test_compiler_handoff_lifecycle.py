from __future__ import annotations

import pytest

from unchain.context import (
    ContextCompileRequest,
    ContextCompiler,
    ContextCompilerError,
    resolve_context_budget,
)
from unchain.journal import ResourceRef


_LIFECYCLE_TYPES = (
    "subagent_completed",
    "subagent_failed",
    "subagent_cancelled",
    "subagent_canceled",
    "agent_thread_completed",
    "agent_thread_failed",
    "subagent_return_handoff_completed",
)


def _request(event: dict) -> ContextCompileRequest:
    return ContextCompileRequest(
        case="handoff-lifecycle-boundary",
        source_messages=({"role": "user", "content": "current"},),
        semantic_events=(event,),
        budget=resolve_context_budget(context_window_tokens=20_000),
    )


@pytest.mark.parametrize("event_type", _LIFECYCLE_TYPES)
def test_lifecycle_without_envelope_is_not_a_durable_handoff(
    event_type: str,
) -> None:
    result = ContextCompiler().compile(
        _request(
            {
                "type": event_type,
                "event_id": f"event-{event_type}",
                "store_seq": 1,
                "child_run_id": "child-1",
                "status": "complete",
                "summary": "lifecycle only",
            }
        )
    )

    assert [dict(message) for message in result.messages] == [
        {"role": "user", "content": "current"}
    ]


def test_lifecycle_top_level_ref_without_envelope_is_not_a_handoff_claim() -> None:
    result = ContextCompiler().compile(
        _request(
            {
                "type": "subagent_completed",
                "event_id": "event-top-level-ref",
                "store_seq": 1,
                "child_run_id": "child-1",
                "status": "complete",
                "full_output_ref": ResourceRef(
                    "artifact", "top-level-only", 1
                ).to_dict(),
            }
        )
    )

    assert [dict(message) for message in result.messages] == [
        {"role": "user", "content": "current"}
    ]


@pytest.mark.parametrize(
    "event",
    (
        {
            "type": "subagent_completed",
            "event_id": "event-envelope-missing-ref",
            "store_seq": 1,
            "handoff_envelope": {
                "child_run_id": "child-1",
                "status": "complete",
            },
        },
        {
            "type": "subagent_completed",
            "event_id": "event-envelope-malformed-ref",
            "store_seq": 1,
            "handoff_envelope": {
                "child_run_id": "child-1",
                "status": "complete",
                "full_output_ref": {
                    "kind": "memory",
                    "id": "not-an-artifact",
                    "revision": 1,
                },
            },
        },
        {
            "type": "handoff.recorded",
            "event_id": "event-recorded-missing-ref",
            "store_seq": 1,
            "child_run_id": "child-1",
            "status": "complete",
        },
    ),
)
def test_explicit_handoff_claim_without_artifact_fails_closed(event: dict) -> None:
    with pytest.raises(ContextCompilerError, match="handoff"):
        ContextCompiler().compile(_request(event))
