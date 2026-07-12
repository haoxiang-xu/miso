from __future__ import annotations

import copy
import json
from pathlib import Path

from unchain.agent import Agent, ToolsModule
from unchain.kernel import ModelTurnResult, ToolCall
from unchain.toolkits import CoreToolkit

from tests.evals.cases import build_eval_case, list_eval_cases
from tests.evals.runner import _run_candidate_case, _run_judge
from tests.evals.types import ModelSpec, RunArtifact


REPO_ROOT = Path(__file__).resolve().parents[1]


class _ScriptedModelIO:
    provider = "openai"
    model = "gpt-5"

    def __init__(self) -> None:
        self.calls = 0
        self.requests = []

    def fetch_turn(self, request):
        self.calls += 1
        self.requests.append(request)
        if request.callback is not None:
            request.callback(
                {
                    "type": "request_messages",
                    "messages": copy.deepcopy(request.messages),
                }
            )
        if self.calls == 1:
            return ModelTurnResult(
                assistant_messages=[
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_glob",
                                "type": "function",
                                "name": "glob",
                                "arguments": json.dumps({"pattern": "**/*.py"}),
                            }
                        ],
                    }
                ],
                tool_calls=[
                    ToolCall(
                        call_id="call_glob",
                        name="glob",
                        arguments={"pattern": "**/*.py"},
                    )
                ],
                response_id="resp_glob",
                consumed_tokens=5,
                input_tokens=3,
                output_tokens=2,
            )
        return ModelTurnResult(
            assistant_messages=[
                {
                    "role": "assistant",
                    "content": "Inspected the workspace with glob and completed the offline eval.",
                }
            ],
            tool_calls=[],
            final_text="Inspected the workspace with glob and completed the offline eval.",
            response_id="resp_done",
            consumed_tokens=4,
            input_tokens=2,
            output_tokens=2,
        )


class _OfflineCurrentAgent(Agent):
    modules_seen = ()
    model_io: _ScriptedModelIO | None = None

    def __init__(self, **kwargs) -> None:
        assert "tools" not in kwargs
        type(self).modules_seen = tuple(kwargs.get("modules") or ())
        model_io = _ScriptedModelIO()
        type(self).model_io = model_io
        super().__init__(
            **kwargs,
            model_io_factory=lambda spec, context: model_io,
        )


class _ScriptedJudgeModelIO:
    provider = "openai"
    model = "gpt-5"

    def fetch_turn(self, request):
        if request.callback is not None:
            request.callback(
                {
                    "type": "request_messages",
                    "messages": copy.deepcopy(request.messages),
                }
            )
        content = json.dumps(
            {
                "overall_score": 93,
                "rubric_scores": {
                    "correctness": 94,
                    "debugging": 92,
                    "tool_strategy": 91,
                    "efficiency": 95,
                },
                "summary": "Current KernelRunResult judge path works.",
                "failure_modes": [],
                "debug_notes": [],
                "recommendations": [],
                "prompt_suggestions": [],
                "tooling_suggestions": [],
            }
        )
        return ModelTurnResult(
            assistant_messages=[{"role": "assistant", "content": content}],
            tool_calls=[],
            final_text=content,
            response_id="resp_judge",
            consumed_tokens=6,
            input_tokens=4,
            output_tokens=2,
        )


class _OfflineJudgeAgent(Agent):
    def __init__(self, **kwargs) -> None:
        model_io = _ScriptedJudgeModelIO()
        super().__init__(
            **kwargs,
            model_io_factory=lambda spec, context: model_io,
        )


