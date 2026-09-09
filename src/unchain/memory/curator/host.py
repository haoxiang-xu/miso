"""Production host seam for the conditional Memory V2 curator.

The core coordinator owns durable candidate and job state.  This module owns
the narrower host concerns: the default-off feature gate, separation of root
run enqueue from worker execution, construction of the official role-specific
toolkits, and a recursion guard that runs before a worker claims another job.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from unchain.journal.models import _required_text
from unchain.memory.toolkit import (
    ConsolidationMemoryToolkitCapabilities,
    DEFAULT_MEMORY_TOOLKIT_DIALECT,
    MemoryToolkitDialect,
    MemoryToolkitRunBinding,
    NormalMemoryToolkitCapabilities,
    build_memory_toolkit,
)
from unchain.tools import Toolkit

from .coordinator import CuratorCoordinator
from .models import (
    ConsolidationJob,
    CuratorLeaseFence,
    CuratorRunRequest,
    CuratorRunResult,
    EnqueueResult,
    MAX_LEASE_MS,
    ProcessResult,
    RootRunCompletion,
)
from .ports import BoundCurationRepository, BoundCuratorMutationGuard


_ACTIVE_HOST_LOCK = threading.RLock()
_ACTIVE_HOST_BINDINGS: set[str] = set()


class MemoryAgentHostError(RuntimeError):
    """Stable configuration or binding failure at the host boundary."""


class MemoryAgentWorkerDisposition(StrEnum):
    DISABLED = "disabled"
    IDLE = "idle"
    RECURSION_BLOCKED = "recursion_blocked"
    PROCESSED = "processed"


@dataclass(frozen=True)
class MemoryAgentHostConfig:
    """Immutable host configuration with the production gate closed by default."""

    enabled: bool = False
    worker_id: str = "memory-curator"
    lease_ms: int = 30_000

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a boolean")
        object.__setattr__(
            self,
            "worker_id",
            _required_text(
                self.worker_id,
                "worker_id",
                maximum=512,
                identifier=True,
            ),
        )
        if isinstance(self.lease_ms, bool) or not isinstance(self.lease_ms, int):
            raise TypeError("lease_ms must be an integer")
        if not 1000 <= self.lease_ms <= MAX_LEASE_MS:
            raise ValueError("lease_ms must be between 1000 and 600000")


@dataclass(frozen=True)
class MemoryAgentEnqueueReceipt:
    enabled: bool
    reason: str
    result: EnqueueResult | None = None


@dataclass(frozen=True)
class MemoryAgentWorkerReceipt:
    disposition: MemoryAgentWorkerDisposition
    reason: str
    claimed_job: ConsolidationJob | None = None
    result: ProcessResult | None = None


class ConsolidationCapabilityFactory(Protocol):
    """Build capabilities already bound to one claimed job and lease fence."""

    binding_id: str

    def build(
        self,
        *,
        binding: MemoryToolkitRunBinding,
        job: ConsolidationJob,
        mutation_guard: BoundCuratorMutationGuard,
    ) -> ConsolidationMemoryToolkitCapabilities:
        ...


class MemoryAgentModelInvoker(Protocol):
    """Host-selected model invocation behind the locked curator policy."""

    def run(
        self,
        request: CuratorRunRequest,
        *,
        toolkit: Toolkit,
        binding: MemoryToolkitRunBinding,
    ) -> CuratorRunResult:
        ...


def _enter_host_scope(binding_id: str) -> bool:
    with _ACTIVE_HOST_LOCK:
        if binding_id in _ACTIVE_HOST_BINDINGS:
            return False
        _ACTIVE_HOST_BINDINGS.add(binding_id)
        return True


def _leave_host_scope(binding_id: str) -> None:
    with _ACTIVE_HOST_LOCK:
        _ACTIVE_HOST_BINDINGS.discard(binding_id)


def _curator_run_binding(
    binding_id: str, job: ConsolidationJob
) -> MemoryToolkitRunBinding:
    digest = hashlib.sha256(f"{job.job_id}:{job.revision}".encode("utf-8")).hexdigest()
    return MemoryToolkitRunBinding(
        binding_id=binding_id,
        session_id=job.trigger.session_id,
        attempt_id=f"memory-curator-attempt-{digest}",
        run_id=f"memory-curator-run-{digest}",
    )


class _OfficialToolkitCuratorRunner:
    def __init__(
        self,
        *,
        binding: MemoryToolkitRunBinding,
        job: ConsolidationJob,
        toolkit: Toolkit,
        mutation_guard: BoundCuratorMutationGuard,
        model_invoker: MemoryAgentModelInvoker,
    ) -> None:
        self.binding_id = binding.binding_id
        self.job_id = job.job_id
        self.candidate_refs = tuple(item.candidate_ref for item in job.candidates)
        self.lease_fence = CuratorLeaseFence.from_job(self.binding_id, job)
        self.toolkit = toolkit
        self._binding = binding
        self._mutation_guard = mutation_guard
        self._model_invoker = model_invoker

    def run(
        self,
        request: CuratorRunRequest,
        *,
        mutation_guard: BoundCuratorMutationGuard,
    ) -> CuratorRunResult:
        if mutation_guard.fence != self.lease_fence:
            raise MemoryAgentHostError(
                "curator mutation guard changed before model run"
            )
        if self._mutation_guard.fence != self.lease_fence:
            raise MemoryAgentHostError("curator toolkit mutation guard changed")
        return self._model_invoker.run(
            request,
            toolkit=self.toolkit,
            binding=self._binding,
        )


class MemoryAgentHostAdapter:
    """Default-off adapter joining Memory V2 jobs, toolkits, and a model runner."""

    def __init__(
        self,
        repository: BoundCurationRepository,
        *,
        capability_factory: ConsolidationCapabilityFactory | None = None,
        model_invoker: MemoryAgentModelInvoker | None = None,
        config: MemoryAgentHostConfig | None = None,
        dialect: MemoryToolkitDialect = DEFAULT_MEMORY_TOOLKIT_DIALECT,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._binding_id = _required_text(
            getattr(repository, "binding_id", ""),
            "repository binding_id",
            maximum=512,
            identifier=True,
        )
        self._repository = repository
        self._config = config or MemoryAgentHostConfig()
        if not isinstance(self._config, MemoryAgentHostConfig):
            raise TypeError("config must be a MemoryAgentHostConfig")
        if not isinstance(dialect, MemoryToolkitDialect):
            raise TypeError("dialect must be a MemoryToolkitDialect")
        if self._config.enabled and (
            capability_factory is None or model_invoker is None
        ):
            raise MemoryAgentHostError(
                "enabled Memory Agent requires capability_factory and model_invoker"
            )
        factory_binding = getattr(capability_factory, "binding_id", "")
        if capability_factory is not None and factory_binding != self._binding_id:
            raise MemoryAgentHostError(
                "consolidation capability factory belongs to another binding"
            )
        self._capability_factory = capability_factory
        self._model_invoker = model_invoker
        self._dialect = dialect
        self._coordinator = CuratorCoordinator(repository, clock_ms=clock_ms)

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def binding_id(self) -> str:
        return self._binding_id

    def build_normal_toolkit(
        self,
        binding: MemoryToolkitRunBinding,
        capabilities: NormalMemoryToolkitCapabilities,
    ) -> Toolkit | None:
        """Return no toolkit while closed, otherwise the exact normal-agent role."""

        if not self.enabled:
            return None
        if not isinstance(binding, MemoryToolkitRunBinding):
            raise TypeError("binding must be a MemoryToolkitRunBinding")
        if binding.binding_id != self.binding_id:
            raise MemoryAgentHostError("normal toolkit belongs to another binding")
        if not isinstance(capabilities, NormalMemoryToolkitCapabilities):
            raise TypeError("capabilities must be NormalMemoryToolkitCapabilities")
        return build_memory_toolkit(
            binding,
            capabilities,
            dialect=self._dialect,
        )

    def enqueue_root_completion(
        self,
        completion: RootRunCompletion,
    ) -> MemoryAgentEnqueueReceipt:
        """Persist at most one job for a root completion; never invoke a model."""

        if not self.enabled:
            return MemoryAgentEnqueueReceipt(
                enabled=False,
                reason="feature_disabled",
            )
        result = self._coordinator.enqueue(completion)
        return MemoryAgentEnqueueReceipt(
            enabled=True,
            reason=result.reason,
            result=result,
        )

    def process_next(self, *, operation_id: str) -> MemoryAgentWorkerReceipt:
        """Claim and process one durable job, independently of root-run enqueue."""

        if not self.enabled:
            return MemoryAgentWorkerReceipt(
                disposition=MemoryAgentWorkerDisposition.DISABLED,
                reason="feature_disabled",
            )
        if not _enter_host_scope(self.binding_id):
            return MemoryAgentWorkerReceipt(
                disposition=MemoryAgentWorkerDisposition.RECURSION_BLOCKED,
                reason="recursion_guard",
            )
        try:
            claimed = self._coordinator.claim_next(
                worker_id=self._config.worker_id,
                lease_ms=self._config.lease_ms,
                operation_id=operation_id,
            )
            if claimed is None:
                return MemoryAgentWorkerReceipt(
                    disposition=MemoryAgentWorkerDisposition.IDLE,
                    reason="no_pending_job",
                )
            runner = self._build_runner(claimed)
            result = self._coordinator.process_claimed(
                claimed,
                runner=runner,
            )
            return MemoryAgentWorkerReceipt(
                disposition=MemoryAgentWorkerDisposition.PROCESSED,
                reason=result.reason,
                claimed_job=claimed,
                result=result,
            )
        finally:
            _leave_host_scope(self.binding_id)

    def _build_runner(self, job: ConsolidationJob) -> _OfficialToolkitCuratorRunner:
        capability_factory = self._capability_factory
        model_invoker = self._model_invoker
        if capability_factory is None or model_invoker is None:
            raise MemoryAgentHostError("Memory Agent worker is not configured")
        mutation_guard = self._repository.bind_mutation_guard(job=job)
        binding = _curator_run_binding(self.binding_id, job)
        capabilities = capability_factory.build(
            binding=binding,
            job=job,
            mutation_guard=mutation_guard,
        )
        if not isinstance(
            capabilities,
            ConsolidationMemoryToolkitCapabilities,
        ):
            raise MemoryAgentHostError(
                "capability factory did not return consolidation capabilities"
            )
        expected_fence = CuratorLeaseFence.from_job(self.binding_id, job)
        if (
            capabilities.binding_id != self.binding_id
            or capabilities.job_id != job.job_id
            or capabilities.candidate_refs
            != tuple(item.candidate_ref for item in job.candidates)
            or capabilities.lease_fence != expected_fence
            or capabilities.mutation_guard.fence != expected_fence
        ):
            raise MemoryAgentHostError(
                "consolidation capabilities changed the claimed job binding"
            )
        toolkit = build_memory_toolkit(
            binding,
            capabilities,
            dialect=self._dialect,
        )
        for name, value in (
            ("binding_id", self.binding_id),
            ("job_id", job.job_id),
            ("candidate_refs", capabilities.candidate_refs),
            ("lease_fence", expected_fence),
            ("mutation_guard", capabilities.mutation_guard),
        ):
            setattr(toolkit, name, value)
        return _OfficialToolkitCuratorRunner(
            binding=binding,
            job=job,
            toolkit=toolkit,
            mutation_guard=mutation_guard,
            model_invoker=model_invoker,
        )


__all__ = [
    "ConsolidationCapabilityFactory",
    "MemoryAgentEnqueueReceipt",
    "MemoryAgentHostAdapter",
    "MemoryAgentHostConfig",
    "MemoryAgentHostError",
    "MemoryAgentModelInvoker",
    "MemoryAgentWorkerDisposition",
    "MemoryAgentWorkerReceipt",
]
