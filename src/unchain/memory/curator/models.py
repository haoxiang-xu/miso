from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from unchain.journal import ResourceRef
from unchain.journal.models import (
    ModelValidationError,
    _bounded_int,
    _freeze_json,
    _optional_text,
    _record_tuple,
    _required_text,
    _sha256,
    _thaw_json,
)
from ._validation import canonical_candidate_link_url, canonical_candidate_path


MAX_CANDIDATES_PER_JOB = 200
MAX_LEASE_MS = 10 * 60 * 1000
MAX_RETRY_DELAY_MS = 24 * 60 * 60 * 1000
MAX_REVIEW_DIFF_BYTES = 32 * 1024
MAX_REVIEW_DIFF_DEPTH = 8
MAX_REVIEW_DIFF_ITEMS = 512


class CandidateOrigin(StrEnum):
    """The only P0 events allowed to create a memory candidate."""

    AGENT_PROPOSAL = "agent_proposal"
    USER_EXPLICIT = "user_explicit"
    CHECKPOINT = "checkpoint"


class CandidateStatus(StrEnum):
    """Schema-v4 candidate states, kept exact at the host boundary."""

    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    APPLIED = "applied"
    AWAITING_USER = "awaiting_user"
    ISOLATED = "isolated"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class CandidateOutcome(StrEnum):
    """Terminal schema-v4 job-binding outcomes."""

    APPLIED = "applied"
    AWAITING_USER = "awaiting_user"
    ISOLATED = "isolated"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ConsolidationJobStatus(StrEnum):
    """Schema-v4 consolidation job states."""

    PENDING = "pending"
    LEASED = "leased"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceRunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunCaptureStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class EnqueueDisposition(StrEnum):
    ENQUEUED = "enqueued"
    REPLAYED = "replayed"
    NO_OP = "no_op"
    ISOLATED = "isolated"
    REJECTED = "rejected"


class ProcessDisposition(StrEnum):
    COMPLETED = "completed"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    LEASE_LOST = "lease_lost"
    RECURSION_BLOCKED = "recursion_blocked"
    ALREADY_TERMINAL = "already_terminal"


class FailureRetryability(StrEnum):
    RETRYABLE = "retryable"
    TERMINAL = "terminal"


