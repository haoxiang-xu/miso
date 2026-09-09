from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from unchain.journal import ResourceRef
from unchain.memory.curator import (
    BoundCurationRepository,
    CandidateOrigin,
    CandidateOutcome,
    CandidateResolution,
    CandidateStatus,
    ConsolidationJobStatus,
    CurationRepositoryError,
    CuratorCoordinator,
    CuratorRunResult,
    CuratorRunnerFailure,
    FailureRetryability,
    FrozenCandidateSnapshot,
    ProcessDisposition,
)
from unchain.memory.workspace import (
    CandidateStatus as WorkspaceCandidateStatus,
    JobStatus as WorkspaceJobStatus,
)

from .test_curator_coordinator import (
    FakeCurationRepository,
    FakeRunner,
    completion,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def bound_candidate(
    value: str = "candidate-bound",
    *,
    kind: str = "file",
    target_space_id: str = "space-chat",
    outcome: CandidateStatus = CandidateStatus.QUEUED,
) -> FrozenCandidateSnapshot:
    candidate_ref = ResourceRef("memory_candidate", value, 4)
    content_ref = candidate_ref if kind == "file" else None
    return FrozenCandidateSnapshot(
        candidate_ref=candidate_ref,
        target_space_id=target_space_id,
        binding_revision=1,
        outcome=outcome,
        origin=CandidateOrigin.AGENT_PROPOSAL,
        target_path=f"/notes/{value}.md" if kind != "folder" else f"/notes/{value}",
        name=f"{value}.md" if kind != "folder" else value,
        description="Durable decision captured for this chat",
        kind=kind,
        media_type="text/markdown" if kind == "file" else "",
        content_ref=content_ref,
        link_url="https://example.test/reference" if kind == "link" else "",
        source_refs=(ResourceRef("context_event", f"event-{value}", 1),),
        source_agent_run_id="agent-run-a",
        source_tool_call_id="tool-call-a",
        rationale="The user explicitly asked to retain this decision",
        confidence=0.95,
        sensitivity="private",
        payload_sha256=SHA_A,
        content_sha256=SHA_B if kind == "file" else "",
        byte_length=42 if kind == "file" else 0,
    )


def applied_result(job) -> CuratorRunResult:
    return CuratorRunResult(
        resolutions=tuple(
            CandidateResolution(
                candidate_ref=item.candidate_ref,
                target_space_id=item.target_space_id,
                outcome=CandidateOutcome.APPLIED,
                result_ref=ResourceRef(
                    "memory",
                    item.candidate_ref.resource_id,
                    1,
                    item.target_space_id,
                ),
            )
            for item in job.candidates
        )
    )


def enqueue_and_claim(repository, *, clock=lambda: 1000):
    coordinator = CuratorCoordinator(repository, clock_ms=clock)
    enqueued = coordinator.enqueue(completion())
    claimed = coordinator.claim_next(
        worker_id="worker-a",
        lease_ms=1000,
        operation_id="hardening-claim-a",
    )
    assert enqueued.job is not None
    assert claimed is not None
    return coordinator, claimed


def test_frozen_binding_contains_every_schema_v4_reconstruction_field():
    frozen = bound_candidate()
    encoded = frozen.to_dict()

    assert frozen.is_durable_binding
    assert encoded["target_space_id"] == "space-chat"
    assert encoded["binding_revision"] == 1
    assert encoded["outcome"] == "queued"
    assert encoded["content_ref"] == frozen.candidate_ref.to_dict()
    assert encoded["source_agent_run_id"] == "agent-run-a"
    assert encoded["source_tool_call_id"] == "tool-call-a"
    assert encoded["rationale"]
    assert encoded["confidence"] == 0.95
    assert encoded["sensitivity"] == "private"
    assert "content" not in encoded


def test_schema_v4_candidate_references_reject_unrepresentable_fragments():
    with pytest.raises(ValueError, match="candidate_ref fragment"):
        replace(
            bound_candidate(),
            candidate_ref=ResourceRef(
                "memory_candidate",
                "candidate-bound",
                4,
                "unrepresentable",
            ),
        )

    candidate = bound_candidate()
    with pytest.raises(ValueError, match="candidate_ref fragment"):
        CandidateResolution(
            candidate_ref=ResourceRef(
                "memory_candidate",
                candidate.candidate_ref.resource_id,
                candidate.candidate_ref.revision,
                "unrepresentable",
            ),
            target_space_id=candidate.target_space_id,
            outcome=CandidateOutcome.APPLIED,
            result_ref=ResourceRef(
                "memory",
                "entry-chat",
                1,
                candidate.target_space_id,
            ),
        )


def test_frozen_binding_enforces_file_link_and_folder_payload_shapes():
    assert bound_candidate("link-a", kind="link").link_url.startswith("https://")
    assert bound_candidate("folder-a", kind="folder").content_ref is None

    with pytest.raises(ValueError, match="file candidate"):
        replace(bound_candidate(), content_ref=None)
    with pytest.raises(ValueError, match="link candidate"):
        replace(
            bound_candidate("link-b", kind="link"),
            link_url="",
        )
    with pytest.raises(ValueError, match="folder candidate"):
        replace(
            bound_candidate("folder-b", kind="folder"),
            content_ref=ResourceRef("artifact", "unexpected", 1),
            content_sha256=SHA_B,
            byte_length=1,
        )


def test_applied_and_superseded_refs_are_bound_to_candidate_chat_space():
    candidate = bound_candidate()
    CandidateResolution(
        candidate_ref=candidate.candidate_ref,
        target_space_id=candidate.target_space_id,
        outcome=CandidateOutcome.APPLIED,
        result_ref=ResourceRef("memory", "entry-chat", 1, "space-chat"),
    )

    with pytest.raises(ValueError, match="target chat space"):
        CandidateResolution(
            candidate_ref=candidate.candidate_ref,
            target_space_id=candidate.target_space_id,
            outcome=CandidateOutcome.APPLIED,
            result_ref=ResourceRef("memory", "entry-long-term", 1, "space-long-term"),
        )
    with pytest.raises(ValueError, match="target chat space"):
        CandidateResolution(
            candidate_ref=candidate.candidate_ref,
            target_space_id=candidate.target_space_id,
            outcome=CandidateOutcome.SUPERSEDED,
            result_ref=ResourceRef("memory", "entry-long-term", 2, "space-long-term"),
        )


@pytest.mark.parametrize(
    "review_diff",
    [
        {"nested": {"x": {"x": {"x": {"x": {"x": {"x": {"x": {"x": {"x": 1}}}}}}}}}},
        {"items": list(range(513))},
        {"preview": "x" * (32 * 1024)},
    ],
)
def test_review_diff_is_bounded_by_depth_items_and_bytes(review_diff):
    candidate = bound_candidate()
    with pytest.raises(ValueError, match="review diff limit"):
        CandidateResolution(
            candidate_ref=candidate.candidate_ref,
            target_space_id=candidate.target_space_id,
            outcome=CandidateOutcome.AWAITING_USER,
            result_ref=ResourceRef(
                "memory_review",
                "review-a",
                1,
                candidate.target_space_id,
            ),
            review_diff=review_diff,
        )


def test_expired_lease_fails_before_runner_even_before_guard_check():
    repository = FakeCurationRepository((bound_candidate(),))
    _, claimed = enqueue_and_claim(repository)
    runner = FakeRunner(claimed, result=applied_result(claimed))
    expired = CuratorCoordinator(
        repository,
        clock_ms=lambda: claimed.lease.expires_at_ms,
    )

    result = expired.process_claimed(claimed, runner=runner)

    assert result.disposition is ProcessDisposition.LEASE_LOST
    assert result.reason == "lease_expired"
    assert runner.requests == []


def test_process_guard_blocks_same_binding_recursion_started_on_new_thread():
    repository = FakeCurationRepository((bound_candidate(),))
    coordinator, claimed = enqueue_and_claim(repository)
    nested_results = []
    nested_runner = FakeRunner(claimed, result=applied_result(claimed))

    def recurse_from_thread(_request, _guard):
        thread = threading.Thread(
            target=lambda: nested_results.append(
                coordinator.process_claimed(claimed, runner=nested_runner)
            )
        )
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()

    outer_runner = FakeRunner(
        claimed,
        result=applied_result(claimed),
        callback=recurse_from_thread,
    )

    outer = coordinator.process_claimed(claimed, runner=outer_runner)

    assert outer.disposition is ProcessDisposition.COMPLETED
    assert nested_results[0].disposition is ProcessDisposition.RECURSION_BLOCKED
    assert nested_runner.requests == []


def test_process_guard_blocks_concurrent_duplicate_callback_for_same_job():
    repository = FakeCurationRepository((bound_candidate(),))
    coordinator, claimed = enqueue_and_claim(repository)
    entered = threading.Event()
    release = threading.Event()
    first_results = []

    def block_runner(_request, _guard):
        entered.set()
        assert release.wait(timeout=2)

    first_runner = FakeRunner(
        claimed,
        result=applied_result(claimed),
        callback=block_runner,
    )
    thread = threading.Thread(
        target=lambda: first_results.append(
            coordinator.process_claimed(claimed, runner=first_runner)
        )
    )
    thread.start()
    assert entered.wait(timeout=2)

    duplicate_runner = FakeRunner(claimed, result=applied_result(claimed))
    duplicate = coordinator.process_claimed(claimed, runner=duplicate_runner)
    release.set()
    thread.join(timeout=2)

    assert duplicate.disposition is ProcessDisposition.RECURSION_BLOCKED
    assert duplicate_runner.requests == []
    assert first_results[0].disposition is ProcessDisposition.COMPLETED


def test_process_guard_allows_independent_bindings_to_run_concurrently():
    repository_a = FakeCurationRepository((bound_candidate("candidate-a"),))
    coordinator_a, claimed_a = enqueue_and_claim(repository_a)
    repository_b = FakeCurationRepository(
        (bound_candidate("candidate-b", target_space_id="space-chat-b"),),
        binding_id="binding-b",
    )
    coordinator_b, claimed_b = enqueue_and_claim(repository_b)
    entered = threading.Event()
    release = threading.Event()
    results_a = []

    runner_a = FakeRunner(
        claimed_a,
        result=applied_result(claimed_a),
        callback=lambda _request, _guard: (
            entered.set(),
            release.wait(timeout=2),
        ),
    )
    thread = threading.Thread(
        target=lambda: results_a.append(
            coordinator_a.process_claimed(claimed_a, runner=runner_a)
        )
    )
    thread.start()
    assert entered.wait(timeout=2)

    runner_b = FakeRunner(
        claimed_b,
        result=applied_result(claimed_b),
        binding_id="binding-b",
    )
    result_b = coordinator_b.process_claimed(claimed_b, runner=runner_b)
    release.set()
    thread.join(timeout=2)

    assert result_b.disposition is ProcessDisposition.COMPLETED
    assert results_a[0].disposition is ProcessDisposition.COMPLETED


def test_retry_transition_captures_one_now_and_validates_pending_return_state():
    repository = FakeCurationRepository((bound_candidate(),))
    _, claimed = enqueue_and_claim(repository)
    values = iter((1000, 2000, 3000))
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: next(values))
    runner = FakeRunner(
        claimed,
        error=CuratorRunnerFailure(
            "runner_temporarily_unavailable",
            retryability=FailureRetryability.RETRYABLE,
            retry_delay_ms=5000,
        ),
    )

    result = coordinator.process_claimed(claimed, runner=runner)

    assert result.disposition is ProcessDisposition.RETRY_SCHEDULED
    assert result.job.next_attempt_at_ms == 7000
    assert repository.failures[-1][-1] == 2000


