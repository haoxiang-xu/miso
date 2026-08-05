from __future__ import annotations

import os
import subprocess
import sys

import pytest

from unchain.agent import Agent
from unchain.kernel.types import KernelRunResult
from unchain.runtime import AgentRuntimeContext, ExecutionIdentity, ModuleGrant


class _PreparedProbe:
    def __init__(self, method_name: str) -> None:
        self.method_name = method_name
        self.calls = 0

    def run(self) -> KernelRunResult:
        assert self.method_name == "run"
        self.calls += 1
        return KernelRunResult(messages=[], status="completed")

    def resume_human_input(self) -> KernelRunResult:
        assert self.method_name == "resume_human_input"
        self.calls += 1
        return KernelRunResult(messages=[], status="completed")

    def resume_interaction(self) -> KernelRunResult:
        assert self.method_name == "resume_interaction"
        self.calls += 1
        return KernelRunResult(messages=[], status="completed")


def test_subagent_plugin_can_be_imported_first_in_a_clean_process():
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        path
        for path in ("src", environment.get("PYTHONPATH", ""))
        if path
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from unchain.subagents.plugin import SubagentToolPlugin; "
            "assert SubagentToolPlugin.__name__ == 'SubagentToolPlugin'",
        ],
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr

def _runtime_context(
    *,
    execution_id: str = "execution-a",
    attempt_id: str = "attempt-a",
    run_id: str = "run-a",
    run_lineage: tuple[str, ...] | None = None,
) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        identity=ExecutionIdentity(
            execution_id=execution_id,
            attempt_id=attempt_id,
            run_id=run_id,
            run_lineage=run_lineage or (run_id,),
        ),
        module_grants=(
            ModuleGrant(
                module_key="memory_v2",
                capabilities=frozenset({"memory.workspace.read"}),
                delegable_capabilities=frozenset({"memory.workspace.read"}),
            ),
        ),
    )


def test_agent_run_carries_explicit_roleless_runtime_context(monkeypatch):
    agent = Agent(name="root")
    contexts = []
    prepared = _PreparedProbe("run")
    runtime_context = _runtime_context()
    monkeypatch.setattr(
        agent,
        "_prepare",
        lambda context: contexts.append(context) or prepared,
    )

    result = agent.run(
        "start",
        runtime_context=runtime_context,
    )

    assert result.status == "completed"
    assert prepared.calls == 1
    assert contexts[0].mode == "run"
    assert contexts[0].runtime_context is runtime_context
    assert contexts[0].session_id == "execution-a"
    assert contexts[0].execution_owner_id == "attempt-a"
    assert contexts[0].run_id == "run-a"
    assert not hasattr(contexts[0], "memory_v2_run_role")


@pytest.mark.parametrize(
    "method_name",
    ("resume_human_input", "resume_interaction"),
)
def test_agent_resume_entrypoints_preserve_explicit_runtime_context(
    monkeypatch,
    method_name,
):
    agent = Agent(name="root")
    contexts = []
    prepared = _PreparedProbe(method_name)
    runtime_context = _runtime_context(
        execution_id="execution-a",
        attempt_id="resume-attempt-a",
        run_id="resume-run-a",
        run_lineage=("root-run-a", "resume-run-a"),
    )
    monkeypatch.setattr(
        agent,
        "_prepare",
        lambda context: contexts.append(context) or prepared,
    )

    if method_name == "resume_human_input":
        result = agent.resume_human_input(
            runtime_context=runtime_context,
        )
    else:
        result = agent.resume_interaction(
            session_id="execution-a",
            runtime_context=runtime_context,
        )

    assert result.status == "completed"
    assert prepared.calls == 1
    assert contexts[0].mode == method_name
    assert contexts[0].runtime_context is runtime_context
    assert contexts[0].run_id == "resume-run-a"
    assert contexts[0].execution_owner_id == "resume-attempt-a"
    assert contexts[0].runtime_context.identity.root_run_id == "root-run-a"
