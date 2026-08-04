from __future__ import annotations

import os
import subprocess
import sys

import pytest

from unchain.agent import Agent, MemoryV2RunRole
from unchain.kernel.types import KernelRunResult


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

def test_agent_run_carries_explicit_root_role_without_inferring_from_mode(monkeypatch):
    agent = Agent(name="root")
    contexts = []
    prepared = _PreparedProbe("run")
    monkeypatch.setattr(
        agent,
        "_prepare",
        lambda context: contexts.append(context) or prepared,
    )

    result = agent.run(
        "start",
        run_id="root-run-a",
        memory_v2_run_role=MemoryV2RunRole.ROOT,
        root_run_id="root-run-a",
    )

    assert result.status == "completed"
    assert prepared.calls == 1
    assert contexts[0].mode == "run"
    assert contexts[0].memory_v2_run_role is MemoryV2RunRole.ROOT
    assert contexts[0].root_run_id == "root-run-a"


@pytest.mark.parametrize(
    "method_name",
    ("resume_human_input", "resume_interaction"),
)
def test_agent_resume_entrypoints_preserve_explicit_root_identity(
    monkeypatch,
    method_name,
):
    agent = Agent(name="root")
    contexts = []
    prepared = _PreparedProbe(method_name)
    monkeypatch.setattr(
        agent,
        "_prepare",
        lambda context: contexts.append(context) or prepared,
    )

    if method_name == "resume_human_input":
        result = agent.resume_human_input(
            run_id="resume-attempt-a",
            memory_v2_run_role=MemoryV2RunRole.ROOT,
            root_run_id="root-run-a",
        )
    else:
        result = agent.resume_interaction(
            session_id="session-a",
            run_id="resume-attempt-a",
            memory_v2_run_role=MemoryV2RunRole.ROOT,
            root_run_id="root-run-a",
        )

    assert result.status == "completed"
    assert prepared.calls == 1
    assert contexts[0].mode == method_name
    assert contexts[0].run_id == "resume-attempt-a"
    assert contexts[0].memory_v2_run_role is MemoryV2RunRole.ROOT
    assert contexts[0].root_run_id == "root-run-a"
