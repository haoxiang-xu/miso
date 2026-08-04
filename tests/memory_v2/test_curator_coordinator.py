from __future__ import annotations

from dataclasses import replace

import pytest

from unchain.journal import OperationRef, ResourceRef
from unchain.memory.curator import (
    CandidateOrigin,
    CandidateOutcome,
    CandidateResolution,
    CandidateStatus,
    ConsolidationJob,
    ConsolidationJobStatus,
    CurationConflictError,
    CuratorCoordinator,
    CuratorLeaseFence,
    CuratorRunResult,
    CuratorRunnerFailure,
    EnqueueDisposition,
    FailureRetryability,
    FrozenCandidateSnapshot,
    Lease,
    ProcessDisposition,
    RootRunCompletion,
    RunCaptureStatus,
    SourceRunStatus,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def event_ref(value: str) -> ResourceRef:
    return ResourceRef("context_event", value, 1)


def candidate(value: str, *, origin=CandidateOrigin.AGENT_PROPOSAL):
    return FrozenCandidateSnapshot(
        candidate_ref=ResourceRef("memory_candidate", value, 1),
        origin=origin,
        target_path=f"/notes/{value}.md",
        name=f"{value}.md",
        description=f"Description for {value}",
        kind="markdown",
        media_type="text/markdown",
        source_refs=(event_ref(f"event-{value}"),),
        payload_sha256=SHA_A,
        content_sha256=SHA_B,
        byte_length=24,
    )


def completion(**overrides):
    values = {
        "session_id": "session-a",
        "attempt_id": "attempt-a",
        "run_id": "run-a",
        "is_root_run": True,
        "run_status": SourceRunStatus.COMPLETED,
        "capture_status": RunCaptureStatus.COMPLETE,
    }
    values.update(overrides)
    return RootRunCompletion(**values)


class FakeCurationRepository:
    """Persistent fake: replacing the coordinator simulates process restart."""

    def __init__(self, candidates=(), *, binding_id="binding-a"):
        self.binding_id = binding_id
        self.candidates = list(candidates)
        self.jobs = {}
        self.trigger_jobs = {}
        self.operations = {}
        self.isolations = []
        self.completions = []
        self.failures = []
        self.cancellations = []
        self.now_ms = 1000

    def _replay(self, operation, value=None):
        previous = self.operations.get(operation.operation_id)
        if previous is not None:
            payload_hash, result = previous
            if payload_hash != operation.payload_sha256:
                raise CurationConflictError("operation_payload_conflict")
            return result
        if value is not None:
            self.operations[operation.operation_id] = (
                operation.payload_sha256,
                value,
            )
        return None

    def find_job_by_trigger(self, *, trigger_key):
        job_id = self.trigger_jobs.get(trigger_key)
        return self.jobs.get(job_id) if job_id else None

    def list_pending_candidates(self, *, completion, limit):
        del completion
        return tuple(self.candidates[:limit])

    def isolate_source_candidates(self, *, completion, reason, operation):
        replay = self._replay(operation)
        if replay is not None:
            return replay
        result = len(self.candidates)
        self.isolations.append((completion, reason, operation))
        self._replay(operation, result)
        return result

    def enqueue(self, *, request, operation):
        replay = self._replay(operation)
        if replay is not None:
            return replay
        existing = self.find_job_by_trigger(trigger_key=request.trigger.trigger_key)
        if existing is not None:
            raise CurationConflictError("trigger_already_has_job")
        bound_candidates = tuple(
            item
            if item.is_durable_binding
            else item.bind(
                target_space_id=f"space-{self.binding_id}",
                binding_revision=1,
                outcome=CandidateStatus.QUEUED,
                storage_kind=(
                    "file" if item.kind in {"markdown", "image"} else item.kind
                ),
            )
            for item in request.candidates
        )
        job = ConsolidationJob.pending(
            job_id=f"job-{len(self.jobs) + 1}",
            trigger=request.trigger,
            candidates=bound_candidates,
            operation_id=operation.operation_id,
            now_ms=self.now_ms,
        )
        self.jobs[job.job_id] = job
        self.trigger_jobs[job.trigger.trigger_key] = job.job_id
        self._replay(operation, job)
        return job

    def read_job(self, *, job_id):
        return self.jobs[job_id]

    def bind_mutation_guard(self, *, job):
        return FakeMutationGuard(self, job)

    def claim_next(self, *, worker_id, now_ms, lease_ms, operation):
        replay = self._replay(operation)
        if replay is not None:
            return replay
        claimable = sorted(self.jobs.values(), key=lambda item: item.created_at_ms)
        selected = next(
            (
                job
                for job in claimable
                if job.next_attempt_at_ms <= now_ms
                and (
                    job.status is ConsolidationJobStatus.PENDING
                    or (
                        job.status is ConsolidationJobStatus.LEASED
                        and job.lease.expires_at_ms <= now_ms
                    )
                )
            ),
            None,
        )
        if selected is None:
            self._replay(operation, False)
            return None
        claimed = selected.with_lease(
            Lease(
                owner=worker_id,
                token=f"lease-{selected.job_id}-{selected.attempt_count + 1}",
                expires_at_ms=now_ms + lease_ms,
            ),
            revision=selected.revision + 1,
            now_ms=now_ms,
        )
        self.jobs[claimed.job_id] = claimed
        self._replay(operation, claimed)
        return claimed

    def reconcile_and_complete(
        self,
        *,
        job,
        resolutions,
        mutation_guard,
        operation,
        now_ms,
    ):
        replay = self._replay(operation)
        if replay is not None:
            return replay
        current = self.jobs[job.job_id]
        self._assert_lease(current, job)
        if mutation_guard.fence != CuratorLeaseFence.from_job(self.binding_id, current):
            raise CurationConflictError("lease_fence_lost")
        mutation_guard.assert_active()
        if current.lease.expires_at_ms <= now_ms:
            raise CurationConflictError("lease_expired")
        by_ref = {item.candidate_ref: item for item in resolutions}
        reconciled = tuple(
            item.with_outcome(
                by_ref[item.candidate_ref].candidate_status,
                result_ref=by_ref[item.candidate_ref].result_ref,
                review_diff=by_ref[item.candidate_ref].review_diff,
            )
            for item in current.candidates
        )
        completed = current.terminal(
            status=ConsolidationJobStatus.COMPLETED,
            revision=current.revision + 1,
            now_ms=now_ms,
            candidates=reconciled,
        )
        self.jobs[job.job_id] = completed
        self.completions.append((job, resolutions, operation))
        self._replay(operation, completed)
        return completed

    def fail(self, *, job, error_code, retry_at_ms, operation, now_ms):
        replay = self._replay(operation)
        if replay is not None:
            return replay
        current = self.jobs[job.job_id]
        self._assert_lease(current, job)
        if retry_at_ms > now_ms:
            failed = current.retry(
                revision=current.revision + 1,
                retry_at_ms=retry_at_ms,
                error_code=error_code,
                now_ms=now_ms,
            )
        else:
            failed = current.terminal(
                status=ConsolidationJobStatus.FAILED,
                revision=current.revision + 1,
                now_ms=now_ms,
                error_code=error_code,
            )
        self.jobs[job.job_id] = failed
        self.failures.append((job, error_code, retry_at_ms, operation, now_ms))
        self._replay(operation, failed)
        return failed

    def cancel(self, *, job, reason, operation, now_ms):
        replay = self._replay(operation)
        if replay is not None:
            return replay
        current = self.jobs[job.job_id]
        if current.revision != job.revision:
            raise CurationConflictError("revision_conflict")
        cancelled = current.terminal(
            status=ConsolidationJobStatus.CANCELLED,
            revision=current.revision + 1,
            now_ms=now_ms,
            error_code=reason,
        )
        self.jobs[job.job_id] = cancelled
        self.cancellations.append((job, reason, operation))
        self._replay(operation, cancelled)
        return cancelled

    @staticmethod
    def _assert_lease(current, supplied):
        if (
            current.revision != supplied.revision
            or current.status is not ConsolidationJobStatus.LEASED
            or current.lease != supplied.lease
        ):
            raise CurationConflictError("lease_lost")


class FakeRunner:
    def __init__(
        self,
        job,
        *,
        result=None,
        error=None,
        callback=None,
        binding_id="binding-a",
    ):
        self.binding_id = binding_id
        self.job_id = job.job_id
        self.lease_fence = CuratorLeaseFence.from_job(binding_id, job)
        self.candidate_refs = tuple(
            item.candidate_ref for item in job.candidates
        )
        self.result = result
        self.error = error
        self.callback = callback
        self.requests = []
        self.mutation_guards = []
        self.toolkit = FakeRunnerToolkit(
            binding_id=self.binding_id,
            job_id=self.job_id,
            candidate_refs=self.candidate_refs,
            lease_fence=self.lease_fence,
        )

    def run(self, request, *, mutation_guard):
        self.requests.append(request)
        self.mutation_guards.append(mutation_guard)
        if self.callback is not None:
            self.callback(request, mutation_guard)
        if self.error is not None:
            raise self.error
        return self.result


class FakeMutationGuard:
    def __init__(self, repository, job):
        self.repository = repository
        self.fence = CuratorLeaseFence.from_job(repository.binding_id, job)

    def assert_active(self):
        current = self.repository.jobs[self.fence.job_id]
        if (
            current.status is not ConsolidationJobStatus.LEASED
            or current.revision != self.fence.job_revision
            or current.lease is None
            or current.lease.owner != self.fence.lease_owner
            or current.lease.token != self.fence.lease_token
        ):
            raise CurationConflictError("lease_fence_lost")


class FakeRunnerToolkit:
    def __init__(self, *, binding_id, job_id, candidate_refs, lease_fence):
        self.binding_id = binding_id
        self.job_id = job_id
        self.candidate_refs = candidate_refs
        self.lease_fence = lease_fence
        self.mutation_guard = _FenceDescriptorGuard(lease_fence)


class _FenceDescriptorGuard:
    def __init__(self, fence):
        self.fence = fence

    def assert_active(self):
        raise AssertionError("descriptor-only test guard must not be invoked")


def enqueue_and_claim(repository, *, clock=lambda: 1000):
    coordinator = CuratorCoordinator(repository, clock_ms=clock)
    result = coordinator.enqueue(completion())
    assert result.disposition is EnqueueDisposition.ENQUEUED
    claimed = coordinator.claim_next(
        worker_id="worker-a",
        lease_ms=30_000,
        operation_id="claim-a",
    )
    assert claimed is not None
    return coordinator, claimed


def applied_result(job):
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


def test_no_candidate_completed_run_is_noop_and_creates_no_job():
    repository = FakeCurationRepository()
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1000)

    result = coordinator.enqueue(completion())

    assert result.disposition is EnqueueDisposition.NO_OP
    assert result.reason == "no_pending_candidates"
    assert repository.jobs == {}


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"is_root_run": False}, "not_root_run"),
        ({"run_status": SourceRunStatus.FAILED}, "source_run_failed"),
        ({"run_status": SourceRunStatus.CANCELLED}, "source_run_cancelled"),
        ({"capture_status": RunCaptureStatus.PARTIAL}, "source_capture_partial"),
        ({"capture_status": RunCaptureStatus.UNAVAILABLE}, "source_capture_unavailable"),
    ],
)
def test_ineligible_sources_are_isolated_without_job(changes, reason):
    repository = FakeCurationRepository((candidate("candidate-a"),))
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1000)

    result = coordinator.enqueue(completion(**changes))

    assert result.disposition is EnqueueDisposition.ISOLATED
    assert result.reason == reason
    assert result.isolated_candidate_count == 1
    assert not repository.jobs
    assert len(repository.isolations) == 1


