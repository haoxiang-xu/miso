from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from unchain.durability import mark_durable_persistence_failure
from unchain.journal.models import (
    ArtifactRef,
    AttemptRef,
    EventCursor,
    EventRange,
    JournalEvent,
    ResourceRef,
    _record_tuple,
    _thaw_json,
)
from unchain.journal.ports import JournalRepositoryError
from unchain.journal.runtime import SemanticEventDraft
from unchain.subagents.types import SubagentResult
from unchain.tools.runtime import ToolRuntimeOutcome

from .artifacts import (
    ArtifactReadPage,
    ArtifactIntegrityError,
    ArtifactService,
    ToolResultArtifactization,
)
from .handoff import DurableHandoffRecorder, PersistedHandoff
from .models import HandoffEnvelope, HandoffStatus
from .ports import ContextRepositoryError


_Result = TypeVar("_Result")


class HostPayloadAdapterError(RuntimeError):
    """A production payload did not satisfy the Context V2 host contract."""


class HostHandoffIntegrityError(HostPayloadAdapterError):
    """A recovered handoff receipt contradicted its durable descriptor."""


def _durable_write(operation: Callable[[], _Result]) -> _Result:
    try:
        return operation()
    except (
        ArtifactIntegrityError,
        ContextRepositoryError,
        JournalRepositoryError,
        OSError,
    ) as error:
        boundary = mark_durable_persistence_failure(error)
        raise boundary from None


def _handoff_status(status: object) -> HandoffStatus:
    normalized = str(status or "").strip().casefold()
    if normalized in {"complete", "completed"}:
        return HandoffStatus.COMPLETE
    if normalized in {"failed", "error"}:
        return HandoffStatus.FAILED
    if normalized in {"cancelled", "canceled"}:
        return HandoffStatus.CANCELLED
    return HandoffStatus.PARTIAL


def _validate_handoff_artifact(
    envelope: HandoffEnvelope,
    artifact: ArtifactRef,
) -> None:
    if (
        artifact.ref.kind != "artifact"
        or artifact.ref.fragment
        or artifact.media_type != "application/json"
        or artifact.ref != envelope.full_output_ref
        or artifact.byte_length != envelope.byte_length
        or artifact.sha256 != envelope.sha256
    ):
        raise HostHandoffIntegrityError(
            "handoff full output descriptor contradicts its envelope"
        )


@dataclass(frozen=True)
class HostHandoffReceipt:
    """Parent-visible handoff plus the exact descriptor used for later reads."""

    envelope: HandoffEnvelope
    full_output_artifact: ArtifactRef
    cursor: EventCursor
    duplicate: bool = False

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
        if not isinstance(self.cursor, EventCursor):
            object.__setattr__(self, "cursor", EventCursor.from_dict(self.cursor))
        if not isinstance(self.duplicate, bool):
            raise TypeError("duplicate must be a boolean")
        _validate_handoff_artifact(self.envelope, self.full_output_artifact)

    @property
    def model_payload(self) -> dict[str, Any]:
        """Return the bounded parent payload; complete output stays behind the ref."""

        return {
            "status": self.envelope.status.value,
            "summary": self.envelope.summary,
            "child_run_id": self.envelope.child_run_id,
            "full_output_ref": self.envelope.full_output_ref.to_dict(),
            "artifact_refs": [ref.to_dict() for ref in self.envelope.artifact_refs],
            "content_bytes": self.envelope.byte_length,
            "content_sha256": self.envelope.sha256,
        }


