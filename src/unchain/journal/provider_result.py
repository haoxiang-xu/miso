"""Canonical, recoverable receipts for one provider turn result."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from unchain.run_bundle import ProviderCallReceipt

from unchain.kernel.types import ModelTurnResult, ToolCall
from unchain.providers.request_lease import (
    ProviderRequestLease,
    ProviderRequestStatus,
    ProviderRequestSubject,
)

from .models import (
    ArtifactRef,
    EventCursor,
    JournalEvent,
    ModelValidationError,
    OperationRef,
    _record_data,
    _required_text,
    _sha256,
)
from .resource_limits import JsonResourceLimits, validate_json_resource


PROVIDER_TURN_RESULT_EVENT_TYPE = "provider.turn_result"
MAX_PROVIDER_TURN_RESULT_RECEIPTS = 2
PROVIDER_TURN_RESULT_LIMITS = JsonResourceLimits(
    max_items=250_000,
    max_bytes=64 * 1024 * 1024,
    max_depth=64,
    max_nodes=1_000_000,
)
_RESULT_FIELDS = frozenset(
    {
        "assistant_messages",
        "tool_calls",
        "final_text",
        "response_id",
        "reasoning_items",
        "consumed_tokens",
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "provider_replay_frame",
    }
)
_EVENT_PAYLOAD_FIELDS = frozenset(
    {
        "subject_sha256",
        "iteration",
        "envelope_sha256",
        "route",
        "retry_ordinal",
        "route_sha256",
        "visible_output",
        "result_sha256",
        "result_artifact",
    }
)


class ProviderTurnResultIntegrityError(RuntimeError):
    """Durable result metadata, bytes, or request subject disagreed."""


def _canonical_bytes(value: Any) -> bytes:
    validate_json_resource(
        value,
        boundary="provider turn result",
        limits=PROVIDER_TURN_RESULT_LIMITS,
    )
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
        raise ModelValidationError(
            "provider turn result must be strict canonical JSON"
        ) from exc


def _strict_json_copy(value: Any, *, path: str) -> Any:
    validate_json_resource(
        value,
        boundary=path,
        limits=PROVIDER_TURN_RESULT_LIMITS,
    )
    pending = [value]
    while pending:
        item = pending.pop()
        item_type = type(item)
        if item is None or item_type in {bool, int, float, str}:
            continue
        if item_type is list:
            pending.extend(item)
            continue
        if item_type is dict:
            if any(type(key) is not str for key in item):
                raise TypeError(f"{path} requires exact text object keys")
            pending.extend(item.values())
            continue
        raise TypeError(f"{path} requires exact JSON value types")
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
        raise ModelValidationError(f"{path} must be detached strict JSON") from exc


def _freeze_json(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType(
            {key: _freeze_json(child) for key, child in value.items()}
        )
    if type(value) is list:
        return tuple(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if type(value) is tuple:
        return [_thaw_json(child) for child in value]
    return value


def _canonical_identifier(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be exact text")
    normalized = unicodedata.normalize("NFC", value)
    if (
        normalized != value
        or not value
        or len(value) > 4_096
        or any(ord(character) < 32 for character in value)
    ):
        raise ModelValidationError(f"{field_name} must be canonical non-empty text")
    return value


def _non_negative_int(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an exact integer")
    if value < 0:
        raise ModelValidationError(f"{field_name} must be non-negative")
    return value


def _subject(value: object) -> ProviderRequestSubject:
    if type(value) is ProviderRequestSubject:
        return ProviderRequestSubject.from_dict(value.to_dict())
    if type(value) is dict:
        return ProviderRequestSubject.from_dict(value)
    raise TypeError("subject must be an exact ProviderRequestSubject")


def _result_payload(result: ModelTurnResult) -> dict[str, Any]:
    if type(result) is not ModelTurnResult:
        raise TypeError("result must be an exact ModelTurnResult")
    return {
        "assistant_messages": result.assistant_messages,
        "tool_calls": [
            {
                "call_id": call.call_id,
                "name": call.name,
                "arguments": call.arguments,
            }
            for call in result.tool_calls
        ],
        "final_text": result.final_text,
        "response_id": result.response_id,
        "reasoning_items": result.reasoning_items,
        "consumed_tokens": result.consumed_tokens,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cache_read_input_tokens": result.cache_read_input_tokens,
        "cache_creation_input_tokens": result.cache_creation_input_tokens,
        "provider_replay_frame": result.provider_replay_frame,
    }


def _validated_result(value: object) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != _RESULT_FIELDS:
        raise ModelValidationError("provider result uses an unsupported shape")
    copied = _strict_json_copy(value, path="provider turn result payload")
    messages = copied["assistant_messages"]
    if type(messages) is not list or any(type(item) is not dict for item in messages):
        raise TypeError("assistant_messages must be an exact array of objects")
    calls = copied["tool_calls"]
    if type(calls) is not list:
        raise TypeError("tool_calls must be an exact array")
    for call in calls:
        if type(call) is not dict or set(call) != {"call_id", "name", "arguments"}:
            raise ModelValidationError("tool call uses an unsupported shape")
        _canonical_identifier(call["call_id"], "tool call id")
        _canonical_identifier(call["name"], "tool call name")
        if type(call["arguments"]) not in {dict, str} and call["arguments"] is not None:
            raise TypeError("tool call arguments must be an object, text, or null")
    if type(copied["final_text"]) is not str:
        raise TypeError("final_text must be exact text")
    response_id = copied["response_id"]
    if response_id is not None:
        _canonical_identifier(response_id, "response_id")
    reasoning = copied["reasoning_items"]
    if reasoning is not None and (
        type(reasoning) is not list or any(type(item) is not dict for item in reasoning)
    ):
        raise TypeError("reasoning_items must be null or an exact array of objects")
    replay = copied["provider_replay_frame"]
    if replay is not None and type(replay) is not dict:
        raise TypeError("provider_replay_frame must be null or an exact object")
    for field_name in (
        "consumed_tokens",
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        _non_negative_int(copied[field_name], field_name)
    return _freeze_json(copied)


@dataclass(frozen=True, slots=True)
class ProviderTurnResultEnvelope:
    """Provider-neutral final result bound to one exact network send."""

    SCHEMA: ClassVar[str] = "unchain.provider_turn_result.v1"

    subject: ProviderRequestSubject
    route_sha256: str
    visible_output: bool
    result: Mapping[str, Any] = field(repr=False)
    result_sha256: str = ""

    def __post_init__(self) -> None:
        subject = _subject(self.subject)
        route_sha256 = _sha256(self.route_sha256, "route_sha256")
        if type(self.visible_output) is not bool:
            raise TypeError("visible_output must be an exact boolean")
        result = _validated_result(self.result)
        result_plain = _thaw_json(result)
        result_sha256 = hashlib.sha256(_canonical_bytes(result_plain)).hexdigest()
        if self.result_sha256 and (
            _sha256(self.result_sha256, "result_sha256") != result_sha256
        ):
            raise ModelValidationError(
                "result_sha256 does not match the provider result"
            )
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "route_sha256", route_sha256)
        object.__setattr__(self, "result", result)
        object.__setattr__(self, "result_sha256", result_sha256)

    @property
    def subject_sha256(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.subject.to_dict())).hexdigest()

    @classmethod
    def from_model_turn_result(
        cls,
        *,
        subject: ProviderRequestSubject,
        route_sha256: str,
        visible_output: bool,
        result: ModelTurnResult,
    ) -> ProviderTurnResultEnvelope:
        return cls(
            subject=subject,
            route_sha256=route_sha256,
            visible_output=visible_output,
            result=_result_payload(result),
        )

    def to_model_turn_result(self) -> ModelTurnResult:
        raw = _thaw_json(self.result)
        return ModelTurnResult(
            assistant_messages=raw["assistant_messages"],
            tool_calls=[ToolCall(**call) for call in raw["tool_calls"]],
            final_text=raw["final_text"],
            response_id=raw["response_id"],
            reasoning_items=raw["reasoning_items"],
            consumed_tokens=raw["consumed_tokens"],
            input_tokens=raw["input_tokens"],
            output_tokens=raw["output_tokens"],
            cache_read_input_tokens=raw["cache_read_input_tokens"],
            cache_creation_input_tokens=raw["cache_creation_input_tokens"],
            provider_replay_frame=raw["provider_replay_frame"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "subject": self.subject.to_dict(),
            "route_sha256": self.route_sha256,
            "visible_output": self.visible_output,
            "result": _thaw_json(self.result),
            "result_sha256": self.result_sha256,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProviderTurnResultEnvelope:
        raw = _record_data(
            value,
            schema=cls.SCHEMA,
            required=frozenset(
                {
                    "subject",
                    "route_sha256",
                    "visible_output",
                    "result",
                    "result_sha256",
                }
            ),
        )
        return cls(
            subject=ProviderRequestSubject.from_dict(raw["subject"]),
            route_sha256=raw["route_sha256"],
            visible_output=raw["visible_output"],
            result=raw["result"],
            result_sha256=raw["result_sha256"],
        )


@dataclass(frozen=True, slots=True)
class ProviderTurnResultEventFields:
    subject: ProviderRequestSubject
    subject_sha256: str
    route_sha256: str
    visible_output: bool
    result_sha256: str
    artifact: ArtifactRef


def _validated_artifact(
    artifact: object,
    *,
    content: bytes | None = None,
) -> ArtifactRef:
    if type(artifact) is not ArtifactRef:
        raise TypeError("result artifact must be an exact ArtifactRef")
    if artifact.ref.kind != "artifact" or artifact.ref.fragment:
        raise ProviderTurnResultIntegrityError(
            "result artifact must be one whole artifact"
        )
    if artifact.media_type != "application/json" or artifact.preview:
        raise ProviderTurnResultIntegrityError(
            "result artifact must be unpreviewed application/json"
        )
    if artifact.byte_length > PROVIDER_TURN_RESULT_LIMITS.max_bytes:
        raise ProviderTurnResultIntegrityError(
            "result artifact size exceeds the provider result limit"
        )
    if content is not None:
        if type(content) is not bytes:
            raise TypeError("result artifact readback must be exact bytes")
        if (
            artifact.byte_length != len(content)
            or artifact.sha256 != hashlib.sha256(content).hexdigest()
        ):
            raise ProviderTurnResultIntegrityError(
                "result artifact bytes or digest changed"
            )
    return artifact


def build_provider_turn_result_event_payload(
    *,
    envelope: ProviderTurnResultEnvelope,
    artifact: ArtifactRef,
) -> dict[str, Any]:
    if type(envelope) is not ProviderTurnResultEnvelope:
        raise TypeError("envelope must be an exact ProviderTurnResultEnvelope")
    content = envelope.canonical_bytes()
    _validated_artifact(artifact, content=content)
    subject = envelope.subject
    return {
        "subject_sha256": envelope.subject_sha256,
        "iteration": subject.iteration,
        "envelope_sha256": subject.envelope_sha256,
        "route": subject.route,
        "retry_ordinal": subject.retry_ordinal,
        "route_sha256": envelope.route_sha256,
        "visible_output": envelope.visible_output,
        "result_sha256": envelope.result_sha256,
        "result_artifact": artifact.to_dict(),
    }


def provider_turn_result_event_fields(
    event: JournalEvent,
) -> ProviderTurnResultEventFields:
    if type(event) is not JournalEvent:
        raise TypeError("result receipt event must be an exact JournalEvent")
    if event.event_type != PROVIDER_TURN_RESULT_EVENT_TYPE:
        raise ProviderTurnResultIntegrityError(
            "result receipt event type is unsupported"
        )
    payload = dict(event.payload)
    if set(payload) != _EVENT_PAYLOAD_FIELDS:
        raise ProviderTurnResultIntegrityError(
            "result receipt event payload shape changed"
        )
    try:
        subject = ProviderRequestSubject(
            attempt=event.attempt,
            iteration=payload["iteration"],
            envelope_sha256=payload["envelope_sha256"],
            route=payload["route"],
            retry_ordinal=payload["retry_ordinal"],
        )
        subject_sha256 = _sha256(payload["subject_sha256"], "subject_sha256")
        route_sha256 = _sha256(payload["route_sha256"], "route_sha256")
        result_sha256 = _sha256(payload["result_sha256"], "result_sha256")
        artifact = ArtifactRef.from_dict(payload["result_artifact"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderTurnResultIntegrityError(
            "result receipt event fields are invalid"
        ) from exc
    expected_subject_sha256 = hashlib.sha256(
        _canonical_bytes(subject.to_dict())
    ).hexdigest()
    if subject_sha256 != expected_subject_sha256:
        raise ProviderTurnResultIntegrityError(
            "result receipt event subject digest changed"
        )
    if type(payload["visible_output"]) is not bool:
        raise ProviderTurnResultIntegrityError("result receipt visible_output changed")
    _validated_artifact(artifact)
    if event.resource_refs != (artifact.ref,):
        raise ProviderTurnResultIntegrityError("result receipt resource ref changed")
    return ProviderTurnResultEventFields(
        subject=subject,
        subject_sha256=subject_sha256,
        route_sha256=route_sha256,
        visible_output=payload["visible_output"],
        result_sha256=result_sha256,
        artifact=artifact,
    )


@dataclass(frozen=True, slots=True)
class ProviderTurnResultReceipt:
    envelope: ProviderTurnResultEnvelope
    artifact: ArtifactRef
    event: JournalEvent
    cursor: EventCursor
    duplicate: bool = False

    def __post_init__(self) -> None:
        if type(self.envelope) is not ProviderTurnResultEnvelope:
            raise TypeError("envelope must be an exact ProviderTurnResultEnvelope")
        _validated_artifact(self.artifact, content=self.envelope.canonical_bytes())
        if type(self.event) is not JournalEvent:
            raise TypeError("event must be an exact JournalEvent")
        if type(self.cursor) is not EventCursor:
            raise TypeError("cursor must be an exact EventCursor")
        if type(self.duplicate) is not bool:
            raise TypeError("duplicate must be an exact boolean")
        fields = provider_turn_result_event_fields(self.event)
        if (
            fields.subject != self.envelope.subject
            or fields.route_sha256 != self.envelope.route_sha256
            or fields.visible_output != self.envelope.visible_output
            or fields.result_sha256 != self.envelope.result_sha256
            or fields.artifact != self.artifact
            or self.cursor != EventCursor(self.event.store_seq, self.event.event_id)
        ):
            raise ProviderTurnResultIntegrityError(
                "result receipt event or cursor disagrees with its envelope"
            )

    @property
    def subject(self) -> ProviderRequestSubject:
        return self.envelope.subject


@dataclass(frozen=True, slots=True)
class ProviderTurnResultReceiptLookup:
    SCHEMA: ClassVar[str] = "unchain.provider_turn_result_receipt_lookup.v1"

    subject: ProviderRequestSubject
    events: tuple[JournalEvent, ...] = ()
    overflow: bool = False

    def __post_init__(self) -> None:
        subject = _subject(self.subject)
        if type(self.events) is not tuple:
            raise TypeError("events must be an exact tuple")
        if len(self.events) > MAX_PROVIDER_TURN_RESULT_RECEIPTS:
            raise ModelValidationError(
                "provider result lookup may contain at most two events"
            )
        if type(self.overflow) is not bool:
            raise TypeError("overflow must be an exact boolean")
        previous_seq = 0
        identities: set[tuple[str, str]] = set()
        for event in self.events:
            fields = provider_turn_result_event_fields(event)
            if fields.subject != subject:
                raise ModelValidationError(
                    "provider result receipt belongs to a foreign subject"
                )
            if event.store_seq <= previous_seq:
                raise ModelValidationError(
                    "provider result receipts must be strictly ordered"
                )
            previous_seq = event.store_seq
            identity = (event.event_id, event.operation.operation_id)
            if identity in identities:
                raise ModelValidationError(
                    "provider result receipt identity is duplicated"
                )
            identities.add(identity)
        object.__setattr__(self, "subject", subject)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "subject": self.subject.to_dict(),
            "events": [event.to_dict() for event in self.events],
            "overflow": self.overflow,
        }

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any],
    ) -> ProviderTurnResultReceiptLookup:
        raw = _record_data(
            value,
            schema=cls.SCHEMA,
            required=frozenset({"subject", "events", "overflow"}),
        )
        if type(raw["events"]) is not list:
            raise TypeError("events must be an exact array")
        return cls(
            subject=ProviderRequestSubject.from_dict(raw["subject"]),
            events=tuple(JournalEvent.from_dict(event) for event in raw["events"]),
            overflow=raw["overflow"],
        )


@dataclass(frozen=True, slots=True)
class ProviderTurnResultPersistRequest:
    """Validated input to one atomic provider-result repository mutation."""

    started_lease: ProviderRequestLease
    envelope: ProviderTurnResultEnvelope
    artifact_operation: OperationRef
    event_operation: OperationRef
    event_id: str
    provider_call_receipt: ProviderCallReceipt | None = None

    def __post_init__(self) -> None:
        if type(self.started_lease) is not ProviderRequestLease:
            raise TypeError("started_lease must be an exact ProviderRequestLease")
        if type(self.envelope) is not ProviderTurnResultEnvelope:
            raise TypeError("envelope must be an exact ProviderTurnResultEnvelope")
        if type(self.artifact_operation) is not OperationRef:
            raise TypeError("artifact_operation must be an exact OperationRef")
        if type(self.event_operation) is not OperationRef:
            raise TypeError("event_operation must be an exact OperationRef")
        object.__setattr__(
            self,
            "event_id",
            _required_text(self.event_id, "event_id", identifier=True),
        )
        if self.provider_call_receipt is not None:
            from unchain.run_bundle import ProviderCallReceipt

            if type(self.provider_call_receipt) is not ProviderCallReceipt:
                raise TypeError(
                    "provider_call_receipt must be an exact ProviderCallReceipt or null"
                )
        if self.started_lease.status is not ProviderRequestStatus.STARTED:
            raise ProviderTurnResultIntegrityError(
                "provider result persistence requires a STARTED lease"
            )
        if self.started_lease.subject != self.envelope.subject:
            raise ProviderTurnResultIntegrityError(
                "provider result lease subject changed"
            )
        if self.started_lease.route_sha256 != self.envelope.route_sha256:
            raise ProviderTurnResultIntegrityError(
                "provider result lease route digest changed"
            )
        if self.provider_call_receipt is not None:
            from unchain.providers.physical_send import provider_physical_ordinal

            identity = self.provider_call_receipt.identity
            subject = self.started_lease.subject
            if (
                identity.execution_id
                != subject.attempt.generation.execution_id
                or identity.attempt_id != subject.attempt.attempt_id
                or identity.iteration != subject.iteration
                or identity.retry_ordinal != provider_physical_ordinal(subject)
            ):
                raise ProviderTurnResultIntegrityError(
                    "provider accounting receipt changed the durable send subject"
                )


class BoundProviderTurnResultStore(ABC):
    """Execution-bound atomic store for an unreplayable provider result."""

    def __init__(self, execution_id: str) -> None:
        self._execution_id = _required_text(
            execution_id,
            "execution_id",
            identifier=True,
        )

    @property
    def execution_id(self) -> str:
        return self._execution_id

    def persist_provider_turn_result_cas(
        self,
        *,
        started_lease: ProviderRequestLease,
        envelope: ProviderTurnResultEnvelope,
        artifact_operation: OperationRef,
        event_operation: OperationRef,
        event_id: str,
        provider_call_receipt: ProviderCallReceipt | None = None,
    ) -> ProviderTurnResultReceipt:
        """Atomically persist result artifact, event, and indexed receipt."""

        request = ProviderTurnResultPersistRequest(
            started_lease=started_lease,
            envelope=envelope,
            artifact_operation=artifact_operation,
            event_operation=event_operation,
            event_id=event_id,
            provider_call_receipt=provider_call_receipt,
        )
        if self.execution_id != envelope.subject.attempt.generation.execution_id:
            raise ProviderTurnResultIntegrityError(
                "provider result persistence crossed its execution scope"
            )
        receipt = self._persist_provider_turn_result_cas(request=request)
        if (
            type(receipt) is not ProviderTurnResultReceipt
            or receipt.envelope != request.envelope
            or receipt.event.event_id != request.event_id
            or receipt.event.operation != request.event_operation
        ):
            raise ProviderTurnResultIntegrityError(
                "provider result repository returned a changed receipt"
            )
        return receipt

    @abstractmethod
    def _persist_provider_turn_result_cas(
        self,
        *,
        request: ProviderTurnResultPersistRequest,
    ) -> ProviderTurnResultReceipt:
        """Implement one validated atomic result mutation."""

    @abstractmethod
    def read_provider_turn_result_full_verified(
        self,
        *,
        artifact: ArtifactRef,
    ) -> bytes:
        """Return complete digest-verified result bytes."""

    @abstractmethod
    def lookup_provider_turn_result_receipts(
        self,
        *,
        subject: ProviderRequestSubject,
    ) -> ProviderTurnResultReceiptLookup:
        """Return the exhaustive bounded result receipt set for one send."""


def recover_provider_turn_result(
    store: BoundProviderTurnResultStore,
    *,
    subject: ProviderRequestSubject,
    expected_route_sha256: str,
) -> ProviderTurnResultReceipt:
    """Recover one exact final result without provider or callback work."""

    if not isinstance(store, BoundProviderTurnResultStore):
        raise TypeError("store must be a BoundProviderTurnResultStore")
    subject = _subject(subject)
    expected_route_sha256 = _sha256(
        expected_route_sha256,
        "expected_route_sha256",
    )
    if store.execution_id != subject.attempt.generation.execution_id:
        raise ProviderTurnResultIntegrityError(
            "provider result store execution scope changed"
        )
    try:
        lookup = store.lookup_provider_turn_result_receipts(subject=subject)
    except Exception as exc:
        raise ProviderTurnResultIntegrityError("provider result lookup failed") from exc
    if (
        type(lookup) is not ProviderTurnResultReceiptLookup
        or lookup.subject != subject
        or lookup.overflow
        or len(lookup.events) != 1
    ):
        raise ProviderTurnResultIntegrityError(
            "provider result lookup is missing, conflicting, or overflowed"
        )
    event = lookup.events[0]
    fields = provider_turn_result_event_fields(event)
    if fields.route_sha256 != expected_route_sha256:
        raise ProviderTurnResultIntegrityError("provider result route digest changed")
    try:
        content = store.read_provider_turn_result_full_verified(
            artifact=fields.artifact
        )
    except Exception as exc:
        raise ProviderTurnResultIntegrityError(
            "provider result artifact read failed"
        ) from exc
    _validated_artifact(fields.artifact, content=content)
    try:
        decoded = json.loads(content.decode("utf-8"))
        envelope = ProviderTurnResultEnvelope.from_dict(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ProviderTurnResultIntegrityError(
            "provider result artifact bytes are invalid"
        ) from exc
    if envelope.canonical_bytes() != content:
        raise ProviderTurnResultIntegrityError(
            "provider result artifact bytes are not canonical"
        )
    return ProviderTurnResultReceipt(
        envelope=envelope,
        artifact=fields.artifact,
        event=event,
        cursor=EventCursor(event.store_seq, event.event_id),
        duplicate=False,
    )


__all__ = [
    "BoundProviderTurnResultStore",
    "MAX_PROVIDER_TURN_RESULT_RECEIPTS",
    "PROVIDER_TURN_RESULT_EVENT_TYPE",
    "PROVIDER_TURN_RESULT_LIMITS",
    "ProviderTurnResultEnvelope",
    "ProviderTurnResultEventFields",
    "ProviderTurnResultIntegrityError",
    "ProviderTurnResultPersistRequest",
    "ProviderTurnResultReceipt",
    "ProviderTurnResultReceiptLookup",
    "build_provider_turn_result_event_payload",
    "provider_turn_result_event_fields",
    "recover_provider_turn_result",
]