def test_root_run_enqueue_is_idempotent_and_freezes_exact_candidate_revisions():
    original = candidate("candidate-a")
    repository = FakeCurationRepository((original,))
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1000)

    first = coordinator.enqueue(completion())
    repository.candidates[0] = replace(
        original,
        description="mutated after enqueue",
        payload_sha256="c" * 64,
    )
    second = coordinator.enqueue(completion())

    assert first.disposition is EnqueueDisposition.ENQUEUED
    assert second.disposition is EnqueueDisposition.REPLAYED
    assert first.job.job_id == second.job.job_id
    assert len(repository.jobs) == 1
    assert first.job.candidates[0].candidate_ref == original.candidate_ref
    assert first.job.candidates[0].payload_sha256 == original.payload_sha256
    assert first.job.candidates[0].is_durable_binding
    assert first.job.candidates[0].description == "Description for candidate-a"


def test_enqueue_rejects_more_than_200_candidates_without_partial_job():
    repository = FakeCurationRepository(
        tuple(candidate(f"candidate-{index}") for index in range(201))
    )
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1000)

    result = coordinator.enqueue(completion())

    assert result.disposition is EnqueueDisposition.REJECTED
    assert result.reason == "candidate_limit_exceeded"
    assert repository.jobs == {}


def test_claim_reclaims_only_an_expired_lease_and_survives_coordinator_restart():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    first = CuratorCoordinator(repository, clock_ms=lambda: 1000)
    first.enqueue(completion())
    claimed = first.claim_next(
        worker_id="worker-a",
        lease_ms=1000,
        operation_id="claim-a",
    )
    assert claimed.lease.token == "lease-job-1-1"

    before_expiry = CuratorCoordinator(repository, clock_ms=lambda: 1999)
    assert before_expiry.claim_next(
        worker_id="worker-b",
        lease_ms=1000,
        operation_id="claim-b-before",
    ) is None

    after_expiry = CuratorCoordinator(repository, clock_ms=lambda: 2000)
    reclaimed = after_expiry.claim_next(
        worker_id="worker-b",
        lease_ms=1000,
        operation_id="claim-b-after",
    )
    assert reclaimed.job_id == claimed.job_id
    assert reclaimed.revision == claimed.revision + 1
    assert reclaimed.lease.owner == "worker-b"
    assert reclaimed.lease.token != claimed.lease.token


