from __future__ import annotations


def _human_input_request():
    from unchain.interaction import HumanInputOption, HumanInputRequest

    return HumanInputRequest(
        request_id="input-1",
        kind="selector",
        title="Choose stack",
        question="Which stack?",
        selection_mode="single",
        options=[
            HumanInputOption(label="React", value="react"),
            HumanInputOption(label="Vue", value="vue", description="Use Vue."),
        ],
        allow_other=True,
        other_label="Other",
        other_placeholder="Type one",
        min_selected=1,
        max_selected=1,
    )


def test_interaction_surface_owns_human_input_effect_helpers():
    from unchain.capabilities import EmitEventOp, RequestSuspendOp
    from unchain.interaction import (
        build_human_input_requested_event,
        build_human_input_suspend_request,
    )

    request = _human_input_request()
    requested = build_human_input_requested_event(request)
    suspended = build_human_input_suspend_request(
        request,
        continuation={"type": "human_input_continuation", "request_id": "input-1"},
    )

    assert isinstance(requested, EmitEventOp)
    assert requested.type == "human_input_requested"
    assert requested.reason == "interaction.requested"
    assert requested.payload == {
        "request_id": "input-1",
        "kind": "selector",
        "title": "Choose stack",
        "question": "Which stack?",
        "selection_mode": "single",
        "options": [
            {"label": "React", "value": "react", "description": ""},
            {"label": "Vue", "value": "vue", "description": "Use Vue."},
        ],
        "allow_other": True,
        "other_label": "Other",
        "other_placeholder": "Type one",
        "min_selected": 1,
        "max_selected": 1,
    }
    assert isinstance(suspended, RequestSuspendOp)
    assert suspended.kind == "human_input"
    assert suspended.reason == "interaction.awaiting_human_input"
    assert suspended.payload == {
        "continuation": {"type": "human_input_continuation", "request_id": "input-1"},
        "request": request.to_dict(),
    }


def test_interaction_surface_builds_human_input_continuation_payload():
    from unchain.interaction import build_human_input_continuation
    from unchain.kernel.state import RunState
    from unchain.schemas import ResponseFormat

    request = _human_input_request()
    state = RunState()
    state.latest_version_id = "version-1"
    state.provider_state.provider = "openai"
    state.provider_state.model = "gpt-test"
    state.provider_state.previous_response_id = "resp-1"
    state.provider_state.use_previous_response_chain = True
    state.provider_state.max_context_window_tokens = 4096
    state.session_state.session_id = "session-1"
    state.session_state.memory_namespace = "memory-1"
    state.token_state.consumed_tokens = 30
    state.token_state.input_tokens = 10
    state.token_state.output_tokens = 20
    state.token_state.last_turn_tokens = 12
    state.token_state.last_turn_input_tokens = 5
    state.token_state.last_turn_output_tokens = 7
    state.workspace_change_state = {"files": [{"path": "app.py"}]}

    continuation = build_human_input_continuation(
        request=request,
        payload={"ok": True, "nested": {"choice": "react"}, "drop": object(), "items": ("a", 1)},
        response_format=ResponseFormat(
            "selection",
            {"type": "object", "required": ["choice"]},
        ),
        next_iteration=3,
        max_iterations=8,
        state=state,
        run_id="run-1",
    )
    state.workspace_change_state["files"][0]["path"] = "mutated.py"

    assert continuation["type"] == "human_input_continuation"
    assert continuation["run_id"] == "run-1"
    assert continuation["provider"] == "openai"
    assert continuation["model"] == "gpt-test"
    assert continuation["request_id"] == "input-1"
    assert continuation["payload"] == {"ok": True, "nested": {"choice": "react"}, "items": ["a", 1]}
    assert continuation["response_format"] == {
        "name": "selection",
        "schema": {"type": "object", "required": ["choice"]},
        "required": ["choice"],
    }
    assert continuation["context_version_id"] == "version-1"
    assert continuation["iteration"] == 3
    assert continuation["max_iterations"] == 8
    assert continuation["previous_response_id"] == "resp-1"
    assert continuation["use_openai_previous_response_chain"] is True
    assert continuation["session_id"] == "session-1"
    assert continuation["memory_namespace"] == "memory-1"
    assert continuation["max_context_window_tokens"] == 4096
    assert continuation["consumed_tokens"] == 30
    assert continuation["input_tokens"] == 10
    assert continuation["output_tokens"] == 20
    assert continuation["last_turn_tokens"] == 12
    assert continuation["last_turn_input_tokens"] == 5
    assert continuation["last_turn_output_tokens"] == 7
    assert continuation["workspace_change_state"] == {"files": [{"path": "app.py"}]}


def test_human_input_resume_harness_lives_under_interaction_boundary():
    from unchain.interaction import HumanInputResumeHarness
    from unchain.tools import HumanInputResumeHarness as LegacyHumanInputResumeHarness

    assert HumanInputResumeHarness is LegacyHumanInputResumeHarness
    assert HumanInputResumeHarness.__module__ == "unchain.interaction.resume"
