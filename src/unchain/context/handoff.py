from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from unchain.journal.models import (
    ArtifactRef,
    AttemptRef,
    EventCursor,
    EventRange,
    OperationRef,
    ResourceRef,
    _record_tuple,
    _required_text,
)
from unchain.journal.runtime import DurableEventSink, build_operation_ref

from .artifacts import MAX_PREVIEW_BYTES, ArtifactService
from .models import HandoffEnvelope, HandoffStatus
from .projector import CanonicalSemanticEventProjector


_NotificationResult = TypeVar("_NotificationResult")


class HandoffNotifier(Protocol[_NotificationResult]):
    """Delivers once per stable operation identity, including after ambiguity."""

    def notify(
        self,
        *,
        operation: OperationRef,
        envelope: HandoffEnvelope,
    ) -> _NotificationResult:
        """Return the prior result when ``operation_id`` was already delivered."""


@dataclass(frozen=True)
class PersistedHandoff:
    """One handoff envelope paired with its exact durable object descriptor."""

    envelope: HandoffEnvelope
    full_output_artifact: ArtifactRef

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, HandoffEnvelope):
            object.__setattr__(
                self,
                "envelope",
                HandoffEnvelope.from_dict(self.envelope),
            )
        if not isinstance(self.full_output_artifact, ArtifactRef):
            object.__setattr__(
                self,
                "full_output_artifact",
                ArtifactRef.from_dict(self.full_output_artifact),
            )
        artifact = self.full_output_artifact
        if (
            artifact.ref != self.envelope.full_output_ref
            or artifact.media_type != "application/json"
            or artifact.byte_length != self.envelope.byte_length
            or artifact.sha256 != self.envelope.sha256
        ):
            raise ValueError("handoff envelope does not match its full output artifact")


def _summary(value: Any) -> str:
    if isinstance(value, str):
        rendered = value
    else:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    if not rendered:
        rendered = "No child output."
    return (
        rendered.encode("utf-8")[:MAX_PREVIEW_BYTES].decode(
            "utf-8",
            errors="ignore",
        )
        or "No child output."
    )


def _explicit_summary_source(full_output: Any, summary: Any) -> str | None:
    if summary is None:
        return None
    if isinstance(full_output, Mapping):
        for field_name in ("summary", "output"):
            if field_name in full_output and full_output[field_name] == summary:
                return field_name
    if full_output == summary:
        return "$"
    raise ValueError("explicit handoff summary must be derived from full output")


class HandoffService:
    """Persists complete child output before constructing or notifying handoff."""

    def __init__(self, artifacts: ArtifactService) -> None:
        if not isinstance(artifacts, ArtifactService):
            raise TypeError("artifacts must be an ArtifactService")
        self._artifacts = artifacts

    @property
    def execution_id(self) -> str:
        return self._artifacts.execution_id

    @property
    def artifacts(self) -> ArtifactService:
        return self._artifacts

    def persist(
        self,
        *,
        child_attempt: AttemptRef,
        status: HandoffStatus | str,
        full_output: Any,
        source_event_range: EventRange,
        artifact_refs: Sequence[ResourceRef] = (),
        operation_id: object,
    ) -> HandoffEnvelope:
        return self.persist_artifactized(
            child_attempt=child_attempt,
            status=status,
            full_output=full_output,
            source_event_range=source_event_range,
            artifact_refs=artifact_refs,
            operation_id=operation_id,
        ).envelope

    def persist_artifactized(
        self,
        *,
        child_attempt: AttemptRef,
        status: HandoffStatus | str,
        full_output: Any,
        source_event_range: EventRange,
        artifact_refs: Sequence[ResourceRef] = (),
        operation_id: object,
        summary: Any = None,
    ) -> PersistedHandoff:
        """Persist full output and retain the descriptor needed for paged reads."""

        if not isinstance(child_attempt, AttemptRef):
            child_attempt = AttemptRef.from_dict(child_attempt)
        normalized_child_run_id = child_attempt.attempt_id
        normalized_status = HandoffStatus(status)
        if not isinstance(source_event_range, EventRange):
            source_event_range = EventRange.from_dict(source_event_range)
        normalized_refs = _record_tuple(
            artifact_refs,
            ResourceRef,
            "artifact_refs",
        )
        if any(ref.kind != "artifact" or ref.fragment for ref in normalized_refs):
            raise ValueError("artifact_refs must contain whole artifact references")
        normalized_operation_id = _required_text(
            operation_id,
            "operation_id",
            identifier=True,
        )
        object_operation_id = (
            "handoff.output."
            + hashlib.sha256(normalized_operation_id.encode("utf-8")).hexdigest()
        )
        summary_source = _explicit_summary_source(full_output, summary)
        operation_binding = {
            "kind": "subagent_handoff_full_output",
            "handoff_operation_id": normalized_operation_id,
            "child_run_id": normalized_child_run_id,
            "child_attempt": child_attempt.to_dict(),
            "status": normalized_status.value,
            "source_event_range": source_event_range.to_dict(),
            "artifact_refs": [ref.to_dict() for ref in normalized_refs],
        }
        if summary_source is not None:
            operation_binding["summary_source"] = summary_source
        artifact, sanitized_output, content = self._artifacts._persist_json_value(
            full_output,
            operation_id=object_operation_id,
            operation_binding=operation_binding,
        )
        if summary_source in {"summary", "output"}:
            if not isinstance(sanitized_output, Mapping) or (
                summary_source not in sanitized_output
            ):
                raise ValueError("sanitizer removed the explicit handoff summary")
            envelope_summary = _summary(sanitized_output[summary_source])
        else:
            envelope_summary = _summary(sanitized_output)
        envelope = HandoffEnvelope(
            child_run_id=normalized_child_run_id,
            child_attempt=child_attempt,
            status=normalized_status,
            summary=envelope_summary,
            full_output_ref=artifact.ref,
            artifact_refs=normalized_refs,
            source_event_range=source_event_range,
            byte_length=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        return PersistedHandoff(
            envelope=envelope,
            full_output_artifact=artifact,
        )

    def persist_then_notify(
        self,
        *,
        notifier: HandoffNotifier[_NotificationResult],
        child_attempt: AttemptRef,
        status: HandoffStatus | str,
        full_output: Any,
        source_event_range: EventRange,
        artifact_refs: Sequence[ResourceRef] = (),
        operation_id: object,
    ) -> _NotificationResult:
        notify = getattr(notifier, "notify", None)
        if not callable(notify):
            raise TypeError("notifier must provide a callable notify method")
        normalized_operation_id = _required_text(
            operation_id,
            "operation_id",
            identifier=True,
        )
        envelope = self.persist(
            child_attempt=child_attempt,
            status=status,
            full_output=full_output,
            source_event_range=source_event_range,
            artifact_refs=artifact_refs,
            operation_id=normalized_operation_id,
        )
        delivery_id = (
            "handoff.notify."
            + hashlib.sha256(normalized_operation_id.encode("utf-8")).hexdigest()
        )
        operation = build_operation_ref(
            delivery_id,
            domain="context.handoff.notify",
            payload={"envelope": envelope.to_dict()},
        )
        return notify(operation=operation, envelope=envelope)


@dataclass(frozen=True)
class DurableHandoffReceipt:
    envelope: HandoffEnvelope
    cursor: EventCursor
    duplicate: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, HandoffEnvelope):
            object.__setattr__(
                self,
                "envelope",
                HandoffEnvelope.from_dict(self.envelope),
            )
        if not isinstance(self.cursor, EventCursor):
            object.__setattr__(self, "cursor", EventCursor.from_dict(self.cursor))
        if not isinstance(self.duplicate, bool):
            raise TypeError("duplicate must be a boolean")


