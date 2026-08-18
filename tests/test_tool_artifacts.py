from __future__ import annotations

import json
import hashlib
from pathlib import Path

from unchain import artifacts
from unchain.events.bridge import RuntimeEventBridge
from unchain.input import ASK_USER_QUESTION_TOOL_NAME
from unchain.kernel import ModelTurnResult
from unchain.kernel.types import ToolCall as KernelToolCall
from unchain.runtime import build_runtime_loop
from unchain.toolkits.base import BuiltinToolkit
from unchain.toolkits import CoreToolkit, PlanToolkit
from unchain.tools._diff_helpers import build_code_diff_payload
from unchain.tools import Toolkit


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


class _AutoWriteToolkit(BuiltinToolkit):
    def __init__(self, *, workspace_root: Path):
        super().__init__(workspace_root=workspace_root)
        self.register(self.write_raw, name="write_raw")

    def write_raw(self, path: str, content: str) -> dict:
        target = self._resolve_workspace_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(target)}


def _run_tool_turn(
    *,
    tool_calls: list[KernelToolCall],
    toolkit: Toolkit,
    tmp_path: Path,
    callback_events: list[dict] | None = None,
    on_tool_confirm=None,
    max_iterations: int = 2,
    tool_runtime_plugins: list | None = None,
):
    assistant_messages = [
        {
            "type": "function_call",
            "call_id": call.call_id,
            "name": call.name,
            "arguments": json.dumps(call.arguments or {}),
        }
        for call in tool_calls
    ]
    model_io = _QueueModelIO(
        [
            ModelTurnResult(
                assistant_messages=assistant_messages,
                tool_calls=tool_calls,
                response_id="resp_1",
            ),
            ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "done"}],
                tool_calls=[],
                final_text="done",
                response_id="resp_2",
            ),
        ]
    )
    loop = build_runtime_loop(model_io=model_io)
    state = loop.seed_state(
        [{"role": "user", "content": "start"}],
        provider="openai",
        model="gpt-4.1",
        session_id=str(tmp_path),
    )
    state.run_status = "running"
    events = callback_events if callback_events is not None else []

    result = loop._run_state(
        state,
        toolkit=toolkit,
        callback=events.append,
        on_tool_confirm=on_tool_confirm,
        run_id="run-1",
        max_iterations=max_iterations,
        tool_runtime_plugins=tool_runtime_plugins,
    )

    return result, state, events, model_io


def _tool_outputs(result):
    return [
        json.loads(message["output"])
        for message in result.messages
        if message.get("type") == "function_call_output"
    ]


def test_tool_result_artifacts_emit_events_and_are_stripped_from_model_visible_output(tmp_path: Path):
    toolkit = Toolkit()
    toolkit.register(
        lambda: {
            "ok": True,
            "_artifacts": [
                artifacts.markdown(
                    "Benchmark report",
                    "# Report\nLooks good.",
                    source_path="reports/latest.md",
                    artifact_id="benchmark-report",
                )
            ],
        },
        name="report_tool",
    )

    result, state, raw_events, _ = _run_tool_turn(
        tool_calls=[KernelToolCall(call_id="call-1", name="report_tool", arguments={})],
        toolkit=toolkit,
        tmp_path=tmp_path,
    )

    assert _tool_outputs(result) == [{"ok": True}]
    tool_completed = next(event for event in raw_events if event["type"] == "tool_result")
    assert tool_completed["result"] == {"ok": True}
    artifact_event = next(event for event in raw_events if event["type"] == "artifact_created")
    assert artifact_event["artifact_id"] == "benchmark-report"
    assert artifact_event["artifact"]["snapshot"]["markdown"] == "# Report\nLooks good."
    assert state.artifacts == [artifact_event["artifact"]]

    bridge = RuntimeEventBridge(session_id="thread-1", root_run_id="run-1")
    [runtime_event] = bridge.normalize(artifact_event)
    assert runtime_event.type == "artifact.created"
    assert runtime_event.links.artifact_id == runtime_event.payload["artifact_id"]
    assert runtime_event.payload == artifact_event["artifact"]


