from __future__ import annotations

import copy
import hashlib
import importlib
import json
from collections.abc import Sequence
from dataclasses import is_dataclass, replace

import pytest

from unchain.journal.models import (
    ArtifactRef,
    AttemptRef,
    EventCursor,
    GenerationRef,
    JournalAppendRequest,
    JournalAppendResult,
    JournalEvent,
    JournalPage,
    OperationRef,
    ResourceRef,
    ToolExecutionReceiptLookup,
)
from unchain.journal.snapshot import capture_journal_snapshot
from unchain.journal.resource_limits import BoundaryResourceLimitError
from unchain.tools.handler_registry import DurableToolHandlerBinding


ATTEMPT = AttemptRef(
    GenerationRef("execution-1", "generation-1"),
    "attempt-1",
)
FOREIGN_ATTEMPT = AttemptRef(
    GenerationRef("execution-1", "generation-1"),
    "attempt-2",
)
ITERATION = 7
CATALOG_SHA256 = "c" * 64
ARTIFACT = ArtifactRef(
    ref=ResourceRef("artifact", "tool-catalog-1", 1),
    media_type="application/json",
    byte_length=123,
    sha256=CATALOG_SHA256,
    preview="",
)


def _api():
    try:
        return importlib.import_module("unchain.journal.tool_catalog")
    except ModuleNotFoundError:
        pytest.fail("tool catalog journal receipt API is not implemented")


def _event(
    store_seq: int = 11,
    *,
    attempt: AttemptRef = ATTEMPT,
    event_type: str = "tool.catalog_snapshot",
    iteration: object = ITERATION,
    catalog_sha256: object = CATALOG_SHA256,
    artifact: ArtifactRef = ARTIFACT,
    refs: tuple[ResourceRef, ...] | None = None,
    extra_payload: dict[str, object] | None = None,
    operation: OperationRef | None = None,
) -> JournalEvent:
    payload: dict[str, object] = {
        "iteration": iteration,
        "catalog_sha256": catalog_sha256,
        "catalog_artifact": artifact.to_dict(),
    }
    if extra_payload:
        payload.update(extra_payload)
    return JournalEvent(
        event_id=f"catalog-event-{store_seq}",
        event_type=event_type,
        attempt=attempt,
        operation=operation or OperationRef(f"catalog-operation-{store_seq}", "a" * 64),
        store_seq=store_seq,
        payload=payload,
        resource_refs=refs if refs is not None else (artifact.ref,),
    )


def _journal(
    *,
    events: tuple[JournalEvent, ...] = (),
    overflow: bool = False,
    returned_attempt: AttemptRef = ATTEMPT,
    returned_iteration: int = ITERATION,
    lookup_type=None,
):
    api = _api()

    class IndexedJournal(api.BoundToolCatalogIndex):
        def __init__(self) -> None:
            super().__init__(ATTEMPT.generation.execution_id)
            self.catalog_query_count = 0
            self.read_count = 0

        def append(
            self,
            *,
            request: JournalAppendRequest,
        ) -> JournalAppendResult:
            raise AssertionError(request)

        def read(self, *, after=None, limit=100) -> JournalPage:
            self.read_count += 1
            raise AssertionError("catalog recovery must not scan journal pages")

        def capture_snapshot(
            self,
            *,
            max_events=10_000,
            max_bytes=32 * 1024 * 1024,
        ):
            return capture_journal_snapshot(
                execution_id=self.execution_id,
                events=(),
            )

        def lookup_tool_execution_receipts(
            self,
            *,
            attempt,
            call_id,
        ) -> ToolExecutionReceiptLookup:
            raise AssertionError("catalog recovery must not query tool executions")

        def lookup_tool_catalog_receipts(self, *, attempt, iteration):
            self.catalog_query_count += 1
            cls = lookup_type or api.ToolCatalogReceiptLookup
            return cls(
                attempt=returned_attempt,
                iteration=returned_iteration,
                events=events,
                overflow=overflow,
            )

    return IndexedJournal()