def _enum(value: Any, enum_type: type[StrEnum], field_name: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ModelValidationError(f"invalid {field_name}") from exc


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resource_refs(
    values: Sequence[ResourceRef],
    *,
    field_name: str,
    allowed_kinds: frozenset[str] | None = None,
    maximum: int = 20_000,
) -> tuple[ResourceRef, ...]:
    refs = _record_tuple(values, ResourceRef, field_name)
    if len(refs) > maximum:
        raise ModelValidationError(f"{field_name} exceeds its item limit")
    if allowed_kinds is not None and any(ref.kind not in allowed_kinds for ref in refs):
        raise ModelValidationError(f"{field_name} contains a disallowed reference kind")
    if len(set(refs)) != len(refs):
        raise ModelValidationError(f"{field_name} must not contain duplicates")
    return refs


def _bounded_review_diff(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("review_diff must be an object")
    item_count = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal item_count
        if depth > MAX_REVIEW_DIFF_DEPTH:
            raise ModelValidationError("review diff limit exceeded")
        if isinstance(item, Mapping):
            item_count += len(item)
            if item_count > MAX_REVIEW_DIFF_ITEMS:
                raise ModelValidationError("review diff limit exceeded")
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, Sequence) and not isinstance(
            item,
            (str, bytes, bytearray),
        ):
            item_count += len(item)
            if item_count > MAX_REVIEW_DIFF_ITEMS:
                raise ModelValidationError("review diff limit exceeded")
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)
    frozen = _freeze_json(value, path="review_diff")
    if not isinstance(frozen, Mapping):
        raise TypeError("review_diff must be an object")
    encoded = json.dumps(
        _thaw_json(frozen),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_REVIEW_DIFF_BYTES:
        raise ModelValidationError("review diff limit exceeded")
    return frozen


@dataclass(frozen=True)
class RootRunCompletion:
    """One root-run terminal callback inside an already-bound host scope."""

    session_id: str
    attempt_id: str
    run_id: str
    is_root_run: bool
    run_status: SourceRunStatus
    capture_status: RunCaptureStatus

    def __post_init__(self) -> None:
        for field_name in ("session_id", "attempt_id", "run_id"):
            object.__setattr__(
                self,
                field_name,
                _required_text(
                    getattr(self, field_name),
                    field_name,
                    maximum=512,
                    identifier=True,
                ),
            )
        if not isinstance(self.is_root_run, bool):
            raise TypeError("is_root_run must be a boolean")
        object.__setattr__(
            self,
            "run_status",
            _enum(self.run_status, SourceRunStatus, "source run status"),
        )
        object.__setattr__(
            self,
            "capture_status",
            _enum(self.capture_status, RunCaptureStatus, "capture status"),
        )

    @property
    def trigger_key(self) -> str:
        return "completed_root_run:" + _canonical_digest(
            {
                "session_id": self.session_id,
                "attempt_id": self.attempt_id,
                "run_id": self.run_id,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "is_root_run": self.is_root_run,
            "run_status": self.run_status.value,
            "capture_status": self.capture_status.value,
            "trigger_key": self.trigger_key,
        }


@dataclass(frozen=True)
class FrozenCandidateSnapshot:
    """Metadata-only candidate payload, complete once durably job-bound.

    Repositories may receive the pre-binding form from candidate discovery, but
    every ``ConsolidationJob`` must contain only ``is_durable_binding`` values.
    """

    candidate_ref: ResourceRef
    origin: CandidateOrigin
    target_path: str
    name: str
    description: str
    kind: str
    media_type: str
    source_refs: tuple[ResourceRef, ...]
    payload_sha256: str
    content_sha256: str
    byte_length: int
    target_space_id: str = ""
    binding_revision: int = 0
    outcome: CandidateStatus = CandidateStatus.PENDING
    content_ref: ResourceRef | None = None
    link_url: str = ""
    source_agent_run_id: str = ""
    source_tool_call_id: str = ""
    rationale: str = ""
    confidence: float | None = None
    sensitivity: str = "normal"
    result_ref: ResourceRef | None = None
    review_diff: Mapping[str, Any] = field(default_factory=dict)
    error_code: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_ref, ResourceRef):
            object.__setattr__(
                self,
                "candidate_ref",
                ResourceRef.from_dict(self.candidate_ref),
            )
        if self.candidate_ref.kind != "memory_candidate":
            raise ModelValidationError("candidate_ref must identify a memory candidate")
        if self.candidate_ref.fragment:
            raise ModelValidationError(
                "candidate_ref fragment is not representable by schema v4"
            )
        object.__setattr__(
            self,
            "origin",
            _enum(self.origin, CandidateOrigin, "candidate origin"),
        )
        target_space_id = _optional_text(
            self.target_space_id,
            "target_space_id",
            maximum=512,
        )
        if target_space_id:
            target_space_id = _required_text(
                target_space_id,
                "target_space_id",
                maximum=512,
                identifier=True,
            )
        object.__setattr__(self, "target_space_id", target_space_id)
        object.__setattr__(
            self,
            "binding_revision",
            _bounded_int(self.binding_revision, "binding_revision"),
        )
        object.__setattr__(
            self,
            "outcome",
            _enum(self.outcome, CandidateStatus, "candidate binding outcome"),
        )
        binding_parts = (
            bool(self.target_space_id),
            self.binding_revision > 0,
            self.outcome is not CandidateStatus.PENDING,
        )
        if any(binding_parts) and not all(binding_parts):
            raise ModelValidationError("candidate binding metadata is incomplete")
        object.__setattr__(
            self,
            "target_path",
            canonical_candidate_path(self.target_path),
        )
        object.__setattr__(self, "name", _required_text(self.name, "name", maximum=256))
        object.__setattr__(
            self,
            "description",
            _required_text(self.description, "description", maximum=8192),
        )
        kind = _required_text(self.kind, "kind", maximum=64, identifier=True).lower()
        if kind not in {"folder", "file", "link", "markdown", "image"}:
            raise ModelValidationError("candidate kind is unsupported")
        if self.is_durable_binding and kind not in {"folder", "file", "link"}:
            raise ModelValidationError("durable candidate kind must match schema v4")
        object.__setattr__(self, "kind", kind)
        media_type = _optional_text(self.media_type, "media_type", maximum=255)
        if media_type and "/" not in media_type:
            raise ModelValidationError("media_type must be a MIME type")
        object.__setattr__(self, "media_type", media_type)
        if self.content_ref is not None and not isinstance(self.content_ref, ResourceRef):
            object.__setattr__(
                self,
                "content_ref",
                ResourceRef.from_dict(self.content_ref),
            )
        link_url = _optional_text(self.link_url, "link_url", maximum=8192)
        if link_url:
            link_url = canonical_candidate_link_url(link_url)
        object.__setattr__(self, "link_url", link_url)
        source_refs = _resource_refs(
            self.source_refs,
            field_name="source_refs",
            allowed_kinds=frozenset({"context_event"}),
        )
        if not source_refs:
            raise ModelValidationError("source_refs must not be empty")
        object.__setattr__(self, "source_refs", source_refs)
        for field_name in ("source_agent_run_id", "source_tool_call_id"):
            normalized = _optional_text(
                getattr(self, field_name),
                field_name,
                maximum=512,
            )
            if normalized:
                normalized = _required_text(
                    normalized,
                    field_name,
                    maximum=512,
                    identifier=True,
                )
            object.__setattr__(self, field_name, normalized)
        object.__setattr__(
            self,
            "rationale",
            _optional_text(self.rationale, "rationale", maximum=8192),
        )
        if self.confidence is not None:
            if (
                isinstance(self.confidence, bool)
                or not isinstance(self.confidence, (int, float))
                or not math.isfinite(float(self.confidence))
                or not 0 <= float(self.confidence) <= 1
            ):
                raise ModelValidationError("confidence must be between 0 and 1")
            object.__setattr__(self, "confidence", float(self.confidence))
        object.__setattr__(
            self,
            "sensitivity",
            _required_text(
                self.sensitivity,
                "sensitivity",
                maximum=64,
                identifier=True,
            ),
        )
        object.__setattr__(self, "payload_sha256", _sha256(self.payload_sha256))
        content_sha256 = _optional_text(
            self.content_sha256,
            "content_sha256",
            maximum=64,
        )
        if content_sha256:
            content_sha256 = _sha256(content_sha256, "content_sha256")
        object.__setattr__(self, "content_sha256", content_sha256)
        object.__setattr__(
            self,
            "byte_length",
            _bounded_int(self.byte_length, "byte_length"),
        )
        if self.result_ref is not None and not isinstance(self.result_ref, ResourceRef):
            object.__setattr__(
                self,
                "result_ref",
                ResourceRef.from_dict(self.result_ref),
            )
        review_diff = _bounded_review_diff(self.review_diff)
        object.__setattr__(self, "review_diff", review_diff)
        error_code = _optional_text(self.error_code, "error_code", maximum=128)
        if error_code:
            error_code = _required_text(
                error_code,
                "error_code",
                maximum=128,
                identifier=True,
            )
        object.__setattr__(self, "error_code", error_code)
        if self.is_durable_binding:
            self._validate_durable_shape()

    @property
    def is_durable_binding(self) -> bool:
        return (
            bool(self.target_space_id)
            and self.binding_revision > 0
            and self.outcome is not CandidateStatus.PENDING
        )

    def _validate_durable_shape(self) -> None:
        if self.kind == "file":
            if (
                self.content_ref is None
                or not self.content_sha256
                or not self.media_type
                or self.link_url
            ):
                raise ModelValidationError(
                    "file candidate requires durable content metadata only"
                )
        elif self.kind == "link":
            if (
                not self.link_url
                or self.content_ref is not None
                or self.content_sha256
                or self.byte_length
                or self.media_type
            ):
                raise ModelValidationError(
                    "link candidate requires a URL without durable file content"
                )
        elif self.kind == "folder":
            if (
                self.content_ref is not None
                or self.content_sha256
                or self.byte_length
                or self.media_type
                or self.link_url
            ):
                raise ModelValidationError(
                    "folder candidate cannot carry content metadata"
                )
        if self.outcome in {
            CandidateStatus.APPLIED,
            CandidateStatus.AWAITING_USER,
            CandidateStatus.SUPERSEDED,
        } and self.result_ref is None:
            raise ModelValidationError("resolved candidate requires a result reference")
        if self.result_ref is not None:
            if self.result_ref.fragment != self.target_space_id:
                raise ModelValidationError(
                    "candidate result reference must remain in the target chat space"
                )
            expected_kind = (
                "memory_review"
                if self.outcome is CandidateStatus.AWAITING_USER
                else "memory"
            )
            if self.result_ref.kind != expected_kind:
                raise ModelValidationError("candidate result reference kind is invalid")
        if self.outcome is CandidateStatus.AWAITING_USER:
            if not self.review_diff:
                raise ModelValidationError(
                    "awaiting_user candidate requires a non-empty review diff"
                )
        elif self.review_diff:
            raise ModelValidationError(
                "only awaiting_user candidates may carry a review diff"
            )

    def bind(
        self,
        *,
        target_space_id: str,
        binding_revision: int,
        outcome: CandidateStatus,
        storage_kind: str | None = None,
        content_ref: ResourceRef | None = None,
    ) -> FrozenCandidateSnapshot:
        kind = _required_text(
            storage_kind or self.kind,
            "storage_kind",
            maximum=64,
            identifier=True,
        ).lower()
        resolved_content_ref = content_ref
        if kind == "file" and resolved_content_ref is None:
            resolved_content_ref = self.candidate_ref
        if kind != "file":
            resolved_content_ref = None
        return replace(
            self,
            target_space_id=target_space_id,
            binding_revision=binding_revision,
            outcome=outcome,
            kind=kind,
            content_ref=resolved_content_ref,
            content_sha256=self.content_sha256 if kind == "file" else "",
            byte_length=self.byte_length if kind == "file" else 0,
            media_type=self.media_type if kind == "file" else "",
            link_url=self.link_url if kind == "link" else "",
        )

    def with_outcome(
        self,
        outcome: CandidateStatus,
        *,
        result_ref: ResourceRef | None = None,
        review_diff: Mapping[str, Any] | None = None,
        error_code: str = "",
    ) -> FrozenCandidateSnapshot:
        if not self.is_durable_binding:
            raise ModelValidationError("candidate must be durably bound before transition")
        return replace(
            self,
            binding_revision=self.binding_revision + 1,
            outcome=outcome,
            result_ref=result_ref,
            review_diff=review_diff or {},
            error_code=error_code,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_ref": self.candidate_ref.to_dict(),
            "target_space_id": self.target_space_id,
            "binding_revision": self.binding_revision,
            "outcome": self.outcome.value,
            "origin": self.origin.value,
            "target_path": self.target_path,
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "media_type": self.media_type,
            "content_ref": self.content_ref.to_dict() if self.content_ref else None,
            "link_url": self.link_url,
            "source_refs": [ref.to_dict() for ref in self.source_refs],
            "source_agent_run_id": self.source_agent_run_id,
            "source_tool_call_id": self.source_tool_call_id,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "sensitivity": self.sensitivity,
            "payload_sha256": self.payload_sha256,
            "content_sha256": self.content_sha256,
            "byte_length": self.byte_length,
            "result_ref": self.result_ref.to_dict() if self.result_ref else None,
            "review_diff": _thaw_json(self.review_diff),
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class Lease:
    owner: str
    token: str
    expires_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "owner",
            _required_text(self.owner, "lease owner", maximum=512, identifier=True),
        )
        object.__setattr__(
            self,
            "token",
            _required_text(self.token, "lease token", maximum=512, identifier=True),
        )
        object.__setattr__(
            self,
            "expires_at_ms",
            _bounded_int(self.expires_at_ms, "lease expiry", minimum=1),
        )


@dataclass(frozen=True)
class ConsolidationJob:
    """Durable, revisioned P0 consolidation job."""

    job_id: str
    trigger: RootRunCompletion
    candidates: tuple[FrozenCandidateSnapshot, ...]
    status: ConsolidationJobStatus
    revision: int
    operation_id: str
    created_at_ms: int
    updated_at_ms: int
    lease: Lease | None = None
    attempt_count: int = 0
    next_attempt_at_ms: int = 0
    last_error_code: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "job_id",
            _required_text(self.job_id, "job_id", maximum=512, identifier=True),
        )
        if not isinstance(self.trigger, RootRunCompletion):
            raise TypeError("trigger must be a RootRunCompletion")
        if not self.trigger.is_root_run or self.trigger.run_status is not SourceRunStatus.COMPLETED:
            raise ModelValidationError("a consolidation job requires a completed root run")
        if self.trigger.capture_status is not RunCaptureStatus.COMPLETE:
            raise ModelValidationError("a consolidation job requires complete source capture")
        candidates = tuple(self.candidates)
        if not candidates or len(candidates) > MAX_CANDIDATES_PER_JOB:
            raise ModelValidationError("a consolidation job requires 1 to 200 candidates")
        if any(not isinstance(item, FrozenCandidateSnapshot) for item in candidates):
            raise TypeError("candidates must contain FrozenCandidateSnapshot values")
        if any(not item.is_durable_binding for item in candidates):
            raise ModelValidationError(
                "a consolidation job requires durable candidate bindings"
            )
        refs = tuple(item.candidate_ref for item in candidates)
        if len(set(refs)) != len(refs):
            raise ModelValidationError("a consolidation job cannot repeat a candidate")
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(
            self,
            "status",
            _enum(self.status, ConsolidationJobStatus, "consolidation job status"),
        )
        object.__setattr__(self, "revision", _bounded_int(self.revision, "revision", minimum=1))
        object.__setattr__(
            self,
            "operation_id",
            _required_text(
                self.operation_id,
                "operation_id",
                maximum=256,
                identifier=True,
            ),
        )
        for field_name in (
            "created_at_ms",
            "updated_at_ms",
            "attempt_count",
            "next_attempt_at_ms",
        ):
            object.__setattr__(
                self,
                field_name,
                _bounded_int(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "last_error_code",
            _optional_text(self.last_error_code, "last_error_code", maximum=128),
        )
        if self.status is ConsolidationJobStatus.LEASED:
            if not isinstance(self.lease, Lease):
                raise ModelValidationError("a leased job requires lease metadata")
        elif self.lease is not None:
            raise ModelValidationError("only a leased job may carry lease metadata")
        candidate_outcomes = {item.outcome for item in self.candidates}
        terminal_outcomes = {
            CandidateStatus.APPLIED,
            CandidateStatus.AWAITING_USER,
            CandidateStatus.ISOLATED,
            CandidateStatus.REJECTED,
            CandidateStatus.SUPERSEDED,
        }
        if self.status is ConsolidationJobStatus.PENDING:
            if candidate_outcomes != {CandidateStatus.QUEUED}:
                raise ModelValidationError("a pending job requires queued candidates")
        elif self.status is ConsolidationJobStatus.LEASED:
            if candidate_outcomes != {CandidateStatus.PROCESSING}:
                raise ModelValidationError("a leased job requires processing candidates")
        elif self.status is ConsolidationJobStatus.COMPLETED:
            if not candidate_outcomes.issubset(terminal_outcomes):
                raise ModelValidationError("a completed job requires resolved candidates")
        elif candidate_outcomes != {CandidateStatus.ISOLATED}:
            raise ModelValidationError(
                "a failed or cancelled job requires isolated candidates"
            )

    @classmethod
    def pending(
        cls,
        *,
        job_id: str,
        trigger: RootRunCompletion,
        candidates: tuple[FrozenCandidateSnapshot, ...],
        operation_id: str,
        now_ms: int,
    ) -> ConsolidationJob:
        return cls(
            job_id=job_id,
            trigger=trigger,
            candidates=candidates,
            status=ConsolidationJobStatus.PENDING,
            revision=1,
            operation_id=operation_id,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )

    def with_lease(self, lease: Lease, *, revision: int, now_ms: int) -> ConsolidationJob:
        if self.status not in {
            ConsolidationJobStatus.PENDING,
            ConsolidationJobStatus.LEASED,
        }:
            raise ModelValidationError("only a runnable job may be leased")
        if not isinstance(lease, Lease):
            raise TypeError("lease must be a Lease")
        if revision != self.revision + 1:
            raise ModelValidationError("lease revision must advance by exactly one")
        if now_ms < self.updated_at_ms:
            raise ModelValidationError("lease time cannot move backwards")
        if lease.expires_at_ms <= now_ms:
            raise ModelValidationError("lease must expire in the future")
        if (
            self.status is ConsolidationJobStatus.LEASED
            and self.lease is not None
            and self.lease.expires_at_ms > now_ms
        ):
            raise ModelValidationError("an active lease cannot be replaced")
        candidates = self.candidates
        if self.status is ConsolidationJobStatus.PENDING:
            candidates = tuple(
                item.with_outcome(CandidateStatus.PROCESSING)
                for item in self.candidates
            )
        return replace(
            self,
            candidates=candidates,
            status=ConsolidationJobStatus.LEASED,
            revision=revision,
            updated_at_ms=now_ms,
            lease=lease,
            attempt_count=self.attempt_count + 1,
            next_attempt_at_ms=0,
        )

    def retry(
        self,
        *,
        revision: int,
        retry_at_ms: int,
        error_code: str,
        now_ms: int,
    ) -> ConsolidationJob:
        if self.status is not ConsolidationJobStatus.LEASED:
            raise ModelValidationError("only a leased job may be retried")
        if revision != self.revision + 1:
            raise ModelValidationError("retry revision must advance by exactly one")
        if now_ms < self.updated_at_ms:
            raise ModelValidationError("retry time cannot move backwards")
        if retry_at_ms <= now_ms:
            raise ModelValidationError("retry_at_ms must be in the future")
        return replace(
            self,
            candidates=tuple(
                item.with_outcome(CandidateStatus.QUEUED)
                for item in self.candidates
            ),
            status=ConsolidationJobStatus.PENDING,
            revision=revision,
            updated_at_ms=now_ms,
            lease=None,
            next_attempt_at_ms=retry_at_ms,
            last_error_code=_required_text(
                error_code,
                "error_code",
                maximum=128,
                identifier=True,
            ),
        )

    def terminal(
        self,
        *,
        status: ConsolidationJobStatus,
        revision: int,
        now_ms: int,
        error_code: str = "",
        candidates: tuple[FrozenCandidateSnapshot, ...] | None = None,
    ) -> ConsolidationJob:
        target = _enum(status, ConsolidationJobStatus, "terminal status")
        if target not in {
            ConsolidationJobStatus.COMPLETED,
            ConsolidationJobStatus.FAILED,
            ConsolidationJobStatus.CANCELLED,
        }:
            raise ModelValidationError("target is not a terminal job status")
        if target in {
            ConsolidationJobStatus.COMPLETED,
            ConsolidationJobStatus.FAILED,
        } and self.status is not ConsolidationJobStatus.LEASED:
            raise ModelValidationError("completion and failure require a leased job")
        if target is ConsolidationJobStatus.CANCELLED and self.status not in {
            ConsolidationJobStatus.PENDING,
            ConsolidationJobStatus.LEASED,
        }:
            raise ModelValidationError("only a runnable job may be cancelled")
        if revision != self.revision + 1:
            raise ModelValidationError("terminal revision must advance by exactly one")
        if now_ms < self.updated_at_ms:
            raise ModelValidationError("terminal time cannot move backwards")
        terminal_candidates = candidates
        if target is ConsolidationJobStatus.COMPLETED:
            if terminal_candidates is None:
                terminal_candidates = self.candidates
            if not isinstance(terminal_candidates, tuple):
                terminal_candidates = tuple(terminal_candidates)
            if len(terminal_candidates) != len(self.candidates):
                raise ModelValidationError(
                    "completion must reconcile every candidate binding"
                )
            for before, after in zip(self.candidates, terminal_candidates):
                if (
                    not isinstance(after, FrozenCandidateSnapshot)
                    or after.candidate_ref != before.candidate_ref
                    or after.target_space_id != before.target_space_id
                    or after.binding_revision != before.binding_revision + 1
                ):
                    raise ModelValidationError(
                        "completion candidate binding does not match the leased job"
                    )
        else:
            failure_code = error_code or target.value
            terminal_candidates = tuple(
                item.with_outcome(
                    CandidateStatus.ISOLATED,
                    error_code=failure_code,
                )
                for item in self.candidates
            )
        return replace(
            self,
            candidates=terminal_candidates,
            status=target,
            revision=revision,
            updated_at_ms=now_ms,
            lease=None,
            next_attempt_at_ms=0,
            last_error_code=(
                _required_text(
                    error_code,
                    "error_code",
                    maximum=128,
                    identifier=True,
                )
                if error_code
                else ""
            ),
        )


@dataclass(frozen=True)
class CuratorLeaseFence:
    """Immutable fencing descriptor required by every curator mutation sink."""

    binding_id: str
    job_id: str
    job_revision: int
    lease_owner: str
    lease_token: str

    def __post_init__(self) -> None:
        for field_name, label in (
            ("binding_id", "binding_id"),
            ("job_id", "job_id"),
            ("lease_owner", "lease owner"),
            ("lease_token", "lease token"),
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(
                    getattr(self, field_name),
                    label,
                    maximum=512,
                    identifier=True,
                ),
            )
        object.__setattr__(
            self,
            "job_revision",
            _bounded_int(self.job_revision, "job_revision", minimum=1),
        )

    @classmethod
    def from_job(
        cls,
        binding_id: str,
        job: ConsolidationJob,
    ) -> CuratorLeaseFence:
        if not isinstance(job, ConsolidationJob):
            raise TypeError("job must be a ConsolidationJob")
        if job.status is not ConsolidationJobStatus.LEASED or job.lease is None:
            raise ModelValidationError("a lease fence requires a leased job")
        return cls(
            binding_id=binding_id,
            job_id=job.job_id,
            job_revision=job.revision,
            lease_owner=job.lease.owner,
            lease_token=job.lease.token,
        )


@dataclass(frozen=True)
class CandidateResolution:
    """One already-performed candidate outcome returned by the bound runner."""

    candidate_ref: ResourceRef
    target_space_id: str
    outcome: CandidateOutcome
    result_ref: ResourceRef | None = None
    review_diff: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_ref, ResourceRef):
            object.__setattr__(
                self,
                "candidate_ref",
                ResourceRef.from_dict(self.candidate_ref),
            )
        if self.candidate_ref.kind != "memory_candidate":
            raise ModelValidationError("candidate_ref must identify a memory candidate")
        if self.candidate_ref.fragment:
            raise ModelValidationError(
                "candidate_ref fragment is not representable by schema v4"
            )
        object.__setattr__(
            self,
            "target_space_id",
            _required_text(
                self.target_space_id,
                "target_space_id",
                maximum=512,
                identifier=True,
            ),
        )
        object.__setattr__(
            self,
            "outcome",
            _enum(self.outcome, CandidateOutcome, "candidate outcome"),
        )
        if self.result_ref is not None and not isinstance(self.result_ref, ResourceRef):
            object.__setattr__(
                self,
                "result_ref",
                ResourceRef.from_dict(self.result_ref),
            )
        frozen_diff = _bounded_review_diff(self.review_diff)
        object.__setattr__(self, "review_diff", frozen_diff)

        if self.outcome is CandidateOutcome.AWAITING_USER:
            if self.result_ref is None or self.result_ref.kind != "memory_review":
                raise ModelValidationError("awaiting_user requires a review reference")
            if not self.review_diff:
                raise ModelValidationError("awaiting_user requires a non-empty review diff")
        elif self.outcome is CandidateOutcome.APPLIED:
            if self.result_ref is None or self.result_ref.kind != "memory":
                raise ModelValidationError("applied requires a memory result reference")
            if self.review_diff:
                raise ModelValidationError("applied cannot carry a review diff")
        elif self.outcome is CandidateOutcome.SUPERSEDED:
            if self.result_ref is None or self.result_ref.kind != "memory":
                raise ModelValidationError(
                    "superseded requires a memory result reference"
                )
            if self.review_diff:
                raise ModelValidationError("superseded cannot carry a review diff")
        else:
            if self.result_ref is not None:
                raise ModelValidationError("candidate outcome cannot carry a result reference")
            if self.review_diff:
                raise ModelValidationError("candidate outcome cannot carry a review diff")
        if (
            self.result_ref is not None
            and self.result_ref.fragment != self.target_space_id
        ):
            raise ModelValidationError(
                "candidate result reference must remain in the target chat space"
            )

    @property
    def candidate_status(self) -> CandidateStatus:
        return CandidateStatus(self.outcome.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_ref": self.candidate_ref.to_dict(),
            "target_space_id": self.target_space_id,
            "outcome": self.outcome.value,
            "result_ref": self.result_ref.to_dict() if self.result_ref else None,
            "review_diff": _thaw_json(self.review_diff),
        }