class ContextArtifactHandoffHostAdapter:
    """Bridge completed host payloads into the Context V2 durable data plane.

    This adapter never executes a tool or child agent. It accepts only completed
    production values, persists their full sanitized output, and exposes a
    bounded model payload after the durable receipt exists.
    """

    def __init__(self, *, recorder: DurableHandoffRecorder) -> None:
        if type(recorder) is not DurableHandoffRecorder:
            raise TypeError("recorder must be the official DurableHandoffRecorder")
        self._recorder = recorder

    @property
    def attempt(self) -> AttemptRef:
        return self._recorder.attempt

    @property
    def artifacts(self) -> ArtifactService:
        return self._recorder.handoffs.artifacts

    @property
    def recorder(self) -> DurableHandoffRecorder:
        return self._recorder

    def persist_tool_outcome(
        self,
        outcome: ToolRuntimeOutcome,
        *,
        operation_id: object,
    ) -> ToolResultArtifactization:
        """Persist an already-completed tool payload before inline reduction."""

        if type(outcome) is not ToolRuntimeOutcome or not outcome.handled:
            raise HostPayloadAdapterError(
                "tool outcome must be one handled production completion"
            )
        if outcome.tool_result is None:
            raise HostPayloadAdapterError("completed tool outcome has no result")
        return _durable_write(
            lambda: self.artifacts.artifactize_tool_result(
                outcome.tool_result,
                operation_id=operation_id,
            )
        )

    def record_subagent_result(
        self,
        result: SubagentResult,
        *,
        child_attempt: AttemptRef,
        source_event_range: EventRange,
        artifact_refs: Sequence[ResourceRef] = (),
        operation_id: object,
    ) -> HostHandoffReceipt:
        """Persist a complete child result before appending its parent receipt."""

        if type(result) is not SubagentResult:
            raise HostPayloadAdapterError(
                "subagent completion must be an exact SubagentResult"
            )
        refs = _record_tuple(artifact_refs, ResourceRef, "artifact_refs")
        persisted = _durable_write(
            lambda: self._recorder.handoffs.persist_artifactized(
                child_attempt=child_attempt,
                status=_handoff_status(result.status),
                full_output=result.to_record_dict(),
                source_event_range=source_event_range,
                artifact_refs=refs,
                operation_id=operation_id,
                summary=(result.summary if result.summary else result.output),
            )
        )
        appended = _durable_write(lambda: self._append_handoff(persisted))
        return HostHandoffReceipt(
            envelope=persisted.envelope,
            full_output_artifact=persisted.full_output_artifact,
            cursor=appended.cursor,
            duplicate=appended.duplicate,
        )

    def _append_handoff(self, persisted: PersistedHandoff):
        draft = self._recorder.projector.project_handoff_envelope(persisted.envelope)
        payload = _thaw_json(draft.payload)
        payload["full_output_artifact"] = persisted.full_output_artifact.to_dict()
        descriptor_bound = SemanticEventDraft(
            event_id=draft.event_id,
            event_type=draft.event_type,
            attempt=draft.attempt,
            operation_id=draft.operation_id,
            payload=payload,
            resource_refs=draft.resource_refs,
        )
        return self._recorder.sink.append_projected(descriptor_bound)

    def recover_handoff(self, event: JournalEvent) -> HostHandoffReceipt:
        """Recover one descriptor-bound handoff without re-running a child."""

        if not isinstance(event, JournalEvent):
            raise TypeError("event must be a JournalEvent")
        if event.event_type != "handoff.recorded" or event.attempt != self.attempt:
            raise HostHandoffIntegrityError(
                "handoff receipt is outside the bound parent attempt"
            )
        raw_envelope = event.payload.get("handoff_envelope")
        raw_artifact = event.payload.get("full_output_artifact")
        if not isinstance(raw_envelope, Mapping) or not isinstance(
            raw_artifact,
            Mapping,
        ):
            raise HostHandoffIntegrityError(
                "handoff receipt has no durable output descriptor"
            )
        try:
            envelope = HandoffEnvelope.from_dict(raw_envelope)
            artifact = ArtifactRef.from_dict(raw_artifact)
        except (KeyError, TypeError, ValueError) as error:
            raise HostHandoffIntegrityError(
                "handoff full output descriptor is malformed"
            ) from error
        _validate_handoff_artifact(envelope, artifact)
        expected_refs = tuple(
            dict.fromkeys((envelope.full_output_ref, *envelope.artifact_refs))
        )
        if event.resource_refs != expected_refs:
            raise HostHandoffIntegrityError(
                "handoff descriptor is not authorized by its journal receipt"
            )
        reconstructed = SemanticEventDraft(
            event_id=event.event_id,
            event_type=event.event_type,
            attempt=event.attempt,
            operation_id=event.operation.operation_id,
            payload=event.payload,
            resource_refs=event.resource_refs,
        )
        if reconstructed.operation != event.operation:
            raise HostHandoffIntegrityError("handoff journal operation digest changed")
        return HostHandoffReceipt(
            envelope=envelope,
            full_output_artifact=artifact,
            cursor=EventCursor(event.store_seq, event.event_id),
            duplicate=False,
        )

    def read_page(
        self,
        artifact: ArtifactRef,
        *,
        offset: int = 0,
        limit: int = 65_536,
    ) -> ArtifactReadPage:
        """Read one verified page from an exact host-visible descriptor."""

        return self.artifacts.read_page(
            artifact,
            offset=offset,
            limit=limit,
        )


__all__ = [
    "ContextArtifactHandoffHostAdapter",
    "HostHandoffIntegrityError",
    "HostHandoffReceipt",
    "HostPayloadAdapterError",
]
