from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from unchain.journal import ResourceRef
from unchain.memory.curator import (
    CandidateOutcome,
    CandidateResolution,
    CuratorCoordinator,
    CuratorRunResult,
    EnqueueDisposition,
    ProcessDisposition,
    RootRunCompletion,
    RunCaptureStatus,
    SourceRunStatus,
)
from unchain.memory.curator.host import (
    MemoryAgentHostAdapter,
    MemoryAgentHostConfig,
    MemoryAgentWorkerDisposition,
)
from unchain.memory.toolkit import (
    CandidateProposalRequest,
    ConsolidationMemoryToolkitCapabilities,
    MemoryToolkitRunBinding,
    ReferencePurpose,
    build_memory_toolkit,
)
from unchain.memory.workspace import MemorySpace, MemoryWorkspaceService
from unchain.memory.workspace.ports import RepositoryConflictError
from unchain.persistence.sqlite_curator_v2 import SQLiteCuratorV2Store
from unchain.persistence.sqlite_memory_host_v2 import (
    SQLiteConsolidationCapabilityFactory,
    SQLiteMemoryHostV2Error,
)
from unchain.persistence.sqlite_memory_v2 import SQLiteMemoryV2Store
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store

from .fakes import FakeReferenceAuthorizer
from .test_memory_toolkit_security import FakeCodec, FakeContext


class _Clock:
    def __init__(self, now_ms: int = 1_000) -> None:
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms


class _ReviewCodec(FakeCodec):
    def encode(self, ref: ResourceRef) -> str:
        if ref.kind == "memory_review":
            return (
                f"pupu://memory/review/{ref.fragment}/"
                f"{ref.resource_id}@{ref.revision}"
            )
        if ref.kind == "memory_candidate_content":
            return (
                f"pupu://memory/candidate-content/{ref.fragment}/"
                f"{ref.resource_id}@{ref.revision}"
            )
        return super().encode(ref)


def _binding() -> MemoryToolkitRunBinding:
    return MemoryToolkitRunBinding(
        binding_id="binding-chat-a",
        session_id="session-a",
        attempt_id="attempt-a",
        run_id="run-a",
    )


def _completion(binding: MemoryToolkitRunBinding) -> RootRunCompletion:
    return RootRunCompletion(
        session_id=binding.session_id,
        attempt_id=binding.attempt_id,
        run_id=binding.run_id,
        is_root_run=True,
        run_status=SourceRunStatus.COMPLETED,
        capture_status=RunCaptureStatus.COMPLETE,
    )


def _space() -> MemorySpace:
    return MemorySpace(
        space_id="space-chat-a",
        namespace="chat",
        name="Chat memory",
        description="Memory Agent production workspace",
        revision=1,
    )


def _proposal(
    source_ref: ResourceRef,
    *,
    operation_id: str = "candidate-proposal-a",
    path: str = "/decisions/context-policy.md",
    content: bytes = b"Keep the canonical execution journal.",
) -> CandidateProposalRequest:
    return CandidateProposalRequest(
        path=path,
        description="Confirmed context policy for long-running agent tasks",
        kind="markdown",
        content=content,
        media_type="text/markdown",
        url="",
        source_refs=(source_ref,),
        rationale="Preserve a confirmed architectural decision",
        confidence=0.98,
        sensitivity="normal",
        operation_id=operation_id,
    )


def _open_stack(
    root: Path,
    *,
    clock: _Clock,
    source_ref: ResourceRef,
):
    database_path = root / "context_v2.sqlite3"
    object_directory = root / "objects"
    SQLiteContextV2Store(
        database_path=database_path,
        object_directory=object_directory,
    )
    memory_store = SQLiteMemoryV2Store(
        database_path=database_path,
        object_directory=object_directory,
    )
    workspace_repository = memory_store.bind_workspace(
        space=_space(),
        owner_chat_id="chat-a",
    )
    workspace = MemoryWorkspaceService(
        repository=workspace_repository,
        mutations=workspace_repository,
        content=workspace_repository,
        history=workspace_repository,
        links=workspace_repository,
        references=FakeReferenceAuthorizer(
            "binding-chat-a",
            {source_ref},
        ),
    )
    curator_store = SQLiteCuratorV2Store(
        database_path=database_path,
        object_directory=object_directory,
        clock_ms=clock,
    )
    curator_repository = curator_store.bind_curation(
        binding_id="binding-chat-a",
        owner_chat_id="chat-a",
        target_space_id=_space().space_id,
    )
    factory = SQLiteConsolidationCapabilityFactory(
        binding_id="binding-chat-a",
        database_path=database_path,
        repository=curator_repository,
        workspace=workspace,
        references=_ReviewCodec("binding-chat-a"),
        context=FakeContext("binding-chat-a"),
        clock_ms=clock,
    )
    return curator_repository, workspace, factory


