"""Journal-only tool-catalog receipt authority.

This layer binds durable receipt fields and an artifact descriptor; it does not
read or verify artifact bytes.  A production repository adapter plus atomic and
concurrent catalog admission remain rollout blockers for the integration layer.
"""

from __future__ import annotations

import hashlib
import json
import re
import weakref
from abc import abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from .models import (
    ArtifactRef,
    AttemptRef,
    EventCursor,
    GenerationRef,
    JournalEvent,
    ModelValidationError,
    OperationRef,
    ResourceRef,
    _record_data,
)
from .ports import BoundToolReceiptIndex
from .resource_limits import JsonResourceLimits, validate_json_resource


TOOL_CATALOG_SNAPSHOT_EVENT_TYPE = "tool.catalog_snapshot"
MAX_TOOL_CATALOG_RECEIPTS = 2
MAX_TOOL_CATALOG_ITERATION = 2**31 - 1
TOOL_CATALOG_RECEIPT_LOOKUP_LIMITS = JsonResourceLimits(
    max_items=MAX_TOOL_CATALOG_RECEIPTS,
    max_bytes=256 * 1024,
    max_depth=32,
    max_nodes=4_096,
)
_CATALOG_PAYLOAD_FIELDS = frozenset(
    {"iteration", "catalog_sha256", "catalog_artifact"}
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ToolCatalogReceiptIntegrityError(RuntimeError):
    """A catalog receipt index did not prove one exact durable authority."""


def _require_iteration(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("iteration must be an integer")
    if not 0 <= value <= MAX_TOOL_CATALOG_ITERATION:
        raise ModelValidationError(
            f"iteration must be between 0 and {MAX_TOOL_CATALOG_ITERATION}"
        )
    return value


def _require_sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ModelValidationError(
            f"{field_name} must be a lowercase SHA-256 digest"
        )
    return value


def _require_exact_attempt(value: object, field_name: str) -> AttemptRef:
    if type(value) is not AttemptRef:
        raise TypeError(f"{field_name} must be an exact AttemptRef")
    if type(value.generation) is not GenerationRef:
        raise TypeError(
            f"{field_name}.generation must be an exact GenerationRef"
        )
    return value


def _require_canonical_event(event: object) -> JournalEvent:
    if type(event) is not JournalEvent:
        raise TypeError("catalog receipts must be exact JournalEvent records")
    _require_exact_attempt(event.attempt, "catalog receipt attempt")
    if type(event.operation) is not OperationRef:
        raise TypeError(
            "catalog receipt operation must be an exact OperationRef"
        )
    if any(type(ref) is not ResourceRef for ref in event.resource_refs):
        raise TypeError(
            "catalog receipt resource refs must be exact ResourceRef records"
        )
    return event


@dataclass(frozen=True)
class ToolCatalogReceiptLookup:
    """One exhaustive bounded lookup for an attempt/iteration catalog."""

    SCHEMA: ClassVar[str] = "unchain.tool_catalog_receipt_lookup.v1"

    attempt: AttemptRef
    iteration: int
    events: tuple[JournalEvent, ...] = ()
    overflow: bool = False

    def __post_init__(self) -> None:
        _require_exact_attempt(self.attempt, "attempt")
        object.__setattr__(self, "iteration", _require_iteration(self.iteration))
        if type(self.events) is not tuple:
            raise TypeError("events must be an exact tuple")
        if len(self.events) > MAX_TOOL_CATALOG_RECEIPTS:
            raise ModelValidationError(
                "tool catalog receipt lookup may contain at most two events"
            )
        if type(self.overflow) is not bool:
            raise TypeError("overflow must be a boolean")

        previous_store_seq = 0
        event_ids: set[str] = set()
        operation_ids: set[str] = set()
        cursors: set[tuple[int, str]] = set()
        for candidate in self.events:
            event = _require_canonical_event(candidate)
            if event.attempt != self.attempt:
                raise ModelValidationError(
                    "tool catalog receipt belongs to a foreign attempt"
                )
            if event.event_type != TOOL_CATALOG_SNAPSHOT_EVENT_TYPE:
                raise ModelValidationError(
                    "tool catalog receipt has an unsupported event type"
                )
            if event.payload.get("iteration") != self.iteration or type(
                event.payload.get("iteration")
            ) is not int:
                raise ModelValidationError(
                    "tool catalog receipt iteration changed"
                )
            if event.store_seq <= previous_store_seq:
                raise ModelValidationError(
                    "tool catalog receipts must be strictly ordered"
                )
            previous_store_seq = event.store_seq
            cursor = (event.store_seq, event.event_id)
            if (
                event.event_id in event_ids
                or event.operation.operation_id in operation_ids
                or cursor in cursors
            ):
                raise ModelValidationError(
                    "tool catalog receipt identity is duplicated"
                )
            event_ids.add(event.event_id)
            operation_ids.add(event.operation.operation_id)
            cursors.add(cursor)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "attempt": self.attempt.to_dict(),
            "iteration": self.iteration,
            "events": [event.to_dict() for event in self.events],
            "overflow": self.overflow,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ToolCatalogReceiptLookup:
        if type(value) is not dict:
            raise TypeError(
                "tool catalog receipt lookup must be an exact dict"
            )
        raw = _record_data(
            value,
            schema=cls.SCHEMA,
            required=frozenset(
                {"attempt", "iteration", "events", "overflow"}
            ),
        )
        raw_events = raw["events"]
        if type(raw_events) is not list:
            raise TypeError("events must be an exact list")
        if len(raw_events) > MAX_TOOL_CATALOG_RECEIPTS:
            raise ModelValidationError(
                "tool catalog receipt lookup may contain at most two events"
            )
        if any(type(event) is not dict for event in raw_events):
            raise TypeError(
                "each tool catalog receipt event must be an exact dict"
            )
        validate_json_resource(
            raw,
            boundary="tool_catalog_receipt_lookup",
            limits=TOOL_CATALOG_RECEIPT_LOOKUP_LIMITS,
        )
        return cls(
            attempt=AttemptRef.from_dict(raw["attempt"]),
            iteration=raw["iteration"],
            events=tuple(JournalEvent.from_dict(event) for event in raw_events),
            overflow=raw["overflow"],
        )


class BoundToolCatalogIndex(BoundToolReceiptIndex):
    """Execution-bound journal with an exact catalog receipt lookup."""

    @abstractmethod
    def lookup_tool_catalog_receipts(
        self,
        *,
        attempt: AttemptRef,
        iteration: int,
    ) -> ToolCatalogReceiptLookup:
        """Atomically return the exhaustive receipts for one catalog subject."""


def _catalog_artifact_from_event(
    event: JournalEvent,
) -> ArtifactRef:
    if frozenset(event.payload) != _CATALOG_PAYLOAD_FIELDS:
        raise ToolCatalogReceiptIntegrityError(
            "catalog receipt payload fields are not canonical"
        )
    raw_artifact = event.payload.get("catalog_artifact")
    if not isinstance(raw_artifact, Mapping):
        raise ToolCatalogReceiptIntegrityError(
            "catalog receipt lacks a complete artifact descriptor"
        )
    try:
        artifact = ArtifactRef.from_dict(raw_artifact)
    except (TypeError, ValueError) as exc:
        raise ToolCatalogReceiptIntegrityError(
            "catalog receipt has a malformed artifact descriptor"
        ) from exc
    if type(artifact) is not ArtifactRef or type(artifact.ref) is not ResourceRef:
        raise ToolCatalogReceiptIntegrityError(
            "catalog receipt artifact descriptor is not canonical"
        )
    if artifact.ref.kind != "artifact":
        raise ToolCatalogReceiptIntegrityError(
            "catalog receipt must identify an artifact resource"
        )
    if artifact.ref.fragment:
        raise ToolCatalogReceiptIntegrityError(
            "catalog receipt must identify the whole artifact"
        )
    if artifact.media_type != "application/json":
        raise ToolCatalogReceiptIntegrityError(
            "catalog receipt must identify a JSON artifact"
        )
    if artifact.preview:
        raise ToolCatalogReceiptIntegrityError(
            "catalog receipt artifact preview must be empty"
        )
    if event.resource_refs != (artifact.ref,):
        raise ToolCatalogReceiptIntegrityError(
            "catalog receipt resource refs must contain exactly its artifact ref"
        )
    return artifact


def _canonical_proof_sha256(
    *,
    attempt: AttemptRef,
    iteration: int,
    event: JournalEvent,
    cursor: EventCursor,
    catalog_sha256: str,
    catalog_artifact: ArtifactRef,
) -> str:
    body = {
        "domain": "unchain.recovered_tool_catalog_authority.v1",
        "attempt": attempt.to_dict(),
        "iteration": iteration,
        "event": event.to_dict(),
        "cursor": cursor.to_dict(),
        "catalog_sha256": catalog_sha256,
        "catalog_artifact": catalog_artifact.to_dict(),
    }
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verify_catalog_proof_fields(
    *,
    attempt: AttemptRef,
    iteration: int,
    event: JournalEvent,
    cursor: EventCursor,
    catalog_sha256: str,
    catalog_artifact: ArtifactRef,
) -> None:
    try:
        _require_exact_attempt(attempt, "catalog proof attempt")
        iteration = _require_iteration(iteration)
        _require_canonical_event(event)
        catalog_sha256 = _require_sha256(
            catalog_sha256,
            "catalog proof catalog_sha256",
        )
    except (TypeError, ValueError) as exc:
        raise ToolCatalogReceiptIntegrityError(
            "catalog authority proof fields are not canonical"
        ) from exc
    if type(cursor) is not EventCursor:
        raise ToolCatalogReceiptIntegrityError(
            "catalog authority proof cursor is not canonical"
        )
    if type(catalog_artifact) is not ArtifactRef or type(
        catalog_artifact.ref
    ) is not ResourceRef:
        raise ToolCatalogReceiptIntegrityError(
            "catalog authority proof artifact is not canonical"
        )
    if event.event_type != TOOL_CATALOG_SNAPSHOT_EVENT_TYPE:
        raise ToolCatalogReceiptIntegrityError(
            "catalog authority proof event type changed"
        )
    if event.attempt != attempt:
        raise ToolCatalogReceiptIntegrityError(
            "catalog authority proof attempt changed"
        )
    event_iteration = event.payload.get("iteration")
    if type(event_iteration) is not int or event_iteration != iteration:
        raise ToolCatalogReceiptIntegrityError(
            "catalog authority proof iteration changed"
        )
    if (
        cursor.store_seq != event.store_seq
        or cursor.event_id != event.event_id
    ):
        raise ToolCatalogReceiptIntegrityError(
            "catalog authority proof cursor changed"
        )
    event_catalog_sha256 = event.payload.get("catalog_sha256")
    if event_catalog_sha256 != catalog_sha256:
        raise ToolCatalogReceiptIntegrityError(
            "catalog authority proof digest changed"
        )
    embedded_artifact = _catalog_artifact_from_event(event)
    if embedded_artifact != catalog_artifact:
        raise ToolCatalogReceiptIntegrityError(
            "catalog authority proof artifact changed"
        )


@dataclass(frozen=True)
class _RecoveredToolCatalogProof:
    attempt: AttemptRef
    iteration: int
    event: JournalEvent
    cursor: EventCursor
    catalog_sha256: str
    catalog_artifact: ArtifactRef
    proof_sha256: str

    @classmethod
    def create(
        cls,
        *,
        attempt: AttemptRef,
        iteration: int,
        event: JournalEvent,
        cursor: EventCursor,
        catalog_sha256: str,
        catalog_artifact: ArtifactRef,
    ) -> _RecoveredToolCatalogProof:
        _verify_catalog_proof_fields(
            attempt=attempt,
            iteration=iteration,
            event=event,
            cursor=cursor,
            catalog_sha256=catalog_sha256,
            catalog_artifact=catalog_artifact,
        )
        return cls(
            attempt=attempt,
            iteration=iteration,
            event=event,
            cursor=cursor,
            catalog_sha256=catalog_sha256,
            catalog_artifact=catalog_artifact,
            proof_sha256=_canonical_proof_sha256(
                attempt=attempt,
                iteration=iteration,
                event=event,
                cursor=cursor,
                catalog_sha256=catalog_sha256,
                catalog_artifact=catalog_artifact,
            ),
        )

    def verify(self) -> None:
        _verify_catalog_proof_fields(
            attempt=self.attempt,
            iteration=self.iteration,
            event=self.event,
            cursor=self.cursor,
            catalog_sha256=self.catalog_sha256,
            catalog_artifact=self.catalog_artifact,
        )
        expected = _canonical_proof_sha256(
            attempt=self.attempt,
            iteration=self.iteration,
            event=self.event,
            cursor=self.cursor,
            catalog_sha256=self.catalog_sha256,
            catalog_artifact=self.catalog_artifact,
        )
        if self.proof_sha256 != expected:
            raise ToolCatalogReceiptIntegrityError(
                "catalog authority canonical proof changed"
            )


class RecoveredToolCatalogAuthority:
    """Identity-bound receipt proof; it does not verify artifact bytes."""

    __slots__ = ("__proof_sha256", "__weakref__")

    def __new__(cls, *args: object, **kwargs: object):
        del args, kwargs
        raise TypeError(
            "RecoveredToolCatalogAuthority is minted only by verified recovery"
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("RecoveredToolCatalogAuthority cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("RecoveredToolCatalogAuthority is immutable")

    def __copy__(self):
        raise TypeError("RecoveredToolCatalogAuthority cannot be copied")

    def __deepcopy__(self, memo: object):
        del memo
        raise TypeError("RecoveredToolCatalogAuthority cannot be copied")

    def __reduce_ex__(self, protocol: int):
        del protocol
        raise TypeError("RecoveredToolCatalogAuthority cannot be serialized")

    @property
    def attempt(self) -> AttemptRef:
        return _RECOVERED_AUTHORITY_ISSUER.verify(self).attempt

    @property
    def iteration(self) -> int:
        return _RECOVERED_AUTHORITY_ISSUER.verify(self).iteration

    @property
    def event(self) -> JournalEvent:
        return _RECOVERED_AUTHORITY_ISSUER.verify(self).event

    @property
    def cursor(self) -> EventCursor:
        return _RECOVERED_AUTHORITY_ISSUER.verify(self).cursor

    @property
    def catalog_sha256(self) -> str:
        return _RECOVERED_AUTHORITY_ISSUER.verify(self).catalog_sha256

    @property
    def catalog_artifact(self) -> ArtifactRef:
        return _RECOVERED_AUTHORITY_ISSUER.verify(self).catalog_artifact


class _RecoveredToolCatalogAuthorityIssuer:
    def __init__(self) -> None:
        self._proofs: dict[
            int,
            tuple[
                weakref.ReferenceType[RecoveredToolCatalogAuthority],
                _RecoveredToolCatalogProof,
            ],
        ] = {}

    def mint(
        self,
        *,
        attempt: AttemptRef,
        iteration: int,
        event: JournalEvent,
        cursor: EventCursor,
        catalog_sha256: str,
        catalog_artifact: ArtifactRef,
    ) -> RecoveredToolCatalogAuthority:
        proof = _RecoveredToolCatalogProof.create(
            attempt=attempt,
            iteration=iteration,
            event=event,
            cursor=cursor,
            catalog_sha256=catalog_sha256,
            catalog_artifact=catalog_artifact,
        )
        authority = object.__new__(RecoveredToolCatalogAuthority)
        object.__setattr__(
            authority,
            "_RecoveredToolCatalogAuthority__proof_sha256",
            proof.proof_sha256,
        )
        identity = id(authority)

        def discard(
            dead_ref: weakref.ReferenceType[RecoveredToolCatalogAuthority],
            *,
            expected_identity: int = identity,
        ) -> None:
            current = self._proofs.get(expected_identity)
            if current is not None and current[0] is dead_ref:
                self._proofs.pop(expected_identity, None)

        authority_ref = weakref.ref(authority, discard)
        self._proofs[identity] = (authority_ref, proof)
        return authority

    def verify(
        self,
        authority: object,
    ) -> _RecoveredToolCatalogProof:
        if type(authority) is not RecoveredToolCatalogAuthority:
            raise ToolCatalogReceiptIntegrityError(
                "catalog authority was not issued by the private issuer"
            )
        record = self._proofs.get(id(authority))
        if record is None or record[0]() is not authority:
            raise ToolCatalogReceiptIntegrityError(
                "catalog authority was not issued by the private issuer"
            )
        proof = record[1]
        try:
            presented_sha256 = object.__getattribute__(
                authority,
                "_RecoveredToolCatalogAuthority__proof_sha256",
            )
        except AttributeError as exc:
            raise ToolCatalogReceiptIntegrityError(
                "catalog authority proof is missing"
            ) from exc
        proof.verify()
        if presented_sha256 != proof.proof_sha256:
            raise ToolCatalogReceiptIntegrityError(
                "catalog authority proof identity changed"
            )
        return proof


_RECOVERED_AUTHORITY_ISSUER = _RecoveredToolCatalogAuthorityIssuer()


def verify_recovered_tool_catalog_authority(
    authority: object,
) -> RecoveredToolCatalogAuthority:
    """Verify issuer identity and the complete canonical receipt proof."""

    _RECOVERED_AUTHORITY_ISSUER.verify(authority)
    return authority


def recover_tool_catalog_authority(
    index: BoundToolCatalogIndex,
    *,
    attempt: AttemptRef,
    iteration: int,
    expected_catalog_sha256: str,
    expected_catalog_artifact: ArtifactRef,
) -> RecoveredToolCatalogAuthority:
    """Recover one catalog receipt without scanning or replay fallback."""

    if not isinstance(index, BoundToolCatalogIndex):
        raise TypeError("index must be a BoundToolCatalogIndex")
    attempt = _require_exact_attempt(attempt, "attempt")
    iteration = _require_iteration(iteration)
    expected_catalog_sha256 = _require_sha256(
        expected_catalog_sha256,
        "expected_catalog_sha256",
    )
    if type(expected_catalog_artifact) is not ArtifactRef or type(
        expected_catalog_artifact.ref
    ) is not ResourceRef:
        raise TypeError(
            "expected_catalog_artifact must be an exact ArtifactRef"
        )
    if index.execution_id != attempt.generation.execution_id:
        raise ToolCatalogReceiptIntegrityError(
            "catalog index execution does not match the requested attempt"
        )

    lookup = index.lookup_tool_catalog_receipts(
        attempt=attempt,
        iteration=iteration,
    )
    if type(lookup) is not ToolCatalogReceiptLookup:
        raise ToolCatalogReceiptIntegrityError(
            "catalog index did not return the exact lookup record"
        )
    if lookup.attempt != attempt or lookup.iteration != iteration:
        raise ToolCatalogReceiptIntegrityError(
            "catalog index crossed its requested subject"
        )
    if lookup.overflow:
        raise ToolCatalogReceiptIntegrityError(
            "catalog receipt lookup overflowed its bounded result"
        )
    if len(lookup.events) != 1:
        raise ToolCatalogReceiptIntegrityError(
            "catalog recovery requires exactly one durable snapshot receipt"
        )

    event = lookup.events[0]
    _require_canonical_event(event)
    if event.attempt != attempt or event.payload.get("iteration") != iteration:
        raise ToolCatalogReceiptIntegrityError(
            "catalog receipt crossed its requested subject"
        )
    try:
        catalog_sha256 = _require_sha256(
            event.payload.get("catalog_sha256"),
            "catalog_sha256",
        )
    except (TypeError, ValueError) as exc:
        raise ToolCatalogReceiptIntegrityError(
            "catalog_sha256 is malformed"
        ) from exc
    artifact = _catalog_artifact_from_event(event)
    if catalog_sha256 != expected_catalog_sha256:
        raise ToolCatalogReceiptIntegrityError("catalog digest mismatch")
    if artifact != expected_catalog_artifact:
        raise ToolCatalogReceiptIntegrityError("catalog artifact mismatch")

    return _RECOVERED_AUTHORITY_ISSUER.mint(
        attempt=attempt,
        iteration=iteration,
        event=event,
        cursor=EventCursor(event.store_seq, event.event_id),
        catalog_sha256=catalog_sha256,
        catalog_artifact=artifact,
    )


__all__ = [
    "BoundToolCatalogIndex",
    "MAX_TOOL_CATALOG_ITERATION",
    "MAX_TOOL_CATALOG_RECEIPTS",
    "RecoveredToolCatalogAuthority",
    "TOOL_CATALOG_SNAPSHOT_EVENT_TYPE",
    "TOOL_CATALOG_RECEIPT_LOOKUP_LIMITS",
    "ToolCatalogReceiptIntegrityError",
    "ToolCatalogReceiptLookup",
    "recover_tool_catalog_authority",
    "verify_recovered_tool_catalog_authority",
]
