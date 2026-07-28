from __future__ import annotations

import json
import os
import shutil
from types import SimpleNamespace

import pytest

from unchain import Agent
from unchain.agent import (
    JobsModule,
    MemoryModule,
    ToolOptimizerModule,
    ToolsModule,
)
from unchain.interaction.durable import InteractionIntegrityError
from unchain.jobs import (
    DurableJobOwnershipError,
    DurableJobSnapshot,
    DurableShellJobPlugin,
    JobEnvironmentProfile,
    JsonFileJobStore,
    ProcessJobSupervisor,
)
from unchain.kernel import ModelTurnResult
from unchain.kernel.types import ToolCall
from unchain.interaction.runtime import DurableInteractionRuntime
from unchain.memory import JsonFileSessionStore, KernelMemoryRuntime
from unchain.runtime import build_runtime_loop
from unchain.toolkits import CoreToolkit
from unchain.tools import ToolOptimizerConfig, Toolkit
from unchain.tools.runtime import snapshot_durable_tool_runtime_route


class _RecordingSupervisor:
    def __init__(self) -> None:
        self.starts: list[dict] = []
        self.environment_profile = JobEnvironmentProfile.capture()

    def start(self, **kwargs) -> DurableJobSnapshot:
        self.starts.append(kwargs)
        return DurableJobSnapshot(
            job_id="job_0123456789abcdef0123456789abcdef",
            execution_id=kwargs["execution_id"],
            adapter=kwargs["adapter"],
            status="queued",
            intent_digest=kwargs["intent_digest"],
            environment_digest=self.environment_profile.digest,
            created_at_ms=1,
            updated_at_ms=1,
        )

    def poll(self, **kwargs):
        raise DurableJobOwnershipError(
            f"durable job not found: {kwargs['job_id']}"
        )


def _context(toolkit: CoreToolkit, confirmation_response) -> SimpleNamespace:
    event = {"on_tool_confirm": lambda request: confirmation_response}
    return SimpleNamespace(
        toolkit=toolkit,
        session_id="session-durable-plugin",
        run_id="run-durable-plugin",
        provider="openai",
        model="gpt-5",
        iteration=1,
        memory_namespace="",
        event=event,
        raw_event=event,
        execution_guard=None,
        loop=None,
        callback=None,
    )


def test_durable_shell_plugin_preserves_denial_and_modified_arguments(tmp_path) -> None:
    toolkit = CoreToolkit(workspace_root=tmp_path)
    supervisor = _RecordingSupervisor()
    plugin = DurableShellJobPlugin(supervisor=supervisor)
    original = ToolCall(
        call_id="call-background",
        name="shell",
        arguments={
            "action": "run",
            "command": "echo original",
            "run_in_background": True,
        },
    )
    try:
        denied = plugin.execute(
            tool_call=original,
            context=_context(
                toolkit,
                {"approved": False, "reason": "not now"},
            ),
        )
        assert denied.tool_result == {
            "denied": True,
            "tool": "shell",
            "reason": "not now",
        }
        assert supervisor.starts == []

        modified = plugin.execute(
            tool_call=original,
            context=_context(
                toolkit,
                {
                    "approved": True,
                    "modified_arguments": {
                        "action": "run",
                        "command": "echo modified",
                        "cwd": str(tmp_path),
                        "timeout_ms": 5_000,
                        "run_in_background": True,
                    },
                },
            ),
        )
        assert modified.tool_result["ok"] is True
        assert modified.tool_result["status"] == "queued"
        assert modified.tool_result["task_id"].startswith("job_")
        assert len(supervisor.starts) == 1
        assert supervisor.starts[0]["idempotency_key"] == "shell:call-background"
        assert supervisor.starts[0]["argv"][-1] == "echo modified"

        no_longer_background = plugin.execute(
            tool_call=original,
            context=_context(
                toolkit,
                {
                    "approved": True,
                    "modified_arguments": {
                        "action": "run",
                        "command": "echo foreground",
                        "run_in_background": False,
                    },
                },
            ),
        )
        assert no_longer_background.tool_result["status"] == "error"
        assert "run_in_background=true" in no_longer_background.tool_result["error"]
        assert len(supervisor.starts) == 1
    finally:
        toolkit.shutdown()


