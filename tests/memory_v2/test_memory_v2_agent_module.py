from __future__ import annotations

import importlib.util
from dataclasses import replace
from types import SimpleNamespace

import pytest

from unchain.agent.builder import AgentBuilder, AgentCallContext
from unchain.agent.model_io import ModelIOFactoryRegistry
from unchain.agent.modules.memory_v2 import (
    MemoryV2AgentAttachment,
    MemoryV2AgentAttachmentRequest,
    MemoryV2AgentModule,
    MemoryV2AgentModuleError,
    MemoryV2RunRole,
)
from unchain.agent.spec import AgentSpec, AgentState
from unchain.kernel.types import KernelRunResult
from unchain.memory.curator import RunCaptureStatus, SourceRunStatus
from unchain.memory.curator.host import MemoryAgentHostAdapter
from unchain.memory.toolkit import MemoryToolkitRunBinding

from .test_curator_coordinator import FakeCurationRepository, candidate, completion
from .test_memory_agent_host import (
    _enabled_host,
    _normal_binding,
    _normal_capabilities,
)


def test_official_memory_v2_agent_module_has_a_direct_import_path():
    assert importlib.util.find_spec("unchain.agent.modules.memory_v2") is not None


class _CompletionFactory:
    def __init__(self, value):
        self.value = value
        self.results = []

    def build(self, *, result):
        self.results.append(result)
        return self.value


class _AttachmentFactory:
    binding_id = "binding-a"

    def __init__(
        self,
        *,
        binding=None,
        completion_factory=None,
    ):
        self.binding = binding or _normal_binding()
        self.completion_factory = completion_factory
        self.requests = []

    def attach(self, request):
        self.requests.append(request)
        return MemoryV2AgentAttachment(
            binding=self.binding,
            capabilities=_normal_capabilities(),
            completion_factory=self.completion_factory,
        )


def _builder(
    *,
    session_id="session-a",
    attempt_id="attempt-a",
    run_id="run-a",
    mode="run",
    run_role=MemoryV2RunRole.ROOT,
    root_run_id="run-a",
):
    return AgentBuilder(
        agent=SimpleNamespace(name="normal-agent"),
        spec=AgentSpec(
            name="normal-agent",
            provider="openai",
            model="gpt-test",
        ),
        state=AgentState(),
        call_context=AgentCallContext(
            mode=mode,
            session_id=session_id,
            execution_owner_id=attempt_id,
            run_id=run_id,
            memory_v2_run_role=run_role,
            root_run_id=root_run_id,
        ),
        model_io_registry=ModelIOFactoryRegistry(),
    )


def _result(status="completed"):
    return KernelRunResult(
        messages=[{"role": "assistant", "content": status}],
        status=status,
    )


def test_default_closed_module_does_not_call_factory_or_change_builder():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    factory = _AttachmentFactory(completion_factory=_CompletionFactory(completion()))
    module = MemoryV2AgentModule(
        host=MemoryAgentHostAdapter(repository),
        attachment_factory=factory,
    )
    builder = _builder()

    module.configure(builder)

    assert factory.requests == []
    assert builder.toolkit.tools == {}
    assert builder.run_hooks == []
    assert repository.jobs == {}


def test_default_module_without_a_host_is_a_noop():
    builder = _builder()

    MemoryV2AgentModule().configure(builder)

    assert builder.toolkit.tools == {}
    assert builder.run_hooks == []


def test_enabled_attachment_adds_only_the_normal_memory_toolkit_and_root_hook():
    host, _, _, _ = _enabled_host()
    completion_factory = _CompletionFactory(completion())
    factory = _AttachmentFactory(completion_factory=completion_factory)
    builder = _builder(mode="resume_interaction")

    MemoryV2AgentModule(host=host, attachment_factory=factory).configure(builder)

    assert tuple(
        name for name in builder.toolkit.tools if name.startswith("memory_")
    ) == (
        "memory_list",
        "memory_search",
        "memory_read",
        "memory_propose",
    )
    assert len(builder.run_hooks) == 1
    assert factory.requests == [
        MemoryV2AgentAttachmentRequest(
            agent_name="normal-agent",
            mode="resume_interaction",
            session_id="session-a",
            attempt_id="attempt-a",
            run_id="run-a",
            role=MemoryV2RunRole.ROOT,
            root_run_id="run-a",
        )
    ]


