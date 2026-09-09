"""Graph-turn journal projection: visible transcript fidelity.

Mirrors the exact event shapes a real coordinator+step graph turn writes:
the step's derived-handoff envelope input, human-input interaction cycles,
and the step/coordinator duplicated final pair. The compiled transcript must
carry the dialogue a user actually saw — one final per turn, the ask/answer
cycles present, and no internal handoff plumbing.
"""

from __future__ import annotations

import json

from unchain.context import ContextCompileRequest, resolve_context_budget
from unchain.context.compiler import project_canonical_journal_messages


COORD_1 = "unchain-coordinator-turn-1"
STEP_1 = "graph-step-turn-1"
COORD_2 = "unchain-coordinator-turn-2"

ENVELOPE_CONTENT = json.dumps(
    {
        "schema": "unchain.derived_handoff_input.v1",
        "consumer_attempt": {"attempt_id": STEP_1},
        "source_attempt": {"attempt_id": COORD_1},
        "handoff_event": {"event_id": "event-handoff", "store_seq": 2},
        "handoff_envelope": {"child_run_id": STEP_1},
        "full_output_artifact": {"sha256": "0" * 64},
    },
    ensure_ascii=False,
)

ASK_REQUEST = {
    "created_revision": 3,
    "interaction_id": "interaction-ask-1",
    "kind": "human_input",
    "occurrence": "human:toolu-ask-1:1",
    "payload": {
        "kind": "selector",
        "question": "数据来源方案选哪种?",
        "min_selected": 1,
        "max_selected": 1,
        "allow_other": False,
        "options": [
            {"label": "内置静态数据", "value": "static", "description": "本地 JSON"},
            {"label": "外部 API", "value": "api", "description": "接入 TMDB"},
        ],
    },
    "response_contract": {"schema": "selector.v1"},
    "schema_digest": "d" * 64,
    "schema_version": 1,
    "session_id": "chat-execution-1",
    "source_run_id": STEP_1,
    "subject": {},
}

ANSWER_PREVIEW = json.dumps(
    {
        "interaction_id": "interaction-ask-1",
        "response": {
            "other_text": None,
            "request_id": "toolu-ask-1",
            "selected_values": ["api"],
        },
        "submitted_by": "ui:pupu",
    },
    ensure_ascii=False,
)

FINAL_ANSWER = "好的，用外部 API 方案，架构如下……"


def _events() -> list[dict]:
    return [
        {
            "type": "message.user",
            "event_id": "event-user-1",
            "store_seq": 1,
            "attempt_id": COORD_1,
            "run_id": COORD_1,
            "message": {"role": "user", "content": "帮我做一个追踪 app"},
        },
        {
            "type": "message.user",
            "event_id": "event-step-envelope",
            "store_seq": 2,
            "attempt_id": STEP_1,
            "run_id": STEP_1,
            "message": {"role": "user", "content": ENVELOPE_CONTENT},
        },
        {
            "type": "interaction.requested",
            "event_id": "event-ask-1",
            "store_seq": 3,
            "attempt_id": STEP_1,
            "run_id": STEP_1,
            "interaction_id": "interaction-ask-1",
            "interaction_request": ASK_REQUEST,
        },
        {
            "type": "interaction.resolved",
            "event_id": "event-answer-1",
            "store_seq": 4,
            "attempt_id": STEP_1,
            "run_id": STEP_1,
            "interaction_id": "interaction-ask-1",
            "submitted_by": "ui:pupu",
            "preview": ANSWER_PREVIEW,
            "preview_truncated": False,
        },
        {
            "type": "final_message",
            "event_id": "event-step-final",
            "store_seq": 5,
            "attempt_id": STEP_1,
            "run_id": STEP_1,
            "content": FINAL_ANSWER,
        },
        {
            "type": "run_completed",
            "event_id": "event-step-terminal",
            "store_seq": 6,
            "attempt_id": STEP_1,
            "run_id": STEP_1,
            "status": "completed",
        },
        {
            "type": "final_message",
            "event_id": "event-root-final",
            "store_seq": 7,
            "attempt_id": COORD_1,
            "run_id": COORD_1,
            "content": FINAL_ANSWER,
        },
        {
            "type": "run_completed",
            "event_id": "event-root-terminal",
            "store_seq": 8,
            "attempt_id": COORD_1,
            "run_id": COORD_1,
            "status": "completed",
        },
        {
            "type": "message.user",
            "event_id": "event-user-2",
            "store_seq": 9,
            "attempt_id": COORD_2,
            "run_id": COORD_2,
            "message": {"role": "user", "content": "继续，开始写代码"},
        },
    ]


