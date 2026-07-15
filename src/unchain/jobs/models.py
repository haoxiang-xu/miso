from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JOB_SCHEMA_VERSION = 1
JOB_STATUSES = frozenset(
    {
        "queued",
        "starting",
        "running",
        "completed",
        "failed",
        "timed_out",
        "cancelled",
        "outcome_unknown",
    }
)
TERMINAL_JOB_STATUSES = frozenset(
    {"completed", "failed", "timed_out", "cancelled", "outcome_unknown"}
)


class DurableJobError(RuntimeError):
    code = "durable_job_error"


class DurableJobNotFoundError(DurableJobError):
    code = "durable_job_not_found"


class DurableJobOwnershipError(DurableJobNotFoundError):
    """Hide a job owned by another execution behind not-found semantics."""

    code = "durable_job_not_found"


class DurableJobConflictError(DurableJobError):
    code = "durable_job_conflict"


class DurableJobStoreCorruptionError(DurableJobError):
    code = "durable_job_store_corruption"


@dataclass(frozen=True)
class DurableJobHandle:
    job_id: str
    execution_id: str
    adapter: str
    external_id: str
    status: str
    intent_digest: str
    environment_digest: str


@dataclass(frozen=True)
class DurableJobSnapshot:
    job_id: str
    execution_id: str
    adapter: str
    status: str
    intent_digest: str
    environment_digest: str
    created_at_ms: int
    updated_at_ms: int
    revision: int = 0
    worker_token: str = field(default="", repr=False)
    worker_pid: int | None = None
    child_pid: int | None = None
    returncode: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    outcome_unknown_reason: str = ""
    error: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @property
    def completed(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES

    @property
    def ok(self) -> bool:
        return self.status == "completed" and self.returncode == 0

    @property
    def external_id(self) -> str:
        return str(self.child_pid or self.worker_pid or "")

    @property
    def handle(self) -> DurableJobHandle:
        return DurableJobHandle(
            job_id=self.job_id,
            execution_id=self.execution_id,
            adapter=self.adapter,
            external_id=self.external_id,
            status=self.status,
            intent_digest=self.intent_digest,
            environment_digest=self.environment_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        # ``worker_token`` is an internal fencing capability.  A snapshot may
        # be returned to a model-facing tool, so never serialize the token in
        # the public representation.
        return {
            "schema_version": JOB_SCHEMA_VERSION,
            "job_id": self.job_id,
            "execution_id": self.execution_id,
            "adapter": self.adapter,
            "status": self.status,
            "intent_digest": self.intent_digest,
            "environment_digest": self.environment_digest,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "revision": self.revision,
            "worker_pid": self.worker_pid,
            "child_pid": self.child_pid,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
            "outcome_unknown_reason": self.outcome_unknown_reason,
            "error": self.error,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "completed": self.completed,
            "ok": self.ok,
        }


__all__ = [
    "JOB_SCHEMA_VERSION",
    "JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "DurableJobConflictError",
    "DurableJobError",
    "DurableJobHandle",
    "DurableJobNotFoundError",
    "DurableJobOwnershipError",
    "DurableJobSnapshot",
    "DurableJobStoreCorruptionError",
]