def test_repeated_artifact_id_emits_created_then_updated_and_stores_latest(tmp_path: Path):
    calls = {"count": 0}

    def report_tool():
        calls["count"] += 1
        return {
            "ok": True,
            "_artifacts": [
                artifacts.markdown(
                    "Report",
                    f"revision {calls['count']}",
                    artifact_id="stable-report",
                )
            ],
        }

    toolkit = Toolkit()
    toolkit.register(report_tool, name="report_tool")

    _, state, raw_events, _ = _run_tool_turn(
        tool_calls=[
            KernelToolCall(call_id="call-1", name="report_tool", arguments={}),
            KernelToolCall(call_id="call-2", name="report_tool", arguments={}),
        ],
        toolkit=toolkit,
        tmp_path=tmp_path,
    )

    artifact_events = [event for event in raw_events if event["type"].startswith("artifact_")]
    assert [event["type"] for event in artifact_events] == ["artifact_created", "artifact_updated"]
    assert artifact_events[0]["artifact"]["revision"] == 1
    assert artifact_events[1]["artifact"]["revision"] == 2
    assert state.artifacts == [artifact_events[1]["artifact"]]
    assert state.artifacts[0]["snapshot"]["markdown"] == "revision 2"


def test_failed_tool_strips_artifacts_but_emits_no_artifact_event(tmp_path: Path):
    toolkit = Toolkit()
    toolkit.register(
        lambda: {
            "error": "boom",
            "_artifacts": [artifacts.markdown("Hidden", "should not emit")],
        },
        name="failing_tool",
    )

    result, state, raw_events, _ = _run_tool_turn(
        tool_calls=[KernelToolCall(call_id="call-1", name="failing_tool", arguments={})],
        toolkit=toolkit,
        tmp_path=tmp_path,
    )

    assert _tool_outputs(result) == [{"error": "boom"}]
    assert not [event for event in raw_events if event["type"].startswith("artifact_")]
    assert state.artifacts == []


def test_ok_false_tool_result_emits_no_artifact_event(tmp_path: Path):
    toolkit = Toolkit()
    toolkit.register(
        lambda: {
            "ok": False,
            "_artifacts": [artifacts.markdown("Hidden", "should not emit")],
        },
        name="not_ok_tool",
    )

    result, state, raw_events, _ = _run_tool_turn(
        tool_calls=[KernelToolCall(call_id="call-1", name="not_ok_tool", arguments={})],
        toolkit=toolkit,
        tmp_path=tmp_path,
    )

    assert _tool_outputs(result) == [{"ok": False}]
    assert not [event for event in raw_events if event["type"].startswith("artifact_")]
    assert state.artifacts == []


def test_core_write_emits_append_only_file_diff_artifact(tmp_path: Path):
    toolkit = CoreToolkit(workspace_root=tmp_path)

    _, state, raw_events, _ = _run_tool_turn(
        tool_calls=[
            KernelToolCall(
                call_id="call-1",
                name="write",
                arguments={"path": str(tmp_path / "app.py"), "content": "print('hi')\n"},
            )
        ],
        toolkit=toolkit,
        tmp_path=tmp_path,
    )

    artifact_event = next(event for event in raw_events if event["type"] == "artifact_created")
    artifact = artifact_event["artifact"]
    assert artifact["kind"] == "file_diff"
    assert artifact["artifact_id"].startswith("file_diff:call-1")
    assert artifact["snapshot"]["files"][0]["path"] == str(tmp_path / "app.py")
    assert "+print('hi')" in artifact["snapshot"]["files"][0]["unified_diff"]
    assert artifact in state.artifacts
    assert any(item["kind"] == "workspace_change_set" for item in state.artifacts)