def test_resume_root_keeps_stable_root_identity_across_a_new_attempt_run():
    host, _, _, _ = _enabled_host()
    factory = _AttachmentFactory(
        binding=MemoryToolkitRunBinding(
            "binding-a",
            "session-a",
            "resume-attempt",
            "resume-run",
        ),
        completion_factory=_CompletionFactory(
            completion(attempt_id="resume-attempt", run_id="resume-run")
        ),
    )
    builder = _builder(
        mode="resume_interaction",
        attempt_id="resume-attempt",
        run_id="resume-run",
        run_role=MemoryV2RunRole.ROOT,
        root_run_id="root-run-a",
    )

    MemoryV2AgentModule(host=host, attachment_factory=factory).configure(builder)

    assert len(builder.run_hooks) == 1
    assert factory.requests[0].role is MemoryV2RunRole.ROOT
    assert factory.requests[0].run_id == "resume-run"
    assert factory.requests[0].root_run_id == "root-run-a"


def test_enabled_non_root_attachment_gets_tools_without_a_terminal_hook():
    host, _, _, _ = _enabled_host()
    builder = _builder(
        session_id="child-session",
        attempt_id="child-attempt",
        run_id="child-run",
        run_role=MemoryV2RunRole.SUBAGENT,
        root_run_id="root-run",
    )
    factory = _AttachmentFactory(
        binding=MemoryToolkitRunBinding(
            "binding-a",
            "child-session",
            "child-attempt",
            "child-run",
        )
    )

    MemoryV2AgentModule(host=host, attachment_factory=factory).configure(builder)

    assert {name for name in builder.toolkit.tools if name.startswith("memory_")} == {
        "memory_list",
        "memory_search",
        "memory_read",
        "memory_propose",
    }
    assert "memory_upsert" not in builder.toolkit.tools
    assert "memory_promote" not in builder.toolkit.tools
    assert builder.run_hooks == []


def test_non_root_attachment_never_gets_a_completion_hook_even_if_factory_returns_one():
    host, _, _, _ = _enabled_host()
    completion_factory = _CompletionFactory(completion())
    builder = _builder(
        session_id="child-session",
        attempt_id="child-attempt",
        run_id="child-run",
        run_role=MemoryV2RunRole.SUBAGENT,
        root_run_id="root-run",
    )

    MemoryV2AgentModule(
        host=host,
        attachment_factory=_AttachmentFactory(
            binding=MemoryToolkitRunBinding(
                "binding-a",
                "child-session",
                "child-attempt",
                "child-run",
            ),
            completion_factory=completion_factory,
        ),
    ).configure(builder)

    assert builder.run_hooks == []
    assert completion_factory.results == []


def test_graph_step_attachment_gets_normal_tools_without_a_completion_hook():
    host, _, _, _ = _enabled_host()
    builder = _builder(
        run_role=MemoryV2RunRole.GRAPH_STEP,
        root_run_id="root-run",
    )

    MemoryV2AgentModule(
        host=host,
        attachment_factory=_AttachmentFactory(
            completion_factory=_CompletionFactory(completion())
        ),
    ).configure(builder)

    assert "memory_propose" in builder.toolkit.tools
    assert builder.run_hooks == []


@pytest.mark.parametrize(
    ("run_role", "root_run_id", "message"),
    (
        (None, "", "explicit Memory V2 run identity"),
        ("root", "run-a", "explicit Memory V2 run identity"),
        (MemoryV2RunRole.SUBAGENT, "run-a", "subagent run identity"),
    ),
)
def test_enabled_attachment_rejects_missing_or_drifting_run_identity(
    run_role,
    root_run_id,
    message,
):
    host, repository, _, _ = _enabled_host()
    builder = _builder(run_role=run_role, root_run_id=root_run_id)

    with pytest.raises(MemoryV2AgentModuleError, match=message):
        MemoryV2AgentModule(
            host=host,
            attachment_factory=_AttachmentFactory(
                completion_factory=_CompletionFactory(completion())
            ),
        ).configure(builder)

    assert builder.toolkit.tools == {}
    assert builder.run_hooks == []
    assert repository.jobs == {}