def test_success_requires_one_terminal_resolution_for_every_frozen_candidate():
    repository = FakeCurationRepository(
        (candidate("candidate-a"), candidate("candidate-b"))
    )
    coordinator, claimed = enqueue_and_claim(repository)
    incomplete = CuratorRunResult(
        resolutions=(
            CandidateResolution(
                candidate_ref=claimed.candidates[0].candidate_ref,
                target_space_id=claimed.candidates[0].target_space_id,
                outcome=CandidateOutcome.APPLIED,
                result_ref=ResourceRef(
                    "memory",
                    "entry-a",
                    1,
                    claimed.candidates[0].target_space_id,
                ),
            ),
        )
    )
    runner = FakeRunner(claimed, result=incomplete)

    result = coordinator.process_claimed(claimed, runner=runner)

    assert result.disposition is ProcessDisposition.FAILED
    assert result.reason == "invalid_runner_result"
    assert repository.jobs[claimed.job_id].status is ConsolidationJobStatus.FAILED
    assert repository.completions == []


def test_success_persists_applied_and_review_required_diffs_then_completes():
    repository = FakeCurationRepository(
        (candidate("candidate-a"), candidate("candidate-b"))
    )
    coordinator, claimed = enqueue_and_claim(repository)
    run_result = CuratorRunResult(
        resolutions=(
            CandidateResolution(
                candidate_ref=claimed.candidates[0].candidate_ref,
                target_space_id=claimed.candidates[0].target_space_id,
                outcome=CandidateOutcome.APPLIED,
                result_ref=ResourceRef(
                    "memory",
                    "entry-a",
                    1,
                    claimed.candidates[0].target_space_id,
                ),
            ),
            CandidateResolution(
                candidate_ref=claimed.candidates[1].candidate_ref,
                target_space_id=claimed.candidates[1].target_space_id,
                outcome=CandidateOutcome.AWAITING_USER,
                result_ref=ResourceRef(
                    "memory_review",
                    "review-b",
                    1,
                    claimed.candidates[1].target_space_id,
                ),
                review_diff={
                    "mode": "overwrite",
                    "before_sha256": "c" * 64,
                    "after_sha256": "d" * 64,
                },
            ),
        )
    )
    runner = FakeRunner(claimed, result=run_result)

    result = coordinator.process_claimed(claimed, runner=runner)

    assert result.disposition is ProcessDisposition.COMPLETED
    assert result.job.status is ConsolidationJobStatus.COMPLETED
    assert len(repository.completions) == 1
    persisted = repository.completions[0][1]
    assert persisted[1].outcome is CandidateOutcome.AWAITING_USER
    assert persisted[1].review_diff["mode"] == "overwrite"
    assert result.job.candidates[0].outcome is CandidateStatus.APPLIED
    assert result.job.candidates[1].outcome is CandidateStatus.AWAITING_USER
    assert result.job.candidates[1].review_diff["mode"] == "overwrite"
    assert runner.requests[0].policy.allow_long_term_write is False
    assert runner.requests[0].policy.allow_promotion_decision is False
    assert runner.requests[0].lease_fence == runner.lease_fence
    assert runner.mutation_guards[0].fence == runner.lease_fence