@dataclass(frozen=True)
class CuratorRunResult:
    resolutions: tuple[CandidateResolution, ...]

    def __post_init__(self) -> None:
        resolutions = tuple(self.resolutions)
        if not resolutions or len(resolutions) > MAX_CANDIDATES_PER_JOB:
            raise ModelValidationError("runner result requires 1 to 200 resolutions")
        if any(not isinstance(item, CandidateResolution) for item in resolutions):
            raise TypeError("resolutions must contain CandidateResolution values")
        refs = tuple(item.candidate_ref for item in resolutions)
        if len(set(refs)) != len(refs):
            raise ModelValidationError("runner result cannot repeat a candidate")
        object.__setattr__(self, "resolutions", resolutions)


@dataclass(frozen=True)
class CuratorPolicy:
    """Non-overridable P0 policy supplied to a host-owned agent runner."""

    policy_id: str
    frozen_candidate_scope_only: bool
    require_candidate_bound_toolkit: bool
    new_paths_require_frozen_apply: bool
    conflicts_require_user_review: bool
    conflicts_require_server_diff: bool
    allow_credentials: bool
    allow_long_term_write: bool
    allow_promotion_decision: bool
    allow_task_state_write: bool
    allow_recursive_curation: bool
    expose_hidden_reasoning: bool

    @classmethod
    def p0(cls) -> CuratorPolicy:
        return cls(
            policy_id="unchain.memory_curator.p0",
            frozen_candidate_scope_only=True,
            require_candidate_bound_toolkit=True,
            new_paths_require_frozen_apply=True,
            conflicts_require_user_review=True,
            conflicts_require_server_diff=True,
            allow_credentials=False,
            allow_long_term_write=False,
            allow_promotion_decision=False,
            allow_task_state_write=False,
            allow_recursive_curation=False,
            expose_hidden_reasoning=False,
        )


