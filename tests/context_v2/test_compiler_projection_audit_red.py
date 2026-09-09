from __future__ import annotations

import pytest

from unchain.context import (
    ContextCompileRequest,
    ContextCompiler,
    ContextCompilerError,
    resolve_context_budget,
)
from unchain.context.compiler import JournalMessageProjectionError


def _request(
    *,
    source_messages: tuple[dict, ...] = (
        {"role": "user", "content": "current"},
    ),
    semantic_events: tuple[dict, ...] = (),
    window: int = 20_000,
) -> ContextCompileRequest:
    return ContextCompileRequest(
        case="compiler-projection-audit-red",
        source_messages=source_messages,
        current_generation="generation-1",
        semantic_events=semantic_events,
        budget=resolve_context_budget(context_window_tokens=window),
        provider="openai",
        model="synthetic",
        build_id="build-projection-audit-red",
        execution_id="execution-1",
        generation_id="generation-1",
        attempt_id="attempt-current",
    )


def _event(
    event_type: str,
    event_id: str,
    store_seq: int,
    *,
    attempt_id: str = "attempt-history",
    **payload: object,
) -> dict:
    return {
        "type": event_type,
        "event_id": event_id,
        "store_seq": store_seq,
        "execution_id": "execution-1",
        "generation_id": "generation-1",
        "attempt_id": attempt_id,
        "run_id": attempt_id,
        **payload,
    }


@pytest.mark.parametrize("origin", ["source", "canonical", "derived_final"])
def test_provider_native_tool_wire_without_an_id_is_rejected_from_every_origin(
    origin: str,
) -> None:
    native_wire = {
        "role": "assistant",
        "content": "looks like text",
        "type": "function_call",
        "name": "dangerous",
    }
    if origin == "source":
        request = _request(
            source_messages=(
                native_wire,
                {"role": "user", "content": "current"},
            )
        )
    elif origin == "canonical":
        request = _request(
            semantic_events=(
                _event(
                    "message.assistant",
                    "event-message",
                    1,
                    message=native_wire,
                ),
            )
        )
    else:
        request = _request(
            semantic_events=(
                _event(
                    "final_message",
                    "event-final",
                    1,
                    content={"type": "function_call", "name": "dangerous"},
                ),
                _event(
                    "run_completed",
                    "event-terminal",
                    2,
                    status="completed",
                ),
            )
        )

    with pytest.raises(ContextCompilerError):
        ContextCompiler().compile(request)


@pytest.mark.parametrize(
    "payload_scope",
    [
        {"execution_id": "execution-foreign"},
        {"generation_id": "generation-foreign"},
        {"attempt_id": "attempt-child"},
        {"run_id": "child-run"},
    ],
)
def test_direct_payload_scope_cannot_be_overridden_by_outer_root_identity(
    payload_scope: dict,
) -> None:
    event = _event(
        "message.assistant",
        "event-message",
        1,
        payload={
            "execution_id": "execution-1",
            "generation_id": "generation-1",
            "attempt_id": "attempt-history",
            "run_id": "attempt-history",
            "message": {"role": "assistant", "content": "must not project"},
            **payload_scope,
        },
    )

    with pytest.raises(JournalMessageProjectionError) as raised:
        ContextCompiler().compile(_request(semantic_events=(event,)))

    assert raised.value.reason == "event_scope_conflict"


def test_direct_payload_cursor_conflict_is_rejected_before_event_filtering() -> None:
    filtered = _event(
        "diagnostic.ignored",
        "event-filtered",
        1,
        payload={
            "event_id": "event-message",
            "store_seq": 2,
            "note": "the outer cursor must not hide this conflicting identity",
        },
    )
    canonical = _event(
        "message.user",
        "event-message",
        2,
        message={"role": "user", "content": "history"},
    )

    with pytest.raises(JournalMessageProjectionError) as raised:
        ContextCompiler().compile(
            _request(semantic_events=(filtered, canonical))
        )

    assert raised.value.reason == "event_cursor_conflict"


def test_graph_final_without_step_eligibility_cannot_create_a_dependency() -> None:
    workflow_scope = {
        "workflow_node_id": "final-node",
        "iteration": 3,
    }
    events = (
        _event(
            "message.user",
            "event-history",
            1,
            message={"role": "user", "content": "old " + ("x" * 30_000)},
        ),
        _event(
            "final_message",
            "event-final",
            2,
            content="graph output without final-step eligibility",
            **workflow_scope,
        ),
        _event(
            "run_completed",
            "event-terminal",
            3,
            status="completed",
            **workflow_scope,
        ),
    )

    with pytest.raises(JournalMessageProjectionError) as raised:
        ContextCompiler().compile(
            _request(semantic_events=events, window=8_192)
        )

    assert raised.value.reason == "terminal_scope_conflict"
