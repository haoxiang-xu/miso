from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from unchain.journal import (
    ArtifactRef,
    AttemptRef,
    BoundToolReceiptIndex,
    EventCursor,
    EventRange,
)
from unchain.journal.models import _freeze_json, _required_text, _thaw_json

from .derived_handoff import (
    DerivedHandoffInputIngress,
    DurableDerivedHandoffInputReceipt,
    HostResolvedDerivedHandoffInput,
)
from .factory import ContextExecutionBundle
from .handoff import PersistedHandoff
from .host_adapter import ContextArtifactHandoffHostAdapter
from .models import HandoffEnvelope, HandoffStatus


class ContextSubagentInputError(RuntimeError):
    """A child run could not prove one exact durable parent input."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _normalize_input_messages(
    value: str | list[dict[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("subagent input text is required")
        messages: list[dict[str, Any]] = [{"role": "user", "content": value}]
    elif isinstance(value, list):
        if not value:
            raise ValueError("subagent input messages are required")
        messages = []
        for message in value:
            if not isinstance(message, Mapping):
                raise TypeError("subagent input messages must contain objects")
            messages.append(dict(message))
    else:
        raise TypeError("subagent input must be text or a list of messages")
    frozen = _freeze_json(messages, path="input_messages")
    if not isinstance(frozen, tuple) or any(
        not isinstance(message, Mapping) for message in frozen
    ):
        raise TypeError("subagent input messages are not canonical JSON")
    return frozen


def _input_summary(messages: Sequence[Mapping[str, Any]]) -> str:
    for message in reversed(messages):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            without_controls = "".join(
                " " if ord(character) < 32 else character
                for character in content
            )
            summary = " ".join(without_controls.split())[:1_024].rstrip()
            if summary:
                return summary
    return "Durable subagent input"


def _operation_id(
    *,
    parent_attempt: AttemptRef,
    call_id: str,
    child_run_id: str,
) -> str:
    identity = {
        "schema": "unchain.subagent_input.operation.v1",
        "parent_attempt": parent_attempt.to_dict(),
        "call_id": call_id,
        "child_run_id": child_run_id,
    }
    return "handoff.subagent-input." + hashlib.sha256(
        _canonical_json_bytes(identity)
    ).hexdigest()


def _source_tool_intent(
    bundle: ContextExecutionBundle,
    *,
    call_id: str,
):
    journal = bundle.journal
    if not isinstance(journal, BoundToolReceiptIndex):
        raise ContextSubagentInputError(
            "durable subagent input requires an exact tool receipt index"
        )
    lookup = journal.lookup_tool_execution_receipts(
        attempt=bundle.attempt,
        call_id=call_id,
    )
    intents = tuple(
        event for event in lookup.events if event.event_type == "tool_call"
    )
    if (
        lookup.attempt != bundle.attempt
        or lookup.call_id != call_id
        or lookup.overflow
        or len(intents) != 1
    ):
        raise ContextSubagentInputError(
            "subagent input has no unique durable parent tool intent"
        )
    intent = intents[0]
    if (
        intent.attempt != bundle.attempt
        or intent.payload.get("call_id") != call_id
        or not isinstance(intent.payload.get("tool_name"), str)
        or not intent.payload.get("tool_name")
    ):
        raise ContextSubagentInputError(
            "subagent input parent tool intent changed identity"
        )
    return intent


@dataclass(frozen=True)
class PreparedSubagentInput:
    parent_attempt: AttemptRef
    call_id: str
    child_run_id: str
    child_id: str
    mode: str
    lineage: tuple[str, ...]
    template_name: str | None
    input_messages: tuple[Mapping[str, Any], ...]
    source_event_range: EventRange
    operation_id: str
    full_output: Mapping[str, Any]
    persisted: PersistedHandoff

    def __post_init__(self) -> None:
        if not isinstance(self.parent_attempt, AttemptRef):
            object.__setattr__(
                self,
                "parent_attempt",
                AttemptRef.from_dict(self.parent_attempt),
            )
        for field_name in ("call_id", "child_run_id", "child_id", "mode"):
            object.__setattr__(
                self,
                field_name,
                _required_text(
                    getattr(self, field_name),
                    field_name,
                    identifier=True,
                ),
            )
        if not isinstance(self.source_event_range, EventRange):
            object.__setattr__(
                self,
                "source_event_range",
                EventRange.from_dict(self.source_event_range),
            )
        object.__setattr__(
            self,
            "operation_id",
            _required_text(self.operation_id, "operation_id", identifier=True),
        )
        lineage = tuple(
            _required_text(item, "lineage item", identifier=True)
            for item in self.lineage
        )
        if not lineage or lineage[-1] != self.child_id:
            raise ValueError("subagent lineage must terminate at child_id")
        object.__setattr__(self, "lineage", lineage)
        if self.template_name is not None:
            object.__setattr__(
                self,
                "template_name",
                _required_text(
                    self.template_name,
                    "template_name",
                    identifier=True,
                ),
            )
        messages = _freeze_json(
            _thaw_json(self.input_messages),
            path="input_messages",
        )
        if not isinstance(messages, tuple):
            raise TypeError("input_messages must be an array")
        object.__setattr__(self, "input_messages", messages)
        full_output = _freeze_json(
            _thaw_json(self.full_output),
            path="full_output",
        )
        if not isinstance(full_output, Mapping):
            raise TypeError("full_output must be an object")
        object.__setattr__(self, "full_output", full_output)
        if not isinstance(self.persisted, PersistedHandoff):
            raise TypeError("persisted must be a PersistedHandoff")

    @property
    def execution_id(self) -> str:
        return self.parent_attempt.generation.execution_id

    @property
    def child_attempt(self) -> AttemptRef:
        return AttemptRef(self.parent_attempt.generation, self.child_run_id)


@dataclass(frozen=True)
class SubagentInputPreparation:
    prepared: PreparedSubagentInput
    recovered_result: Any = None

    @property
    def execution_id(self) -> str:
        return self.prepared.execution_id


def prepare_subagent_input(
    bundle: ContextExecutionBundle,
    *,
    call_id: str,
    child_run_id: str,
    child_id: str,
    mode: str,
    lineage: Sequence[str],
    template_name: str | None,
    input_messages: str | list[dict[str, Any]],
) -> PreparedSubagentInput:
    if not isinstance(bundle, ContextExecutionBundle):
        raise TypeError("bundle must be a ContextExecutionBundle")
    normalized_call_id = _required_text(call_id, "call_id", identifier=True)
    normalized_child_run_id = _required_text(
        child_run_id,
        "child_run_id",
        identifier=True,
    )
    normalized_child_id = _required_text(child_id, "child_id", identifier=True)
    normalized_mode = _required_text(mode, "mode", identifier=True)
    normalized_lineage = tuple(
        _required_text(item, "lineage item", identifier=True) for item in lineage
    )
    normalized_template = (
        None
        if template_name is None
        else _required_text(template_name, "template_name", identifier=True)
    )
    normalized_messages = _normalize_input_messages(input_messages)
    intent = _source_tool_intent(bundle, call_id=normalized_call_id)
    cursor = EventCursor(intent.store_seq, intent.event_id)
    source_range = EventRange(start=cursor, end=cursor)
    operation_id = _operation_id(
        parent_attempt=bundle.attempt,
        call_id=normalized_call_id,
        child_run_id=normalized_child_run_id,
    )
    summary = _input_summary(normalized_messages)
    full_output = {
        "schema": "unchain.subagent_input.v1",
        "parent_attempt": bundle.attempt.to_dict(),
        "call_id": normalized_call_id,
        "child_run_id": normalized_child_run_id,
        "child_id": normalized_child_id,
        "mode": normalized_mode,
        "lineage": list(normalized_lineage),
        "template_name": normalized_template,
        "input_messages": _thaw_json(normalized_messages),
        "summary": summary,
    }
    persisted = bundle.handoffs.persist_artifactized(
        child_attempt=bundle.attempt,
        status=HandoffStatus.COMPLETE,
        full_output=full_output,
        source_event_range=source_range,
        operation_id=operation_id,
        summary=summary,
    )
    return PreparedSubagentInput(
        parent_attempt=bundle.attempt,
        call_id=normalized_call_id,
        child_run_id=normalized_child_run_id,
        child_id=normalized_child_id,
        mode=normalized_mode,
        lineage=normalized_lineage,
        template_name=normalized_template,
        input_messages=normalized_messages,
        source_event_range=source_range,
        operation_id=operation_id,
        full_output=full_output,
        persisted=persisted,
    )


def persist_subagent_input(
    prepared: PreparedSubagentInput,
    bundle: ContextExecutionBundle,
) -> DurableDerivedHandoffInputReceipt:
    if not isinstance(prepared, PreparedSubagentInput):
        raise TypeError("prepared must be a PreparedSubagentInput")
    if not isinstance(bundle, ContextExecutionBundle):
        raise TypeError("bundle must be a ContextExecutionBundle")
    if bundle.attempt != prepared.child_attempt:
        raise ContextSubagentInputError(
            "subagent input child attempt changed its prepared binding"
        )
    ingress = DerivedHandoffInputIngress(
        consumer_attempt=bundle.attempt,
        source_attempt=prepared.parent_attempt,
        handoff_recorder=bundle.handoff_recorder,
        input_ingress=bundle.ingress,
    )
    receipt = ingress.persist(
        HostResolvedDerivedHandoffInput(
            consumer_attempt=bundle.attempt,
            source_attempt=prepared.parent_attempt,
            status=HandoffStatus.COMPLETE,
            full_output=_thaw_json(prepared.full_output),
            source_event_range=prepared.source_event_range,
            operation_id=prepared.operation_id,
            summary=_thaw_json(prepared.full_output)["summary"],
        )
    )
    if (
        receipt.envelope != prepared.persisted.envelope
        or receipt.full_output_artifact != prepared.persisted.full_output_artifact
    ):
        raise ContextSubagentInputError(
            "subagent input bootstrap changed its prepared CAS descriptor"
        )
    return receipt


def _canonical_json_value(content: bytes) -> Any:
    try:
        decoded = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextSubagentInputError(
            "subagent durable artifact is not valid UTF-8 JSON"
        ) from exc
    if _canonical_json_bytes(decoded) != content:
        raise ContextSubagentInputError(
            "subagent durable artifact is not canonical JSON"
        )
    return decoded


def _has_exact_persisted_input(
    bundle: ContextExecutionBundle,
    prepared: PreparedSubagentInput,
) -> bool:
    snapshot = bundle.journal.capture_snapshot()
    handoff_candidates = []
    for event in snapshot.events:
        if (
            event.attempt != prepared.child_attempt
            or event.event_type != "handoff.recorded"
        ):
            continue
        raw_envelope = event.payload.get("handoff_envelope")
        raw_artifact = event.payload.get("full_output_artifact")
        if not isinstance(raw_envelope, Mapping) or not isinstance(
            raw_artifact,
            Mapping,
        ):
            continue
        envelope = HandoffEnvelope.from_dict(raw_envelope)
        artifact = ArtifactRef.from_dict(raw_artifact)
        if (
            envelope.child_attempt == prepared.parent_attempt
            and envelope.source_event_range == prepared.source_event_range
        ):
            if (
                envelope != prepared.persisted.envelope
                or artifact != prepared.persisted.full_output_artifact
            ):
                raise ContextSubagentInputError(
                    "persisted subagent input contradicts its prepared CAS"
                )
            handoff_candidates.append((event, envelope, artifact))
    if not handoff_candidates:
        return False
    if len(handoff_candidates) != 1:
        raise ContextSubagentInputError(
            "subagent input has ambiguous durable handoff receipts"
        )
    handoff_event, envelope, artifact = handoff_candidates[0]
    input_candidates = []
    for event in snapshot.events:
        if (
            event.attempt != prepared.child_attempt
            or event.event_type != "message.user"
            or event.store_seq <= handoff_event.store_seq
        ):
            continue
        message = _thaw_json(event.payload.get("message"))
        if not isinstance(message, Mapping):
            continue
        attachments = message.get("attachments")
        if not isinstance(attachments, list) or len(attachments) != 1:
            continue
        attachment = attachments[0]
        if not isinstance(attachment, Mapping):
            continue
        try:
            attachment_artifact = ArtifactRef.from_dict(attachment["artifact"])
        except (KeyError, TypeError, ValueError):
            continue
        if attachment_artifact != artifact:
            continue
        try:
            content = json.loads(str(message.get("content") or ""))
        except json.JSONDecodeError:
            continue
        if (
            content.get("schema") != "unchain.derived_handoff_input.v1"
            or content.get("consumer_attempt") != prepared.child_attempt.to_dict()
            or content.get("source_attempt") != prepared.parent_attempt.to_dict()
            or content.get("handoff_event")
            != EventCursor(handoff_event.store_seq, handoff_event.event_id).to_dict()
            or content.get("handoff_envelope") != envelope.to_dict()
            or content.get("full_output_artifact") != artifact.to_dict()
        ):
            continue
        input_candidates.append(event)
    if len(input_candidates) != 1:
        raise ContextSubagentInputError(
            "subagent input has no unique durable current-input receipt"
        )
    return True


def recover_subagent_result(
    bundle: ContextExecutionBundle,
    prepared: PreparedSubagentInput,
):
    """Return one exact parent-recorded result or fail closed after child start."""

    from unchain.subagents.types import SubagentResult

    if not isinstance(bundle, ContextExecutionBundle):
        raise TypeError("bundle must be a ContextExecutionBundle")
    if not isinstance(prepared, PreparedSubagentInput):
        raise TypeError("prepared must be a PreparedSubagentInput")
    snapshot = bundle.journal.capture_snapshot()
    completed_events = tuple(
        event
        for event in snapshot.events
        if event.attempt == prepared.parent_attempt
        and event.event_type == "handoff.recorded"
        and event.payload.get("child_run_id") == prepared.child_run_id
    )
    has_input = _has_exact_persisted_input(bundle, prepared)
    if completed_events:
        if len(completed_events) != 1 or not has_input:
            raise ContextSubagentInputError(
                "completed subagent has no unique durable input/result pair"
            )
        adapter = ContextArtifactHandoffHostAdapter(
            recorder=bundle.handoff_recorder,
        )
        receipt = adapter.recover_handoff(completed_events[0])
        if receipt.envelope.child_attempt != prepared.child_attempt:
            raise ContextSubagentInputError(
                "completed subagent result changed its child attempt"
            )
        content = bundle.artifacts.read_full(
            receipt.full_output_artifact,
            remaining_budget_bytes=receipt.full_output_artifact.byte_length,
        )
        decoded = _canonical_json_value(content)
        if not isinstance(decoded, dict):
            raise ContextSubagentInputError(
                "completed subagent result artifact is not an object"
            )
        try:
            result = SubagentResult.from_record_dict(decoded)
        except (TypeError, ValueError) as exc:
            raise ContextSubagentInputError(
                "completed subagent result artifact is malformed"
            ) from exc
        if (
            result.to_record_dict() != decoded
            or result.mode != prepared.mode
            or result.agent_name != prepared.child_id
            or result.template_name != prepared.template_name
            or tuple(result.lineage) != prepared.lineage
        ):
            raise ContextSubagentInputError(
                "completed subagent result changed its prepared identity"
            )
        return result

    child_events = tuple(
        event
        for event in snapshot.events
        if event.attempt == prepared.child_attempt
        and event.event_type not in {"handoff.recorded", "message.user"}
    )
    if child_events:
        raise ContextSubagentInputError(
            "subagent attempt already started without a reusable completion"
        )
    return None


__all__ = [
    "ContextSubagentInputError",
    "PreparedSubagentInput",
    "SubagentInputPreparation",
    "persist_subagent_input",
    "prepare_subagent_input",
    "recover_subagent_result",
]