def test_durable_shell_plugin_hides_foreign_job_as_missing(tmp_path) -> None:
    toolkit = CoreToolkit(workspace_root=tmp_path)
    plugin = DurableShellJobPlugin(supervisor=_RecordingSupervisor())
    job_id = "job_fedcba9876543210fedcba9876543210"
    tool_call = ToolCall(
        call_id="call-poll",
        name="shell",
        arguments={"action": "poll", "task_id": job_id},
    )
    try:
        context = _context(toolkit, True)
        assert plugin.can_handle(tool_call=tool_call, context=context) is True
        outcome = plugin.execute(tool_call=tool_call, context=context)
        assert outcome.tool_result["status"] == "missing"
        assert outcome.tool_result["task_id"] == job_id
        assert outcome.tool_result["completed"] is True
    finally:
        toolkit.shutdown()


def test_durable_background_start_without_session_fails_closed(tmp_path) -> None:
    toolkit = CoreToolkit(workspace_root=tmp_path)
    supervisor = _RecordingSupervisor()
    plugin = DurableShellJobPlugin(supervisor=supervisor)
    tool_call = ToolCall(
        call_id="call-no-session",
        name="shell",
        arguments={
            "action": "run",
            "command": "echo must-not-run",
            "run_in_background": True,
        },
    )
    try:
        context = _context(toolkit, True)
        context.session_id = ""
        assert plugin.can_handle(tool_call=tool_call, context=context) is True
        outcome = plugin.execute(tool_call=tool_call, context=context)
        assert outcome.tool_result["status"] == "error"
        assert "session_id" in outcome.tool_result["error"]
        assert supervisor.starts == []
    finally:
        toolkit.shutdown()


class _QueueModelIO:
    provider = "openai"
    model = "gpt-5"

    def __init__(self, turns: list[ModelTurnResult]) -> None:
        self.turns = list(turns)
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        if not self.turns:
            raise AssertionError("unexpected model turn")
        return self.turns.pop(0)


def test_agent_jobs_module_routes_shell_and_a_fresh_supervisor_reattaches(
    tmp_path,
) -> None:
    state_dir = tmp_path / "job-state"
    toolkit = CoreToolkit(workspace_root=tmp_path)
    supervisor = ProcessJobSupervisor(JsonFileJobStore(state_dir))
    shell_call = ToolCall(
        call_id="call-agent-background",
        name="shell",
        arguments={
            "action": "run",
            "command": "printf durable-agent-e2e",
            "run_in_background": True,
            "yield_time_ms": 0,
        },
    )
    model_io = _QueueModelIO(
        [
            ModelTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": shell_call.call_id,
                        "name": shell_call.name,
                        "arguments": json.dumps(shell_call.arguments),
                    }
                ],
                tool_calls=[shell_call],
                response_id="response-shell",
            ),
            ModelTurnResult(
                assistant_messages=[{"role": "assistant", "content": "started"}],
                tool_calls=[],
                final_text="started",
                response_id="response-final",
            ),
        ]
    )
    agent = Agent(
        name="durable-agent-test",
        provider="openai",
        model="gpt-5",
        modules=(
            ToolsModule(tools=(toolkit,)),
            JobsModule(supervisor=supervisor),
        ),
        model_io_factory=lambda _spec, _context: model_io,
    )
    job_id = ""
    fresh: ProcessJobSupervisor | None = None
    try:
        result = agent.run(
            "start the background command",
            session_id="session-agent-durable-job",
            on_tool_confirm=lambda request: True,
            max_iterations=3,
        )
        assert result.status == "completed"
        tool_message = next(
            message
            for message in result.messages
            if message.get("type") == "function_call_output"
        )
        started = json.loads(tool_message["output"])
        assert started["ok"] is True
        assert started["durable"] is True
        job_id = started["job_id"]
        assert job_id.startswith("job_")

        supervisor.close()
        fresh = ProcessJobSupervisor(JsonFileJobStore(state_dir))
        assert [item.job_id for item in fresh.reattach("session-agent-durable-job")] == [
            job_id
        ]
        completed = fresh.wait(
            job_id,
            execution_id="session-agent-durable-job",
            timeout_ms=5_000,
        )
        assert completed.status == "completed"
        assert completed.stdout == "durable-agent-e2e"
    finally:
        if fresh is not None and job_id:
            try:
                fresh.cancel(
                    job_id,
                    execution_id="session-agent-durable-job",
                    wait_timeout_ms=1_000,
                )
            except Exception:
                pass
            fresh.close()
        supervisor.close()
        toolkit.shutdown()