def test_retryable_runner_failure_requeues_job_with_safe_code():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    coordinator, claimed = enqueue_and_claim(repository)
    runner = FakeRunner(
        claimed,
        error=CuratorRunnerFailure(
            "model_temporarily_unavailable",
            retryability=FailureRetryability.RETRYABLE,
            retry_delay_ms=5000,
        ),
    )

    result = coordinator.process_claimed(claimed, runner=runner)

    assert result.disposition is ProcessDisposition.RETRY_SCHEDULED
    assert result.reason == "model_temporarily_unavailable"
    assert result.job.status is ConsolidationJobStatus.PENDING
    assert result.job.next_attempt_at_ms == 6000
    assert repository.failures[0][1] == "model_temporarily_unavailable"


def test_unknown_runner_exception_is_terminal_and_message_is_not_exposed():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    coordinator, claimed = enqueue_and_claim(repository)
    runner = FakeRunner(
        claimed,
        error=RuntimeError("SECRET_PRIVATE_REASONING_MUST_NOT_ESCAPE"),
    )

    result = coordinator.process_claimed(claimed, runner=runner)

    assert result.disposition is ProcessDisposition.FAILED
    assert result.reason == "runner_runtimeerror"
    assert "SECRET" not in repr(result)
    assert repository.jobs[claimed.job_id].status is ConsolidationJobStatus.FAILED