def test_enqueue_rejects_repository_candidate_reordering():
    class ReorderingRepository(FakeCurationRepository):
        def enqueue(self, **kwargs):
            job = super().enqueue(**kwargs)
            return replace(job, candidates=tuple(reversed(job.candidates)))

    repository = ReorderingRepository(
        (bound_candidate("candidate-a"), bound_candidate("candidate-b"))
    )
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1000)

    with pytest.raises(CurationRepositoryError, match="repository_state_mismatch"):
        coordinator.enqueue(completion())


def test_replay_rejects_noncanonical_repository_candidate_order():
    repository = FakeCurationRepository(
        (bound_candidate("candidate-a"), bound_candidate("candidate-b"))
    )
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1000)
    first = coordinator.enqueue(completion()).job
    repository.jobs[first.job_id] = replace(
        first,
        candidates=tuple(reversed(first.candidates)),
    )

    with pytest.raises(CurationRepositoryError, match="repository_state_mismatch"):
        coordinator.enqueue(completion())


def test_replay_rejects_modified_enqueue_operation_identity():
    repository = FakeCurationRepository((bound_candidate(),))
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1000)
    first = coordinator.enqueue(completion()).job
    repository.jobs[first.job_id] = replace(
        first,
        operation_id="different-enqueue-operation",
    )

    with pytest.raises(CurationRepositoryError, match="repository_state_mismatch"):
        coordinator.enqueue(completion())