def test_durable_approval_cold_resume_starts_the_job_once(tmp_path) -> None:
    session_id = "session-cold-job-approval"
    memory_dir = tmp_path / "memory"
    state_dir = tmp_path / "jobs"
    first_toolkit = CoreToolkit(workspace_root=tmp_path)
    first_supervisor = ProcessJobSupervisor(JsonFileJobStore(state_dir))
    shell_call = ToolCall(
        call_id="call-cold-background",
        name="shell",
        arguments={
            "action": "run",
            "command": "printf durable-cold-approval",
            "run_in_background": True,
            "yield_time_ms": 0,
        },
    )
    first_model = _QueueModelIO(
        [
            ModelTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": shell_call.call_id,
                        "name": shell_call.name,
                        "arguments": json.dumps(shell_call.arguments),
                    }
                ],
                tool_calls=[shell_call],
                response_id="response-cold-shell",
            )
        ]
    )
    first_loop = build_runtime_loop(
        model_io=first_model,
        memory_runtime=KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(memory_dir)
        ),
    )
    fresh_supervisor: ProcessJobSupervisor | None = None
    fresh_toolkit: CoreToolkit | None = None
    job_id = ""
    try:
        suspended = first_loop.run(
            [{"role": "user", "content": "start it"}],
            session_id=session_id,
            provider="openai",
            model="gpt-5",
            toolkit=first_toolkit,
            tool_runtime_plugins=[
                DurableShellJobPlugin(supervisor=first_supervisor)
            ],
            max_iterations=3,
        )
        assert suspended.status == "awaiting_interaction"
        assert suspended.interaction_request is not None
        assert first_supervisor.reattach(session_id) == []

        memory = KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(memory_dir)
        )
        interaction = DurableInteractionRuntime(memory)
        pending = interaction.load_active(session_id)
        interaction.record_receipt(
            session_id,
            interaction_id=pending.request.interaction_id,
            response={"approved": True},
            expected_revision=pending.session_snapshot.revision,
        )
        first_supervisor.close()
        first_toolkit.shutdown()

        fresh_supervisor = ProcessJobSupervisor(JsonFileJobStore(state_dir))
        fresh_toolkit = CoreToolkit(workspace_root=tmp_path)
        resume_model = _QueueModelIO(
            [
                ModelTurnResult(
                    assistant_messages=[
                        {"role": "assistant", "content": "resumed"}
                    ],
                    tool_calls=[],
                    final_text="resumed",
                    response_id="response-cold-final",
                )
            ]
        )
        resume_loop = build_runtime_loop(
            model_io=resume_model,
            memory_runtime=KernelMemoryRuntime.from_config(
                store=JsonFileSessionStore(memory_dir)
            ),
        )
        resumed = resume_loop.resume_interaction(
            session_id=session_id,
            response=None,
            toolkit=fresh_toolkit,
            tool_runtime_plugins=[
                DurableShellJobPlugin(supervisor=fresh_supervisor)
            ],
        )
        assert resumed.status == "completed"
        [tool_message] = [
            message
            for message in resumed.messages
            if message.get("type") == "function_call_output"
        ]
        job_id = json.loads(tool_message["output"])["job_id"]
        completed = fresh_supervisor.wait(
            job_id,
            execution_id=session_id,
            timeout_ms=5_000,
        )
        assert completed.status == "completed"
        assert completed.stdout == "durable-cold-approval"
        assert [item.job_id for item in fresh_supervisor.reattach(session_id)] == [
            job_id
        ]
    finally:
        if fresh_supervisor is not None and job_id:
            try:
                fresh_supervisor.cancel(
                    job_id,
                    execution_id=session_id,
                    wait_timeout_ms=1_000,
                )
            except Exception:
                pass
        if fresh_supervisor is not None:
            fresh_supervisor.close()
        if fresh_toolkit is not None:
            fresh_toolkit.shutdown()
        first_supervisor.close()
        first_toolkit.shutdown()