def test_candidate_eval_runs_through_current_agent_and_kernel_result_api():
    case = build_eval_case(
        case_id="current_agent_smoke",
        title="Current Agent Smoke",
        task_prompt="Inspect the workspace with the available tools.",
        workspace_mode="fixture_copy",
        workspace_source="tests/evals/fixtures/fixture_debug",
        allowed_toolkits=["core"],
        rule_checks={
            "required_tool_names": ["glob"],
            "min_tool_calls": 1,
        },
    )
    model_spec = ModelSpec(
        provider="openai",
        model="gpt-5",
        label="offline-gpt-5",
    )

    artifact = _run_candidate_case(
        repo_root=REPO_ROOT,
        suite_id="offline-suite",
        case=case,
        model_spec=model_spec,
        api_key="offline-key",
        max_iterations=3,
        agent_cls=_OfflineCurrentAgent,
    )

    assert artifact.status == "completed"
    assert artifact.error is None
    assert artifact.tool_usage["by_tool"] == {"glob": 1}
    assert artifact.tool_usage["workspace_changes"] == []
    assert artifact.tool_usage["failed_calls"] == []
    assert artifact.token_usage["consumed_tokens"] == 9
    assert artifact.token_usage["input_tokens"] == 5
    assert artifact.token_usage["output_tokens"] == 4
    assert artifact.token_usage["last_turn_tokens"] == 4
    assert artifact.token_usage["last_turn_input_tokens"] == 2
    assert artifact.token_usage["last_turn_output_tokens"] == 2
    assert artifact.token_usage["cache_read_input_tokens"] == 0
    assert artifact.token_usage["cache_creation_input_tokens"] == 0
    assert artifact.token_usage["max_context_window_tokens"] > 0
    assert artifact.bundle["iteration"] == 2
    assert any(isinstance(module, ToolsModule) for module in _OfflineCurrentAgent.modules_seen)
    assert _OfflineCurrentAgent.model_io is not None
    assert len(_OfflineCurrentAgent.model_io.requests) == 2
    tool_results = [
        event
        for event in artifact.callback_events
        if event.get("type") == "tool_result" and event.get("tool_name") == "glob"
    ]
    assert len(tool_results) == 1
    assert tool_results[0]["result"]["match_count"] >= 1
    assert any(
        str(path).endswith("/src/reporting.py")
        for path in tool_results[0]["result"]["matches"]
    )
    request_events = [
        event for event in artifact.callback_events if event.get("type") == "request_messages"
    ]
    assert len(request_events) == 2
    assert "src/reporting.py" in json.dumps(request_events[1]["messages"])


def test_builtin_eval_case_tool_rules_match_current_core_tool_inventory():
    inventory = set(CoreToolkit(workspace_root=REPO_ROOT).tools)

    for case in list_eval_cases(REPO_ROOT):
        rule_checks = case.rule_checks or {}
        configured_names = {
            str(name)
            for name in rule_checks.get("required_tool_names", [])
        }
        configured_names.update(
            str(name)
            for group in rule_checks.get("required_tool_any_of", [])
            for name in group
        )
        configured_names.update(
            str(name)
            for name in rule_checks.get("forbidden_tool_names", [])
        )

        assert configured_names <= inventory, (
            f"eval case {case.id!r} references unavailable tools: "
            f"{sorted(configured_names - inventory)}"
        )


def test_judge_eval_runs_through_current_agent_and_kernel_result_api(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-key")
    case = build_eval_case(
        case_id="current_judge_smoke",
        title="Current Judge Smoke",
        task_prompt="Inspect the workspace.",
        workspace_mode="fixture_copy",
        workspace_source="tests/evals/fixtures/fixture_debug",
    )
    run_artifact = RunArtifact(
        run_id="current_judge_smoke__offline",
        suite_id="offline-suite",
        case_id=case.id,
        case_title=case.title,
        provider="openai",
        model="gpt-5",
        model_label="offline-candidate",
        status="completed",
        started_at="2026-07-11T00:00:00+00:00",
        duration_seconds=0.1,
        workspace_mode=case.workspace_mode,
        workspace_source=case.workspace_source,
        final_answer="Inspected the workspace.",
        messages=[{"role": "assistant", "content": "Inspected the workspace."}],
    )

    report = _run_judge(
        repo_root=REPO_ROOT,
        case=case,
        run_artifact=run_artifact,
        judge_model_spec=ModelSpec(
            provider="openai",
            model="gpt-5",
            label="offline-judge",
        ),
        rubric_weights=case.rubric_weights,
        judge_agent_cls=_OfflineJudgeAgent,
    )

    assert report.status == "completed"
    assert report.overall_score == 93
    assert report.raw_bundle["previous_response_id"] == "resp_judge"
    assert report.raw_bundle["consumed_tokens"] == 6
