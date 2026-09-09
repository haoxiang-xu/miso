from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from unchain.journal import OperationRef, ResourceRef

from .models import (
    CandidateResolution,
    ConsolidationJob,
    CuratorLeaseFence,
    CuratorRunRequest,
    CuratorRunResult,
    EnqueueRequest,
    FrozenCandidateSnapshot,
    RootRunCompletion,
)


class CurationRepositoryError(RuntimeError):
    """Stable error raised by an already-bound curation repository."""

    def __init__(self, code: str) -> None:
        normalized = ""
        if isinstance(code, str):
            normalized = re.sub(
                r"[^a-z0-9_:-]+",
                "_",
                code.casefold(),
            ).strip("_")[:128]
        self.code = normalized or "curation_repository_error"
        super().__init__(self.code)


class CurationConflictError(CurationRepositoryError):
    """A CAS, idempotency, trigger-uniqueness, or lease conflict."""


@runtime_checkable
class BoundCuratorMutationGuard(Protocol):
    """Lease fence checked immediately before every entry/review mutation.

    A host toolkit adapter must call ``assert_active`` inside the same storage
    transaction as each apply/review effect. The check includes binding, job
    revision, lease owner/token, and lease expiry. A final job CAS is not a
    substitute.
    """

    fence: CuratorLeaseFence

    def assert_active(self) -> None: ...


@runtime_checkable
class FenceBoundConsolidationToolkit(Protocol):
    """Host-only toolkit scope that a model cannot widen or reconstruct.

    Every semantic mutation exposed by this capability must require its bound
    ``mutation_guard`` and pass the guard to storage. Storage must validate the
    exact fence in the same transaction as the entry or review mutation.
    """

    binding_id: str
    job_id: str
    candidate_refs: tuple[ResourceRef, ...]
    lease_fence: CuratorLeaseFence
    mutation_guard: BoundCuratorMutationGuard


@runtime_checkable
class BoundCurationRepository(Protocol):
    """Persistence capability bound once to an authorized memory scope."""

    binding_id: str

    def find_job_by_trigger(self, *, trigger_key: str) -> ConsolidationJob | None: ...

    def list_pending_candidates(
        self,
        *,
        completion: RootRunCompletion,
        limit: int,
    ) -> tuple[FrozenCandidateSnapshot, ...]: ...

    def isolate_source_candidates(
        self,
        *,
        completion: RootRunCompletion,
        reason: str,
        operation: OperationRef,
    ) -> int: ...

    def enqueue(
        self,
        *,
        request: EnqueueRequest,
        operation: OperationRef,
    ) -> ConsolidationJob: ...

    def read_job(self, *, job_id: str) -> ConsolidationJob: ...

    def bind_mutation_guard(
        self,
        *,
        job: ConsolidationJob,
    ) -> BoundCuratorMutationGuard: ...

    def claim_next(
        self,
        *,
        worker_id: str,
        now_ms: int,
        lease_ms: int,
        operation: OperationRef,
    ) -> ConsolidationJob | None: ...

    def reconcile_and_complete(
        self,
        *,
        job: ConsolidationJob,
        resolutions: tuple[CandidateResolution, ...],
        mutation_guard: BoundCuratorMutationGuard,
        operation: OperationRef,
        now_ms: int,
    ) -> ConsolidationJob:
        """Atomically reconcile candidate bindings and complete their job.

        One storage transaction must use ``mutation_guard`` to assert the exact
        active lease fence,
        compare every candidate binding revision and target chat space, persist
        every terminal outcome/result/review diff, and transition the job to
        ``completed``. The candidate reconciliation and terminal job state may
        not commit independently.
        """
        ...

    def fail(
        self,
        *,
        job: ConsolidationJob,
        error_code: str,
        retry_at_ms: int,
        operation: OperationRef,
        now_ms: int,
    ) -> ConsolidationJob: ...

    def cancel(
        self,
        *,
        job: ConsolidationJob,
        reason: str,
        operation: OperationRef,
        now_ms: int,
    ) -> ConsolidationJob: ...


@runtime_checkable
class CuratorAgentRunner(Protocol):
    """Host-supplied runner already bound to one model and one safe toolkit."""

    binding_id: str
    job_id: str
    candidate_refs: tuple[ResourceRef, ...]
    lease_fence: CuratorLeaseFence
    toolkit: FenceBoundConsolidationToolkit

    def run(
        self,
        request: CuratorRunRequest,
        *,
        mutation_guard: BoundCuratorMutationGuard,
    ) -> CuratorRunResult: ...


__all__ = [
    "BoundCurationRepository",
    "BoundCuratorMutationGuard",
    "CurationConflictError",
    "CurationRepositoryError",
    "CuratorAgentRunner",
    "FenceBoundConsolidationToolkit",
]