def test_tool_optimizer_deferred_shell_still_uses_durable_jobs(tmp_path) -> None:
    session_id = "session-optimizer-durable-job"
    memory_dir = tmp_path / "memory"
    state_dir = tmp_path / "jobs"
    toolkit = CoreToolkit(workspace_root=tmp_path)
    supervisor = ProcessJobSupervisor(JsonFileJobStore(state_dir))
    selector_payload = json.dumps({"tool_names": ["read"]})
    deferred_arguments = {
        "tool_name": "shell",
        "arguments": {
            "action": "run",
            "command": "printf optimizer-durable",
            "run_in_background": True,
            "yield_time_ms": 0,
        },
    }
    model_io = _QueueModelIO(
        [
            ModelTurnResult(
                assistant_messages=[
                    {"role": "assistant", "content": selector_payload}
                ],
                tool_calls=[],
                final_text=selector_payload,
                response_id="response-optimizer-selector",
            ),
            ModelTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": "call-optimizer-background",
                        "name": "tool_execute_deferred",
                        "arguments": json.dumps(deferred_arguments),
                    }
                ],
                tool_calls=[
                    ToolCall(
                        call_id="call-optimizer-background",
                        name="tool_execute_deferred",
                        arguments=deferred_arguments,
                    )
                ],
                response_id="response-optimizer-shell",
            ),
        ]
    )
    agent = Agent(
        name="optimizer-durable-jobs",
        provider="openai",
        model="gpt-5",
        modules=(
            MemoryModule(
                memory=KernelMemoryRuntime.from_config(
                    store=JsonFileSessionStore(memory_dir)
                )
            ),
            ToolsModule(tools=(toolkit,)),
            JobsModule(supervisor=supervisor),
            ToolOptimizerModule(
                config=ToolOptimizerConfig(
                    max_direct_tools=5,
                    trigger_tool_count=1,
                )
            ),
        ),
        model_io_factory=lambda _spec, _context: model_io,
    )
    job_id = ""
    fresh: ProcessJobSupervisor | None = None
    fresh_toolkit: CoreToolkit | None = None
    try:
        suspended = agent.run(
            "start through the deferred shell",
            session_id=session_id,
            max_iterations=3,
        )
        assert suspended.status == "awaiting_interaction"
        assert "shell" not in model_io.requests[1].toolkit.tools

        memory = KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(memory_dir)
        )
        interaction = DurableInteractionRuntime(memory)
        pending = interaction.load_active(session_id)
        interaction.record_receipt(
            session_id,
            interaction_id=pending.request.interaction_id,
            response={"approved": True},
            expected_revision=pending.session_snapshot.revision,
        )
        supervisor.close()
        toolkit.shutdown()

        fresh = ProcessJobSupervisor(JsonFileJobStore(state_dir))
        fresh_toolkit = CoreToolkit(workspace_root=tmp_path)
        resume_model = _QueueModelIO(
            [
                ModelTurnResult(
                    assistant_messages=[
                        {"role": "assistant", "content": "started"}
                    ],
                    tool_calls=[],
                    final_text="started",
                    response_id="response-optimizer-final",
                )
            ]
        )
        resume_agent = Agent(
            name="optimizer-durable-jobs-resume",
            provider="openai",
            model="gpt-5",
            modules=(
                MemoryModule(
                    memory=KernelMemoryRuntime.from_config(
                        store=JsonFileSessionStore(memory_dir)
                    )
                ),
                ToolsModule(tools=(fresh_toolkit,)),
                JobsModule(supervisor=fresh),
                ToolOptimizerModule(
                    config=ToolOptimizerConfig(
                        max_direct_tools=5,
                        trigger_tool_count=1,
                    )
                ),
            ),
            model_io_factory=lambda _spec, _context: resume_model,
        )
        result = resume_agent.resume_interaction(session_id=session_id)
        assert result.status == "completed"
        [tool_message] = [
            message
            for message in result.messages
            if message.get("type") == "function_call_output"
        ]
        started = json.loads(tool_message["output"])
        assert started["durable"] is True
        job_id = started["job_id"]
        assert job_id.startswith("job_")

        assert [item.job_id for item in fresh.reattach(session_id)] == [job_id]
        completed = fresh.wait(
            job_id,
            execution_id=session_id,
            timeout_ms=5_000,
        )
        assert completed.status == "completed"
        assert completed.stdout == "optimizer-durable"
    finally:
        if fresh is not None and job_id:
            try:
                fresh.cancel(
                    job_id,
                    execution_id=session_id,
                    wait_timeout_ms=1_000,
                )
            except Exception:
                pass
        if fresh is not None:
            fresh.close()
        if fresh_toolkit is not None:
            fresh_toolkit.shutdown()
        supervisor.close()
        toolkit.shutdown()