def test_plugin_executed_write_still_emits_file_diff_artifact(tmp_path: Path):
    """The sidecar executes workspace tools through a tool runtime plugin;
    the approved code_diff confirmation policy must still derive the
    file_diff artifact on that path, exactly as on native execution."""

    from unchain.tools.runtime import ToolRuntimeOutcome

    toolkit = CoreToolkit(workspace_root=tmp_path)
    target = tmp_path / "app.py"

    class _PassthroughWritePlugin:
        def can_handle(self, *, tool_call, context):
            return tool_call.name == "write"

        def execute(self, *, tool_call, context):
            path = Path(tool_call.arguments["path"])
            path.write_text(tool_call.arguments["content"], encoding="utf-8")
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={"ok": True, "path": str(path)},
            )

    _, _state, raw_events, _ = _run_tool_turn(
        tool_calls=[
            KernelToolCall(
                call_id="call-1",
                name="write",
                arguments={
                    "path": str(target),
                    "content": "print('hi')\n",
                },
            )
        ],
        toolkit=toolkit,
        tmp_path=tmp_path,
        tool_runtime_plugins=[_PassthroughWritePlugin()],
    )

    artifact_event = next(
        (event for event in raw_events if event["type"] == "artifact_created"),
        None,
    )
    assert artifact_event is not None
    artifact = artifact_event["artifact"]
    assert artifact["kind"] == "file_diff"
    assert artifact["snapshot"]["files"][0]["path"] == str(target)
    assert "+print('hi')" in artifact["snapshot"]["files"][0]["unified_diff"]


def test_core_write_and_edit_emit_one_run_level_workspace_change_set(tmp_path: Path):
    toolkit = CoreToolkit(workspace_root=tmp_path)
    target = tmp_path / "app.py"

    _, state, raw_events, _ = _run_tool_turn(
        tool_calls=[
            KernelToolCall(
                call_id="call-write",
                name="write",
                arguments={"path": str(target), "content": "print('one')\n"},
            ),
            KernelToolCall(
                call_id="call-edit",
                name="edit",
                arguments={
                    "path": str(target),
                    "old_string": "one",
                    "new_string": "two",
                },
            ),
        ],
        toolkit=toolkit,
        tmp_path=tmp_path,
    )

    workspace_events = [
        event
        for event in raw_events
        if event["type"].startswith("artifact_")
        and event.get("artifact", {}).get("kind") == "workspace_change_set"
    ]
    assert len(workspace_events) == 1

    artifact = workspace_events[0]["artifact"]
    assert artifact["artifact_id"] == "workspace_change_set:run-1"
    assert artifact["presentation"]["surface"] == "run_summary"
    assert artifact["snapshot"]["totals"]["files"] == 1
    assert artifact["snapshot"]["files"][0]["status"] == "created"
    assert "+print('two')" in artifact["snapshot"]["files"][0]["unified_diff"]
    assert artifact["snapshot"]["undo"]["supported"] is True
    assert [
        operation["call_id"]
        for operation in state.workspace_change_state["files"][str(target)]["operations"]
    ] == ["call-write", "call-edit"]
    assert state.workspace_change_state["files"][str(target)]["latest_after"]["text"] == "print('two')\n"


def test_builtin_tool_file_mutations_are_auto_tracked_without_authored_artifact(tmp_path: Path):
    toolkit = _AutoWriteToolkit(workspace_root=tmp_path)
    target = tmp_path / "generated.txt"

    _, state, raw_events, _ = _run_tool_turn(
        tool_calls=[
            KernelToolCall(
                call_id="call-auto",
                name="write_raw",
                arguments={"path": str(target), "content": "auto tracked\n"},
            )
        ],
        toolkit=toolkit,
        tmp_path=tmp_path,
    )

    workspace_event = next(
        event
        for event in raw_events
        if event["type"].startswith("artifact_")
        and event.get("artifact", {}).get("kind") == "workspace_change_set"
    )
    artifact = workspace_event["artifact"]

    assert artifact["artifact_id"] == "workspace_change_set:run-1"
    assert artifact["snapshot"]["totals"]["files"] == 1
    assert artifact["snapshot"]["files"][0]["relative_path"] == "generated.txt"
    assert artifact["snapshot"]["files"][0]["status"] == "created"
    assert "+auto tracked" in artifact["snapshot"]["files"][0]["unified_diff"]
    assert state.workspace_change_state["files"][str(target)]["latest_after"]["text"] == "auto tracked\n"
    assert state.workspace_change_state["files"][str(target)]["operations"][0] == {
        "turn_id": "run-1:turn-0",
        "tool_name": "write_raw",
        "call_id": "call-auto",
        "operation": "created",
    }


