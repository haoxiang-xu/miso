from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from unchain.journal import (
    AttemptRef,
    DurableEventSink,
    JournalAppendResult,
    PreparedSemanticEvent,
    ResourceRef,
)
from unchain.journal.models import _freeze_json, _required_text, _thaw_json
from unchain.journal.snapshot import JournalSnapshot

from .attachments import (
    HostResolvedAttachment,
    normalize_host_resolved_attachments,
)
from .projector import CanonicalSemanticEventProjector


class ContextInputIngressError(RuntimeError):
    """An explicit host input could not become one durable input receipt."""


def _authorized_content_ref(payload, resource_refs) -> ResourceRef:
    raw_ref = payload.get("content_ref")
    try:
        content_ref = (
            raw_ref
            if isinstance(raw_ref, ResourceRef)
            else ResourceRef.from_dict(raw_ref)
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise ContextInputIngressError(
            "current-input receipt has no durable content reference"
        ) from exc
    if content_ref.kind != "artifact" or content_ref not in resource_refs:
        raise ContextInputIngressError(
            "current-input content reference is not authorized"
        )
    return content_ref


def _authorized_attachment_refs(
    payload,
    resource_refs,
    *,
    content_ref: ResourceRef,
) -> tuple[tuple[HostResolvedAttachment, ...], tuple[ResourceRef, ...]]:
    raw_attachments = payload.get("attachments")
    raw_refs = payload.get("attachment_refs")
    if raw_attachments is None and raw_refs is None:
        return (), ()
    if raw_attachments is None or raw_refs is None:
        raise ContextInputIngressError(
            "current-input attachment references are incomplete"
        )
    try:
        attachments = normalize_host_resolved_attachments(raw_attachments)
        if not isinstance(raw_refs, (list, tuple)):
            raise TypeError("attachment_refs must be an array")
        refs = tuple(
            value if isinstance(value, ResourceRef) else ResourceRef.from_dict(value)
            for value in raw_refs
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise ContextInputIngressError(
            "current-input attachment references are invalid"
        ) from exc
    expected_refs = tuple(attachment.artifact.ref for attachment in attachments)
    message = payload.get("message")
    message_attachments = (
        message.get("attachments") if isinstance(message, Mapping) else None
    )
    try:
        normalized_message_attachments = normalize_host_resolved_attachments(
            message_attachments
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise ContextInputIngressError(
            "current-input attachment references are invalid"
        ) from exc
    if (
        not attachments
        or refs != expected_refs
        or any(ref.kind != "artifact" or ref.fragment for ref in refs)
        or tuple(resource_refs) != (content_ref, *refs)
        or normalized_message_attachments != attachments
    ):
        raise ContextInputIngressError(
            "current-input attachment references are not exactly authorized"
        )
    return attachments, refs


@dataclass(frozen=True)
class HostResolvedCurrentInput:
    """One host-resolved user input; never inferred from a transcript."""

    attempt: AttemptRef
    content: str
    message_index: int = 0
    attachments: tuple[HostResolvedAttachment, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, AttemptRef):
            object.__setattr__(
                self,
                "attempt",
                AttemptRef.from_dict(self.attempt),
            )
        attachments = normalize_host_resolved_attachments(self.attachments)
        object.__setattr__(self, "attachments", attachments)
        if not isinstance(self.content, str):
            raise TypeError("current user input content must be text")
        if not self.content.strip() and not attachments:
            raise ValueError(
                "current user input requires non-empty text or one attachment"
            )
        if (
            isinstance(self.message_index, bool)
            or not isinstance(self.message_index, int)
            or self.message_index < 0
        ):
            raise ValueError("message_index must be a non-negative integer")


@dataclass(frozen=True)
class HostResolvedInteractionInput:
    """One host-resolved interaction response; never guessed from messages."""

    attempt: AttemptRef
    interaction_id: str
    response: Any
    submitted_by: str = "user"

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, AttemptRef):
            object.__setattr__(
                self,
                "attempt",
                AttemptRef.from_dict(self.attempt),
            )
        object.__setattr__(
            self,
            "interaction_id",
            _required_text(
                self.interaction_id,
                "interaction_id",
                identifier=True,
            ),
        )
        object.__setattr__(
            self,
            "submitted_by",
            _required_text(
                self.submitted_by,
                "submitted_by",
                identifier=True,
            ),
        )
        object.__setattr__(
            self,
            "response",
            _freeze_json(self.response, path="response"),
        )


class ContextInputIngress:
    """Persist an exact current input through one attempt-owned projector/sink."""

    def __init__(
        self,
        *,
        attempt: AttemptRef,
        projector: CanonicalSemanticEventProjector,
        sink: DurableEventSink,
    ) -> None:
        if not isinstance(attempt, AttemptRef):
            attempt = AttemptRef.from_dict(attempt)
        if not isinstance(projector, CanonicalSemanticEventProjector):
            raise TypeError("projector must be a CanonicalSemanticEventProjector")
        if not isinstance(sink, DurableEventSink):
            raise TypeError("sink must be a DurableEventSink")
        if projector.attempt != attempt or sink.attempt != attempt:
            raise ContextInputIngressError(
                "ingress components must share the exact bound attempt"
            )
        if sink.projector is not projector:
            raise ContextInputIngressError(
                "durable sink must own the same projector instance"
            )
        self._attempt = attempt
        self._projector = projector
        self._sink = sink

    @property
    def attempt(self) -> AttemptRef:
        return self._attempt

    @property
    def projector(self) -> CanonicalSemanticEventProjector:
        return self._projector

    @property
    def sink(self) -> DurableEventSink:
        return self._sink

    def persist(
        self,
        current_input: HostResolvedCurrentInput | HostResolvedInteractionInput,
        *,
        precondition: Callable[[JournalSnapshot], None] | None = None,
    ) -> JournalAppendResult:
        if not isinstance(
            current_input,
            (HostResolvedCurrentInput, HostResolvedInteractionInput),
        ):
            raise TypeError(
                "current_input must be a host-resolved user or interaction input"
            )
        if current_input.attempt != self._attempt:
            raise ContextInputIngressError(
                "current input does not match the bound attempt"
            )
        if precondition is not None and not callable(precondition):
            raise TypeError("precondition must be callable")
        prepared: PreparedSemanticEvent | None = None
        if isinstance(current_input, HostResolvedCurrentInput):
            if precondition is not None:
                raise ContextInputIngressError(
                    "user messages do not take an acceptance precondition"
                )
            message = {"role": "user", "content": current_input.content}
            if current_input.attachments:
                draft = self._projector.project_user_message(
                    message,
                    message_index=current_input.message_index,
                    attachments=current_input.attachments,
                )
            else:
                draft = self._projector.project_user_message(
                    message,
                    message_index=current_input.message_index,
                )
            expected_event_type = "message.user"
        else:
            prepared = self._projector.prepare_interaction_resolution(
                interaction_id=current_input.interaction_id,
                response=_thaw_json(current_input.response),
                submitted_by=current_input.submitted_by,
            )
            draft = prepared.draft
            expected_event_type = "interaction.resolved"
        if draft.attempt != self._attempt or draft.event_type != expected_event_type:
            raise ContextInputIngressError(
                "projector returned a foreign current-input draft"
            )
        projected_content_ref = _authorized_content_ref(
            draft.payload,
            draft.resource_refs,
        )
        projected_attachments, projected_attachment_refs = (
            _authorized_attachment_refs(
                draft.payload,
                draft.resource_refs,
                content_ref=projected_content_ref,
            )
        )
        if isinstance(current_input, HostResolvedCurrentInput):
            expected_attachments = current_input.attachments
            if projected_attachments != expected_attachments:
                raise ContextInputIngressError(
                    "projected current-input attachment references changed"
                )
        if prepared is not None:
            result = self._sink.append_prepared(prepared, precondition=precondition)
        else:
            result = self._sink.append_projected(draft)
        if not isinstance(result, JournalAppendResult):
            raise ContextInputIngressError(
                "durable sink did not return an append receipt"
            )
        event = result.event
        content_ref = _authorized_content_ref(
            event.payload,
            event.resource_refs,
        )
        attachments, attachment_refs = _authorized_attachment_refs(
            event.payload,
            event.resource_refs,
            content_ref=content_ref,
        )
        if (
            event.attempt != self._attempt
            or event.event_type != expected_event_type
            or content_ref != projected_content_ref
            or attachments != projected_attachments
            or attachment_refs != projected_attachment_refs
        ):
            raise ContextInputIngressError(
                "current-input append receipt failed exact verification"
            )
        return result


__all__ = [
    "ContextInputIngress",
    "ContextInputIngressError",
    "HostResolvedAttachment",
    "HostResolvedCurrentInput",
    "HostResolvedInteractionInput",
]