def _suspend_background_shell_approval(
    tmp_path,
    *,
    session_id: str,
    state_dir,
    command: str,
    cwd_for_session=None,
    environment=None,
):
    memory_dir = tmp_path / f"memory-{session_id}"
    toolkit = CoreToolkit(workspace_root=tmp_path)
    if cwd_for_session is not None:
        toolkit._coding_backend._shell_runtime.cwd_by_session[session_id] = (
            cwd_for_session.resolve()
        )
    supervisor = ProcessJobSupervisor(
        JsonFileJobStore(state_dir),
        environment=environment,
    )
    shell_call = ToolCall(
        call_id=f"call-{session_id}",
        name="shell",
        arguments={
            "action": "run",
            "command": command,
            "run_in_background": True,
            "yield_time_ms": 0,
        },
    )
    model = _QueueModelIO(
        [
            ModelTurnResult(
                assistant_messages=[
                    {
                        "type": "function_call",
                        "call_id": shell_call.call_id,
                        "name": shell_call.name,
                        "arguments": json.dumps(shell_call.arguments),
                    }
                ],
                tool_calls=[shell_call],
                response_id=f"response-{session_id}",
            )
        ]
    )
    loop = build_runtime_loop(
        model_io=model,
        memory_runtime=KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(memory_dir)
        ),
    )
    suspended = loop.run(
        [{"role": "user", "content": "start it"}],
        session_id=session_id,
        provider="openai",
        model="gpt-5",
        toolkit=toolkit,
        tool_runtime_plugins=[DurableShellJobPlugin(supervisor=supervisor)],
        max_iterations=3,
    )
    assert suspended.status == "awaiting_interaction"
    route = suspended.interaction_request["subject"]["extra"][
        "tool_runtime_route"
    ]
    manifest = route["handlers"][0]["manifest"]
    assert manifest["handler"] == "durable_shell_job"
    assert manifest["operation"]["intent_digest"]
    assert manifest["operation"]["environment_digest"]
    assert manifest["store"]["store_id"] == supervisor.store.store_id

    memory = KernelMemoryRuntime.from_config(
        store=JsonFileSessionStore(memory_dir)
    )
    interaction = DurableInteractionRuntime(memory)
    pending = interaction.load_active(session_id)
    interaction.record_receipt(
        session_id,
        interaction_id=pending.request.interaction_id,
        response={"approved": True},
        expected_revision=pending.session_snapshot.revision,
    )
    supervisor.close()
    toolkit.shutdown()
    return memory_dir


def _resume_model() -> _QueueModelIO:
    return _QueueModelIO(
        [
            ModelTurnResult(
                assistant_messages=[
                    {"role": "assistant", "content": "resumed"}
                ],
                tool_calls=[],
                final_text="resumed",
                response_id="response-route-final",
            )
        ]
    )


def test_durable_approval_resume_without_jobs_plugin_fails_closed(tmp_path) -> None:
    session_id = "route-missing"
    state_dir = tmp_path / "jobs-original"
    marker = tmp_path / "must-not-run.txt"
    memory_dir = _suspend_background_shell_approval(
        tmp_path,
        session_id=session_id,
        state_dir=state_dir,
        command=f"printf forbidden > {marker}",
    )
    toolkit = CoreToolkit(workspace_root=tmp_path)
    loop = build_runtime_loop(
        model_io=_resume_model(),
        memory_runtime=KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(memory_dir)
        ),
    )
    try:
        with pytest.raises(InteractionIntegrityError):
            loop.resume_interaction(
                session_id=session_id,
                response=None,
                toolkit=toolkit,
                tool_runtime_plugins=[],
            )
        assert marker.exists() is False
        store_probe = ProcessJobSupervisor(JsonFileJobStore(state_dir))
        try:
            assert store_probe.reattach(session_id) == []
        finally:
            store_probe.close()
    finally:
        toolkit.shutdown()