@dataclass(frozen=True)
class CuratorRunRequest:
    job: ConsolidationJob
    policy: CuratorPolicy
    lease_fence: CuratorLeaseFence

    def __post_init__(self) -> None:
        if not isinstance(self.job, ConsolidationJob):
            raise TypeError("job must be a ConsolidationJob")
        if self.job.status is not ConsolidationJobStatus.LEASED:
            raise ModelValidationError("the runner requires a leased job")
        if not isinstance(self.policy, CuratorPolicy):
            raise TypeError("policy must be a CuratorPolicy")
        if not isinstance(self.lease_fence, CuratorLeaseFence):
            raise TypeError("lease_fence must be a CuratorLeaseFence")
        if (
            self.lease_fence.job_id != self.job.job_id
            or self.lease_fence.job_revision != self.job.revision
            or self.job.lease is None
            or self.lease_fence.lease_owner != self.job.lease.owner
            or self.lease_fence.lease_token != self.job.lease.token
        ):
            raise ModelValidationError("lease_fence does not match the runner job")


@dataclass(frozen=True)
class EnqueueRequest:
    trigger: RootRunCompletion
    candidates: tuple[FrozenCandidateSnapshot, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.trigger, RootRunCompletion):
            raise TypeError("trigger must be a RootRunCompletion")
        candidates = tuple(self.candidates)
        if not candidates or len(candidates) > MAX_CANDIDATES_PER_JOB:
            raise ModelValidationError("enqueue requires 1 to 200 candidates")
        if any(not isinstance(item, FrozenCandidateSnapshot) for item in candidates):
            raise TypeError("candidates must contain FrozenCandidateSnapshot values")
        object.__setattr__(self, "candidates", candidates)