def test_enqueue_rejects_repository_mutation_of_frozen_candidate_fields():
    class MutatingRepository(FakeCurationRepository):
        def enqueue(self, **kwargs):
            job = super().enqueue(**kwargs)
            mutated = replace(
                job.candidates[0],
                description="repository changed the frozen description",
            )
            return replace(job, candidates=(mutated, *job.candidates[1:]))

    repository = MutatingRepository((bound_candidate(),))
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1000)

    with pytest.raises(CurationRepositoryError, match="repository_state_mismatch"):
        coordinator.enqueue(completion())


def test_enqueue_rejects_repository_mutation_of_frozen_job_fields():
    class MutatingRepository(FakeCurationRepository):
        def enqueue(self, **kwargs):
            job = super().enqueue(**kwargs)
            return replace(job, operation_id="different-enqueue-operation")

    repository = MutatingRepository((bound_candidate(),))
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1000)

    with pytest.raises(CurationRepositoryError, match="repository_state_mismatch"):
        coordinator.enqueue(completion())


def test_completion_rejects_repository_mutation_of_frozen_candidate_fields():
    class MutatingRepository(FakeCurationRepository):
        def reconcile_and_complete(self, **kwargs):
            job = super().reconcile_and_complete(**kwargs)
            mutated = replace(
                job.candidates[0],
                description="repository changed the frozen description",
            )
            return replace(job, candidates=(mutated, *job.candidates[1:]))

    repository = MutatingRepository((bound_candidate(),))
    coordinator, claimed = enqueue_and_claim(repository)
    runner = FakeRunner(claimed, result=applied_result(claimed))

    with pytest.raises(CurationRepositoryError, match="repository_state_mismatch"):
        coordinator.process_claimed(claimed, runner=runner)