def test_cas_conflict_after_runner_does_not_rerun_or_overwrite_new_state():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    coordinator, claimed = enqueue_and_claim(repository)

    def steal_after_runner_starts(_request, _guard):
        repository.jobs[claimed.job_id] = replace(
            claimed,
            revision=claimed.revision + 1,
        )

    runner = FakeRunner(
        claimed,
        result=applied_result(claimed),
        callback=steal_after_runner_starts,
    )

    result = coordinator.process_claimed(claimed, runner=runner)

    assert result.disposition is ProcessDisposition.LEASE_LOST
    assert len(runner.requests) == 1
    assert repository.completions == []


def test_stale_runner_capability_write_is_fenced_before_entry_or_review_effect():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    coordinator, claimed = enqueue_and_claim(repository)
    effects = []

    def steal_then_attempt_write(_request, guard):
        repository.jobs[claimed.job_id] = replace(
            claimed,
            lease=Lease(
                owner="worker-b",
                token="lease-stolen",
                expires_at_ms=5000,
            ),
            revision=claimed.revision + 1,
            updated_at_ms=1001,
        )
        guard.assert_active()
        effects.append("entry-or-review-created")

    runner = FakeRunner(
        claimed,
        result=applied_result(claimed),
        callback=steal_then_attempt_write,
    )

    result = coordinator.process_claimed(claimed, runner=runner)

    assert result.disposition is ProcessDisposition.LEASE_LOST
    assert effects == []
    assert repository.completions == []