def _projected_messages() -> list[dict]:
    request = ContextCompileRequest(
        case="portable_contract",
        source_messages=(
            {"role": "system", "content": "you are a developer"},
            {"role": "user", "content": "继续，开始写代码"},
        ),
        fixed_overhead_tokens=0,
        semantic_events=tuple(_events()),
        task_state=None,
        pending_task_inputs=(),
        budget=resolve_context_budget(context_window_tokens=50_000),
        source_event_ids=(),
        source_event_store_seqs=(),
        source_message_cursors=(),
        provider="openai",
        model="synthetic",
    )
    projected = project_canonical_journal_messages(request)
    return [dict(message) for message in projected.source_messages]


def test_turn_projects_exactly_one_visible_final() -> None:
    messages = _projected_messages()
    finals = [
        message
        for message in messages
        if message.get("role") == "assistant"
        and message.get("content") == FINAL_ANSWER
    ]
    assert len(finals) == 1


def test_derived_handoff_envelope_never_reaches_the_transcript() -> None:
    messages = _projected_messages()
    assert not any(
        "unchain.derived_handoff_input.v1" in str(message.get("content"))
        for message in messages
    )


def test_human_input_question_and_answer_project_as_dialogue() -> None:
    messages = _projected_messages()
    question_indexes = [
        index
        for index, message in enumerate(messages)
        if message.get("role") == "assistant"
        and "数据来源方案选哪种?" in str(message.get("content"))
        and "内置静态数据" in str(message.get("content"))
    ]
    assert len(question_indexes) == 1
    answer_indexes = [
        index
        for index, message in enumerate(messages)
        if message.get("role") == "user"
        and str(message.get("content")).strip() == "api"
    ]
    assert len(answer_indexes) == 1
    assert question_indexes[0] < answer_indexes[0]

    final_index = next(
        index
        for index, message in enumerate(messages)
        if message.get("content") == FINAL_ANSWER
    )
    assert answer_indexes[0] < final_index


def test_tool_approval_interactions_stay_out_of_the_transcript() -> None:
    events = _events()
    events.insert(
        4,
        {
            "type": "interaction.requested",
            "event_id": "event-approval-1",
            "store_seq": 3,
            "attempt_id": STEP_1,
            "run_id": STEP_1,
            "interaction_id": "interaction-approval-1",
            "interaction_request": {
                **ASK_REQUEST,
                "interaction_id": "interaction-approval-1",
                "kind": "tool_approval",
                "payload": {"call_id": "call-x", "tool_name": "write_file"},
            },
        },
    )
    # renumber to keep store_seq strictly increasing
    for index, event in enumerate(events):
        event["store_seq"] = index + 1
    request = ContextCompileRequest(
        case="portable_contract",
        source_messages=(
            {"role": "system", "content": "you are a developer"},
            {"role": "user", "content": "继续，开始写代码"},
        ),
        fixed_overhead_tokens=0,
        semantic_events=tuple(events),
        task_state=None,
        pending_task_inputs=(),
        budget=resolve_context_budget(context_window_tokens=50_000),
        source_event_ids=(),
        source_event_store_seqs=(),
        source_message_cursors=(),
        provider="openai",
        model="synthetic",
    )
    projected = project_canonical_journal_messages(request)
    assert not any(
        "write_file" in str(dict(message).get("content"))
        for message in projected.source_messages
    )
