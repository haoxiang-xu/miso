from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Mapping
from typing import Any, Callable

from unchain.journal import OperationRef, ResourceRef
from unchain.journal.models import _required_text

from .models import (
    CandidateStatus,
    ConsolidationJob,
    ConsolidationJobStatus,
    CuratorLeaseFence,
    CuratorPolicy,
    CuratorRunRequest,
    CuratorRunResult,
    CuratorRunnerFailure,
    EnqueueDisposition,
    EnqueueRequest,
    EnqueueResult,
    FailureRetryability,
    FrozenCandidateSnapshot,
    MAX_CANDIDATES_PER_JOB,
    MAX_LEASE_MS,
    ProcessDisposition,
    ProcessResult,
    RootRunCompletion,
    RunCaptureStatus,
    SourceRunStatus,
)
from .ports import (
    BoundCurationRepository,
    CurationConflictError,
    CurationRepositoryError,
    CuratorAgentRunner,
)


_ACTIVE_CURATOR_LOCK = threading.RLock()
_ACTIVE_CURATOR_BINDINGS: set[str] = set()
_ACTIVE_CURATOR_JOBS: set[tuple[str, str]] = set()
_TERMINAL_STATUSES = frozenset(
    {
        ConsolidationJobStatus.COMPLETED,
        ConsolidationJobStatus.FAILED,
        ConsolidationJobStatus.CANCELLED,
    }
)


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _operation(kind: str, payload: Mapping[str, Any]) -> OperationRef:
    encoded = _canonical_json(payload)
    digest = hashlib.sha256(encoded).hexdigest()
    return OperationRef(
        operation_id=f"curator.{kind}.{digest}",
        payload_sha256=digest,
    )


def _external_operation(
    operation_id: str,
    *,
    kind: str,
    payload: Mapping[str, Any],
) -> OperationRef:
    normalized = _required_text(
        operation_id,
        "operation_id",
        maximum=256,
        identifier=True,
    )
    encoded = _canonical_json({"kind": kind, **dict(payload)})
    return OperationRef(normalized, hashlib.sha256(encoded).hexdigest())


def _safe_code(value: object, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9_:-]+", "_", str(value or "").casefold()).strip("_")
    return normalized[:128] or fallback


def _resource_ref_identity(ref: ResourceRef) -> tuple[str, str, int, str]:
    """Return the complete stable identity accepted by ``ResourceRef``."""

    return (ref.kind, ref.resource_id, ref.revision, ref.fragment)


def _expected_enqueued_candidate(
    requested: FrozenCandidateSnapshot,
    returned: FrozenCandidateSnapshot,
) -> FrozenCandidateSnapshot:
    if requested.is_durable_binding:
        return requested
    storage_kind = (
        "file" if requested.kind in {"markdown", "image"} else requested.kind
    )
    return requested.bind(
        target_space_id=returned.target_space_id,
        binding_revision=1,
        outcome=CandidateStatus.QUEUED,
        storage_kind=storage_kind,
        content_ref=requested.content_ref,
    )


def _enter_curator_scope(binding_id: str, job_id: str) -> bool:
    job_key = (binding_id, job_id)
    with _ACTIVE_CURATOR_LOCK:
        if (
            binding_id in _ACTIVE_CURATOR_BINDINGS
            or job_key in _ACTIVE_CURATOR_JOBS
        ):
            return False
        _ACTIVE_CURATOR_BINDINGS.add(binding_id)
        _ACTIVE_CURATOR_JOBS.add(job_key)
        return True


def _leave_curator_scope(binding_id: str, job_id: str) -> None:
    with _ACTIVE_CURATOR_LOCK:
        _ACTIVE_CURATOR_BINDINGS.discard(binding_id)
        _ACTIVE_CURATOR_JOBS.discard((binding_id, job_id))