def test_durable_approval_resume_with_different_store_fails_closed(tmp_path) -> None:
    session_id = "route-store-changed"
    original_state_dir = tmp_path / "jobs-original"
    memory_dir = _suspend_background_shell_approval(
        tmp_path,
        session_id=session_id,
        state_dir=original_state_dir,
        command="printf must-not-start",
    )
    toolkit = CoreToolkit(workspace_root=tmp_path)
    changed_supervisor = ProcessJobSupervisor(
        JsonFileJobStore(tmp_path / "jobs-different")
    )
    loop = build_runtime_loop(
        model_io=_resume_model(),
        memory_runtime=KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(memory_dir)
        ),
    )
    try:
        with pytest.raises(InteractionIntegrityError):
            loop.resume_interaction(
                session_id=session_id,
                response=None,
                toolkit=toolkit,
                tool_runtime_plugins=[
                    DurableShellJobPlugin(supervisor=changed_supervisor)
                ],
            )
        assert changed_supervisor.reattach(session_id) == []
    finally:
        changed_supervisor.close()
        toolkit.shutdown()


def test_durable_approval_resume_after_same_path_store_replacement_fails_closed(
    tmp_path,
) -> None:
    session_id = "route-store-replaced"
    state_dir = tmp_path / "jobs"
    marker = tmp_path / "must-not-run.txt"
    memory_dir = _suspend_background_shell_approval(
        tmp_path,
        session_id=session_id,
        state_dir=state_dir,
        command=f"printf forbidden > {marker}",
    )
    original_store_id = JsonFileJobStore(state_dir).store_id
    shutil.rmtree(state_dir)
    replacement_store = JsonFileJobStore(state_dir)
    assert replacement_store.store_id != original_store_id

    toolkit = CoreToolkit(workspace_root=tmp_path)
    supervisor = ProcessJobSupervisor(replacement_store)
    loop = build_runtime_loop(
        model_io=_resume_model(),
        memory_runtime=KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(memory_dir)
        ),
    )
    try:
        with pytest.raises(InteractionIntegrityError):
            loop.resume_interaction(
                session_id=session_id,
                response=None,
                toolkit=toolkit,
                tool_runtime_plugins=[DurableShellJobPlugin(supervisor=supervisor)],
            )
        assert marker.exists() is False
        assert supervisor.reattach(session_id) == []
    finally:
        supervisor.close()
        toolkit.shutdown()


def test_durable_approval_resume_from_copied_store_path_fails_closed(
    tmp_path,
) -> None:
    session_id = "route-store-copied"
    original_state_dir = tmp_path / "jobs-original"
    copied_state_dir = tmp_path / "jobs-copy"
    memory_dir = _suspend_background_shell_approval(
        tmp_path,
        session_id=session_id,
        state_dir=original_state_dir,
        command="printf must-not-start",
    )
    shutil.copytree(original_state_dir, copied_state_dir)
    original = JsonFileJobStore(original_state_dir)
    copied = JsonFileJobStore(copied_state_dir)
    assert copied.store_id == original.store_id

    toolkit = CoreToolkit(workspace_root=tmp_path)
    supervisor = ProcessJobSupervisor(copied)
    loop = build_runtime_loop(
        model_io=_resume_model(),
        memory_runtime=KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(memory_dir)
        ),
    )
    try:
        with pytest.raises(InteractionIntegrityError):
            loop.resume_interaction(
                session_id=session_id,
                response=None,
                toolkit=toolkit,
                tool_runtime_plugins=[DurableShellJobPlugin(supervisor=supervisor)],
            )
        assert supervisor.reattach(session_id) == []
    finally:
        supervisor.close()
        toolkit.shutdown()