@dataclass(frozen=True)
class EnqueueResult:
    disposition: EnqueueDisposition
    reason: str
    job: ConsolidationJob | None = None
    isolated_candidate_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "disposition",
            _enum(self.disposition, EnqueueDisposition, "enqueue disposition"),
        )
        object.__setattr__(self, "reason", _required_text(self.reason, "reason", maximum=128, identifier=True))
        if self.job is not None and not isinstance(self.job, ConsolidationJob):
            raise TypeError("job must be a ConsolidationJob")
        object.__setattr__(
            self,
            "isolated_candidate_count",
            _bounded_int(self.isolated_candidate_count, "isolated_candidate_count"),
        )


@dataclass(frozen=True)
class ProcessResult:
    disposition: ProcessDisposition
    reason: str
    job: ConsolidationJob

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "disposition",
            _enum(self.disposition, ProcessDisposition, "process disposition"),
        )
        object.__setattr__(self, "reason", _required_text(self.reason, "reason", maximum=128, identifier=True))
        if not isinstance(self.job, ConsolidationJob):
            raise TypeError("job must be a ConsolidationJob")


class CuratorRunnerFailure(RuntimeError):
    """A model-safe runner failure with an explicit retry disposition."""

    def __init__(
        self,
        code: str,
        *,
        retryability: FailureRetryability = FailureRetryability.TERMINAL,
        retry_delay_ms: int = 0,
    ) -> None:
        self.code = _required_text(code, "runner failure code", maximum=128, identifier=True)
        self.retryability = _enum(
            retryability,
            FailureRetryability,
            "failure retryability",
        )
        self.retry_delay_ms = _bounded_int(retry_delay_ms, "retry_delay_ms")
        if self.retryability is FailureRetryability.RETRYABLE:
            if not 0 < self.retry_delay_ms <= MAX_RETRY_DELAY_MS:
                raise ModelValidationError(
                    "a retryable failure requires a bounded positive retry delay"
                )
        elif self.retry_delay_ms:
            raise ModelValidationError("a terminal failure cannot request a retry delay")
        super().__init__(self.code)


__all__ = [
    "CandidateOrigin",
    "CandidateOutcome",
    "CandidateResolution",
    "CandidateStatus",
    "ConsolidationJob",
    "ConsolidationJobStatus",
    "CuratorPolicy",
    "CuratorLeaseFence",
    "CuratorRunRequest",
    "CuratorRunResult",
    "CuratorRunnerFailure",
    "EnqueueDisposition",
    "EnqueueRequest",
    "EnqueueResult",
    "FailureRetryability",
    "FrozenCandidateSnapshot",
    "Lease",
    "MAX_CANDIDATES_PER_JOB",
    "MAX_LEASE_MS",
    "MAX_RETRY_DELAY_MS",
    "ProcessDisposition",
    "ProcessResult",
    "RootRunCompletion",
    "RunCaptureStatus",
    "SourceRunStatus",
]