def test_completed_job_is_not_reinvoked_after_process_restart():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    coordinator, claimed = enqueue_and_claim(repository)
    first_runner = FakeRunner(claimed, result=applied_result(claimed))
    first = coordinator.process_claimed(claimed, runner=first_runner)
    assert first.disposition is ProcessDisposition.COMPLETED

    restarted = CuratorCoordinator(repository, clock_ms=lambda: 1001)
    second_runner = FakeRunner(claimed, result=applied_result(claimed))
    second = restarted.process_claimed(claimed, runner=second_runner)

    assert second.disposition is ProcessDisposition.ALREADY_TERMINAL
    assert second.job.status is ConsolidationJobStatus.COMPLETED
    assert second_runner.requests == []


def test_process_wide_recursion_guard_blocks_nested_curator_invocation():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    coordinator, claimed = enqueue_and_claim(repository)
    nested = []
    nested_runner = FakeRunner(claimed, result=applied_result(claimed))

    def recurse(_request, _guard):
        nested.append(coordinator.process_claimed(claimed, runner=nested_runner))

    outer_runner = FakeRunner(
        claimed,
        result=applied_result(claimed),
        callback=recurse,
    )
    outer = coordinator.process_claimed(claimed, runner=outer_runner)

    assert outer.disposition is ProcessDisposition.COMPLETED
    assert nested[0].disposition is ProcessDisposition.RECURSION_BLOCKED
    assert nested_runner.requests == []


def test_runner_binding_and_candidate_set_must_match_claimed_job():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    coordinator, claimed = enqueue_and_claim(repository)
    runner = FakeRunner(claimed, result=applied_result(claimed))
    runner.binding_id = "binding-other"

    result = coordinator.process_claimed(claimed, runner=runner)

    assert result.disposition is ProcessDisposition.FAILED
    assert result.reason == "runner_binding_mismatch"
    assert runner.requests == []
    assert repository.jobs[claimed.job_id].status is ConsolidationJobStatus.FAILED


def test_runner_toolkit_scope_must_match_the_authoritative_lease_fence():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    coordinator, claimed = enqueue_and_claim(repository)
    runner = FakeRunner(claimed, result=applied_result(claimed))
    runner.toolkit.binding_id = "binding-other"

    result = coordinator.process_claimed(claimed, runner=runner)

    assert result.disposition is ProcessDisposition.FAILED
    assert result.reason == "toolkit_binding_mismatch"
    assert runner.requests == []
    assert repository.completions == []


def test_cancel_is_cas_protected_idempotent_and_terminal():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1000)
    job = coordinator.enqueue(completion()).job

    first = coordinator.cancel(
        job,
        reason="user_cancelled",
        operation_id="cancel-a",
    )
    second = coordinator.cancel(
        job,
        reason="user_cancelled",
        operation_id="cancel-a",
    )

    assert first.status is ConsolidationJobStatus.CANCELLED
    assert second == first
    assert len(repository.cancellations) == 1


def test_operation_id_payload_conflict_fails_closed():
    repository = FakeCurationRepository((candidate("candidate-a"),))
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1000)
    coordinator.claim_next(
        worker_id="worker-a",
        lease_ms=1000,
        operation_id="shared-operation",
    )
    with pytest.raises(CurationConflictError, match="operation_payload_conflict"):
        coordinator.claim_next(
            worker_id="worker-b",
            lease_ms=2000,
            operation_id="shared-operation",
        )