class CuratorCoordinator:
    """Provider-neutral P0 candidate/job lifecycle coordinator."""

    def __init__(
        self,
        repository: BoundCurationRepository,
        *,
        clock_ms: Callable[[], int] | None = None,
        policy: CuratorPolicy | None = None,
    ) -> None:
        binding_id = _required_text(
            getattr(repository, "binding_id", ""),
            "repository binding_id",
            maximum=512,
            identifier=True,
        )
        self._repository = repository
        self._binding_id = binding_id
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._policy = policy or CuratorPolicy.p0()
        if self._policy != CuratorPolicy.p0():
            raise ValueError("the P0 curator core policy cannot be weakened or replaced")

    @property
    def binding_id(self) -> str:
        return self._binding_id

    def enqueue(self, completion: RootRunCompletion) -> EnqueueResult:
        if not isinstance(completion, RootRunCompletion):
            raise TypeError("completion must be a RootRunCompletion")
        isolation_reason = self._isolation_reason(completion)
        if isolation_reason:
            operation = _operation(
                "isolate",
                {
                    "trigger_key": completion.trigger_key,
                    "reason": isolation_reason,
                },
            )
            count = self._repository.isolate_source_candidates(
                completion=completion,
                reason=isolation_reason,
                operation=operation,
            )
            return EnqueueResult(
                disposition=EnqueueDisposition.ISOLATED,
                reason=isolation_reason,
                isolated_candidate_count=count,
            )

        existing = self._repository.find_job_by_trigger(
            trigger_key=completion.trigger_key
        )
        if existing is not None:
            self._validate_replayed_job(existing, completion)
            return EnqueueResult(
                disposition=EnqueueDisposition.REPLAYED,
                reason="already_enqueued",
                job=existing,
            )

        listed = self._repository.list_pending_candidates(
            completion=completion,
            limit=MAX_CANDIDATES_PER_JOB + 1,
        )
        candidates = tuple(listed)
        if len(candidates) > MAX_CANDIDATES_PER_JOB:
            return EnqueueResult(
                disposition=EnqueueDisposition.REJECTED,
                reason="candidate_limit_exceeded",
            )
        if not candidates:
            return EnqueueResult(
                disposition=EnqueueDisposition.NO_OP,
                reason="no_pending_candidates",
            )
        if any(not isinstance(item, FrozenCandidateSnapshot) for item in candidates):
            raise TypeError("repository returned an invalid candidate snapshot")
        candidates = tuple(
            sorted(
                candidates,
                key=lambda item: _resource_ref_identity(item.candidate_ref),
            )
        )
        request = EnqueueRequest(trigger=completion, candidates=candidates)
        operation = _operation(
            "enqueue",
            {
                "trigger_key": completion.trigger_key,
                "candidates": [
                    {
                        "ref": item.candidate_ref.to_dict(),
                        "payload_sha256": item.payload_sha256,
                    }
                    for item in candidates
                ],
            },
        )
        try:
            job = self._repository.enqueue(request=request, operation=operation)
        except CurationConflictError:
            concurrent = self._repository.find_job_by_trigger(
                trigger_key=completion.trigger_key
            )
            if concurrent is None:
                raise
            self._validate_replayed_job(concurrent, completion)
            return EnqueueResult(
                disposition=EnqueueDisposition.REPLAYED,
                reason="already_enqueued",
                job=concurrent,
            )
        self._validate_enqueued_job(job, completion, candidates, operation)
        return EnqueueResult(
            disposition=EnqueueDisposition.ENQUEUED,
            reason="completed_root_run",
            job=job,
        )

    @staticmethod
    def _isolation_reason(completion: RootRunCompletion) -> str:
        if not completion.is_root_run:
            return "not_root_run"
        if completion.run_status is SourceRunStatus.FAILED:
            return "source_run_failed"
        if completion.run_status is SourceRunStatus.CANCELLED:
            return "source_run_cancelled"
        if completion.capture_status is RunCaptureStatus.PARTIAL:
            return "source_capture_partial"
        if completion.capture_status is RunCaptureStatus.UNAVAILABLE:
            return "source_capture_unavailable"
        return ""

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_ms: int,
        operation_id: str,
    ) -> ConsolidationJob | None:
        worker = _required_text(
            worker_id,
            "worker_id",
            maximum=512,
            identifier=True,
        )
        if isinstance(lease_ms, bool) or not isinstance(lease_ms, int):
            raise TypeError("lease_ms must be an integer")
        if not 1000 <= lease_ms <= MAX_LEASE_MS:
            raise ValueError("lease_ms must be between 1000 and 600000")
        operation = _external_operation(
            operation_id,
            kind="claim",
            payload={"worker_id": worker, "lease_ms": lease_ms},
        )
        now_ms = self._clock_ms()
        claimed = self._repository.claim_next(
            worker_id=worker,
            now_ms=now_ms,
            lease_ms=lease_ms,
            operation=operation,
        )
        if claimed is not None:
            if (
                not isinstance(claimed, ConsolidationJob)
                or claimed.status is not ConsolidationJobStatus.LEASED
                or claimed.lease is None
                or claimed.lease.owner != worker
                or claimed.lease.expires_at_ms <= now_ms
            ):
                raise CurationRepositoryError("repository_state_mismatch")
        return claimed

    @staticmethod
    def _validate_replayed_job(
        job: object,
        completion: RootRunCompletion,
    ) -> None:
        if not isinstance(job, ConsolidationJob):
            raise CurationRepositoryError("repository_state_mismatch")
        enqueue_operation = _operation(
            "enqueue",
            {
                "trigger_key": completion.trigger_key,
                "candidates": [
                    {
                        "ref": item.candidate_ref.to_dict(),
                        "payload_sha256": item.payload_sha256,
                    }
                    for item in job.candidates
                ],
            },
        )
        if (
            job.trigger != completion
            or job.operation_id != enqueue_operation.operation_id
            or job.candidates
            != tuple(
                sorted(
                    job.candidates,
                    key=lambda item: _resource_ref_identity(item.candidate_ref),
                )
            )
        ):
            raise CurationRepositoryError("repository_state_mismatch")

    @staticmethod
    def _validate_enqueued_job(
        job: object,
        completion: RootRunCompletion,
        candidates: tuple,
        operation: OperationRef,
    ) -> None:
        if not isinstance(job, ConsolidationJob):
            raise CurationRepositoryError("repository_state_mismatch")
        try:
            expected_candidates = tuple(
                _expected_enqueued_candidate(requested, returned)
                for requested, returned in zip(candidates, job.candidates)
            )
        except (TypeError, ValueError):
            raise CurationRepositoryError("repository_state_mismatch") from None
        if (
            job.status is not ConsolidationJobStatus.PENDING
            or job.trigger != completion
            or job.revision != 1
            or job.operation_id != operation.operation_id
            or job.created_at_ms != job.updated_at_ms
            or job.lease is not None
            or job.attempt_count != 0
            or job.next_attempt_at_ms != 0
            or job.last_error_code
            or len(job.candidates) != len(candidates)
            or job.candidates != expected_candidates
        ):
            raise CurationRepositoryError("repository_state_mismatch")

    def process_claimed(
        self,
        job: ConsolidationJob,
        *,
        runner: CuratorAgentRunner,
    ) -> ProcessResult:
        if not isinstance(job, ConsolidationJob):
            raise TypeError("job must be a ConsolidationJob")
        if not _enter_curator_scope(self.binding_id, job.job_id):
            return ProcessResult(
                disposition=ProcessDisposition.RECURSION_BLOCKED,
                reason="recursion_guard",
                job=job,
            )
        try:
            return self._process_claimed_active(job, runner=runner)
        finally:
            _leave_curator_scope(self.binding_id, job.job_id)

    def _process_claimed_active(
        self,
        job: ConsolidationJob,
        *,
        runner: CuratorAgentRunner,
    ) -> ProcessResult:
        current = self._repository.read_job(job_id=job.job_id)
        if current.status in _TERMINAL_STATUSES:
            return ProcessResult(
                disposition=ProcessDisposition.ALREADY_TERMINAL,
                reason=f"already_{current.status.value}",
                job=current,
            )
        if (
            current.status is not ConsolidationJobStatus.LEASED
            or current.revision != job.revision
            or current.lease != job.lease
        ):
            return ProcessResult(
                disposition=ProcessDisposition.LEASE_LOST,
                reason="lease_lost",
                job=current,
            )
        observed_now_ms = self._clock_ms()
        if current.lease is None or current.lease.expires_at_ms <= observed_now_ms:
            return ProcessResult(
                disposition=ProcessDisposition.LEASE_LOST,
                reason="lease_expired",
                job=current,
            )

        binding_error = self._runner_binding_error(current, runner)
        if binding_error:
            return self._terminal_failure(current, binding_error)

        mutation_guard = self._repository.bind_mutation_guard(job=current)
        expected_fence = CuratorLeaseFence.from_job(self.binding_id, current)
        if getattr(mutation_guard, "fence", None) != expected_fence:
            return self._terminal_failure(current, "mutation_guard_mismatch")
        toolkit_error = self._toolkit_binding_error(
            current,
            runner,
            mutation_guard,
        )
        if toolkit_error:
            return self._terminal_failure(current, toolkit_error)
        try:
            mutation_guard.assert_active()
        except CurationConflictError:
            latest = self._repository.read_job(job_id=current.job_id)
            return ProcessResult(
                disposition=ProcessDisposition.LEASE_LOST,
                reason="lease_lost",
                job=latest,
            )

        try:
            run_result = runner.run(
                CuratorRunRequest(
                    job=current,
                    policy=self._policy,
                    lease_fence=expected_fence,
                ),
                mutation_guard=mutation_guard,
            )
        except CuratorRunnerFailure as exc:
            return self._runner_failure(current, exc)
        except CurationConflictError:
            latest = self._repository.read_job(job_id=current.job_id)
            return ProcessResult(
                disposition=ProcessDisposition.LEASE_LOST,
                reason="lease_lost",
                job=latest,
            )
        except Exception as exc:
            code = "runner_" + _safe_code(type(exc).__name__, "failure")
            return self._terminal_failure(current, code)

        if not self._valid_runner_result(current, run_result):
            return self._terminal_failure(current, "invalid_runner_result")
        operation = _operation(
            "reconcile_and_complete",
            {
                "job_id": current.job_id,
                "job_revision": current.revision,
                "lease_token": current.lease.token,
                "resolutions": [item.to_dict() for item in run_result.resolutions],
            },
        )
        completed_at_ms = self._clock_ms()
        try:
            completed = self._repository.reconcile_and_complete(
                job=current,
                resolutions=run_result.resolutions,
                mutation_guard=mutation_guard,
                operation=operation,
                now_ms=completed_at_ms,
            )
        except CurationConflictError:
            latest = self._repository.read_job(job_id=current.job_id)
            return ProcessResult(
                disposition=ProcessDisposition.LEASE_LOST,
                reason="lease_lost",
                job=latest,
            )
        self._validate_completed_job(
            current,
            completed,
            run_result,
            now_ms=completed_at_ms,
        )
        return ProcessResult(
            disposition=ProcessDisposition.COMPLETED,
            reason="completed",
            job=completed,
        )

    def _runner_binding_error(
        self,
        job: ConsolidationJob,
        runner: CuratorAgentRunner,
    ) -> str:
        if str(getattr(runner, "binding_id", "")) != self.binding_id:
            return "runner_binding_mismatch"
        if str(getattr(runner, "job_id", "")) != job.job_id:
            return "runner_job_mismatch"
        expected_fence = CuratorLeaseFence.from_job(self.binding_id, job)
        if getattr(runner, "lease_fence", None) != expected_fence:
            return "runner_lease_fence_mismatch"
        refs = getattr(runner, "candidate_refs", ())
        if not isinstance(refs, tuple) or any(
            not isinstance(ref, ResourceRef) for ref in refs
        ):
            return "runner_candidate_scope_mismatch"
        if refs != tuple(item.candidate_ref for item in job.candidates):
            return "runner_candidate_scope_mismatch"
        return ""

    def _toolkit_binding_error(
        self,
        job: ConsolidationJob,
        runner: CuratorAgentRunner,
        mutation_guard: object,
    ) -> str:
        toolkit = getattr(runner, "toolkit", None)
        if toolkit is None:
            return "runner_toolkit_missing"
        if str(getattr(toolkit, "binding_id", "")) != self.binding_id:
            return "toolkit_binding_mismatch"
        if str(getattr(toolkit, "job_id", "")) != job.job_id:
            return "toolkit_job_mismatch"
        expected_fence = CuratorLeaseFence.from_job(self.binding_id, job)
        if getattr(toolkit, "lease_fence", None) != expected_fence:
            return "toolkit_lease_fence_mismatch"
        if getattr(toolkit, "candidate_refs", None) != tuple(
            item.candidate_ref for item in job.candidates
        ):
            return "toolkit_candidate_scope_mismatch"
        toolkit_guard = getattr(toolkit, "mutation_guard", None)
        if getattr(toolkit_guard, "fence", None) != expected_fence:
            return "toolkit_mutation_guard_mismatch"
        if getattr(mutation_guard, "fence", None) != expected_fence:
            return "toolkit_mutation_guard_mismatch"
        return ""

    @staticmethod
    def _valid_runner_result(
        job: ConsolidationJob,
        result: object,
    ) -> bool:
        if not isinstance(result, CuratorRunResult):
            return False
        expected = {
            item.candidate_ref: item.target_space_id
            for item in job.candidates
        }
        actual = {
            item.candidate_ref: item.target_space_id
            for item in result.resolutions
        }
        return len(actual) == len(expected) and actual == expected

    @staticmethod
    def _validate_completed_job(
        previous: ConsolidationJob,
        completed: object,
        result: CuratorRunResult,
        *,
        now_ms: int,
    ) -> None:
        if not isinstance(completed, ConsolidationJob):
            raise CurationRepositoryError("repository_state_mismatch")
        resolutions = {item.candidate_ref: item for item in result.resolutions}
        try:
            expected_candidates = tuple(
                before.with_outcome(
                    resolutions[before.candidate_ref].candidate_status,
                    result_ref=resolutions[before.candidate_ref].result_ref,
                    review_diff=resolutions[before.candidate_ref].review_diff,
                )
                for before in previous.candidates
            )
            expected = previous.terminal(
                status=ConsolidationJobStatus.COMPLETED,
                revision=previous.revision + 1,
                now_ms=now_ms,
                candidates=expected_candidates,
            )
        except (KeyError, TypeError, ValueError):
            raise CurationRepositoryError("repository_state_mismatch") from None
        if completed != expected:
            raise CurationRepositoryError("repository_state_mismatch")

    def _runner_failure(
        self,
        job: ConsolidationJob,
        error: CuratorRunnerFailure,
    ) -> ProcessResult:
        now_ms = self._clock_ms()
        if error.retryability is FailureRetryability.RETRYABLE:
            return self._fail(
                job,
                error_code=error.code,
                retry_at_ms=now_ms + error.retry_delay_ms,
                retry_disposition=ProcessDisposition.RETRY_SCHEDULED,
                now_ms=now_ms,
            )
        return self._terminal_failure(job, error.code, now_ms=now_ms)

    def _terminal_failure(
        self,
        job: ConsolidationJob,
        code: str,
        *,
        now_ms: int | None = None,
    ) -> ProcessResult:
        return self._fail(
            job,
            error_code=_safe_code(code, "curator_failed"),
            retry_at_ms=0,
            retry_disposition=ProcessDisposition.FAILED,
            now_ms=self._clock_ms() if now_ms is None else now_ms,
        )

    def _fail(
        self,
        job: ConsolidationJob,
        *,
        error_code: str,
        retry_at_ms: int,
        retry_disposition: ProcessDisposition,
        now_ms: int,
    ) -> ProcessResult:
        operation = _operation(
            "fail",
            {
                "job_id": job.job_id,
                "job_revision": job.revision,
                "lease_token": job.lease.token if job.lease else "",
                "error_code": error_code,
                "retry_at_ms": retry_at_ms,
            },
        )
        try:
            failed = self._repository.fail(
                job=job,
                error_code=error_code,
                retry_at_ms=retry_at_ms,
                operation=operation,
                now_ms=now_ms,
            )
        except CurationConflictError:
            latest = self._repository.read_job(job_id=job.job_id)
            return ProcessResult(
                disposition=ProcessDisposition.LEASE_LOST,
                reason="lease_lost",
                job=latest,
            )
        expected_status = (
            ConsolidationJobStatus.PENDING
            if retry_disposition is ProcessDisposition.RETRY_SCHEDULED
            else ConsolidationJobStatus.FAILED
        )
        if (
            not isinstance(failed, ConsolidationJob)
            or failed.job_id != job.job_id
            or failed.status is not expected_status
            or failed.revision != job.revision + 1
            or failed.lease is not None
            or (
                expected_status is ConsolidationJobStatus.PENDING
                and failed.next_attempt_at_ms != retry_at_ms
            )
            or (
                expected_status is ConsolidationJobStatus.FAILED
                and failed.next_attempt_at_ms != 0
            )
        ):
            raise CurationRepositoryError("repository_state_mismatch")
        return ProcessResult(
            disposition=retry_disposition,
            reason=error_code,
            job=failed,
        )

    def cancel(
        self,
        job: ConsolidationJob,
        *,
        reason: str,
        operation_id: str,
    ) -> ConsolidationJob:
        if not isinstance(job, ConsolidationJob):
            raise TypeError("job must be a ConsolidationJob")
        normalized_reason = _required_text(
            reason,
            "reason",
            maximum=128,
            identifier=True,
        )
        current = self._repository.read_job(job_id=job.job_id)
        if current.status in _TERMINAL_STATUSES:
            return current
        operation = _external_operation(
            operation_id,
            kind="cancel",
            payload={
                "job_id": job.job_id,
                "job_revision": job.revision,
                "reason": normalized_reason,
            },
        )
        now_ms = self._clock_ms()
        cancelled = self._repository.cancel(
            job=job,
            reason=normalized_reason,
            operation=operation,
            now_ms=now_ms,
        )
        self._validate_cancelled_job(
            job,
            cancelled,
            reason=normalized_reason,
            now_ms=now_ms,
        )
        return cancelled

    @staticmethod
    def _validate_cancelled_job(
        previous: ConsolidationJob,
        cancelled: object,
        *,
        reason: str,
        now_ms: int,
    ) -> None:
        if not isinstance(cancelled, ConsolidationJob):
            raise CurationRepositoryError("repository_state_mismatch")
        try:
            expected = previous.terminal(
                status=ConsolidationJobStatus.CANCELLED,
                revision=previous.revision + 1,
                now_ms=now_ms,
                error_code=reason,
            )
        except (TypeError, ValueError):
            raise CurationRepositoryError("repository_state_mismatch") from None
        if cancelled != expected:
            raise CurationRepositoryError("repository_state_mismatch")


__all__ = ["CuratorCoordinator"]
