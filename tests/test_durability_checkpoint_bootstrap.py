from __future__ import annotations

import copy
import json

from unchain.input import ASK_USER_QUESTION_TOOL_NAME
from unchain.kernel import ModelTurnResult, ToolCall
from unchain.kernel.provider_replay import tool_schema_manifest
from unchain.memory import (
    InMemorySessionStore,
    KernelMemoryRuntime,
    MemoryRuntimeComponentMode,
)
from unchain.runtime import build_runtime_loop
from unchain.tools import Toolkit


SESSION_ID = "durability-only-cold-resume"
LEGACY_CONTENT = "legacy semantic memory must not load"


def _toolkit() -> Toolkit:
    toolkit = Toolkit()
    toolkit.register(
        lambda **_: {"error": "reserved"},
        name=ASK_USER_QUESTION_TOOL_NAME,
        parameters=[],
    )
    return toolkit


def _ask_arguments() -> dict:
    return {
        "title": "Choose framework",
        "question": "Which framework?",
        "selection_mode": "single",
        "options": [
            {"label": "React", "value": "react"},
            {"label": "Vue", "value": "vue"},
        ],
    }


class _AskModelIO:
    provider = "openai"
    model = "gpt-test"

    def __init__(self, toolkit: Toolkit) -> None:
        self.toolkit = toolkit
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        arguments = _ask_arguments()
        call = {
            "type": "function_call",
            "call_id": "call-framework",
            "name": ASK_USER_QUESTION_TOOL_NAME,
            "arguments": json.dumps(arguments),
        }
        return ModelTurnResult(
            assistant_messages=[copy.deepcopy(call)],
            tool_calls=[
                ToolCall(
                    call_id="call-framework",
                    name=ASK_USER_QUESTION_TOOL_NAME,
                    arguments=arguments,
                )
            ],
            response_id="response-framework-question",
            provider_replay_frame={
                "format": "openai.responses.v1",
                "complete": True,
                "items": [
                    *copy.deepcopy(request.messages),
                    copy.deepcopy(call),
                ],
                "source": "test.ask",
                "tool_schema_manifest": tool_schema_manifest(
                    self.toolkit,
                    "openai",
                ),
            },
        )


class _FinalModelIO:
    provider = "openai"
    model = "gpt-test"

    def __init__(self, toolkit: Toolkit, text: str) -> None:
        self.toolkit = toolkit
        self.text = text
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        output = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": self.text}],
        }
        return ModelTurnResult(
            assistant_messages=[
                {"role": "assistant", "content": self.text}
            ],
            tool_calls=[],
            final_text=self.text,
            response_id=f"response-{self.text}",
            provider_replay_frame={
                "format": "openai.responses.v1",
                "complete": True,
                "items": [
                    *copy.deepcopy(request.messages),
                    output,
                ],
                "source": "test.final",
                "tool_schema_manifest": tool_schema_manifest(
                    self.toolkit,
                    "openai",
                ),
            },
        )


def _loop(*, runtime: KernelMemoryRuntime, model_io):
    return build_runtime_loop(
        memory_runtime=runtime,
        memory_runtime_component_mode=(
            MemoryRuntimeComponentMode.DURABILITY_ONLY
        ),
        model_io=model_io,
    )


def test_durability_only_restores_checkpoint_without_loading_semantic_memory() -> None:
    store = InMemorySessionStore()
    store.save(
        SESSION_ID,
        {
            "messages": [{"role": "user", "content": LEGACY_CONTENT}],
            "summary": LEGACY_CONTENT,
        },
    )
    toolkit = _toolkit()
    initial_runtime = KernelMemoryRuntime.from_config(store=store)
    ask_model = _AskModelIO(toolkit)
    suspended = _loop(
        runtime=initial_runtime,
        model_io=ask_model,
    ).run(
        [{"role": "user", "content": "ask for a framework"}],
        session_id=SESSION_ID,
        provider="openai",
        model="gpt-test",
        toolkit=toolkit,
    )

    assert suspended.status == "awaiting_human_input"
    checkpoint = initial_runtime.load_execution_checkpoint(SESSION_ID)
    assert checkpoint is not None
    assert checkpoint["replay_frame"]["complete"] is True
    assert LEGACY_CONTENT not in json.dumps(
        ask_model.requests[0].messages,
        ensure_ascii=False,
    )

    cold_runtime = KernelMemoryRuntime.from_config(store=store)
    resumed_model = _FinalModelIO(toolkit, "resumed")
    completed = _loop(
        runtime=cold_runtime,
        model_io=resumed_model,
    ).resume_interaction(
        session_id=SESSION_ID,
        response={
            "request_id": "call-framework",
            "selected_values": ["react"],
        },
        toolkit=toolkit,
    )

    assert completed.status == "completed"
    assert cold_runtime.last_prepare_info[
        "execution_checkpoint_restored"
    ] is True
    resumed_wire = json.dumps(
        resumed_model.requests[0].messages,
        ensure_ascii=False,
    )
    assert LEGACY_CONTENT not in resumed_wire
    assert "call-framework" in resumed_wire
    assert "function_call_output" in resumed_wire

    sequential_runtime = KernelMemoryRuntime.from_config(store=store)
    sequential_model = _FinalModelIO(toolkit, "next-step")
    next_step = _loop(
        runtime=sequential_runtime,
        model_io=sequential_model,
    ).run(
        [{"role": "user", "content": "start the next step"}],
        session_id=SESSION_ID,
        provider="openai",
        model="gpt-test",
        toolkit=toolkit,
    )

    assert next_step.status == "completed"
    assert sequential_runtime.last_prepare_info[
        "execution_checkpoint_restored"
    ] is False
    assert LEGACY_CONTENT not in json.dumps(
        sequential_model.requests[0].messages,
        ensure_ascii=False,
    )