class DurableHandoffRecorder:
    """Persist a complete child output before its parent journal receipt."""

    def __init__(
        self,
        *,
        attempt: AttemptRef,
        handoffs: HandoffService,
        projector: CanonicalSemanticEventProjector,
        sink: DurableEventSink,
    ) -> None:
        if not isinstance(attempt, AttemptRef):
            attempt = AttemptRef.from_dict(attempt)
        if not isinstance(handoffs, HandoffService):
            raise TypeError("handoffs must be a HandoffService")
        if type(projector) is not CanonicalSemanticEventProjector:
            raise TypeError(
                "projector must be the official CanonicalSemanticEventProjector"
            )
        if type(sink) is not DurableEventSink:
            raise TypeError("sink must be the official DurableEventSink")
        if projector.attempt != attempt or sink.attempt != attempt:
            raise ValueError(
                "handoff recorder components do not share one parent attempt"
            )
        if sink.projector is not projector:
            raise ValueError("handoff recorder sink does not use the bound projector")
        if handoffs.artifacts is not projector.artifacts:
            raise ValueError("handoff recorder must share one artifact service")
        self._attempt = attempt
        self._handoffs = handoffs
        self._projector = projector
        self._sink = sink

    @property
    def attempt(self) -> AttemptRef:
        return self._attempt

    @property
    def handoffs(self) -> HandoffService:
        return self._handoffs

    @property
    def projector(self) -> CanonicalSemanticEventProjector:
        return self._projector

    @property
    def sink(self) -> DurableEventSink:
        return self._sink

    def record(
        self,
        *,
        child_attempt: AttemptRef,
        status: HandoffStatus | str,
        full_output: Any,
        source_event_range: EventRange,
        artifact_refs: Sequence[ResourceRef] = (),
        operation_id: object,
    ) -> DurableHandoffReceipt:
        envelope = self._handoffs.persist(
            child_attempt=child_attempt,
            status=status,
            full_output=full_output,
            source_event_range=source_event_range,
            artifact_refs=artifact_refs,
            operation_id=operation_id,
        )
        draft = self._projector.project_handoff_envelope(envelope)
        appended = self._sink.append_projected(draft)
        return DurableHandoffReceipt(
            envelope=envelope,
            cursor=appended.cursor,
            duplicate=appended.duplicate,
        )


__all__ = [
    "DurableHandoffReceipt",
    "DurableHandoffRecorder",
    "HandoffNotifier",
    "HandoffService",
    "PersistedHandoff",
]
