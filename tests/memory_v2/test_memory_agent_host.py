from __future__ import annotations

import pytest

from unchain.journal import ResourceRef
from unchain.memory.curator.host import (
    MemoryAgentHostAdapter,
    MemoryAgentHostConfig,
    MemoryAgentHostError,
    MemoryAgentWorkerDisposition,
)
from unchain.memory.curator.models import (
    CuratorLeaseFence,
    EnqueueDisposition,
    ProcessDisposition,
)
from unchain.memory.toolkit import (
    ConsolidationMemoryToolkitCapabilities,
    MemoryToolkitRunBinding,
    NormalMemoryToolkitCapabilities,
)

from .test_curator_coordinator import (
    FakeCurationRepository,
    applied_result,
    candidate,
    completion,
)


class _CommonCapability:
    def __init__(self, *, binding_id: str = "binding-a") -> None:
        self.binding_id = binding_id
        self.space_id = f"space-{binding_id}"
        self.space_revision = 1

    def decode(self, _value, *, purpose):
        del purpose
        return ResourceRef("artifact", "unused", 1)

    def encode(self, ref):
        return f"ref:{ref.kind}:{ref.resource_id}:{ref.revision}:{ref.fragment}"

    def authorize(self, *, ref, purpose):
        del purpose
        return ref

    def __getattr__(self, _name):
        def unused(**_arguments):
            return {}

        return unused


class _ConsolidationCapability(_CommonCapability):
    def read_candidate(self, *, job_id, ref, offset, limit):
        del job_id, ref, offset, limit
        raise AssertionError("the model runner did not request candidate bytes")

    def apply_new(
        self,
        *,
        job_id,
        candidate_ref,
        expected_binding_revision,
        expected_space_revision,
        mutation_guard,
        operation_id,
    ):
        del (
            job_id,
            candidate_ref,
            expected_binding_revision,
            expected_space_revision,
            mutation_guard,
            operation_id,
        )
        raise AssertionError("the model runner did not apply a candidate")

    def propose_review(
        self,
        *,
        job_id,
        candidate_ref,
        expected_binding_revision,
        target_entry_id,
        expected_target_revision,
        mode,
        mutation_guard,
        operation_id,
    ):
        del (
            job_id,
            candidate_ref,
            expected_binding_revision,
            target_entry_id,
            expected_target_revision,
            mode,
            mutation_guard,
            operation_id,
        )
        raise AssertionError("the model runner did not propose a review")


class _CapabilityFactory:
    binding_id = "binding-a"

    def __init__(self) -> None:
        self.calls = []

    def build(self, *, binding, job, mutation_guard):
        self.calls.append((binding, job, mutation_guard))
        common = _CommonCapability()
        return ConsolidationMemoryToolkitCapabilities(
            binding_id=self.binding_id,
            references=common,
            context=common,
            chat=common,
            consolidation=_ConsolidationCapability(),
            job_id=job.job_id,
            candidate_refs=tuple(item.candidate_ref for item in job.candidates),
            lease_fence=CuratorLeaseFence.from_job(self.binding_id, job),
            mutation_guard=mutation_guard,
            source_refs=tuple(
                dict.fromkeys(
                    source_ref
                    for item in job.candidates
                    for source_ref in item.source_refs
                )
            ),
        )


class _ModelInvoker:
    def __init__(self) -> None:
        self.calls = []

    def run(self, request, *, toolkit, binding):
        self.calls.append((request, toolkit, binding))
        return applied_result(request.job)


class _CountingRepository(FakeCurationRepository):
    def __init__(self, candidates=()) -> None:
        super().__init__(candidates)
        self.claim_calls = 0

    def claim_next(self, **arguments):
        self.claim_calls += 1
        return super().claim_next(**arguments)


def _enabled_host(repository=None, *, invoker=None):
    repository = repository or _CountingRepository()
    capabilities = _CapabilityFactory()
    invoker = invoker or _ModelInvoker()
    host = MemoryAgentHostAdapter(
        repository,
        capability_factory=capabilities,
        model_invoker=invoker,
        config=MemoryAgentHostConfig(enabled=True),
        clock_ms=lambda: 1000,
    )
    return host, repository, capabilities, invoker


def _normal_capabilities():
    common = _CommonCapability()
    return NormalMemoryToolkitCapabilities(
        references=common,
        context=common,
        chat=common,
        candidates=common,
    )


def _normal_binding():
    return MemoryToolkitRunBinding(
        binding_id="binding-a",
        session_id="session-a",
        attempt_id="attempt-a",
        run_id="run-a",
    )


def test_default_closed_gate_has_no_repository_model_or_toolkit_effects():
    repository = _CountingRepository((candidate("candidate-a"),))
    host = MemoryAgentHostAdapter(repository)

    assert (
        host.build_normal_toolkit(
            _normal_binding(),
            _normal_capabilities(),
        )
        is None
    )

    enqueued = host.enqueue_root_completion(completion())
    processed = host.process_next(operation_id="claim-disabled")

    assert enqueued.enabled is False
    assert enqueued.reason == "feature_disabled"
    assert enqueued.result is None
    assert processed.disposition is MemoryAgentWorkerDisposition.DISABLED
    assert repository.jobs == {}
    assert repository.claim_calls == 0