def test_suspended_result_is_not_inferred_as_a_root_completion():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    host, _, worker_capabilities, invoker = _enabled_host(repository)
    completion_factory = _CompletionFactory(None)
    builder = _builder()
    MemoryV2AgentModule(
        host=host,
        attachment_factory=_AttachmentFactory(completion_factory=completion_factory),
    ).configure(builder)
    suspended = _result("awaiting_interaction")

    returned = builder.run_hooks[0](suspended)

    assert returned is None
    assert completion_factory.results == [suspended]
    assert repository.jobs == {}
    assert repository.isolations == []
    assert worker_capabilities.calls == []
    assert invoker.calls == []


def test_no_candidate_root_completion_is_a_noop_without_running_curator_model():
    host, repository, worker_capabilities, invoker = _enabled_host()
    completion_factory = _CompletionFactory(completion())
    builder = _builder()
    MemoryV2AgentModule(
        host=host,
        attachment_factory=_AttachmentFactory(completion_factory=completion_factory),
    ).configure(builder)
    result = _result()

    returned = builder.run_hooks[0](result)

    assert returned is None
    assert completion_factory.results == [result]
    assert repository.jobs == {}
    assert worker_capabilities.calls == []
    assert invoker.calls == []


def test_replayed_root_completion_keeps_stable_identity_and_enqueues_once():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    host, _, worker_capabilities, invoker = _enabled_host(repository)
    completion_factory = _CompletionFactory(completion())
    factory = _AttachmentFactory(completion_factory=completion_factory)
    first = _builder()
    second = _builder()
    module = MemoryV2AgentModule(host=host, attachment_factory=factory)
    module.configure(first)
    module.configure(second)

    first.run_hooks[0](_result())
    second.run_hooks[0](_result())

    assert len(repository.jobs) == 1
    assert len(factory.requests) == 2
    assert factory.requests[0] == factory.requests[1]
    assert worker_capabilities.calls == []
    assert invoker.calls == []


@pytest.mark.parametrize(
    ("run_status", "capture_status", "reason"),
    (
        (SourceRunStatus.FAILED, RunCaptureStatus.COMPLETE, "source_run_failed"),
        (
            SourceRunStatus.CANCELLED,
            RunCaptureStatus.COMPLETE,
            "source_run_cancelled",
        ),
        (
            SourceRunStatus.COMPLETED,
            RunCaptureStatus.PARTIAL,
            "source_capture_partial",
        ),
        (
            SourceRunStatus.COMPLETED,
            RunCaptureStatus.UNAVAILABLE,
            "source_capture_unavailable",
        ),
    ),
)
def test_non_eligible_terminal_captures_are_isolated_without_a_worker(
    run_status,
    capture_status,
    reason,
):
    repository = FakeCurationRepository((candidate("candidate-a"),))
    host, _, worker_capabilities, invoker = _enabled_host(repository)
    terminal = completion(
        run_status=run_status,
        capture_status=capture_status,
    )
    builder = _builder()
    MemoryV2AgentModule(
        host=host,
        attachment_factory=_AttachmentFactory(
            completion_factory=_CompletionFactory(terminal)
        ),
    ).configure(builder)

    builder.run_hooks[0](_result())

    assert repository.jobs == {}
    assert len(repository.isolations) == 1
    assert repository.isolations[0][1] == reason
    assert worker_capabilities.calls == []
    assert invoker.calls == []


def test_attachment_identity_drift_fails_before_tools_or_hooks_are_added():
    host, repository, _, _ = _enabled_host()
    builder = _builder()
    factory = _AttachmentFactory(
        binding=replace(_normal_binding(), run_id="different-run"),
        completion_factory=_CompletionFactory(completion()),
    )

    with pytest.raises(MemoryV2AgentModuleError, match="run_id"):
        MemoryV2AgentModule(host=host, attachment_factory=factory).configure(builder)

    assert builder.toolkit.tools == {}
    assert builder.run_hooks == []
    assert repository.jobs == {}


def test_completion_identity_drift_fails_without_enqueuing():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    host, _, _, _ = _enabled_host(repository)
    builder = _builder()
    terminal = completion(run_id="different-run")
    MemoryV2AgentModule(
        host=host,
        attachment_factory=_AttachmentFactory(
            completion_factory=_CompletionFactory(terminal)
        ),
    ).configure(builder)

    with pytest.raises(MemoryV2AgentModuleError, match="run identity"):
        builder.run_hooks[0](_result())

    assert repository.jobs == {}
