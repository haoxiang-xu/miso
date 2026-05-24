from __future__ import annotations

import json
from pathlib import Path

from unchain.kernel import KernelLoop, ModelTurnResult
from unchain.kernel.types import ToolCall as KernelToolCall
from unchain.memory.manager import InMemorySessionStore
from unchain.toolkits import PlanToolkit
from unchain.tools import ToolkitRegistry


class _QueueModelIO:
    provider = "openai"
    model = "gpt-4.1"

    def __init__(self, results):
        self.results = list(results)
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        if not self.results:
            raise AssertionError("unexpected fetch_turn call")
        return self.results.pop(0)


def test_plan_toolkit_lifecycle_renders_markdown_and_proposed_plan():
    toolkit = PlanToolkit()

    started = toolkit.plan_start(
        title="Auth rollout",
        goal="Plan the first authentication rollout.",
        constraints=["Do not change provider protocols."],
    )
    plan_id = started["plan_id"]

    assert started["ok"] is True
    assert started["status"] == "draft"
    assert plan_id == "plan_1"

    updated = toolkit.plan_update(
        plan_id=plan_id,
        summary="Add the smallest planning surface that agents can use safely.",
        steps=[
            {"step": "Create draft", "status": "completed"},
            {"step": "Review plan", "status": "in_progress"},
            {"step": "Finalize plan", "status": "pending"},
        ],
        key_changes=["Add a PlanToolkit builtin."],
        public_interfaces=["PlanToolkit exported from unchain.toolkits."],
        test_cases=["Lifecycle and finalization tests."],
        assumptions=["State is process-local."],
        references=["Codex proposed_plan block."],
        open_questions=["Should v2 persist plans to disk?"],
    )
    assert updated["ok"] is True
    assert updated["revision"] == 2

    read = toolkit.plan_read(plan_id)
    assert read["ok"] is True
    assert read["plan"]["title"] == "Auth rollout"
    assert "## Summary" in read["markdown"]
    assert "- [completed] Create draft" in read["markdown"]
    assert "- [in_progress] Review plan" in read["markdown"]
    assert "- [pending] Finalize plan" in read["markdown"]
    assert "- [x]" not in read["markdown"]
    assert "- [ ]" not in read["markdown"]
    assert "## Public Interfaces" in read["markdown"]
    assert read["artifact"] == {
        "type": "plan_doc",
        "plan_id": plan_id,
        "revision": 2,
        "status": "draft",
        "title": "Auth rollout",
    }
    assert read["artifacts"] == [read["artifact"]]

    finalized = toolkit.plan_finalize(plan_id)
    assert finalized["ok"] is True
    assert finalized["status"] == "finalized"
    assert finalized["proposed_plan"].startswith("<proposed_plan>\n# Auth rollout")
    assert finalized["proposed_plan"].endswith("\n</proposed_plan>")

    listed = toolkit.plan_list()
    assert listed["ok"] is True
    assert listed["plans"] == [
        {
            "plan_id": plan_id,
            "title": "Auth rollout",
            "status": "finalized",
            "revision": 3,
        }
    ]


def test_plan_toolkit_persists_plan_state_in_session_store():
    store = InMemorySessionStore()
    first = PlanToolkit(session_store=store, session_id="thread-1")

    started = first.plan_start(
        title="Standalone plan doc",
        goal="Persist plan state outside chat messages.",
    )
    plan_id = started["plan_id"]
    first.plan_update(
        plan_id=plan_id,
        summary="Render this plan as a separate document.",
        steps=[{"step": "Persist state", "status": "completed"}],
    )

    second = PlanToolkit(session_store=store, session_id="thread-1")
    read = second.plan_read(plan_id)

    assert read["ok"] is True
    assert read["plan"]["summary"] == "Render this plan as a separate document."
    assert "- [completed] Persist state" in read["markdown"]
    assert "- [x]" not in read["markdown"]
    assert "- [ ]" not in read["markdown"]

    state = store.load("thread-1")
    assert state["plans"]["active_plan_id"] == plan_id
    assert state["plans"]["items"][plan_id]["title"] == "Standalone plan doc"
    assert "messages" not in state


def test_plan_toolkit_mirrors_markdown_into_workspace(tmp_path: Path):
    toolkit = PlanToolkit(workspace_root=tmp_path)

    started = toolkit.plan_start(
        title="Workspace backed plan",
        goal="Mirror rendered plans into the selected workspace.",
    )
    plan_id = started["plan_id"]
    plan_file = tmp_path / "plans" / f"{plan_id}.md"

    assert started["ok"] is True
    assert started["workspace_file"] == {
        "path": str(plan_file),
        "relative_path": f"plans/{plan_id}.md",
    }
    assert plan_file.read_text(encoding="utf-8").startswith("# Workspace backed plan\n")

    updated = toolkit.plan_update(
        plan_id=plan_id,
        summary="Plans remain structured in memory but are visible as workspace files.",
        steps=[{"step": "Write Markdown file", "status": "completed"}],
    )

    assert updated["ok"] is True
    assert "- [completed] Write Markdown file" in plan_file.read_text(encoding="utf-8")

    finalized = toolkit.plan_finalize(plan_id)

    assert finalized["ok"] is True
    assert finalized["artifact"]["workspace_file"]["relative_path"] == f"plans/{plan_id}.md"
    assert "Plans remain structured in memory" in plan_file.read_text(encoding="utf-8")