def test_enabled_gate_fails_preflight_without_bound_worker_dependencies():
    repository = _CountingRepository()

    with pytest.raises(
        MemoryAgentHostError,
        match="requires capability_factory and model_invoker",
    ):
        MemoryAgentHostAdapter(
            repository,
            config=MemoryAgentHostConfig(enabled=True),
        )

    unbound_factory = _CapabilityFactory()
    unbound_factory.binding_id = "binding-other"
    with pytest.raises(MemoryAgentHostError, match="another binding"):
        MemoryAgentHostAdapter(
            repository,
            capability_factory=unbound_factory,
            model_invoker=_ModelInvoker(),
            config=MemoryAgentHostConfig(enabled=True),
        )


def test_enabled_normal_agents_receive_only_read_search_list_and_propose_memory_tools():
    host, _, _, _ = _enabled_host()

    toolkit = host.build_normal_toolkit(
        _normal_binding(),
        _normal_capabilities(),
    )

    assert toolkit is not None
    assert tuple(name for name in toolkit.tools if name.startswith("memory_")) == (
        "memory_list",
        "memory_search",
        "memory_read",
        "memory_propose",
    )


def test_no_candidates_is_noop_and_never_constructs_or_runs_memory_agent():
    host, repository, capabilities, invoker = _enabled_host()

    enqueued = host.enqueue_root_completion(completion())
    processed = host.process_next(operation_id="claim-empty")

    assert enqueued.result is not None
    assert enqueued.result.disposition is EnqueueDisposition.NO_OP
    assert processed.disposition is MemoryAgentWorkerDisposition.IDLE
    assert repository.jobs == {}
    assert capabilities.calls == []
    assert invoker.calls == []


def test_non_root_memory_agent_source_is_isolated_without_a_worker_job():
    repository = _CountingRepository((candidate("candidate-a"),))
    host, _, capabilities, invoker = _enabled_host(repository)

    enqueued = host.enqueue_root_completion(completion(is_root_run=False))
    processed = host.process_next(operation_id="claim-non-root")

    assert enqueued.result.disposition is EnqueueDisposition.ISOLATED
    assert enqueued.reason == "not_root_run"
    assert processed.disposition is MemoryAgentWorkerDisposition.IDLE
    assert repository.jobs == {}
    assert capabilities.calls == []
    assert invoker.calls == []


def test_root_completion_enqueue_is_idempotent_and_model_runs_only_in_worker():
    repository = _CountingRepository((candidate("candidate-a"),))
    host, _, capabilities, invoker = _enabled_host(repository)

    first = host.enqueue_root_completion(completion())
    replay = host.enqueue_root_completion(completion())

    assert first.result.disposition is EnqueueDisposition.ENQUEUED
    assert replay.result.disposition is EnqueueDisposition.REPLAYED
    assert first.result.job == replay.result.job
    assert len(repository.jobs) == 1
    assert capabilities.calls == []
    assert invoker.calls == []

    processed = host.process_next(operation_id="claim-job-1-revision-1")

    assert processed.disposition is MemoryAgentWorkerDisposition.PROCESSED
    assert processed.result.disposition is ProcessDisposition.COMPLETED
    assert len(capabilities.calls) == 1
    assert len(invoker.calls) == 1


def test_worker_uses_official_candidate_bound_toolkit_without_promotion_tools():
    repository = _CountingRepository((candidate("candidate-a"),))
    host, _, _, invoker = _enabled_host(repository)
    host.enqueue_root_completion(completion())

    processed = host.process_next(operation_id="claim-official-toolkit")

    assert processed.result.disposition is ProcessDisposition.COMPLETED
    request, toolkit, binding = invoker.calls[0]
    assert request.job.job_id == processed.claimed_job.job_id
    assert binding.binding_id == "binding-a"
    assert binding.session_id == request.job.trigger.session_id
    assert toolkit.binding_id == "binding-a"
    assert toolkit.job_id == request.job.job_id
    assert toolkit.candidate_refs == tuple(
        item.candidate_ref for item in request.job.candidates
    )
    assert toolkit.lease_fence == request.lease_fence
    assert tuple(name for name in toolkit.tools if name.startswith("memory_")) == (
        "memory_list",
        "memory_search",
        "memory_read",
        "memory_candidate_read",
        "memory_candidate_source_read",
        "memory_candidate_apply_new",
        "memory_candidate_propose_review",
    )
    assert "memory_promote" not in toolkit.tools
    assert "memory_upsert" not in toolkit.tools


def test_recursive_memory_agent_call_is_blocked_before_another_job_claim():
    repository = _CountingRepository((candidate("candidate-a"),))

    class RecursiveInvoker(_ModelInvoker):
        host = None

        def run(self, request, *, toolkit, binding):
            self.calls.append((request, toolkit, binding))
            nested = self.host.process_next(operation_id="recursive-claim")
            assert nested.disposition is MemoryAgentWorkerDisposition.RECURSION_BLOCKED
            return applied_result(request.job)

    invoker = RecursiveInvoker()
    host, _, _, _ = _enabled_host(repository, invoker=invoker)
    invoker.host = host
    host.enqueue_root_completion(completion(run_id="root-one"))
    host.enqueue_root_completion(completion(attempt_id="attempt-b", run_id="root-two"))

    processed = host.process_next(operation_id="outer-claim")

    assert processed.result.disposition is ProcessDisposition.COMPLETED
    assert repository.claim_calls == 1
    assert len(invoker.calls) == 1
    assert len(repository.jobs) == 2
    assert sum(job.status.value == "pending" for job in repository.jobs.values()) == 1