def test_workspace_change_set_emits_when_run_stops_at_max_iterations(tmp_path: Path):
    toolkit = CoreToolkit(workspace_root=tmp_path)
    target = tmp_path / "app.py"

    result, state, raw_events, _ = _run_tool_turn(
        tool_calls=[
            KernelToolCall(
                call_id="call-write",
                name="write",
                arguments={"path": str(target), "content": "print('one')\n"},
            )
        ],
        toolkit=toolkit,
        tmp_path=tmp_path,
        max_iterations=1,
    )

    workspace_event = next(
        event
        for event in raw_events
        if event["type"].startswith("artifact_")
        and event.get("artifact", {}).get("kind") == "workspace_change_set"
    )
    run_max_index = next(index for index, event in enumerate(raw_events) if event["type"] == "run_max_iterations")
    workspace_index = raw_events.index(workspace_event)

    assert result.status == "max_iterations"
    assert workspace_index < run_max_index
    assert workspace_event["artifact"]["presentation"]["surface"] == "run_summary"
    assert workspace_event["artifact"] in state.artifacts


def test_workspace_change_state_survives_human_input_resume(tmp_path: Path):
    target = tmp_path / "app.py"
    ask_arguments = {
        "title": "Continue",
        "question": "Continue?",
        "selection_mode": "single",
        "options": [{"label": "Continue", "value": "continue"}],
    }
    toolkit = CoreToolkit(workspace_root=tmp_path)
    initial_loop = build_runtime_loop(
        model_io=_QueueModelIO(
            [
                ModelTurnResult(
                    assistant_messages=[
                        {
                            "type": "function_call",
                            "call_id": "call-write",
                            "name": "write",
                            "arguments": json.dumps(
                                {"path": str(target), "content": "print('one')\n"}
                            ),
                        }
                    ],
                    tool_calls=[
                        KernelToolCall(
                            call_id="call-write",
                            name="write",
                            arguments={"path": str(target), "content": "print('one')\n"},
                        )
                    ],
                    response_id="resp_write",
                ),
                ModelTurnResult(
                    assistant_messages=[
                        {
                            "type": "function_call",
                            "call_id": "call-user",
                            "name": ASK_USER_QUESTION_TOOL_NAME,
                            "arguments": json.dumps(ask_arguments),
                        }
                    ],
                    tool_calls=[
                        KernelToolCall(
                            call_id="call-user",
                            name=ASK_USER_QUESTION_TOOL_NAME,
                            arguments=ask_arguments,
                        )
                    ],
                    response_id="resp_ask",
                ),
            ]
        )
    )

    suspended = initial_loop.run(
        [{"role": "user", "content": "start"}],
        toolkit=toolkit,
        run_id="run-1",
        max_iterations=4,
    )

    assert suspended.status == "awaiting_human_input"
    assert suspended.continuation["run_id"] == "run-1"
    assert suspended.continuation["workspace_change_state"]["files"][str(target)]["latest_after"]["text"] == (
        "print('one')\n"
    )

    resumed_events: list[dict] = []
    resumed_loop = build_runtime_loop(
        model_io=_QueueModelIO(
            [
                ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": "done"}],
                    tool_calls=[],
                    final_text="done",
                    response_id="resp_done",
                )
            ]
        )
    )
    resumed = resumed_loop.resume_human_input(
        conversation=suspended.messages,
        continuation=suspended.continuation,
        response={"request_id": "call-user", "selected_values": ["continue"]},
        toolkit=toolkit,
        callback=resumed_events.append,
    )

    workspace_event = next(
        event
        for event in resumed_events
        if event["type"].startswith("artifact_")
        and event.get("artifact", {}).get("kind") == "workspace_change_set"
    )
    assert resumed.status == "completed"
    assert workspace_event["artifact"]["artifact_id"] == "workspace_change_set:run-1"
    assert workspace_event["artifact"]["snapshot"]["files"][0]["relative_path"] == "app.py"
    assert "+print('one')" in workspace_event["artifact"]["snapshot"]["files"][0]["unified_diff"]