def test_completion_rejects_repository_mutation_of_frozen_job_fields():
    class MutatingRepository(FakeCurationRepository):
        def reconcile_and_complete(self, **kwargs):
            job = super().reconcile_and_complete(**kwargs)
            return replace(job, operation_id="different-enqueue-operation")

    repository = MutatingRepository((bound_candidate(),))
    coordinator, claimed = enqueue_and_claim(repository)
    runner = FakeRunner(claimed, result=applied_result(claimed))

    with pytest.raises(CurationRepositoryError, match="repository_state_mismatch"):
        coordinator.process_claimed(claimed, runner=runner)


def test_completion_rejects_repository_timestamp_other_than_requested_now():
    class MutatingRepository(FakeCurationRepository):
        def reconcile_and_complete(self, **kwargs):
            job = super().reconcile_and_complete(**kwargs)
            return replace(job, updated_at_ms=job.updated_at_ms + 1)

    repository = MutatingRepository((bound_candidate(),))
    coordinator, claimed = enqueue_and_claim(repository)
    runner = FakeRunner(claimed, result=applied_result(claimed))

    with pytest.raises(CurationRepositoryError, match="repository_state_mismatch"):
        coordinator.process_claimed(claimed, runner=runner)


def test_cancel_rejects_non_cancelled_repository_return():
    class PendingRepository(FakeCurationRepository):
        def cancel(self, **kwargs):
            return kwargs["job"]

    repository = PendingRepository((bound_candidate(),))
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1000)
    job = coordinator.enqueue(completion()).job

    with pytest.raises(CurationRepositoryError, match="repository_state_mismatch"):
        coordinator.cancel(
            job,
            reason="user_cancelled",
            operation_id="invalid-cancel-return",
        )


def test_cancel_rejects_repository_mutation_of_frozen_candidate_fields():
    class MutatingRepository(FakeCurationRepository):
        def cancel(self, **kwargs):
            job = super().cancel(**kwargs)
            mutated = replace(
                job.candidates[0],
                description="repository changed the frozen description",
            )
            return replace(job, candidates=(mutated, *job.candidates[1:]))

    repository = MutatingRepository((bound_candidate(),))
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1000)
    job = coordinator.enqueue(completion()).job

    with pytest.raises(CurationRepositoryError, match="repository_state_mismatch"):
        coordinator.cancel(
            job,
            reason="user_cancelled",
            operation_id="mutated-cancel-return",
        )


def test_repository_retry_state_mismatch_fails_closed():
    class LyingRepository(FakeCurationRepository):
        def fail(self, **kwargs):
            super().fail(**kwargs)
            return kwargs["job"]

    repository = LyingRepository((bound_candidate(),))
    coordinator, claimed = enqueue_and_claim(repository)
    runner = FakeRunner(
        claimed,
        error=CuratorRunnerFailure(
            "runner_temporarily_unavailable",
            retryability=FailureRetryability.RETRYABLE,
            retry_delay_ms=5000,
        ),
    )

    with pytest.raises(CurationRepositoryError, match="repository_state_mismatch"):
        coordinator.process_claimed(claimed, runner=runner)


def test_repository_error_code_is_identifier_bounded_and_never_exception_text():
    assert CurationRepositoryError("Lease-Lost").code == "lease-lost"
    error = CurationRepositoryError(RuntimeError("SECRET raw database failure"))
    assert error.code == "curation_repository_error"
    assert "SECRET" not in str(error)


def test_repository_contract_requires_atomic_resolution_reconciliation():
    assert hasattr(BoundCurationRepository, "reconcile_and_complete")
    assert not hasattr(BoundCurationRepository, "complete")


def test_curator_status_enums_are_canonical_for_workspace_records():
    assert WorkspaceCandidateStatus is CandidateStatus
    assert WorkspaceJobStatus is ConsolidationJobStatus