def test_durable_approval_resume_with_environment_drift_fails_closed(
    tmp_path,
) -> None:
    session_id = "route-environment-changed"
    state_dir = tmp_path / "jobs"
    marker = tmp_path / "must-not-run.txt"
    variable = "UNCHAIN_DURABLE_APPROVAL_PROFILE"
    environment_a = dict(os.environ)
    environment_a[variable] = "profile-a"
    environment_b = dict(os.environ)
    environment_b[variable] = "profile-b"
    memory_dir = _suspend_background_shell_approval(
        tmp_path,
        session_id=session_id,
        state_dir=state_dir,
        command=f"printf forbidden > {marker}",
        environment=environment_a,
    )

    toolkit = CoreToolkit(workspace_root=tmp_path)
    supervisor = ProcessJobSupervisor(
        JsonFileJobStore(state_dir),
        environment=environment_b,
    )
    loop = build_runtime_loop(
        model_io=_resume_model(),
        memory_runtime=KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(memory_dir)
        ),
    )
    try:
        with pytest.raises(InteractionIntegrityError):
            loop.resume_interaction(
                session_id=session_id,
                response=None,
                toolkit=toolkit,
                tool_runtime_plugins=[DurableShellJobPlugin(supervisor=supervisor)],
            )
        assert marker.exists() is False
        assert supervisor.reattach(session_id) == []
    finally:
        supervisor.close()
        toolkit.shutdown()


def test_durable_approval_resume_with_changed_resolved_cwd_fails_closed(
    tmp_path,
) -> None:
    session_id = "route-cwd-changed"
    state_dir = tmp_path / "jobs"
    original_cwd = tmp_path / "original-cwd"
    original_cwd.mkdir()
    memory_dir = _suspend_background_shell_approval(
        tmp_path,
        session_id=session_id,
        state_dir=state_dir,
        command="printf must-not-start",
        cwd_for_session=original_cwd,
    )
    toolkit = CoreToolkit(workspace_root=tmp_path)
    supervisor = ProcessJobSupervisor(JsonFileJobStore(state_dir))
    loop = build_runtime_loop(
        model_io=_resume_model(),
        memory_runtime=KernelMemoryRuntime.from_config(
            store=JsonFileSessionStore(memory_dir)
        ),
    )
    try:
        with pytest.raises(InteractionIntegrityError):
            loop.resume_interaction(
                session_id=session_id,
                response=None,
                toolkit=toolkit,
                tool_runtime_plugins=[DurableShellJobPlugin(supervisor=supervisor)],
            )
        assert supervisor.reattach(session_id) == []
    finally:
        supervisor.close()
        toolkit.shutdown()


def test_custom_shell_tool_falls_through_durable_jobs_plugin(tmp_path) -> None:
    toolkit = Toolkit()
    toolkit.register(
        lambda **arguments: {"arguments": arguments},
        name="shell",
        description="Application-defined shell-shaped tool.",
    )
    supervisor = ProcessJobSupervisor(JsonFileJobStore(tmp_path / "jobs"))
    plugin = DurableShellJobPlugin(supervisor=supervisor)
    tool_call = ToolCall(
        call_id="call-custom-shell",
        name="shell",
        arguments={
            "action": "run",
            "command": "application operation",
            "run_in_background": True,
        },
    )
    try:
        assert plugin.can_handle(
            tool_call=tool_call,
            context=_context(toolkit, True),
        ) is False
    finally:
        supervisor.close()


def test_unmanifested_plugin_cannot_shadow_durable_shell_route(tmp_path) -> None:
    class EarlierShellPlugin:
        def can_handle(self, *, tool_call, context) -> bool:
            del context
            return tool_call.name == "shell"

        def execute(self, *, tool_call, context):
            raise AssertionError("route validation must fail before execution")

    toolkit = CoreToolkit(workspace_root=tmp_path)
    supervisor = ProcessJobSupervisor(JsonFileJobStore(tmp_path / "jobs"))
    tool_call = ToolCall(
        call_id="call-shadowed-shell",
        name="shell",
        arguments={
            "action": "run",
            "command": "printf must-not-run",
            "run_in_background": True,
        },
    )
    context = _context(toolkit, True)
    try:
        with pytest.raises(
            InteractionIntegrityError,
            match="has no durable runtime manifest",
        ):
            snapshot_durable_tool_runtime_route(
                [
                    EarlierShellPlugin(),
                    DurableShellJobPlugin(supervisor=supervisor),
                ],
                tool_call=tool_call,
                context=context,
            )
    finally:
        supervisor.close()
        toolkit.shutdown()