def test_core_write_large_file_diff_artifact_hashes_full_diff(tmp_path: Path):
    toolkit = CoreToolkit(workspace_root=tmp_path)
    target = tmp_path / "big.py"
    content = "\n".join(f"line {index}" for index in range(500)) + "\n"

    _, state, raw_events, _ = _run_tool_turn(
        tool_calls=[
            KernelToolCall(
                call_id="call-large",
                name="write",
                arguments={"path": str(target), "content": content},
            )
        ],
        toolkit=toolkit,
        tmp_path=tmp_path,
    )

    expected_payload = build_code_diff_payload(
        str(target),
        "",
        content,
        "create",
        max_lines=10_000,
    )
    assert expected_payload is not None
    artifact_event = next(event for event in raw_events if event["type"] == "artifact_created")
    artifact = artifact_event["artifact"]
    file_snapshot = artifact["snapshot"]["files"][0]
    displayed_diff = file_snapshot["unified_diff"]
    expected_hash = hashlib.sha256(expected_payload["unified_diff"].encode("utf-8")).hexdigest()

    assert artifact["snapshot"]["truncated"] is True
    assert artifact["snapshot"]["total_lines"] == expected_payload["total_lines"]
    assert artifact["snapshot"]["displayed_lines"] == 400
    assert artifact["snapshot"]["sha256"] == expected_hash
    assert artifact["snapshot"]["sha256"] != hashlib.sha256(displayed_diff.encode("utf-8")).hexdigest()
    assert file_snapshot["truncated"] is True
    assert file_snapshot["total_lines"] == expected_payload["total_lines"]
    assert file_snapshot["displayed_lines"] == 400
    assert artifact in state.artifacts
    assert any(item["kind"] == "workspace_change_set" for item in state.artifacts)


def test_modified_write_confirmation_artifact_matches_effective_arguments(tmp_path: Path):
    toolkit = CoreToolkit(workspace_root=tmp_path)
    target = tmp_path / "modified.py"

    _, state, raw_events, _ = _run_tool_turn(
        tool_calls=[
            KernelToolCall(
                call_id="call-modified",
                name="write",
                arguments={"path": str(target), "content": "original\n"},
            )
        ],
        toolkit=toolkit,
        tmp_path=tmp_path,
        on_tool_confirm=lambda request: {
            "approved": True,
            "modified_arguments": {"path": str(target), "content": "modified\n"},
        },
    )

    assert target.read_text(encoding="utf-8") == "modified\n"
    artifact_event = next(event for event in raw_events if event["type"] == "artifact_created")
    artifact = artifact_event["artifact"]
    unified_diff = artifact["snapshot"]["files"][0]["unified_diff"]
    assert "+modified" in unified_diff
    assert "+original" not in unified_diff
    assert artifact in state.artifacts
    assert any(item["kind"] == "workspace_change_set" for item in state.artifacts)


def test_plan_tools_emit_stable_plan_artifact_updates(tmp_path: Path):
    toolkit = PlanToolkit(workspace_root=tmp_path)

    _, state, raw_events, _ = _run_tool_turn(
        tool_calls=[
            KernelToolCall(
                call_id="call-1",
                name="plan_start",
                arguments={"title": "Artifact plan", "goal": "Emit a plan artifact."},
            ),
            KernelToolCall(
                call_id="call-2",
                name="plan_update",
                arguments={
                    "plan_id": "plan_1",
                    "summary": "Use stable plan artifact ids.",
                    "steps": [{"step": "Emit artifacts", "status": "completed"}],
                },
            ),
        ],
        toolkit=toolkit,
        tmp_path=tmp_path,
    )

    artifact_events = [event for event in raw_events if event["type"].startswith("artifact_")]
    assert [event["type"] for event in artifact_events] == ["artifact_created", "artifact_updated"]
    assert artifact_events[0]["artifact_id"] == "plan:plan_1"
    assert artifact_events[1]["artifact_id"] == "plan:plan_1"
    assert artifact_events[1]["plan_id"] == "plan_1"
    assert artifact_events[1]["artifact"]["kind"] == "plan"
    assert artifact_events[1]["artifact"]["snapshot"]["plan_id"] == "plan_1"
    assert artifact_events[1]["artifact"]["snapshot"]["status"] == "draft"
    assert artifact_events[1]["artifact"]["snapshot"]["revision"] == 2
    assert "# Artifact plan" in artifact_events[1]["artifact"]["snapshot"]["markdown"]
    assert state.artifacts == [artifact_events[1]["artifact"]]
