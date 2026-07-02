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


def test_human_input_resume_harness_lives_under_interaction_boundary():
    from unchain.interaction import HumanInputResumeHarness
    from unchain.tools import HumanInputResumeHarness as LegacyHumanInputResumeHarness

    assert HumanInputResumeHarness is LegacyHumanInputResumeHarness
    assert HumanInputResumeHarness.__module__ == "unchain.interaction.resume"