def test_plan_toolkit_reports_unknown_plan_and_invalid_steps():
    toolkit = PlanToolkit()

    assert toolkit.plan_read("missing") == {"ok": False, "plan_id": "missing", "error": "unknown plan_id: missing"}

    plan_id = toolkit.plan_start(title="Invalid steps", goal="Validate step payloads.")["plan_id"]

    bad_status = toolkit.plan_update(plan_id=plan_id, steps=[{"step": "Draft", "status": "blocked"}])
    assert bad_status["ok"] is False
    assert "invalid step status" in bad_status["error"]

    multiple_active = toolkit.plan_update(
        plan_id=plan_id,
        steps=[
            {"step": "One", "status": "in_progress"},
            {"step": "Two", "status": "in_progress"},
        ],
    )
    assert multiple_active["ok"] is False
    assert "at most one step can be in_progress" in multiple_active["error"]


def test_builtin_registry_discovers_plan_toolkit_manifest():
    registry = ToolkitRegistry()
    descriptor = registry.require("plan")

    assert descriptor.name == "Plan"
    assert set(descriptor.tools) == {
        "plan_start",
        "plan_update",
        "plan_read",
        "plan_finalize",
        "plan_list",
    }
    assert descriptor.tools["plan_finalize"].requires_confirmation is True


def test_kernel_runs_plan_tools_and_finalize_requires_confirmation():
    model_io = _QueueModelIO(
        [
            ModelTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": "call_start",
                        "name": "plan_start",
                        "arguments": json.dumps(
                            {
                                "title": "Plan toolkit",
                                "goal": "Create a planning toolkit.",
                            }
                        ),
                    }
                ],
                tool_calls=[
                    KernelToolCall(
                        call_id="call_start",
                        name="plan_start",
                        arguments={"title": "Plan toolkit", "goal": "Create a planning toolkit."},
                    )
                ],
                response_id="resp_1",
            ),
            ModelTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": "call_update",
                        "name": "plan_update",
                        "arguments": json.dumps(
                            {
                                "plan_id": "plan_1",
                                "summary": "Ship the smallest useful version.",
                                "steps": [{"step": "Implement toolkit", "status": "in_progress"}],
                            }
                        ),
                    }
                ],
                tool_calls=[
                    KernelToolCall(
                        call_id="call_update",
                        name="plan_update",
                        arguments={
                            "plan_id": "plan_1",
                            "summary": "Ship the smallest useful version.",
                            "steps": [{"step": "Implement toolkit", "status": "in_progress"}],
                        },
                    )
                ],
                response_id="resp_2",
            ),
            ModelTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": "call_finalize",
                        "name": "plan_finalize",
                        "arguments": json.dumps({"plan_id": "plan_1"}),
                    }
                ],
                tool_calls=[
                    KernelToolCall(
                        call_id="call_finalize",
                        name="plan_finalize",
                        arguments={"plan_id": "plan_1"},
                    )
                ],
                response_id="resp_3",
            ),
            ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "done"}],
                tool_calls=[],
                final_text="done",
                response_id="resp_4",
            ),
        ]
    )
    confirmations = []

    result = KernelLoop(model_io=model_io).run(
        [{"role": "user", "content": "plan this"}],
        provider="openai",
        model="gpt-4.1",
        toolkit=PlanToolkit(),
        max_iterations=5,
        on_tool_confirm=lambda request: confirmations.append(request) or {"approved": True},
    )

    assert result.status == "completed"
    assert len(confirmations) == 1
    assert confirmations[0].tool_name == "plan_finalize"
    finalize_output = [
        json.loads(message["output"])
        for message in result.messages
        if message.get("type") == "function_call_output"
    ][-1]
    assert finalize_output["status"] == "finalized"
    assert "<proposed_plan>" in finalize_output["proposed_plan"]


def test_kernel_plan_finalize_denial_returns_standard_denied_result():
    model_io = _QueueModelIO(
        [
            ModelTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": "call_finalize",
                        "name": "plan_finalize",
                        "arguments": json.dumps({"plan_id": "plan_1"}),
                    }
                ],
                tool_calls=[
                    KernelToolCall(
                        call_id="call_finalize",
                        name="plan_finalize",
                        arguments={"plan_id": "plan_1"},
                    )
                ],
                response_id="resp_1",
            ),
            ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "denied"}],
                tool_calls=[],
                final_text="denied",
                response_id="resp_2",
            ),
        ]
    )

    result = KernelLoop(model_io=model_io).run(
        [{"role": "user", "content": "finalize"}],
        provider="openai",
        model="gpt-4.1",
        toolkit=PlanToolkit(),
        max_iterations=3,
        on_tool_confirm=lambda request: {"approved": False, "reason": "needs changes"},
    )

    denied_output = next(
        json.loads(message["output"])
        for message in result.messages
        if message.get("type") == "function_call_output"
    )
    assert denied_output == {
        "denied": True,
        "tool": "plan_finalize",
        "reason": "needs changes",
    }