def _enqueue_and_claim(
    repository,
    binding: MemoryToolkitRunBinding,
    *,
    operation_id: str = "claim-a",
):
    coordinator = CuratorCoordinator(repository, clock_ms=repository._store._clock_ms)
    result = coordinator.enqueue(_completion(binding))
    assert result.disposition is EnqueueDisposition.ENQUEUED
    job = coordinator.claim_next(
        worker_id="worker-a",
        lease_ms=1_000,
        operation_id=operation_id,
    )
    assert job is not None
    return job


def _build_capabilities(factory, repository, job):
    digest = hashlib.sha256(f"{job.job_id}:{job.revision}".encode("utf-8")).hexdigest()
    binding = MemoryToolkitRunBinding(
        binding_id="binding-chat-a",
        session_id=job.trigger.session_id,
        attempt_id=f"memory-curator-attempt-{digest}",
        run_id=f"memory-curator-run-{digest}",
    )
    guard = repository.bind_mutation_guard(job=job)
    return factory.build(binding=binding, job=job, mutation_guard=guard)


def test_factory_builds_exact_job_scope_and_pages_full_candidate_cas(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    binding = _binding()
    source_ref = ResourceRef("context_event", "event-a", 1)
    repository, _, factory = _open_stack(
        tmp_path,
        clock=clock,
        source_ref=source_ref,
    )
    payload = (b"complete candidate bytes\n" * 4_000) + b"tail"
    proposal = repository.bind_candidate_proposals(binding=binding)
    candidate = proposal.propose(
        request=_proposal(source_ref, content=payload),
    )
    job = _enqueue_and_claim(repository, binding)

    capabilities = _build_capabilities(factory, repository, job)

    assert isinstance(capabilities, ConsolidationMemoryToolkitCapabilities)
    assert capabilities.binding_id == repository.binding_id
    assert capabilities.job_id == job.job_id
    assert capabilities.candidate_refs == (candidate.candidate_ref,)
    assert capabilities.source_refs == (source_ref,)
    first = capabilities.consolidation.read_candidate(
        job_id=job.job_id,
        ref=candidate.candidate_ref,
        offset=11,
        limit=32_768,
    )
    second = capabilities.consolidation.read_candidate(
        job_id=job.job_id,
        ref=candidate.candidate_ref,
        offset=first.next_offset,
        limit=32_768,
    )
    assert first.data + second.data == payload[11 : 11 + 65_536]
    assert first.total_bytes == len(payload)
    assert first.sha256 == hashlib.sha256(payload).hexdigest()

    with pytest.raises(SQLiteMemoryHostV2Error, match="candidate_scope"):
        capabilities.consolidation.read_candidate(
            job_id="foreign-job",
            ref=candidate.candidate_ref,
            offset=0,
            limit=1,
        )

    forged_binding = MemoryToolkitRunBinding(
        binding_id="binding-chat-a",
        session_id=job.trigger.session_id,
        attempt_id="memory-curator-attempt-forged",
        run_id="memory-curator-run-forged",
    )
    with pytest.raises(SQLiteMemoryHostV2Error, match="run_binding_mismatch"):
        factory.build(
            binding=forged_binding,
            job=job,
            mutation_guard=capabilities.mutation_guard,
        )
    with pytest.raises(SQLiteMemoryHostV2Error, match="candidate_scope"):
        capabilities.consolidation.read_candidate(
            job_id=job.job_id,
            ref=ResourceRef("memory_candidate", "foreign-candidate", 1),
            offset=0,
            limit=1,
        )


def test_apply_new_cold_retry_reuses_exact_workspace_effect_across_lease_revision(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    binding = _binding()
    source_ref = ResourceRef("context_event", "event-a", 1)
    repository, workspace, factory = _open_stack(
        tmp_path,
        clock=clock,
        source_ref=source_ref,
    )
    content = b"Keep every semantic tool result across restart."
    candidate = repository.bind_candidate_proposals(binding=binding).propose(
        request=_proposal(source_ref, content=content),
    )
    first_job = _enqueue_and_claim(repository, binding)
    first_capabilities = _build_capabilities(factory, repository, first_job)
    assert first_capabilities.target_space_revision == 1

    first = first_capabilities.consolidation.apply_new(
        job_id=first_job.job_id,
        candidate_ref=candidate.candidate_ref,
        expected_binding_revision=first_job.candidates[0].binding_revision,
        expected_space_revision=first_capabilities.target_space_revision,
        mutation_guard=first_capabilities.mutation_guard,
        operation_id="outer-operation-first-lease",
    )
    first_ref = first["result_ref"]
    assert first["outcome"] == "applied"
    assert workspace.read(first_ref, offset=0, limit=256).data == content
    assert workspace.space.revision == 2

    # Simulate a process crash before curator reconciliation, then reclaim the
    # same job under a later job/lease revision and a different outer operation.
    clock.now_ms = 2_500
    reopened_repository, reopened_workspace, reopened_factory = _open_stack(
        tmp_path,
        clock=clock,
        source_ref=source_ref,
    )
    coordinator = CuratorCoordinator(reopened_repository, clock_ms=clock)
    second_job = coordinator.claim_next(
        worker_id="worker-b",
        lease_ms=1_000,
        operation_id="claim-after-cold-restart",
    )
    assert second_job is not None
    assert second_job.revision > first_job.revision
    second_capabilities = _build_capabilities(
        reopened_factory,
        reopened_repository,
        second_job,
    )
    assert second_capabilities.target_space_revision == 2

    replayed = second_capabilities.consolidation.apply_new(
        job_id=second_job.job_id,
        candidate_ref=candidate.candidate_ref,
        expected_binding_revision=second_job.candidates[0].binding_revision,
        expected_space_revision=second_capabilities.target_space_revision,
        mutation_guard=second_capabilities.mutation_guard,
        operation_id="outer-operation-second-lease",
    )

    assert replayed == first
    assert reopened_workspace.space.revision == 2
    page = reopened_workspace.list(parent_path="/", recursive=True, limit=20)
    assert tuple(entry.entry_id for entry in page.entries) == (first_ref.resource_id,)
    assert reopened_workspace.read(first_ref, offset=0, limit=256).data == content


def test_existing_path_requires_persisted_server_diff_and_never_overwrites(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    binding = _binding()
    source_ref = ResourceRef("context_event", "event-a", 1)
    repository, workspace, factory = _open_stack(
        tmp_path,
        clock=clock,
        source_ref=source_ref,
    )
    existing = workspace.write_markdown(
        path="/decisions/context-policy.md",
        description="Existing context policy awaiting a reviewed replacement",
        content="Old policy",
        expected_space_revision=1,
        source_refs=(source_ref,),
        operation_id="seed-conflicting-path",
    )
    candidate = repository.bind_candidate_proposals(binding=binding).propose(
        request=_proposal(source_ref, content=b"New policy"),
    )
    job = _enqueue_and_claim(repository, binding)
    capabilities = _build_capabilities(factory, repository, job)
    assert capabilities.target_space_revision == 2

    conflict = capabilities.consolidation.apply_new(
        job_id=job.job_id,
        candidate_ref=candidate.candidate_ref,
        expected_binding_revision=job.candidates[0].binding_revision,
        expected_space_revision=capabilities.target_space_revision,
        mutation_guard=capabilities.mutation_guard,
        operation_id="outer-apply-conflict",
    )

    assert conflict == {
        "outcome": "conflict",
        "reason": "path_exists",
        "candidate_ref": candidate.candidate_ref,
        "target_space_id": _space().space_id,
        "target_entry_ref": ResourceRef(
            "memory",
            existing.entry_id,
            existing.revision,
            _space().space_id,
        ),
        "server_review_required": True,
    }
    assert workspace.space.revision == 2
    review = capabilities.consolidation.propose_review(
        job_id=job.job_id,
        candidate_ref=candidate.candidate_ref,
        expected_binding_revision=job.candidates[0].binding_revision,
        target_entry_id=existing.entry_id,
        expected_target_revision=existing.revision,
        mode="overwrite",
        mutation_guard=capabilities.mutation_guard,
        operation_id="outer-review-first",
    )
    assert review["outcome"] == "awaiting_user"
    assert review["result_ref"].kind == "memory_review"
    assert review["result_ref"].fragment == _space().space_id
    assert review["review_diff"]["mode"] == "overwrite"
    assert (
        review["review_diff"]["candidate"]["content_sha256"]
        == hashlib.sha256(b"New policy").hexdigest()
    )
    assert (
        review["review_diff"]["target"]["content_sha256"]
        == hashlib.sha256(b"Old policy").hexdigest()
    )
    assert workspace.space.revision == 2

    reopened_repository, reopened_workspace, reopened_factory = _open_stack(
        tmp_path,
        clock=clock,
        source_ref=source_ref,
    )
    current_job = reopened_repository.read_job(job_id=job.job_id)
    replay_capabilities = _build_capabilities(
        reopened_factory,
        reopened_repository,
        current_job,
    )
    replay = replay_capabilities.consolidation.propose_review(
        job_id=current_job.job_id,
        candidate_ref=candidate.candidate_ref,
        expected_binding_revision=current_job.candidates[0].binding_revision,
        target_entry_id=existing.entry_id,
        expected_target_revision=existing.revision,
        mode="overwrite",
        mutation_guard=replay_capabilities.mutation_guard,
        operation_id="outer-review-after-restart",
    )
    assert replay == review
    assert reopened_workspace.space.revision == 2
    with sqlite3.connect(tmp_path / "context_v2.sqlite3") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM memory_review_proposals"
            ).fetchone()[0]
            == 1
        )


def test_casefold_equivalent_path_is_a_reviewable_conflict(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    binding = _binding()
    source_ref = ResourceRef("context_event", "event-a", 1)
    repository, workspace, factory = _open_stack(
        tmp_path,
        clock=clock,
        source_ref=source_ref,
    )
    existing = workspace.write_markdown(
        path="/Decisions/Context-Policy.md",
        description="Existing case-preserving path in the durable workspace",
        content="Old policy",
        expected_space_revision=1,
        source_refs=(source_ref,),
        operation_id="seed-casefold-conflict",
    )
    candidate = repository.bind_candidate_proposals(binding=binding).propose(
        request=_proposal(source_ref, content=b"New policy"),
    )
    job = _enqueue_and_claim(repository, binding)
    capabilities = _build_capabilities(factory, repository, job)

    conflict = capabilities.consolidation.apply_new(
        job_id=job.job_id,
        candidate_ref=candidate.candidate_ref,
        expected_binding_revision=job.candidates[0].binding_revision,
        expected_space_revision=2,
        mutation_guard=capabilities.mutation_guard,
        operation_id="outer-casefold-apply",
    )
    review = capabilities.consolidation.propose_review(
        job_id=job.job_id,
        candidate_ref=candidate.candidate_ref,
        expected_binding_revision=job.candidates[0].binding_revision,
        target_entry_id=existing.entry_id,
        expected_target_revision=existing.revision,
        mode="overwrite",
        mutation_guard=capabilities.mutation_guard,
        operation_id="outer-casefold-review",
    )

    assert conflict["outcome"] == "conflict"
    assert conflict["target_entry_ref"].resource_id == existing.entry_id
    assert review["outcome"] == "awaiting_user"
    assert (
        review["review_diff"]["candidate"]["path"]
        != review["review_diff"]["target"]["path"]
    )
    assert workspace.space.revision == 2


def test_workspace_cas_failure_never_reports_candidate_as_applied(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    binding = _binding()
    source_ref = ResourceRef("context_event", "event-a", 1)
    repository, workspace, factory = _open_stack(
        tmp_path,
        clock=clock,
        source_ref=source_ref,
    )
    candidate = repository.bind_candidate_proposals(binding=binding).propose(
        request=_proposal(source_ref),
    )
    job = _enqueue_and_claim(repository, binding)
    capabilities = _build_capabilities(factory, repository, job)

    with pytest.raises(RepositoryConflictError, match="space revision"):
        capabilities.consolidation.apply_new(
            job_id=job.job_id,
            candidate_ref=candidate.candidate_ref,
            expected_binding_revision=job.candidates[0].binding_revision,
            expected_space_revision=99,
            mutation_guard=capabilities.mutation_guard,
            operation_id="outer-stale-space",
        )

    assert workspace.space.revision == 1
    assert workspace.list(parent_path="/", recursive=True, limit=20).entries == ()


def test_apply_new_tool_uses_frozen_host_revision_and_rejects_later_write(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    binding = _binding()
    source_ref = ResourceRef("context_event", "event-a", 1)
    repository, workspace, factory = _open_stack(
        tmp_path,
        clock=clock,
        source_ref=source_ref,
    )
    candidate = repository.bind_candidate_proposals(binding=binding).propose(
        request=_proposal(source_ref),
    )
    job = _enqueue_and_claim(repository, binding)
    capabilities = _build_capabilities(factory, repository, job)
    assert capabilities.target_space_revision == 1

    workspace.write_markdown(
        path="/facts/concurrent.md",
        description="A concurrent write after the curator capability was bound",
        content="concurrent",
        expected_space_revision=1,
        source_refs=(source_ref,),
        operation_id="concurrent-write-after-curator-bind",
    )
    assert workspace.space.revision == 2

    digest = hashlib.sha256(
        f"{job.job_id}:{job.revision}".encode("utf-8")
    ).hexdigest()
    toolkit = build_memory_toolkit(
        MemoryToolkitRunBinding(
            binding_id="binding-chat-a",
            session_id=job.trigger.session_id,
            attempt_id=f"memory-curator-attempt-{digest}",
            run_id=f"memory-curator-run-{digest}",
        ),
        capabilities,
    )

    with pytest.raises(RepositoryConflictError, match="space revision"):
        toolkit.tools["memory_candidate_apply_new"].func(
            candidate_ref=_ReviewCodec("binding-chat-a").encode(
                candidate.candidate_ref
            ),
            expected_binding_revision=job.candidates[0].binding_revision,
        )

    assert workspace.space.revision == 2


class _ApplyingModelInvoker:
    def __init__(self, codec: _ReviewCodec) -> None:
        self.codec = codec
        self.calls = 0

    def run(self, request, *, toolkit, binding):
        del binding
        self.calls += 1
        candidate = request.job.candidates[0]
        result = toolkit.tools["memory_candidate_apply_new"].func(
            candidate_ref=self.codec.encode(candidate.candidate_ref),
            expected_binding_revision=candidate.binding_revision,
        )
        result_ref = self.codec.decode(
            result["result_ref"],
            purpose=ReferencePurpose.MEMORY,
        )
        return CuratorRunResult(
            resolutions=(
                CandidateResolution(
                    candidate_ref=candidate.candidate_ref,
                    target_space_id=candidate.target_space_id,
                    outcome=CandidateOutcome.APPLIED,
                    result_ref=result_ref,
                ),
            )
        )


def test_official_memory_agent_host_uses_sqlite_factory_and_reconciles(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    binding = _binding()
    source_ref = ResourceRef("context_event", "event-a", 1)
    repository, workspace, factory = _open_stack(
        tmp_path,
        clock=clock,
        source_ref=source_ref,
    )
    candidate = repository.bind_candidate_proposals(binding=binding).propose(
        request=_proposal(source_ref),
    )
    invoker = _ApplyingModelInvoker(factory.references)
    host = MemoryAgentHostAdapter(
        repository,
        capability_factory=factory,
        model_invoker=invoker,
        config=MemoryAgentHostConfig(enabled=True),
        clock_ms=clock,
    )

    enqueued = host.enqueue_root_completion(_completion(binding))
    processed = host.process_next(operation_id="official-host-claim")

    assert enqueued.result.disposition is EnqueueDisposition.ENQUEUED
    assert processed.disposition is MemoryAgentWorkerDisposition.PROCESSED
    assert processed.result.disposition is ProcessDisposition.COMPLETED
    assert invoker.calls == 1
    completed = repository.read_job(job_id=processed.claimed_job.job_id)
    assert completed.candidates[0].candidate_ref == candidate.candidate_ref
    assert completed.candidates[0].outcome.value == "applied"
    assert workspace.space.revision == 2