def _recover(journal, **overrides):
    api = _api()
    arguments = {
        "attempt": ATTEMPT,
        "iteration": ITERATION,
        "expected_catalog_sha256": CATALOG_SHA256,
        "expected_catalog_artifact": ARTIFACT,
    }
    arguments.update(overrides)
    return api.recover_tool_catalog_authority(journal, **arguments)


def _json_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _real_catalog_contract_snapshot():
    contract = importlib.import_module("unchain.context.tool_catalog")
    schema = {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search durable documents",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }
    envelope = contract.ToolCatalogEnvelope(
        attempt=ATTEMPT,
        iteration=ITERATION,
        provider="openai",
        model="gpt-frontier",
        semantic_schemas=[schema],
        entries=[
            contract.ToolCatalogEntry(
                tool_name="search",
                semantic_schema_sha256=_json_sha256(schema),
                tool_descriptor_sha256="4" * 64,
                handler_binding=DurableToolHandlerBinding(
                    handler_id="host.search",
                    revision=1,
                    config_sha256="0" * 64,
                    kind="stable",
                ),
                route_kind="normal",
            )
        ],
        required_betas_sha256="1" * 64,
        prompt_sha256="2" * 64,
        exposure_plan_sha256="3" * 64,
    )
    content = envelope.canonical_bytes()
    artifact = ArtifactRef(
        ref=ResourceRef("artifact", "real-tool-catalog-1", 1),
        media_type="application/json",
        byte_length=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        preview="",
    )
    event = _event(
        store_seq=42,
        catalog_sha256=envelope.catalog_sha256,
        artifact=artifact,
    )
    snapshot = contract.ToolCatalogSnapshot(
        envelope=envelope,
        event_cursor=EventCursor(event.store_seq, event.event_id),
        artifact=artifact,
    )
    return snapshot, event


def test_recovery_uses_one_exact_index_lookup_without_scan_fallback() -> None:
    api = _api()
    event = _event()
    journal = _journal(events=(event,))

    recovered = _recover(journal)

    assert type(recovered) is api.RecoveredToolCatalogAuthority
    assert recovered.attempt == ATTEMPT
    assert recovered.iteration == ITERATION
    assert recovered.catalog_sha256 == CATALOG_SHA256
    assert recovered.catalog_artifact == ARTIFACT
    assert recovered.event is event
    assert recovered.cursor.store_seq == event.store_seq
    assert recovered.cursor.event_id == event.event_id
    assert journal.catalog_query_count == 1
    assert journal.read_count == 0


def test_real_context_catalog_contract_recovers_distinct_semantic_and_bytes_digests() -> (
    None
):
    snapshot, event = _real_catalog_contract_snapshot()

    assert snapshot.catalog_sha256 != snapshot.artifact.sha256
    recovered = _recover(
        _journal(events=(event,)),
        expected_catalog_sha256=snapshot.catalog_sha256,
        expected_catalog_artifact=snapshot.artifact,
    )

    assert recovered.catalog_sha256 == snapshot.catalog_sha256
    assert recovered.catalog_artifact == snapshot.artifact


@pytest.mark.parametrize(
    ("events", "overflow", "message"),
    [
        ((), False, "exactly one"),
        ((_event(11), _event(12)), False, "exactly one"),
        ((_event(11),), True, "overflow"),
    ],
)
def test_recovery_fails_closed_on_none_duplicates_or_overflow(
    events: tuple[JournalEvent, ...],
    overflow: bool,
    message: str,
) -> None:
    api = _api()

    with pytest.raises(api.ToolCatalogReceiptIntegrityError, match=message):
        _recover(_journal(events=events, overflow=overflow))


@pytest.mark.parametrize(
    ("returned_attempt", "returned_iteration", "message"),
    [
        (FOREIGN_ATTEMPT, ITERATION, "requested subject"),
        (ATTEMPT, ITERATION + 1, "requested subject"),
    ],
)
def test_recovery_rejects_index_lookup_that_crosses_requested_subject(
    returned_attempt: AttemptRef,
    returned_iteration: int,
    message: str,
) -> None:
    api = _api()

    with pytest.raises(api.ToolCatalogReceiptIntegrityError, match=message):
        _recover(
            _journal(
                events=(
                    _event(attempt=returned_attempt, iteration=returned_iteration),
                ),
                returned_attempt=returned_attempt,
                returned_iteration=returned_iteration,
            )
        )


@pytest.mark.parametrize(
    ("events", "message"),
    [
        (
            (_event(attempt=FOREIGN_ATTEMPT),),
            "foreign attempt",
        ),
        (
            (_event(iteration=ITERATION + 1),),
            "iteration",
        ),
        (
            (_event(event_type="tool.started"),),
            "event type",
        ),
        (
            (_event(12), _event(11)),
            "strictly ordered",
        ),
    ],
)
def test_lookup_rejects_cross_scope_type_and_ordering(
    events: tuple[JournalEvent, ...],
    message: str,
) -> None:
    api = _api()

    with pytest.raises((TypeError, ValueError), match=message):
        api.ToolCatalogReceiptLookup(
            attempt=ATTEMPT,
            iteration=ITERATION,
            events=events,
        )


def test_lookup_rejects_duplicate_event_operation_or_cursor_identity() -> None:
    api = _api()
    first = _event(11)
    duplicate_operation = _event(12, operation=first.operation)

    with pytest.raises(ValueError, match="identity is duplicated"):
        api.ToolCatalogReceiptLookup(
            attempt=ATTEMPT,
            iteration=ITERATION,
            events=(first, duplicate_operation),
        )


def test_lookup_enforces_fixed_two_receipt_cap_and_explicit_overflow() -> None:
    api = _api()

    with pytest.raises(ValueError, match="at most two"):
        api.ToolCatalogReceiptLookup(
            attempt=ATTEMPT,
            iteration=ITERATION,
            events=(_event(11), _event(12), _event(13)),
            overflow=True,
        )
    with pytest.raises(TypeError, match="overflow"):
        api.ToolCatalogReceiptLookup(
            attempt=ATTEMPT,
            iteration=ITERATION,
            overflow=1,
        )


@pytest.mark.parametrize("iteration", [True, -1, 2**31])
def test_lookup_rejects_noncanonical_or_unbounded_iteration(iteration) -> None:
    api = _api()

    with pytest.raises((TypeError, ValueError), match="iteration"):
        api.ToolCatalogReceiptLookup(
            attempt=ATTEMPT,
            iteration=iteration,
        )


@pytest.mark.parametrize(
    ("event", "message"),
    [
        (
            _event(catalog_sha256="A" * 64),
            "catalog_sha256",
        ),
        (
            _event(catalog_sha256="d" * 64),
            "catalog digest mismatch",
        ),
        (
            _event(refs=()),
            "resource refs",
        ),
        (
            _event(refs=(ARTIFACT.ref, ResourceRef("artifact", "extra", 1))),
            "resource refs",
        ),
        (
            _event(
                artifact=replace(ARTIFACT, media_type="text/plain"),
            ),
            "JSON artifact",
        ),
        (
            _event(
                artifact=replace(
                    ARTIFACT,
                    ref=ResourceRef("memory", "tool-catalog-1", 1),
                ),
            ),
            "artifact resource",
        ),
        (
            _event(
                artifact=replace(
                    ARTIFACT,
                    ref=ResourceRef(
                        "artifact",
                        "tool-catalog-1",
                        1,
                        fragment="chunk/7",
                    ),
                ),
            ),
            "whole artifact",
        ),
        (
            _event(
                artifact=replace(
                    ARTIFACT,
                    preview="password=must-not-enter-journal-authority",
                ),
            ),
            "preview",
        ),
        (
            _event(extra_payload={"provider": "forged"}),
            "payload fields",
        ),
    ],
)
def test_recovery_rejects_malformed_catalog_receipt(
    event: JournalEvent,
    message: str,
) -> None:
    api = _api()

    with pytest.raises(api.ToolCatalogReceiptIntegrityError, match=message):
        _recover(_journal(events=(event,)))


def test_recovery_rejects_expected_digest_or_artifact_mismatch() -> None:
    api = _api()
    journal = _journal(events=(_event(),))

    with pytest.raises(api.ToolCatalogReceiptIntegrityError, match="digest mismatch"):
        _recover(journal, expected_catalog_sha256="d" * 64)

    other = replace(
        ARTIFACT,
        ref=ResourceRef("artifact", "tool-catalog-other", 1),
    )
    with pytest.raises(api.ToolCatalogReceiptIntegrityError, match="artifact mismatch"):
        _recover(_journal(events=(_event(),)), expected_catalog_artifact=other)


def test_recovery_rejects_duck_typed_index_and_lookup_subclass() -> None:
    api = _api()

    class DuckIndex:
        def lookup_tool_catalog_receipts(self, *, attempt, iteration):
            raise AssertionError((attempt, iteration))

    with pytest.raises(TypeError, match="BoundToolCatalogIndex"):
        _recover(DuckIndex())

    class LookupSubclass(api.ToolCatalogReceiptLookup):
        pass

    with pytest.raises(api.ToolCatalogReceiptIntegrityError, match="exact lookup"):
        _recover(
            _journal(
                events=(_event(),),
                lookup_type=LookupSubclass,
            )
        )


def test_lookup_rejects_journal_event_subclass_as_authority() -> None:
    api = _api()

    class JournalEventSubclass(JournalEvent):
        pass

    raw = _event().to_dict()
    event = JournalEventSubclass(
        event_id=raw["event_id"],
        event_type=raw["event_type"],
        attempt=AttemptRef.from_dict(raw["attempt"]),
        operation=OperationRef.from_dict(raw["operation"]),
        store_seq=raw["store_seq"],
        payload=raw["payload"],
        resource_refs=tuple(ResourceRef.from_dict(ref) for ref in raw["resource_refs"]),
    )

    with pytest.raises(TypeError, match="exact JournalEvent"):
        api.ToolCatalogReceiptLookup(
            attempt=ATTEMPT,
            iteration=ITERATION,
            events=(event,),
        )


def test_lookup_round_trip_rebuilds_only_canonical_exact_records() -> None:
    api = _api()
    lookup = api.ToolCatalogReceiptLookup(
        attempt=ATTEMPT,
        iteration=ITERATION,
        events=(_event(),),
        overflow=False,
    )

    restored = api.ToolCatalogReceiptLookup.from_dict(lookup.to_dict())

    assert type(restored) is api.ToolCatalogReceiptLookup
    assert type(restored.attempt) is AttemptRef
    assert type(restored.events) is tuple
    assert all(type(event) is JournalEvent for event in restored.events)
    assert restored == lookup


def test_lookup_from_dict_rejects_over_cap_before_parsing_event_elements() -> None:
    api = _api()
    raw = {
        "schema": api.ToolCatalogReceiptLookup.SCHEMA,
        "attempt": ATTEMPT.to_dict(),
        "iteration": ITERATION,
        "events": [object(), object(), object()],
        "overflow": True,
    }

    with pytest.raises(ValueError, match="at most two"):
        api.ToolCatalogReceiptLookup.from_dict(raw)


def test_lookup_from_dict_rejects_deceptive_sequence_without_iterating_it() -> None:
    api = _api()

    class DeceptiveSequence(Sequence):
        def __init__(self) -> None:
            self.reads = 0

        def __len__(self) -> int:
            return 2

        def __getitem__(self, index: int):
            self.reads += 1
            if index >= 1_001:
                raise IndexError(index)
            return _event(index + 1).to_dict()

    events = DeceptiveSequence()
    raw = {
        "schema": api.ToolCatalogReceiptLookup.SCHEMA,
        "attempt": ATTEMPT.to_dict(),
        "iteration": ITERATION,
        "events": events,
        "overflow": True,
    }

    with pytest.raises(TypeError, match="exact list"):
        api.ToolCatalogReceiptLookup.from_dict(raw)
    assert events.reads == 0


def test_lookup_from_dict_rejects_non_exact_outer_and_event_dicts() -> None:
    api = _api()

    class DictSubclass(dict):
        pass

    raw = {
        "schema": api.ToolCatalogReceiptLookup.SCHEMA,
        "attempt": ATTEMPT.to_dict(),
        "iteration": ITERATION,
        "events": [_event().to_dict()],
        "overflow": False,
    }
    with pytest.raises(TypeError, match="exact dict"):
        api.ToolCatalogReceiptLookup.from_dict(DictSubclass(raw))

    raw["events"] = [DictSubclass(_event().to_dict())]
    with pytest.raises(TypeError, match="event.*exact dict"):
        api.ToolCatalogReceiptLookup.from_dict(raw)


def test_lookup_from_dict_bounds_raw_depth_nodes_and_bytes_before_parsing() -> None:
    api = _api()
    limits = api.TOOL_CATALOG_RECEIPT_LOOKUP_LIMITS

    deep: object = 0
    for _ in range(1_200):
        deep = [deep]

    resources = {
        "depth": deep,
        "nodes": [0] * (limits.max_nodes + 1),
        "bytes": "x" * (limits.max_bytes + 1),
    }
    for dimension, resource in resources.items():
        event = _event().to_dict()
        event["payload"]["untrusted_resource"] = resource
        raw = {
            "schema": api.ToolCatalogReceiptLookup.SCHEMA,
            "attempt": ATTEMPT.to_dict(),
            "iteration": ITERATION,
            "events": [event],
            "overflow": False,
        }

        with pytest.raises(BoundaryResourceLimitError) as caught:
            api.ToolCatalogReceiptLookup.from_dict(raw)

        assert caught.value.boundary == "tool_catalog_receipt_lookup"
        assert caught.value.dimension == dimension


def test_recovered_authority_cannot_be_constructed_with_a_boolean_marker() -> None:
    api = _api()
    event = _event()

    with pytest.raises(TypeError, match="verified recovery"):
        api.RecoveredToolCatalogAuthority(
            attempt=ATTEMPT,
            iteration=ITERATION,
            event=event,
            cursor=EventCursor(event.store_seq, event.event_id),
            catalog_sha256=CATALOG_SHA256,
            catalog_artifact=ARTIFACT,
            _authority=True,
        )


def test_recovered_authority_rejects_replace_copy_and_manual_construction() -> None:
    api = _api()
    recovered = _recover(_journal(events=(_event(),)))

    assert is_dataclass(recovered) is False
    with pytest.raises(TypeError):
        replace(recovered)
    with pytest.raises(TypeError, match="copied"):
        copy.copy(recovered)
    with pytest.raises(TypeError, match="minted"):
        api.RecoveredToolCatalogAuthority()

    manual = object.__new__(api.RecoveredToolCatalogAuthority)
    with pytest.raises(api.ToolCatalogReceiptIntegrityError, match="issued"):
        api.verify_recovered_tool_catalog_authority(manual)
    with pytest.raises(api.ToolCatalogReceiptIntegrityError, match="issued"):
        _ = manual.attempt


def test_every_authority_read_rejects_an_altered_canonical_proof() -> None:
    api = _api()
    recovered = _recover(_journal(events=(_event(),)))
    proof_slot = "_RecoveredToolCatalogAuthority__proof_sha256"
    object.__setattr__(recovered, proof_slot, "f" * 64)

    with pytest.raises(api.ToolCatalogReceiptIntegrityError, match="proof"):
        _ = recovered.attempt
    with pytest.raises(api.ToolCatalogReceiptIntegrityError, match="proof"):
        api.verify_recovered_tool_catalog_authority(recovered)
